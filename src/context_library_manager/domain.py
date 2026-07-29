from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Envelope(StrictModel):
    schema_version: Literal[1] = 1
    request_id: str
    status: Literal["ok", "error", "pending"] = "ok"
    data: dict[str, Any] = Field(default_factory=dict)
    errors: list[dict[str, str]] = Field(default_factory=list)


class Source(StrictModel):
    external_id: str = Field(min_length=1, max_length=256)
    source_type: Literal[
        "ticket",
        "documentation",
        "project-note",
        "chat",
        "meeting",
        "idea",
        "code",
        "commit",
        "other",
    ]
    uri: str = Field(min_length=1, max_length=2048)
    title: str = Field(default="", max_length=500)
    content: str = Field(min_length=1, max_length=1_000_000)
    retrieved_at: datetime
    content_format: str = "text"

    @field_validator("retrieved_at")
    @classmethod
    def utc(cls, value: datetime) -> datetime:
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)


class RouteRequest(StrictModel):
    operation: Literal["source", "observation", "candidate", "relationship", "publication"]
    semantic_fields: list[str] = Field(default_factory=list)
    category: str | None = None
    input_tokens: int = Field(default=0, ge=0)


class RouteDecision(StrictModel):
    route: Literal["deterministic", "agent", "review"]
    reason: str
    deterministic_checks: list[str]
    estimated_input_tokens: int
    profile: Literal["cheap", "standard", "review"] | None = None


class ResolveRequest(StrictModel):
    choice: str = Field(min_length=1, max_length=100)
    rationale: str = Field(min_length=1, max_length=10_000)
    idempotency_key: str = Field(min_length=1, max_length=256)


class ReviewCreate(StrictModel):
    work_id: str
    question: str = Field(min_length=1, max_length=1000)
    choices: list[str] = Field(min_length=2, max_length=4)
    evidence: list[str] = Field(default_factory=list)


class Contribution(StrictModel):
    kind: Literal["observation", "candidate", "finding", "applicability"]
    payload: dict[str, Any]
    evidence_references: list[str] = Field(default_factory=list)
    agent_identity: str | None = None
    client_idempotency_key: str = Field(min_length=1, max_length=256)
    token_metadata: dict[str, Any] = Field(default_factory=dict)


class AgentBudget(StrictModel):
    max_input_tokens: int = Field(ge=0)
    max_output_tokens: int = Field(ge=0)


class AgentRequest(StrictModel):
    schema_version: Literal[1] = 1
    run_id: str
    project: str
    task_type: str
    actor: str
    model_profile: Literal["cheap", "standard", "review"]
    budget: AgentBudget
    prompt_revision: str
    evidence: list[dict[str, str]] = Field(default_factory=list)
    task_context: dict[str, Any] = Field(default_factory=dict)
    required_output_schema: str


class AgentUsage(StrictModel):
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    estimated_cost: float = Field(default=0.0, ge=0)


class AgentResponse(StrictModel):
    schema_version: Literal[1]
    run_id: str
    status: Literal["ok", "error"]
    result: dict[str, Any] = Field(default_factory=dict)
    usage: AgentUsage = Field(default_factory=AgentUsage)
    confidence: float | None = Field(default=None, ge=0, le=1)
    warnings: list[str] = Field(default_factory=list)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
