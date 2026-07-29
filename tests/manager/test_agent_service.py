from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from test_overview_shell import app_and_client, csrf

from context_library_manager.agent import AgentResult, invoke
from context_library_manager.agent_service import (
    ServiceConflict,
    complete_drain,
    control_service,
    heartbeat_health,
)
from context_library_manager.configuration import apply_revision
from context_library_manager.worker import Worker


def control(store, action, version, key):
    return control_service(store, action, version, "operator test", key, "human:test", "demo")


def test_service_transition_table_stale_and_idempotency(tmp_path):
    app, _ = app_and_client(tmp_path)
    store = app.state.store
    paused = control(store, "pause", 1, "pause-1")
    assert paused == {
        "previous_state": "running",
        "state": "paused",
        "version": 2,
        "idempotent": False,
    }
    assert control(store, "pause", 1, "pause-1")["idempotent"] is True
    with pytest.raises(ServiceConflict) as stale:
        control(store, "resume", 1, "resume-stale")
    assert stale.value.code == "revision-conflict"
    resumed = control(store, "resume", 2, "resume-1")
    assert resumed["state"] == "running"
    draining = control(store, "drain", 3, "drain-1")
    assert draining["state"] == "draining"
    assert complete_drain(store, "worker:test") is True
    assert store.service_state()["state"] == "paused"
    events = store.db.execute("SELECT event_type FROM audit_events WHERE project='demo' ORDER BY created_at").fetchall()
    assert [row["event_type"] for row in events] == [
        "agent-service-pause",
        "agent-service-resume",
        "agent-service-drain",
    ]


def test_drain_waits_for_active_run_then_pauses(tmp_path):
    app, _ = app_and_client(tmp_path)
    store = app.state.store
    work_id, _ = store.add_work("demo", "semantic_task", "drain-active", {}, "test")
    store.transition("demo", work_id, "leased", "worker")
    store.transition("demo", work_id, "running", "worker")
    store.start_agent_run("agent-draining", work_id, "cheap", "worker", "1", "cache")
    assert control(store, "drain", 1, "drain-active")["state"] == "draining"
    assert complete_drain(store, "worker") is False
    assert store.service_state()["state"] == "draining"
    store.finish_agent_run("agent-draining", "ok", 1, 1)
    store.transition("demo", work_id, "succeeded", "worker")
    assert complete_drain(store, "worker") is True
    assert store.service_state()["state"] == "paused"


def test_service_api_requires_admin_and_returns_current_revision(tmp_path):
    app, client = app_and_client(tmp_path)
    path = "/api/v1/agent-service/pause"
    body = {
        "schema_version": 1,
        "expected_version": 1,
        "reason": "maintenance",
        "idempotency_key": "api-pause",
    }
    assert client.post(path, headers=csrf(client, path), json=body).status_code == 200
    stale = client.post(
        "/api/v1/agent-service/resume",
        headers=csrf(client, "/api/v1/agent-service/resume"),
        json={**body, "idempotency_key": "api-stale"},
    )
    assert stale.status_code == 409
    assert stale.json()["errors"][0]["current_version"] == 2

    client.post("/auth/logout", headers=csrf(client, "/auth/logout"))
    client.post(
        "/auth/dev-login",
        json={
            "subject": "fixture:maintainer",
            "display_name": "Maintainer",
            "capabilities": ["maintain"],
            "projects": ["demo"],
            "selected_project": "demo",
        },
    )
    denied = client.post(path, headers=csrf(client, path), json=body)
    assert denied.status_code == 403
    assert app.state.store.db.execute("SELECT 1 FROM audit_events WHERE event_type='access-denied'").fetchone()


def test_publication_intent_requires_admin_review_and_authorizes_requeued_work(tmp_path):
    app, client = app_and_client(tmp_path)
    store = app.state.store
    work_id, _ = store.add_work(
        "demo",
        "candidate_task",
        "publication-approval",
        {"publication_intent": True, "authorized_publication": False},
        "runtime:test",
    )
    store.transition("demo", work_id, "leased", "worker")
    store.transition("demo", work_id, "running", "worker")
    store.transition("demo", work_id, "waiting-human", "worker", "publication-approval-required")
    review_id = store.create_review(
        "demo",
        work_id,
        "Publish?",
        ["retain-current", "adopt-candidate"],
        [],
        "worker",
    )
    path = f"/api/v1/projects/demo/reviews/{review_id}/resolve"
    response = client.post(
        path,
        headers=csrf(client, path),
        json={
            "choice": "adopt-candidate",
            "rationale": "Administrator approved publication.",
            "idempotency_key": "approve-publication",
        },
    )
    assert response.status_code == 200
    work = store.work("demo", work_id)
    assert work["state"] == "queued"
    payload = json.loads(work["payload"])
    assert payload["authorized_publication"] is True
    assert payload["human_resolution"] == "adopt-candidate"
    audit = store.db.execute(
        "SELECT payload FROM audit_events WHERE work_id=? AND event_type='publication-authorized'",
        (work_id,),
    ).fetchone()
    assert json.loads(audit["payload"])["capability"] == "admin"


