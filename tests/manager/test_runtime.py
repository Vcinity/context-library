import base64
import hashlib
import hmac
import json
import sys
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from context_library_manager.agent import invoke
from context_library_manager.api import create_app
from context_library_manager.config import ConfigurationError, Settings
from context_library_manager.db import Store
from context_library_manager.domain import RouteRequest
from context_library_manager.notifications import deliver_pending
from context_library_manager.routing import route
from context_library_manager.worker import Worker


def sign_in(client: TestClient, capability: str = "admin") -> dict:
    response = client.post(
        "/auth/dev-login",
        json={
            "subject": f"fixture:{capability}",
            "display_name": f"Fixture {capability}",
            "capabilities": [capability],
            "projects": ["demo"],
            "selected_project": "demo",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


def csrf_headers(client: TestClient, path: str, method: str = "POST") -> dict:
    response = client.get("/api/v1/session/csrf", params={"method": method, "path": path})
    assert response.status_code == 200, response.text
    return {"X-CSRF-Token": response.json()["data"]["csrf_token"]}


def development_client(app, capability: str = "admin") -> TestClient:
    client = TestClient(app, base_url="https://testserver")
    sign_in(client, capability)
    return client


def test_deterministic_routes_do_not_need_agent():
    decision = route(RouteRequest(operation="publication"))
    assert decision.route == "deterministic"
    assert decision.profile is None


def test_semantic_work_uses_cheap_profile():
    decision = route(RouteRequest(operation="candidate", semantic_fields=["rationale"], input_tokens=1200))
    assert decision.route == "agent"
    assert decision.profile == "cheap"


def test_state_machine_and_review_are_idempotent(tmp_path):
    store = Store(tmp_path / "runtime.db")
    work_id, created = store.add_work("demo", "candidate_task", "key", {"subject": "x"}, "test")
    assert created
    store.transition("demo", work_id, "leased", "worker")
    store.transition("demo", work_id, "running", "worker")
    review_id = store.create_review("demo", work_id, "Which directive applies?", ["a", "b"], ["obs_1"], "worker")
    assert store.create_review("demo", work_id, "ignored", ["a"], [], "worker") == review_id
    assert store.db.execute("SELECT COUNT(*) FROM notifications").fetchone()[0] == 1
    store.transition("demo", work_id, "waiting-human", "worker")
    try:
        store.transition("demo", work_id, "leased", "worker")
    except ValueError:
        pass
    else:
        raise AssertionError("waiting-human must not be leased")


def test_health_and_source_idempotency(tmp_path):
    settings = Settings(
        "sqlite:///" + str(tmp_path / "runtime.db"),
        tmp_path / "library",
        tmp_path / "clm-state",
        "demo",
        require_oidc=False,
        session_secret="test-session-secret",
        allow_local_dev_identity=True,
        development_mode=True,
    )
    client = development_client(create_app(settings), "maintain")
    health = client.get("/api/v1/health")
    assert health.status_code == 200 and health.json()["data"]["status"] == "offline"
    assert any(item["process"] == "api" and item["state"] == "healthy" for item in health.json()["data"]["heartbeats"])
    source = {
        "external_id": "T-1",
        "source_type": "ticket",
        "uri": "jira://T-1",
        "content": "Keep UI product-owned.",
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
    }
    first = client.post(
        "/api/v1/projects/demo/sources",
        json=source,
        headers={
            "Idempotency-Key": "same",
            **csrf_headers(client, "/api/v1/projects/demo/sources"),
        },
    )
    second = client.post(
        "/api/v1/projects/demo/sources",
        json=source,
        headers={
            "Idempotency-Key": "same",
            **csrf_headers(client, "/api/v1/projects/demo/sources"),
        },
    )
    assert first.status_code == second.status_code == 200
    assert first.json()["data"]["work_id"] == second.json()["data"]["work_id"]
    assert second.json()["data"]["idempotent"] is True


def test_html_review_role_comes_from_authenticated_request(tmp_path):
    settings = Settings(
        "sqlite:///" + str(tmp_path / "runtime.db"),
        tmp_path / "library",
        tmp_path / "clm-state",
        "demo",
        require_oidc=False,
        allow_local_dev_identity=True,
        development_mode=True,
    )
    app = create_app(settings)
    work_id, _ = app.state.store.add_work("demo", "relationship_task", "html-review", {"category": "product"}, "test")
    app.state.store.transition("demo", work_id, "leased", "worker")
    app.state.store.transition("demo", work_id, "running", "worker")
    review_id = app.state.store.create_review(
        "demo", work_id, "Choose", ["retain-current", "adopt-candidate"], [], "worker"
    )
    client = TestClient(app, base_url="https://testserver")
    forged = client.post(
        f"/reviews/{review_id}/resolve",
        data={"choice": "retain-current", "rationale": "because", "role": "reviewer"},
        headers={"X-Role": "reviewer"},
        follow_redirects=False,
    )
    assert forged.status_code == 303
    sign_in(client, "review")
    page = client.get(f"/reviews/{review_id}")
    csrf_token = page.text.split("name='csrf_token' value='")[1].split("'")[0]
    idempotency_key = page.text.split('name="idempotency_key" value="')[1].split('"')[0]
    authenticated = client.post(
        f"/reviews/{review_id}/resolve",
        data={
            "choice": "retain-current",
            "rationale": "because",
            "csrf_token": csrf_token,
            "idempotency_key": idempotency_key,
        },
        headers={"X-Role": "reviewer"},
    )
    assert authenticated.status_code == 200


def test_required_read_only_web_screens_and_evidence_redaction(tmp_path):
    settings = Settings(
        "sqlite:///" + str(tmp_path / "runtime.db"),
        tmp_path / "library",
        tmp_path / "clm-state",
        "demo",
        require_oidc=False,
        allow_local_dev_identity=True,
        development_mode=True,
    )
    app = create_app(settings)
    work_id, _ = app.state.store.add_work(
        "demo",
        "observation_task",
        "screen",
        {"observation_id": "obs-screen", "content": "secret source body"},
        "test",
    )
    client = development_client(app, "read")
    for path in (
        "/",
        "/decisions",
        "/evidence/obs-screen",
        "/reviews",
        "/publications",
        "/health",
        "/audit",
    ):
        assert client.get(path).status_code == 200
    evidence = client.get("/api/v1/projects/demo/evidence/obs-screen").json()
    assert "content" not in evidence["data"]["matches"][0]


def test_strict_runtime_config_and_budgetless_agent_escalation(tmp_path, monkeypatch):
    library = tmp_path / "library"
    project = library / "projects" / "demo"
    project.mkdir(parents=True)
    (project / "runtime.yaml").write_text(
        "schema_version: 1\nruntime:\n  worker_concurrency: 2\ncost:\n  item_token_budget: 100\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CLM_LIBRARY_ROOT", str(library))
    settings = Settings.from_env("demo")
    assert settings.worker_concurrency == 2
    assert settings.item_token_budget == 100
    (project / "runtime.yaml").write_text("schema_version: 1\nunknown: true\n", encoding="utf-8")
    try:
        Settings.from_env("demo")
    except ConfigurationError:
        pass
    else:
        raise AssertionError("unknown configuration must be rejected")


def test_contribution_is_queued_and_missing_agent_creates_review(tmp_path, monkeypatch):
    monkeypatch.delenv("CLM_AGENT_COMMAND", raising=False)
    settings = Settings(
        "sqlite:///" + str(tmp_path / "runtime.db"),
        tmp_path / "library",
        tmp_path / "clm-state",
        "demo",
        require_oidc=False,
        allow_local_dev_identity=True,
        development_mode=True,
    )
    client = development_client(create_app(settings), "maintain")
    response = client.post(
        "/api/v1/projects/demo/contributions",
        headers={
            "Idempotency-Key": "contrib-1",
            **csrf_headers(client, "/api/v1/projects/demo/contributions"),
        },
        json={
            "kind": "candidate",
            "payload": {"subject": "ambiguous"},
            "evidence_references": ["obs-1"],
            "client_idempotency_key": "contrib-1",
        },
    )
    assert response.status_code == 200
    result = Worker(Store(settings.db_path), settings).run_once()
    assert result["status"] == "waiting-human"
    reviews = client.get("/api/v1/projects/demo/reviews").json()["data"]["reviews"]
    assert len(reviews) == 1
    review_id = reviews[0]["id"]
    sign_in(client, "read")
    denied = client.post(
        f"/api/v1/projects/demo/reviews/{review_id}/resolve",
        headers=csrf_headers(client, f"/api/v1/projects/demo/reviews/{review_id}/resolve"),
        json={"choice": "defer", "rationale": "safe", "idempotency_key": "r-1"},
    )
    assert denied.status_code == 403
    sign_in(client, "review")
    resolved = client.post(
        f"/api/v1/projects/demo/reviews/{review_id}/resolve",
        headers=csrf_headers(client, f"/api/v1/projects/demo/reviews/{review_id}/resolve"),
        json={"choice": "defer", "rationale": "safe", "idempotency_key": "r-1"},
    )
    assert resolved.status_code == 200
    assert client.app.state.store.work("demo", result["work_id"])["state"] == "waiting-human"
    evidence = client.app.state.store.db.execute(
        "SELECT kind FROM review_evidence WHERE review_id=?", (review_id,)
    ).fetchone()
    assert evidence["kind"] == "human-resolution"
    assert deliver_pending(client.app.state.store) == {"delivered": 1, "failed": 0}


def test_contribution_authorization_precedes_mutation_and_replay_checks_content(tmp_path):
    settings = Settings(
        "sqlite:///" + str(tmp_path / "runtime.db"),
        tmp_path / "library",
        tmp_path / "clm-state",
        "demo",
        require_oidc=False,
        allow_local_dev_identity=True,
        development_mode=True,
    )
    app = create_app(settings)
    client = development_client(app, "maintain")
    path = "/api/v1/projects/demo/contributions"
    headers = csrf_headers(client, path)
    denied = client.post(
        path,
        headers=headers,
        json={
            "kind": "candidate",
            "payload": {"publish": True},
            "client_idempotency_key": "unauthorized-publication",
        },
    )
    assert denied.status_code == 403
    assert app.state.store.db.execute("SELECT COUNT(*) AS n FROM contributions").fetchone()["n"] == 0

    first = client.post(
        path,
        headers=headers,
        json={
            "kind": "finding",
            "payload": {"finding": "one"},
            "client_idempotency_key": "digest-key",
        },
    )
    conflicting = client.post(
        path,
        headers=headers,
        json={
            "kind": "finding",
            "payload": {"finding": "two"},
            "client_idempotency_key": "digest-key",
        },
    )
    assert first.status_code == 200
    assert conflicting.status_code == 409
    assert conflicting.json()["errors"][0]["code"] == "idempotency-conflict"


def test_agent_adapter_validates_schema_and_budget(tmp_path):
    script = tmp_path / "agent.py"
    script.write_text(
        "import json,sys\n"
        "request=json.load(sys.stdin)\n"
        "print(json.dumps({'schema_version':1,'run_id':request['run_id'],'status':'ok','result':{'kind':'candidate'},'usage':{'input_tokens':1,'output_tokens':1,'estimated_cost':0.0},'confidence':0.9,'warnings':[]}))\n",
        encoding="utf-8",
    )
    request = {
        "schema_version": 1,
        "run_id": "run-1",
        "project": "demo",
        "task_type": "candidate_task",
        "actor": "worker",
        "model_profile": "cheap",
        "budget": {"max_input_tokens": 2, "max_output_tokens": 2},
        "prompt_revision": "p1",
        "evidence": [],
        "required_output_schema": "candidate-v1",
    }
    result = invoke([sys.executable, str(script)], request)
    assert result.status == "ok"
    assert (result.input_tokens, result.output_tokens) == (1, 1)


def test_local_identity_must_be_explicitly_enabled(tmp_path):
    settings = Settings(
        "sqlite:///" + str(tmp_path / "runtime.db"),
        tmp_path / "library",
        tmp_path / "clm-state",
        "demo",
        require_oidc=False,
        session_secret="test-session-secret",
    )
    client = TestClient(create_app(settings))
    response = client.post(
        "/api/v1/projects/demo/contributions",
        headers={"X-Role": "administrator"},
        json={
            "kind": "candidate",
            "payload": {"publish": True},
            "client_idempotency_key": "untrusted-local",
        },
    )
    assert response.status_code == 401


def test_notification_failure_is_visible_and_backed_off(tmp_path):
    store = Store(tmp_path / "runtime.db")
    work_id, _ = store.add_work("demo", "candidate_task", "notify", {}, "test")
    store.create_review("demo", work_id, "Choose", ["a", "b"], [], "test")
    result = deliver_pending(store, "http://127.0.0.1:1/unavailable")
    assert result == {"delivered": 0, "failed": 1}
    notification = store.db.execute("SELECT * FROM notifications").fetchone()
    assert notification["status"] == "pending"
    assert notification["attempts"] == 1
    assert notification["next_attempt"] is not None


def test_notification_failure_redacts_durable_error_and_emits_lineage(tmp_path, monkeypatch):
    store = Store(tmp_path / "runtime.db")
    work_id, _ = store.add_work("demo", "candidate_task", "notify-secret", {}, "test")
    store.create_review("demo", work_id, "Choose", ["a", "b"], [], "test")
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("token=notification-secret")),
    )
    assert deliver_pending(store, "https://example.invalid/hook", "signing-secret") == {
        "delivered": 0,
        "failed": 1,
    }
    notification = store.db.execute("SELECT id,last_error FROM notifications").fetchone()
    assert "notification-secret" not in notification["last_error"]
    event = store.db.execute(
        "SELECT payload FROM telemetry_events WHERE producer='notification' "
        "AND event_type='notification-retry-scheduled' ORDER BY project_sequence DESC LIMIT 1"
    ).fetchone()
    assert json.loads(event["payload"]) == {
        "error_class": "RuntimeError",
        "notification_id": notification["id"],
        "status": "pending",
    }


