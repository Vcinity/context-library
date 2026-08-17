from __future__ import annotations

import dataclasses
import re
from bisect import bisect_right
from pathlib import Path
from typing import Iterable

PROVENANCE_RANK = {"assumed": 0, "inferred": 1, "explicit": 2}
DERIVATIONS = {"direct", "condensed", "synthesized"}
ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
ANCHOR_RE = re.compile(r'^<a\s+id=["\']([^"\']+)["\']\s*></a>\s*$', re.MULTILINE)
FIELD_RE = re.compile(r"^- [A-Za-z][A-Za-z0-9 _-]*:(?:\s|$)", re.MULTILINE)


class CanonicalParseError(ValueError):
    """Canonical decision content is malformed or unsafe to interpret."""


@dataclasses.dataclass(frozen=True)
class Decision:
    decision_id: str
    subject: str
    category: str
    decision: str
    provenance: str
    constraints: tuple[str, ...]
    derivation: str
    source_ids: tuple[str, ...]
    supersedes: tuple[str, ...]
    conflicts_with: tuple[str, ...]
    conflict_key: str | None
    affected_layers: tuple[str, ...]
    applies_when: str | None
    confidence: str | None
    review: str | None
    metadata: dict[str, object]


@dataclasses.dataclass(frozen=True)
class ProjectPack:
    project: str
    register_path: Path
    location: str
    compatibility_locations: tuple[str, ...] = ()


def _field_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def _parse_fields(body: str) -> dict[str, list[str]]:
    fields: dict[str, list[str]] = {}
    current: str | None = None
    for line in body.splitlines():
        match = re.match(r"^- ([A-Za-z][A-Za-z0-9 _-]*):(?:\s*(.*))?$", line)
        if match:
            current = _field_name(match.group(1))
            fields.setdefault(current, [])
            value = (match.group(2) or "").strip()
            if value:
                fields[current].append(value)
            continue
        if current is None:
            continue
        nested = re.match(r"^\s{2,}-\s+(.*)$", line)
        if nested:
            fields[current].append(nested.group(1).strip())
        elif line.startswith("  ") and line.strip():
            if fields[current]:
                fields[current][-1] += " " + line.strip()
            else:
                fields[current].append(line.strip())
        elif line.strip():
            current = None
    return fields


def _one(fields: dict[str, list[str]], name: str, default: str = "") -> str:
    values = fields.get(name, [])
    return " ".join(values).strip() if values else default


def _items(fields: dict[str, list[str]], *names: str) -> tuple[str, ...]:
    values: list[str] = []
    for name in names:
        values.extend(fields.get(name, []))
    return tuple(value.strip() for value in values if value.strip())


def _references(fields: dict[str, list[str]], *names: str) -> tuple[str, ...]:
    raw = _items(fields, *names)
    references: list[str] = []
    for value in raw:
        candidates = re.findall(r"`([a-z0-9][a-z0-9._-]*)`|\(#([a-z0-9][a-z0-9._-]*)\)", value)
        extracted = [first or second for first, second in candidates]
        if not extracted and re.fullmatch(r"[a-z0-9][a-z0-9._-]*(?:\s*,\s*[a-z0-9][a-z0-9._-]*)*", value):
            extracted = [part.strip() for part in value.split(",")]
        for reference in extracted:
            if reference not in references:
                references.append(reference)
    return tuple(references)


def _comma_items(value: str) -> tuple[str, ...]:
    return tuple(part.strip().strip("`") for part in value.split(",") if part.strip())


def parse_register_bytes(content: bytes) -> tuple[Decision, ...]:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CanonicalParseError("decision register is not valid UTF-8") from exc
    return parse_register(text)


