from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from context_library_manager.api import create_app
from context_library_manager.auth import (
    Capability,
    bearer_claims,
    normalize_capabilities,
)
from context_library_manager.config import ConfigurationError, Settings


def settings_for(tmp_path, *, development=True, oidc=False):
    return Settings(
        "sqlite:///" + str(tmp_path / "runtime.db"),
        tmp_path / "library",
        tmp_path / "state",
        "demo",
        require_oidc=oidc,
        oidc_hs256_secret="test-secret" if oidc else None,
        allow_local_dev_identity=development,
        development_mode=development,
        session_secret="session-secret-for-tests",
    )


def login(client, capability="admin"):
    response = client.post(
        "/auth/dev-login",
        json={
            "subject": f"fixture:{capability}",
            "display_name": "Fixture User",
            "capabilities": [capability],
            "projects": ["demo"],
            "selected_project": "demo",
        },
    )
    assert response.status_code == 200
    return response


def csrf(client, path, method="POST"):
    response = client.get("/api/v1/session/csrf", params={"method": method, "path": path})
    assert response.status_code == 200
    return response.json()["data"]["csrf_token"]


def bearer(claims):
    header = base64.urlsafe_b64encode(b'{"alg":"HS256","typ":"JWT"}').decode()
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode()
    signing_input = f"{header.rstrip('=')}.{payload.rstrip('=')}"
    signature = base64.urlsafe_b64encode(
        hmac.new(b"test-secret", signing_input.encode(), hashlib.sha256).digest()
    ).decode()
    return f"Bearer {signing_input}.{signature.rstrip('=')}"


def test_hs256_tokens_enforce_configured_issuer_and_audience():
    valid = bearer(
        {
            "sub": "user:test",
            "iss": "https://issuer.example",
            "aud": ["runtime", "other-client"],
            "exp": 4_000_000_000,
        }
    )
    claims = bearer_claims(
        valid,
        "test-secret",
        issuer="https://issuer.example",
        audience="runtime",
    )
    assert claims["sub"] == "user:test"
    with pytest.raises(ValueError, match="issuer"):
        bearer_claims(valid, "test-secret", issuer="https://other.example")
    with pytest.raises(ValueError, match="audience"):
        bearer_claims(valid, "test-secret", audience="different-client")


def test_standard_oidc_human_claims_are_not_classified_as_m2m():
    from context_library_manager.auth import is_m2m

    assert not is_m2m(
        {
            "sub": "user-1",
            "name": "User One",
            "roles": ["read"],
            "token_class": "human",
        }
    )
    assert not is_m2m(
        {
            "sub": "user-1",
            "email": "user@example.test",
            "client_id": "browser-client",
            "token_class": "human",
        }
    )
    assert is_m2m(
        {
            "sub": "automation",
            "client_id": "automation",
            "token_class": "m2m",
        }
    )


def test_capabilities_normalize_legacy_and_expand_additively():
    assert normalize_capabilities({"viewer"}) == {Capability.READ}
    assert normalize_capabilities({"reviewer"}) == {
        Capability.READ,
        Capability.REVIEW,
    }
    assert normalize_capabilities({"maintainer"}) == {
        Capability.READ,
        Capability.REVIEW,
        Capability.MAINTAIN,
    }
    assert normalize_capabilities({"administrator"}) == set(Capability)


def test_browser_session_duration_must_be_positive(tmp_path):
    with pytest.raises(ConfigurationError, match="durations"):
        replace(settings_for(tmp_path), session_duration_seconds=0)


def test_development_login_issues_local_session_and_canonical_identity(tmp_path):
    client = TestClient(create_app(settings_for(tmp_path)), base_url="https://testserver")
    response = login(client, "administrator")
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie
    assert "Secure" not in cookie
    assert "SameSite=lax" in cookie
    session = client.get("/api/v1/session")
    assert session.status_code == 200
    assert session.json()["data"]["capabilities"] == [
        "read",
        "review",
        "maintain",
        "admin",
    ]
    assert session.json()["data"]["csrf_token"] is None
    assert csrf(client, "/auth/logout")


def test_header_impersonation_and_missing_csrf_are_rejected(tmp_path):
    app = create_app(settings_for(tmp_path))
    anonymous = TestClient(app, base_url="https://testserver")
    denied = anonymous.post(
        "/api/v1/projects/demo/contributions",
        headers={"X-Role": "administrator", "X-Actor": "forged"},
        json={
            "kind": "candidate",
            "payload": {"subject": "forged"},
            "client_idempotency_key": "forged",
        },
    )
    assert denied.status_code == 401

    login(anonymous, "maintain")
    missing = anonymous.post(
        "/api/v1/projects/demo/route",
        json={"operation": "candidate", "semantic_fields": ["rationale"]},
    )
    assert missing.status_code == 403
    assert missing.json()["errors"][0]["code"] == "csrf-required"
    allowed = anonymous.post(
        "/api/v1/projects/demo/route",
        headers={"X-CSRF-Token": csrf(anonymous, "/api/v1/projects/demo/route")},
        json={"operation": "candidate", "semantic_fields": ["rationale"]},
    )
    assert allowed.status_code == 200


