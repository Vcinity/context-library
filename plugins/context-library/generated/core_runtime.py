# Generated from context_library_core.canonical; do not edit.
# source-version: 0.4.4
# source-sha256: 9c79150cdf3ae8b6135495e9197ceac253db312c19512a7e895c1498a28a4751
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


# Generated read-only contract metadata used by the self-contained Plugin.
PRODUCT_VERSION = '0.4.4'
CONTEXT_POLICY_JSON_SCHEMA = {'additionalProperties': False,
 'properties': {'affected_layers': {'additionalProperties': {'type': 'string'},
                                    'title': 'Affected Layers',
                                    'type': 'object'},
                'context_requirement': {'enum': ['required', 'optional', 'disabled'],
                                        'title': 'Context Requirement',
                                        'type': 'string'},
                'project': {'anyOf': [{'pattern': '^[a-z][a-z0-9-]*$', 'type': 'string'}, {'type': 'null'}],
                            'default': None,
                            'title': 'Project'},
                'schema': {'const': 'context-library/context-policy', 'title': 'Schema', 'type': 'string'},
                'schema_version': {'const': 1, 'title': 'Schema Version', 'type': 'integer'}},
 'required': ['schema', 'schema_version', 'context_requirement'],
 'title': 'ContextPolicy',
 'type': 'object'}
APPLICABILITY_REQUEST_JSON_SCHEMA = {'$defs': {'ApplicabilityDecision': {'additionalProperties': False,
                                     'properties': {'applies_when': {'anyOf': [{'maxLength': 2000, 'type': 'string'},
                                                                               {'type': 'null'}],
                                                                     'default': None,
                                                                     'title': 'Applies When'},
                                                    'conflict_ids': {'items': {'type': 'string'},
                                                                     'maxItems': 1000,
                                                                     'title': 'Conflict Ids',
                                                                     'type': 'array'},
                                                    'decision_id': {'maxLength': 256,
                                                                    'minLength': 1,
                                                                    'title': 'Decision Id',
                                                                    'type': 'string'},
                                                    'effective_provenance': {'enum': ['explicit', 'inferred',
                                                                                      'assumed'],
                                                                             'title': 'Effective Provenance',
                                                                             'type': 'string'},
                                                    'provenance': {'enum': ['explicit', 'inferred', 'assumed'],
                                                                   'title': 'Provenance',
                                                                   'type': 'string'},
                                                    'repository_scopes': {'items': {'type': 'string'},
                                                                          'maxItems': 100,
                                                                          'title': 'Repository Scopes',
                                                                          'type': 'array'},
                                                    'source_scope': {'maxLength': 512,
                                                                     'minLength': 1,
                                                                     'title': 'Source Scope',
                                                                     'type': 'string'},
                                                    'supersedes': {'items': {'type': 'string'},
                                                                   'maxItems': 1000,
                                                                   'title': 'Supersedes',
                                                                   'type': 'array'}},
                                     'required': ['decision_id', 'provenance', 'effective_provenance', 'source_scope'],
                                     'title': 'ApplicabilityDecision',
                                     'type': 'object'},
           'ApplicabilityTask': {'additionalProperties': False,
                                 'properties': {'repository_scopes': {'items': {'type': 'string'},
                                                                      'maxItems': 100,
                                                                      'title': 'Repository Scopes',
                                                                      'type': 'array'}},
                                 'title': 'ApplicabilityTask',
                                 'type': 'object'}},
 'additionalProperties': False,
 'properties': {'decision': {'$ref': '#/$defs/ApplicabilityDecision'},
                'schema': {'const': 'context-library/applicability',
                           'default': 'context-library/applicability',
                           'title': 'Schema',
                           'type': 'string'},
                'schema_version': {'const': 1, 'default': 1, 'title': 'Schema Version', 'type': 'integer'},
                'task': {'$ref': '#/$defs/ApplicabilityTask'}},
 'required': ['task', 'decision'],
 'title': 'ApplicabilityRequest',
 'type': 'object'}
