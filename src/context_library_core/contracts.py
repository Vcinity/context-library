from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_FAMILIES: dict[str, list[int]] = {
    "context-library/agent-provider-request": [1],
    "context-library/agent-provider-response": [1],
    "context-library/applicability": [1],
    "context-library/applicability-result": [1],
    "context-library/candidate": [1],
    "context-library/capabilities": [1],
    "context-library/configuration-draft": [1],
    "context-library/configuration-rollback": [1],
    "context-library/context-policy": [1],
    "context-library/context-resolution": [1],
    "context-library/decision-audit-response": [1],
    "context-library/conflict-packet": [1],
    "context-library/conflict-resolution": [1],
    "context-library/finding": [1],
    "context-library/harvest-batch": [1],
    "context-library/maintainer-command": [1],
    "context-library/manager-proposal": [1],
    "context-library/manager-query": [1],
    "context-library/missing-context-notice": [1],
    "context-library/observation": [1],
    "context-library/retrieval-benchmark-gold": [1],
    "context-library/retrieval-benchmark-report": [1],
    "context-library/retrieval-benchmark-task": [1],
    "context-library/search-decisions-response": [1],
    "context-library/source-envelope": [1],
    "context-library/version": [1],
}


class Contract(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        populate_by_name=True,
        serialize_by_alias=True,
    )


class ContextRequirement(StrEnum):
    REQUIRED = "required"
    OPTIONAL = "optional"
    DISABLED = "disabled"
    UNDETERMINED = "undetermined"


class ContextAvailability(StrEnum):
    AVAILABLE = "available"
    MISSING = "missing"
    UNREADABLE = "unreadable"
    INVALID = "invalid"
    AMBIGUOUS = "ambiguous"
    STALE_PROJECTION = "stale-projection"


class ContextPolicy(Contract):
    schema_id: Literal["context-library/context-policy"] = Field(alias="schema")
    schema_version: Literal[1]
    project: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9-]*$")
    context_requirement: Literal["required", "optional", "disabled"]
    affected_layers: dict[str, str] = Field(default_factory=dict)


class ContextResolution(Contract):
    schema_id: Literal["context-library/context-resolution"] = Field(
        default="context-library/context-resolution", alias="schema"
    )
    schema_version: Literal[1] = 1
    project: str | None = None
    requirement: ContextRequirement
    requirement_source: str | None = None
    availability: ContextAvailability
    source_digest: str | None = None
    projection_fresh: bool | None = None
    notice_emitted: bool = False
    proceeded_without_context: bool = False


class MissingContextNotice(Contract):
    schema_id: Literal["context-library/missing-context-notice"] = Field(
        default="context-library/missing-context-notice", alias="schema"
    )
    schema_version: Literal[1] = 1
    project: str | None = None
    requirement_source: str | None = None
    classification: Literal["missing", "unreadable", "invalid", "ambiguous", "stale-projection"]
    affected_action: str
    fabricated_substitute: Literal[False] = False
    invitation: str = "Provide relevant context if you want it applied."
    remediation: str = "Use the Context Library Manager to create or update canonical context."


class ApplicabilityState(StrEnum):
    UNCONDITIONAL = "unconditional"
    SATISFIED = "satisfied"
    UNSATISFIED = "unsatisfied"
    UNDETERMINED = "undetermined"


def _scope_values(values: list[str]) -> list[str]:
    normalized: list[str] = []
    for value in values:
        if not isinstance(value, str) or not value or value.startswith("/") or "\\" in value:
            raise ValueError("repository scopes must be non-empty relative paths")
        parts = value.split("/")
        if any(part in {"", ".", ".."} for part in parts):
            raise ValueError("repository scopes must not contain empty or traversal components")
        if value not in normalized:
            normalized.append(value)
    if len(normalized) != len(values):
        raise ValueError("repository scopes must be unique")
    return normalized


class ApplicabilityTask(Contract):
    repository_scopes: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_scopes(self) -> "ApplicabilityTask":
        _scope_values(self.repository_scopes)
        return self


class ApplicabilityDecision(Contract):
    decision_id: str = Field(min_length=1, max_length=256)
    repository_scopes: list[str] = Field(default_factory=list, max_length=100)
    provenance: Literal["explicit", "inferred", "assumed"]
    effective_provenance: Literal["explicit", "inferred", "assumed"]
    source_scope: str = Field(min_length=1, max_length=512)
    supersedes: list[str] = Field(default_factory=list, max_length=1000)
    conflict_ids: list[str] = Field(default_factory=list, max_length=1000)
    applies_when: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def validate_decision(self) -> "ApplicabilityDecision":
        _scope_values(self.repository_scopes)
        if len(self.supersedes) != len(set(self.supersedes)):
            raise ValueError("supersession references must be unique")
        if len(self.conflict_ids) != len(set(self.conflict_ids)):
            raise ValueError("conflict references must be unique")
        return self


class ApplicabilityRequest(Contract):
    schema_id: Literal["context-library/applicability"] = Field(default="context-library/applicability", alias="schema")
    schema_version: Literal[1] = 1
    task: ApplicabilityTask
    decision: ApplicabilityDecision


class ApplicabilityResult(Contract):
    schema_id: Literal["context-library/applicability-result"] = Field(
        default="context-library/applicability-result", alias="schema"
    )
    schema_version: Literal[1] = 1
    decision_id: str
    state: ApplicabilityState
    matched_selectors: dict[str, list[str]] = Field(default_factory=dict)
    required_selectors: list[str] = Field(default_factory=list)
    reason: Literal["none", "scope-mismatch", "missing-task-signal", "conditional-unresolved"]
    provenance: Literal["explicit", "inferred", "assumed"]
    effective_provenance: Literal["explicit", "inferred", "assumed"]
    source_scope: str
    supersedes: list[str] = Field(default_factory=list)
    conflict_ids: list[str] = Field(default_factory=list)