def test_expired_lease_is_recovered_without_duplicate_terminal_effect(tmp_path):
    store = Store(tmp_path / "runtime.db")
    work_id, _ = store.add_work("demo", "candidate_task", "lease-1", {}, "test")
    assert store.claim("demo", "worker", lease_seconds=1)["id"] == work_id
    store.transition("demo", work_id, "running", "worker")
    store.start_agent_run(
        "agent-expired",
        work_id,
        "cheap",
        "worker",
        "1",
        "cache",
        reserved_tokens=2000,
    )
    assert store.reserve_budget("demo", "worker", 2000, 500_000, 100_000)
    store.db.execute(
        "UPDATE work_items SET lease_expires='2000-01-01T00:00:00Z' WHERE id=?",
        (work_id,),
    )
    store.db.commit()
    assert (
        store.recover_expired(
            "demo",
            "reconciler",
            max_attempts=3,
            item_token_budget=1000,
            cheap_profile_max_tokens=1000,
            standard_profile_max_tokens=1000,
        )
        == 1
    )
    assert store.work("demo", work_id)["state"] == "queued"
    assert store.db.execute("SELECT status FROM agent_runs WHERE id='agent-expired'").fetchone()["status"] == "error"
    assert (
        store.db.execute("SELECT reserved_tokens FROM project_budgets WHERE project='demo'").fetchone()[
            "reserved_tokens"
        ]
        == 0
    )
    events = store.db.execute("SELECT event_type FROM events WHERE work_id=? ORDER BY id", (work_id,)).fetchall()
    event_types = [row["event_type"] for row in events]
    assert "lease-expired" in event_types
    assert "requeued-after-expiry" in event_types


