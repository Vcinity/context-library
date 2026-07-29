from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from .models import AuthorityPolicy, ProjectConfig, Topology


class ConfigError(ValueError):
    exit_code = 2


def safe_path(root: Path, target: Path, *, allow_missing: bool = False) -> Path:
    """Return a contained target after rejecting every symlinked path component."""
    root = root.expanduser().absolute()
    target = target.expanduser().absolute()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ConfigError(f"path escapes configured library root: {target}") from exc
    for ancestor in (*reversed(root.parents), root):
        if ancestor.is_symlink():
            raise ConfigError(f"symlinked library path component is unsafe: {ancestor}")
    current = root
    for part in target.relative_to(root).parts:
        current = current / part
        if current.is_symlink():
            raise ConfigError(f"symlinked path component is unsafe: {current}")
        if not current.exists() and allow_missing:
            continue
    resolved_root = root.resolve(strict=False)
    resolved_target = target.resolve(strict=False)
    try:
        resolved_target.relative_to(resolved_root)
    except ValueError as exc:
        raise ConfigError(f"path resolves outside configured library root: {target}") from exc
    return target


def resolve_config(
    library_root: Path,
    project: str | None = None,
    state_root: Path | None = None,
    actor: str | None = None,
    json_output: bool | None = None,
    auto_publish: bool | None = None,
) -> dict[str, Any]:
    env_project = os.getenv("CLM_PROJECT")
    xdg = os.getenv("XDG_STATE_HOME")
    default_state = (
        Path(xdg) / "context-library-maintainer" if xdg else Path.home() / ".local/state/context-library-maintainer"
    )
    return {
        "library_root": Path(os.path.abspath(library_root.expanduser())),
        "project": project or env_project,
        "state_root": (state_root or Path(os.getenv("CLM_STATE_ROOT", str(default_state)))).expanduser().resolve(),
        "actor": actor or os.getenv("CLM_ACTOR", "agent:unknown"),
        "json": json_output if json_output is not None else os.getenv("CLM_JSON", "false").lower() == "true",
        "auto_publish": auto_publish
        if auto_publish is not None
        else os.getenv("CLM_AUTO_PUBLISH", "false").lower() == "true",
    }


def _load_yaml(path: Path, model: type[Any]) -> Any:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ConfigError(f"unable to read {path}: {exc}") from exc
    try:
        return model.model_validate(payload)
    except Exception as exc:
        raise ConfigError(f"invalid configuration {path}: {exc}") from exc


def project_files(settings: dict[str, Any]) -> tuple[Path, ProjectConfig, Topology, AuthorityPolicy]:
    project = settings.get("project")
    if not project:
        raise ConfigError("project is required")
    root = settings["library_root"] / "projects" / project
    safe_path(settings["library_root"], root)
    config_path = root / "maintainer.yaml"
    if not config_path.is_file():
        raise ConfigError(f"missing project configuration: {config_path}")
    config = _load_yaml(config_path, ProjectConfig)
    if config.project != project or root.name != project:
        raise ConfigError("project configuration does not match project directory")
    for relative in (config.register, config.topology, config.authority):
        p = Path(relative)
        if p.is_absolute() or ".." in p.parts or p.as_posix() != relative:
            raise ConfigError(f"unsafe project-relative path: {relative}")
        safe_path(settings["library_root"], root / relative)
    topology = _load_yaml(root / config.topology, Topology)
    authority = _load_yaml(root / config.authority, AuthorityPolicy)
    if topology.project != project or authority.project != project:
        raise ConfigError("project metadata mismatch")
    return root, config, topology, authority


def scaffold(settings: dict[str, Any]) -> list[str]:
    project = settings.get("project")
    if not project:
        raise ConfigError("project is required")
    if not project.isidentifier() or not project[0].islower() or not all(c.isalnum() or c == "-" for c in project):
        raise ConfigError("invalid project identifier")
    root = settings["library_root"] / "projects" / project
    safe_path(settings["library_root"], root, allow_missing=True)
    root.mkdir(parents=True, exist_ok=True)
    files = {
        "maintainer.yaml": {
            "schema_version": 1,
            "project": project,
            "display_name": project.title(),
            "register": "decision-register.md",
            "topology": "topology.yaml",
            "authority": "authority.yaml",
            "policies": {
                "automatic_publication": False,
                "minimum_routing_confidence": 0.85,
                "human_approval_categories": [],
                "retain_source_content": True,
            },
        },
        "topology.yaml": {
            "schema_version": 1,
            "project": project,
            "layers": {
                "product": {
                    "description": "Product-wide behavior and terminology",
                    "aliases": [],
                    "repository_hints": [],
                }
            },
        },
        "authority.yaml": {
            "schema_version": 1,
            "project": project,
            "default_precedence": 0,
            "authorities": [],
            "category_owners": {},
        },
    }
    created: list[str] = []
    for name, payload in files.items():
        path = root / name
        safe_path(settings["library_root"], path, allow_missing=True)
        if not path.exists():
            path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
            created.append(str(path))
    conflicts = root / "conflicts"
    safe_path(settings["library_root"], conflicts, allow_missing=True)
    if not conflicts.exists():
        conflicts.mkdir()
        created.append(str(conflicts))
    register = root / "decision-register.md"
    if not register.exists():
        register.write_text("# Decision Register\n\n", encoding="utf-8")
        created.append(str(register))
    empty_indexes = {
        "index-by-category.md": "# Decisions by Category\n",
        "index-by-date.md": "# Decisions by Date\n",
        "index-by-layer.md": "# Decisions by Layer\n",
        "supersession-index.md": "# Supersession Index\n",
    }
    for name, content in empty_indexes.items():
        path = root / name
        if not path.exists():
            path.write_text(content, encoding="utf-8")
            created.append(str(path))
    return created