TASK_CONTEXT_REQUEST_JSON_SCHEMA = {'$defs': {'TokenizerIdentity': {'additionalProperties': False,
                                 'properties': {'accounting_method': {'maxLength': 512,
                                                                      'minLength': 1,
                                                                      'title': 'Accounting Method',
                                                                      'type': 'string'},
                                                'name': {'maxLength': 256,
                                                         'minLength': 1,
                                                         'title': 'Name',
                                                         'type': 'string'},
                                                'pinned': {'const': True,
                                                           'default': True,
                                                           'title': 'Pinned',
                                                           'type': 'boolean'},
                                                'version': {'maxLength': 128,
                                                            'minLength': 1,
                                                            'title': 'Version',
                                                            'type': 'string'},
                                                'vocabulary_revision': {'maxLength': 128,
                                                                        'minLength': 1,
                                                                        'title': 'Vocabulary Revision',
                                                                        'type': 'string'}},
                                 'required': ['name', 'version', 'vocabulary_revision', 'accounting_method'],
                                 'title': 'TokenizerIdentity',
                                 'type': 'object'}},
 'additionalProperties': False,
 'properties': {'agent_token_budget': {'minimum': 0, 'title': 'Agent Token Budget', 'type': 'integer'},
                'operation': {'maxLength': 256, 'minLength': 1, 'title': 'Operation', 'type': 'string'},
                'project': {'pattern': '^[a-z][a-z0-9-]*$', 'title': 'Project', 'type': 'string'},
                'repository_scopes': {'items': {'type': 'string'},
                                      'maxItems': 100,
                                      'minItems': 1,
                                      'title': 'Repository Scopes',
                                      'type': 'array'},
                'schema': {'const': 'context-library/task-context-request',
                           'default': 'context-library/task-context-request',
                           'title': 'Schema',
                           'type': 'string'},
                'schema_version': {'const': 1, 'default': 1, 'title': 'Schema Version', 'type': 'integer'},
                'task_summary': {'maxLength': 4000, 'minLength': 1, 'title': 'Task Summary', 'type': 'string'},
                'tokenizer': {'$ref': '#/$defs/TokenizerIdentity'}},
 'required': ['project', 'task_summary', 'operation', 'repository_scopes', 'agent_token_budget', 'tokenizer'],
 'title': 'TaskContextRequest',
 'type': 'object'}
