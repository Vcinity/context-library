from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .contracts import Capability, SessionIdentity
from .domain import utc_now

LEGACY_CAPABILITIES = {
    "viewer": Capability.READ,
    "reviewer": Capability.REVIEW,
    "maintainer": Capability.MAINTAIN,
    "administrator": Capability.ADMIN,
}
CAPABILITY_ORDER = (
    Capability.READ,
    Capability.REVIEW,
    Capability.MAINTAIN,
    Capability.ADMIN,
)
CAPABILITY_EXPANSION = {
    Capability.READ: {Capability.READ},
    Capability.REVIEW: {Capability.READ, Capability.REVIEW},
    Capability.MAINTAIN: {Capability.READ, Capability.REVIEW, Capability.MAINTAIN},
    Capability.ADMIN: set(CAPABILITY_ORDER),
}


def normalize_capabilities(values: list[str] | set[str]) -> set[Capability]:
    normalized: set[Capability] = set()
    for value in values:
        try:
            capability = Capability(value)
        except ValueError:
            capability = LEGACY_CAPABILITIES.get(value)
        if capability:
            normalized.update(CAPABILITY_EXPANSION[capability])
    return normalized


def capability_values(values: set[Capability]) -> list[str]:
    return [item.value for item in CAPABILITY_ORDER if item in values]


def is_m2m(claims: dict[str, Any]) -> bool:
    explicit = claims.get("token_class") or claims.get("typ")
    if explicit in {"m2m", "client-credentials", "client_credentials"}:
        return True
    if explicit in {"human", "interactive", "user"}:
        return False
    raise ValueError("token_class must explicitly identify human or m2m authority")


@dataclass(frozen=True)
class Principal:
    subject: str
    display_name: str
    capabilities: frozenset[Capability]
    allowed_projects: frozenset[str]
    actor_class: str = "human"
    source_claims: tuple[str, ...] = ()
    csrf_token: str | None = None
    session_id: str | None = None

    def allows(self, capability: Capability, project: str | None = None) -> bool:
        return capability in self.capabilities and (project is None or project in self.allowed_projects)


def principal_from_claims(claims: dict[str, Any], default_project: str) -> Principal:
    source = roles(claims)
    capabilities = normalize_capabilities(source)
    if not capabilities:
        raise ValueError("token has no recognized capability")
    allowed = claims.get("projects")
    if not isinstance(allowed, list):
        project = claims.get("project")
        allowed = [project] if isinstance(project, str) and project else []
    if not allowed or any(not isinstance(item, str) or not item for item in allowed):
        raise ValueError("token must explicitly identify at least one authorized project")
    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject:
        raise ValueError("token subject is required")
    display = claims.get("name") or claims.get("preferred_username") or subject
    return Principal(
        subject=subject,
        display_name=str(display),
        capabilities=frozenset(capabilities),
        allowed_projects=frozenset(str(item) for item in allowed),
        actor_class="m2m" if is_m2m(claims) else "human",
        source_claims=tuple(sorted(source)),
    )


