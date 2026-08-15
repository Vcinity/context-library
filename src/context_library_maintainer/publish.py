from __future__ import annotations

import base64

# ruff: noqa: E501
import hashlib
import json
import os
import re
import subprocess
import tempfile
from contextlib import nullcontext
from pathlib import Path
from typing import Any

from .config import project_files, safe_path
from .models import Candidate, Observation, timestamp
from .state import State


class PublicationError(RuntimeError):
    exit_code = 3

    def __init__(
        self,
        message: str,
        *,
        restored: list[str] | None = None,
        unrestored: list[str] | None = None,
        stage: str | None = None,
    ):
        super().__init__(message)
        self.restored = restored or []
        self.unrestored = unrestored or []
        self.stage = stage


class PublicationLockedError(PublicationError):
    exit_code = 4


class PublicationSafetyError(PublicationError):
    exit_code = 2


class PublicationRecoveryDivergedError(PublicationSafetyError):
    pass


def _git(args: list[str], root: Path, *, timeout: int = 30, capture: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=capture,
        text=True,
        timeout=timeout,
    )


def _recovery_path(state: State, project: str) -> Path:
    return state.root / f"{project}.publication-recovery.json"


def _write_recovery(
    state: State,
    settings: dict[str, Any],
    project: str,
    originals: dict[Path, bytes | None],
    original_head: str | None,
    replacements: dict[Path, bytes] | None = None,
    publication: dict[str, Any] | None = None,
) -> Path:
    path = _recovery_path(state, project)
    replacements = replacements or {}
    payload = {
        "schema": "context-library/publication-recovery",
        "schema_version": 3,
        "library_root": str(settings["library_root"]),
        "original_head": original_head,
        "publication": publication or {},
        "targets": {
            str(target.relative_to(settings["library_root"])): {
                "original": None if content is None else base64.b64encode(content).decode("ascii"),
                "original_sha256": _content_digest(content),
                "staged_sha256": _content_digest(replacements.get(target)),
            }
            for target, content in originals.items()
        },
    }
    _write_recovery_payload(path, payload)
    return path


