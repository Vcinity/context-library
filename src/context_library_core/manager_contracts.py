from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Annotated, Any, Generic, Literal, TypeVar

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    GetJsonSchemaHandler,
    model_serializer,
    model_validator,
)
from pydantic.json_schema import JsonSchemaValue


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    @model_validator(mode="before")
    @classmethod
    def normalize_schema_key(cls, value: Any) -> Any:
        if "schema_id" not in cls.model_fields or not isinstance(value, dict) or "schema" not in value:
            return value
        normalized = dict(value)
        schema = normalized.pop("schema")
        if "schema_id" in normalized and normalized["schema_id"] != schema:
            raise ValueError("schema and schema_id disagree")
        normalized["schema_id"] = schema
        return normalized

    @model_serializer(mode="wrap")
    def serialize_schema_key(self, handler: Any) -> dict[str, Any]:
        data = handler(self)
        if "schema_id" in data:
            data["schema"] = data.pop("schema_id")
        return data

    @classmethod
    def __get_pydantic_json_schema__(
        cls,
        core_schema: Any,
        handler: GetJsonSchemaHandler,
    ) -> JsonSchemaValue:
        schema = handler(core_schema)
        properties = schema.get("properties", {})
        if "schema_id" in properties:
            properties["schema"] = properties.pop("schema_id")
        required = schema.get("required", [])
        schema["required"] = ["schema" if field == "schema_id" else field for field in required]
        return schema


class Capability(StrEnum):
    READ = "read"
    REVIEW = "review"
    MAINTAIN = "maintain"
    ADMIN = "admin"


class ContentStatus(StrEnum):
    AUTHORITATIVE = "authoritative"
    INFERRED = "inferred"
    ASSUMED = "assumed"
    PENDING = "pending"
    SUPERSEDED = "superseded"
    EXCLUDED = "excluded"


class ServiceState(StrEnum):
    RUNNING = "running"
    PAUSED = "paused"
    DRAINING = "draining"


