from __future__ import annotations

import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from test_overview_shell import app_and_client, csrf

from context_library_maintainer.config import resolve_config, scaffold
from context_library_maintainer.service import (
    MaintainerApplicationService,
    MaintainerContext,
)
from context_library_maintainer.state import State
from context_library_manager.agent import invoke, provider_value_contains_secret, redact_provider_value
from context_library_manager.api import create_app
from context_library_manager.config import Settings
from context_library_manager.db import Store
from context_library_manager.domain import Source, utc_now
from context_library_manager.notifications import deliver_pending
from context_library_manager.service import SourceIdempotencyConflict, intake_source
from context_library_manager.telemetry import append_event
from context_library_manager.worker import Worker


def settings_for(tmp_path) -> Settings:
    return Settings(
        "sqlite:///" + str(tmp_path / "runtime.db"),
        tmp_path / "library",
        tmp_path / "state",
        "demo",
        require_oidc=False,
        allow_local_dev_identity=True,
        development_mode=True,
        session_secret="runtime-correctness-secret",
    )


def test_provider_and_cache_filter_nested_confidential_fields_and_structured_strings(tmp_path):
    payload = {
        "safe": "visible",
        "nested": {
            "client_secret": "never-store",
            "encoded": json.dumps(
                {
                    "authorization": "Bearer never-send",
                    "safe": [{"api_key": "never-cache"}, {"note": "kept"}],
                }
            ),
        },
    }
    filtered = redact_provider_value(payload)
    assert filtered["safe"] == "visible"
    assert "client_secret" not in filtered["nested"]
    encoded = json.loads(filtered["nested"]["encoded"])
    assert encoded == {"safe": [{}, {"note": "kept"}]}
    assert not provider_value_contains_secret(filtered)

    store = Store(tmp_path / "cache.db")
    store.cache_put("safe-cache", "demo", payload, 1, 2)
    cached = json.loads(store.cache_get("safe-cache")["payload"])
    assert cached == filtered
    assert "never-store" not in json.dumps(cached)
    assert "never-send" not in json.dumps(cached)
    assert "never-cache" not in json.dumps(cached)


def test_notification_claim_is_single_consumer_and_stale_claim_is_recoverable(
    tmp_path,
    monkeypatch,
):
    database = tmp_path / "notifications.db"
    first = Store(database)
    work_id, _ = first.add_work("demo", "candidate_task", "notify-once", {}, "test")
    first.create_review("demo", work_id, "Choose", ["a", "b"], [], "test")
    second_work_id, _ = first.add_work("demo", "candidate_task", "notify-second", {}, "test")
    first.create_review("demo", second_work_id, "Choose again", ["a", "b"], [], "test")
    second = Store(database)
    calls: list[bytes] = []
    calls_lock = threading.Lock()

    class Response:
        status = 204

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def urlopen(request, timeout):
        with calls_lock:
            calls.append(request.data)
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    barrier = threading.Barrier(2)

    def deliver(store, owner):
        barrier.wait()
        return deliver_pending(
            store,
            "https://example.invalid/hook",
            "signing-key",
            owner=owner,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda args: deliver(*args),
                ((first, "notification-one"), (second, "notification-two")),
            )
        )
    assert sum(item["delivered"] for item in results) == 2
    assert len(calls) == 2
    rows = first.db.execute(
        "SELECT id,status,claim_owner,claimed_at,claim_expires FROM notifications ORDER BY id"
    ).fetchall()
    assert all(tuple(row)[1:] == ("delivered", None, None, None) for row in rows)

    first.db.execute(
        "UPDATE notifications SET status='pending',delivered_at=NULL,"
        "claim_owner='dead-worker',claimed_at='2000-01-01T00:00:00Z',"
        "claim_expires='2000-01-01T00:00:01Z' WHERE id=?",
        (rows[0]["id"],),
    )
    first.db.commit()
    assert deliver_pending(
        first,
        "https://example.invalid/hook",
        "signing-key",
        owner="recovery-worker",
    ) == {"delivered": 1, "failed": 0}
    assert len(calls) == 3