TASK_CONTEXT_RESPONSE_JSON_SCHEMA = {'$defs': {'ApplicabilityState': {'enum': ['unconditional', 'satisfied', 'unsatisfied', 'undetermined'],
                                  'title': 'ApplicabilityState',
                                  'type': 'string'},
           'CapsuleAccounting': {'additionalProperties': False,
                                 'properties': {'budget_status': {'enum': ['verified', 'unverified'],
                                                                  'title': 'Budget Status',
                                                                  'type': 'string'},
                                                'serialized_content': {'title': 'Serialized Content', 'type': 'string'},
                                                'sha256': {'pattern': '^[0-9a-f]{64}$',
                                                           'title': 'Sha256',
                                                           'type': 'string'},
                                                'token_count': {'minimum': 0,
                                                                'title': 'Token Count',
                                                                'type': 'integer'},
                                                'tokenizer': {'$ref': '#/$defs/TokenizerIdentity'},
                                                'utf8_byte_count': {'minimum': 0,
                                                                    'title': 'Utf8 Byte Count',
                                                                    'type': 'integer'}},
                                 'required': ['serialized_content', 'utf8_byte_count', 'sha256', 'token_count',
                                              'tokenizer', 'budget_status'],
                                 'title': 'CapsuleAccounting',
                                 'type': 'object'},
           'TaskContextCoverage': {'additionalProperties': False,
                                   'properties': {'budget_status': {'enum': ['verified', 'unverified'],
                                                                    'title': 'Budget Status',
                                                                    'type': 'string'},
                                                  'complete': {'title': 'Complete', 'type': 'boolean'},
                                                  'omitted_operative_decision_ids': {'items': {'type': 'string'},
                                                                                     'title': 'Omitted Operative '
                                                                                              'Decision Ids',
                                                                                     'type': 'array'},
                                                  'operative_expected': {'minimum': 0,
                                                                         'title': 'Operative Expected',
                                                                         'type': 'integer'},
                                                  'operative_included': {'minimum': 0,
                                                                         'title': 'Operative Included',
                                                                         'type': 'integer'}},
                                   'required': ['operative_expected', 'operative_included', 'complete',
                                                'budget_status'],
                                   'title': 'TaskContextCoverage',
                                   'type': 'object'},
           'TaskContextItem': {'additionalProperties': False,
                               'properties': {'conflict_ids': {'items': {'type': 'string'},
                                                               'title': 'Conflict Ids',
                                                               'type': 'array'},
                                              'decision_id': {'maxLength': 256,
                                                              'minLength': 1,
                                                              'title': 'Decision Id',
                                                              'type': 'string'},
                                              'effective_provenance': {'enum': ['explicit', 'inferred', 'assumed'],
                                                                       'title': 'Effective Provenance',
                                                                       'type': 'string'},
                                              'provenance': {'enum': ['explicit', 'inferred', 'assumed'],
                                                             'title': 'Provenance',
                                                             'type': 'string'},
                                              'source_scope': {'maxLength': 512,
                                                               'minLength': 1,
                                                               'title': 'Source Scope',
                                                               'type': 'string'},
                                              'state': {'$ref': '#/$defs/ApplicabilityState'},
                                              'supersedes': {'items': {'type': 'string'},
                                                             'title': 'Supersedes',
                                                             'type': 'array'},
                                              'text': {'maxLength': 4000,
                                                       'minLength': 1,
                                                       'title': 'Text',
                                                       'type': 'string'}},
                               'required': ['decision_id', 'text', 'state', 'provenance', 'effective_provenance',
                                            'source_scope'],
                               'title': 'TaskContextItem',
                               'type': 'object'},
           'TaskContextTruncation': {'additionalProperties': False,
                                     'properties': {'omitted_operative_decision_ids': {'items': {'type': 'string'},
                                                                                       'title': 'Omitted Operative '
                                                                                                'Decision Ids',
                                                                                       'type': 'array'},
                                                    'reason': {'default': 'none',
                                                               'enum': ['none', 'token-budget'],
                                                               'title': 'Reason',
                                                               'type': 'string'},
                                                    'truncated': {'title': 'Truncated', 'type': 'boolean'}},
                                     'required': ['truncated'],
                                     'title': 'TaskContextTruncation',
                                     'type': 'object'},
           'TokenizerIdentity': {'additionalProperties': False,
                                 'properties': {'accounting_method': {'maxLength': 512,
                                                                      'minLength': 1,
                                                                      'title': 'Accounting Method',
                                                                      'type': 'string'},
                                                'name': {'maxLength': 256,
                                                         'minLength': 1,
                                                         'title': 'Name',
                                                         'type': 'string'},
                                                'pinned': {'const': True,
                                                           'default': True,
                                                           'title': 'Pinned',
                                                           'type': 'boolean'},
                                                'version': {'maxLength': 128,
                                                            'minLength': 1,
                                                            'title': 'Version',
                                                            'type': 'string'},
                                                'vocabulary_revision': {'maxLength': 128,
                                                                        'minLength': 1,
                                                                        'title': 'Vocabulary Revision',
                                                                        'type': 'string'}},
                                 'required': ['name', 'version', 'vocabulary_revision', 'accounting_method'],
                                 'title': 'TokenizerIdentity',
                                 'type': 'object'}},
 'additionalProperties': False,
 'properties': {'agent_visible_capsule': {'$ref': '#/$defs/CapsuleAccounting'},
                'applicability_uncertainties': {'items': {'$ref': '#/$defs/TaskContextItem'},
                                                'title': 'Applicability Uncertainties',
                                                'type': 'array'},
                'applicable_conflicts': {'items': {'type': 'string'}, 'title': 'Applicable Conflicts', 'type': 'array'},
                'coverage': {'$ref': '#/$defs/TaskContextCoverage'},
                'non_operative_directives': {'items': {'$ref': '#/$defs/TaskContextItem'},
                                             'title': 'Non Operative Directives',
                                             'type': 'array'},
                'operative_directives': {'items': {'$ref': '#/$defs/TaskContextItem'},
                                         'title': 'Operative Directives',
                                         'type': 'array'},
                'project': {'title': 'Project', 'type': 'string'},
                'revision': {'maxLength': 256, 'minLength': 1, 'title': 'Revision', 'type': 'string'},
                'schema': {'const': 'context-library/task-context-response',
                           'default': 'context-library/task-context-response',
                           'title': 'Schema',
                           'type': 'string'},
                'schema_version': {'const': 1, 'default': 1, 'title': 'Schema Version', 'type': 'integer'},
                'truncation': {'$ref': '#/$defs/TaskContextTruncation'}},
 'required': ['project', 'revision', 'coverage', 'truncation', 'agent_visible_capsule'],
 'title': 'TaskContextResponse',
 'type': 'object'}