def test_immediate_drain_replay_returns_final_paused_version(tmp_path):
    _, client = app_and_client(tmp_path)
    path = "/api/v1/agent-service/drain"
    body = {
        "schema_version": 1,
        "expected_version": 1,
        "reason": "empty queue drain",
        "idempotency_key": "drain-replay",
    }
    first = client.post(path, headers=csrf(client, path), json=body).json()["data"]
    repeated = client.post(path, headers=csrf(client, path), json=body).json()["data"]
    assert first["state"] == "paused"
    assert first["version"] == 3
    assert repeated["state"] == "paused"
    assert repeated["version"] == 3
    assert repeated["idempotent"] is True


def test_paused_worker_defers_agent_but_executes_deterministic_work(tmp_path):
    app, _ = app_and_client(tmp_path)
    store = app.state.store
    control(store, "pause", 1, "pause-worker")
    agent, _ = store.add_work("demo", "semantic_task", "agent", {"evidence": []}, "test")
    deterministic, _ = store.add_work("demo", "source_batch", "deterministic", {}, "test")
    worker = Worker(store, app.state.settings)
    assert worker.run_once() == {
        "work_id": deterministic,
        "route": "deterministic",
        "status": "succeeded",
    }
    assert worker.run_once() is None
    assert store.work("demo", agent)["state"] == "queued"
    assert not store.db.execute("SELECT 1 FROM agent_runs").fetchone()


def test_large_paused_queue_returns_idle_without_recursion(tmp_path):
    app, _ = app_and_client(tmp_path)
    store = app.state.store
    control(store, "pause", 1, "pause-large-queue")
    for index in range(1050):
        store.add_work("demo", "semantic_task", f"paused-{index}", {}, "test")
    assert Worker(store, app.state.settings).run_once() is None
    assert all(row["state"] == "queued" for row in store.work("demo"))


def test_project_agent_concurrency_limit_still_allows_deterministic_work(tmp_path):
    app, _ = app_and_client(tmp_path)
    store = app.state.store
    semantic, _ = store.add_work("demo", "semantic_task", "active-slot", {}, "test")
    deterministic, _ = store.add_work("demo", "publication_task", "deterministic-slot", {}, "test")
    leased = store.claim("demo", "worker-one", agent_concurrency_limit=1)
    assert leased["id"] == semantic
    result = Worker(store, app.state.settings, owner="worker-two").run_once()
    assert result == {
        "work_id": deterministic,
        "route": "deterministic",
        "status": "succeeded",
    }
    assert Worker(store, app.state.settings, owner="worker-two").run_once() is None


def test_autonomy_disabled_escalates_without_agent_dispatch(tmp_path, monkeypatch):
    app, _ = app_and_client(tmp_path)
    store = app.state.store
    apply_revision(
        store,
        app.state.settings,
        "demo",
        "human:admin",
        "test:autonomy",
        1,
        "disable autonomous dispatch",
        "autonomy-off",
        changes={"autonomy_enabled": False},
    )
    work_id, _ = store.add_work("demo", "semantic_task", "autonomy-off", {}, "test")
    monkeypatch.setattr(
        "context_library_manager.worker.invoke",
        lambda *_args: pytest.fail("agent must not be invoked"),
    )
    result = Worker(store, app.state.settings).run_once()
    assert result == {
        "work_id": work_id,
        "status": "waiting-human",
        "reason": "autonomy-disabled",
    }
    assert store.work("demo", work_id)["state"] == "waiting-human"
    assert not store.db.execute("SELECT 1 FROM agent_runs WHERE work_id=?", (work_id,)).fetchone()