class DecisionAuditApplicability(Contract):
    state: ApplicabilityState
    reason: Literal["none", "scope-mismatch", "missing-task-signal", "conditional-unresolved"]
    matched_selectors: dict[str, list[str]] = Field(default_factory=dict)
    required_selectors: list[str] = Field(default_factory=list)


class DecisionAuditRecord(Contract):
    decision_id: str = Field(min_length=1, max_length=256)
    subject: str = Field(min_length=1, max_length=4000)
    category: str = Field(min_length=1, max_length=512)
    decision: str = Field(min_length=1, max_length=4000)
    constraints: list[str] = Field(default_factory=list, max_length=1000)
    rationale: str | None = Field(default=None, max_length=20_000)
    evidence: list[str] = Field(default_factory=list, max_length=1000)
    provenance: Literal["explicit", "inferred", "assumed"]
    effective_provenance: Literal["explicit", "inferred", "assumed"]
    derivation: Literal["direct", "condensed", "synthesized"]
    source_ids: list[str] = Field(default_factory=list, max_length=1000)
    source_scope: str = Field(min_length=1, max_length=512)
    supersedes: list[str] = Field(default_factory=list, max_length=1000)
    conflict_ids: list[str] = Field(default_factory=list, max_length=1000)
    conflict_key: str | None = Field(default=None, max_length=512)
    affected_layers: list[str] = Field(default_factory=list, max_length=1000)
    applies_when: str | None = Field(default=None, max_length=2000)
    confidence: str | None = Field(default=None, max_length=512)
    review: str | None = Field(default=None, max_length=512)
    applicability: DecisionAuditApplicability


class DecisionAuditResponse(Contract):
    schema_id: Literal["context-library/decision-audit-response"] = Field(
        default="context-library/decision-audit-response", alias="schema"
    )
    schema_version: Literal[1] = 1
    project: str
    revision: str = Field(min_length=1, max_length=256)
    records: list[DecisionAuditRecord] = Field(min_length=1, max_length=100)


class VersionEnvelope(Contract):
    schema_id: Literal["context-library/version"] = Field(alias="schema")
    schema_version: Literal[1]
    product_version: str


class CapabilityEnvelope(Contract):
    schema_id: Literal["context-library/capabilities"] = Field(alias="schema")
    schema_version: Literal[1]
    product_version: str
    schema_families: dict[str, list[int]]
    canonical_layout_versions: list[str]
    features: dict[str, bool]


class ErrorDetail(Contract):
    code: str
    message: str
    field: str | None = None


class CommandEnvelope(Contract):
    schema_id: Literal["context-library/maintainer-command"] = Field(
        default="context-library/maintainer-command", alias="schema"
    )
    schema_version: Literal[1] = 1
    command: str
    status: Literal["ok", "pending", "error"]
    run_id: str
    data: dict[str, Any] = Field(default_factory=dict)
    errors: list[ErrorDetail] = Field(default_factory=list)


class AgentBudget(Contract):
    max_input_tokens: int = Field(ge=0)
    max_output_tokens: int = Field(ge=0)


class AgentEvidence(Contract):
    observation_id: str
    excerpt: str = Field(max_length=1000)
    location: str


class AgentProviderRequest(Contract):
    schema_id: Literal["context-library/agent-provider-request"] = Field(alias="schema")
    schema_version: Literal[1]
    run_id: str
    project: str
    task_type: str
    actor: str
    model_profile: Literal["cheap", "standard", "review"]
    budget: AgentBudget
    prompt_revision: str
    evidence: list[AgentEvidence]
    required_output_schema: str
    deterministic_checks: list[str]
    cache_check: Literal["miss"]
    invocation_reason: str


class AgentUsage(Contract):
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    estimated_cost: float = Field(ge=0)


class AgentProviderResponse(Contract):
    schema_id: Literal["context-library/agent-provider-response"] = Field(alias="schema")
    schema_version: Literal[1]
    run_id: str
    status: Literal["ok", "error"]
    result: dict[str, Any]
    usage: AgentUsage
    confidence: float = Field(ge=0, le=1)
    warnings: list[str] = Field(default_factory=list)


class SearchDecisionMatch(Contract):
    decision_id: str = Field(min_length=1, max_length=256)
    subject: str = Field(min_length=1, max_length=4000)
    excerpt: str = Field(min_length=1, max_length=4000)
    provenance: Literal["explicit", "inferred", "assumed"]
    match_mode: Literal["exact", "lexical"]
    matched_terms: list[str] = Field(max_length=1000)
    superseded: list[str] = Field(default_factory=list, max_length=1000)
    superseded_by: list[str] = Field(default_factory=list, max_length=1000)
    applicability: Literal["unconditional", "satisfied", "unsatisfied", "undetermined"]


class SearchDecisionsResponse(Contract):
    schema_id: Literal["context-library/search-decisions-response"] = Field(
        default="context-library/search-decisions-response", alias="schema"
    )
    schema_version: Literal[1] = 1
    project: str
    query: str
    path: str
    matches: list[SearchDecisionMatch] = Field(default_factory=list)
    diagnostic: Literal["exact", "lexical", "no-match"]
    truncated: bool
    total_matches: int = Field(ge=0)