def _write_recovery_payload(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(".tmp")
    encoded = json.dumps(payload, sort_keys=True).encode("utf-8")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _record_expected_commit(path: Path, original_head: str, committed_head: str) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if (
        payload.get("schema") != "context-library/publication-recovery"
        or payload.get("schema_version") != 3
        or payload.get("original_head") != original_head
    ):
        raise PublicationSafetyError("publication recovery record changed before commit")
    payload["expected_committed_head"] = committed_head
    _write_recovery_payload(path, payload)


def _remove_recovery(path: Path) -> None:
    path.unlink(missing_ok=True)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _content_digest(content: bytes | None) -> str | None:
    return None if content is None else hashlib.sha256(content).hexdigest()


def _path_digest(path: Path) -> str | None:
    return _content_digest(path.read_bytes()) if path.exists() else None


def _git_status_paths(root: Path) -> set[str]:
    output = _git(["status", "--porcelain=v1", "-z", "--untracked-files=all"], root).stdout
    entries = output.split("\0")
    paths: set[str] = set()
    index = 0
    while index < len(entries):
        entry = entries[index]
        index += 1
        if not entry:
            continue
        status = entry[:2]
        paths.add(entry[3:])
        if "R" in status or "C" in status:
            if index < len(entries) and entries[index]:
                paths.add(entries[index])
                index += 1
    return paths


def _recovery_divergence(
    root: Path,
    payload: dict[str, Any],
) -> list[str]:
    targets = payload.get("targets")
    if payload.get("schema_version") not in {2, 3} or not isinstance(targets, dict):
        return ["recovery-record:schema-version"]
    divergent: list[str] = []
    target_paths = set(targets)
    if _git_root(root):
        expected_head = payload.get("original_head")
        current_head = _git(["rev-parse", "HEAD"], root).stdout.strip()
        expected_committed_head = payload.get("expected_committed_head")
        if expected_head and current_head not in {expected_head, expected_committed_head}:
            divergent.append(".git/HEAD")
        divergent.extend(sorted(_git_status_paths(root) - target_paths))
    for relative, record in targets.items():
        if not isinstance(record, dict):
            divergent.append(relative)
            continue
        target = safe_path(root, root / relative, allow_missing=True)
        current = _path_digest(target)
        if current not in {record.get("original_sha256"), record.get("staged_sha256")}:
            divergent.append(relative)
    return sorted(set(divergent))


def _recover_committed_publication(
    state: State,
    payload: dict[str, Any],
    project: str,
) -> dict[str, Any]:
    publication = payload.get("publication")
    if not isinstance(publication, dict):
        raise PublicationRecoveryDivergedError(
            "committed publication recovery metadata is missing",
            unrestored=["recovery-record:publication"],
            stage="recovery-validation",
        )
    candidate_ids = publication.get("candidate_ids")
    conflict_ids = publication.get("conflict_ids")
    digest = publication.get("digest")
    if (
        not isinstance(candidate_ids, list)
        or not all(isinstance(item, str) and item for item in candidate_ids)
        or not isinstance(conflict_ids, list)
        or not all(isinstance(item, str) and item for item in conflict_ids)
        or not isinstance(digest, str)
        or not digest
    ):
        raise PublicationRecoveryDivergedError(
            "committed publication recovery metadata is invalid",
            unrestored=["recovery-record:publication"],
            stage="recovery-validation",
        )
    rows = {
        row["id"]: row["state"]
        for row in state.db.execute(
            "SELECT id, state FROM candidates WHERE project=?",
            (project,),
        )
        if row["id"] in candidate_ids
    }
    invalid = [item for item in candidate_ids if rows.get(item) not in {"ready", "published"}]
    if invalid:
        raise PublicationRecoveryDivergedError(
            "candidate state diverged from the committed publication: " + ", ".join(invalid),
            unrestored=[f"candidate:{item}" for item in invalid],
            stage="recovery-validation",
        )
    with state.transaction():
        for candidate_id in candidate_ids:
            if rows[candidate_id] == "ready":
                state.transition(candidate_id, "published")
        existing = state.db.execute(
            "SELECT 1 FROM publications WHERE project=? AND phase='published' AND digest=? LIMIT 1",
            (project, digest),
        ).fetchone()
        if existing is None:
            state.db.execute(
                "INSERT INTO publications(project, run_id, phase, digest, payload_json, created_at) "
                "VALUES (?, ?, 'published', ?, ?, ?)",
                (
                    project,
                    str(publication.get("run_id", "")),
                    digest,
                    json.dumps(publication.get("authorization", {}), sort_keys=True),
                    timestamp(
                        __import__(
                            "context_library_maintainer.models",
                            fromlist=["now_utc"],
                        ).now_utc()
                    ),
                ),
            )
    return {
        "published": candidate_ids,
        "conflicts": conflict_ids,
        "changed": True,
        "digest": digest,
        "indexes": publication.get("indexes", []),
        "authorization": publication.get("authorization", {}),
        "recovered": True,
    }


def _recover_interrupted(
    state: State,
    settings: dict[str, Any],
    project: str,
) -> dict[str, Any] | None:
    path = _recovery_path(state, project)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PublicationRecoveryDivergedError(
            f"publication recovery record is unreadable ({type(exc).__name__})",
            unrestored=["recovery-record:unreadable"],
            stage="recovery-validation",
        ) from exc
    if payload.get("library_root") != str(settings["library_root"]):
        raise PublicationRecoveryDivergedError(
            "publication recovery root does not match configured library",
            unrestored=["recovery-record:library-root"],
            stage="recovery-validation",
        )
    root = settings["library_root"]
    divergent = _recovery_divergence(root, payload)
    if divergent:
        raise PublicationRecoveryDivergedError(
            "refusing publication recovery because library state diverged: " + ", ".join(divergent),
            unrestored=divergent,
            stage="recovery-validation",
        )
    expected_committed_head = payload.get("expected_committed_head")
    if expected_committed_head and _git_root(root):
        current_head = _git(["rev-parse", "HEAD"], root).stdout.strip()
        if current_head == expected_committed_head:
            result = _recover_committed_publication(state, payload, project)
            try:
                _remove_recovery(path)
            except OSError as exc:
                raise PublicationError(
                    f"publication state recovered but recovery record cleanup failed ({type(exc).__name__})",
                    restored=["maintainer-state"],
                    unrestored=["recovery-record"],
                    stage="recovery-cleanup",
                ) from exc
            return result
    if payload.get("original_head") and _git_root(root):
        _git(["reset", "--mixed", payload["original_head"]], root)
    restored: list[str] = []
    for relative, record in payload.get("targets", {}).items():
        target = safe_path(root, root / relative, allow_missing=True)
        current = _path_digest(target)
        if current not in {record.get("original_sha256"), record.get("staged_sha256")}:
            raise PublicationRecoveryDivergedError(
                f"publication target changed during recovery: {relative}",
                restored=restored,
                unrestored=[relative],
                stage="recovery-restore",
            )
        encoded = record.get("original")
        if encoded is None:
            target.unlink(missing_ok=True)
        else:
            temporary = target.with_name(f".{target.name}.clm-recovery")
            try:
                temporary.write_bytes(base64.b64decode(encoded))
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
        restored.append(relative)
    try:
        _remove_recovery(path)
    except OSError as exc:
        raise PublicationError(
            f"publication targets recovered but recovery record cleanup failed ({type(exc).__name__})",
            restored=restored,
            unrestored=["recovery-record"],
            stage="recovery-cleanup",
        ) from exc
    return None


def _record(candidate: Candidate, observations: list[Observation], actor: str) -> str:
    lines = [
        f'<a id="{candidate.candidate_id}"></a>',
        f"### {candidate.subject}",
        "",
        f"- Category: {candidate.category}",
        f"- Date: {timestamp(candidate.decision_at)}",
    ]
    if candidate.decisionmaker:
        lines.append(f"- Decisionmaker: {candidate.decisionmaker.display_name} <{candidate.decisionmaker.identity}>")
    else:
        lines.append(f"- Synthesized-By: {actor}")
    lines.append(f"- Decision: {candidate.decision}")
    if candidate.constraint:
        lines.append(f"- Constraint: {candidate.constraint}")
    lines.extend(
        [
            f"- Rationale: {candidate.rationale}",
            f"- Provenance: {candidate.provenance}",
            f"- Derivation: {candidate.derivation}",
        ]
    )
    routing_ok = (
        candidate.applicability.provenance in {"explicit", "inferred"} and candidate.applicability.confidence >= 0.85
    )
    if routing_ok and candidate.affected_layers and candidate.affected_layers != ["product"]:
        lines.append(f"- Affected-Layers: {', '.join(candidate.affected_layers)}")
    if candidate.applicability.provenance != "explicit" or candidate.applicability.confidence < 0.85:
        lines.extend(
            [
                f"- Suggested-Affected-Layers: {', '.join(candidate.affected_layers) or 'product'}",
                f"- Applicability-Provenance: {candidate.applicability.provenance}",
                f"- Applicability-Confidence: {candidate.applicability.confidence:.2f}",
            ]
        )
    if candidate.conflict_key:
        lines.append(f"- Conflict-Key: {candidate.conflict_key}")
    if candidate.supersedes:
        lines.append(f"- Supersedes: {', '.join(f'`{item}`' for item in candidate.supersedes)}")
    if candidate.derivation == "synthesized":
        lines.append(f"- Sources: {', '.join(f'`{item}`' for item in candidate.sources)}")
    lines.append("- Evidence:")
    for observation in observations:
        speaker = f", {observation.speaker.display_name}" if observation.speaker else ""
        occurred = f", {timestamp(observation.occurred_at)}" if observation.occurred_at else ""
        excerpt = observation.excerpt.replace('"', '\\"').replace("\n", " ")
        lines.append(f'  - `source:{observation.source_id}`{occurred}{speaker}: "{excerpt}"')
    return "\n".join(lines) + "\n\n"


def _indexes(register: str) -> dict[str, str]:
    from context_library_core.canonical import parse_register

    category: dict[str, list[str]] = {}
    dates: dict[str, list[str]] = {}
    layers: dict[str, list[str]] = {}
    superseded: list[str] = []
    decisions = parse_register(register) if "<a id=" in register else ()
    for decision in decisions:
        category.setdefault(decision.category, []).append(decision.decision_id)
        dates.setdefault(str(decision.metadata.get("date", "unknown")), []).append(decision.decision_id)
        for layer in decision.affected_layers:
            layers.setdefault(layer, []).append(decision.decision_id)
        if decision.supersedes:
            references = ", ".join(f"`{item}`" for item in decision.supersedes)
            superseded.append(f"- `{decision.decision_id}` supersedes {references}")

    def render(title: str, groups: dict[str, list[str]]) -> str:
        lines = [f"# {title}", ""]
        for key in sorted(groups):
            lines.extend([f"## {key}", *(f"- `{item}`" for item in groups[key]), ""])
        return "\n".join(lines).rstrip() + "\n"

    return {
        "index-by-category.md": render("Decisions by Category", category),
        "index-by-date.md": render("Decisions by Date", dates),
        "index-by-layer.md": render("Decisions by Layer", layers),
        "supersession-index.md": "# Supersession Index\n\n" + "\n".join(superseded) + "\n",
    }


def _git_root(path: Path) -> bool:
    return (path / ".git").exists()


def _conflict_markdown(payload: dict[str, Any]) -> str:
    lines = [
        f"# Conflict {payload['conflict_id']}",
        "",
        f"- Status: {payload.get('status', 'open')}",
        f"- Created: {payload.get('created_at', '')}",
        f"- Question: {payload.get('question', '')}",
        f"- Reason: {payload.get('reason', '')}",
        "",
        "## Choices",
        "",
    ]
    lines.extend(f"- `{item['value']}` — {item['label']}" for item in payload.get("choices", []))
    lines.extend(
        [
            "",
            f"Recommendation: `{payload.get('recommendation', '')}`",
            "",
            f"Safe behavior: {payload.get('safe_behavior', '')}",
            "",
            "## Resolution commands",
            "",
            *[
                f"`clm conflict resolve {payload['conflict_id']} --choice {item['value']} --actor <resolver>`"
                for item in payload.get("choices", [])
            ],
            "",
            "```yaml",
            "schema_version: 1",
            f"conflict_id: {payload['conflict_id']}",
            f"status: {payload.get('status', 'open')}",
            "choices:",
            *[f"  - {item['value']}" for item in payload.get("choices", [])],
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def publish(
    state: State, settings: dict[str, Any], ready_only: bool = False, no_commit: bool = False
) -> dict[str, Any]:
    project = settings["project"]
    try:
        with state.project_lock(project, settings["actor"]):
            locked_settings = {**settings, "_publication_lock_held": True}
            return _publish_locked(state, locked_settings, ready_only=ready_only, no_commit=no_commit)
    except RuntimeError as exc:
        if "project lock is held" in str(exc):
            raise PublicationLockedError("project publication lock is held") from exc
        raise


def _publish_locked(
    state: State, settings: dict[str, Any], ready_only: bool = False, no_commit: bool = False
) -> dict[str, Any]:
    root, config, _, _ = project_files(settings)
    project = settings["project"]
    recovered = _recover_interrupted(state, settings, project)
    if recovered is not None:
        return recovered
    authorized_ids = settings.get("authorized_candidate_ids")
    authorized_set = set(authorized_ids or [])
    candidates = [
        Candidate.model_validate_json(row["payload_json"])
        for row in state.candidates(project, "ready")
        if not authorized_set or row["id"] in authorized_set
    ]
    register = root / config.register
    original = register.read_bytes() if register.exists() else b"# Decision Register\n\n"
    additions = []
    for candidate in candidates:
        observations = [
            Observation.model_validate_json(row["payload_json"])
            for row in state.observations(candidate.source_observation_ids, candidate.project)
        ]
        additions.append(_record(candidate, observations, settings["actor"]))
    open_conflicts = [] if authorized_set else list(
        state.db.execute(
            "SELECT id, payload_json FROM conflicts WHERE project=? AND status='open' ORDER BY id", (project,)
        )
    )
    if not additions and not open_conflicts:
        return {"published": [], "changed": False}
    updated = original.decode("utf-8") + "".join(additions)
    try:
        from context_library_core.canonical import validate_projection_compatibility

        if "<a id=" in updated:
            validate_projection_compatibility(updated)
        indexes = _indexes(updated)
    except Exception as exc:
        raise PublicationSafetyError(
            "publication failed safely: staged project pack is incompatible with "
            f"the Plugin read runtime ({type(exc).__name__})"
        ) from exc
    try:
        lock = (
            nullcontext() if settings.get("_publication_lock_held") else state.project_lock(project, settings["actor"])
        )
        with lock:
            safe_path(settings["library_root"], root)
            safe_path(settings["library_root"], register)
            git_repository = _git_root(settings["library_root"])
            recovered = _recover_interrupted(state, settings, project)
            if recovered is not None:
                return recovered
            original_head = None
            if git_repository:
                status = _git(
                    ["status", "--porcelain", "--untracked-files=all"],
                    settings["library_root"],
                ).stdout
                if status.strip():
                    raise PublicationSafetyError("refusing publication into a dirty library worktree")
                original_head = _git(["rev-parse", "HEAD"], settings["library_root"]).stdout.strip()
            with tempfile.TemporaryDirectory(prefix="clm-stage-") as stage:
                staged = Path(stage) / project
                staged.mkdir(parents=True)
                (staged / config.register).write_text(updated, encoding="utf-8")
                if additions:
                    from context_library_core.canonical import validate_projection_compatibility

                    staged_text = (staged / config.register).read_text(encoding="utf-8")
                    validate_projection_compatibility(staged_text)
                    validator = settings.get("plugin_compatibility_validator")
                    if validator is not None:
                        validator(staged_text)
                for name, content in indexes.items():
                    (staged / name).write_text(content, encoding="utf-8")
                targets = {
                    register: (staged / config.register).read_bytes(),
                    **{root / name: (staged / name).read_bytes() for name in indexes},
                }
                conflict_dir = root / "conflicts"
                safe_path(settings["library_root"], conflict_dir, allow_missing=True)
                for conflict in open_conflicts:
                    target = conflict_dir / f"{conflict['id']}.md"
                    safe_path(settings["library_root"], target, allow_missing=True)
                    targets[target] = _conflict_markdown(__import__("json").loads(conflict["payload_json"])).encode()
                originals = {path: path.read_bytes() if path.exists() else None for path in targets}
                recovery: Path | None = None
                stage_name = "recovery-record"
                state_committed = False
                try:
                    library_digest = hashlib.sha256(targets[register]).hexdigest()
                    recovery = _write_recovery(
                        state,
                        settings,
                        project,
                        originals,
                        original_head,
                        replacements=targets,
                        publication={
                            "candidate_ids": [candidate.candidate_id for candidate in candidates],
                            "conflict_ids": [item["id"] for item in open_conflicts],
                            "digest": library_digest,
                            "indexes": sorted(indexes),
                            "run_id": settings.get("run_id", ""),
                            "authorization": settings.get("publication_metadata", {}),
                        },
                    )
                    for path, content in targets.items():
                        relative_path = str(path.relative_to(settings["library_root"]))
                        stage_name = f"replace:{relative_path}"
                        safe_path(settings["library_root"], path, allow_missing=True)
                        path.parent.mkdir(parents=True, exist_ok=True)
                        temporary = path.with_name(f".{path.name}.clm-tmp")
                        safe_path(settings["library_root"], temporary, allow_missing=True)
                        try:
                            temporary.write_bytes(content)
                            os.replace(temporary, path)
                        finally:
                            temporary.unlink(missing_ok=True)
                    if git_repository and not no_commit:
                        relative = [str(path.relative_to(settings["library_root"])) for path in targets]
                        stage_name = "git-add"
                        _git(["add", "--", *relative], settings["library_root"])
                        source_ids = sorted(
                            {
                                observation.source_id
                                for candidate in candidates
                                for observation in [
                                    Observation.model_validate_json(row["payload_json"])
                                    for row in state.observations(candidate.source_observation_ids, candidate.project)
                                ]
                            }
                        )
                        body = "\n".join(
                            [
                                f"Source IDs: {', '.join(source_ids) or 'none'}",
                                "Published decision IDs: "
                                + (", ".join(candidate.candidate_id for candidate in candidates) or "none"),
                                f"Conflict IDs: {', '.join(item['id'] for item in open_conflicts) or 'none'}",
                            ]
                        )
                        actor = settings.get("actor", "agent:unknown")
                        author_name, author_email = actor, "clm@localhost"
                        match = re.match(r"(.+?)\s*<([^>]+)>$", actor)
                        if match:
                            author_name, author_email = match.group(1).strip(), match.group(2).strip()
                        git_env = os.environ.copy()
                        git_env.update(
                            {
                                "GIT_AUTHOR_NAME": author_name,
                                "GIT_AUTHOR_EMAIL": author_email,
                                "GIT_COMMITTER_NAME": author_name,
                                "GIT_COMMITTER_EMAIL": author_email,
                            }
                        )
                        stage_name = "git-commit"
                        tree = _git(["write-tree"], settings["library_root"]).stdout.strip()
                        message = f"Maintain {project} context library\n\n{body}\n"
                        committed_head = subprocess.run(
                            ["git", "commit-tree", tree, "-p", original_head],
                            cwd=settings["library_root"],
                            check=True,
                            capture_output=True,
                            text=True,
                            input=message,
                            env=git_env,
                            timeout=30,
                        ).stdout.strip()
                        _record_expected_commit(recovery, original_head, committed_head)
                        _git(
                            ["update-ref", "HEAD", committed_head, original_head],
                            settings["library_root"],
                        )
                    stage_name = "state-transaction"
                    with state.transaction():
                        for candidate in candidates:
                            state.transition(candidate.candidate_id, "published")
                        state.db.execute(
                            "INSERT INTO publications(project, run_id, phase, digest, payload_json, created_at) "
                            "VALUES (?, ?, 'published', ?, ?, ?)",
                            (
                                project,
                                settings.get("run_id", ""),
                                library_digest,
                                json.dumps(settings.get("publication_metadata", {}), sort_keys=True),
                                timestamp(
                                    __import__(
                                        "context_library_maintainer.models",
                                        fromlist=["now_utc"],
                                    ).now_utc()
                                ),
                            ),
                        )
                    state_committed = True
                    stage_name = "recovery-cleanup"
                    _remove_recovery(recovery)
                    return {
                        "published": [candidate.candidate_id for candidate in candidates],
                        "conflicts": [item["id"] for item in open_conflicts],
                        "changed": True,
                        "digest": library_digest,
                        "indexes": sorted(indexes),
                    }
                except Exception as exc:
                    restored: list[str] = []
                    unrestored: list[str] = []
                    if state_committed:
                        try:
                            with state.transaction():
                                for candidate in candidates:
                                    state.db.execute(
                                        "UPDATE candidates SET state='ready', updated_at=? WHERE id=?",
                                        (
                                            timestamp(
                                                __import__(
                                                    "context_library_maintainer.models",
                                                    fromlist=["now_utc"],
                                                ).now_utc()
                                            ),
                                            candidate.candidate_id,
                                        ),
                                    )
                                    state.db.execute(
                                        "DELETE FROM candidate_events WHERE candidate_id=? "
                                        "AND from_state='ready' AND to_state='published'",
                                        (candidate.candidate_id,),
                                    )
                                state.db.execute(
                                    "DELETE FROM publications WHERE project=? AND run_id=? AND phase='published'",
                                    (project, settings.get("run_id", "")),
                                )
                        except Exception:
                            unrestored.append("maintainer-state")
                    if git_repository and original_head:
                        try:
                            _git(["reset", "--mixed", original_head], settings["library_root"])
                        except Exception:
                            unrestored.extend([".git/HEAD", ".git/index"])
                    for path, content in originals.items():
                        try:
                            relative_path = str(path.relative_to(settings["library_root"]))
                            if _path_digest(path) not in {
                                _content_digest(content),
                                _content_digest(targets[path]),
                            }:
                                unrestored.append(relative_path)
                                continue
                            if content is None:
                                path.unlink(missing_ok=True)
                            else:
                                temporary = path.with_name(f".{path.name}.clm-rollback")
                                try:
                                    temporary.write_bytes(content)
                                    os.replace(temporary, path)
                                finally:
                                    temporary.unlink(missing_ok=True)
                            restored.append(relative_path)
                        except Exception:
                            unrestored.append(str(path.relative_to(settings["library_root"])))
                    if not unrestored and recovery is not None:
                        try:
                            _remove_recovery(recovery)
                        except OSError:
                            unrestored.append("recovery-record")
                    raise PublicationError(
                        f"publication failed safely at {stage_name} ({type(exc).__name__})",
                        restored=restored,
                        unrestored=unrestored,
                        stage=stage_name,
                    ) from exc
    except RuntimeError as exc:
        if "project lock is held" in str(exc):
            raise PublicationLockedError("project publication lock is held") from exc
        raise
