import re

from fastapi.testclient import TestClient

from context_library_manager.api import create_app
from context_library_manager.config import Settings
from context_library_manager.domain import utc_now

REGISTER = """# Decision Register

## Product

<a id="decision-one"></a>
### A project decision
- Date: 2026-01-01
- Decisionmaker: Product Owner
- Decision: Keep the project decision.
- Rationale: It is the current direction.
- Provenance: explicit
- Evidence:
  - ticket://ONE Evidence.
"""


def app_and_client(tmp_path, projects=("demo",), selected="demo"):
    library = tmp_path / "library"
    for project in projects:
        root = library / "projects" / project
        root.mkdir(parents=True)
        (root / "decision-register.md").write_text(REGISTER)
    settings = Settings(
        "sqlite:///" + str(tmp_path / "runtime.db"),
        library,
        tmp_path / "state",
        "demo",
        require_oidc=False,
        oidc_hs256_secret="test-secret",
        allow_local_dev_identity=True,
        development_mode=True,
        session_secret="overview-shell-session-secret",
    )
    app = create_app(settings)
    now = utc_now()
    for project in projects:
        app.state.store.db.execute(
            "INSERT INTO projects(id,name,created_at,updated_at) VALUES(?,?,?,?) ON CONFLICT(id) DO NOTHING",
            (project, project.title(), now, now),
        )
    app.state.store.db.commit()
    client = TestClient(app, base_url="https://testserver")
    response = client.post(
        "/auth/dev-login",
        json={
            "subject": "fixture:admin",
            "display_name": "Fixture Admin",
            "capabilities": ["admin"],
            "projects": list(projects),
            "selected_project": selected,
        },
    )
    assert response.status_code == 200
    return app, client


def csrf(client, path):
    response = client.get("/api/v1/session/csrf", params={"method": "POST", "path": path})
    assert response.status_code == 200
    return {"X-CSRF-Token": response.json()["data"]["csrf_token"]}


def test_overview_attention_values_and_explanatory_links(tmp_path):
    app, client = app_and_client(tmp_path)
    failed, _ = app.state.store.add_work("demo", "candidate_task", "failed", {"category": "product"}, "test")
    app.state.store.db.execute("UPDATE work_items SET state='failed' WHERE id=?", (failed,))
    app.state.store.create_review("demo", failed, "Choose a recovery", ["retry", "retain-current"], [], "test")
    app.state.store.db.execute(
        "INSERT INTO publication_history(id,project,status,digest,git_revision,created_at) VALUES(?,?,?,?,?,?)",
        ("pub-1", "demo", "succeeded", "d" * 64, "rev-1", utc_now()),
    )
    app.state.store.db.commit()

    response = client.get("/api/v1/projects/demo/overview")
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["attention"]["review"] == 1
    assert data["attention"]["failed"] == 1
    assert data["last_publication"]["git_revision"] == "rev-1"
    assert data["budget"]["remaining"] == data["budget"]["limit"]
    assert data["agent_service"]["health"] == "offline"

    page = client.get("/")
    assert page.status_code == 200
    assert 'href="/reviews?status=open"' in page.text
    assert 'href="/agent-service/runs?state=failed"' in page.text
    assert 'data-island="overview"' in page.text
    assert "No publication recorded" not in page.text
    assert "Agent Service: offline (operator state running)" in page.text
    for path in (
        "/agent-service",
        "/agent-service/runs?state=failed",
        "/agent-service/runs?state=retryable",
        "/agent-service/runs?state=stale",
        "/agent-service/runs?state=waiting-human",
        "/configuration",
        "/audit?event_type=review-created",
    ):
        assert client.get(path).status_code == 200, path
    failed_page = client.get("/agent-service/runs?state=failed")
    assert failed in failed_page.text
    assert "Filtered by failed" in failed_page.text
    missing = client.get("/not-a-real-page")
    assert missing.status_code == 404
    assert "Page unavailable" in missing.text
    assert "Context Library Manager" in missing.text


def test_project_selection_is_scoped_persisted_and_updates_shell(tmp_path):
    _, client = app_and_client(tmp_path, projects=("demo", "other"))
    path = "/api/v1/session/project"
    switched = client.post(path, headers=csrf(client, path), json={"project": "other"})
    assert switched.status_code == 200
    assert client.get("/api/v1/session").json()["data"]["selected_project"] == "other"
    page = client.get("/")
    assert page.status_code == 200
    assert 'data-project="other"' in page.text
    assert "Other" in page.text

    token = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
    fallback = client.post(
        "/session/project",
        data={"project": "demo", "csrf_token": token},
        follow_redirects=False,
    )
    assert fallback.status_code == 303
    assert client.get("/api/v1/session").json()["data"]["selected_project"] == "demo"
    denied = client.post(path, headers=csrf(client, path), json={"project": "outside-scope"})
    assert denied.status_code == 404
    assert client.get("/api/v1/session").json()["data"]["selected_project"] == "demo"


def test_proposal_detail_uses_selected_project(tmp_path):
    app, client = app_and_client(tmp_path, projects=("demo", "other"), selected="other")
    app.state.store.db.execute(
        "INSERT INTO library_proposals VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "proposal-other",
            "other",
            None,
            "create",
            "queued",
            "human:fixture:admin",
            "work-other",
            "contribution-other",
            "a" * 64,
            '{"rationale":"other project rationale","proposed_fields":{}}',
            utc_now(),
            utc_now(),
        ),
    )
    app.state.store.db.commit()
    page = client.get("/library/proposals/proposal-other")
    assert page.status_code == 200
    assert "other project rationale" in page.text
    assert 'data-project="other"' in page.text


def test_source_ingestion_uses_requested_non_default_project(tmp_path, monkeypatch):
    app, client = app_and_client(tmp_path, projects=("demo", "other"), selected="other")
    path = "/api/v1/projects/other/sources"
    response = client.post(
        path,
        headers=csrf(client, path),
        json={
            "external_id": "other-source",
            "source_type": "ticket",
            "uri": "ticket://other-source",
            "content": "Other project direction.",
            "retrieved_at": "2026-07-17T00:00:00Z",
        },
    )
    assert response.status_code == 200
    work_id = response.json()["data"]["work_id"]
    assert app.state.store.work("other", work_id)["state"] == "succeeded"
    assert app.state.store.work("demo", work_id) is None


def test_overview_uses_latest_policy_revision_and_audit_filter(tmp_path):
    app, client = app_and_client(tmp_path)
    app.state.store.db.execute(
        "INSERT INTO policy_revisions(id,project,revision,payload,created_at) VALUES(?,?,?,?,?)",
        ("policy-demo-2", "demo", "2", "{}", utc_now()),
    )
    app.state.store.event(None, "fixture", "one-event", {}, "demo")
    app.state.store.event(None, "fixture", "other-event", {}, "demo")
    app.state.store.db.commit()
    overview = client.get("/api/v1/projects/demo/overview").json()["data"]
    assert overview["autonomy"]["policy_revision"] == "2"
    audit = client.get("/audit?event_type=one-event")
    assert "one-event" in audit.text
    assert "other-event" not in audit.text
