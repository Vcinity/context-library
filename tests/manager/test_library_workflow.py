from __future__ import annotations

from fastapi.testclient import TestClient

from context_library_manager.api import create_app
from context_library_manager.config import Settings

REGISTER = """# Decision Register

## User Interface

<a id="ui-react"></a>
### Use React
- Date: 2026-01-01
- Decisionmaker: Product Owner
- Decision: Use React for the product UI.
- Rationale: It matches the established stack.
- Provenance: explicit
- Evidence:
  - https://example.invalid/messages/1 Product Owner confirmed token=secret-value.

<a id="ui-assumption"></a>
### Assumed layout
- Date: 2026-01-02
- Decisionmaker: Design Team
- Decision: Use the assumed layout.
- Rationale: This needs validation.
- Provenance: assumed
- Evidence:
  - ticket://UI-2 Layout note.

<a id="ui-old"></a>
### Earlier UI direction
- Date: 2025-01-01
- Decisionmaker: Product Owner
- Decision: Use the earlier direction.
- Rationale: It was the prior choice.
- Provenance: inferred
- Evidence:
  - ticket://UI-1 Earlier direction.

<a id="ui-new"></a>
### Replace earlier direction
- Date: 2026-02-01
- Decisionmaker: Product Owner
- Decision: Replace the earlier direction.
- Rationale: Requirements changed.
- Provenance: explicit
- Supersedes: `ui-old`
- Evidence:
  - ticket://UI-3 Replacement direction.

<a id="ui-sensitive"></a>
### Sensitive token=subject-secret
- Date: 2026-03-01
- Decisionmaker: password=owner-secret
- Decision: Never expose api_key=decision-secret.
- Rationale: Rotate token=rationale-secret before publication.
- Provenance: explicit
- Evidence:
  - javascript://example.invalid/?token=uri-secret password=label-secret
"""


def make_client(tmp_path):
    library = tmp_path / "library"
    project = library / "projects" / "demo"
    project.mkdir(parents=True)
    (project / "decision-register.md").write_text(REGISTER)
    settings = Settings(
        "sqlite:///" + str(tmp_path / "runtime.db"),
        library,
        tmp_path / "state",
        "demo",
        require_oidc=False,
        allow_local_dev_identity=True,
        development_mode=True,
        session_secret="library-workflow-session-secret",
    )
    app = create_app(settings)
    client = TestClient(app, base_url="https://testserver")
    response = client.post(
        "/auth/dev-login",
        json={
            "subject": "fixture:maintainer",
            "display_name": "Fixture Maintainer",
            "capabilities": ["maintain"],
            "projects": ["demo"],
        },
    )
    assert response.status_code == 200
    return app, client


def csrf(client, path):
    response = client.get("/api/v1/session/csrf", params={"method": "POST", "path": path})
    assert response.status_code == 200
    return {"X-CSRF-Token": response.json()["data"]["csrf_token"]}


def test_library_search_filters_pages_and_performs_no_runtime_write(tmp_path):
    app, client = make_client(tmp_path)
    before = app.state.store.db.total_changes
    search = client.get("/api/v1/projects/demo/library/search", params={"q": "Product Owner"})
    assert search.status_code == 200, search.text
    assert search.json()["data"]["total"] == 3
    assert app.state.store.db.total_changes == before

    assumed = client.get("/api/v1/projects/demo/library/search", params={"status": "assumed"}).json()["data"]
    assert [item["decision_id"] for item in assumed["items"]] == ["ui-assumption"]
    assert assumed["items"][0]["status"] == "assumed"

    paged = client.get(
        "/api/v1/projects/demo/library/search",
        params={"category": "User Interface", "page_size": 2},
    ).json()["data"]
    assert paged["total"] == 5
    assert paged["next_page"] == 2
    assert len(paged["library_digest"]) == 64


def test_library_detail_redacts_secret_and_reports_supersession(tmp_path):
    _, client = make_client(tmp_path)
    detail = client.get("/api/v1/projects/demo/library/decisions/ui-react").json()["data"]["decision"]
    assert detail["status"] == "authoritative"
    assert "secret-value" not in str(detail)
    assert "[REDACTED]" in detail["sources"][0]["label"]
    old = client.get("/api/v1/projects/demo/library/decisions/ui-old").json()["data"]["decision"]
    assert old["status"] == "superseded"
    assert old["superseded_by"] == ["ui-new"]
    missing = client.get("/api/v1/projects/demo/library/decisions/missing")
    assert missing.status_code == 404
    assert missing.json()["errors"][0]["code"] == "decision-not-found"

    sensitive = client.get("/api/v1/projects/demo/library/decisions/ui-sensitive").json()["data"]["decision"]
    serialized = str(sensitive)
    for secret in (
        "subject-secret",
        "owner-secret",
        "decision-secret",
        "rationale-secret",
        "uri-secret",
        "label-secret",
    ):
        assert secret not in serialized
    assert sensitive["sources"][0]["redacted"] is True
    assert sensitive["sources"][0]["secret_state"] == "redacted"
    search = client.get("/api/v1/projects/demo/library/search", params={"q": "decision-secret"})
    assert search.status_code == 200
    assert "decision-secret" not in search.text