def test_excluded_category_and_low_confidence_require_human_review(tmp_path, monkeypatch):
    app, _ = app_and_client(tmp_path)
    store = app.state.store
    excluded, _ = store.add_work("demo", "semantic_task", "excluded-policy", {"category": "security"}, "test")
    first = Worker(store, app.state.settings).run_once()
    assert first == {
        "work_id": excluded,
        "status": "waiting-human",
        "reason": "excluded-category",
    }
    assert not store.db.execute("SELECT 1 FROM agent_runs WHERE work_id=?", (excluded,)).fetchone()

    monkeypatch.setenv("CLM_AGENT_COMMAND", "configured-adapter")
    low, _ = store.add_work("demo", "semantic_task", "low-confidence", {"category": "product"}, "test")
    monkeypatch.setattr(
        "context_library_manager.worker.invoke",
        lambda *_args, **_kwargs: AgentResult("ok", {"candidate": "bounded"}, 2, 3, 0.1),
    )
    second = Worker(store, app.state.settings).run_once()
    assert second == {
        "work_id": low,
        "status": "waiting-human",
        "reason": "confidence-below-threshold",
    }
    assert store.work("demo", low)["state"] == "waiting-human"


def test_proposal_agent_result_is_queued_for_clm_reconciliation(tmp_path, monkeypatch):
    app, _ = app_and_client(tmp_path)
    store = app.state.store
    work_id, _ = store.add_work(
        "demo",
        "candidate_task",
        "proposal-normalization",
        {
            "proposal_id": "proposal-one",
            "operation": "revise",
            "decision_id": "existing-react-ui",
            "proposed_fields": {"decision": "Keep React."},
            "rationale": "Established direction.",
            "authority": "Product owner",
            "publication_intent": True,
            "base_library_digest": "a" * 64,
            "evidence": [{"observation_id": "obs-one"}],
        },
        "test",
    )
    candidate = {
        "schema": "context-library/candidate",
        "schema_version": 1,
        "candidate_id": "keep-react-ui",
        "project": "demo",
    }
    captured = {}
    monkeypatch.setenv("CLM_AGENT_COMMAND", "configured-adapter")

    def agent(_command, request, **_kwargs):
        captured.update(request)
        return AgentResult("ok", candidate, 2, 3, 1.0)

    monkeypatch.setattr("context_library_manager.worker.invoke", agent)
    result = Worker(store, app.state.settings).run_once()
    assert result == {
        "work_id": work_id,
        "status": "queued",
        "reason": "candidate-normalized",
        "cache_hit": False,
    }
    work = store.work("demo", work_id)
    assert work["state"] == "queued"
    assert json.loads(work["payload"])["clm_payload"] == candidate
    assert captured["task_context"]["operation"] == "revise"
    assert captured["task_context"]["decision_id"] == "existing-react-ui"
    assert captured["task_context"]["proposed_fields"] == {"decision": "Keep React."}

    class FakeMaintainer:
        def __init__(self, context):
            captured["maintainer_project"] = context.project

        def add_candidate(self, payload):
            assert payload == candidate
            return {"candidate_id": candidate["candidate_id"]}

        def reconcile(self, candidate_id):
            assert candidate_id == candidate["candidate_id"]
            return {"status": "ok", "conflicted": [], "invalid": []}

    monkeypatch.setattr(
        "context_library_manager.worker.MaintainerApplicationService",
        FakeMaintainer,
    )
    completed = Worker(store, app.state.settings).run_once()
    assert completed["status"] == "waiting-human"
    assert completed["reason"] == "publication-approval-required"
    assert store.work("demo", work_id)["state"] == "waiting-human"
    review = store.db.execute("SELECT choices FROM reviews WHERE work_id=?", (work_id,)).fetchone()
    assert json.loads(review["choices"]) == ["retain-current", "adopt-candidate"]

    assert captured["maintainer_project"] == "demo"


