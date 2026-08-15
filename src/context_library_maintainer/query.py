from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from context_library_core.canonical import discover_packs, resolve_pack
from context_library_core.shared_context import SharedContextError, resolve_effective_view

from .models import canonical_json

URI = re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s)]+", re.IGNORECASE)
SECRET = re.compile(r"(?i)(\b(?:password|passwd|secret|token|api[_-]?key)\s*[:=]\s*)([^&\s]+)")


def _redact(value: str) -> tuple[str, bool]:
    safe = SECRET.sub(r"\1[REDACTED]", value)
    return safe, safe != value


def _git_revision(root: Path, fallback: str) -> str:
    git = root / ".git"
    try:
        if git.is_file():
            pointer = git.read_text(encoding="utf-8").strip().removeprefix("gitdir: ")
            git = (root / pointer).resolve()
        head = (git / "HEAD").read_text(encoding="utf-8").strip()
        if not head.startswith("ref: "):
            return head
        reference = head.removeprefix("ref: ")
        loose = git / reference
        if loose.is_file():
            return loose.read_text(encoding="utf-8").strip()
        for line in (git / "packed-refs").read_text(encoding="utf-8").splitlines():
            if line.endswith(f" {reference}"):
                return line.split(" ", 1)[0]
    except (OSError, UnicodeError):
        pass
    return fallback


def resolve_register(library_root: Path, project: str) -> tuple[Path, Path]:
    from .config import safe_path

    library_root = library_root.expanduser().absolute()
    project_root = library_root / "projects" / project
    safe_path(library_root, project_root, allow_missing=True)
    configured = project_root / "maintainer.yaml"
    if configured.is_file():
        from .config import project_files, resolve_config

        root, config, _, _ = project_files(resolve_config(library_root, project=project))
        register = root / config.register
        if register.is_file():
            return root, register
    legacy = library_root / "decision-artifacts" / "decision-register.md"
    selected = resolve_pack(discover_packs(library_root), project)
    if selected is not None and selected.location == "decision-artifacts" and legacy.is_file():
        safe_path(library_root, legacy)
        return legacy.parent, legacy
    direct = project_root / "decision-register.md"
    if direct.is_file():
        return project_root, direct
    raise ValueError(f"decision register is unavailable for project {project}")


def read_library(library_root: Path, project: str) -> dict[str, Any]:
    try:
        effective = resolve_effective_view(library_root, project)
    except SharedContextError as exc:
        raise ValueError(str(exc)) from exc
    pack_root, _ = resolve_register(library_root, project)
    digest_input: dict[str, Any] = {"source_digests": effective.source_digests}
    for name in (
        "index-by-category.md",
        "index-by-date.md",
        "index-by-layer.md",
        "supersession-index.md",
    ):
        path = pack_root / name
        if path.is_file() and not path.is_symlink():
            digest_input[name] = path.read_text(encoding="utf-8")
    library_digest = hashlib.sha256(canonical_json(digest_input).encode()).hexdigest()
    revision = _git_revision(library_root, library_digest[:16])
    records: list[dict[str, Any]] = []
    for effective_item in effective.records:
        parsed = effective_item.decision
        evidence = [str(value) for value in parsed.metadata.get("evidence", ())]
        source_references = []
        for evidence_item in evidence:
            uri_match = URI.search(evidence_item)
            uri, uri_redacted = _redact(
                uri_match.group(0)
                if uri_match
                else "evidence:" + hashlib.sha256(evidence_item.encode()).hexdigest()[:16]
            )
            label, label_redacted = _redact(evidence_item)
            redacted = uri_redacted or label_redacted
            source_references.append(
                {
                    "uri": uri,
                    "label": label,
                    "redacted": redacted,
                    "secret_state": "redacted" if redacted else "none",
                }
            )
        provenance = parsed.provenance
        status = str(parsed.metadata.get("status", provenance)).strip().lower()
        if status not in {
            "authoritative",
            "inferred",
            "assumed",
            "pending",
            "superseded",
            "excluded",
        }:
            status = "authoritative" if provenance == "explicit" else provenance
        subject, _ = _redact(parsed.subject)
        decision, _ = _redact(parsed.decision)
        rationale, _ = _redact(str(parsed.metadata.get("rationale", "")))
        decisionmaker, _ = _redact(str(parsed.metadata.get("decisionmaker", "")))
        records.append(
            {
                "decision_id": parsed.decision_id,
                "subject": subject,
                "decision": decision,
                "rationale": rationale,
                "category": parsed.category,
                "provenance": provenance,
                "status": status,
                "source_count": len(source_references),
                "publication_revision": revision,
                "library_digest": library_digest,
                "decisionmaker": decisionmaker,
                "decision_date": str(parsed.metadata.get("date", "")),
                "sources": source_references,
                "supersedes": list(parsed.supersedes),
                "superseded_by": [],
                "related_decisions": [],
                "open_proposals": [],
                "open_reviews": [],
                "source_scope": effective_item.source_scope,
                "source_project": effective_item.source_project,
                "source_digest": effective_item.source_digest,
            }
        )
    by_id = {record["decision_id"]: record for record in records}
    for record in records:
        for old in record["supersedes"]:
            if old in by_id:
                by_id[old]["superseded_by"].append(record["decision_id"])
                by_id[old]["status"] = "superseded"
    return {
        "project": project,
        "library_digest": library_digest,
        "publication_revision": revision,
        "records": records,
    }


def query_library(
    library_root: Path,
    project: str,
    *,
    query: str = "",
    decision_id: str | None = None,
    status: str | None = None,
    category: str | None = None,
    page: int = 1,
    page_size: int = 25,
    digest_only: bool = False,
) -> dict[str, Any]:
    if len(query) > 500:
        raise ValueError("query is limited to 500 characters")
    if page < 1 or page_size < 1 or page_size > 100:
        raise ValueError("page must be positive and page_size must be 1 through 100")
    snapshot = read_library(library_root, project)
    records = snapshot.pop("records")
    if digest_only:
        return snapshot
    if decision_id:
        match = next(
            (record for record in records if record["decision_id"] == decision_id),
            None,
        )
        if not match:
            raise KeyError(decision_id)
        return {**snapshot, "decision": match}
    if status:
        records = [record for record in records if record["status"] == status]
    if category:
        records = [record for record in records if record["category"].casefold() == category.casefold()]
    if query:
        needle = query.casefold()
        records = [record for record in records if needle in canonical_json(record).casefold()]
    records.sort(key=lambda item: (item["subject"].casefold(), item["decision_id"]))
    total = len(records)
    start = (page - 1) * page_size
    summary_fields = {
        "decision_id",
        "subject",
        "decision",
        "rationale",
        "category",
        "provenance",
        "status",
        "source_count",
        "publication_revision",
        "library_digest",
        "source_scope",
        "source_project",
        "source_digest",
    }
    items = [
        {key: value for key, value in record.items() if key in summary_fields}
        for record in records[start : start + page_size]
    ]
    return {
        **snapshot,
        "items": items,
        "page": page,
        "page_size": page_size,
        "total": total,
        "next_page": page + 1 if start + page_size < total else None,
    }