def test_library_html_is_server_rendered_enhanced_and_secret_free(tmp_path):
    _, client = make_client(tmp_path)
    page = client.get("/library")
    assert page.status_code == 200
    assert "Current decisions" in page.text
    assert "Assumed—not mandatory" in page.text
    assert 'data-island="library-search"' in page.text
    assert "/static/assets/main-" in page.text
    assert "secret-value" not in page.text
    detail = client.get("/library/decisions/ui-react")
    assert detail.status_code == 200
    assert "[REDACTED]" in detail.text
    assert "secret-value" not in detail.text
    unsafe = client.get("/library/decisions/ui-sensitive")
    assert unsafe.status_code == 200
    assert 'href="javascript:' not in unsafe.text
    assert "uri-secret" not in unsafe.text
    proposal_page = client.get("/library/proposals/new?decision_id=ui-react")
    assert proposal_page.status_code == 200
    assert 'data-island="proposal-form"' in proposal_page.text
    asset_path = page.text.split('src="')[1].split('"')[0]
    assert client.get(asset_path).status_code == 200


def proposal(digest, *, key=None):
    value = {
        "operation": "revise",
        "decision_id": "ui-react",
        "proposed_fields": {"decision": "Use React with TypeScript."},
        "rationale": "Clarify the implementation language.",
        "evidence_references": ["https://example.invalid/messages/1"],
        "authority": "Product Owner",
        "publication_intent": True,
        "library_digest": digest,
    }
    if key:
        value["idempotency_key"] = key
    return value


def test_proposal_preview_is_read_only_and_submission_is_idempotent(tmp_path):
    app, client = make_client(tmp_path)
    digest = client.get("/api/v1/projects/demo/library/search").json()["data"]["library_digest"]
    preview_path = "/api/v1/projects/demo/library/proposals/preview"
    before = {
        table: app.state.store.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in ("work_items", "contributions", "library_proposals")
    }
    preview = client.post(preview_path, headers=csrf(client, preview_path), json=proposal(digest))
    assert preview.status_code == 200, preview.text
    assert preview.json()["data"]["route"] in {"agent", "review"}
    after = {table: app.state.store.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in before}
    assert after == before

    submit_path = "/api/v1/projects/demo/library/proposals"
    body = proposal(digest, key="proposal-1")
    first = client.post(submit_path, headers=csrf(client, submit_path), json=body)
    second = client.post(submit_path, headers=csrf(client, submit_path), json=body)
    assert first.status_code == second.status_code == 200
    assert first.json()["data"]["proposal_id"] == second.json()["data"]["proposal_id"]
    assert second.json()["data"]["idempotent"] is True
    work = app.state.store.work("demo", first.json()["data"]["work_id"])
    assert "authorized_publication" in work["payload"]
    assert '"authorized_publication": false' in work["payload"]

    listing = client.get(submit_path).json()["data"]
    assert listing["total"] == 1
    detail = client.get(f"{submit_path}/{first.json()['data']['proposal_id']}").json()["data"]
    assert detail["lifecycle"]["state"] == "queued"
    html = client.get(f"/library/proposals/{first.json()['data']['proposal_id']}")
    assert html.status_code == 200
    assert "canonical content has not changed" in html.text


def test_proposal_read_models_redact_secret_named_fields(tmp_path):
    _, client = make_client(tmp_path)
    digest = client.get("/api/v1/projects/demo/library/search").json()["data"]["library_digest"]
    path = "/api/v1/projects/demo/library/proposals"
    body = proposal(digest, key="secret-proposal")
    body["proposed_fields"]["client_secret"] = "proposal-secret-value"
    submitted = client.post(path, headers=csrf(client, path), json=body)
    proposal_id = submitted.json()["data"]["proposal_id"]
    for response in (
        client.get(path),
        client.get(f"{path}/{proposal_id}"),
        client.get(f"/library/proposals/{proposal_id}"),
    ):
        assert response.status_code == 200
        assert "proposal-secret-value" not in response.text


def test_proposal_submit_survives_api_restart_after_preview(tmp_path):
    app, first = make_client(tmp_path)
    digest = first.get("/api/v1/projects/demo/library/search").json()["data"]["library_digest"]
    preview_path = "/api/v1/projects/demo/library/proposals/preview"
    assert first.post(preview_path, headers=csrf(first, preview_path), json=proposal(digest)).status_code == 200
    restarted = TestClient(create_app(app.state.settings), base_url="https://testserver")
    assert (
        restarted.post(
            "/auth/dev-login",
            json={
                "subject": "fixture:maintainer",
                "display_name": "Fixture Maintainer",
                "capabilities": ["maintain"],
                "projects": ["demo"],
            },
        ).status_code
        == 200
    )
    submit_path = "/api/v1/projects/demo/library/proposals"
    submitted = restarted.post(
        submit_path,
        headers=csrf(restarted, submit_path),
        json=proposal(digest, key="post-restart"),
    )
    assert submitted.status_code == 200
    assert submitted.json()["data"]["state"] == "queued"