def test_cancel_active_run_is_cooperative_idempotent_and_audited(tmp_path):
    app, client = app_and_client(tmp_path)
    store = app.state.store
    work_id, _ = store.add_work("demo", "semantic_task", "cancel", {}, "test")
    store.transition("demo", work_id, "leased", "worker")
    store.transition("demo", work_id, "running", "worker")
    store.start_agent_run("agent-active", work_id, "cheap", "worker", "1", "cache")
    path = f"/api/v1/projects/demo/agent-runs/{work_id}/cancel"
    body = {
        "schema_version": 1,
        "reason": "incorrect source",
        "idempotency_key": "cancel-one",
    }
    first = client.post(path, headers=csrf(client, path), json=body)
    assert first.status_code == 200
    assert first.json()["status"] == "pending"
    repeated = client.post(path, headers=csrf(client, path), json=body)
    assert repeated.status_code == 200
    assert repeated.json()["data"]["idempotent"] is True
    assert store.work("demo", work_id)["state"] == "cancel-requested"
    assert store.cancellation_requested("agent-active")
    store.acknowledge_cancellation("demo", work_id, "agent-active", "worker", input_tokens=11, output_tokens=3)
    run = store.db.execute(
        "SELECT status,input_tokens,output_tokens FROM agent_runs WHERE id='agent-active'"
    ).fetchone()
    assert dict(run) == {"status": "canceled", "input_tokens": 11, "output_tokens": 3}
    assert store.work("demo", work_id)["state"] == "canceled"
    assert not store.db.execute("SELECT 1 FROM publication_history WHERE work_id=?", (work_id,)).fetchone()
    assert store.db.execute(
        "SELECT 1 FROM audit_events WHERE work_id=? AND event_type='agent-run-cancel-requested'",
        (work_id,),
    ).fetchone()


def test_worker_cancellation_settles_incurred_usage_and_releases_reservation(tmp_path, monkeypatch):
    app, _ = app_and_client(tmp_path)
    store = app.state.store
    monkeypatch.setenv("CLM_AGENT_COMMAND", "configured-adapter")
    work_id, _ = store.add_work("demo", "semantic_task", "budget-cancel", {}, "test")

    def cancel_during_invoke(_command, request, **_kwargs):
        run_id = request["run_id"]
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        store.db.execute(
            "INSERT INTO work_cancellations VALUES(?,?,?,?,?,?,?,?,?)",
            (
                "cancel-budget",
                work_id,
                run_id,
                "cancel-requested",
                "human:test",
                "stop",
                "budget-key",
                now,
                None,
            ),
        )
        store.db.execute("UPDATE agent_runs SET status='cancel-requested' WHERE id=?", (run_id,))
        store.transition("demo", work_id, "cancel-requested", "human:test", "stop")
        return AgentResult("ok", {"discarded": True}, 13, 5)

    monkeypatch.setattr("context_library_manager.worker.invoke", cancel_during_invoke)
    assert Worker(store, app.state.settings).run_once()["status"] == "canceled"
    budget = store.db.execute(
        "SELECT reserved_tokens,spent_tokens FROM project_budgets WHERE project='demo'"
    ).fetchone()
    assert dict(budget) == {"reserved_tokens": 0, "spent_tokens": 18}
    assert not store.db.execute("SELECT 1 FROM publication_history WHERE work_id=?", (work_id,)).fetchone()


def test_expired_cancellation_releases_budget_and_unblocks_drain(tmp_path):
    app, client = app_and_client(tmp_path)
    store = app.state.store
    work_id, _ = store.add_work("demo", "semantic_task", "lost-cancel", {}, "test")
    assert store.claim("demo", "lost-worker")["id"] == work_id
    store.transition("demo", work_id, "running", "lost-worker")
    store.start_agent_run(
        "agent-lost",
        work_id,
        "cheap",
        "lost-worker",
        "1",
        "lost-cache",
        reserved_tokens=2000,
    )
    assert store.reserve_budget("demo", "lost-worker", 2000, 500_000, 100_000)
    path = f"/api/v1/projects/demo/agent-runs/{work_id}/cancel"
    response = client.post(
        path,
        headers=csrf(client, path),
        json={
            "schema_version": 1,
            "reason": "worker disappeared",
            "idempotency_key": "lost-cancel",
        },
    )
    assert response.status_code == 200
    store.db.execute(
        "UPDATE work_items SET lease_expires='2020-01-01T00:00:00Z' WHERE id=?",
        (work_id,),
    )
    store.db.commit()
    assert control(store, "drain", 1, "drain-lost-cancel")["state"] == "draining"
    assert store.recover_expired("demo", "scheduler", 3) == 1
    assert complete_drain(store, "scheduler") is True
    assert store.work("demo", work_id)["state"] == "canceled"
    assert store.service_state()["state"] == "paused"
    budget = store.db.execute("SELECT reserved_tokens FROM project_budgets WHERE project='demo'").fetchone()
    assert budget["reserved_tokens"] == 0


