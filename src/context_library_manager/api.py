from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from functools import wraps
from typing import Annotated
from urllib.parse import parse_qs, urlencode
from urllib.request import Request as URLRequest
from urllib.request import urlopen

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from context_library_core.maintainer_contracts import HarvestBatch

from .agent_service import (
    ServiceConflict,
    complete_drain,
    control_service,
    heartbeat_health,
)
from .auth import (
    SessionManager,
    bearer_claims,
    normalize_capabilities,
    principal_from_claims,
)
from .config import ConfigurationError, Settings
from .configuration import (
    apply_revision as apply_configuration_revision,
)
from .configuration import (
    effective_settings,
)
from .configuration import (
    history as configuration_history,
)
from .configuration import (
    impact as configuration_impact,
)
from .configuration import (
    read_model as configuration_read_model,
)
from .contracts import (
    AgentRunCancellation,
    AgentServiceControl,
    Capability,
    ConfigurationDraft,
    ConfigurationRollback,
    ContentStatus,
    CSRFIntent,
    DevelopmentLogin,
    ProposalDraft,
    ProposalLifecycle,
    ProposalPreview,
    ProposalSubmission,
    SessionProjectSelection,
)
from .db import ProjectLifecycleConflict, Store
from .domain import (
    Contribution,
    Envelope,
    ResolveRequest,
    ReviewCreate,
    RouteRequest,
    Source,
    utc_now,
)
from .library import LibraryError, LibraryReader, redact
from .routing import route
from .service import SourceIdempotencyConflict, intake_harvest_batch, intake_source
from .web import ROOT as WEB_ROOT
from .web import render as render_template


