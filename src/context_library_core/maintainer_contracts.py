from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=lambda item: item.isoformat() if isinstance(item, datetime) else str(item),
    )


def model_json(model: BaseModel) -> str:
    return canonical_json(model.model_dump(by_alias=True))


def now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def safe_error(value: BaseException | str) -> str:
    text = str(value)
    return re.sub(
        r"(?i)((?:password|passwd|secret|token|api[_-]?key|authorization|credential)\s*[:=]\s*)(\S+)",
        r"\1[REDACTED]",
        text,
    )[:2000]


class StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        populate_by_name=True,
        serialize_by_alias=True,
    )


class Person(StrictModel):
    identity: str
    display_name: str | None = None


SourceType = Literal["ticket", "documentation", "project-note", "chat", "meeting", "idea", "code", "commit", "other"]
ContentFormat = Literal["text", "markdown", "json", "transcript"]


class RetainedExcerpt(StrictModel):
    excerpt: str
    location: str


class SecretSpan(StrictModel):
    start: int
    end: int

    @model_validator(mode="after")
    def valid_range(self) -> "SecretSpan":
        if self.start < 0 or self.end <= self.start:
            raise ValueError("secret span must be a non-empty half-open range")
        return self


class SourceEnvelope(StrictModel):
    schema_id: Literal["context-library/source-envelope"] = Field(
        default="context-library/source-envelope", alias="schema"
    )
    schema_version: Literal[1] = 1
    external_id: str
    source_type: SourceType
    uri: str
    title: str
    author: Person | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    retrieved_at: datetime
    content_format: ContentFormat
    content: str
    secret_spans: list[SecretSpan] = Field(default_factory=list)
    retained_excerpts: list[RetainedExcerpt] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_spans_and_excerpts(self) -> "SourceEnvelope":
        spans = sorted(self.secret_spans, key=lambda item: item.start)
        for index, span in enumerate(spans):
            if span.end > len(self.content) or (index and span.start < spans[index - 1].end):
                raise ValueError("secret spans must be in-bounds and non-overlapping")
        redacted = redact(self.content, spans)
        for excerpt in self.retained_excerpts:
            if excerpt.excerpt not in redacted:
                raise ValueError("retained excerpt must be an exact substring of redacted content")
        return self


def redact(content: str, spans: list[SecretSpan]) -> str:
    if not spans:
        return content
    result: list[str] = []
    cursor = 0
    for span in sorted(spans, key=lambda item: item.start):
        result.append(content[cursor : span.start])
        result.append("[REDACTED]")
        cursor = span.end
    result.append(content[cursor:])
    return "".join(result)


ObservationKind = Literal[
    "directive", "rationale", "constraint", "question", "assumption", "implementation", "outcome", "context"
]
Provenance = Literal["explicit", "inferred", "assumed"]
Derivation = Literal["direct", "condensed", "synthesized"]


class Observation(StrictModel):
    schema_id: Literal["context-library/observation"] = Field(default="context-library/observation", alias="schema")
    schema_version: Literal[1] = 1
    source_id: str
    kind: ObservationKind
    excerpt: str
    location: str
    speaker: Person | None = None
    occurred_at: datetime | None = None
    agent_interpretation: str


class Applicability(StrictModel):
    provenance: Provenance
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_observation_ids: list[str] = Field(default_factory=list)
    reasoning: str


class Review(StrictModel):
    status: Literal["unreviewed", "reviewed", "ratified", "rejected"] = "unreviewed"
    reviewer: Person | None = None
    reviewed_at: datetime | None = None


class Candidate(StrictModel):
    schema_id: Literal["context-library/candidate"] = Field(default="context-library/candidate", alias="schema")
    schema_version: Literal[1] = 1
    project: str
    candidate_id: str = Field(pattern=r"^[a-z][a-z0-9-]{2,79}$")
    subject: str
    category: str
    decision: str
    constraint: str | None = None
    rationale: str
    decisionmaker: Person | None = None
    decision_at: datetime
    provenance: Provenance
    derivation: Derivation
    source_observation_ids: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    supersedes: list[str] = Field(default_factory=list)
    conflicts_with: list[str] = Field(default_factory=list)
    conflict_key: str | None = None
    applies_when: str | None = None
    affected_layers: list[str] = Field(default_factory=list)
    applicability: Applicability
    tags: list[str] = Field(default_factory=list)
    review: Review = Field(default_factory=Review)

    @model_validator(mode="after")
    def provenance_rules(self) -> "Candidate":
        if self.provenance == "explicit" and self.decisionmaker is None:
            raise ValueError("explicit candidates require a decisionmaker")
        if self.derivation == "synthesized" and not self.sources:
            raise ValueError("synthesized candidates require source decision IDs")
        if self.review.status != "unreviewed":
            raise ValueError("candidate add accepts only unreviewed candidates")
        if self.provenance == "assumed" and not self.rationale:
            raise ValueError("assumed candidates require rationale")
        return self