def test_maintainer_can_retry_canceled_work(tmp_path):
    app, client = app_and_client(tmp_path)
    store = app.state.store
    work_id, _ = store.add_work("demo", "semantic_task", "retry-canceled", {}, "test")
    store.db.execute("UPDATE work_items SET state='canceled' WHERE id=?", (work_id,))
    store.db.commit()
    path = f"/api/v1/projects/demo/runs/{work_id}/retry"
    response = client.post(
        path,
        headers={**csrf(client, path), "Idempotency-Key": "retry-canceled"},
    )
    assert response.status_code == 200
    assert store.work("demo", work_id)["state"] == "queued"
    assert store.db.execute(
        "SELECT 1 FROM audit_events WHERE work_id=? AND event_type='retry-requested'",
        (work_id,),
    ).fetchone()


def test_agent_error_usage_is_settled_once_and_recorded(tmp_path, monkeypatch):
    app, _ = app_and_client(tmp_path)
    store = app.state.store
    monkeypatch.setenv("CLM_AGENT_COMMAND", "configured-adapter")
    work_id, _ = store.add_work("demo", "semantic_task", "usage-error", {}, "test")
    monkeypatch.setattr(
        "context_library_manager.worker.invoke",
        lambda _command, _request, **_kwargs: AgentResult("error", {}, 9, 4),
    )
    assert Worker(store, app.state.settings).run_once()["status"] == "retryable"
    budget = store.db.execute(
        "SELECT reserved_tokens,spent_tokens FROM project_budgets WHERE project='demo'"
    ).fetchone()
    assert dict(budget) == {"reserved_tokens": 0, "spent_tokens": 13}
    run = store.db.execute(
        "SELECT input_tokens,output_tokens,status FROM agent_runs WHERE work_id=?",
        (work_id,),
    ).fetchone()
    assert dict(run) == {"input_tokens": 9, "output_tokens": 4, "status": "error"}


def test_provider_request_is_recursively_redacted_and_secret_response_is_rejected(tmp_path, monkeypatch):
    app, _ = app_and_client(tmp_path)
    store = app.state.store
    monkeypatch.setenv("CLM_AGENT_COMMAND", "configured-adapter")
    work_id, _ = store.add_work(
        "demo",
        "semantic_task",
        "provider-redaction",
        {
            "proposed_fields": {"client_secret": "never-send", "decision": "safe"},
            "rationale": "authorization=Bearer never-send-either",
        },
        "test",
    )
    captured = {}

    def provider(_command, request, **_kwargs):
        captured.update(request)
        return AgentResult("ok", {"decision": "safe"}, 1, 1)

    monkeypatch.setattr("context_library_manager.worker.invoke", provider)
    assert Worker(store, app.state.settings).run_once()["status"] == "succeeded"
    serialized = json.dumps(captured)
    assert "never-send" not in serialized
    assert "[REDACTED]" in serialized

    script = tmp_path / "secret-provider.py"
    script.write_text(
        "import json,sys\n"
        "request=json.load(sys.stdin)\n"
        "print(json.dumps({'schema_version':1,'run_id':request['run_id'],'status':'ok',"
        "'result':{'api_key':'provider-secret'},'usage':{'input_tokens':1,'output_tokens':1}}))\n",
        encoding="utf-8",
    )
    request = {
        "schema_version": 1,
        "run_id": "secret-run",
        "project": "demo",
        "task_type": "semantic_task",
        "actor": "worker",
        "model_profile": "cheap",
        "budget": {"max_input_tokens": 10, "max_output_tokens": 10},
        "prompt_revision": "1",
        "evidence": [],
        "task_context": {},
        "required_output_schema": "candidate-v1",
    }
    with pytest.raises(ValueError, match="secret pattern"):
        invoke([sys.executable, str(script)], request)

    sleeper = tmp_path / "sleep-provider.py"
    sleeper.write_text("import time\ntime.sleep(60)\n", encoding="utf-8")
    with pytest.raises(TimeoutError, match="timeout"):
        invoke([sys.executable, str(sleeper)], request, timeout=0.01)


def test_public_agent_run_id_matches_processing_run_id(tmp_path):
    app, client = app_and_client(tmp_path)
    store = app.state.store
    work_id, _ = store.add_work("demo", "semantic_task", "public-run-id", {}, "test")
    store.start_agent_run("private-invocation", work_id, "cheap", "worker", "1", "cache")
    listing = client.get("/api/v1/projects/demo/agent-runs").json()["data"]["items"]
    assert listing[0]["run_id"] == work_id
    assert listing[0]["agent_invocation_id"] == "private-invocation"
    detail = client.get(f"/api/v1/projects/demo/agent-runs/{work_id}").json()["data"]
    assert detail["run_id"] == work_id
    assert detail["agent_invocation_id"] == "private-invocation"