class SessionManager:
    cookie_name = "clm_session"

    def __init__(self, store, secret: str, duration_seconds: int = 900):
        self.store = store
        self.secret = secret.encode()
        self.key = hashlib.sha256(self.secret + b":encryption").digest()
        self.signing_key = hashlib.sha256(self.secret + b":signature").digest()
        self.duration_seconds = duration_seconds

    @staticmethod
    def _b64(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).decode().rstrip("=")

    @staticmethod
    def _unb64(value: str) -> bytes:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))

    def _seal(self, raw: str) -> str:
        nonce = secrets.token_bytes(12)
        encrypted = nonce + AESGCM(self.key).encrypt(nonce, raw.encode(), b"clm-session")
        body = self._b64(encrypted)
        signature = self._b64(hmac.new(self.signing_key, body.encode(), hashlib.sha256).digest())
        return f"{body}.{signature}"

    def _open(self, value: str) -> str:
        try:
            body, signature = value.split(".", 1)
            expected = hmac.new(self.signing_key, body.encode(), hashlib.sha256).digest()
            if not hmac.compare_digest(self._unb64(signature), expected):
                raise ValueError("invalid session signature")
            encrypted = self._unb64(body)
            return AESGCM(self.key).decrypt(encrypted[:12], encrypted[12:], b"clm-session").decode()
        except Exception as exc:
            raise ValueError("invalid session cookie") from exc

    def _session_key(self, raw_id: str) -> str:
        return hashlib.sha256(raw_id.encode()).hexdigest()

    def _csrf(self, raw_id: str) -> str:
        return self._b64(hmac.new(self.secret, f"csrf:{raw_id}".encode(), hashlib.sha256).digest())

    @staticmethod
    def csrf_for(principal: Principal, method: str, path: str) -> str:
        if not principal.csrf_token:
            raise ValueError("browser session required")
        intent = f"{method.upper()}:{path}"
        return SessionManager._b64(hmac.new(principal.csrf_token.encode(), intent.encode(), hashlib.sha256).digest())

    def create(
        self,
        subject: str,
        display_name: str,
        capabilities: set[Capability],
        allowed_projects: set[str],
        selected_project: str | None = None,
        oidc_session_reference: str | None = None,
    ) -> tuple[str, SessionIdentity]:
        now = datetime.now(timezone.utc)
        expires = now + timedelta(seconds=self.duration_seconds)
        raw_id = secrets.token_urlsafe(32)
        csrf = self._csrf(raw_id)
        self.store.db.execute(
            "INSERT INTO browser_sessions "
            "(id,subject,display_name,capabilities,allowed_projects,selected_project,"
            "csrf_digest,oidc_session_reference,issued_at,expires_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                self._session_key(raw_id),
                subject,
                display_name,
                json.dumps(capability_values(capabilities)),
                json.dumps(sorted(allowed_projects)),
                selected_project,
                hashlib.sha256(csrf.encode()).hexdigest(),
                oidc_session_reference,
                now.isoformat().replace("+00:00", "Z"),
                expires.isoformat().replace("+00:00", "Z"),
            ),
        )
        self.store.db.commit()
        identity = SessionIdentity(
            subject=subject,
            display_name=display_name,
            capabilities=capability_values(capabilities),
            allowed_projects=sorted(allowed_projects),
            selected_project=selected_project,
            issued_at=now,
            expires_at=expires,
            oidc_session_reference=oidc_session_reference,
            csrf_token=None,
        )
        return self._seal(raw_id), identity

    def load(self, value: str | None) -> tuple[Principal, SessionIdentity] | None:
        if not value:
            return None
        try:
            raw_id = self._open(value)
        except ValueError:
            return None
        row = self.store.db.execute(
            "SELECT * FROM browser_sessions WHERE id=? AND invalidated_at IS NULL",
            (self._session_key(raw_id),),
        ).fetchone()
        if not row:
            return None
        expires = datetime.fromisoformat(row["expires_at"].replace("Z", "+00:00"))
        if expires <= datetime.now(timezone.utc):
            return None
        csrf = self._csrf(raw_id)
        if not hmac.compare_digest(hashlib.sha256(csrf.encode()).hexdigest(), row["csrf_digest"]):
            return None
        capability_set = normalize_capabilities(set(json.loads(row["capabilities"])))
        projects = set(json.loads(row["allowed_projects"]))
        identity = SessionIdentity(
            subject=row["subject"],
            display_name=row["display_name"],
            capabilities=capability_values(capability_set),
            allowed_projects=sorted(projects),
            selected_project=row["selected_project"],
            issued_at=datetime.fromisoformat(row["issued_at"].replace("Z", "+00:00")),
            expires_at=expires,
            oidc_session_reference=row["oidc_session_reference"],
            csrf_token=None,
        )
        principal = Principal(
            subject=identity.subject,
            display_name=identity.display_name,
            capabilities=frozenset(capability_set),
            allowed_projects=frozenset(projects),
            csrf_token=csrf,
            session_id=row["id"],
        )
        return principal, identity

    def invalidate(self, value: str | None) -> None:
        if not value:
            return
        try:
            raw_id = self._open(value)
        except ValueError:
            return
        self.store.db.execute(
            "UPDATE browser_sessions SET invalidated_at=? WHERE id=?",
            (utc_now(), self._session_key(raw_id)),
        )
        self.store.db.commit()

    def select_project(self, principal: Principal, project: str) -> None:
        if not principal.session_id or project not in principal.allowed_projects:
            raise ValueError("project is outside the session scope")
        cursor = self.store.db.execute(
            "UPDATE browser_sessions SET selected_project=? WHERE id=? AND invalidated_at IS NULL",
            (project, principal.session_id),
        )
        if cursor.rowcount != 1:
            self.store.db.rollback()
            raise ValueError("browser session is unavailable")
        self.store.db.commit()


def bearer_claims(
    header: str | None,
    verification_secret: str | None = None,
    issuer: str | None = None,
    jwks_url: str | None = None,
    audience: str | None = None,
) -> dict[str, Any]:
    if not header or not header.startswith("Bearer "):
        raise ValueError("Bearer token required")
    token = header[7:]
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("malformed JWT")

    def decode(value: str) -> bytes:
        padding = "=" * (-len(value) % 4)
        return base64.urlsafe_b64decode((value + padding).encode())

    try:
        header_claims = json.loads(decode(parts[0]))
        claims = json.loads(decode(parts[1]))
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("malformed JWT") from exc
    if not isinstance(header_claims, dict) or not isinstance(claims, dict):
        raise ValueError("malformed JWT")
    if jwks_url:
        if header_claims.get("alg") not in {
            "RS256",
            "RS384",
            "RS512",
            "ES256",
            "ES384",
            "ES512",
        }:
            raise ValueError("unsupported OIDC signing algorithm")
        try:
            import jwt

            signing_key = jwt.PyJWKClient(jwks_url).get_signing_key_from_jwt(token)
            options = {"verify_aud": audience is not None, "require": ["exp"]}
            verified = jwt.decode(
                token,
                signing_key.key,
                algorithms=[header_claims["alg"]],
                issuer=issuer,
                audience=audience,
                options=options,
            )
        except Exception as exc:
            raise ValueError("OIDC token verification failed") from exc
        return verified
    if header_claims.get("alg") != "HS256" or not verification_secret:
        raise ValueError("JWT signature verification is not configured")
    expected = hmac.new(
        verification_secret.encode(),
        f"{parts[0]}.{parts[1]}".encode(),
        hashlib.sha256,
    ).digest()
    try:
        supplied = decode(parts[2])
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("malformed JWT signature") from exc
    if not hmac.compare_digest(supplied, expected):
        raise ValueError("invalid JWT signature")
    if claims.get("exp") is None:
        raise ValueError("JWT expiry is required")
    if float(claims["exp"]) <= time.time():
        raise ValueError("expired JWT")
    if issuer is not None and claims.get("iss") != issuer:
        raise ValueError("invalid JWT issuer")
    if audience is not None:
        token_audience = claims.get("aud")
        audiences = {token_audience} if isinstance(token_audience, str) else set(token_audience or [])
        if audience not in audiences:
            raise ValueError("invalid JWT audience")
    return claims


def roles(claims: dict) -> set[str]:
    values = claims.get("roles", claims.get("realm_access", {}).get("roles", []))
    return set(values) if isinstance(values, list) else set()