class Finding(StrictModel):
    schema_id: Literal["context-library/finding"] = Field(default="context-library/finding", alias="schema")
    schema_version: Literal[1] = 1
    finding: Literal["duplicate", "supersedes", "conflict", "related"]
    candidate_id: str
    canonical_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_observation_ids: list[str]
    reasoning: str

    @field_validator("evidence_observation_ids")
    @classmethod
    def needs_evidence(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("semantic findings require evidence")
        return value


class HarvestBatch(StrictModel):
    """Proposal-only transfer from a private context harvester to the library."""

    schema_id: Literal["context-library/harvest-batch"] = Field(default="context-library/harvest-batch", alias="schema")
    schema_version: Literal[1] = 1
    batch_id: str = Field(min_length=1, max_length=256)
    idempotency_key: str = Field(min_length=1, max_length=256)
    project: str = Field(min_length=1, max_length=256)
    produced_at: datetime
    redacted: Literal[True] = True
    canonical_write: Literal[False] = False
    sources: list[SourceEnvelope] = Field(default_factory=list)
    observations: list[Observation] = Field(default_factory=list)
    candidates: list[Candidate] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)

    @model_validator(mode="after")
    def references_are_local_and_proposal_only(self) -> "HarvestBatch":
        source_ids = {source.external_id for source in self.sources}
        if len(source_ids) != len(self.sources):
            raise ValueError("harvest sources must have unique external IDs")
        if any(source.secret_spans for source in self.sources):
            raise ValueError("harvest sources must already be redacted")
        if any(observation.source_id not in source_ids for observation in self.observations):
            raise ValueError("observations must reference a source in the batch")
        candidate_projects = {candidate.project for candidate in self.candidates}
        if candidate_projects and candidate_projects != {self.project}:
            raise ValueError("candidate projects must match the harvest project")
        candidate_ids = {candidate.candidate_id for candidate in self.candidates}
        if any(finding.candidate_id not in candidate_ids for finding in self.findings):
            raise ValueError("findings must reference a candidate in the batch")
        return self


class ConflictChoice(StrictModel):
    value: str
    label: str


class ConflictResolution(StrictModel):
    schema_id: Literal["context-library/conflict-resolution"] = Field(
        default="context-library/conflict-resolution", alias="schema"
    )
    schema_version: Literal[1] = 1
    choice: str
    rationale: str | None = None
    resolver: str
    resolved_at: datetime
    resolution_source_id: str | None = None
    resolution_candidate_id: str | None = None


class ConflictPacket(StrictModel):
    schema_id: Literal["context-library/conflict-packet"] = Field(
        default="context-library/conflict-packet", alias="schema"
    )
    schema_version: Literal[1] = 1
    conflict_id: str
    project: str
    status: Literal["open", "resolved"]
    created_at: datetime
    question: str
    candidate_ids: list[str]
    canonical_ids: list[str]
    reason: str
    choices: list[ConflictChoice]
    recommendation: str
    safe_behavior: str
    resolution: ConflictResolution | None = None


class ProjectPolicies(StrictModel):
    automatic_publication: bool = False
    minimum_routing_confidence: float = Field(default=0.85, ge=0.0, le=1.0)
    human_approval_categories: list[str] = Field(default_factory=list)
    retain_source_content: bool = True


class ProjectConfig(StrictModel):
    schema_id: Literal["context-library/project-config"] = Field(
        default="context-library/project-config", alias="schema"
    )
    schema_version: Literal[1] = 1
    project: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    display_name: str
    register_path: str = Field(default="decision-register.md", alias="register")
    topology: str = "topology.yaml"
    authority: str = "authority.yaml"
    policies: ProjectPolicies = Field(default_factory=ProjectPolicies)

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, populate_by_name=True)

    @property
    def register(self) -> str:
        return self.register_path


class Layer(StrictModel):
    description: str
    aliases: list[str] = Field(default_factory=list)
    repository_hints: list[str] = Field(default_factory=list)


class Topology(StrictModel):
    schema_id: Literal["context-library/topology"] = Field(default="context-library/topology", alias="schema")
    schema_version: Literal[1] = 1
    project: str
    layers: dict[str, Layer]

    @model_validator(mode="after")
    def product_layer(self) -> "Topology":
        if "product" not in self.layers:
            raise ValueError("topology must contain product layer")
        aliases: set[str] = set()
        for name, layer in self.layers.items():
            if not re.fullmatch(r"[a-z][a-z0-9-]*", name):
                raise ValueError(f"invalid layer identifier: {name}")
            for alias in layer.aliases:
                key = alias.lower()
                if key in aliases:
                    raise ValueError(f"duplicate topology alias: {alias}")
                aliases.add(key)
        return self


class Authority(StrictModel):
    identity: str
    display_name: str
    precedence: int
    categories: list[str] = Field(default_factory=list)


class AuthorityPolicy(StrictModel):
    schema_id: Literal["context-library/authority-policy"] = Field(
        default="context-library/authority-policy", alias="schema"
    )
    schema_version: Literal[1] = 1
    project: str
    default_precedence: int = 0
    authorities: list[Authority] = Field(default_factory=list)
    category_owners: dict[str, str] = Field(default_factory=dict)


class Response(StrictModel):
    schema_id: Literal["context-library/maintainer-command"] = Field(
        default="context-library/maintainer-command", alias="schema"
    )
    schema_version: Literal[1] = 1
    command: str
    status: Literal["ok", "pending", "error"]
    run_id: str
    data: dict[str, Any] = Field(default_factory=dict)
    errors: list[dict[str, str]] = Field(default_factory=list)