def test_concurrent_cancel_requests_return_conflict_not_server_error(tmp_path):
    app, client = app_and_client(tmp_path)
    store = app.state.store
    work_id, _ = store.add_work("demo", "semantic_task", "cancel-race", {}, "test")
    store.transition("demo", work_id, "leased", "worker")
    store.transition("demo", work_id, "running", "worker")
    store.start_agent_run("agent-race", work_id, "cheap", "worker", "1", "cache")
    path = f"/api/v1/projects/demo/agent-runs/{work_id}/cancel"
    token = csrf(client, path)["X-CSRF-Token"]

    def cancel(key):
        return client.post(
            path,
            headers={"X-CSRF-Token": token},
            json={"schema_version": 1, "reason": "race test", "idempotency_key": key},
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = sorted(pool.map(cancel, ["race-one", "race-two"]))
    assert statuses == [200, 409]
    assert (
        store.db.execute("SELECT COUNT(*) AS n FROM work_cancellations WHERE agent_run_id='agent-race'").fetchone()["n"]
        == 1
    )


def test_two_administrators_cannot_commit_same_service_version(tmp_path):
    _, client = app_and_client(tmp_path)
    path = "/api/v1/agent-service/pause"
    token = csrf(client, path)["X-CSRF-Token"]

    def pause(key):
        return client.post(
            path,
            headers={"X-CSRF-Token": token},
            json={
                "schema_version": 1,
                "expected_version": 1,
                "reason": "concurrent administrator test",
                "idempotency_key": key,
            },
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = sorted(pool.map(pause, ["admin-one", "admin-two"]))
    assert statuses == [200, 409]


def test_agent_service_page_uses_selected_project_data(tmp_path):
    app, client = app_and_client(tmp_path, projects=("demo", "other"), selected="other")
    store = app.state.store
    work_id, _ = store.add_work("other", "semantic_task", "selected", {}, "test")
    store.transition("other", work_id, "leased", "worker")
    store.transition("other", work_id, "running", "worker")
    store.start_agent_run("agent-other", work_id, "cheap", "worker", "1", "cache")
    page = client.get("/agent-service")
    assert page.status_code == 200
    assert work_id in page.text


def test_agent_run_collection_paginates_and_detail_uses_effective_budget(tmp_path):
    app, client = app_and_client(tmp_path)
    store = app.state.store
    apply_revision(
        store,
        app.state.settings,
        "demo",
        "human:admin",
        "test:run-budget",
        1,
        "lower item budget",
        "run-budget",
        changes={"item_token_budget": 9000},
    )
    first_work = None
    for index in range(105):
        work_id, _ = store.add_work("demo", "semantic_task", f"page-run-{index}", {}, "test")
        first_work = first_work or work_id
        store.start_agent_run(f"agent-page-{index}", work_id, "cheap", "worker", "1", f"cache-{index}")
        store.finish_agent_run(f"agent-page-{index}", "ok", 1, 1)
    first = client.get("/api/v1/projects/demo/agent-runs", params={"page": 1, "page_size": 100}).json()["data"]
    second = client.get("/api/v1/projects/demo/agent-runs", params={"page": 2, "page_size": 100}).json()["data"]
    assert first["total"] == 105
    assert first["next_page"] == 2
    assert len(first["items"]) == 100
    assert len(second["items"]) == 5
    detail = client.get(f"/api/v1/projects/demo/agent-runs/{first_work}").json()["data"]
    assert detail["budget"]["item_limit"] == 9000


def test_heartbeat_freshness_does_not_mutate_operator_state(tmp_path):
    app, _ = app_and_client(tmp_path)
    store = app.state.store
    assert heartbeat_health(None) == "offline"
    fresh = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    degraded = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat().replace("+00:00", "Z")
    offline = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat().replace("+00:00", "Z")
    assert heartbeat_health(fresh) == "healthy"
    assert heartbeat_health(degraded) == "degraded"
    assert heartbeat_health(offline) == "offline"
    original = dict(store.service_state())
    store.heartbeat("worker", "test", details={"safe": True})
    assert dict(store.service_state()) == original
    details = store.db.execute("SELECT details FROM process_heartbeats WHERE process='worker'").fetchone()["details"]
    assert json.loads(details) == {"safe": True}