def parse_register(text: str) -> tuple[Decision, ...]:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    anchors = list(ANCHOR_RE.finditer(text))
    before = text[: anchors[0].start()] if anchors else text
    if re.search(r"^###\s+", before, re.MULTILINE) or FIELD_RE.search(before):
        raise CanonicalParseError("decision-like content appears before the first decision anchor")
    decisions: list[Decision] = []
    seen: set[str] = set()
    sections = list(re.finditer(r"^##\s+(.+?)\s*$", text, re.MULTILINE))
    section_starts = [section.start() for section in sections]
    for index, anchor in enumerate(anchors):
        decision_id = anchor.group(1)
        if not ID_RE.fullmatch(decision_id):
            raise CanonicalParseError(f"invalid decision identifier: {decision_id!r}")
        if decision_id in seen:
            raise CanonicalParseError(f"duplicate decision identifier: {decision_id}")
        seen.add(decision_id)
        end = anchors[index + 1].start() if index + 1 < len(anchors) else len(text)
        body = text[anchor.end() : end]
        heading = re.search(r"^###\s+(.+?)\s*$", body, re.MULTILINE)
        if heading is None:
            raise CanonicalParseError(f"decision {decision_id} is missing a level-three heading")
        if body[: heading.start()].strip():
            raise CanonicalParseError(f"decision {decision_id} anchor must appear immediately before its heading")
        if re.search(r"^###\s+", body[heading.end() :], re.MULTILINE):
            raise CanonicalParseError(f"unanchored decision heading follows decision {decision_id}")
        fields = _parse_fields(body)
        provenance = _one(fields, "provenance").lower()
        if provenance not in PROVENANCE_RANK:
            raise CanonicalParseError(f"decision {decision_id} has missing or invalid provenance")
        decision_text = _one(fields, "decision")
        constraints = _items(fields, "constraint", "constraints")
        if not constraints and decision_text:
            constraints = (decision_text,)
        if not decision_text and not constraints:
            raise CanonicalParseError(f"decision {decision_id} has no decision or constraint text")
        derivation = _one(fields, "derivation", "direct").lower()
        if derivation not in DERIVATIONS:
            raise CanonicalParseError(f"decision {decision_id} has invalid derivation: {derivation}")
        source_ids = _references(fields, "source_ids", "sources")
        if derivation == "synthesized" and not source_ids:
            raise CanonicalParseError(f"synthesized decision {decision_id} must name its sources")
        if derivation != "synthesized" and source_ids:
            raise CanonicalParseError(f"non-synthesized decision {decision_id} cannot name synthesis sources")
        if not source_ids:
            source_ids = (decision_id,)
        metadata: dict[str, object] = {}
        for name in (
            "date",
            "decisionmaker",
            "rationale",
            "strength",
            "required_evidence",
            "exception_authority",
            "status",
        ):
            value = _one(fields, name)
            if value:
                metadata[name] = value
        evidence = _items(fields, "evidence")
        if evidence:
            metadata["evidence"] = evidence
        section_index = bisect_right(section_starts, anchor.start()) - 1
        category = _one(fields, "category") or (
            sections[section_index].group(1).strip() if section_index >= 0 else "Uncategorized"
        )
        decisions.append(
            Decision(
                decision_id=decision_id,
                subject=heading.group(1).strip(),
                category=category,
                decision=decision_text or constraints[0],
                provenance=provenance,
                constraints=constraints,
                derivation=derivation,
                source_ids=source_ids,
                supersedes=_references(fields, "supersedes"),
                conflicts_with=_references(fields, "conflicts_with", "conflicts"),
                conflict_key=_one(fields, "conflict_key") or None,
                affected_layers=_comma_items(_one(fields, "affected_layers")),
                applies_when=_one(fields, "applies_when") or None,
                confidence=_one(fields, "confidence") or None,
                review=_one(fields, "review", "review_status") or None,
                metadata=metadata,
            )
        )
    if not decisions:
        raise CanonicalParseError("decision register contains no anchored decisions")
    known = {decision.decision_id for decision in decisions}
    by_id = {decision.decision_id: decision for decision in decisions}
    for decision in decisions:
        for source_id in decision.source_ids:
            if source_id not in known:
                raise CanonicalParseError(f"decision {decision.decision_id} references unknown source {source_id}")
        for reference in (*decision.supersedes, *decision.conflicts_with):
            if reference not in known:
                raise CanonicalParseError(f"decision {decision.decision_id} references unknown decision {reference}")
        if decision.derivation == "synthesized":
            expected = weakest_provenance(by_id[source].provenance for source in decision.source_ids)
            if decision.provenance != expected:
                raise CanonicalParseError(
                    f"synthesized decision {decision.decision_id} must use weakest source provenance {expected}"
                )

    def visit(identifier: str, visiting: set[str], visited: set[str]) -> None:
        if identifier in visiting:
            raise CanonicalParseError(f"synthesis cycle includes decision {identifier}")
        if identifier in visited:
            return
        visiting.add(identifier)
        decision = by_id[identifier]
        if decision.derivation == "synthesized":
            for source in decision.source_ids:
                visit(source, visiting, visited)
        visiting.remove(identifier)
        visited.add(identifier)

    visited: set[str] = set()
    for identifier in sorted(known):
        visit(identifier, set(), visited)

    def visit_supersession(identifier: str, visiting: set[str], visited: set[str]) -> None:
        if identifier in visiting:
            raise CanonicalParseError(f"supersession cycle includes decision {identifier}")
        if identifier in visited:
            return
        visiting.add(identifier)
        for superseded in by_id[identifier].supersedes:
            visit_supersession(superseded, visiting, visited)
        visiting.remove(identifier)
        visited.add(identifier)

    supersession_visited: set[str] = set()
    for identifier in sorted(known):
        visit_supersession(identifier, set(), supersession_visited)
    return tuple(decisions)


