"""Resolve deployment-time Plugin settings with environment overrides."""

from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().with_name("runtime-config.json")
SCHEMA = "context-library/plugin-runtime-config"
SCHEMA_VERSION = 1
FIELDS = {
    "library_root": "CONTEXT_LIBRARY_ROOT",
    "project": "CONTEXT_LIBRARY_PROJECT",
    "context_requirement": "CONTEXT_LIBRARY_CONTEXT_REQUIREMENT",
}
PROJECT_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")

# Preflight condition enum: a runtime-config/root-layer classification
# evaluated before SPEC Section 12.5 content resolution. It is a distinct,
# machine-readable vocabulary that downstream consumers (the MCP status
# boundary, session-start diagnostics, and issue #48's fail-stop policy) must
# preserve rather than collapse into a generic state.
CONDITION_HEALTHY = "healthy"
CONDITION_MISSING_CONFIG = "missing_config"
CONDITION_MALFORMED_CONFIG = "malformed_config"
CONDITION_UNREADABLE_CONFIG = "unreadable_config"
CONDITION_MISSING_ROOT = "missing_root"
CONDITION_UNREADABLE_ROOT = "unreadable_root"

REMEDIATION = {
    CONDITION_MISSING_CONFIG: (
        "Create the deployment-local runtime configuration with "
        "'python3 plugins/context-library/scripts/configure.py --library-root <path>', "
        "or set the CONTEXT_LIBRARY_ROOT environment variable."
    ),
    CONDITION_MALFORMED_CONFIG: (
        "Regenerate the bundled runtime configuration with "
        "'python3 plugins/context-library/scripts/configure.py --library-root <path>'; "
        "the existing runtime-config.json failed schema validation."
    ),
    CONDITION_UNREADABLE_CONFIG: (
        "Fix file permissions on the bundled runtime-config.json so the Plugin "
        "process can read it, or regenerate it with scripts/configure.py."
    ),
    CONDITION_MISSING_ROOT: (
        "Verify the configured context library root path exists and is mounted, "
        "then reconfigure with 'scripts/configure.py --library-root <path>' if it moved."
    ),
    CONDITION_UNREADABLE_ROOT: (
        "Grant the Plugin process read and traverse access to the configured "
        "context library root directory and its contents."
    ),
}


class RuntimeConfigError(ValueError):
    """The bundled Plugin runtime configuration is malformed."""


class RuntimeConfigUnreadable(RuntimeConfigError):
    """The bundled Plugin runtime configuration exists but could not be read."""


@dataclass(frozen=True)
class Setting:
    value: str | None
    source: str | None


@dataclass(frozen=True)
class RuntimePreflight:
    """A safe, redacted classification of the deployment-local runtime state."""

    condition: str
    allowed: bool
    config_path: str
    config_source: str | None
    library_root: str | None
    remediation: str | None


def load_runtime_config(path: Path = CONFIG_PATH) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise RuntimeConfigUnreadable(f"unable to read Plugin runtime configuration {path}: {exc}") from exc
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeConfigError(f"Plugin runtime configuration {path} is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeConfigError("Plugin runtime configuration must be a JSON object")
    allowed = {"schema", "schema_version", *FIELDS}
    unknown = set(payload).difference(allowed)
    if unknown:
        raise RuntimeConfigError(f"unknown Plugin runtime configuration field: {sorted(unknown)[0]}")
    if payload.get("schema") != SCHEMA or payload.get("schema_version") != SCHEMA_VERSION:
        raise RuntimeConfigError("unsupported Plugin runtime configuration schema")
    values: dict[str, str] = {}
    for field in FIELDS:
        value = payload.get(field)
        if value is None:
            continue
        if not isinstance(value, str) or not value.strip():
            raise RuntimeConfigError(f"Plugin runtime configuration field {field!r} must be a non-empty string")
        values[field] = value
    project = values.get("project")
    if project is not None and not PROJECT_RE.fullmatch(project):
        raise RuntimeConfigError("configured project must be a stable lowercase identifier")
    requirement = values.get("context_requirement")
    if requirement is not None and requirement not in {"required", "optional", "disabled"}:
        raise RuntimeConfigError(f"invalid context requirement: {requirement}")
    library_root = values.get("library_root")
    if library_root is not None and not Path(library_root).expanduser().is_absolute():
        raise RuntimeConfigError("configured library root must be an absolute path")
    return values


def setting(field: str, path: Path | None = None) -> Setting:
    try:
        environment_name = FIELDS[field]
    except KeyError as exc:
        raise RuntimeConfigError(f"unknown Plugin runtime setting: {field}") from exc
    environment_value = os.environ.get(environment_name)
    if environment_value is not None:
        return Setting(environment_value, "environment")
    resolved_path = path or CONFIG_PATH
    value = load_runtime_config(resolved_path).get(field)
    return Setting(value, str(resolved_path) if value is not None else None)


def _root_condition(root: Path) -> str | None:
    """Classify a resolved library root, distinguishing absence from unreadability."""
    try:
        info = root.stat()
    except FileNotFoundError:
        return CONDITION_MISSING_ROOT
    except OSError:
        return CONDITION_UNREADABLE_ROOT
    if not stat.S_ISDIR(info.st_mode):
        return CONDITION_MISSING_ROOT
    if not os.access(root, os.R_OK | os.X_OK):
        return CONDITION_UNREADABLE_ROOT
    return None


def preflight(path: Path | None = None) -> RuntimePreflight:
    """Classify the deployment-local runtime state before normal MCP use.

    This is the single preflight classifier that the MCP status boundary,
    library-dependent MCP tools, and session-start diagnostics all consume,
    so the runtime-config/root-layer condition is never re-derived or
    duplicated by a caller.
    """
    resolved_path = path or CONFIG_PATH
    config_path = str(resolved_path)
    environment_value = os.environ.get(FIELDS["library_root"])
    if environment_value is not None:
        library_root_value: str | None = environment_value
        config_source: str | None = "environment"
    else:
        try:
            values = load_runtime_config(resolved_path)
        except RuntimeConfigUnreadable:
            return RuntimePreflight(
                CONDITION_UNREADABLE_CONFIG,
                False,
                config_path,
                None,
                None,
                REMEDIATION[CONDITION_UNREADABLE_CONFIG],
            )
        except RuntimeConfigError:
            return RuntimePreflight(
                CONDITION_MALFORMED_CONFIG,
                False,
                config_path,
                None,
                None,
                REMEDIATION[CONDITION_MALFORMED_CONFIG],
            )
        library_root_value = values.get("library_root")
        config_source = config_path if library_root_value is not None else None

    if library_root_value is None:
        return RuntimePreflight(
            CONDITION_MISSING_CONFIG,
            False,
            config_path,
            None,
            None,
            REMEDIATION[CONDITION_MISSING_CONFIG],
        )

    root = Path(library_root_value).expanduser()
    condition = _root_condition(root)
    if condition is not None:
        return RuntimePreflight(condition, False, config_path, config_source, str(root), REMEDIATION[condition])
    try:
        resolved_root = str(root.resolve())
    except OSError:
        resolved_root = str(root)
    return RuntimePreflight(CONDITION_HEALTHY, True, config_path, config_source, resolved_root, None)