def test_agent_failure_retries_without_run_id_collision(tmp_path, monkeypatch):
    script = tmp_path / "failing-agent.py"
    script.write_text(
        "import json, sys\n"
        "request = json.load(sys.stdin)\n"
        "response = {'schema_version': 1, 'run_id': request['run_id'], "
        "'status': 'error', 'result': {}, 'usage': {'input_tokens': 0, "
        "'output_tokens': 0, 'estimated_cost': 0}, 'confidence': 0, "
        "'warnings': ['failure']}\n"
        "print(json.dumps(response))\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CLM_AGENT_COMMAND", f"{sys.executable} {script}")
    settings = Settings(
        "sqlite:///" + str(tmp_path / "runtime.db"),
        tmp_path / "library",
        tmp_path / "clm-state",
        "demo",
        require_oidc=False,
        allow_local_dev_identity=True,
        max_attempts=2,
    )
    store = Store(settings.db_path)
    work_id, _ = store.add_work("demo", "candidate_task", "agent-failure", {}, "test")
    worker = Worker(store, settings)
    assert worker.run_once()["status"] == "retryable"
    assert store.requeue_retryable("demo", "scheduler", 2) == 1
    assert worker.run_once()["status"] == "waiting-human"
    runs = store.db.execute("SELECT id, status FROM agent_runs WHERE work_id=?", (work_id,)).fetchall()
    assert len(runs) == 2
    assert len({row["id"] for row in runs}) == 2