DECISION_AUDIT_RESPONSE_JSON_SCHEMA = {'$defs': {'ApplicabilityState': {'enum': ['unconditional', 'satisfied', 'unsatisfied', 'undetermined'],
                                  'title': 'ApplicabilityState',
                                  'type': 'string'},
           'DecisionAuditApplicability': {'additionalProperties': False,
                                          'properties': {'matched_selectors': {'additionalProperties': {'items': {'type': 'string'},
                                                                                                        'type': 'array'},
                                                                               'title': 'Matched Selectors',
                                                                               'type': 'object'},
                                                         'reason': {'enum': ['none', 'scope-mismatch',
                                                                             'missing-task-signal',
                                                                             'conditional-unresolved'],
                                                                    'title': 'Reason',
                                                                    'type': 'string'},
                                                         'required_selectors': {'items': {'type': 'string'},
                                                                                'title': 'Required Selectors',
                                                                                'type': 'array'},
                                                         'state': {'$ref': '#/$defs/ApplicabilityState'}},
                                          'required': ['state', 'reason'],
                                          'title': 'DecisionAuditApplicability',
                                          'type': 'object'},
           'DecisionAuditRecord': {'additionalProperties': False,
                                   'properties': {'affected_layers': {'items': {'type': 'string'},
                                                                      'maxItems': 1000,
                                                                      'title': 'Affected Layers',
                                                                      'type': 'array'},
                                                  'applicability': {'$ref': '#/$defs/DecisionAuditApplicability'},
                                                  'applies_when': {'anyOf': [{'maxLength': 2000, 'type': 'string'},
                                                                             {'type': 'null'}],
                                                                   'default': None,
                                                                   'title': 'Applies When'},
                                                  'category': {'maxLength': 512,
                                                               'minLength': 1,
                                                               'title': 'Category',
                                                               'type': 'string'},
                                                  'confidence': {'anyOf': [{'maxLength': 512, 'type': 'string'},
                                                                           {'type': 'null'}],
                                                                 'default': None,
                                                                 'title': 'Confidence'},
                                                  'conflict_ids': {'items': {'type': 'string'},
                                                                   'maxItems': 1000,
                                                                   'title': 'Conflict Ids',
                                                                   'type': 'array'},
                                                  'conflict_key': {'anyOf': [{'maxLength': 512, 'type': 'string'},
                                                                             {'type': 'null'}],
                                                                   'default': None,
                                                                   'title': 'Conflict Key'},
                                                  'constraints': {'items': {'type': 'string'},
                                                                  'maxItems': 1000,
                                                                  'title': 'Constraints',
                                                                  'type': 'array'},
                                                  'decision': {'maxLength': 4000,
                                                               'minLength': 1,
                                                               'title': 'Decision',
                                                               'type': 'string'},
                                                  'decision_id': {'maxLength': 256,
                                                                  'minLength': 1,
                                                                  'title': 'Decision Id',
                                                                  'type': 'string'},
                                                  'derivation': {'enum': ['direct', 'condensed', 'synthesized'],
                                                                 'title': 'Derivation',
                                                                 'type': 'string'},
                                                  'effective_provenance': {'enum': ['explicit', 'inferred', 'assumed'],
                                                                           'title': 'Effective Provenance',
                                                                           'type': 'string'},
                                                  'evidence': {'items': {'type': 'string'},
                                                               'maxItems': 1000,
                                                               'title': 'Evidence',
                                                               'type': 'array'},
                                                  'provenance': {'enum': ['explicit', 'inferred', 'assumed'],
                                                                 'title': 'Provenance',
                                                                 'type': 'string'},
                                                  'rationale': {'anyOf': [{'maxLength': 20000, 'type': 'string'},
                                                                          {'type': 'null'}],
                                                                'default': None,
                                                                'title': 'Rationale'},
                                                  'review': {'anyOf': [{'maxLength': 512, 'type': 'string'},
                                                                       {'type': 'null'}],
                                                             'default': None,
                                                             'title': 'Review'},
                                                  'source_ids': {'items': {'type': 'string'},
                                                                 'maxItems': 1000,
                                                                 'title': 'Source Ids',
                                                                 'type': 'array'},
                                                  'source_scope': {'maxLength': 512,
                                                                   'minLength': 1,
                                                                   'title': 'Source Scope',
                                                                   'type': 'string'},
                                                  'subject': {'maxLength': 4000,
                                                              'minLength': 1,
                                                              'title': 'Subject',
                                                              'type': 'string'},
                                                  'supersedes': {'items': {'type': 'string'},
                                                                 'maxItems': 1000,
                                                                 'title': 'Supersedes',
                                                                 'type': 'array'}},
                                   'required': ['decision_id', 'subject', 'category', 'decision', 'provenance',
                                                'effective_provenance', 'derivation', 'source_scope', 'applicability'],
                                   'title': 'DecisionAuditRecord',
                                   'type': 'object'}},
 'additionalProperties': False,
 'properties': {'project': {'title': 'Project', 'type': 'string'},
                'records': {'items': {'$ref': '#/$defs/DecisionAuditRecord'},
                            'maxItems': 100,
                            'minItems': 1,
                            'title': 'Records',
                            'type': 'array'},
                'revision': {'maxLength': 256, 'minLength': 1, 'title': 'Revision', 'type': 'string'},
                'schema': {'const': 'context-library/decision-audit-response',
                           'default': 'context-library/decision-audit-response',
                           'title': 'Schema',
                           'type': 'string'},
                'schema_version': {'const': 1, 'default': 1, 'title': 'Schema Version', 'type': 'integer'}},
 'required': ['project', 'revision', 'records'],
 'title': 'DecisionAuditResponse',
 'type': 'object'}