class HealthState(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    OFFLINE = "offline"


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return value.astimezone(timezone.utc)


AwareUTC = Annotated[datetime, AfterValidator(_utc)]


class AwareTimestamp(Contract):
    at: AwareUTC


class SessionIdentity(Contract):
    subject: str = Field(min_length=1, max_length=512)
    display_name: str = Field(min_length=1, max_length=512)
    capabilities: list[Capability] = Field(min_length=1)
    allowed_projects: list[str] = Field(min_length=1)
    selected_project: str | None = None
    issued_at: AwareUTC
    expires_at: AwareUTC
    oidc_session_reference: str | None = Field(default=None, max_length=1024)
    csrf_token: str | None = Field(default=None, min_length=32, max_length=512)


class DevelopmentLogin(Contract):
    subject: str = Field(min_length=1, max_length=512)
    display_name: str = Field(min_length=1, max_length=512)
    capabilities: list[str] = Field(min_length=1, max_length=4)
    projects: list[str] = Field(min_length=1, max_length=100)
    selected_project: str | None = None


class CSRFIntent(Contract):
    method: Literal["POST", "PUT", "PATCH", "DELETE"]
    path: str = Field(pattern=r"^/[A-Za-z0-9_./{}:-]+$", max_length=2048)


class SessionProjectSelection(Contract):
    project: str = Field(min_length=1, max_length=256, pattern=r"^[A-Za-z0-9_.-]+$")


T = TypeVar("T")


class Page(Contract, Generic[T]):
    items: list[T]
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total: int = Field(ge=0)
    next_page: int | None = Field(default=None, ge=2)
    filters: dict[str, list[str]] = Field(default_factory=dict)


class SourceReference(Contract):
    uri: str = Field(min_length=1, max_length=2048)
    label: str = Field(default="", max_length=512)
    observed_at: AwareUTC | None = None
    redacted: bool = False
    secret_state: Literal["none", "configured", "redacted"] = "none"


class DecisionSummary(Contract):
    decision_id: str = Field(min_length=1, max_length=256)
    subject: str = Field(min_length=1, max_length=1000)
    decision: str = Field(min_length=1, max_length=20_000)
    rationale: str = Field(default="", max_length=20_000)
    category: str = Field(default="uncategorized", max_length=256)
    provenance: Literal["explicit", "inferred", "assumed"]
    status: ContentStatus
    source_count: int = Field(default=0, ge=0)
    source_scope: str = Field(default="", max_length=512)
    source_project: str = Field(default="", max_length=256)
    source_digest: str = Field(default="", max_length=128)
    publication_revision: str
    library_digest: str = Field(min_length=16, max_length=128)


class DecisionDetail(DecisionSummary):
    decisionmaker: str = Field(default="", max_length=1000)
    decision_date: str = Field(default="", max_length=256)
    sources: list[SourceReference] = Field(default_factory=list)
    supersedes: list[str] = Field(default_factory=list)
    superseded_by: list[str] = Field(default_factory=list)
    related_decisions: list[str] = Field(default_factory=list)
    open_proposals: list[str] = Field(default_factory=list)
    open_reviews: list[str] = Field(default_factory=list)


class LibrarySnapshot(Contract):
    project: str
    library_digest: str = Field(min_length=16, max_length=128)
    publication_revision: str


class QueryEnvelope(Contract):
    schema_id: Literal["context-library/manager-query"] = "context-library/manager-query"
    schema_version: Literal[1] = 1
    command: Literal["query"]
    status: Literal["ok", "error"]
    run_id: str
    data: dict[str, Any]
    errors: list[dict[str, str]] = Field(default_factory=list)


class ProposalOperation(StrEnum):
    CREATE = "create"
    REVISE = "revise"
    SUPERSEDE = "supersede"
    EXCLUDE = "exclude"


class ProposalDraft(Contract):
    operation: ProposalOperation
    decision_id: str | None = Field(default=None, max_length=256)
    proposed_fields: dict[str, Any] = Field(min_length=1)
    rationale: str = Field(min_length=1, max_length=20_000)
    evidence_references: list[str] = Field(min_length=1, max_length=100)
    authority: str = Field(min_length=1, max_length=1000)
    publication_intent: bool
    library_digest: str = Field(min_length=16, max_length=128)


class ProposalPreview(Contract):
    deterministic_checks: list[str]
    route: Literal["deterministic", "agent", "review"]
    estimated_input_tokens: int = Field(ge=0)
    estimated_max_cost: float = Field(ge=0)
    review_required: bool
    current_library_digest: str = Field(min_length=16, max_length=128)
    stale_source: bool


class ProposalSubmission(ProposalDraft):
    schema_id: Literal["context-library/manager-proposal"] = "context-library/manager-proposal"
    schema_version: Literal[1] = 1
    idempotency_key: str = Field(min_length=1, max_length=256)


class ProposalLifecycle(Contract):
    proposal_id: str
    work_id: str
    state: Literal[
        "queued",
        "leased",
        "running",
        "waiting-human",
        "retryable",
        "failed",
        "succeeded",
        "cancel-requested",
        "canceled",
    ]
    created_at: AwareUTC
    updated_at: AwareUTC
    review_id: str | None = None
    publication_id: str | None = None


class AgentServiceStatus(Contract):
    state: ServiceState
    health: HealthState
    version: int = Field(ge=1)
    last_heartbeat: AwareUTC | None = None
    active_work_id: str | None = None
    queue_counts: dict[str, int] = Field(default_factory=dict)
    project_token_budget: int = Field(ge=0)
    project_tokens_used: int = Field(ge=0)
    last_success: AwareUTC | None = None
    recent_failures: list[str] = Field(default_factory=list)


class AgentServiceControl(Contract):
    schema_id: Literal["context-library/agent-service-control"] = "context-library/agent-service-control"
    schema_version: Literal[1] = 1
    expected_version: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=2000)
    idempotency_key: str = Field(min_length=1, max_length=256)