def decision_ids(text: str) -> list[str]:
    return [decision.decision_id for decision in parse_register(text)]


def validate_projection_compatibility(text: str) -> tuple[Decision, ...]:
    """Validate register invariants required by the generated Plugin projection."""
    decisions = parse_register(text)
    superseded = {
        identifier for decision in decisions if decision.provenance == "explicit" for identifier in decision.supersedes
    }
    active = {
        decision.decision_id: decision
        for decision in decisions
        if decision.decision_id not in superseded and decision.provenance == "explicit" and not decision.applies_when
    }

    def overlaps(left: Decision, right: Decision) -> bool:
        if not left.affected_layers or not right.affected_layers:
            return True
        return bool(set(left.affected_layers).intersection(right.affected_layers))

    for decision in active.values():
        for identifier in decision.conflicts_with:
            other = active.get(identifier)
            if other is not None and overlaps(decision, other):
                raise CanonicalParseError(
                    f"conflicting active decisions: {decision.decision_id} and {other.decision_id}"
                )
    conflict_keys: dict[str, Decision] = {}
    for decision in active.values():
        if not decision.conflict_key:
            continue
        prior = conflict_keys.get(decision.conflict_key)
        if prior is not None and prior.decision != decision.decision and overlaps(prior, decision):
            raise CanonicalParseError(
                f"conflicting active decisions for {decision.conflict_key!r}: "
                f"{prior.decision_id} and {decision.decision_id}"
            )
        conflict_keys[decision.conflict_key] = decision
    return decisions


def discover_packs(root: Path, *, include_incomplete: bool = False) -> tuple[ProjectPack, ...]:
    root = root.expanduser().absolute()
    resolved_root = root.resolve(strict=False)

    def safe_descendant(path: Path, *, require_file: bool = False) -> bool:
        try:
            relative = path.relative_to(root)
        except ValueError:
            return False
        current = root
        for part in relative.parts:
            current /= part
            if current.is_symlink():
                return False
        try:
            path.resolve(strict=False).relative_to(resolved_root)
        except ValueError:
            return False
        return path.is_file() if require_file else True

    packs: dict[str, ProjectPack] = {}
    projects = root / "projects"
    if projects.is_dir() and safe_descendant(projects):
        for child in sorted(projects.iterdir(), key=lambda path: path.name):
            if (
                child.is_symlink()
                or not child.is_dir()
                or not safe_descendant(child)
                or not ID_RE.fullmatch(child.name)
            ):
                continue
            register = child / "decision-register.md"
            if include_incomplete or safe_descendant(register, require_file=True):
                packs[child.name] = ProjectPack(
                    project=child.name,
                    register_path=register,
                    location=f"projects/{child.name}",
                )
    legacy = root / "decision-artifacts" / "decision-register.md"
    if safe_descendant(legacy, require_file=True):
        current = packs.get("legacy")
        if current:
            packs["legacy"] = dataclasses.replace(
                current,
                compatibility_locations=(*current.compatibility_locations, "decision-artifacts"),
            )
        else:
            packs["legacy"] = ProjectPack(
                project="legacy",
                register_path=legacy,
                location="decision-artifacts",
            )
    return tuple(packs[name] for name in sorted(packs))


def resolve_pack(packs: Iterable[ProjectPack], project: str) -> ProjectPack | None:
    available = tuple(packs)
    exact = next((pack for pack in available if pack.project == project), None)
    if exact is not None:
        return exact
    if len(available) == 1 and available[0].location == "decision-artifacts":
        return available[0]
    return None


def weakest_provenance(values: Iterable[str]) -> str:
    values = tuple(values)
    if not values:
        raise ValueError("at least one provenance value is required")
    if any(value not in PROVENANCE_RANK for value in values):
        raise ValueError("unknown provenance")
    return min(values, key=PROVENANCE_RANK.__getitem__)


def lexical_tokens(text: str) -> tuple[str, ...]:
    """Extract normalized lexical tokens from text for deterministic search.

    Converts to lowercase and splits on whitespace and punctuation,
    filtering empty results. Used for consistent term matching across
    search and retrieval implementations.
    """
    normalized = text.lower()
    tokens = re.split(r"[^a-z0-9]+", normalized)
    return tuple(token for token in tokens if token)