def test_project_scope_denial_is_audited(tmp_path):
    app = create_app(settings_for(tmp_path))
    client = TestClient(app, base_url="https://testserver")
    login(client, "read")
    response = client.get("/api/v1/projects/other/overview")
    assert response.status_code == 404
    event = app.state.store.db.execute(
        "SELECT payload FROM audit_events WHERE event_type='access-denied' ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    assert json.loads(event["payload"])["requested_project"] == "other"
    audit = client.get("/api/v1/projects/demo/audit")
    assert audit.status_code == 200
    assert any(item["event_type"] == "access-denied" for item in audit.json()["data"]["events"])


def test_expired_and_logged_out_sessions_cannot_be_reused(tmp_path):
    app = create_app(settings_for(tmp_path))
    client = TestClient(app, base_url="https://testserver")
    login(client, "read")
    logout = client.post("/auth/logout", headers={"X-CSRF-Token": csrf(client, "/auth/logout")})
    assert logout.status_code == 200
    assert client.get("/api/v1/session").status_code == 401

    login(client, "read")
    app.state.store.db.execute("UPDATE browser_sessions SET expires_at='2000-01-01T00:00:00Z'")
    app.state.store.db.commit()
    assert client.get("/api/v1/session").status_code == 401


def test_m2m_tokens_cannot_enter_human_browser_surface(tmp_path):
    app = create_app(settings_for(tmp_path, development=False, oidc=True))
    client = TestClient(app, base_url="https://testserver")
    token = bearer(
        {
            "sub": "client:automation",
            "client_id": "automation",
            "token_class": "m2m",
            "email": "automation@example.invalid",
            "roles": ["viewer"],
            "projects": ["demo"],
            "exp": 4_000_000_000,
        }
    )
    api = client.get("/api/v1/projects/demo/overview", headers={"Authorization": token})
    assert api.status_code == 200
    page = client.get("/", headers={"Authorization": token})
    assert page.status_code == 403
    assert "M2M token rejected" in page.text
    session = client.get("/api/v1/session", headers={"Authorization": token})
    assert session.status_code == 403
    assert session.json()["errors"][0]["code"] == "interactive-user-required"
    audit = client.get("/api/v1/projects/demo/audit", headers={"Authorization": token}).json()["data"]["events"]
    assert any(item["event_type"] == "access-denied" for item in audit)


def test_malformed_json_jwt_and_bearer_only_html_fail_closed(tmp_path):
    app = create_app(settings_for(tmp_path, development=False, oidc=True))
    client = TestClient(app, base_url="https://testserver", raise_server_exceptions=False)
    encoded_list = base64.urlsafe_b64encode(b"[]").decode().rstrip("=")
    encoded_object = base64.urlsafe_b64encode(b"{}").decode().rstrip("=")
    malformed = client.get(
        "/api/v1/projects/demo/overview",
        headers={"Authorization": f"Bearer {encoded_list}.{encoded_object}.x"},
    )
    assert malformed.status_code == 401
    assert malformed.json()["errors"][0]["code"] == "invalid-token"

    human = bearer(
        {
            "sub": "user:bearer",
            "name": "Bearer User",
            "roles": ["read"],
            "projects": ["demo"],
            "token_class": "human",
            "exp": 4_000_000_000,
        }
    )
    page = client.get("/", headers={"Authorization": human})
    assert page.status_code == 403
    assert "interactive session" in page.text
    projects = client.get("/api/v1/projects", headers={"Authorization": human})
    assert projects.status_code == 200
    assert projects.json()["data"]["selected_project"] == "demo"


def test_csrf_token_cannot_be_reused_for_a_different_intent(tmp_path):
    client = TestClient(create_app(settings_for(tmp_path)), base_url="https://testserver")
    login(client, "maintain")
    route_token = csrf(client, "/api/v1/projects/demo/route")
    response = client.post(
        "/api/v1/projects/demo/contributions",
        headers={"X-CSRF-Token": route_token},
        json={
            "kind": "candidate",
            "payload": {"subject": "wrong intent"},
            "client_idempotency_key": "wrong-intent",
        },
    )
    assert response.status_code == 403
    assert response.json()["errors"][0]["code"] == "csrf-required"


def test_development_login_requires_both_explicit_flags(tmp_path):
    client = TestClient(
        create_app(settings_for(tmp_path, development=False)),
        base_url="https://testserver",
    )
    response = client.post(
        "/auth/dev-login",
        json={
            "subject": "fixture:admin",
            "display_name": "Admin",
            "capabilities": ["admin"],
            "projects": ["demo"],
        },
    )
    assert response.status_code == 404


def test_non_development_api_requires_persistent_session_secret(tmp_path):
    unsafe = Settings(
        "sqlite:///" + str(tmp_path / "runtime.db"),
        tmp_path / "library",
        tmp_path / "state",
        "demo",
        require_oidc=True,
    )
    with pytest.raises(RuntimeError, match="CLM_SESSION_SECRET"):
        create_app(unsafe)


def test_security_headers_cover_public_and_authenticated_responses(tmp_path):
    client = TestClient(create_app(settings_for(tmp_path)), base_url="https://testserver")
    health = client.get("/api/v1/health")
    assert "default-src 'self'" in health.headers["content-security-policy"]
    assert health.headers["x-frame-options"] == "DENY"
    login(client, "read")
    overview = client.get("/api/v1/projects/demo/overview")
    assert overview.headers["cache-control"] == "no-store"