def validate_context_policy(payload: object) -> dict[str, object]:
    """Validate the Core context-policy/v1 contract without write dependencies."""
    if not isinstance(payload, dict):
        raise ValueError("context policy must be a JSON object")
    schema = CONTEXT_POLICY_JSON_SCHEMA
    properties = schema["properties"]
    unknown = set(payload).difference(properties)
    if unknown:
        raise ValueError(f"unknown context policy field: {sorted(unknown)[0]}")
    missing = set(schema.get("required", ())).difference(payload)
    if missing:
        raise ValueError(f"missing context policy field: {sorted(missing)[0]}")
    if payload.get("schema") != properties["schema"]["const"]:
        raise ValueError("unsupported context policy schema family")
    if payload.get("schema_version") != properties["schema_version"]["const"]:
        raise ValueError("unsupported context policy schema version")
    requirement = payload.get("context_requirement")
    if requirement not in properties["context_requirement"]["enum"]:
        raise ValueError("invalid context requirement")
    project = payload.get("project")
    if project is not None and (not isinstance(project, str) or not ID_RE.fullmatch(project)):
        raise ValueError("configured project must be a stable lowercase identifier")
    affected = payload.get("affected_layers", {})
    if not isinstance(affected, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in affected.items()
    ):
        raise ValueError("context policy affected_layers must map strings to strings")
    return payload


def evaluate_applicability(payload: object) -> dict[str, object]:
    """Evaluate the Core v1 repository-scope rule without write dependencies."""
    if not isinstance(payload, dict) or payload.get("schema") != "context-library/applicability":
        raise ValueError("unsupported applicability schema family")
    if payload.get("schema_version") != 1:
        raise ValueError("unsupported applicability schema version")
    task = payload.get("task")
    decision = payload.get("decision")
    if not isinstance(task, dict) or not isinstance(decision, dict):
        raise ValueError("applicability task and decision are required objects")
    task_scopes = task.get("repository_scopes", [])
    decision_scopes = decision.get("repository_scopes", [])
    if not all(
        isinstance(item, str)
        and item
        and not item.startswith("/")
        and ".." not in item.split("/")
        for item in (*task_scopes, *decision_scopes)
    ):
        raise ValueError("repository scopes must be safe relative paths")
    if len(task_scopes) != len(set(task_scopes)) or len(decision_scopes) != len(set(decision_scopes)):
        raise ValueError("repository scopes must be unique")
    matched = sorted(set(task_scopes) & set(decision_scopes))
    if not decision_scopes and decision.get("applies_when") is None:
        state, reason = "unconditional", "none"
    elif decision.get("applies_when") is not None:
        state, reason = "undetermined", "conditional-unresolved"
    elif not task_scopes:
        state, reason = "undetermined", "missing-task-signal"
    elif matched:
        state, reason = "satisfied", "none"
    else:
        state, reason = "unsatisfied", "scope-mismatch"
    return {
        "decision_id": decision.get("decision_id"),
        "state": state,
        "reason": reason,
        "matched_selectors": {"repository_scopes": matched} if matched else {},
        "required_selectors": ["repository_scopes"] if decision_scopes else [],
        "provenance": decision.get("provenance"),
        "effective_provenance": decision.get("effective_provenance"),
        "source_scope": decision.get("source_scope"),
        "supersedes": decision.get("supersedes", []),
        "conflict_ids": decision.get("conflict_ids", []),
    }