class AgentRunCancellation(Contract):
    schema_id: Literal["context-library/agent-run-cancellation"] = "context-library/agent-run-cancellation"
    schema_version: Literal[1] = 1
    reason: str = Field(min_length=1, max_length=2000)
    idempotency_key: str = Field(min_length=1, max_length=256)


class AgentServiceControlResult(Contract):
    previous_state: ServiceState
    state: ServiceState
    version: int = Field(ge=1)
    idempotent: bool = False


class AgentRunSummary(Contract):
    run_id: str
    work_id: str
    project: str
    status: str
    profile: str
    cache_hit: bool
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cost: float = Field(ge=0)
    started_at: AwareUTC | None = None
    finished_at: AwareUTC | None = None


class AgentRunDetail(AgentRunSummary):
    prompt_revision: str
    provider: str
    evidence_references: list[str] = Field(default_factory=list)
    routing_checks: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    timeline: list[dict[str, Any]] = Field(default_factory=list)
    structured_result: dict[str, Any] = Field(default_factory=dict)
    raw_payload_state: Literal["unavailable", "redacted", "available"] = "redacted"


class EffectiveField(Contract):
    value: Any | None = None
    source: Literal["default", "project-file", "environment", "secret-store"]
    editable: bool
    secret_state: Literal["none", "configured", "not-configured", "redacted"]
    restart_required: bool
    constraints: dict[str, Any] = Field(default_factory=dict)


class ConfigurationDraft(Contract):
    schema_id: Literal["context-library/configuration-draft"] = "context-library/configuration-draft"
    schema_version: Literal[1] = 1
    expected_revision: int = Field(ge=1)
    changes: dict[str, Any] = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=2000)
    idempotency_key: str = Field(min_length=1, max_length=256)


class ConfigurationRollback(Contract):
    schema_id: Literal["context-library/configuration-rollback"] = "context-library/configuration-rollback"
    schema_version: Literal[1] = 1
    expected_revision: int = Field(ge=1)
    target_revision: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=2000)
    idempotency_key: str = Field(min_length=1, max_length=256)


class ConfigurationImpact(Contract):
    valid: bool
    affected_queues: list[str] = Field(default_factory=list)
    budget_effects: list[str] = Field(default_factory=list)
    cache_invalidated: bool = False
    restart_required: bool = False
    errors: list[dict[str, str]] = Field(default_factory=list)


class ConfigurationRevision(Contract):
    revision: int = Field(ge=1)
    project: str
    actor: str
    reason: str
    values: dict[str, EffectiveField]
    created_at: AwareUTC
    rolled_back_from: int | None = Field(default=None, ge=1)


class ProcessHeartbeat(Contract):
    process: Literal["api", "scheduler", "worker", "notification", "reconciliation"]
    instance_id: str
    state: HealthState
    observed_at: AwareUTC
    details: dict[str, Any] = Field(default_factory=dict)


class RuntimeHealth(Contract):
    status: HealthState
    database: Literal["sqlite", "postgresql"]
    heartbeats: list[ProcessHeartbeat]
    active_leases: int = Field(ge=0)
    retry_backlog: int = Field(ge=0)
    notification_failures: int = Field(ge=0)
    configuration_warnings: list[str] = Field(default_factory=list)


class AuditResult(Contract):
    audit_id: str
    actor: str
    action: str
    project: str | None
    capability: Capability | None = None
    work_id: str | None = None
    run_id: str | None = None
    policy_revision: int | None = Field(default=None, ge=1)
    before_reference: str | None = None
    after_reference: str | None = None
    created_at: AwareUTC


class ReviewSummary(Contract):
    review_id: str
    project: str
    work_id: str
    status: Literal["open", "resolved", "stale"]
    question: str
    created_at: AwareUTC
    updated_at: AwareUTC


class NotificationSummary(Contract):
    notification_id: str
    review_id: str
    status: Literal["pending", "delivered", "failed"]
    attempts: int = Field(ge=0)
    created_at: AwareUTC
