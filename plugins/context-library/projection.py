#!/usr/bin/env python3
"""Compile Context Library decisions into repository-local AGENTS.md blocks."""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import datetime as dt
import fcntl
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Mapping

from generated.core_runtime import (
    ID_RE,
    PROVENANCE_RANK,
    CanonicalParseError,
    Decision,
    parse_register,
    validate_context_policy,
)
from generated.core_runtime import (
    discover_packs as core_discover_packs,
)
from runtime_config import RuntimeConfigError, Setting, setting

CONFIG_PATH = Path(".context-library/config.json")
SIDECAR_PATH = Path(".context-library/projection.json")
MARKER_START_PREFIX = "<!-- context-library:begin"
MARKER_END = "<!-- context-library:end -->"
_LEGACY_GENERIC_BLOCK = """<!-- context-library:begin -->
## Context Library

Before project-affecting work, consult the relevant project pack and decision
register using the Context Library plugin or its shared companion repository.
Prefer explicit current decisions and preserve supersession history.
<!-- context-library:end -->
"""
SCHEMA_VERSION = 1

EXIT_OK = 0
EXIT_CHECK_FAILED = 1
EXIT_ERROR = 2

BLOCK_RE = re.compile(
    r"<!-- context-library:begin(?: -->|\n.*?-->)\n?.*?<!-- context-library:end -->\n?",
    re.DOTALL,
)


class ProjectionError(Exception):
    """An invalid source, configuration, or unsafe projection operation."""


class PolicyError(ProjectionError):
    def __init__(self, message: str, requirement: str | None = None, source: str | None = None) -> None:
        super().__init__(message)
        self.requirement = requirement
        self.source = source


class CheckError(ProjectionError):
    """A non-mutating projection check found drift or malformed artifacts."""


@dataclasses.dataclass(frozen=True)
class ContextPolicy:
    requirement: str
    project: str | None
    source: str | None


@dataclasses.dataclass(frozen=True)
class Source:
    project: str
    pack: str
    register_path: Path
    revision: str | None
    digest: str
    text: str


@dataclasses.dataclass(frozen=True)
class Config:
    project: str
    layer_scopes: dict[str, str]
    digest: str


@dataclasses.dataclass(frozen=True)
class Constraint:
    text: str
    source_ids: tuple[str, ...]
    source_provenance: str
    derivation: str
    scope: str


@dataclasses.dataclass(frozen=True)
class Compilation:
    source: Source
    config: Config
    constraints: tuple[Constraint, ...]
    excluded_context: tuple[dict[str, object], ...]
    source_decisions: tuple[dict[str, object], ...]
    blocks: dict[str, str]
    projection_digest: str


def sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def canonical_json(payload: object) -> bytes:
    return (json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


def git_repository_root(cwd: Path | None = None) -> Path | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return Path(result.stdout.strip()).resolve()


def activation_root(cwd: Path | None = None) -> Path:
    override = os.environ.get("CONTEXT_LIBRARY_PROJECT_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    root = git_repository_root(cwd)
    if root is not None:
        return root
    return (cwd or Path.cwd()).resolve()


def library_root() -> Path:
    try:
        configured = setting("library_root").value
    except RuntimeConfigError as exc:
        raise ProjectionError(str(exc)) from exc
    if not configured:
        raise ProjectionError("Context Library source is unavailable: CONTEXT_LIBRARY_ROOT is not configured")
    return Path(configured).expanduser().resolve()


def parse_decisions(text: str) -> tuple[Decision, ...]:
    try:
        return parse_register(text)
    except CanonicalParseError as exc:
        raise ProjectionError(str(exc)) from exc


def discover_packs(root: Path) -> dict[str, tuple[Path, str]]:
    return {pack.project: (pack.register_path, pack.location) for pack in core_discover_packs(root)}


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProjectionError(f"unable to read valid JSON from {path}: {exc}") from exc


def context_requirement_setting() -> Setting:
    try:
        return setting("context_requirement")
    except RuntimeConfigError as exc:
        raise ProjectionError(str(exc)) from exc


def environment_context_requirement() -> str | None:
    requirement = context_requirement_setting().value
    if requirement is not None and requirement not in {"required", "optional", "disabled"}:
        raise ProjectionError(f"invalid context requirement: {requirement}")
    return requirement


def resolution_classification(error: BaseException) -> str:
    message = str(error).lower()
    if "ambiguous" in message or "undetermined" in message:
        return "ambiguous"
    if "permission" in message or "unreadable" in message:
        return "unreadable"
    if "stale" in message or "locally modified" in message or "projection" in message:
        return "stale-projection"
    if "missing" in message or "unavailable" in message or "does not exist" in message:
        return "missing"
    return "invalid"


def _validate_declared_policy(payload: dict[str, object], path: Path) -> None:
    declares_policy = any(field in payload for field in ("schema", "schema_version", "context_requirement"))
    if not declares_policy:
        return
    try:
        validate_context_policy(payload)
    except ValueError as exc:
        declared = payload.get("context_requirement")
        requirement_hint = str(declared) if declared in {"required", "optional", "disabled"} else None
        raise PolicyError(str(exc), requirement_hint, str(path)) from exc


def resolve_context_policy(root: Path) -> ContextPolicy:
    requirement_setting = context_requirement_setting()
    environment_requirement = requirement_setting.value
    if environment_requirement == "disabled":
        return ContextPolicy(requirement="disabled", project=None, source=requirement_setting.source)
    path = root / CONFIG_PATH
    payload: dict[str, object] = {}
    if path.is_file():
        parsed = _read_json(path)
        if not isinstance(parsed, dict):
            raise ProjectionError(f"project configuration {path} must be a JSON object")
        payload = parsed
    unknown = set(payload).difference({"schema", "schema_version", "project", "context_requirement", "affected_layers"})
    if unknown:
        raise ProjectionError(f"unknown project configuration field: {sorted(unknown)[0]}")
    _validate_declared_policy(payload, path)
    raw_requirement = environment_requirement or payload.get("context_requirement")
    requirement = str(raw_requirement) if raw_requirement is not None else "undetermined"
    if requirement not in {"required", "optional", "disabled", "undetermined"}:
        raise ProjectionError(f"invalid context requirement: {requirement}")
    try:
        project_setting = setting("project")
    except RuntimeConfigError as exc:
        raise ProjectionError(str(exc)) from exc
    project_value = project_setting.value or payload.get("project")
    project = str(project_value) if project_value is not None else None
    if project is not None and not ID_RE.fullmatch(project):
        raise ProjectionError("configured project must be a stable lowercase identifier")
    source = (
        requirement_setting.source
        if environment_requirement is not None
        else str(path)
        if raw_requirement is not None
        else None
    )
    return ContextPolicy(requirement=requirement, project=project, source=source)


def resolve_project_pack(
    available: Mapping[str, tuple[Path, str]],
    project: str,
) -> tuple[Path, str] | None:
    exact = available.get(project)
    if exact is not None:
        return exact
    if len(available) == 1:
        only = next(iter(available.values()))
        if only[1] == "decision-artifacts":
            return only
    return None


def load_config(root: Path, available: Mapping[str, tuple[Path, str]]) -> Config:
    path = root / CONFIG_PATH
    payload: dict[str, object] = {}
    if path.exists():
        try:
            raw = path.read_bytes()
            parsed = json.loads(raw.decode("utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ProjectionError(f"unable to read valid project configuration {path}: {exc}") from exc
        if not isinstance(parsed, dict):
            raise ProjectionError(f"project configuration {path} must be a JSON object")
        payload = parsed
    unknown = set(payload).difference({"schema", "schema_version", "project", "context_requirement", "affected_layers"})
    if unknown:
        raise ProjectionError(f"unknown project configuration field: {sorted(unknown)[0]}")
    _validate_declared_policy(payload, path)
    try:
        project_override = setting("project").value
    except RuntimeConfigError as exc:
        raise ProjectionError(str(exc)) from exc
    configured = project_override or payload.get("project")
    projects = sorted(available)
    if configured is None:
        if not projects:
            raise ProjectionError("no Context Library project packs are available")
        raise ProjectionError(
            "project selection is ambiguous or undetermined; configure .context-library/config.json "
            "or CONTEXT_LIBRARY_PROJECT"
        )
    if not isinstance(configured, str) or not ID_RE.fullmatch(configured):
        raise ProjectionError("configured project must be a stable lowercase identifier")
    if resolve_project_pack(available, configured) is None:
        raise ProjectionError(f"configured project pack is unavailable: {configured}")
    mappings = payload.get("affected_layers", {})
    if not isinstance(mappings, dict):
        raise ProjectionError("configuration affected_layers must be an object")
    layer_scopes: dict[str, str] = {}
    used_scopes: set[str] = set()
    for layer, scope_value in mappings.items():
        if not isinstance(layer, str) or not layer.strip() or not isinstance(scope_value, str):
            raise ProjectionError("affected_layers mappings must use non-empty string keys and values")
        scope = scope_value.strip()
        if not scope or "\\" in scope:
            raise ProjectionError(f"affected layer {layer!r} must map to a normalized relative path")
        path_scope = Path(scope)
        normalized = path_scope.as_posix()
        if normalized != scope or path_scope.is_absolute() or ".." in path_scope.parts:
            raise ProjectionError(f"affected layer {layer!r} must map to a normalized relative path")
        if scope != "." and "." in path_scope.parts:
            raise ProjectionError(f"affected layer {layer!r} maps outside the activation root")
        try:
            (root / path_scope).resolve().relative_to(root.resolve())
        except ValueError as exc:
            raise ProjectionError(f"affected layer {layer!r} resolves outside the activation root") from exc
        if scope in used_scopes:
            raise ProjectionError(f"affected layer mapping is ambiguous at scope {scope!r}")
        used_scopes.add(scope)
        layer_scopes[layer.strip()] = scope
    digest_payload = {"project": configured, "affected_layers": dict(sorted(layer_scopes.items()))}
    return Config(project=configured, layer_scopes=layer_scopes, digest=sha256(canonical_json(digest_payload)))


def load_source(root: Path, project: str, pack: tuple[Path, str]) -> Source:
    register_path, pack_name = pack
    try:
        raw = register_path.read_bytes()
        text = raw.decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise ProjectionError(f"decision source is unavailable: {register_path}: {exc}") from exc
    revision: str | None = None
    try:
        relative_register = register_path.resolve().relative_to(root.resolve()).as_posix()
        result = subprocess.run(
            ["git", "rev-parse", f"HEAD:{relative_register}"], cwd=root, check=True, capture_output=True, text=True
        )
        revision = result.stdout.strip() or None
    except (OSError, ValueError, subprocess.CalledProcessError):
        pass
    return Source(project, pack_name, register_path, revision, sha256(raw), text)


def _effective_provenances(by_id: dict[str, Decision]) -> dict[str, str]:
    result: dict[str, str] = {}
    visiting: set[str] = set()

    def resolve(decision_id: str) -> str:
        if decision_id in result:
            return result[decision_id]
        if decision_id in visiting:
            raise ProjectionError(f"synthesis provenance cycle includes {decision_id}")
        visiting.add(decision_id)
        decision = by_id[decision_id]
        provenances = [decision.provenance]
        if decision.derivation == "synthesized":
            provenances.extend(resolve(source_id) for source_id in decision.source_ids)
        visiting.remove(decision_id)
        result[decision_id] = min(provenances, key=PROVENANCE_RANK.__getitem__)
        return result[decision_id]

    for identifier in by_id:
        resolve(identifier)
    return result


def _expanded_source_ids(by_id: dict[str, Decision]) -> dict[str, tuple[str, ...]]:
    result: dict[str, tuple[str, ...]] = {}

    def expand(decision_id: str) -> tuple[str, ...]:
        if decision_id in result:
            return result[decision_id]
        decision = by_id[decision_id]
        if decision.derivation != "synthesized":
            result[decision_id] = (decision_id,)
            return result[decision_id]
        identifiers: set[str] = set(decision.source_ids)
        for source_id in decision.source_ids:
            identifiers.update(expand(source_id))
        result[decision_id] = tuple(sorted(identifiers))
        return result[decision_id]

    for identifier in by_id:
        expand(identifier)
    return result


def _reject_supersession_cycles(decisions: tuple[Decision, ...]) -> None:
    graph = {decision.decision_id: decision.supersedes for decision in decisions}
    visited: set[str] = set()
    visiting: set[str] = set()

    def visit(identifier: str) -> None:
        if identifier in visited:
            return
        if identifier in visiting:
            raise ProjectionError(f"supersession cycle includes {identifier}")
        visiting.add(identifier)
        for superseded in graph[identifier]:
            visit(superseded)
        visiting.remove(identifier)
        visited.add(identifier)

    for identifier in graph:
        visit(identifier)


def _scope_for(decision: Decision, config: Config) -> tuple[str, ...]:
    if not decision.affected_layers:
        return (".",)
    scopes: list[str] = []
    for layer in decision.affected_layers:
        scope = config.layer_scopes.get(layer)
        if scope is None:
            return ()
        scopes.append(scope)
    return tuple(sorted(set(scopes)))


def compile_projection(source: Source, config: Config, *, automatic: bool = False) -> Compilation:
    decisions = parse_decisions(source.text)
    by_id = {decision.decision_id: decision for decision in decisions}
    effective_provenance = _effective_provenances(by_id)
    expanded_source_ids = _expanded_source_ids(by_id)
    _reject_supersession_cycles(decisions)
    superseded_by: dict[str, list[str]] = {}
    for decision in decisions:
        if effective_provenance[decision.decision_id] != "explicit":
            continue
        for old_id in decision.supersedes:
            superseded_by.setdefault(old_id, []).append(decision.decision_id)
    active = [decision for decision in decisions if decision.decision_id not in superseded_by]
    active_ids = {decision.decision_id for decision in active}
    conflicted_ids: set[str] = set()
    if automatic:
        # Automatic projection is deliberately conservative.  A universal
        # decision that conflicts with scoped guidance is not safe to place in
        # the always-active AGENTS.md hot path.
        for decision in active:
            overlap = active_ids.intersection(decision.conflicts_with)
            conflicted_ids.update(overlap)
            if overlap:
                conflicted_ids.add(decision.decision_id)
        conflict_groups: dict[str, list[str]] = {}
        for decision in active:
            if decision.conflict_key:
                conflict_groups.setdefault(decision.conflict_key, []).append(decision.decision_id)
        for identifiers in conflict_groups.values():
            if len(identifiers) > 1:
                conflicted_ids.update(identifiers)
    else:
        for decision in active:
            if effective_provenance[decision.decision_id] != "explicit" or decision.applies_when:
                continue
            decision_scopes = set(_scope_for(decision, config))
            overlap = {
                other_id
                for other_id in active_ids.intersection(decision.conflicts_with)
                if not by_id[other_id].applies_when
                and decision_scopes.intersection(_scope_for(by_id[other_id], config))
            }
            if overlap:
                raise ProjectionError(f"conflicting active decisions: {decision.decision_id} and {sorted(overlap)[0]}")
    conflict_keys: dict[tuple[str, str], tuple[str, str]] = {}
    constraints: list[Constraint] = []
    excluded: list[dict[str, object]] = []
    for decision in decisions:
        effective = effective_provenance[decision.decision_id]
        scopes = _scope_for(decision, config)
        reason: str | None = None
        if decision.decision_id in superseded_by:
            reason = "superseded"
        elif effective != "explicit":
            reason = "non-authoritative"
        elif decision.applies_when:
            reason = "unevaluated-applicability"
        elif automatic and decision.affected_layers:
            reason = "scoped"
        elif automatic and decision.decision_id in conflicted_ids:
            reason = "conflicted"
        elif not scopes:
            reason = "unmapped-affected-layer"
        if reason:
            for text in decision.constraints:
                item: dict[str, object] = {
                    "text": text,
                    "record_id": decision.decision_id,
                    "source_ids": list(expanded_source_ids[decision.decision_id]),
                    "source_provenance": effective,
                    "derivation": decision.derivation,
                    "reason": reason,
                }
                if decision.provenance != effective:
                    item["declared_provenance"] = decision.provenance
                if decision.affected_layers:
                    item["affected_layers"] = list(decision.affected_layers)
                if decision.confidence:
                    item["confidence"] = decision.confidence
                if decision.review:
                    item["review"] = decision.review
                if decision.decision_id in superseded_by:
                    item["superseded_by"] = sorted(superseded_by[decision.decision_id])
                excluded.append(item)
            continue
        for scope in scopes:
            for text in decision.constraints:
                if decision.conflict_key:
                    key = (scope, decision.conflict_key)
                    prior = conflict_keys.get(key)
                    if prior and prior[0] != text:
                        raise ProjectionError(
                            f"conflicting active decisions for {decision.conflict_key!r}: "
                            f"{prior[1]} and {decision.decision_id}"
                        )
                    conflict_keys[key] = (text, decision.decision_id)
                constraints.append(
                    Constraint(
                        text,
                        expanded_source_ids[decision.decision_id],
                        effective,
                        decision.derivation,
                        scope,
                    )
                )
    combined: dict[tuple[str, str], Constraint] = {}
    for constraint in constraints:
        key = (constraint.scope, constraint.text)
        prior = combined.get(key)
        if prior is None:
            combined[key] = constraint
            continue
        source_ids = tuple(sorted(set((*prior.source_ids, *constraint.source_ids))))
        combined[key] = Constraint(
            constraint.text,
            source_ids,
            min((prior.source_provenance, constraint.source_provenance), key=PROVENANCE_RANK.__getitem__),
            "synthesized",
            constraint.scope,
        )
    ordered = tuple(sorted(combined.values(), key=lambda item: (item.scope, item.source_ids, item.text)))
    source_decisions: list[dict[str, object]] = []
    for decision in decisions:
        item: dict[str, object] = {
            "id": decision.decision_id,
            "provenance": decision.provenance,
            "effective_provenance": effective_provenance[decision.decision_id],
            "derivation": decision.derivation,
            "source_ids": list(expanded_source_ids[decision.decision_id]),
            "supersedes": list(decision.supersedes),
            "affected_layers": list(decision.affected_layers),
        }
        if decision.applies_when:
            item["applies_when"] = decision.applies_when
        if decision.derivation == "synthesized":
            item["declared_source_ids"] = list(decision.source_ids)
        if decision.confidence:
            item["confidence"] = decision.confidence
        if decision.review:
            item["review"] = decision.review
        if decision.metadata:
            item["metadata"] = decision.metadata
        source_decisions.append(item)
    projection_payload = [dataclasses.asdict(item) for item in ordered]
    projection_digest = sha256(canonical_json(projection_payload))
    blocks: dict[str, str] = {}
    for scope in sorted({item.scope for item in ordered} or {"."}):
        scoped = [item for item in ordered if item.scope == scope]
        blocks[scope] = render_block(source, projection_digest, scoped)
    return Compilation(
        source,
        config,
        ordered,
        tuple(sorted(excluded, key=lambda item: (str(item["record_id"]), str(item["text"])))),
        tuple(source_decisions),
        blocks,
        projection_digest,
    )


def render_block(source: Source, projection_digest: str, constraints: list[Constraint]) -> str:
    lines = [
        "<!-- context-library:begin",
        f"project: {source.project}",
        f"source-digest: {source.digest}",
        f"projection-digest: {projection_digest}",
        "-->",
        "## Project Constraints",
        "",
    ]
    for constraint in constraints:
        labels = ", ".join(constraint.source_ids)
        lines.append(f"- `[{labels}]` {constraint.text}")
    lines.append(MARKER_END)
    return "\n".join(lines) + "\n"


def _mask_fenced_code(text: str) -> str:
    masked: list[str] = []
    fence_character: str | None = None
    fence_length = 0
    for line in text.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        opening = re.match(r"^[ \t]{0,3}(`{3,}|~{3,})", content) if fence_character is None else None
        closing = (
            re.match(rf"^[ \t]{{0,3}}{re.escape(fence_character)}{{{fence_length},}}[ \t]*$", content)
            if fence_character is not None
            else None
        )
        inside = fence_character is not None or opening is not None
        masked.append("".join(character if character in "\r\n" else " " for character in line) if inside else line)
        if opening is not None:
            fence_character = opening.group(1)[0]
            fence_length = len(opening.group(1))
        elif closing is not None:
            fence_character = None
            fence_length = 0
    return "".join(masked)


def _blocks(text: str, path: Path) -> list[re.Match[str]]:
    visible = _mask_fenced_code(text)
    starts = visible.count(MARKER_START_PREFIX)
    ends = visible.count(MARKER_END)
    visible_matches = list(BLOCK_RE.finditer(visible))
    matches = [BLOCK_RE.match(text, match.start()) for match in visible_matches]
    if any(match is None for match in matches):
        raise ProjectionError(f"malformed Context Library managed block in {path}")
    matches = [match for match in matches if match is not None]
    if starts != ends or starts != len(matches) or starts > 1:
        raise ProjectionError(f"malformed Context Library managed block in {path}")
    return matches


def extract_block(text: str, path: Path) -> str | None:
    matches = _blocks(text, path)
    return matches[0].group(0) if matches else None


def replace_block(text: str, path: Path, block: str | None) -> str:
    matches = _blocks(text, path)
    if matches:
        match = matches[0]
        replacement = block or ""
        return text[: match.start()] + replacement + text[match.end() :]
    if block is None:
        return text
    separator = "" if not text else ("\n" if text.endswith("\n") else "\n\n")
    return text + separator + block


def _read_sidecar(root: Path, required: bool) -> dict[str, object] | None:
    path = root / SIDECAR_PATH
    try:
        payload = _read_json(path)
    except FileNotFoundError:
        if required:
            raise CheckError(f"projection sidecar is missing: {path}")
        return None
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
        error = CheckError if required else ProjectionError
        raise error(f"projection sidecar is malformed or has an unsupported schema: {path}")
    return payload


def _artifact_map(sidecar: dict[str, object] | None) -> dict[str, dict[str, object]]:
    if sidecar is None:
        return {}
    artifacts = sidecar.get("artifacts")
    if not isinstance(artifacts, list):
        raise ProjectionError("projection sidecar artifacts must be an array")
    result: dict[str, dict[str, object]] = {}
    for item in artifacts:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("scope"), str)
            or not isinstance(item.get("path"), str)
            or not isinstance(item.get("block_digest"), str)
        ):
            raise ProjectionError("projection sidecar contains a malformed artifact")
        scope = item["scope"]
        scope_path = Path(scope)
        if scope != "." and (scope_path.is_absolute() or ".." in scope_path.parts or str(scope_path) != scope):
            raise ProjectionError("projection sidecar contains an unsafe artifact scope")
        if scope in result:
            raise ProjectionError(f"projection sidecar repeats artifact scope {scope!r}")
        expected_path = str(_agents_path(Path("."), scope))
        if item["path"] != expected_path:
            raise ProjectionError(f"projection sidecar has an inconsistent path for scope {scope!r}")
        result[scope] = item
    return result


def _agents_path(root: Path, scope: str) -> Path:
    return root / ("AGENTS.md" if scope == "." else Path(scope) / "AGENTS.md")


def _assert_safe_target(root: Path, path: Path) -> None:
    root = root.resolve()
    try:
        relative = path.relative_to(root)
        path.resolve().relative_to(root)
    except ValueError as exc:
        raise ProjectionError(f"projection target resolves outside the activation root: {path}") from exc
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise ProjectionError(f"projection target contains a symbolic link: {current}")


def _read_agents(path: Path) -> str:
    try:
        return path.read_bytes().decode("utf-8") if path.exists() else ""
    except (OSError, UnicodeError) as exc:
        raise ProjectionError(f"unable to read UTF-8 AGENTS.md content from {path}: {exc}") from exc


def _preflight_local_edits(root: Path, sidecar: dict[str, object] | None, target_scopes: set[str]) -> None:
    artifacts = _artifact_map(sidecar)
    scopes = target_scopes | set(artifacts)
    for scope in scopes:
        path = _agents_path(root, scope)
        _assert_safe_target(root, path)
        text = _read_agents(path)
        block = extract_block(text, path)
        prior = artifacts.get(scope)
        if prior is not None:
            expected_digest = prior.get("block_digest")
            if not path.exists():
                continue
            if (
                not isinstance(expected_digest, str)
                or block is None
                or sha256(block.encode("utf-8")) != expected_digest
            ):
                raise ProjectionError(
                    f"refusing to overwrite locally modified generated block: {path}; "
                    "restore the generated block or remove the projection sidecar to rebuild it"
                )
        elif block is not None:
            raise ProjectionError(f"refusing to overwrite unmanaged or locally modified block: {path}")


def _generated_at(existing: dict[str, object] | None, compilation: Compilation) -> str:
    if existing:
        source = existing.get("source")
        if (
            isinstance(source, dict)
            and source.get("digest") == compilation.source.digest
            and existing.get("projection_digest") == compilation.projection_digest
            and existing.get("config_digest") == compilation.config.digest
            and isinstance(existing.get("generated_at"), str)
        ):
            return str(existing["generated_at"])
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def build_sidecar(compilation: Compilation, generated_at: str) -> dict[str, object]:
    artifacts = [
        {
            "scope": scope,
            "path": str(_agents_path(Path("."), scope)),
            "block_digest": sha256(block.encode("utf-8")),
        }
        for scope, block in sorted(compilation.blocks.items())
    ]
    constraints = [dataclasses.asdict(item) | {"source_ids": list(item.source_ids)} for item in compilation.constraints]
    return {
        "schema_version": SCHEMA_VERSION,
        "project": compilation.source.project,
        "source": {
            "pack": compilation.source.pack,
            "revision": compilation.source.revision,
            "digest": compilation.source.digest,
        },
        "config_digest": compilation.config.digest,
        "generated_at": generated_at,
        "projection_digest": compilation.projection_digest,
        "constraints": constraints,
        "excluded_context": json.loads(json.dumps(compilation.excluded_context)),
        "source_decisions": json.loads(json.dumps(compilation.source_decisions)),
        "artifacts": artifacts,
    }


def _atomic_write(root: Path, path: Path, data: bytes) -> None:
    _assert_safe_target(root, path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
            os.fchmod(stream.fileno(), mode)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        _assert_safe_target(root, path)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _apply_transaction(root: Path, changes: dict[Path, bytes | None]) -> bool:
    original: dict[Path, bytes | None] = {}
    pending: dict[Path, bytes | None] = {}
    for path, data in changes.items():
        _assert_safe_target(root, path)
        prior = path.read_bytes() if path.exists() else None
        original[path] = prior
        if prior != data:
            pending[path] = data
    if not pending:
        return False
    written: list[Path] = []
    try:
        for path, data in pending.items():
            if data is None:
                path.unlink(missing_ok=True)
            else:
                _atomic_write(root, path, data)
            written.append(path)
    except Exception as original_error:
        restored: list[Path] = []
        unrestored: list[tuple[Path, Exception]] = []
        for path in reversed(written):
            prior = original[path]
            try:
                if prior is None:
                    path.unlink(missing_ok=True)
                else:
                    _atomic_write(root, path, prior)
                restored.append(path)
            except Exception as rollback_error:
                unrestored.append((path, rollback_error))
        if unrestored:
            updated = ", ".join(str(path) for path in written)
            restored_text = ", ".join(str(path) for path in restored) or "none"
            unrestored_text = ", ".join(f"{path} ({error})" for path, error in unrestored)
            raise ProjectionError(
                f"projection transaction failed ({original_error}); updated paths: {updated}; "
                f"restored paths: {restored_text}; unrestored paths: {unrestored_text}"
            ) from original_error
        raise
    return True


@contextlib.contextmanager
def _projection_lock(root: Path):
    identifier = hashlib.sha256(str(root.resolve()).encode("utf-8")).hexdigest()
    lock_path = Path(tempfile.gettempdir()) / f"context-library-projection-{identifier}.lock"
    with lock_path.open("a+b") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def prepare(root: Path, *, automatic: bool = False) -> Compilation:
    if not root.is_dir():
        raise ProjectionError(f"activation root is unavailable: {root}")
    requirement = resolve_context_policy(root).requirement
    if requirement == "disabled":
        raise ProjectionError("consumer projection is disabled by explicit context policy")
    if requirement == "undetermined":
        raise ProjectionError("consumer projection requires an explicit required or optional context policy")
    source_root = library_root()
    resolved_root = root.resolve()
    if resolved_root == source_root or source_root in resolved_root.parents or resolved_root in source_root.parents:
        raise ProjectionError("consumer activation root must be outside the canonical Context Library root")
    if not source_root.is_dir():
        raise ProjectionError(f"Context Library source is unavailable: {source_root}")
    packs = discover_packs(source_root)
    config = load_config(root, packs)
    selected = resolve_project_pack(packs, config.project)
    if selected is None:
        raise ProjectionError(f"configured project pack is unavailable: {config.project}")
    source = load_source(source_root, config.project, selected)
    return compile_projection(source, config, automatic=automatic)


def sync(root: Path, *, automatic: bool = False) -> bool:
    with _projection_lock(root):
        compilation = prepare(root, automatic=automatic)
        existing = _read_sidecar(root, required=False)
        target_scopes = set(compilation.blocks)
        prior_artifacts = _artifact_map(existing)
        scopes = target_scopes | set(prior_artifacts)
        root_agents = _agents_path(root, ".")
        if "." not in scopes and extract_block(_read_agents(root_agents), root_agents) is not None:
            scopes.add(".")
        _preflight_local_edits(root, existing, scopes)
        changes: dict[Path, bytes | None] = {}
        for scope in sorted(scopes):
            path = _agents_path(root, scope)
            _assert_safe_target(root, path)
            text = _read_agents(path)
            updated = replace_block(text, path, compilation.blocks.get(scope))
            changes[path] = None if not updated and scope not in target_scopes else updated.encode("utf-8")
        sidecar = build_sidecar(compilation, _generated_at(existing, compilation))
        changes[root / SIDECAR_PATH] = (
            json.dumps(sidecar, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        ).encode("utf-8")
        return _apply_transaction(root, changes)


def _validate_sidecar(sidecar: dict[str, object], compilation: Compilation) -> None:
    generated_at = sidecar.get("generated_at")
    if not isinstance(generated_at, str):
        raise CheckError("projection sidecar has a malformed generation time")
    try:
        parsed_time = dt.datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CheckError("projection sidecar has a malformed generation time") from exc
    if parsed_time.tzinfo is None:
        raise CheckError("projection sidecar generation time must include a timezone")
    expected = build_sidecar(compilation, generated_at)
    if sidecar != expected:
        raise CheckError("projection sidecar is stale, malformed, or inconsistent with the source")


def check(root: Path, *, automatic: bool = False) -> None:
    compilation = prepare(root, automatic=automatic)
    try:
        sidecar = _read_sidecar(root, required=True)
        assert sidecar is not None
        _validate_sidecar(sidecar, compilation)
        artifacts = _artifact_map(sidecar)
        if set(artifacts) != set(compilation.blocks):
            raise CheckError("projection artifact scopes do not match the current compilation")
        for scope, expected_block in compilation.blocks.items():
            path = _agents_path(root, scope)
            _assert_safe_target(root, path)
            if not path.exists():
                raise CheckError(f"projected AGENTS.md is missing: {path}")
            text = _read_agents(path)
            actual = extract_block(text, path)
            if actual != expected_block:
                raise CheckError(f"projected AGENTS.md is locally edited or stale: {path}")
        if "." not in artifacts:
            root_agents = _agents_path(root, ".")
            if extract_block(_read_agents(root_agents), root_agents) is not None:
                raise CheckError(f"unexpected unmanaged Context Library block remains: {root_agents}")
    except ProjectionError as exc:
        if isinstance(exc, CheckError):
            raise
        raise CheckError(str(exc)) from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("sync", "check"))
    parser.add_argument(
        "--root", type=Path, help="activation root; defaults to explicit, Git, then working-directory selection"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = args.root.expanduser().resolve() if args.root else activation_root()
    try:
        if args.operation == "sync":
            changed = sync(root)
            print(f"Context Library projection {'updated' if changed else 'already current'} at {root}.")
        else:
            check(root)
            print(f"Context Library projection is current at {root}.")
    except CheckError as exc:
        print(
            f"Context Library projection check failed: {exc}. Run sync after resolving the condition.", file=sys.stderr
        )
        return EXIT_CHECK_FAILED
    except (OSError, ProjectionError) as exc:
        print(f"Context Library projection error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