def test_stale_proposal_and_invalid_filters_fail_without_partial_write(tmp_path):
    app, client = make_client(tmp_path)
    submit_path = "/api/v1/projects/demo/library/proposals"
    stale = client.post(
        submit_path,
        headers=csrf(client, submit_path),
        json=proposal("0" * 64, key="stale"),
    )
    assert stale.status_code == 409
    assert stale.json()["errors"][0]["code"] == "revision-conflict"
    assert app.state.store.db.execute("SELECT COUNT(*) FROM work_items").fetchone()[0] == 0
    invalid = client.get("/api/v1/projects/demo/library/search", params={"status": "invented"})
    assert invalid.status_code == 422


def test_library_digest_change_invalidates_cached_authority(tmp_path):
    _, client = make_client(tmp_path)
    first = client.get("/api/v1/projects/demo/library/search", params={"q": "React"}).json()["data"]
    register = tmp_path / "library/projects/demo/decision-register.md"
    register.write_text(REGISTER.replace("Use React for", "Use modern React for"))
    second = client.get("/api/v1/projects/demo/library/search", params={"q": "modern React"}).json()["data"]
    assert second["library_digest"] != first["library_digest"]
    assert second["items"][0]["decision"] == "Use modern React for the product UI."


def test_idempotency_key_reuse_with_changed_proposal_is_rejected(tmp_path):
    _, client = make_client(tmp_path)
    digest = client.get("/api/v1/projects/demo/library/search").json()["data"]["library_digest"]
    path = "/api/v1/projects/demo/library/proposals"
    original = proposal(digest, key="same-key")
    assert client.post(path, headers=csrf(client, path), json=original).status_code == 200
    changed = {**original, "rationale": "Different content."}
    conflict = client.post(path, headers=csrf(client, path), json=changed)
    assert conflict.status_code == 409
    assert conflict.json()["errors"][0]["code"] == "idempotency-conflict"


def test_same_idempotency_key_is_independent_between_actors(tmp_path):
    app, first = make_client(tmp_path)
    second = TestClient(app, base_url="https://testserver")
    login = second.post(
        "/auth/dev-login",
        json={
            "subject": "fixture:other-maintainer",
            "display_name": "Other Maintainer",
            "capabilities": ["maintain"],
            "projects": ["demo"],
        },
    )
    assert login.status_code == 200
    digest = first.get("/api/v1/projects/demo/library/search").json()["data"]["library_digest"]
    path = "/api/v1/projects/demo/library/proposals"
    body = proposal(digest, key="shared-browser-key")
    assert first.post(path, headers=csrf(first, path), json=body).status_code == 200
    response = second.post(path, headers=csrf(second, path), json=body)
    assert response.status_code == 200, response.text
    assert app.state.store.db.execute("SELECT COUNT(*) FROM library_proposals").fetchone()[0] == 2


def test_unhandled_errors_return_secure_envelope_and_durable_audit(tmp_path, monkeypatch):
    app, signed_in = make_client(tmp_path)
    monkeypatch.setattr(
        app.state.library,
        "search",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("sensitive detail")),
    )
    client = TestClient(app, base_url="https://testserver", raise_server_exceptions=False)
    client.cookies.update(signed_in.cookies)
    response = client.get("/api/v1/projects/demo/library/search")
    assert response.status_code == 500
    assert response.json()["errors"] == [{"code": "internal-error", "message": "internal server error"}]
    assert response.headers["x-content-type-options"] == "nosniff"
    event = app.state.store.db.execute(
        "SELECT payload FROM audit_events WHERE event_type='unhandled-error' ORDER BY created_at DESC"
    ).fetchone()
    assert event is not None
    assert "sensitive detail" not in event["payload"]


def test_proposal_listing_derives_filterable_status_from_work(tmp_path):
    app, client = make_client(tmp_path)
    digest = client.get("/api/v1/projects/demo/library/search").json()["data"]["library_digest"]
    path = "/api/v1/projects/demo/library/proposals"
    submitted = client.post(path, headers=csrf(client, path), json=proposal(digest, key="status-derived")).json()[
        "data"
    ]
    app.state.store.db.execute("UPDATE work_items SET state='succeeded' WHERE id=?", (submitted["work_id"],))
    app.state.store.db.commit()
    succeeded = client.get(path, params={"status": "succeeded"}).json()["data"]
    assert succeeded["total"] == 1
    assert succeeded["items"][0]["status"] == "succeeded"
    assert client.get(path, params={"status": "queued"}).json()["data"]["total"] == 0