def test_notification_persists_only_bounded_error_class(tmp_path, monkeypatch):
    store = Store(tmp_path / "notification-error.db")
    work_id, _ = store.add_work("demo", "candidate_task", "notify-error", {}, "test")
    store.create_review("demo", work_id, "Choose", ["a", "b"], [], "test")
    sensitive = "token=durable-secret-" + "x" * 5000
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError(sensitive)),
    )
    assert deliver_pending(
        store,
        "https://example.invalid/hook",
        "signing-key",
    ) == {"delivered": 0, "failed": 1}
    row = store.db.execute("SELECT last_error FROM notifications").fetchone()
    assert row["last_error"] == "RuntimeError"
    assert sensitive not in row["last_error"]


def test_cancellation_acknowledgement_rolls_back_all_lineage_on_event_failure(
    tmp_path,
    monkeypatch,
):
    store = Store(tmp_path / "cancellation.db")
    work_id, _ = store.add_work("demo", "candidate_task", "cancel-atomic", {}, "test")
    store.claim_work("demo", work_id, "worker")
    store.transition("demo", work_id, "running", "worker")
    store.start_agent_run("run-cancel", work_id, "cheap", "worker", "1", "cache")
    store.transition("demo", work_id, "cancel-requested", "human:operator")
    store.db.execute(
        "INSERT INTO work_cancellations VALUES(?,?,?,?,?,?,?,?,?)",
        (
            "cancel-one",
            work_id,
            "run-cancel",
            "cancel-requested",
            "human:operator",
            "operator request",
            "cancel-key",
            utc_now(),
            None,
        ),
    )
    store.db.commit()
    before = {
        table: store.db.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
        for table in ("events", "audit_events", "telemetry_events")
    }
    original = append_event

    def fail_terminal_event(*args, **kwargs):
        if args[3] == "state-transition" and kwargs.get("payload", {}).get("to") == "canceled":
            raise RuntimeError("injected lineage failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(
        "context_library_manager.telemetry.append_event",
        fail_terminal_event,
    )
    with pytest.raises(RuntimeError, match="injected lineage failure"):
        store.acknowledge_cancellation(
            "demo",
            work_id,
            "run-cancel",
            "worker",
            3,
            5,
        )
    assert store.work("demo", work_id)["state"] == "cancel-requested"
    assert store.db.execute("SELECT status FROM agent_runs WHERE id='run-cancel'").fetchone()["status"] == "running"
    assert (
        store.db.execute("SELECT state FROM work_cancellations WHERE id='cancel-one'").fetchone()["state"]
        == "cancel-requested"
    )
    assert {table: store.db.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"] for table in before} == before

    monkeypatch.setattr("context_library_manager.telemetry.append_event", original)
    store.acknowledge_cancellation(
        "demo",
        work_id,
        "run-cancel",
        "worker",
        3,
        5,
    )
    assert store.work("demo", work_id)["state"] == "canceled"
    assert store.db.execute(
        "SELECT status,input_tokens,output_tokens FROM agent_runs WHERE id='run-cancel'"
    ).fetchone()[:] == ("canceled", 3, 5)
    assert (
        store.db.execute("SELECT state FROM work_cancellations WHERE id='cancel-one'").fetchone()["state"] == "canceled"
    )
    assert (
        store.db.execute(
            "SELECT COUNT(*) AS n FROM audit_events WHERE work_id=? AND event_type='cancellation-acknowledged'",
            (work_id,),
        ).fetchone()["n"]
        == 1
    )
    sequences = [
        row["project_sequence"]
        for row in store.db.execute(
            "SELECT project_sequence FROM telemetry_events WHERE project='demo' ORDER BY project_sequence"
        ).fetchall()
    ]
    assert sequences == list(range(1, len(sequences) + 1))


def test_source_replay_recovers_an_expired_lease_and_finishes_original_request(
    tmp_path,
):
    settings = settings_for(tmp_path)
    scaffold(resolve_config(settings.library_root, "demo", settings.state_root, "human:source"))
    store = Store(settings.db_path)
    source = Source(
        external_id="source-replay",
        source_type="project-note",
        uri="local://source-replay",
        content="Replay the original request after lease expiry.",
        retrieved_at=datetime.now(timezone.utc),
    )
    key = "source-replay-key"
    actor = "human:source"
    work_id, _ = store.add_work(
        "demo",
        "source_batch",
        key,
        source.model_dump(mode="json"),
        actor,
    )
    route_name = "POST:/api/v1/projects/demo/sources"
    response = {"work_id": work_id, "created": True, "status": "pending"}
    store.db.execute(
        "INSERT INTO idempotency_records VALUES(?,?,?,?,?,?,?,?,?)",
        (
            "source-replay-idempotency",
            actor,
            "demo",
            route_name,
            key,
            store.digest(source.model_dump(mode="json")),
            202,
            json.dumps(response),
            utc_now(),
        ),
    )
    store.db.commit()
    store.claim_work("demo", work_id, actor)
    store.transition("demo", work_id, "running", actor)
    store.db.execute(
        "UPDATE work_items SET lease_expires='2000-01-01T00:00:00Z' WHERE id=?",
        (work_id,),
    )
    store.db.commit()

    replay = intake_source(store, settings, "demo", source, actor, key)
    assert replay["work_id"] == work_id
    assert replay["created"] is False
    assert replay["status"] == "succeeded"
    assert store.work("demo", work_id)["state"] == "succeeded"
    assert store.db.execute("SELECT COUNT(*) AS n FROM work_items WHERE project='demo'").fetchone()["n"] == 1
    durable = State(settings.state_root)
    assert durable.db.execute("SELECT COUNT(*) AS n FROM sources WHERE project='demo'").fetchone()["n"] == 1
    durable.db.close()


def test_concurrent_source_intake_replays_winner_and_rejects_changed_payload(tmp_path):
    settings = settings_for(tmp_path)
    scaffold(resolve_config(settings.library_root, "demo", settings.state_root, "human:source"))
    stores = [Store(settings.db_path), Store(settings.db_path)]
    barrier = threading.Barrier(2)
    original_executes = []

    for store in stores:
        original_execute = store.db.execute
        original_executes.append(original_execute)
        first_lookup = {"pending": True}

        def execute(
            sql,
            parameters=(),
            original_execute=original_execute,
            first_lookup=first_lookup,
        ):
            cursor = original_execute(sql, parameters)
            if first_lookup["pending"] and sql.startswith("SELECT request_digest,response FROM idempotency_records"):
                first_lookup["pending"] = False

                class BarrierCursor:
                    def fetchone(self):
                        row = cursor.fetchone()
                        barrier.wait(timeout=5)
                        return row

                return BarrierCursor()
            return cursor

        store.db.execute = execute

    retrieved_at = datetime.now(timezone.utc)
    source = Source(
        external_id="source-race",
        source_type="project-note",
        uri="local://source-race",
        content="One durable source.",
        retrieved_at=retrieved_at,
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda store: intake_source(
                    store,
                    settings,
                    "demo",
                    source,
                    "human:source",
                    "source-race-key",
                ),
                stores,
            )
        )
    for store, original_execute in zip(stores, original_executes):
        store.db.execute = original_execute
    assert len({result["work_id"] for result in results}) == 1
    assert stores[0].db.execute("SELECT COUNT(*) AS n FROM work_items").fetchone()["n"] == 1
    assert stores[0].db.execute("SELECT COUNT(*) AS n FROM idempotency_records").fetchone()["n"] == 1
    replay = intake_source(
        stores[0],
        settings,
        "demo",
        source,
        "human:source",
        "source-race-key",
    )
    assert replay["status"] == "succeeded"
    assert replay["created"] is False

    changed = source.model_copy(update={"content": "Changed source content."})
    with pytest.raises(SourceIdempotencyConflict):
        intake_source(
            stores[0],
            settings,
            "demo",
            changed,
            "human:source",
            "source-race-key",
        )
    assert stores[0].db.execute("SELECT COUNT(*) AS n FROM work_items").fetchone()["n"] == 1