# Generated task-context and audit helpers.  Keep this code dependency-free so
# the independently installable Plugin can preserve Core semantics without
# importing the write-capable application packages.
import hashlib
import re


def _require_object(value: object, message: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(message)
    return value


def validate_task_context_request(payload: object) -> dict[str, object]:
    data = _require_object(payload, "task-context request must be a JSON object")
    schema = TASK_CONTEXT_REQUEST_JSON_SCHEMA
    properties = schema["properties"]
    unknown = set(data).difference(properties)
    if unknown:
        raise ValueError(f"unknown task-context field: {sorted(unknown)[0]}")
    if data.get("schema", "context-library/task-context-request") != "context-library/task-context-request":
        raise ValueError("unsupported task-context schema family")
    if data.get("schema_version", 1) != 1:
        raise ValueError("unsupported task-context schema version")
    required = (
        "project",
        "task_summary",
        "operation",
        "repository_scopes",
        "agent_token_budget",
        "tokenizer",
    )
    missing = [name for name in required if name not in data]
    if missing:
        raise ValueError(f"missing task-context field: {missing[0]}")
    project = data["project"]
    if not isinstance(project, str) or not re.fullmatch(r"^[a-z][a-z0-9-]*$", project):
        raise ValueError("project must be a stable lowercase identifier")
    for name in ("task_summary", "operation"):
        if not isinstance(data[name], str) or not data[name].strip():
            raise ValueError(f"{name} must be non-empty")
    scopes = data["repository_scopes"]
    if not isinstance(scopes, list) or not scopes:
        raise ValueError("repository_scopes must be a non-empty list")
    if any(
        not isinstance(item, str)
        or not item
        or item.startswith("/")
        or "\\" in item
        or any(part in {"", ".", ".."} for part in item.split("/"))
        for item in scopes
    ):
        raise ValueError("repository scopes must be non-empty relative paths")
    if len(scopes) != len(set(scopes)):
        raise ValueError("repository scopes must be unique")
    if not isinstance(data["agent_token_budget"], int) or isinstance(data["agent_token_budget"], bool) or data["agent_token_budget"] < 0:
        raise ValueError("agent_token_budget must be a non-negative integer")
    tokenizer = _require_object(data["tokenizer"], "tokenizer must be an object")
    allowed_tokenizer = {"name", "version", "vocabulary_revision", "accounting_method", "pinned"}
    if set(tokenizer).difference(allowed_tokenizer):
        raise ValueError("unknown tokenizer field")
    if tokenizer.get("pinned", True) is not True:
        raise ValueError("tokenizer must be pinned")
    if any(not isinstance(tokenizer.get(name), str) or not tokenizer[name] for name in allowed_tokenizer - {"pinned"}):
        raise ValueError("tokenizer identity fields must be non-empty strings")
    data["tokenizer"] = dict(tokenizer)
    data["tokenizer"].setdefault("pinned", True)
    return data


def lexical_tokens(text: str) -> tuple[str, ...]:
    """Extract normalized lexical tokens from text for deterministic search.

    Converts to lowercase and splits on whitespace and punctuation,
    filtering empty results. Used for consistent term matching across
    search and retrieval implementations.
    """
    normalized = text.lower()
    tokens = re.split(r"[^a-z0-9]+", normalized)
    return tuple(token for token in tokens if token)


def _task_item(decision: Decision, state: str, source_scope: str) -> dict[str, object]:
    return {
        "decision_id": decision.decision_id,
        "text": decision.decision,
        "state": state,
        "provenance": decision.provenance,
        "effective_provenance": decision.provenance,
        "source_scope": source_scope,
        "supersedes": list(decision.supersedes),
        "conflict_ids": list(decision.conflicts_with),
    }


_SUPPORTED_TOKENIZER = {
    "name": "tiktoken",
    "version": "0.9.0",
    "vocabulary_revision": "cl100k_base",
}

# Narrow, enumerated boundary for encoder resolution/lookup failures (missing
# dependency, corrupt or unreachable encoding cache, unsupported platform).
_ENCODER_FAILURE_EXCEPTIONS = (ImportError, OSError, RuntimeError, ValueError, LookupError)


def _resolve_verified_encoder(tokenizer: dict[str, object]):
    if (
        tokenizer.get("name") != _SUPPORTED_TOKENIZER["name"]
        or tokenizer.get("version") != _SUPPORTED_TOKENIZER["version"]
        or tokenizer.get("vocabulary_revision") != _SUPPORTED_TOKENIZER["vocabulary_revision"]
    ):
        raise ValueError(
            "unsupported tokenizer identity: task-context accounting requires the packaged "
            f"{_SUPPORTED_TOKENIZER['name']} {_SUPPORTED_TOKENIZER['version']} "
            f"{_SUPPORTED_TOKENIZER['vocabulary_revision']} encoder"
        )
    try:
        import tiktoken
        return tiktoken.get_encoding(_SUPPORTED_TOKENIZER["vocabulary_revision"])
    except _ENCODER_FAILURE_EXCEPTIONS as exc:
        raise ValueError("tokenizer encoder is unavailable") from exc


def _count_tokens(encoder, capsule: str) -> int:
    try:
        return len(encoder.encode(capsule))
    except _ENCODER_FAILURE_EXCEPTIONS as exc:
        raise ValueError("tokenizer encoder is unavailable") from exc


def _render_task_context(payload: dict[str, object], decisions: tuple[Decision, ...], *, revision: str, source_scope: str) -> dict[str, object]:
    scopes = payload["repository_scopes"]
    superseded = {identifier for decision in decisions for identifier in decision.supersedes}
    items: list[dict[str, object]] = []
    for decision in decisions:
        decision_scopes = list(decision.affected_layers)
        evaluation = evaluate_applicability({
            "schema": "context-library/applicability",
            "schema_version": 1,
            "task": {"repository_scopes": scopes},
            "decision": {
                "decision_id": decision.decision_id,
                "repository_scopes": decision_scopes,
                "provenance": decision.provenance,
                "effective_provenance": decision.provenance,
                "source_scope": source_scope,
                "supersedes": list(decision.supersedes),
                "conflict_ids": list(decision.conflicts_with),
                "applies_when": decision.applies_when,
            },
        })
        state = str(evaluation["state"])
        if decision.provenance != "explicit" or decision.decision_id in superseded:
            state = "unsatisfied"
        items.append(_task_item(decision, state, source_scope))
    ordered = sorted(items, key=lambda item: (str(item["state"]), str(item["decision_id"]), str(item["source_scope"])))
    operative = [item for item in ordered if item["state"] in {"unconditional", "satisfied"}]
    uncertainties = [item for item in ordered if item["state"] == "undetermined"]
    non_operative = [item for item in ordered if item["state"] == "unsatisfied"]
    tokenizer = payload["tokenizer"]
    encoder = _resolve_verified_encoder(tokenizer)
    budget_status = "verified"
    capsule_lines = [f"# Task context: {payload['project']}", f"revision: {revision}", "", "## Operative directives"]
    capsule_lines.extend(f"- [{item['decision_id']}] {item['text']}" for item in operative)
    capsule = "\n".join(capsule_lines) + "\n"
    token_count = _count_tokens(encoder, capsule)
    omitted = []
    if token_count > payload["agent_token_budget"]:
        omitted = [str(item["decision_id"]) for item in operative]
        capsule = ""
        token_count = 0
    encoded = capsule.encode("utf-8")
    return {
        "schema": "context-library/task-context-response",
        "schema_version": 1,
        "project": payload["project"],
        "revision": revision,
        "operative_directives": operative,
        "applicability_uncertainties": uncertainties,
        "non_operative_directives": non_operative,
        "applicable_conflicts": sorted({str(conflict) for item in operative + uncertainties + non_operative for conflict in item["conflict_ids"]}),
        "coverage": {
            "operative_expected": len(operative),
            "operative_included": len(operative) - len(omitted),
            "omitted_operative_decision_ids": omitted,
            "complete": not omitted,
            "budget_status": budget_status,
        },
        "truncation": {
            "truncated": bool(omitted),
            "reason": "token-budget" if omitted else "none",
            "omitted_operative_decision_ids": omitted,
        },
        "agent_visible_capsule": {
            "serialized_content": capsule,
            "utf8_byte_count": len(encoded),
            "sha256": hashlib.sha256(encoded).hexdigest(),
            "token_count": token_count,
            "tokenizer": tokenizer,
            "budget_status": budget_status,
        },
    }


def resolve_task_context(payload: object, register: str, *, revision: str, source_scope: str) -> dict[str, object]:
    request = validate_task_context_request(payload)
    return _render_task_context(request, parse_register(register), revision=revision, source_scope=source_scope)


def _audit_applicability(decision: Decision) -> dict[str, object]:
    scopes = list(decision.affected_layers)
    if not scopes and decision.applies_when is None:
        state, reason = "unconditional", "none"
    elif decision.applies_when is not None:
        state, reason = "undetermined", "conditional-unresolved"
    else:
        state, reason = "undetermined", "missing-task-signal"
    return {
        "state": state,
        "reason": reason,
        "matched_selectors": {},
        "required_selectors": ["repository_scopes"] if scopes else [],
    }


def _effective_provenances(decisions: tuple[Decision, ...]) -> dict[str, str]:
    by_id = {decision.decision_id: decision for decision in decisions}
    resolved: dict[str, str] = {}
    visiting: set[str] = set()

    def resolve(identifier: str) -> str:
        if identifier in resolved:
            return resolved[identifier]
        if identifier in visiting:
            raise ValueError(f"synthesis provenance cycle includes {identifier}")
        visiting.add(identifier)
        decision = by_id[identifier]
        values = [decision.provenance]
        if decision.derivation == "synthesized":
            values.extend(resolve(source_id) for source_id in decision.source_ids)
        visiting.remove(identifier)
        resolved[identifier] = min(values, key=PROVENANCE_RANK.__getitem__)
        return resolved[identifier]

    for identifier in by_id:
        resolve(identifier)
    return resolved


def build_decision_audit(register: str, *, project: str, revision: str, source_scope: str, decision_ids: list[str], include_related: bool = False) -> dict[str, object]:
    decisions = parse_register(register)
    by_id = {decision.decision_id: decision for decision in decisions}
    if not decision_ids or len(decision_ids) > 100 or len(decision_ids) != len(set(decision_ids)):
        raise ValueError("decision_ids must contain between 1 and 100 unique IDs")
    if any(not isinstance(identifier, str) or not ID_RE.fullmatch(identifier) for identifier in decision_ids):
        raise ValueError("decision IDs must be stable identifiers")
    if not isinstance(include_related, bool):
        raise ValueError("include_related must be a boolean")
    selected = set(decision_ids)
    unknown = selected.difference(by_id)
    if unknown:
        raise ValueError(f"unknown decision ID: {sorted(unknown)[0]}")
    if include_related:
        for decision in decisions:
            references = set(decision.supersedes) | set(decision.conflicts_with) | set(decision.source_ids)
            if decision.decision_id in selected or references.intersection(selected):
                selected.add(decision.decision_id)
    effective_provenance = _effective_provenances(decisions)
    records = []
    for decision in decisions:
        if decision.decision_id not in selected:
            continue
        metadata = decision.metadata
        records.append({
            "decision_id": decision.decision_id,
            "subject": decision.subject,
            "category": decision.category,
            "decision": decision.decision,
            "constraints": list(decision.constraints),
            "rationale": metadata.get("rationale"),
            "evidence": list(metadata.get("evidence", ())),
            "provenance": decision.provenance,
            "effective_provenance": effective_provenance[decision.decision_id],
            "derivation": decision.derivation,
            "source_ids": list(decision.source_ids),
            "source_scope": source_scope,
            "supersedes": list(decision.supersedes),
            "conflict_ids": list(decision.conflicts_with),
            "conflict_key": decision.conflict_key,
            "affected_layers": list(decision.affected_layers),
            "applies_when": decision.applies_when,
            "confidence": decision.confidence,
            "review": None if decision.review == "review_status" else decision.review,
            "applicability": _audit_applicability(decision),
        })
    return {
        "schema": "context-library/decision-audit-response",
        "schema_version": 1,
        "project": project,
        "revision": revision,
        "records": records,
    }
