"""Dependency-light shared project-pack relationships and effective views."""

from __future__ import annotations

import dataclasses
import hashlib
from pathlib import Path
from typing import Any

import yaml

from .canonical import Decision, ProjectPack, discover_packs, parse_register, resolve_pack


class SharedContextError(ValueError):
    """A shared-context relationship or effective view is unsafe to use."""


@dataclasses.dataclass(frozen=True)
class SharedContextParent:
    project: str
    required: bool = True
    order: int = 0


@dataclasses.dataclass(frozen=True)
class SharedContextRelationships:
    project: str
    revision: str
    parents: tuple[SharedContextParent, ...]


@dataclasses.dataclass(frozen=True)
class EffectiveDecision:
    decision: Decision
    source_project: str
    source_scope: str
    source_digest: str


@dataclasses.dataclass(frozen=True)
class EffectiveView:
    project: str
    records: tuple[EffectiveDecision, ...]
    source_digests: tuple[tuple[str, str], ...]
    relationship_revision: str | None = None


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SharedContextError(f"{label} must be a mapping")
    return value


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or any(
        character not in "abcdefghijklmnopqrstuvwxyz0123456789._-" for character in value
    ) or not value[0].islower() or value[0] in ".-_":
        raise SharedContextError(f"{label} must be a normalized project identifier")
    return value


def load_relationships(path: Path, project: str) -> SharedContextRelationships:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise SharedContextError(f"unable to read shared-context relationships: {path}") from exc
    data = _mapping(payload, "shared-context relationships")
    unknown = set(data) - {"schema", "schema_version", "project", "revision", "parents"}
    if unknown:
        raise SharedContextError(f"unknown shared-context relationship fields: {sorted(unknown)}")
    if data.get("schema") != "context-library/shared-context-relationships" or data.get("schema_version") != 1:
        raise SharedContextError("unsupported shared-context relationship schema")
    if data.get("project") != project:
        raise SharedContextError("shared-context relationship project does not match its pack")
    revision = data.get("revision")
    if not isinstance(revision, str) or not revision.strip():
        raise SharedContextError("shared-context relationship revision is required")
    raw_parents = data.get("parents", [])
    if not isinstance(raw_parents, list):
        raise SharedContextError("shared-context parents must be a list")
    parents: list[SharedContextParent] = []
    seen: set[str] = set()
    for raw in raw_parents:
        item = _mapping(raw, "shared-context parent")
        unknown = set(item) - {"project", "required", "order"}
        if unknown:
            raise SharedContextError(f"unknown shared-context parent fields: {sorted(unknown)}")
        parent = _identifier(item.get("project"), "shared-context parent project")
        if parent in seen or parent == project:
            raise SharedContextError(f"duplicate or self-referential shared-context parent: {parent}")
        seen.add(parent)
        required = item.get("required", True)
        order = item.get("order", 0)
        if not isinstance(required, bool) or not isinstance(order, int) or isinstance(order, bool):
            raise SharedContextError(f"invalid relationship defaults for parent {parent}")
        parents.append(SharedContextParent(parent, required, order))
    ordered = tuple(sorted(parents, key=lambda parent: (parent.order, parent.project)))
    return SharedContextRelationships(project, revision.strip(), ordered)


def _pack_root(pack: ProjectPack) -> Path:
    return pack.register_path.parent


def _source(pack: ProjectPack) -> tuple[tuple[Decision, ...], str]:
    try:
        content = pack.register_path.read_bytes()
        decisions = parse_register(content.decode("utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise SharedContextError(f"invalid decision register for {pack.project}: {exc}") from exc
    return decisions, hashlib.sha256(content).hexdigest()


def _authorized(pack: ProjectPack, consumer: str) -> bool:
    authority = _pack_root(pack) / "authority.yaml"
    if not authority.is_file():
        return False
    try:
        payload = _mapping(yaml.safe_load(authority.read_text(encoding="utf-8")), "authority")
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise SharedContextError(f"unable to read authority for {pack.project}") from exc
    if payload.get("project", pack.project) != pack.project:
        raise SharedContextError(f"authority project does not match pack {pack.project}")
    consumers = payload.get("shared_context_consumers", [])
    if not isinstance(consumers, list) or any(not isinstance(item, str) for item in consumers):
        raise SharedContextError(f"invalid shared_context_consumers for {pack.project}")
    return consumer in consumers


def resolve_effective_view(library_root: Path, project: str) -> EffectiveView:
    """Resolve explicit relationships into a deterministic, read-only view."""
    packs = discover_packs(library_root, include_incomplete=True)
    root_pack = resolve_pack(packs, project)
    if root_pack is None:
        raise SharedContextError(f"project pack is unavailable or ambiguous: {project}")
    by_project = {pack.project: pack for pack in packs}
    seen: dict[str, EffectiveDecision] = {}
    source_digests: dict[str, str] = {}
    visiting: list[str] = []
    relationship_revision: str | None = None

    def visit(pack: ProjectPack, ultimate_consumer: str, is_root: bool = False) -> None:
        nonlocal relationship_revision
        if pack.project in visiting:
            cycle = " -> ".join((*visiting, pack.project))
            raise SharedContextError(f"shared-context relationship cycle: {cycle}")
        visiting.append(pack.project)
        decisions, source_digest = _source(pack)
        source_digests[pack.project] = source_digest
        artifact = _pack_root(pack) / "shared-context-relationships.yaml"
        relationships = load_relationships(artifact, pack.project) if artifact.is_file() else None
        if is_root:
            relationship_revision = relationships.revision if relationships else None
        if relationships:
            for parent in relationships.parents:
                parent_pack = by_project.get(parent.project)
                if parent_pack is None or not parent_pack.register_path.is_file():
                    if parent.required:
                        raise SharedContextError(f"required shared-context parent is missing: {parent.project}")
                    continue
                if not _authorized(parent_pack, ultimate_consumer):
                    raise SharedContextError(
                        f"shared-context parent {parent.project} does not authorize {ultimate_consumer}"
                    )
                visit(parent_pack, ultimate_consumer)
        for decision in decisions:
            candidate = EffectiveDecision(decision, pack.project, pack.location, source_digest)
            prior = seen.get(decision.decision_id)
            if prior is not None:
                if prior.decision != decision:
                    raise SharedContextError(f"conflicting duplicate decision identity: {decision.decision_id}")
                continue
            seen[decision.decision_id] = candidate
        visiting.pop()

    visit(root_pack, project, True)
    return EffectiveView(
        project=project,
        records=tuple(seen.values()),
        source_digests=tuple(sorted(source_digests.items())),
        relationship_revision=relationship_revision,
    )