def test_provider_output_survives_descendant_inherited_pipes_and_descendant_is_stopped(tmp_path):
    marker = tmp_path / "descendant-survived"
    script = tmp_path / "provider-with-child.py"
    script.write_text(
        "import json,subprocess,sys\n"
        "request=json.load(sys.stdin)\n"
        f'child="import time; from pathlib import Path; time.sleep(0.5); '
        f"Path({str(marker)!r}).write_text('survived')\"\n"
        "subprocess.Popen([sys.executable,'-c',child])\n"
        "print(json.dumps({'schema_version':1,'run_id':request['run_id'],'status':'ok',"
        "'result':{'decision':'safe'},'usage':{'input_tokens':1,'output_tokens':1}}),flush=True)\n",
        encoding="utf-8",
    )
    request = {
        "schema_version": 1,
        "run_id": "inherited-pipe-run",
        "project": "demo",
        "task_type": "candidate_task",
        "actor": "worker",
        "model_profile": "cheap",
        "budget": {"max_input_tokens": 10, "max_output_tokens": 10},
        "prompt_revision": "1",
        "evidence": [],
        "task_context": {},
        "required_output_schema": "candidate-v1",
    }
    started = time.monotonic()
    result = invoke([sys.executable, str(script)], request, timeout=3)
    elapsed = time.monotonic() - started
    assert result.status == "ok"
    assert result.payload == {"decision": "safe"}
    assert elapsed < 1
    time.sleep(0.7)
    assert not marker.exists()


