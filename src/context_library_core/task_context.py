from __future__ import annotations

import hashlib
from typing import Literal

import tiktoken
from pydantic import ConfigDict, Field, model_validator

from .contracts import ApplicabilityState, Contract
from .retrieval_contracts import TokenizerIdentity


class TaskContextRequest(Contract):
    schema_id: Literal["context-library/task-context-request"] = Field(
        default="context-library/task-context-request", alias="schema"
    )
    schema_version: Literal[1] = 1
    project: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    task_summary: str = Field(min_length=1, max_length=4000)
    operation: str = Field(min_length=1, max_length=256)
    repository_scopes: list[str] = Field(min_length=1, max_length=100)
    agent_token_budget: int = Field(ge=0)
    tokenizer: TokenizerIdentity


class TaskContextItem(Contract):
    decision_id: str = Field(min_length=1, max_length=256)
    text: str = Field(min_length=1, max_length=4000)
    state: ApplicabilityState
    provenance: Literal["explicit", "inferred", "assumed"]
    effective_provenance: Literal["explicit", "inferred", "assumed"]
    source_scope: str = Field(min_length=1, max_length=512)
    supersedes: list[str] = Field(default_factory=list)
    conflict_ids: list[str] = Field(default_factory=list)


class TaskContextCoverage(Contract):
    operative_expected: int = Field(ge=0)
    operative_included: int = Field(ge=0)
    omitted_operative_decision_ids: list[str] = Field(default_factory=list)
    complete: bool
    budget_status: Literal["verified", "unverified"]


class TaskContextTruncation(Contract):
    truncated: bool
    reason: Literal["none", "token-budget"] = "none"
    omitted_operative_decision_ids: list[str] = Field(default_factory=list)


class CapsuleAccounting(Contract):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=False,
        populate_by_name=True,
        serialize_by_alias=True,
    )
    serialized_content: str
    utf8_byte_count: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    token_count: int = Field(ge=0)
    tokenizer: TokenizerIdentity
    budget_status: Literal["verified", "unverified"]


class TaskContextResponse(Contract):
    schema_id: Literal["context-library/task-context-response"] = Field(
        default="context-library/task-context-response", alias="schema"
    )
    schema_version: Literal[1] = 1
    project: str
    revision: str = Field(min_length=1, max_length=256)
    operative_directives: list[TaskContextItem] = Field(default_factory=list)
    applicability_uncertainties: list[TaskContextItem] = Field(default_factory=list)
    non_operative_directives: list[TaskContextItem] = Field(default_factory=list)
    applicable_conflicts: list[str] = Field(default_factory=list)
    coverage: TaskContextCoverage
    truncation: TaskContextTruncation
    agent_visible_capsule: CapsuleAccounting

    @model_validator(mode="after")
    def truthful_coverage(self) -> "TaskContextResponse":
        if self.coverage.complete and self.coverage.omitted_operative_decision_ids:
            raise ValueError("complete coverage cannot omit operative decisions")
        if not self.truncation.truncated and self.truncation.omitted_operative_decision_ids:
            raise ValueError("omitted operative IDs require truncation")
        return self


def _capsule(project: str, revision: str, items: list[TaskContextItem]) -> str:
    lines = [f"# Task context: {project}", f"revision: {revision}", "", "## Operative directives"]
    lines.extend(f"- [{item.decision_id}] {item.text}" for item in items)
    return "\n".join(lines) + "\n"


def render_task_context(
    request: TaskContextRequest,
    items: list[TaskContextItem],
    *,
    revision: str,
) -> TaskContextResponse:
    if len({item.decision_id for item in items}) != len(items):
        raise ValueError("task-context decision IDs must be unique")
    ordered = sorted(items, key=lambda item: (item.state.value, item.decision_id, item.source_scope))
    operative = [
        item for item in ordered if item.state in {ApplicabilityState.UNCONDITIONAL, ApplicabilityState.SATISFIED}
    ]
    uncertainties = [item for item in ordered if item.state == ApplicabilityState.UNDETERMINED]
    non_operative = [item for item in ordered if item.state == ApplicabilityState.UNSATISFIED]
    budget_status = (
        "verified"
        if request.tokenizer.name == "tiktoken"
        and request.tokenizer.version == "0.9.0"
        and request.tokenizer.vocabulary_revision == "cl100k_base"
        else "unverified"
    )
    tokenizer = tiktoken.get_encoding("cl100k_base") if budget_status == "verified" else None
    capsule = _capsule(request.project, revision, operative)
    token_count = len(tokenizer.encode(capsule)) if tokenizer else 0
    omitted = []
    if token_count > request.agent_token_budget:
        omitted = [item.decision_id for item in operative]
        capsule = ""
        token_count = 0
    encoded = capsule.encode("utf-8")
    truncation = TaskContextTruncation(
        truncated=bool(omitted),
        reason="token-budget" if omitted else "none",
        omitted_operative_decision_ids=omitted,
    )
    return TaskContextResponse(
        project=request.project,
        revision=revision,
        operative_directives=operative,
        applicability_uncertainties=uncertainties,
        non_operative_directives=non_operative,
        applicable_conflicts=sorted({conflict for item in operative + uncertainties for conflict in item.conflict_ids}),
        coverage=TaskContextCoverage(
            operative_expected=len(operative),
            operative_included=len(operative) - len(omitted),
            omitted_operative_decision_ids=omitted,
            complete=not omitted,
            budget_status=budget_status,
        ),
        truncation=truncation,
        agent_visible_capsule=CapsuleAccounting(
            serialized_content=capsule,
            utf8_byte_count=len(encoded),
            sha256=hashlib.sha256(encoded).hexdigest(),
            token_count=token_count,
            tokenizer=request.tokenizer,
            budget_status=budget_status,
        ),
    )