class ProjectLifecycleControl(BaseModel):
    lifecycle: str = Field(min_length=1, max_length=32)
    expected_version: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=2000)
    idempotency_key: str = Field(min_length=1, max_length=256)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    if not settings.development_mode and settings.ephemeral_session_secret:
        raise RuntimeError("CLM_SESSION_SECRET is required outside development mode")
    from context_library_core.version import VERSION

    app = FastAPI(title="Context Library Manager Runtime", version=VERSION)
    static_root = WEB_ROOT / "static"
    if static_root.is_dir():
        app.mount("/static", StaticFiles(directory=static_root), name="static")
    app.state.settings = settings
    app.state.store = Store(settings.storage_target)
    app.state.sessions = SessionManager(app.state.store, settings.session_secret, settings.session_duration_seconds)
    app.state.library = LibraryReader(settings)
    app.state.review_resolution_lock = threading.Lock()
    app.state.last_api_heartbeat = 0.0

    def serialized_store_write(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            with app.state.store._write_lock:
                return function(*args, **kwargs)

        return wrapped

    now = utc_now()
    managed = list(settings.managed_projects)
    configured_ids = {entry.id for entry in managed}
    existing_projects = {
        row["id"]: row
        for row in app.state.store.db.execute("SELECT id,active,lifecycle FROM projects").fetchall()
    }
    for removed_id, row in existing_projects.items():
        if removed_id in configured_ids or (not row["active"] and row["lifecycle"] == "disabled"):
            continue
        app.state.store.db.execute(
            "UPDATE projects SET active=0,lifecycle='disabled',lifecycle_version=lifecycle_version+1,"
            "updated_at=? WHERE id=?",
            (now, removed_id),
        )
        app.state.store.event(
            None,
            "runtime:configuration",
            "project-removed",
            {"project": removed_id, "reason": "removed from managed_projects"},
            removed_id,
        )
    for entry in managed:
        app.state.store.db.execute(
            "INSERT INTO projects(id,name,created_at,updated_at,active,lifecycle) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET active=excluded.active,"
            "lifecycle=CASE WHEN excluded.active=0 THEN 'disabled' ELSE projects.lifecycle END,"
            "updated_at=excluded.updated_at",
            (entry.id, entry.id, now, now, int(entry.enabled), "enabled" if entry.enabled else "disabled"),
        )
        if settings.explicit_project_registry and entry.id not in existing_projects:
            app.state.store.event(
                None,
                "runtime:configuration",
                "project-enrolled",
                {
                    "project": entry.id,
                    "library_root": str(entry.library_root),
                    "state_namespace": entry.state_namespace,
                },
                entry.id,
            )
        app.state.store.db.execute(
            "INSERT INTO policy_revisions(id,project,revision,payload,created_at) VALUES(?,?,?,?,?) "
            "ON CONFLICT(id) DO NOTHING",
            (
                f"policy_{entry.id}_1",
                entry.id,
                "1",
                json.dumps({"excluded_categories": settings.excluded_categories}),
                now,
            ),
        )
    app.state.store.db.commit()
    from .telemetry import DEFAULT_PRODUCERS, append_event, install_manifest

    for entry in managed:
        install_manifest(
            app.state.store,
            entry.id,
            f"{VERSION}:default-producers-v1",
            DEFAULT_PRODUCERS,
            effective_at=now,
        )
        current_policy = app.state.store.db.execute(
            "SELECT revision FROM policy_revisions WHERE project=? "
            "ORDER BY CAST(revision AS INTEGER) DESC,created_at DESC LIMIT 1",
            (entry.id,),
        ).fetchone()
        append_event(
            app.state.store,
            entry.id,
            "policy",
            "policy-snapshot",
            payload={"revision": current_policy["revision"] if current_policy else "1"},
        )
    rate_windows: dict[tuple[str, str], deque[float]] = defaultdict(deque)

    def secure(response):
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
            "base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
        )
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Cache-Control"] = "no-store"
        return response

    def auth_error(request: Request, status: int, code: str, message: str):
        if status == 401 and not request.url.path.startswith("/api/"):
            return secure(RedirectResponse("/auth/login", status_code=303))
        if not request.url.path.startswith("/api/"):
            return secure(
                HTMLResponse(
                    f"<html><body><h1>Access denied</h1><p>{html.escape(message)}</p></body></html>",
                    status_code=status,
                )
            )
        return secure(
            JSONResponse(
                status_code=status,
                content={
                    "schema_version": 1,
                    "request_id": request.headers.get("x-request-id", "req-unknown"),
                    "status": "error",
                    "data": {},
                    "errors": [{"code": code, "message": message}],
                },
            )
        )

    @app.exception_handler(StarletteHTTPException)
    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        if not request.url.path.startswith("/api/"):
            code = detail.get("code", "http-error")
            message = detail.get("message", str(exc.detail))
            try:
                context = page_context(request, "Request unavailable")
                return secure(
                    render_template(
                        "error.html",
                        {**context, "code": code, "message": message},
                        status_code=exc.status_code,
                    )
                )
            except Exception:
                return auth_error(request, exc.status_code, code, message)
        return secure(
            JSONResponse(
                status_code=exc.status_code,
                content={
                    "schema_version": 1,
                    "request_id": request.headers.get("x-request-id", "req-unknown"),
                    "status": "error",
                    "data": {},
                    "errors": [
                        {
                            "code": detail.get("code", "http-error"),
                            "message": detail.get("message", str(exc.detail)),
                            **{
                                key: value
                                for key, value in detail.items()
                                if key not in {"code", "message"} and value is not None
                            },
                        }
                    ],
                },
            )
        )

    @app.exception_handler(Exception)
    async def unhandled_error(request: Request, exc: Exception):
        try:
            app.state.store.event(
                None,
                "runtime:api",
                "unhandled-error",
                {"path": request.url.path, "error_class": type(exc).__name__},
                settings.project,
            )
            app.state.store.db.commit()
        except Exception:
            app.state.store.db.rollback()
        if not request.url.path.startswith("/api/"):
            try:
                return secure(
                    render_template(
                        "error.html",
                        {
                            **page_context(request, "Unexpected error"),
                            "code": "internal-error",
                            "message": "The page could not be displayed.",
                        },
                        status_code=500,
                    )
                )
            except Exception:
                pass
        return secure(
            JSONResponse(
                status_code=500,
                content={
                    "schema_version": 1,
                    "request_id": request.headers.get("x-request-id", "req-unknown"),
                    "status": "error",
                    "data": {},
                    "errors": [{"code": "internal-error", "message": "internal server error"}],
                },
            )
        )

    @app.exception_handler(LibraryError)
    async def library_error(request: Request, exc: LibraryError):
        return secure(
            JSONResponse(
                status_code=exc.status_code,
                content={
                    "schema_version": 1,
                    "request_id": request.headers.get("x-request-id", "req-unknown"),
                    "status": "error",
                    "data": {},
                    "errors": [{"code": exc.code, "message": exc.message}],
                },
            )
        )

    @app.middleware("http")
    async def authenticate(request: Request, call_next):
        heartbeat_now = time.monotonic()
        if heartbeat_now - app.state.last_api_heartbeat >= 10:
            try:
                app.state.store.heartbeat("api", "api-1", details={"version": app.version})
                app.state.last_api_heartbeat = heartbeat_now
            except Exception:
                app.state.store.db.rollback()
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            key = (
                request.client.host if request.client else "unknown",
                request.url.path,
            )
            now = time.monotonic()
            if key not in rate_windows and len(rate_windows) >= 10_000:
                for stale_key in list(rate_windows)[:100]:
                    stale_window = rate_windows[stale_key]
                    while stale_window and now - stale_window[0] >= 60:
                        stale_window.popleft()
                    if not stale_window:
                        del rate_windows[stale_key]
                if len(rate_windows) >= 10_000:
                    rate_windows.pop(next(iter(rate_windows)))
            window = rate_windows[key]
            while window and now - window[0] >= 60:
                window.popleft()
            if len(window) >= 60:
                return secure(
                    JSONResponse(
                        status_code=429,
                        content={
                            "schema_version": 1,
                            "request_id": request.headers.get("x-request-id", "req-unknown"),
                            "status": "error",
                            "data": {},
                            "errors": [{"code": "rate-limited", "message": "retry later"}],
                        },
                    )
                )
            window.append(now)
        request.state.principal = None
        request.state.identity = None
        loaded = app.state.sessions.load(request.cookies.get(SessionManager.cookie_name))
        if loaded:
            request.state.principal, request.state.identity = loaded
        authorization = request.headers.get("authorization")
        if authorization:
            try:
                claims = bearer_claims(
                    authorization,
                    settings.oidc_hs256_secret,
                    settings.oidc_issuer,
                    settings.oidc_jwks_url,
                    settings.oidc_audience,
                )
                request.state.principal = principal_from_claims(claims, settings.project)
            except ValueError as exc:
                return auth_error(request, 401, "invalid-token", str(exc))
        public = {
            "/api/v1/health",
            "/favicon.ico",
            "/auth/login",
            "/auth/callback",
            "/auth/dev-login",
        }
        principal = request.state.principal
        is_public = request.url.path in public or request.url.path.startswith("/static/")
        if not is_public and principal is None:
            return auth_error(request, 401, "unauthorized", "authentication required")
        if (
            principal is not None
            and principal.actor_class == "m2m"
            and (not request.url.path.startswith("/api/") or request.url.path == "/api/v1/session")
        ):
            deny(request, settings.project, Capability.READ)
            return auth_error(request, 403, "interactive-user-required", "M2M token rejected")
        if (
            principal is not None
            and not request.url.path.startswith(("/api/", "/static/"))
            and request.state.identity is None
            and request.url.path not in public
        ):
            return auth_error(
                request,
                403,
                "browser-session-required",
                "browser pages require an interactive session",
            )
        return secure(await call_next(request))

    def project_allowed(request: Request, project: str) -> bool:
        principal = request.state.principal
        if settings.explicit_project_registry and project not in settings.managed_project_ids:
            return False
        configured = app.state.store.db.execute("SELECT 1 FROM projects WHERE id=? AND active=1", (project,)).fetchone()
        return bool(principal and configured and principal.allows(Capability.READ, project))

    def deny(request: Request, project: str | None, capability: Capability) -> None:
        principal = request.state.principal
        actor = f"{principal.actor_class}:{principal.subject}" if principal else "anonymous"
        audit_project = (
            project
            if project == settings.project
            else settings.project
            if principal and settings.project in principal.allowed_projects
            else None
        )
        app.state.store.event(
            None,
            actor,
            "access-denied",
            {
                "requested_project": project,
                "path": request.url.path,
                "capability": capability.value,
            },
            audit_project,
        )
        app.state.store.db.commit()

    def require_capability(request: Request, capability: Capability, project: str | None = None) -> None:
        principal = request.state.principal
        if not principal or not principal.allows(capability, project):
            deny(request, project, capability)
            raise HTTPException(
                403,
                {
                    "code": "forbidden",
                    "message": f"{capability.value} capability required",
                },
            )

    def require_project(request: Request, project: str) -> None:
        if not project_allowed(request, project):
            deny(request, project, Capability.READ)
            raise HTTPException(404, {"code": "project-not-found", "message": "project not configured"})

    def actor_for(request: Request, fallback: str = "user:local") -> str:
        principal = request.state.principal
        return f"{principal.actor_class}:{principal.subject}" if principal else fallback

    def require_csrf(
        request: Request,
        supplied: str | None = None,
        method: str | None = None,
        path: str | None = None,
    ) -> None:
        principal = request.state.principal
        if not principal or not principal.session_id:
            return
        token = supplied or request.headers.get("x-csrf-token")
        expected = SessionManager.csrf_for(principal, method or request.method, path or request.url.path)
        if not token or not hmac.compare_digest(token, expected):
            deny(request, None, Capability.READ)
            raise HTTPException(403, {"code": "csrf-required", "message": "valid CSRF token required"})

    def envelope(
        request: Request,
        status: str = "ok",
        data: dict | None = None,
        errors: list[dict[str, str]] | None = None,
    ):
        return Envelope(
            request_id=request.headers.get(
                "x-request-id",
                f"req_{hashlib.sha256(utc_now().encode()).hexdigest()[:16]}",
            ),
            status=status,
            data=data or {},
            errors=errors or [],
        ).model_dump()

    def set_session_cookie(response, value: str) -> None:
        response.set_cookie(
            SessionManager.cookie_name,
            value,
            max_age=settings.session_duration_seconds,
            httponly=True,
            secure=not settings.development_mode,
            samesite="lax",
            path="/",
        )

    def state_cookie(value: str) -> str:
        signature = hmac.new(settings.session_secret.encode(), value.encode(), hashlib.sha256).digest()
        return value + "." + base64.urlsafe_b64encode(signature).decode().rstrip("=")

    def valid_state(cookie: str | None, supplied: str) -> bool:
        if not cookie or "." not in cookie:
            return False
        value, signature = cookie.rsplit(".", 1)
        if value != supplied:
            return False
        expected = hmac.new(settings.session_secret.encode(), value.encode(), hashlib.sha256).digest()
        try:
            actual = base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4))
        except ValueError:
            return False
        return hmac.compare_digest(actual, expected)

    @app.get("/auth/login")
    def login():
        required = (
            settings.oidc_authorize_url,
            settings.oidc_client_id,
            settings.oidc_redirect_uri,
        )
        if not all(required):
            if settings.development_mode and settings.allow_local_dev_identity:
                return HTMLResponse(
                    "<html><body><h1>Development sign in</h1>"
                    "<form method='post' action='/auth/dev-login'>"
                    "<label>Subject <input name='subject' value='fixture:admin' required></label>"
                    "<label>Display name <input name='display_name' value='Local Administrator' required></label>"
                    "<label>Capability <select name='capability'>"
                    "<option>read</option><option>review</option><option>maintain</option>"
                    "<option selected>admin</option></select></label>"
                    f"<input type='hidden' name='project' value='{html.escape(settings.project)}'>"
                    "<button type='submit'>Sign in</button></form>"
                    "</body></html>"
                )
            raise HTTPException(
                503,
                {
                    "code": "oidc-not-configured",
                    "message": "OIDC login is not configured",
                },
            )
        state = secrets.token_urlsafe(32)
        query = urlencode(
            {
                "response_type": "code",
                "client_id": settings.oidc_client_id,
                "redirect_uri": settings.oidc_redirect_uri,
                "scope": "openid profile email",
                "state": state,
            }
        )
        response = RedirectResponse(f"{settings.oidc_authorize_url}?{query}", 303)
        response.set_cookie(
            "clm_oidc_state",
            state_cookie(state),
            max_age=300,
            httponly=True,
            secure=True,
            samesite="lax",
        )
        return response

    @app.get("/auth/callback")
    def callback(request: Request, code: str, state: str):
        if not valid_state(request.cookies.get("clm_oidc_state"), state):
            raise HTTPException(400, {"code": "invalid-oidc-state", "message": "invalid OIDC state"})
        if not all(
            (
                settings.oidc_token_url,
                settings.oidc_client_id,
                settings.oidc_client_secret,
                settings.oidc_redirect_uri,
            )
        ):
            raise HTTPException(
                503,
                {
                    "code": "oidc-not-configured",
                    "message": "OIDC token exchange is not configured",
                },
            )
        body = urlencode(
            {
                "grant_type": "authorization_code",
                "code": code,
                "client_id": settings.oidc_client_id,
                "client_secret": settings.oidc_client_secret,
                "redirect_uri": settings.oidc_redirect_uri,
            }
        ).encode()
        token_request = URLRequest(
            settings.oidc_token_url,
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urlopen(token_request, timeout=10) as token_response:  # noqa: S310
                tokens = json.loads(token_response.read())
        except Exception as exc:
            raise HTTPException(502, {"code": "oidc-exchange-failed", "message": "OIDC exchange failed"}) from exc
        token = tokens.get("id_token") or tokens.get("access_token")
        if not isinstance(token, str):
            raise HTTPException(502, {"code": "oidc-token-missing", "message": "OIDC token missing"})
        claims = bearer_claims(
            f"Bearer {token}",
            settings.oidc_hs256_secret,
            settings.oidc_issuer,
            settings.oidc_jwks_url,
            settings.oidc_audience,
        )
        principal = principal_from_claims(claims, settings.project)
        if principal.actor_class != "human":
            raise HTTPException(
                403,
                {
                    "code": "interactive-user-required",
                    "message": "M2M token cannot create a browser session",
                },
            )
        cookie, _ = app.state.sessions.create(
            principal.subject,
            principal.display_name,
            set(principal.capabilities),
            set(principal.allowed_projects),
            settings.project if settings.project in principal.allowed_projects else None,
            str(claims.get("sid")) if claims.get("sid") else None,
        )
        response = RedirectResponse("/", 303)
        set_session_cookie(response, cookie)
        response.delete_cookie("clm_oidc_state")
        return response

    @app.post("/auth/dev-login")
    async def dev_login(request: Request):
        if not (settings.development_mode and settings.allow_local_dev_identity and not settings.require_oidc):
            raise HTTPException(404, {"code": "not-found", "message": "development login disabled"})
        is_form = request.headers.get("content-type", "").startswith("application/x-www-form-urlencoded")
        try:
            if is_form:
                fields = parse_qs((await request.body()).decode())
                body = DevelopmentLogin(
                    subject=fields.get("subject", [""])[0],
                    display_name=fields.get("display_name", [""])[0],
                    capabilities=[fields.get("capability", [""])[0]],
                    projects=[fields.get("project", [""])[0]],
                    selected_project=fields.get("project", [""])[0],
                )
            else:
                body = DevelopmentLogin.model_validate(await request.json())
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            raise HTTPException(
                422,
                {
                    "code": "invalid-development-identity",
                    "message": "invalid development identity",
                },
            ) from exc
        capabilities = normalize_capabilities(set(body.capabilities))
        projects = set(body.projects)
        if not capabilities or settings.project not in projects:
            raise HTTPException(
                422,
                {
                    "code": "invalid-development-identity",
                    "message": "recognized capability and configured project required",
                },
            )
        selected = body.selected_project or settings.project
        if selected not in projects:
            raise HTTPException(
                422,
                {
                    "code": "invalid-selected-project",
                    "message": "selected project is outside identity scope",
                },
            )
        cookie, identity = app.state.sessions.create(
            body.subject,
            body.display_name,
            capabilities,
            projects,
            selected,
        )
        response = (
            RedirectResponse("/", 303)
            if is_form
            else JSONResponse(envelope(request, data=identity.model_dump(mode="json")))
        )
        set_session_cookie(response, cookie)
        return response

    @app.get("/api/v1/session")
    def session(request: Request):
        principal = request.state.principal
        if not principal or principal.actor_class != "human":
            raise HTTPException(
                403,
                {
                    "code": "interactive-user-required",
                    "message": "human browser session required",
                },
            )
        identity = request.state.identity
        if identity is None:
            raise HTTPException(
                403,
                {
                    "code": "browser-session-required",
                    "message": "browser session required",
                },
            )
        return envelope(request, data=identity.model_dump(mode="json"))

    @app.get("/api/v1/session/csrf")
    def csrf_intent(request: Request, method: str, path: str):
        principal = request.state.principal
        if not principal or not principal.session_id:
            raise HTTPException(
                403,
                {
                    "code": "browser-session-required",
                    "message": "browser session required",
                },
            )
        intent = CSRFIntent(method=method.upper(), path=path)
        return envelope(
            request,
            data={
                "method": intent.method,
                "path": intent.path,
                "csrf_token": SessionManager.csrf_for(principal, intent.method, intent.path),
            },
        )

    @app.post("/auth/logout")
    def logout(request: Request):
        require_csrf(request)
        app.state.sessions.invalidate(request.cookies.get(SessionManager.cookie_name))
        response = JSONResponse(envelope(request, data={"logged_out": True}))
        response.delete_cookie(SessionManager.cookie_name, path="/")
        return response

    def apply_review_choice(project: str, review_row, resolution: ResolveRequest, actor: str):
        work = app.state.store.work(project, review_row["work_id"])
        if not work:
            return
        payload = json.loads(work["payload"])
        payload["human_resolution"] = resolution.choice
        payload["human_resolution_rationale"] = resolution.rationale
        payload["human_resolver"] = actor
        if resolution.choice == "adopt-candidate" and payload.get("publication_intent"):
            payload["authorized_publication"] = True
            app.state.store.event(
                review_row["work_id"],
                actor,
                "publication-authorized",
                {"review_id": review_row["id"], "capability": Capability.ADMIN.value},
                project,
            )
        app.state.store.db.execute(
            "UPDATE work_items SET payload=?, updated_at=? WHERE id=?",
            (json.dumps(payload), utc_now(), review_row["work_id"]),
        )
        target = {
            "retry": "queued",
            "reassess": "queued",
            "adopt-candidate": "queued",
            "authorize-more": "queued",
            "retain-current": "succeeded",
        }.get(resolution.choice)
        if (
            resolution.choice == "retain-current"
            and work["item_type"] == "candidate_task"
            and payload.get("clm_payload")
        ):
            target = "queued"
        if work["state"] == "waiting-human" and target:
            app.state.store.transition(
                project,
                review_row["work_id"],
                target,
                actor,
                commit=False,
            )

    def runtime_health_data(project: str | None = None) -> dict:
        rows = app.state.store.db.execute(
            "SELECT process,instance_id,state,details,observed_at FROM process_heartbeats ORDER BY process,instance_id"
        ).fetchall()
        expected = {"api", "scheduler", "worker", "notification", "reconciliation"}
        heartbeats = []
        observed_processes = set()
        for row in rows:
            observed_processes.add(row["process"])
            heartbeats.append(
                {
                    **dict(row),
                    "state": heartbeat_health(row["observed_at"]),
                    "details": safe_value(json.loads(row["details"])),
                }
            )
        for missing in sorted(expected - observed_processes):
            heartbeats.append(
                {
                    "process": missing,
                    "instance_id": "not-observed",
                    "state": "offline",
                    "details": {},
                    "observed_at": None,
                }
            )
        states = {item["state"] for item in heartbeats}
        status = "offline" if "offline" in states else "degraded" if "degraded" in states else "healthy"
        project_clause = " AND project=?" if project else ""
        active_leases = app.state.store.db.execute(
            "SELECT COUNT(*) AS n FROM work_items WHERE state IN ('leased','running','cancel-requested') "
            "AND lease_expires IS NOT NULL AND lease_expires>=?" + project_clause,
            (utc_now(), project) if project else (utc_now(),),
        ).fetchone()["n"]
        retry_backlog = app.state.store.db.execute(
            "SELECT COUNT(*) AS n FROM work_items WHERE state IN ('retryable','expired')" + project_clause,
            (project,) if project else (),
        ).fetchone()["n"]
        notification_failures = app.state.store.db.execute(
            "SELECT COUNT(*) AS n FROM notifications n JOIN reviews r ON r.id=n.review_id "
            "WHERE n.last_error IS NOT NULL AND n.delivered_at IS NULL" + (" AND r.project=?" if project else ""),
            (project,) if project else (),
        ).fetchone()["n"]
        budgets = app.state.store.db.execute(
            "SELECT project,day,reserved_tokens,spent_tokens FROM project_budgets "
            + ("WHERE project=? " if project else "")
            + "ORDER BY project",
            (project,) if project else (),
        ).fetchall()
        maintenance = app.state.store.db.execute(
            "SELECT project,event_type,work_id,created_at FROM audit_events "
            "WHERE event_type IN ('published','publication-succeeded','agent-service-drain-complete',"
            "'retry-requested','review-resolved')"
            + (" AND project=?" if project else "")
            + " ORDER BY created_at DESC LIMIT 10",
            (project,) if project else (),
        ).fetchall()
        return {
            "version": app.version,
            "status": status,
            "database": "postgresql"
            if settings.database_url.startswith(("postgresql://", "postgres://"))
            else "sqlite",
            "heartbeats": heartbeats,
            "active_leases": active_leases,
            "retry_backlog": retry_backlog,
            "notification_failures": notification_failures,
            "budgets": [dict(row) for row in budgets],
            "configuration_warnings": [],
            "last_maintenance_actions": [dict(row) for row in maintenance],
        }

    def configuration_error(exc: ConfigurationError) -> HTTPException:
        message = str(exc)
        if message.startswith("revision-conflict:"):
            return HTTPException(
                409,
                {
                    "code": "revision-conflict",
                    "message": "configuration changed; refresh before applying",
                    "current_revision": int(message.rsplit(":", 1)[1]),
                },
            )
        if message == "idempotency-conflict":
            return HTTPException(
                409,
                {
                    "code": "idempotency-conflict",
                    "message": "idempotency key was used for different content",
                },
            )
        if message == "target-revision-not-found":
            return HTTPException(
                404,
                {
                    "code": "target-revision-not-found",
                    "message": "rollback target does not exist",
                },
            )
        return HTTPException(
            422,
            {"code": "invalid-configuration", "message": message},
        )

    @app.get("/api/v1/projects/{project}/configuration")
    def project_configuration(project: str, request: Request):
        require_project(request, project)
        require_capability(request, Capability.READ, project)
        return envelope(
            request,
            data=configuration_read_model(app.state.store, settings, project),
        )

    @app.post("/api/v1/projects/{project}/configuration/preview")
    def project_configuration_preview(project: str, body: ConfigurationDraft, request: Request):
        require_project(request, project)
        require_capability(request, Capability.ADMIN, project)
        return envelope(
            request,
            data=configuration_impact(
                app.state.store,
                settings,
                project,
                body.expected_revision,
                body.changes,
            ),
        )

    @app.put("/api/v1/projects/{project}/configuration")
    def project_configuration_apply(project: str, body: ConfigurationDraft, request: Request):
        require_project(request, project)
        require_csrf(request)
        require_capability(request, Capability.ADMIN, project)
        route_name = f"PUT:/api/v1/projects/{project}/configuration"
        try:
            result, idempotent = apply_configuration_revision(
                app.state.store,
                settings,
                project,
                actor_for(request),
                route_name,
                body.expected_revision,
                body.reason,
                body.idempotency_key,
                changes=body.changes,
            )
        except ConfigurationError as exc:
            raise configuration_error(exc) from exc
        return envelope(request, data={**result, "idempotent": idempotent})

    @app.get("/api/v1/projects/{project}/configuration/history")
    def project_configuration_history(project: str, request: Request):
        require_project(request, project)
        require_capability(request, Capability.READ, project)
        return envelope(
            request,
            data={"items": configuration_history(app.state.store, project)},
        )

    @app.post("/api/v1/projects/{project}/configuration/rollback")
    def project_configuration_rollback(project: str, body: ConfigurationRollback, request: Request):
        require_project(request, project)
        require_csrf(request)
        require_capability(request, Capability.ADMIN, project)
        route_name = f"POST:/api/v1/projects/{project}/configuration/rollback"
        try:
            result, idempotent = apply_configuration_revision(
                app.state.store,
                settings,
                project,
                actor_for(request),
                route_name,
                body.expected_revision,
                body.reason,
                body.idempotency_key,
                target_revision=body.target_revision,
            )
        except ConfigurationError as exc:
            raise configuration_error(exc) from exc
        return envelope(request, data={**result, "idempotent": idempotent})

    @app.get("/api/v1/health")
    def health(request: Request):
        public_health = runtime_health_data()
        public_health["budgets"] = []
        public_health["last_maintenance_actions"] = []
        public_health["heartbeats"] = [
            {
                "process": item["process"],
                "instance_id": "observed" if item["instance_id"] != "not-observed" else "not-observed",
                "state": item["state"],
                "observed_at": item["observed_at"],
                "details": {},
            }
            for item in public_health["heartbeats"]
        ]
        return envelope(
            request,
            data=public_health,
        )

    @app.get("/api/v1/projects/{project}/health")
    def project_health(project: str, request: Request):
        require_project(request, project)
        require_capability(request, Capability.READ, project)
        return envelope(request, data=runtime_health_data(project))

    @app.get("/api/v1/projects/{project}/lifecycle")
    def project_lifecycle(project: str, request: Request):
        if settings.explicit_project_registry and project not in {
            entry.id for entry in settings.managed_projects
        }:
            raise HTTPException(404, {"code": "project-not-found", "message": "project not configured"})
        require_capability(request, Capability.READ, project)
        row = app.state.store.project_lifecycle(project)
        if not row:
            raise HTTPException(404, {"code": "project-not-found", "message": "project not configured"})
        return envelope(request, data=dict(row))

    @app.post("/api/v1/projects/{project}/lifecycle")
    @serialized_store_write
    def transition_project_lifecycle(project: str, body: ProjectLifecycleControl, request: Request):
        if settings.explicit_project_registry and project not in {
            entry.id for entry in settings.managed_projects
        }:
            raise HTTPException(404, {"code": "project-not-found", "message": "project not configured"})
        require_csrf(request)
        require_capability(request, Capability.ADMIN, project)
        actor = actor_for(request)
        route_name = f"POST:/api/v1/projects/{project}/lifecycle"
        digest = app.state.store.digest(body.model_dump(mode="json"))
        existing = app.state.store.db.execute(
            "SELECT request_digest,response_status,response FROM idempotency_records "
            "WHERE actor=? AND project=? AND route=? AND idempotency_key=?",
            (actor, project, route_name, body.idempotency_key),
        ).fetchone()
        if existing:
            if existing["request_digest"] != digest:
                raise HTTPException(409, {"code": "idempotency-conflict", "message": "idempotency key was reused"})
            return JSONResponse(json.loads(existing["response"]), status_code=existing["response_status"])
        try:
            result = app.state.store.transition_project_lifecycle(
                project,
                body.lifecycle,
                body.expected_version,
                actor,
                body.reason,
            )
        except ProjectLifecycleConflict as exc:
            raise HTTPException(
                409,
                {
                    "code": "project-lifecycle-conflict",
                    "message": "project lifecycle version is stale",
                    "current_lifecycle": exc.current.get("lifecycle"),
                    "current_version": exc.current.get("lifecycle_version"),
                },
            ) from exc
        except (KeyError, ValueError) as exc:
            raise HTTPException(409, {"code": "invalid-project-lifecycle", "message": str(exc)}) from exc
        response = envelope(request, data=result)
        app.state.store.db.execute(
            "INSERT INTO idempotency_records(id,actor,project,route,idempotency_key,request_digest,"
            "response_status,response,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                "idem_" + app.state.store.digest([actor, project, route_name, body.idempotency_key])[:24],
                actor,
                project,
                route_name,
                body.idempotency_key,
                digest,
                200,
                json.dumps(response),
                utc_now(),
            ),
        )
        app.state.store.db.commit()
        return JSONResponse(response)

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon():
        from fastapi import Response

        return Response(status_code=204)

    @app.get("/api/v1/projects")
    def projects(request: Request):
        principal = request.state.principal
        rows = app.state.store.db.execute("SELECT id,name FROM projects WHERE active=1 ORDER BY name,id").fetchall()
        visible = [dict(row) for row in rows if principal.allows(Capability.READ, row["id"])]
        return envelope(
            request,
            data={
                "projects": visible,
                "selected_project": (
                    request.state.identity.selected_project
                    if request.state.identity
                    else settings.project
                    if principal.allows(Capability.READ, settings.project)
                    else visible[0]["id"]
                    if visible
                    else None
                ),
            },
        )

    @app.post("/api/v1/session/project")
    def select_session_project(body: SessionProjectSelection, request: Request):
        require_csrf(request)
        require_project(request, body.project)
        principal = request.state.principal
        try:
            app.state.sessions.select_project(principal, body.project)
        except ValueError as exc:
            raise HTTPException(409, {"code": "session-conflict", "message": str(exc)}) from exc
        app.state.store.event(
            None,
            actor_for(request),
            "session-project-selected",
            {"selected_project": body.project},
            body.project,
        )
        app.state.store.db.commit()
        return envelope(request, data={"selected_project": body.project})

    @app.post("/session/project", response_class=HTMLResponse)
    async def select_session_project_page(request: Request):
        fields = parse_qs((await request.body()).decode())
        body = SessionProjectSelection(project=fields.get("project", [""])[0])
        require_csrf(request, fields.get("csrf_token", [""])[0])
        require_project(request, body.project)
        app.state.sessions.select_project(request.state.principal, body.project)
        app.state.store.event(
            None,
            actor_for(request),
            "session-project-selected",
            {"selected_project": body.project},
            body.project,
        )
        app.state.store.db.commit()
        return RedirectResponse("/", status_code=303)

    @app.get("/api/v1/projects/{project}/overview")
    def overview(project: str, request: Request):
        require_project(request, project)
        return envelope(request, data=overview_data(project))

    @app.get("/api/v1/projects/{project}/runs")
    def runs(project: str, request: Request):
        require_project(request, project)
        return envelope(request, data={"runs": [dict(row) for row in app.state.store.work(project)]})

    @app.get("/api/v1/projects/{project}/decisions")
    def decisions(project: str, request: Request):
        require_project(request, project)
        register = settings.library_root / "projects" / project / "decision-register.md"
        return envelope(
            request,
            data={
                "project": project,
                "register": register.read_text(encoding="utf-8") if register.is_file() else "",
            },
        )

    def preview_proposal(project: str, body: ProposalDraft) -> ProposalPreview:
        snapshot = app.state.library.snapshot(project)
        semantic_fields = sorted(body.proposed_fields)
        decision = route(
            RouteRequest(
                operation="candidate",
                semantic_fields=semantic_fields,
                input_tokens=max(1, len(body.model_dump_json()) // 4),
            )
        )
        stale = body.library_digest != snapshot.library_digest
        return ProposalPreview(
            deterministic_checks=[
                "strict-schema-valid",
                "evidence-references-present",
                "publication-authority-unchanged",
                "library-digest-current" if not stale else "library-digest-stale",
            ],
            route="review" if stale else decision.route,
            estimated_input_tokens=decision.estimated_input_tokens,
            estimated_max_cost=round(decision.estimated_input_tokens * 0.000001, 6),
            review_required=stale or decision.route == "review",
            current_library_digest=snapshot.library_digest,
            stale_source=stale,
        )

    @app.get("/api/v1/projects/{project}/library/search")
    def library_search(
        project: str,
        request: Request,
        q: str = "",
        status: ContentStatus | None = None,
        category: str | None = None,
        page: int = 1,
        page_size: int = 25,
    ):
        require_project(request, project)
        if len(q) > 500 or (category and len(category) > 256):
            raise HTTPException(
                422,
                {"code": "invalid-filter", "message": "search filter is too long"},
            )
        if page < 1 or page_size < 1 or page_size > 100:
            raise HTTPException(
                422,
                {"code": "invalid-page", "message": "invalid pagination values"},
            )
        snapshot, collection = app.state.library.search(
            query=q,
            status=status,
            category=category,
            page=page,
            page_size=page_size,
            project=project,
        )
        return envelope(
            request,
            data={
                **collection.model_dump(mode="json"),
                **snapshot.model_dump(mode="json"),
            },
        )

    @app.get("/api/v1/projects/{project}/library/decisions/{decision_id}")
    def library_decision(project: str, decision_id: str, request: Request):
        require_project(request, project)
        snapshot, decision = app.state.library.detail(decision_id, project=project)
        return envelope(
            request,
            data={
                "decision": decision.model_dump(mode="json"),
                **snapshot.model_dump(mode="json"),
            },
        )

    @app.post("/api/v1/projects/{project}/library/proposals/preview")
    def library_proposal_preview(project: str, body: ProposalDraft, request: Request):
        require_project(request, project)
        require_csrf(request)
        require_capability(request, Capability.MAINTAIN, project)
        return envelope(request, data=preview_proposal(project, body).model_dump(mode="json"))

    @app.post("/api/v1/projects/{project}/library/proposals")
    @serialized_store_write
    def library_proposal_submit(project: str, body: ProposalSubmission, request: Request):
        require_project(request, project)
        require_csrf(request)
        require_capability(request, Capability.MAINTAIN, project)
        actor = actor_for(request)
        preview = preview_proposal(project, body)
        if preview.stale_source:
            raise HTTPException(
                409,
                {
                    "code": "revision-conflict",
                    "message": "library changed; refresh the proposal",
                },
            )
        request_payload = body.model_dump(mode="json")
        request_digest = app.state.store.digest(request_payload)
        route_name = f"POST:/api/v1/projects/{project}/library/proposals"
        prior = app.state.store.db.execute(
            "SELECT request_digest,response_status,response FROM idempotency_records "
            "WHERE actor=? AND project=? AND route=? AND idempotency_key=?",
            (actor, project, route_name, body.idempotency_key),
        ).fetchone()
        if prior:
            if prior["request_digest"] != request_digest:
                raise HTTPException(
                    409,
                    {
                        "code": "idempotency-conflict",
                        "message": "idempotency key was used for different content",
                    },
                )
            response_data = json.loads(prior["response"])
            response_data["idempotent"] = True
            return envelope(request, status="pending", data=response_data)
        database = app.state.store.db
        database.execute("BEGIN IMMEDIATE" if database.__class__.__name__ != "PostgresConnection" else "BEGIN")
        try:
            proposal_id = "proposal_" + app.state.store.digest([project, actor, body.idempotency_key])[:24]
            contribution_id = "contrib_" + app.state.store.digest([project, proposal_id])[:24]
            now = utc_now()
            database.execute(
                "INSERT INTO contributions VALUES(?,?,?,?,?,?)",
                (
                    contribution_id,
                    project,
                    actor,
                    body.model_dump_json(),
                    f"{project}:proposal:{app.state.store.digest([actor, body.idempotency_key])}",
                    now,
                ),
            )
            work_id, _ = app.state.store.add_work(
                project,
                "candidate_task",
                f"proposal:{body.idempotency_key}",
                {
                    "proposal_id": proposal_id,
                    "operation": body.operation,
                    "decision_id": body.decision_id,
                    "proposed_fields": body.proposed_fields,
                    "rationale": body.rationale,
                    "evidence": [{"observation_id": item} for item in body.evidence_references],
                    "authority": body.authority,
                    "publication_intent": body.publication_intent,
                    "authorized_publication": False,
                    "base_library_digest": body.library_digest,
                },
                actor,
                commit=False,
            )
            app.state.store.event(
                work_id,
                actor,
                "contribution-accepted",
                {"contribution_id": contribution_id},
                project,
            )
            database.execute(
                "INSERT INTO contribution_work_links VALUES(?,?,?)",
                (contribution_id, work_id, now),
            )
            database.execute(
                "INSERT INTO library_proposals VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    proposal_id,
                    project,
                    body.decision_id,
                    body.operation,
                    "queued",
                    actor,
                    work_id,
                    contribution_id,
                    body.library_digest,
                    body.model_dump_json(),
                    now,
                    now,
                ),
            )
            response_data = {
                "proposal_id": proposal_id,
                "work_id": work_id,
                "state": "queued",
                "idempotent": False,
            }
            database.execute(
                "INSERT INTO idempotency_records VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    "idem_" + app.state.store.digest([actor, project, route_name, body.idempotency_key])[:24],
                    actor,
                    project,
                    route_name,
                    body.idempotency_key,
                    request_digest,
                    202,
                    json.dumps(response_data),
                    now,
                ),
            )
            database.commit()
        except Exception:
            database.rollback()
            concurrent = database.execute(
                "SELECT request_digest,response FROM idempotency_records "
                "WHERE actor=? AND project=? AND route=? AND idempotency_key=?",
                (actor, project, route_name, body.idempotency_key),
            ).fetchone()
            if concurrent:
                if concurrent["request_digest"] != request_digest:
                    raise HTTPException(
                        409,
                        {
                            "code": "idempotency-conflict",
                            "message": "idempotency key was used for different content",
                        },
                    )
                response_data = json.loads(concurrent["response"])
                response_data["idempotent"] = True
                return envelope(request, status="pending", data=response_data)
            raise
        return envelope(request, status="pending", data=response_data)

    @app.get("/api/v1/projects/{project}/library/proposals")
    def library_proposals(
        project: str,
        request: Request,
        page: int = 1,
        page_size: int = 25,
        status: str | None = None,
    ):
        require_project(request, project)
        if page < 1 or page_size < 1 or page_size > 100:
            raise HTTPException(422, {"code": "invalid-page", "message": "invalid page"})
        allowed_status = {
            "queued",
            "leased",
            "running",
            "waiting-human",
            "retryable",
            "failed",
            "succeeded",
            "cancel-requested",
            "canceled",
            "expired",
        }
        if status and status not in allowed_status:
            raise HTTPException(422, {"code": "invalid-filter", "message": "invalid proposal status"})
        effective_status = "COALESCE(w.state,lp.status)"
        where = "lp.project=?" + (f" AND {effective_status}=?" if status else "")
        params = (project, status) if status else (project,)
        total = app.state.store.db.execute(
            "SELECT COUNT(*) AS count FROM library_proposals lp "
            f"LEFT JOIN work_items w ON w.id=lp.work_id WHERE {where}",
            params,
        ).fetchone()["count"]
        rows = app.state.store.db.execute(
            f"SELECT lp.*,{effective_status} AS effective_status,"
            "COALESCE(w.updated_at,lp.updated_at) AS effective_updated_at "
            "FROM library_proposals lp LEFT JOIN work_items w ON w.id=lp.work_id "
            f"WHERE {where} ORDER BY effective_updated_at DESC,lp.id LIMIT ? OFFSET ?",
            (*params, page_size, (page - 1) * page_size),
        ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["status"] = item.pop("effective_status")
            item["updated_at"] = item.pop("effective_updated_at")
            item["payload"] = safe_value(json.loads(item["payload"]))
            items.append(item)
        return envelope(
            request,
            data={
                "items": items,
                "page": page,
                "page_size": page_size,
                "total": total,
                "next_page": page + 1 if page * page_size < total else None,
            },
        )

    @app.get("/api/v1/projects/{project}/library/proposals/{proposal_id}")
    def library_proposal(project: str, proposal_id: str, request: Request):
        require_project(request, project)
        row = app.state.store.db.execute(
            "SELECT * FROM library_proposals WHERE project=? AND id=?",
            (project, proposal_id),
        ).fetchone()
        if not row:
            raise HTTPException(
                404,
                {"code": "proposal-not-found", "message": "proposal not found"},
            )
        work = app.state.store.work(project, row["work_id"])
        review = app.state.store.db.execute(
            "SELECT id FROM reviews WHERE project=? AND work_id=? ORDER BY created_at DESC LIMIT 1",
            (project, row["work_id"]),
        ).fetchone()
        publication = app.state.store.db.execute(
            "SELECT id FROM publication_history WHERE project=? AND work_id=? ORDER BY created_at DESC LIMIT 1",
            (project, row["work_id"]),
        ).fetchone()
        lifecycle = ProposalLifecycle(
            proposal_id=row["id"],
            work_id=row["work_id"],
            state=work["state"] if work else row["status"],
            created_at=row["created_at"],
            updated_at=work["updated_at"] if work else row["updated_at"],
            review_id=review["id"] if review else None,
            publication_id=publication["id"] if publication else None,
        )
        return envelope(
            request,
            data={
                "proposal": {
                    **dict(row),
                    "payload": safe_value(json.loads(row["payload"])),
                },
                "lifecycle": lifecycle.model_dump(mode="json"),
            },
        )

    @app.get("/api/v1/projects/{project}/costs")
    def costs(project: str, request: Request):
        require_project(request, project)
        row = app.state.store.db.execute(
            "SELECT COALESCE(SUM(input_tokens+output_tokens),0) AS tokens, "
            "COALESCE(SUM(cost),0) AS cost FROM agent_runs "
            "WHERE work_id IN (SELECT id FROM work_items WHERE project=?)",
            (project,),
        ).fetchone()
        return envelope(
            request,
            data={"project": project, "tokens": row["tokens"], "cost": row["cost"]},
        )

    @app.get("/api/v1/projects/{project}/publications")
    def publications(project: str, request: Request, page: int = 1, page_size: int = 25):
        require_project(request, project)
        if page < 1 or page_size < 1 or page_size > 100:
            raise HTTPException(422, {"code": "invalid-pagination", "message": "invalid pagination"})
        total = app.state.store.db.execute(
            "SELECT COUNT(*) AS n FROM publication_history WHERE project=?", (project,)
        ).fetchone()["n"]
        rows = app.state.store.db.execute(
            "SELECT ph.*,lp.id AS proposal_id,w.item_type AS originating_item_type "
            "FROM publication_history ph LEFT JOIN library_proposals lp ON lp.work_id=ph.work_id "
            "LEFT JOIN work_items w ON w.id=ph.work_id WHERE ph.project=? "
            "ORDER BY ph.created_at DESC,ph.id LIMIT ? OFFSET ?",
            (project, page_size, (page - 1) * page_size),
        ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            if item.get("error"):
                item["error"] = redact(str(item["error"]))[0][:2000]
            item["rollback_guidance"] = (
                "Retain the last known-good publication and investigate the failure; "
                "direct rollback is unavailable until the Maintainer defines a safe command."
                if item["status"] != "succeeded"
                else None
            )
            items.append(item)
        return envelope(
            request,
            data={
                "items": items,
                "publications": items,
                "page": page,
                "page_size": page_size,
                "total": total,
                "next_page": page + 1 if page * page_size < total else None,
            },
        )

    def evidence_matches(project: str, observation_id: str) -> list[dict]:
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,256}", observation_id):
            raise HTTPException(422, "observation_id is malformed")
        escaped = observation_id.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

        def references(value) -> bool:
            if isinstance(value, list):
                return any(references(item) for item in value)
            if not isinstance(value, dict):
                return False
            if value.get("observation_id") == observation_id:
                return True
            source_ids = value.get("source_observation_ids")
            if isinstance(source_ids, list) and observation_id in source_ids:
                return True
            return any(references(item) for item in value.values())

        matches = []
        offset = 0
        while len(matches) < 100:
            rows = app.state.store.db.execute(
                "SELECT payload FROM work_items WHERE project=? "
                "AND payload LIKE ? ESCAPE '\\' ORDER BY id LIMIT 500 OFFSET ?",
                (project, f"%{escaped}%", offset),
            ).fetchall()
            if not rows:
                break
            offset += len(rows)
            for row in rows:
                payload = json.loads(row["payload"])
                if not references(payload):
                    continue
                payload.pop("content", None)
                payload.pop("prompt", None)
                payload.pop("response", None)
                matches.append(safe_value(payload))
                if len(matches) == 100:
                    break
        return matches

    @app.get("/api/v1/projects/{project}/evidence/{observation_id}")
    def evidence(project: str, observation_id: str, request: Request):
        require_project(request, project)
        return envelope(
            request,
            data={
                "observation_id": observation_id,
                "matches": evidence_matches(project, observation_id),
            },
        )

    def audit_search(
        project: str,
        *,
        actor: str | None = None,
        action: str | None = None,
        work_id: str | None = None,
        run_id: str | None = None,
        event_id: str | None = None,
        capability: str | None = None,
        policy_revision: int | None = None,
        created_from: str | None = None,
        created_to: str | None = None,
        page: int = 1,
        page_size: int = 25,
    ) -> tuple[list[dict], int]:
        if page < 1 or page_size < 1 or page_size > 100:
            raise HTTPException(422, {"code": "invalid-pagination", "message": "invalid pagination"})
        for value in (actor, action, work_id, run_id, event_id):
            if value and (len(value) > 256 or not re.fullmatch(r"[A-Za-z0-9_.:@/-]+", value)):
                raise HTTPException(422, {"code": "invalid-filter", "message": "invalid audit filter"})
        if capability and capability not in {item.value for item in Capability}:
            raise HTTPException(422, {"code": "invalid-filter", "message": "invalid capability"})
        parsed_dates = []
        for value in (created_from, created_to):
            if value:
                try:
                    parsed_dates.append(datetime.fromisoformat(value.replace("Z", "+00:00")))
                except ValueError as exc:
                    raise HTTPException(422, {"code": "invalid-filter", "message": "invalid date range"}) from exc
        if len(parsed_dates) == 2 and (
            parsed_dates[1] < parsed_dates[0] or (parsed_dates[1] - parsed_dates[0]).days > 366
        ):
            raise HTTPException(422, {"code": "invalid-filter", "message": "invalid date range"})
        clauses = ["project=?"]
        params: list = [project]
        mappings = {
            "actor": actor,
            "event_type": action,
            "work_id": work_id,
            "run_id": run_id,
            "id": event_id,
            "capability": capability,
            "policy_revision": policy_revision,
        }
        for column, value in mappings.items():
            if value is not None:
                clauses.append(f"{column}=?")
                params.append(value)
        if created_from:
            clauses.append("created_at>=?")
            params.append(created_from)
        if created_to:
            clauses.append("created_at<=?")
            params.append(created_to)
        where = " AND ".join(clauses)
        total = app.state.store.db.execute(
            f"SELECT COUNT(*) AS n FROM audit_events WHERE {where}", tuple(params)
        ).fetchone()["n"]
        rows = app.state.store.db.execute(
            f"SELECT * FROM audit_events WHERE {where} ORDER BY created_at DESC,id LIMIT ? OFFSET ?",
            tuple(params + [page_size, (page - 1) * page_size]),
        ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            try:
                item["payload"] = safe_value(json.loads(item["payload"]))
            except (TypeError, json.JSONDecodeError):
                item["payload"] = {"redacted": True}
            items.append(item)
        return items, total

    @app.get("/api/v1/projects/{project}/audit")
    def audit(
        project: str,
        request: Request,
        actor: str | None = None,
        action: str | None = None,
        work_id: str | None = None,
        run_id: str | None = None,
        capability: str | None = None,
        policy_revision: int | None = None,
        created_from: str | None = None,
        created_to: str | None = None,
        page: int = 1,
        page_size: int = 25,
    ):
        require_project(request, project)
        items, total = audit_search(
            project,
            actor=actor,
            action=action,
            work_id=work_id,
            run_id=run_id,
            capability=capability,
            policy_revision=policy_revision,
            created_from=created_from,
            created_to=created_to,
            page=page,
            page_size=page_size,
        )
        return envelope(
            request,
            data={
                "items": items,
                "events": items,
                "page": page,
                "page_size": page_size,
                "total": total,
                "next_page": page + 1 if page * page_size < total else None,
            },
        )

    @app.get("/api/v1/agent-service")
    def agent_service_api(request: Request):
        project = selected_project(request)
        require_capability(request, Capability.READ, project)
        return envelope(request, data=agent_service_summary(project))

    @app.post("/api/v1/agent-service/{action}")
    def agent_service_control_api(action: str, body: AgentServiceControl, request: Request):
        if action not in {"pause", "resume", "drain"}:
            raise HTTPException(404, {"code": "not-found", "message": "unknown control"})
        require_csrf(request)
        require_capability(request, Capability.ADMIN)
        try:
            result = control_service(
                app.state.store,
                action,
                body.expected_version,
                body.reason,
                body.idempotency_key,
                actor_for(request),
                selected_project(request),
            )
        except ServiceConflict as exc:
            raise HTTPException(
                409,
                {
                    "code": exc.code,
                    "message": exc.message,
                    "current_version": exc.current.get("version"),
                    "current_state": exc.current.get("state"),
                },
            ) from exc
        if action == "drain" and complete_drain(app.state.store, actor_for(request)):
            current = app.state.store.service_state()
            result["state"] = current["state"]
            result["version"] = current["version"]
            app.state.store.db.execute(
                "UPDATE idempotency_records SET response=? WHERE actor=? "
                "AND project IS NULL AND route='agent-service:drain' "
                "AND idempotency_key=?",
                (
                    json.dumps(result),
                    actor_for(request),
                    body.idempotency_key,
                ),
            )
            app.state.store.db.commit()
        return envelope(request, data=result)

    def agent_run_detail(project: str, run_id: str):
        project_settings = effective_settings(app.state.store, settings, project)
        run = app.state.store.db.execute(
            "SELECT ar.*,w.project,w.item_type,w.state AS work_state,w.payload "
            "FROM agent_runs ar JOIN work_items w ON w.id=ar.work_id "
            "WHERE ar.work_id=? AND w.project=? ORDER BY ar.created_at DESC LIMIT 1",
            (run_id, project),
        ).fetchone()
        if not run:
            raise HTTPException(404, {"code": "run-not-found", "message": "agent run not found"})
        payload = safe_value(json.loads(run["payload"]))
        timeline = app.state.store.db.execute(
            "SELECT event_type,actor,payload,created_at FROM events WHERE work_id=? ORDER BY created_at",
            (run["work_id"],),
        ).fetchall()
        review = app.state.store.db.execute(
            "SELECT id,status FROM reviews WHERE work_id=?", (run["work_id"],)
        ).fetchone()
        publication = app.state.store.db.execute(
            "SELECT id,status FROM publication_history WHERE work_id=? ORDER BY created_at DESC LIMIT 1",
            (run["work_id"],),
        ).fetchone()
        return {
            **{key: run[key] for key in run.keys() if key != "payload"},
            "run_id": run["work_id"],
            "agent_invocation_id": run["id"],
            "evidence_references": payload.get("evidence", []),
            "routing_checks": payload.get("routing_checks", []),
            "warnings": payload.get("warnings", []),
            "budget": {
                "item_limit": project_settings.item_token_budget,
                "input_tokens": run["input_tokens"],
                "output_tokens": run["output_tokens"],
                "cost": run["cost"],
            },
            "timeline": [safe_value(dict(item)) for item in timeline],
            "review": dict(review) if review else None,
            "publication": dict(publication) if publication else None,
            "raw_payload_state": "redacted",
        }

    @app.get("/api/v1/projects/{project}/agent-runs")
    def agent_runs_api(
        project: str,
        request: Request,
        page: int = 1,
        page_size: int = 25,
    ):
        require_project(request, project)
        if page < 1 or page_size < 1 or page_size > 100:
            raise HTTPException(422, {"code": "invalid-pagination", "message": "invalid pagination"})
        total = app.state.store.db.execute(
            "SELECT COUNT(DISTINCT ar.work_id) AS n FROM agent_runs ar "
            "JOIN work_items w ON w.id=ar.work_id WHERE w.project=?",
            (project,),
        ).fetchone()["n"]
        rows = app.state.store.db.execute(
            "SELECT ar.work_id AS run_id,ar.id AS agent_invocation_id,w.project,ar.status,ar.profile,"
            "ar.cache_hit,ar.input_tokens,ar.output_tokens,ar.cost,ar.started_at,ar.finished_at "
            "FROM agent_runs ar JOIN work_items w ON w.id=ar.work_id "
            "WHERE w.project=? AND ar.created_at=(SELECT MAX(newest.created_at) "
            "FROM agent_runs newest WHERE newest.work_id=ar.work_id) "
            "ORDER BY ar.created_at DESC LIMIT ? OFFSET ?",
            (project, page_size, (page - 1) * page_size),
        ).fetchall()
        return envelope(
            request,
            data={
                "items": [dict(row) for row in rows],
                "page": page,
                "page_size": page_size,
                "total": total,
                "next_page": page + 1 if page * page_size < total else None,
            },
        )

    @app.get("/api/v1/projects/{project}/agent-runs/{run_id}")
    def agent_run_api(project: str, run_id: str, request: Request):
        require_project(request, project)
        return envelope(request, data=agent_run_detail(project, run_id))

    @app.post("/api/v1/projects/{project}/agent-runs/{run_id}/cancel")
    def cancel_agent_run(project: str, run_id: str, body: AgentRunCancellation, request: Request):
        require_project(request, project)
        require_csrf(request)
        require_capability(request, Capability.ADMIN, project)
        actor = actor_for(request)
        scoped_key = f"{actor}:{body.idempotency_key}"
        with app.state.store._write_lock:
            database = app.state.store.db
            database.execute("BEGIN IMMEDIATE" if database.__class__.__name__ != "PostgresConnection" else "BEGIN")
            try:
                prior = database.execute(
                    "SELECT state,work_id,agent_run_id,reason FROM work_cancellations WHERE idempotency_key=?",
                    (scoped_key,),
                ).fetchone()
                if prior:
                    database.rollback()
                    if prior["work_id"] != run_id or prior["reason"] != body.reason:
                        raise HTTPException(
                            409,
                            {
                                "code": "idempotency-conflict",
                                "message": "idempotency key used for different content",
                            },
                        )
                    return envelope(
                        request,
                        status="pending" if prior["state"] == "cancel-requested" else "ok",
                        data={
                            "run_id": run_id,
                            "work_id": prior["work_id"],
                            "state": prior["state"],
                            "idempotent": True,
                        },
                    )
                run_query = (
                    "SELECT ar.id AS invocation_id,ar.work_id,ar.status,w.state FROM agent_runs ar "
                    "JOIN work_items w ON w.id=ar.work_id WHERE ar.work_id=? AND w.project=? "
                    "ORDER BY ar.created_at DESC LIMIT 1"
                )
                if database.__class__.__name__ == "PostgresConnection":
                    run_query += " FOR UPDATE OF ar,w"
                run = database.execute(run_query, (run_id, project)).fetchone()
                if not run:
                    raise HTTPException(404, {"code": "run-not-found", "message": "agent run not found"})
                if run["status"] != "running" or run["state"] != "running":
                    raise HTTPException(
                        409,
                        {"code": "run-not-active", "message": "agent run is not active"},
                    )
                now = utc_now()
                cancellation_id = f"cancel_{app.state.store.digest([actor, run_id, body.idempotency_key])[:24]}"
                database.execute(
                    "INSERT INTO work_cancellations VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        cancellation_id,
                        run["work_id"],
                        run["invocation_id"],
                        "cancel-requested",
                        actor,
                        body.reason,
                        scoped_key,
                        now,
                        None,
                    ),
                )
                updated = database.execute(
                    "UPDATE agent_runs SET status='cancel-requested' WHERE id=? AND status='running'",
                    (run["invocation_id"],),
                )
                if updated.rowcount != 1:
                    raise HTTPException(
                        409,
                        {
                            "code": "run-not-active",
                            "message": "agent run changed concurrently",
                        },
                    )
                app.state.store.transition(
                    project,
                    run["work_id"],
                    "cancel-requested",
                    actor,
                    body.reason,
                    commit=False,
                )
                app.state.store.event(
                    run["work_id"],
                    actor,
                    "agent-run-cancel-requested",
                    {
                        "run_id": run_id,
                        "agent_invocation_id": run["invocation_id"],
                        "reason": body.reason,
                    },
                )
                from .telemetry import append_event

                append_event(
                    app.state.store,
                    project,
                    "agent",
                    "agent-cancel-requested",
                    item_id=run["work_id"],
                    actor_class="human",
                    payload={
                        "invocation_id": run["invocation_id"],
                        "status": "cancel-requested",
                    },
                    commit=False,
                )
                database.commit()
            except Exception:
                database.rollback()
                raise
        return envelope(
            request,
            status="pending",
            data={
                "run_id": run_id,
                "work_id": run["work_id"],
                "state": "cancel-requested",
                "idempotent": False,
            },
        )

    @app.post("/api/v1/projects/{project}/runs/{run_id}/retry")
    @serialized_store_write
    def retry(
        project: str,
        run_id: str,
        request: Request,
        idempotency_key: Annotated[str | None, Header()] = None,
    ):
        require_project(request, project)
        require_csrf(request)
        require_capability(request, Capability.MAINTAIN, project)
        retry_key = idempotency_key or f"retry:{run_id}"
        actor = actor_for(request)
        route_name = f"POST:/runs/{run_id}/retry"
        database = app.state.store.db
        database.execute("BEGIN IMMEDIATE" if database.__class__.__name__ != "PostgresConnection" else "BEGIN")
        try:
            row_query = "SELECT * FROM work_items WHERE project=? AND id=?"
            if database.__class__.__name__ == "PostgresConnection":
                row_query += " FOR UPDATE"
            row = database.execute(row_query, (project, run_id)).fetchone()
            if not row:
                raise HTTPException(404, "run not found")
            prior = database.execute(
                "SELECT response FROM idempotency_records "
                "WHERE actor=? AND project=? AND route=? AND idempotency_key=?",
                (actor, project, route_name, retry_key),
            ).fetchone()
            if prior:
                database.rollback()
                return envelope(
                    request,
                    status="pending",
                    data={"run_id": run_id, "state": "queued", "idempotent": True},
                )
            if row["state"] not in {"failed", "retryable", "expired", "canceled"}:
                raise HTTPException(409, "run is not retryable")
            app.state.store.transition(project, run_id, "queued", actor, commit=False)
            database.execute("UPDATE work_items SET attempts=attempts+1 WHERE id=?", (run_id,))
            app.state.store.event(
                run_id,
                actor,
                "retry-requested",
                {"idempotency_key": retry_key},
            )
            response_data = {"run_id": run_id, "state": "queued", "idempotent": False}
            database.execute(
                "INSERT INTO idempotency_records VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    "idem_" + app.state.store.digest([project, run_id, retry_key])[:24],
                    actor,
                    project,
                    route_name,
                    retry_key,
                    app.state.store.digest({"run_id": run_id}),
                    200,
                    json.dumps(response_data),
                    utc_now(),
                ),
            )
            database.commit()
        except Exception:
            database.rollback()
            raise
        return envelope(
            request,
            status="pending",
            data=response_data,
        )

    @app.post("/api/v1/projects/{project}/route")
    def route_dry_run(project: str, body: RouteRequest, request: Request):
        require_project(request, project)
        require_csrf(request)
        require_capability(request, Capability.MAINTAIN, project)
        project_settings = effective_settings(app.state.store, settings, project)
        return envelope(
            request,
            data=route(body, set(project_settings.excluded_categories)).model_dump(),
        )

    @app.post("/api/v1/projects/{project}/sources")
    def source(
        project: str,
        body: Source,
        request: Request,
        idempotency_key: Annotated[str | None, Header()] = None,
    ):
        key = idempotency_key or f"source:{project}:{body.uri}:{body.external_id}:{body.retrieved_at.isoformat()}"
        require_project(request, project)
        require_csrf(request)
        require_capability(request, Capability.MAINTAIN, project)
        try:
            result = intake_source(
                app.state.store,
                settings,
                project,
                body,
                actor_for(request, "runtime:api"),
                key,
            )
        except SourceIdempotencyConflict as exc:
            raise HTTPException(
                409,
                {
                    "code": "idempotency-conflict",
                    "message": str(exc),
                },
            ) from exc
        return envelope(
            request,
            status="ok" if result.get("status") == "succeeded" else "pending",
            data={**result, "idempotent": not result["created"]},
        )

    @app.post("/api/v1/projects/{project}/harvest-batches")
    def harvest_batch(
        project: str,
        body: HarvestBatch,
        request: Request,
        idempotency_key: Annotated[str | None, Header()] = None,
    ):
        """Accept a redacted, proposal-only private-harvester batch."""
        require_project(request, project)
        require_csrf(request)
        require_capability(request, Capability.MAINTAIN, project)
        try:
            result = intake_harvest_batch(
                app.state.store,
                settings,
                project,
                body,
                actor_for(request, "runtime:harvester"),
                idempotency_key or body.idempotency_key,
            )
        except SourceIdempotencyConflict as exc:
            raise HTTPException(
                409,
                {"code": "idempotency-conflict", "message": str(exc)},
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                422,
                {"code": "invalid-harvest-batch", "message": str(exc)},
            ) from exc
        return envelope(request, status="pending", data=result)

    @app.post("/api/v1/projects/{project}/contributions")
    @serialized_store_write
    def contribution(project: str, body: Contribution, request: Request):
        require_project(request, project)
        require_csrf(request)
        require_capability(request, Capability.MAINTAIN, project)
        actor = actor_for(request)
        publication_requested = bool(body.payload.get("publish"))
        if publication_requested:
            require_capability(request, Capability.ADMIN, project)

        def linked_work_id(contribution_id: str) -> str | None:
            linked = app.state.store.db.execute(
                "SELECT work_id FROM contribution_work_links WHERE contribution_id=?",
                (contribution_id,),
            ).fetchone()
            return linked["work_id"] if linked else None

        request_digest = app.state.store.digest(body.model_dump(mode="json"))
        row = app.state.store.db.execute(
            "SELECT id,actor,payload FROM contributions WHERE project=? AND idempotency_key=?",
            (project, f"{project}:{body.client_idempotency_key}"),
        ).fetchone()
        if row:
            if row["actor"] != actor or app.state.store.digest(json.loads(row["payload"])) != request_digest:
                raise HTTPException(
                    409,
                    {
                        "code": "idempotency-conflict",
                        "message": "idempotency key was used for different content",
                    },
                )
            return envelope(
                request,
                status="pending",
                data={
                    "contribution_id": row["id"],
                    "work_id": linked_work_id(row["id"]),
                    "idempotent": True,
                },
            )
        contribution_key = f"{project}:{body.client_idempotency_key}"
        ident = "contrib_" + app.state.store.digest([project, body.client_idempotency_key])[:24]
        database = app.state.store.db
        database.execute("BEGIN IMMEDIATE" if database.__class__.__name__ != "PostgresConnection" else "BEGIN")
        try:
            database.execute(
                "INSERT INTO contributions VALUES (?,?,?,?,?,?)",
                (
                    ident,
                    project,
                    actor,
                    body.model_dump_json(),
                    contribution_key,
                    utc_now(),
                ),
            )
            work_payload = {
                **body.payload,
                "evidence": [{"observation_id": item} for item in body.evidence_references],
            }
            if body.kind in {"observation", "candidate"}:
                work_payload["clm_payload"] = {
                    key: value for key, value in body.payload.items() if key not in {"publish", "publication_intent"}
                }
            work_payload["publication_intent"] = bool(body.payload.get("publication_intent"))
            work_payload["authorized_publication"] = publication_requested
            if publication_requested:
                app.state.store.event(
                    None,
                    actor,
                    "publication-authorized",
                    {"contribution_id": ident, "capability": Capability.ADMIN.value},
                    project,
                )
            work_id, _ = app.state.store.add_work(
                project,
                f"{body.kind}_task",
                body.client_idempotency_key,
                work_payload,
                actor,
                commit=False,
            )
            app.state.store.event(
                work_id,
                actor,
                "contribution-accepted",
                {"contribution_id": ident},
                project,
            )
            database.execute(
                "INSERT INTO contribution_work_links VALUES(?,?,?)",
                (ident, work_id, utc_now()),
            )
            database.commit()
        except Exception:
            database.rollback()
            concurrent = database.execute(
                "SELECT id,actor,payload FROM contributions WHERE project=? AND idempotency_key=?",
                (project, contribution_key),
            ).fetchone()
            if concurrent:
                if (
                    concurrent["actor"] == actor
                    and app.state.store.digest(json.loads(concurrent["payload"])) == request_digest
                ):
                    return envelope(
                        request,
                        status="pending",
                        data={
                            "contribution_id": concurrent["id"],
                            "work_id": linked_work_id(concurrent["id"]),
                            "idempotent": True,
                        },
                    )
                raise HTTPException(
                    409,
                    {
                        "code": "idempotency-conflict",
                        "message": "idempotency key was used for different content",
                    },
                )
            raise
        return envelope(
            request,
            status="ok",
            data={"contribution_id": ident, "work_id": work_id, "idempotent": False},
        )

    def safe_value(value):
        if isinstance(value, str):
            return redact(value)[0]
        if isinstance(value, list):
            return [safe_value(item) for item in value]
        if isinstance(value, dict):
            return {
                key: safe_value(item)
                for key, item in value.items()
                if not re.search(
                    r"(?i)(content|raw[_-]?(prompt|response)|secret|password|token|api[_-]?key|authorization|credential|private[_-]?key)",
                    key,
                )
            }
        return value

    def review_detail_data(project: str, row) -> dict:
        project_settings = effective_settings(app.state.store, settings, project)
        work = app.state.store.work(project, row["work_id"])
        payload = json.loads(work["payload"]) if work else {}

        safe_payload = safe_value(payload)
        runs = app.state.store.db.execute(
            "SELECT id,status,profile,input_tokens,output_tokens,cost,created_at "
            "FROM agent_runs WHERE work_id=? ORDER BY created_at DESC LIMIT 20",
            (row["work_id"],),
        ).fetchall()
        evidence = app.state.store.db.execute(
            "SELECT id,kind,payload,created_at FROM review_evidence WHERE review_id=? ORDER BY created_at",
            (row["id"],),
        ).fetchall()
        created = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
        return {
            **dict(row),
            "question": safe_value(row["question"]),
            "choices": json.loads(row["choices"]),
            "evidence": safe_value(json.loads(row["evidence"])),
            "resolution": safe_value(json.loads(row["resolution"])) if row["resolution"] else None,
            "work": {
                "id": row["work_id"],
                "state": work["state"] if work else "unavailable",
                "payload": safe_payload,
            },
            "candidates": list(safe_payload.get("alternatives", []))[:4],
            "recommendation": safe_payload.get("recommendation"),
            "reason": safe_payload.get("review_reason", "evidence-conflict"),
            "urgency": safe_payload.get("urgency", "normal"),
            "owner": safe_payload.get("owner"),
            "source": safe_payload.get("source_type"),
            "age_seconds": max(0, int((datetime.now(timezone.utc) - created).total_seconds())),
            "sla_seconds": project_settings.review_reminder_days * 86400,
            "runs": [dict(item) for item in runs],
            "resolution_evidence": [safe_value(dict(item)) for item in evidence],
        }

    def review_summaries(
        project: str,
        *,
        status: str | None,
        urgency: str | None,
        reason: str | None,
        source: str | None,
        owner: str | None,
        page: int,
        page_size: int,
    ) -> tuple[list[dict], int]:
        project_settings = effective_settings(app.state.store, settings, project)
        clauses = ["r.project=?"]
        values: list[object] = [project]
        for column, value in (
            ("r.status", status),
            ("COALESCE(m.urgency,'normal')", urgency),
            ("COALESCE(m.reason,'evidence-conflict')", reason),
            ("m.source", source),
            ("m.owner", owner),
        ):
            if value:
                clauses.append(f"{column}=?")
                values.append(value)
        where = " AND ".join(clauses)
        total = app.state.store.db.execute(
            "SELECT COUNT(*) AS n FROM reviews r LEFT JOIN review_metadata m ON m.review_id=r.id WHERE " + where,
            tuple(values),
        ).fetchone()["n"]
        rows = app.state.store.db.execute(
            "SELECT r.id,r.project,r.work_id,r.question,r.status,r.created_at,r.updated_at,"
            "COALESCE(m.urgency,'normal') AS urgency,"
            "COALESCE(m.reason,'evidence-conflict') AS reason,m.source,m.owner,"
            "w.state AS work_state,w.payload AS work_payload "
            "FROM reviews r LEFT JOIN review_metadata m ON m.review_id=r.id "
            "LEFT JOIN work_items w ON w.id=r.work_id WHERE "
            + where
            + " ORDER BY r.status,r.created_at,r.id LIMIT ? OFFSET ?",
            (*values, page_size, (page - 1) * page_size),
        ).fetchall()
        items = []
        for row in rows:
            payload = json.loads(row["work_payload"]) if row["work_payload"] else {}
            created = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
            items.append(
                {
                    "id": row["id"],
                    "project": row["project"],
                    "work_id": row["work_id"],
                    "question": redact(row["question"])[0],
                    "status": row["status"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                    "urgency": row["urgency"],
                    "reason": row["reason"],
                    "source": row["source"],
                    "owner": row["owner"],
                    "work": {"id": row["work_id"], "state": row["work_state"]},
                    "candidates": [safe_value(item) for item in payload.get("alternatives", [])[:4]],
                    "age_seconds": max(
                        0,
                        int((datetime.now(timezone.utc) - created).total_seconds()),
                    ),
                    "sla_seconds": project_settings.review_reminder_days * 86400,
                }
            )
        return items, total

    def validate_review_text_filters(reason: str | None, source: str | None, owner: str | None) -> None:
        for name, value, limit in (
            ("reason", reason, 128),
            ("source", source, 64),
            ("owner", owner, 256),
        ):
            if value and (len(value) > limit or not re.fullmatch(r"[A-Za-z0-9_.:@/-]+", value)):
                raise HTTPException(
                    422,
                    {"code": "invalid-filter", "message": f"invalid {name} filter"},
                )

    @app.get("/api/v1/projects/{project}/reviews")
    def reviews(
        project: str,
        request: Request,
        status: str | None = None,
        urgency: str | None = None,
        reason: str | None = None,
        source: str | None = None,
        owner: str | None = None,
        page: int = 1,
        page_size: int = 25,
    ):
        require_project(request, project)
        if status not in {None, "open", "resolved"}:
            raise HTTPException(422, {"code": "invalid-filter", "message": "invalid review status"})
        if urgency not in {None, "low", "normal", "high", "critical"}:
            raise HTTPException(422, {"code": "invalid-filter", "message": "invalid urgency"})
        if page < 1 or page_size < 1 or page_size > 100:
            raise HTTPException(422, {"code": "invalid-page", "message": "invalid pagination"})
        validate_review_text_filters(reason, source, owner)
        items, total = review_summaries(
            project,
            status=status,
            urgency=urgency,
            reason=reason,
            source=source,
            owner=owner,
            page=page,
            page_size=page_size,
        )
        data = {
            "items": items,
            "reviews": items,
            "page": page,
            "page_size": page_size,
            "total": total,
            "next_page": page + 1 if page * page_size < total else None,
        }
        return envelope(request, data=data)

    @app.post("/api/v1/projects/{project}/reviews")
    def create_review(project: str, body: ReviewCreate, request: Request):
        require_project(request, project)
        require_csrf(request)
        require_capability(request, Capability.MAINTAIN, project)
        if not app.state.store.work(project, body.work_id):
            raise HTTPException(404, "work item not found")
        review_id = app.state.store.create_review(
            project,
            body.work_id,
            body.question,
            body.choices,
            body.evidence,
            actor_for(request, "runtime:api"),
        )
        return envelope(request, status="pending", data={"review_id": review_id, "idempotent": True})

    @app.get("/api/v1/projects/{project}/reviews/{review_id}")
    def review(project: str, review_id: str, request: Request):
        require_project(request, project)
        row = app.state.store.db.execute(
            "SELECT * FROM reviews WHERE project=? AND id=?", (project, review_id)
        ).fetchone()
        if not row:
            raise HTTPException(404, "review not found")
        return envelope(request, data=review_detail_data(project, row))

    def resolve_review_record(
        project: str,
        row,
        body: ResolveRequest,
        actor: str,
        route_name: str,
    ) -> dict:
        digest = app.state.store.digest(body.model_dump(mode="json"))
        prior = app.state.store.db.execute(
            "SELECT request_digest,response_status,response FROM idempotency_records "
            "WHERE actor=? AND project=? AND route=? AND idempotency_key=?",
            (actor, project, route_name, body.idempotency_key),
        ).fetchone()
        if prior:
            if prior["request_digest"] != digest:
                raise HTTPException(
                    409,
                    {
                        "code": "idempotency-conflict",
                        "message": "key reused for different resolution",
                    },
                )
            data = json.loads(prior["response"])
            if prior["response_status"] == 409:
                raise HTTPException(
                    409,
                    {
                        "code": "review-already-resolved",
                        "message": "review was resolved by another actor",
                    },
                )
            data["idempotent"] = True
            return data
        choices = json.loads(row["choices"])
        if body.choice not in choices:
            raise HTTPException(422, "choice is not one of the review choices")
        cursor = app.state.store.db.execute(
            "UPDATE reviews SET status='resolved', resolution=?, updated_at=? "
            "WHERE id=? AND project=? AND status='open'",
            (body.model_dump_json(), utc_now(), row["id"], project),
        )
        if cursor.rowcount != 1:
            conflict_id = (
                "evidence_" + app.state.store.digest([row["id"], actor, body.idempotency_key, "conflict"])[:24]
            )
            app.state.store.db.execute(
                "INSERT INTO review_evidence VALUES (?,?,?,?,?) ON CONFLICT(id) DO NOTHING",
                (
                    conflict_id,
                    row["id"],
                    "competing-resolution",
                    json.dumps(
                        {
                            "actor": actor,
                            "choice": body.choice,
                            "rationale": body.rationale,
                            "alternatives": choices,
                        }
                    ),
                    utc_now(),
                ),
            )
            app.state.store.event(
                row["work_id"],
                actor,
                "review-resolution-conflict",
                {"review_id": row["id"], "evidence_id": conflict_id},
                project,
            )
            conflict_data = {
                "review_id": row["id"],
                "status": "conflict",
                "evidence_id": conflict_id,
            }
            app.state.store.db.execute(
                "INSERT INTO idempotency_records VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    "idem_" + app.state.store.digest([actor, project, route_name, body.idempotency_key])[:24],
                    actor,
                    project,
                    route_name,
                    body.idempotency_key,
                    digest,
                    409,
                    json.dumps(conflict_data),
                    utc_now(),
                ),
            )
            app.state.store.db.commit()
            raise HTTPException(
                409,
                {
                    "code": "review-already-resolved",
                    "message": "review was resolved by another actor",
                },
            )
        evidence_id = "evidence_" + app.state.store.digest([row["id"], actor, body.idempotency_key])[:24]
        app.state.store.db.execute(
            "INSERT INTO review_evidence VALUES (?,?,?,?,?) ON CONFLICT(id) DO NOTHING",
            (
                evidence_id,
                row["id"],
                "human-resolution",
                json.dumps(
                    {
                        "actor": actor,
                        "choice": body.choice,
                        "rationale": body.rationale,
                        "alternatives": choices,
                    }
                ),
                utc_now(),
            ),
        )
        app.state.store.event(row["work_id"], actor, "review-resolved", body.model_dump(), project)
        from .telemetry import append_event

        append_event(
            app.state.store,
            project,
            "review",
            "review-resolved",
            item_id=row["work_id"],
            actor_class="human",
            payload={"review_id": row["id"], "status": "resolved", "choice": body.choice},
            commit=False,
        )
        apply_review_choice(project, row, body, actor)
        audit = app.state.store.db.execute(
            "SELECT id FROM audit_events WHERE project=? AND work_id=? "
            "AND event_type='review-resolved' ORDER BY created_at DESC LIMIT 1",
            (project, row["work_id"]),
        ).fetchone()
        response_data = {
            "review_id": row["id"],
            "status": "resolved",
            "idempotent": False,
            "audit_event_id": audit["id"] if audit else None,
            "work_id": row["work_id"],
        }
        app.state.store.db.execute(
            "INSERT INTO idempotency_records VALUES(?,?,?,?,?,?,?,?,?)",
            (
                "idem_" + app.state.store.digest([actor, project, route_name, body.idempotency_key])[:24],
                actor,
                project,
                route_name,
                body.idempotency_key,
                digest,
                200,
                json.dumps(response_data),
                utc_now(),
            ),
        )
        app.state.store.db.commit()
        return response_data

    @app.post("/api/v1/projects/{project}/reviews/{review_id}/resolve")
    def resolve(project: str, review_id: str, body: ResolveRequest, request: Request):
        require_project(request, project)
        require_csrf(request)
        require_capability(request, Capability.REVIEW, project)
        row = app.state.store.db.execute(
            "SELECT * FROM reviews WHERE project=? AND id=?", (project, review_id)
        ).fetchone()
        if not row:
            raise HTTPException(404, "review not found")
        work = app.state.store.work(project, row["work_id"])
        if work and body.choice == "adopt-candidate":
            payload = json.loads(work["payload"])
            if payload.get("publication_intent"):
                require_capability(request, Capability.ADMIN, project)
        actor = actor_for(request)
        route_name = f"POST:/api/v1/projects/{project}/reviews/{review_id}/resolve"
        with app.state.review_resolution_lock:
            try:
                response_data = resolve_review_record(project, row, body, actor, route_name)
            except Exception:
                app.state.store.db.rollback()
                raise
        return envelope(request, data=response_data)

    def selected_project(request: Request) -> str:
        identity = request.state.identity
        candidate = identity.selected_project if identity else None
        if candidate and project_allowed(request, candidate):
            return candidate
        if project_allowed(request, settings.project):
            return settings.project
        principal = request.state.principal
        for project in sorted(principal.allowed_projects):
            if project_allowed(request, project):
                return project
        raise HTTPException(404, {"code": "project-not-found", "message": "project not configured"})

    def agent_service_summary(project: str | None = None) -> dict:
        project = project or settings.project
        project_settings = effective_settings(app.state.store, settings, project)
        service = app.state.store.db.execute(
            "SELECT state,version,updated_at FROM agent_service_state WHERE singleton=1"
        ).fetchone()
        heartbeat = app.state.store.db.execute(
            "SELECT observed_at FROM process_heartbeats WHERE process='worker' ORDER BY observed_at DESC LIMIT 1"
        ).fetchone()
        health = heartbeat_health(heartbeat["observed_at"] if heartbeat else None)
        operator_state = service["state"]
        counts = app.state.store.db.execute(
            "SELECT state,COUNT(*) AS n FROM work_items WHERE project=? GROUP BY state",
            (project,),
        ).fetchall()
        active = app.state.store.db.execute(
            "SELECT ar.id,ar.work_id,ar.profile,ar.provider,ar.started_at FROM agent_runs ar "
            "JOIN work_items w ON w.id=ar.work_id WHERE w.project=? "
            "AND ar.status IN ('running','cancel-requested') ORDER BY ar.started_at LIMIT 1",
            (project,),
        ).fetchone()
        budget = app.state.store.db.execute(
            "SELECT reserved_tokens,spent_tokens FROM project_budgets WHERE project=? ORDER BY day DESC LIMIT 1",
            (project,),
        ).fetchone()
        last_success = app.state.store.db.execute(
            "SELECT ar.finished_at FROM agent_runs ar JOIN work_items w ON w.id=ar.work_id "
            "WHERE w.project=? AND ar.status='ok' ORDER BY ar.finished_at DESC LIMIT 1",
            (project,),
        ).fetchone()
        failures = app.state.store.db.execute(
            "SELECT ar.id FROM agent_runs ar JOIN work_items w ON w.id=ar.work_id "
            "WHERE w.project=? AND ar.status='error' ORDER BY ar.finished_at DESC LIMIT 5",
            (project,),
        ).fetchall()
        return {
            **dict(service),
            "operator_state": operator_state,
            "health": health,
            "effective_state": operator_state if health == "healthy" else health,
            "last_heartbeat": heartbeat["observed_at"] if heartbeat else None,
            "active_work_id": active["work_id"] if active else None,
            "active_run": dict(active) if active else None,
            "queue_counts": {row["state"]: row["n"] for row in counts},
            "project_token_budget": project_settings.project_daily_token_budget,
            "project_tokens_used": (budget["spent_tokens"] if budget else 0),
            "project_tokens_reserved": (budget["reserved_tokens"] if budget else 0),
            "last_success": last_success["finished_at"] if last_success else None,
            "recent_failures": [row["id"] for row in failures],
            "worker_concurrency": project_settings.worker_concurrency,
            "lease_seconds": project_settings.lease_seconds,
            "retry_limit": project_settings.max_attempts,
            "agent_adapter_configured": bool(os.getenv("CLM_AGENT_COMMAND", "").strip()),
        }

    def overview_data(project: str) -> dict:
        project_settings = effective_settings(app.state.store, settings, project)
        data = app.state.store.overview(project)
        try:
            snapshot = app.state.library.snapshot(project)
            data["library"] = snapshot.model_dump(mode="json")
        except LibraryError:
            data["library"] = {
                "project": project,
                "publication_revision": "unavailable",
                "library_digest": "",
                "stale": True,
            }
        data["budget"]["limit"] = project_settings.project_daily_token_budget
        used = data["budget"]["reserved_tokens"] + data["budget"]["spent_tokens"]
        data["budget"]["remaining"] = max(0, project_settings.project_daily_token_budget - used)
        data["agent_service"] = agent_service_summary(project)
        return data

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request):
        project = selected_project(request)
        metrics = overview_data(project)
        return render_template(
            "overview.html",
            {
                **page_context(request, "Overview", project),
                "overview": metrics,
                "initial_overview": json.dumps(metrics, separators=(",", ":")),
            },
        )

    def page_context(request: Request, title: str, project: str | None = None) -> dict:
        principal = request.state.principal
        project = project or selected_project(request)
        project_rows = app.state.store.db.execute(
            "SELECT id,name FROM projects WHERE active=1 ORDER BY name,id"
        ).fetchall()
        visible_projects = [dict(row) for row in project_rows if principal.allows(Capability.READ, row["id"])]
        service = agent_service_summary(project)
        return {
            "title": title,
            "project": project,
            "projects": visible_projects,
            "user": principal,
            "can_maintain": principal.allows(Capability.MAINTAIN, project),
            "can_review": principal.allows(Capability.REVIEW, project),
            "can_admin": principal.allows(Capability.ADMIN, project),
            "service_state": service["effective_state"],
            "service_operator_state": service["operator_state"],
            "project_csrf": SessionManager.csrf_for(principal, "POST", "/session/project"),
        }

    @app.get("/agent-service", response_class=HTMLResponse)
    def agent_service_page(request: Request):
        project = selected_project(request)
        return render_template(
            "agent_service/index.html",
            {
                **page_context(request, "Agent Service", project),
                "service": agent_service_summary(project),
                "overview": app.state.store.overview(project),
            },
        )

    @app.get("/agent-service/runs", response_class=HTMLResponse)
    def agent_service_runs_page(request: Request, state: str | None = None, work_id: str | None = None):
        project = selected_project(request)
        allowed = {
            "queued",
            "leased",
            "running",
            "waiting-human",
            "retryable",
            "failed",
            "succeeded",
            "cancel-requested",
            "canceled",
            "stale",
        }
        if state and state not in allowed:
            raise HTTPException(422, {"code": "invalid-filter", "message": "invalid work state"})
        rows = app.state.store.work(project)
        if state == "stale":
            now = utc_now()
            rows = [
                row
                for row in rows
                if row["state"] in {"leased", "running"} and row["lease_expires"] and row["lease_expires"] < now
            ]
        elif state:
            rows = [row for row in rows if row["state"] == state]
        if work_id:
            rows = [row for row in rows if row["id"] == work_id]
        run_rows = app.state.store.db.execute(
            "SELECT id,work_id,status FROM agent_runs WHERE work_id IN "
            "(SELECT id FROM work_items WHERE project=?) ORDER BY created_at DESC",
            (project,),
        ).fetchall()
        latest_runs = {}
        for run in run_rows:
            latest_runs.setdefault(run["work_id"], dict(run))
        return render_template(
            "agent_service/runs.html",
            {
                **page_context(request, "Runs", project),
                "runs": [dict(row) for row in rows],
                "latest_runs": latest_runs,
                "filters": {"state": state, "work_id": work_id},
            },
        )

    @app.get("/agent-service/runs/{run_id}", response_class=HTMLResponse)
    def agent_service_run_detail_page(run_id: str, request: Request):
        project = selected_project(request)
        return render_template(
            "agent_service/detail.html",
            {
                **page_context(request, "Agent run", project),
                "run": agent_run_detail(project, run_id),
                "service": agent_service_summary(project),
            },
        )

    @app.get("/configuration", response_class=HTMLResponse)
    def configuration_page(request: Request):
        project = selected_project(request)
        model = configuration_read_model(app.state.store, settings, project)
        return render_template(
            "configuration/index.html",
            {
                **page_context(request, "Configuration", project),
                "configuration": model,
                "history": configuration_history(app.state.store, project),
            },
        )

    @app.get("/library", response_class=HTMLResponse)
    def library_page(request: Request):
        project = selected_project(request)
        snapshot, decisions = app.state.library.search(project=project)
        return render_template(
            "library/index.html",
            {
                **page_context(request, "Library", project),
                "snapshot": snapshot,
                "decisions": decisions,
                "initial_search": decisions.model_dump_json(),
            },
        )

    @app.get("/library/decisions/{decision_id}", response_class=HTMLResponse)
    def library_detail_page(decision_id: str, request: Request):
        project = selected_project(request)
        snapshot, decision = app.state.library.detail(decision_id, project=project)
        return render_template(
            "library/detail.html",
            {
                **page_context(request, decision.subject, project),
                "snapshot": snapshot,
                "decision": decision,
            },
        )

    @app.get("/library/proposals/new", response_class=HTMLResponse)
    def library_proposal_new_page(request: Request, decision_id: str | None = None):
        project = selected_project(request)
        require_capability(request, Capability.MAINTAIN, project)
        snapshot = app.state.library.snapshot(project)
        if decision_id:
            app.state.library.detail(decision_id, project=project)
        return render_template(
            "library/proposal_new.html",
            {
                **page_context(request, "New proposal", project),
                "snapshot": snapshot,
                "decision_id": decision_id or "",
            },
        )

    @app.get("/library/proposals/{proposal_id}", response_class=HTMLResponse)
    def library_proposal_page(proposal_id: str, request: Request):
        project = selected_project(request)
        require_project(request, project)
        row = app.state.store.db.execute(
            "SELECT * FROM library_proposals WHERE project=? AND id=?",
            (project, proposal_id),
        ).fetchone()
        if not row:
            raise HTTPException(
                404,
                {"code": "proposal-not-found", "message": "proposal not found"},
            )
        work = app.state.store.work(project, row["work_id"])
        review_row = app.state.store.db.execute(
            "SELECT id FROM reviews WHERE project=? AND work_id=? ORDER BY created_at DESC LIMIT 1",
            (project, row["work_id"]),
        ).fetchone()
        publication_row = app.state.store.db.execute(
            "SELECT id FROM publication_history WHERE project=? AND work_id=? ORDER BY created_at DESC LIMIT 1",
            (project, row["work_id"]),
        ).fetchone()
        proposal_data = {
            **dict(row),
            "payload": safe_value(json.loads(row["payload"])),
        }
        lifecycle = ProposalLifecycle(
            proposal_id=row["id"],
            work_id=row["work_id"],
            state=work["state"] if work else row["status"],
            created_at=row["created_at"],
            updated_at=work["updated_at"] if work else row["updated_at"],
            review_id=review_row["id"] if review_row else None,
            publication_id=publication_row["id"] if publication_row else None,
        )
        return render_template(
            "library/proposal_detail.html",
            {
                **page_context(request, "Proposal", project),
                "proposal": proposal_data,
                "lifecycle": lifecycle,
            },
        )

    def html_page(title: str, body: str) -> HTMLResponse:
        nav = (
            "<nav><a href='/'>Overview</a> | <a href='/decisions'>Decisions</a> | "
            "<a href='/reviews'>Reviews</a> | <a href='/publications'>Publications</a> | "
            "<a href='/health'>Health</a> | <a href='/audit'>Audit</a></nav>"
        )
        return HTMLResponse(f"<html><title>{html.escape(title)}</title><body>{nav}{body}</body></html>")

    @app.get("/decisions", response_class=HTMLResponse)
    def decisions_page(request: Request):
        project = selected_project(request)
        require_project(request, project)
        register = settings.library_root / "projects" / project / "decision-register.md"
        content = register.read_text(encoding="utf-8") if register.is_file() else "No decision register available."
        return html_page("Decisions", f"<h1>Project decisions</h1><pre>{html.escape(content)}</pre>")

    @app.get("/evidence/{observation_id}", response_class=HTMLResponse)
    def evidence_page(observation_id: str, request: Request):
        project = selected_project(request)
        require_project(request, project)
        matches = evidence_matches(project, observation_id)
        return html_page(
            "Evidence",
            f"<h1>Evidence {html.escape(observation_id)}</h1><pre>{html.escape(json.dumps(matches, indent=2))}</pre>",
        )

    @app.get("/reviews", response_class=HTMLResponse)
    def reviews_page(
        request: Request,
        status: str | None = None,
        urgency: str | None = None,
        reason: str | None = None,
        source: str | None = None,
        owner: str | None = None,
        page: int = 1,
    ):
        project = selected_project(request)
        if status not in {None, "open", "resolved"} or urgency not in {
            None,
            "low",
            "normal",
            "high",
            "critical",
        }:
            raise HTTPException(422, {"code": "invalid-filter", "message": "invalid review filter"})
        validate_review_text_filters(reason, source, owner)
        items, total = review_summaries(
            project,
            status=status,
            urgency=urgency,
            reason=reason,
            source=source,
            owner=owner,
            page=max(1, page),
            page_size=25,
        )
        return render_template(
            "reviews/index.html",
            {
                **page_context(request, "Reviews", project),
                "reviews": items,
                "filters": {"status": status, "urgency": urgency},
                "total": total,
            },
        )

    @app.get("/publications", response_class=HTMLResponse)
    def publications_page(request: Request):
        project = selected_project(request)
        require_project(request, project)
        rows = app.state.store.db.execute(
            "SELECT ph.*,lp.id AS proposal_id FROM publication_history ph "
            "LEFT JOIN library_proposals lp ON lp.work_id=ph.work_id "
            "WHERE ph.project=? ORDER BY ph.created_at DESC,ph.id",
            (project,),
        ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            if item.get("error"):
                item["error"] = redact(str(item["error"]))[0][:2000]
                item["rollback_guidance"] = (
                    "The last known-good content remains canonical. Investigate and retry; "
                    "direct rollback is unavailable until a safe Maintainer command exists."
                )
            items.append(item)
        return render_template(
            "publications/index.html",
            {
                **page_context(request, "Publication history", project),
                "publications": items,
            },
        )

    @app.get("/health", response_class=HTMLResponse)
    def health_page(request: Request):
        project = selected_project(request)
        require_project(request, project)
        return render_template(
            "runtime/health.html",
            {
                **page_context(request, "Runtime health", project),
                "health": runtime_health_data(project),
            },
        )

    @app.get("/audit", response_class=HTMLResponse)
    def audit_page(
        request: Request,
        event_type: str | None = None,
        event_id: str | None = None,
        actor: str | None = None,
        action: str | None = None,
        work_id: str | None = None,
        run_id: str | None = None,
        capability: str | None = None,
        policy_revision: int | None = None,
        created_from: str | None = None,
        created_to: str | None = None,
        page: int = 1,
    ):
        project = selected_project(request)
        selected_action = action or event_type
        items, total = audit_search(
            project,
            actor=actor,
            action=selected_action,
            work_id=work_id,
            run_id=run_id,
            event_id=event_id,
            capability=capability,
            policy_revision=policy_revision,
            created_from=created_from,
            created_to=created_to,
            page=page,
            page_size=25,
        )
        return render_template(
            "audit/index.html",
            {
                **page_context(request, "Audit", project),
                "events": items,
                "filters": {
                    "event_type": selected_action,
                    "event_id": event_id,
                    "actor": actor,
                    "work_id": work_id,
                    "run_id": run_id,
                    "capability": capability,
                    "policy_revision": policy_revision,
                    "created_from": created_from,
                    "created_to": created_to,
                },
                "total": total,
                "page": page,
                "next_page": page + 1 if page * 25 < total else None,
            },
        )

    @app.get("/reviews/{review_id}", response_class=HTMLResponse)
    def review_page(review_id: str, request: Request):
        project = selected_project(request)
        row = app.state.store.db.execute(
            "SELECT * FROM reviews WHERE project=? AND id=?",
            (project, review_id),
        ).fetchone()
        if not row:
            raise HTTPException(404, "review not found")
        return render_template(
            "reviews/detail.html",
            {
                **page_context(request, "Review", project),
                "review": review_detail_data(project, row),
                "review_csrf": SessionManager.csrf_for(
                    request.state.principal,
                    "POST",
                    f"/reviews/{review_id}/resolve",
                ),
                "web_idempotency_key": secrets.token_urlsafe(24),
            },
        )

    @app.post("/reviews/{review_id}/resolve", response_class=HTMLResponse)
    async def resolve_page(review_id: str, request: Request):
        fields = parse_qs((await request.body()).decode())
        choice = fields.get("choice", [""])[0]
        rationale = fields.get("rationale", [""])[0]
        idempotency_key = fields.get("idempotency_key", [""])[0]
        supplied_csrf = fields.get("csrf_token", [""])[0]
        project = selected_project(request)
        require_csrf(request, supplied_csrf)
        require_capability(request, Capability.REVIEW, project)
        row = app.state.store.db.execute(
            "SELECT * FROM reviews WHERE project=? AND id=?",
            (project, review_id),
        ).fetchone()
        if not row:
            raise HTTPException(404, "review not found")
        choices = json.loads(row["choices"])
        if choice not in choices or not rationale or not idempotency_key:
            raise HTTPException(422, "valid choice and rationale required")
        resolution = ResolveRequest(choice=choice, rationale=rationale, idempotency_key=idempotency_key)
        actor = actor_for(request)
        try:
            with app.state.review_resolution_lock:
                result = resolve_review_record(
                    project,
                    row,
                    resolution,
                    actor,
                    f"POST:/reviews/{review_id}/resolve",
                )
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {}
            if exc.status_code != 409 or detail.get("code") != "review-already-resolved":
                raise
            current = app.state.store.db.execute(
                "SELECT * FROM reviews WHERE project=? AND id=?", (project, review_id)
            ).fetchone()
            return render_template(
                "reviews/conflict.html",
                {
                    **page_context(request, "Review already resolved", project),
                    "review": review_detail_data(project, current),
                },
                status_code=409,
            )
        return render_template(
            "reviews/result.html",
            {
                **page_context(request, "Review resolved", project),
                "result": result,
            },
        )

    return app
