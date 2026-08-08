"""Resolve deployment-time Plugin settings with environment overrides."""

from __future__ import annotations

import json
import os
import re
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


class RuntimeConfigError(ValueError):
    """The bundled Plugin runtime configuration is malformed."""


@dataclass(frozen=True)
class Setting:
    value: str | None
    source: str | None


def load_runtime_config(path: Path = CONFIG_PATH) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeConfigError(f"unable to read valid Plugin runtime configuration {path}: {exc}") from exc
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
