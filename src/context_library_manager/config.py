from __future__ import annotations

import os
import re
import secrets
from dataclasses import dataclass, field, replace
from pathlib import Path
from urllib.parse import urlparse

import yaml


class ConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class ManagedProject:
    """One explicitly enrolled project pack in a Manager deployment."""

    id: str
    library_root: Path
    state_namespace: str
    enabled: bool = True

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,127}", self.id):
            raise ConfigurationError("managed project id must be a stable lowercase identifier")
        if not self.library_root.is_absolute() or self.library_root == Path("/"):
            raise ConfigurationError("managed project library_root must be a safe absolute path")
        if self.library_root.is_symlink():
            raise ConfigurationError("managed project library_root must not be a symlink")
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,127}", self.state_namespace):
            raise ConfigurationError("managed project state_namespace must be stable")


@dataclass(frozen=True)
class Settings:
    database_url: str
    library_root: Path
    state_root: Path
    project: str
    require_oidc: bool = True
    oidc_hs256_secret: str | None = None
    oidc_issuer: str | None = None
    oidc_jwks_url: str | None = None
    oidc_audience: str | None = None
    csrf_secret: str = ""
    item_token_budget: int = 16_000
    max_attempts: int = 3
    semantic_threshold: float = 0.50
    worker_concurrency: int = 4
    lease_seconds: int = 1800
    review_reminder_days: int = 7
    project_daily_token_budget: int = 500_000
    user_daily_token_budget: int = 100_000
    cheap_profile_max_tokens: int = 2_000
    standard_profile_max_tokens: int = 8_000
    cache_enabled: bool = True
    excluded_categories: tuple[str, ...] = ("security",)
    allow_local_dev_identity: bool = False
    autonomy_enabled: bool = True
    notifications_enabled: bool = True
    in_app_notifications: bool = True
    webhook_url: str | None = None
    webhook_secret: str | None = None
    development_mode: bool = False
    session_secret: str = ""
    session_duration_seconds: int = 900
    oidc_authorize_url: str | None = None
    oidc_token_url: str | None = None
    oidc_client_id: str | None = None
    oidc_client_secret: str | None = None
    oidc_redirect_uri: str | None = None
    field_sources: dict[str, str] = field(default_factory=dict, compare=False)
    managed_projects: tuple[ManagedProject, ...] = field(default_factory=tuple)
    _allow_unmanaged_effective_project: bool = field(default=False, repr=False, compare=False)
    explicit_project_registry: bool = field(init=False, default=False, repr=False, compare=False)
    ephemeral_session_secret: bool = field(init=False, default=False)

    def __post_init__(self):
        object.__setattr__(self, "explicit_project_registry", bool(self.managed_projects))
        if not self.managed_projects:
            object.__setattr__(
                self,
                "managed_projects",
                (
                    ManagedProject(
                        self.project,
                        self.library_root / "projects" / self.project,
                        self.project,
                    ),
                ),
            )
        ids = [item.id for item in self.managed_projects]
        if len(ids) != len(set(ids)):
            raise ConfigurationError("managed project ids must be unique")
        namespaces = [item.state_namespace for item in self.managed_projects]
        if len(namespaces) != len(set(namespaces)):
            raise ConfigurationError("managed project state namespaces must be unique")
        if self.project not in set(ids) and not self._allow_unmanaged_effective_project:
            raise ConfigurationError("default project must be explicitly managed")
        if not self.csrf_secret:
            object.__setattr__(self, "csrf_secret", secrets.token_urlsafe(32))
        if not self.session_secret:
            object.__setattr__(self, "session_secret", secrets.token_urlsafe(32))
            object.__setattr__(self, "ephemeral_session_secret", True)
        if (
            self.worker_concurrency <= 0
            or self.lease_seconds <= 0
            or self.review_reminder_days <= 0
            or self.session_duration_seconds <= 0
        ):
            raise ConfigurationError("runtime durations and concurrency must be positive")
        for name in (
            "lease_seconds",
            "review_reminder_days",
            "project_daily_token_budget",
            "user_daily_token_budget",
            "item_token_budget",
            "cheap_profile_max_tokens",
            "standard_profile_max_tokens",
            "max_attempts",
            "session_duration_seconds",
        ):
            if getattr(self, name) < 0:
                raise ConfigurationError(f"{name} must not be negative")
        if self.semantic_threshold < 0 or self.semantic_threshold > 1:
            raise ConfigurationError("semantic_threshold must be between 0 and 1")
        if not self.library_root.is_absolute() or self.library_root == Path("/"):
            raise ConfigurationError("library_root must be a safe absolute path")
        if self.library_root.is_symlink():
            raise ConfigurationError("library_root must not be a symlink")
        if self.webhook_url:
            parsed = urlparse(self.webhook_url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ConfigurationError("webhook_url must be an absolute HTTP(S) URL")
            if not self.webhook_secret:
                raise ConfigurationError("webhook_secret is required with webhook_url")
        if self.oidc_jwks_url:
            parsed = urlparse(self.oidc_jwks_url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ConfigurationError("oidc_jwks_url must be an absolute HTTP(S) URL")
            if not self.oidc_issuer:
                raise ConfigurationError("oidc_issuer is required with oidc_jwks_url")

    @classmethod
    def from_env(cls, project: str | None = None) -> "Settings":
        chosen = project or os.getenv("CLM_PROJECT")
        if not chosen:
            raise ConfigurationError("project must be explicit via an argument or CLM_PROJECT")
        database = os.getenv("CLM_DATABASE_URL", "sqlite:///./runtime-state/runtime.db")
        configured_library_root = os.getenv("CLM_LIBRARY_ROOT")
        if not configured_library_root:
            raise ConfigurationError("CLM_LIBRARY_ROOT must explicitly identify the canonical library")
        field_sources: dict[str, str] = {}
        values: dict = {
            "database_url": database,
            "library_root": Path(configured_library_root),
            "state_root": Path(os.getenv("CLM_STATE_ROOT", "runtime-state")),
            "project": chosen,
            "require_oidc": os.getenv("CLM_REQUIRE_OIDC", "true").lower() == "true",
            "allow_local_dev_identity": os.getenv("CLM_ALLOW_LOCAL_DEV_IDENTITY", "false").lower() == "true",
            "oidc_hs256_secret": os.getenv("CLM_OIDC_HS256_SECRET"),
            "oidc_issuer": os.getenv("CLM_OIDC_ISSUER"),
            "oidc_jwks_url": os.getenv("CLM_OIDC_JWKS_URL"),
            "oidc_audience": os.getenv("CLM_OIDC_AUDIENCE"),
            "oidc_authorize_url": os.getenv("CLM_OIDC_AUTHORIZE_URL"),
            "oidc_token_url": os.getenv("CLM_OIDC_TOKEN_URL"),
            "oidc_client_id": os.getenv("CLM_OIDC_CLIENT_ID"),
            "oidc_client_secret": os.getenv("CLM_OIDC_CLIENT_SECRET"),
            "oidc_redirect_uri": os.getenv("CLM_OIDC_REDIRECT_URI"),
            "csrf_secret": os.getenv("CLM_CSRF_SECRET", ""),
            "session_secret": os.getenv("CLM_SESSION_SECRET", ""),
            "session_duration_seconds": int(os.getenv("CLM_SESSION_DURATION_SECONDS", "900")),
            "development_mode": os.getenv("CLM_DEVELOPMENT_MODE", "false").lower() == "true",
            "webhook_secret": os.getenv("CLM_WEBHOOK_SECRET"),
        }
        config_file = os.getenv("CLM_CONFIG_FILE")
        if not config_file:
            candidate = values["library_root"] / "projects" / chosen / "runtime.yaml"
            config_file = str(candidate) if candidate.is_file() else None
        if config_file:
            try:
                raw = yaml.safe_load(Path(config_file).read_text(encoding="utf-8")) or {}
                if not isinstance(raw, dict):
                    raise ConfigurationError("runtime configuration must be a mapping")
                allowed = {
                    "managed_projects",
                    "schema_version",
                    "runtime",
                    "autonomy",
                    "cost",
                    "notifications",
                    "security",
                }
                unknown = set(raw) - allowed
                if unknown or raw.get("schema_version") != 1:
                    raise ConfigurationError(f"unknown or invalid configuration fields: {sorted(unknown)}")
                sections = {key: value for key, value in raw.items() if key != "schema_version"}
                managed = sections.pop("managed_projects", None)
                if managed is not None:
                    if not isinstance(managed, list) or not managed:
                        raise ConfigurationError("managed_projects must be a non-empty list")
                    entries: list[ManagedProject] = []
                    for entry in managed:
                        if not isinstance(entry, dict) or set(entry) - {
                            "id",
                            "library_root",
                            "state_namespace",
                            "enabled",
                        }:
                            raise ConfigurationError("managed project has unknown fields")
                        try:
                            entries.append(
                                ManagedProject(
                                    id=entry["id"],
                                    library_root=Path(entry["library_root"]),
                                    state_namespace=entry.get("state_namespace", entry["id"]),
                                    enabled=entry.get("enabled", True),
                                )
                            )
                        except (KeyError, TypeError, ValueError) as exc:
                            raise ConfigurationError("invalid managed project entry") from exc
                    values["managed_projects"] = tuple(entries)
                    if chosen not in {entry.id for entry in entries}:
                        raise ConfigurationError("CLM_PROJECT must name an enrolled project")
                mapping = {
                    "runtime": {
                        "database_url": "database_url",
                        "library_root": "library_root",
                        "worker_concurrency": "worker_concurrency",
                        "lease_seconds": "lease_seconds",
                        "review_reminder_days": "review_reminder_days",
                    },
                    "autonomy": {
                        "enabled": "autonomy_enabled",
                        "semantic_threshold": "semantic_threshold",
                        "excluded_categories": "excluded_categories",
                    },
                    "cost": {
                        "project_daily_token_budget": "project_daily_token_budget",
                        "user_daily_token_budget": "user_daily_token_budget",
                        "item_token_budget": "item_token_budget",
                        "cheap_profile_max_tokens": "cheap_profile_max_tokens",
                        "standard_profile_max_tokens": "standard_profile_max_tokens",
                        "max_attempts_per_item": "max_attempts",
                        "cache_enabled": "cache_enabled",
                    },
                    "security": {
                        "require_oidc": "require_oidc",
                        "allow_local_dev_identity": "allow_local_dev_identity",
                    },
                    "notifications": {
                        "enabled": "notifications_enabled",
                        "in_app": "in_app_notifications",
                        "webhook_url": "webhook_url",
                    },
                }
                for section, entries in sections.items():
                    if not isinstance(entries, dict):
                        raise ConfigurationError(f"{section} must be a mapping")
                    for key, value in entries.items():
                        if key not in mapping.get(section, {}):
                            raise ConfigurationError(f"unknown {section} field: {key}")
                        target = mapping[section][key]
                        if target == "library_root":
                            value = Path(value)
                        elif target == "excluded_categories":
                            value = tuple(value)
                        elif target == "webhook_url" and value:
                            raise ConfigurationError("webhook_url must come from the environment or secret store")
                        env_names = {
                            "database_url": "CLM_DATABASE_URL",
                            "library_root": "CLM_LIBRARY_ROOT",
                            "state_root": "CLM_STATE_ROOT",
                            "require_oidc": "CLM_REQUIRE_OIDC",
                        }
                        if env_names.get(target) and os.getenv(env_names[target]) is not None:
                            field_sources[target] = "environment"
                            continue
                        values[target] = value
                        field_sources[target] = "project-file"
            except yaml.YAMLError as exc:
                raise ConfigurationError(f"invalid YAML configuration: {exc}") from exc
        values["field_sources"] = field_sources
        return cls(**values)

    @property
    def db_path(self) -> Path:
        if not self.database_url.startswith("sqlite:///"):
            raise ValueError("db_path is only available for SQLite URLs")
        return Path(self.database_url.removeprefix("sqlite:///"))

    @property
    def storage_target(self) -> Path | str:
        return self.db_path if self.database_url.startswith("sqlite:///") else self.database_url

    @property
    def managed_project_ids(self) -> tuple[str, ...]:
        return tuple(item.id for item in self.managed_projects if item.enabled)

    def project_config(self, project: str) -> ManagedProject:
        for entry in self.managed_projects:
            if entry.id == project:
                return entry
        if not self.explicit_project_registry:
            return ManagedProject(
                project,
                self.library_root / "projects" / project,
                project,
            )
        raise ConfigurationError(f"project is not explicitly managed: {project}")

    @staticmethod
    def _manager_library_root(entry: ManagedProject) -> Path:
        """Return the canonical root expected by Manager/Maintainer services."""
        project_root = entry.library_root
        if project_root.name == entry.id and project_root.parent.name == "projects":
            return project_root.parent.parent
        return project_root

    def for_project(self, project: str) -> "Settings":
        entry = self.project_config(project)
        return replace(
            self,
            project=project,
            library_root=self._manager_library_root(entry),
            _allow_unmanaged_effective_project=project not in self.managed_project_ids,
        )