def test_oidc_mode_rejects_missing_or_malformed_tokens(tmp_path):
    settings = Settings(
        "sqlite:///" + str(tmp_path / "runtime.db"),
        tmp_path / "library",
        tmp_path / "clm-state",
        "demo",
        True,
        session_secret="test-session-secret",
    )
    client = TestClient(create_app(settings))
    assert client.get("/api/v1/projects/demo/overview").status_code == 401
    payload = (
        base64.urlsafe_b64encode(
            json.dumps(
                {
                    "sub": "oidc-reader",
                    "preferred_username": "reader",
                    "roles": ["viewer"],
                    "projects": ["demo"],
                    "token_class": "human",
                    "exp": 4_000_000_000,
                }
            ).encode()
        )
        .decode()
        .rstrip("=")
    )
    header = base64.urlsafe_b64encode(b'{"alg":"HS256","typ":"JWT"}').decode().rstrip("=")
    signing_input = f"{header}.{payload}"
    signature = (
        base64.urlsafe_b64encode(hmac.new(b"test-secret", signing_input.encode(), hashlib.sha256).digest())
        .decode()
        .rstrip("=")
    )
    token = f"Bearer {signing_input}.{signature}"
    settings = Settings(
        settings.database_url,
        settings.library_root,
        settings.state_root,
        settings.project,
        True,
        oidc_hs256_secret="test-secret",
        session_secret="test-session-secret",
    )
    client = TestClient(create_app(settings))
    assert client.get("/api/v1/projects/demo/overview", headers={"Authorization": token}).status_code == 200