def test_evidence_routes_match_exact_ids_only(tmp_path):
    app, client = app_and_client(tmp_path)
    app.state.store.add_work(
        "demo",
        "candidate_task",
        "exact-evidence",
        {"evidence": [{"observation_id": "obs_1"}], "marker": "exact-marker"},
        "test",
    )
    app.state.store.add_work(
        "demo",
        "candidate_task",
        "similar-evidence",
        {"evidence": [{"observation_id": "obs_10"}], "marker": "similar-marker"},
        "test",
    )
    app.state.store.add_work(
        "demo",
        "candidate_task",
        "text-only-evidence",
        {"note": "obs_1", "marker": "text-marker"},
        "test",
    )
    api = client.get("/api/v1/projects/demo/evidence/obs_1")
    assert api.status_code == 200
    assert [item["marker"] for item in api.json()["data"]["matches"]] == ["exact-marker"]
    page = client.get("/evidence/obs_1")
    assert "exact-marker" in page.text
    assert "similar-marker" not in page.text
    assert "text-marker" not in page.text


def test_contribution_acceptance_has_safe_durable_work_link(tmp_path):
    app, client = app_and_client(tmp_path)
    path = "/api/v1/projects/demo/contributions"
    body = {
        "kind": "finding",
        "payload": {
            "finding": "safe finding",
            "client_secret": "must-not-enter-audit",
        },
        "evidence_references": ["obs_exact"],
        "client_idempotency_key": "linked-contribution",
    }
    first = client.post(path, headers=csrf(client, path), json=body)
    replay = client.post(path, headers=csrf(client, path), json=body)
    assert first.status_code == replay.status_code == 200
    assert replay.json()["data"]["idempotent"] is True
    assert replay.json()["data"]["work_id"] == first.json()["data"]["work_id"]
    audit = app.state.store.db.execute(
        "SELECT work_id,payload FROM audit_events WHERE project='demo' AND event_type='contribution-accepted'"
    ).fetchone()
    assert audit["work_id"] == first.json()["data"]["work_id"]
    assert json.loads(audit["payload"]) == {"contribution_id": first.json()["data"]["contribution_id"]}
    link = app.state.store.db.execute(
        "SELECT work_id FROM contribution_work_links WHERE contribution_id=?",
        (first.json()["data"]["contribution_id"],),
    ).fetchone()
    assert link["work_id"] == first.json()["data"]["work_id"]
    assert "must-not-enter-audit" not in audit["payload"]


def test_publication_exception_creates_linked_safe_history_and_audit(
    tmp_path,
    monkeypatch,
):
    class PublicationExplosion(RuntimeError):
        pass

    class FailingMaintainer:
        def __init__(self, _context):
            pass

        def add_candidate(self, _payload):
            return {"candidate_id": "candidate-one"}

        def reconcile(self, _candidate_id):
            return {"status": "ok", "ready": ["candidate-one"]}

        def publish_authorized(self, _authorization):
            raise PublicationExplosion("token=must-not-be-durable")

    settings = settings_for(tmp_path)
    store = Store(settings.db_path)
    work_id, _ = store.add_work(
        "demo",
        "candidate_task",
        "publication-failure",
        {
            "clm_payload": {
                "schema_version": 1,
                "candidate_id": "candidate-one",
            },
                "authorized_publication": True,
                "publication_authorization": {"authorization_id": "auth-publication-failure"},
        },
        "human:admin",
    )
    monkeypatch.setattr(
        "context_library_manager.worker.MaintainerApplicationService",
        FailingMaintainer,
    )
    worker = Worker(store, settings)
    monkeypatch.setattr(worker, "_library_is_clean", lambda: True)
    result = worker.run_once()
    assert result == {
        "work_id": work_id,
        "route": "deterministic",
        "status": "failed",
        "reason": "PublicationExplosion",
    }
    history = store.db.execute(
        "SELECT id,work_id,status,error FROM publication_history WHERE project='demo'"
    ).fetchone()
    assert history["work_id"] == work_id
    assert history["status"] == "failed"
    assert history["error"] == "PublicationExplosion"
    audit = store.db.execute(
        "SELECT payload FROM audit_events WHERE work_id=? AND event_type='publication-failed'",
        (work_id,),
    ).fetchone()
    assert json.loads(audit["payload"]) == {
        "publication_id": history["id"],
        "error_class": "PublicationExplosion",
    }
    assert "must-not-be-durable" not in audit["payload"]


@pytest.mark.parametrize(
    ("manager_choice", "expected_resolution_candidate", "pre_resolved"),
    (
        ("retain-current", False, False),
        ("adopt-candidate", True, False),
        ("retain-current", False, True),
        ("adopt-candidate", True, True),
    ),
)
def test_manager_review_updates_typed_maintainer_conflict_before_terminal_success(
    tmp_path,
    manager_choice,
    expected_resolution_candidate,
    pre_resolved,
):
    settings = settings_for(tmp_path)
    scaffold(
        resolve_config(
            settings.library_root,
            "demo",
            settings.state_root,
            "human:owner",
        )
    )
    maintainer = MaintainerApplicationService(
        MaintainerContext(
            settings.library_root,
            settings.state_root,
            "demo",
            "human:owner",
        )
    )
    source_id = maintainer.ingest_source(
        {
            "external_id": "manager-conflict",
            "source_type": "project-note",
            "uri": "local://manager-conflict",
            "title": "Manager conflict",
            "retrieved_at": "2026-07-29T00:00:00Z",
            "content_format": "text",
            "content": "Choose direction A or direction B.",
        }
    )["sources"][0]["source_id"]
    observation_id = maintainer.add_observation(
        {
            "source_id": source_id,
            "kind": "directive",
            "excerpt": "Choose direction A or direction B.",
            "location": "body",
            "speaker": {
                "identity": "owner@example.test",
                "display_name": "Owner",
            },
            "occurred_at": "2026-07-29T00:00:00Z",
            "agent_interpretation": "Explicit competing choices.",
        }
    )["observation_id"]

    def candidate(identifier: str, decision: str) -> dict:
        return {
            "schema_version": 1,
            "project": "demo",
            "candidate_id": identifier,
            "subject": identifier,
            "category": "product",
            "decision": decision,
            "rationale": "Explicit directive.",
            "decisionmaker": {
                "identity": "owner@example.test",
                "display_name": "Owner",
            },
            "decision_at": "2026-07-29T00:00:00Z",
            "provenance": "explicit",
            "derivation": "direct",
            "source_observation_ids": [observation_id],
            "conflict_key": "product.manager-choice",
            "applicability": {
                "provenance": "explicit",
                "confidence": 1,
                "evidence_observation_ids": [observation_id],
                "reasoning": "Product-wide.",
            },
        }

    first_candidate = candidate("candidate-a", "Choose direction A.")
    reviewed_candidate = candidate("candidate-b", "Choose direction B.")
    maintainer.add_candidate(first_candidate)
    assert maintainer.reconcile("candidate-a")["ready"] == ["candidate-a"]
    maintainer.add_candidate(reviewed_candidate)
    assert maintainer.reconcile("candidate-b")["conflicted"] == ["candidate-b"]
    conflict_id = maintainer.conflict_list()["conflicts"][0]["id"]

    app = create_app(settings)
    work_id, _ = app.state.store.add_work(
        "demo",
        "candidate_task",
        f"manager-conflict-{manager_choice}",
        {
            "clm_payload": reviewed_candidate,
            "maintainer_conflict_id": conflict_id,
            "publication_intent": False,
            "authorized_publication": False,
        },
        "worker",
    )
    app.state.store.claim_work("demo", work_id, "worker")
    app.state.store.transition("demo", work_id, "running", "worker")
    review_id = app.state.store.create_review(
        "demo",
        work_id,
        "Choose the authoritative direction.",
        ["retain-current", "adopt-candidate"],
        [observation_id],
        "worker",
    )
    app.state.store.transition("demo", work_id, "waiting-human", "worker")
    client = TestClient(app, base_url="https://testserver")
    assert (
        client.post(
            "/auth/dev-login",
            json={
                "subject": "fixture:reviewer",
                "display_name": "Fixture Reviewer",
                "capabilities": ["review"],
                "projects": ["demo"],
            },
        ).status_code
        == 200
    )
    path = f"/api/v1/projects/demo/reviews/{review_id}/resolve"
    resolved = client.post(
        path,
        headers=csrf(client, path),
        json={
            "choice": manager_choice,
            "rationale": f"Human selected {manager_choice}.",
            "idempotency_key": f"resolve-{manager_choice}",
        },
    )
    assert resolved.status_code == 200, resolved.text
    assert app.state.store.work("demo", work_id)["state"] == "queued"
    assert maintainer.conflict_show(conflict_id).status == "open"
    if pre_resolved:
        maintainer.conflict_resolve(
            conflict_id,
            ("accept:candidate-b" if manager_choice == "adopt-candidate" else "retain-current"),
            "Simulated completion before Manager crash.",
        )

    result = Worker(app.state.store, settings).run_once()
    assert result["status"] == "succeeded"
    assert app.state.store.work("demo", work_id)["state"] == "succeeded"
    packet = maintainer.conflict_show(conflict_id)
    assert packet.status == "resolved"
    assert packet.resolution is not None
    assert bool(packet.resolution.resolution_candidate_id) is expected_resolution_candidate
    durable = State(settings.state_root)
    assert {row["id"] for row in durable.candidates("demo", "rejected")}.issuperset({"candidate-a", "candidate-b"})
    durable.db.close()
