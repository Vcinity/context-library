from __future__ import annotations

import hashlib
import math
from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from context_library_core.contracts import Contract


class RetrievalClassification(StrEnum):
    OPERATIVE = "operative"
    JUDGMENT_REQUIRED = "judgment-required"
    EXCLUDED = "excluded"


class ExclusionReason(StrEnum):
    NON_AUTHORITATIVE = "non-authoritative"
    SUPERSEDED = "superseded"
    OUT_OF_SCOPE = "out-of-scope"
    INAPPLICABLE = "inapplicable"
    DUPLICATE = "duplicate"
    OTHER_REVIEWED = "other-reviewed"


class ConflictReference(Contract):
    conflict_id: str = Field(min_length=1, max_length=256)
    member_decision_ids: list[str] = Field(min_length=2, max_length=100)

    @model_validator(mode="after")
    def unique_members(self) -> "ConflictReference":
        if len(self.member_decision_ids) != len(set(self.member_decision_ids)):
            raise ValueError("conflict members must be unique")
        return self


class RetrievalBenchmarkTask(Contract):
    schema_id: Literal["context-library/retrieval-benchmark-task"] = Field(
        default="context-library/retrieval-benchmark-task", alias="schema"
    )
    schema_version: Literal[1] = 1
    task_id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,127}$")
    task_revision: str = Field(min_length=1, max_length=128)
    summary: str = Field(min_length=1, max_length=4000)
    operation: str = Field(min_length=1, max_length=256)
    repository_scopes: list[str] = Field(min_length=1, max_length=100)
    expected_operative_decision_ids: list[str] = Field(default_factory=list, max_length=1000)
    judgment_required_decision_ids: list[str] = Field(default_factory=list, max_length=1000)
    excluded_decision_ids: list[str] = Field(default_factory=list, max_length=1000)
    applicable_conflicts: list[ConflictReference] = Field(default_factory=list, max_length=100)
    complete_coverage_possible: bool
    gold_revision: str = Field(min_length=1, max_length=128)
    gold_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_classifications(self) -> "RetrievalBenchmarkTask":
        groups = {
            "operative": self.expected_operative_decision_ids,
            "judgment-required": self.judgment_required_decision_ids,
            "excluded": self.excluded_decision_ids,
        }
        seen: dict[str, str] = {}
        for classification, decision_ids in groups.items():
            if len(decision_ids) != len(set(decision_ids)):
                raise ValueError(f"{classification} decision IDs must be unique")
            for decision_id in decision_ids:
                if decision_id in seen:
                    raise ValueError(f"decision {decision_id!r} has multiple classifications")
                seen[decision_id] = classification
        for conflict in self.applicable_conflicts:
            unknown = set(conflict.member_decision_ids) - set(seen)
            if unknown:
                raise ValueError(
                    f"conflict {conflict.conflict_id!r} references unknown decisions: "
                    + ", ".join(sorted(unknown))
                )
        if not self.complete_coverage_possible and not self.judgment_required_decision_ids:
            raise ValueError(
                "an incomplete-coverage task must identify judgment-required decisions"
            )
        return self


class GoldDecisionLabel(Contract):
    decision_id: str = Field(min_length=1, max_length=256)
    classification: RetrievalClassification
    exclusion_reason: ExclusionReason | None = None
    conflict_ids: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_exclusion(self) -> "GoldDecisionLabel":
        if self.classification == RetrievalClassification.EXCLUDED and self.exclusion_reason is None:
            raise ValueError("excluded gold labels require a structured exclusion reason")
        if self.classification != RetrievalClassification.EXCLUDED and self.exclusion_reason is not None:
            raise ValueError("only excluded gold labels may have an exclusion reason")
        return self


class RetrievalBenchmarkGold(Contract):
    schema_id: Literal["context-library/retrieval-benchmark-gold"] = Field(
        default="context-library/retrieval-benchmark-gold", alias="schema"
    )
    schema_version: Literal[1] = 1
    task_id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,127}$")
    task_revision: str = Field(min_length=1, max_length=128)
    gold_revision: str = Field(min_length=1, max_length=128)
    gold_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    labels: list[GoldDecisionLabel] = Field(min_length=1, max_length=10000)
    conflicts: list[ConflictReference] = Field(default_factory=list, max_length=1000)

    @model_validator(mode="after")
    def validate_gold(self) -> "RetrievalBenchmarkGold":
        ids = [label.decision_id for label in self.labels]
        if len(ids) != len(set(ids)):
            raise ValueError("gold decision IDs must be unique")
        conflict_ids = [conflict.conflict_id for conflict in self.conflicts]
        if len(conflict_ids) != len(set(conflict_ids)):
            raise ValueError("gold conflict IDs must be unique")
        known = set(ids)
        for conflict in self.conflicts:
            unknown = set(conflict.member_decision_ids) - known
            if unknown:
                raise ValueError(
                    f"conflict {conflict.conflict_id!r} references unknown decisions: "
                    + ", ".join(sorted(unknown))
                )
        known_conflicts = set(conflict_ids)
        for label in self.labels:
            unknown = set(label.conflict_ids) - known_conflicts
            if unknown:
                raise ValueError(
                    f"decision {label.decision_id!r} references unknown conflicts: "
                    + ", ".join(sorted(unknown))
                )
        return self


class TokenizerIdentity(Contract):
    pinned: Literal[True] = True
    name: str = Field(min_length=1, max_length=256)
    version: str = Field(min_length=1, max_length=128)
    vocabulary_revision: str = Field(min_length=1, max_length=128)
    accounting_method: str = Field(min_length=1, max_length=512)


class BaselineComparison(Contract):
    baseline_id: str = Field(min_length=1, max_length=256)
    baseline_agent_visible_tokens: int = Field(gt=0)
    reduction_tokens: int
    relative_reduction: float = Field(ge=-1, le=1)

    @model_validator(mode="after")
    def reductions_are_deterministic(self) -> "BaselineComparison":
        if self.reduction_tokens < 0:
            raise ValueError("reduction_tokens must not be negative")
        expected = self.reduction_tokens / self.baseline_agent_visible_tokens
        if not math.isclose(self.relative_reduction, expected, rel_tol=0, abs_tol=1e-12):
            raise ValueError("relative_reduction must equal reduction_tokens / baseline tokens")
        return self


class AgentVisibleResponse(Contract):
    serialization_format: Literal["utf-8-json"]
    serialized_content: str = Field(min_length=1)
    utf8_byte_count: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def exact_representation(self) -> "AgentVisibleResponse":
        encoded = self.serialized_content.encode("utf-8")
        if len(encoded) != self.utf8_byte_count:
            raise ValueError("utf8_byte_count must describe serialized_content exactly")
        if hashlib.sha256(encoded).hexdigest() != self.sha256:
            raise ValueError("sha256 must describe serialized_content exactly")
        return self


class CoverageReport(Contract):
    basis: Literal["task-signal", "gold-labels", "undetermined"]
    operative_expected: int = Field(ge=0)
    operative_recalled: int = Field(ge=0)
    missing_operative_decision_ids: list[str] = Field(default_factory=list, max_length=10000)
    unsafe_inclusion_decision_ids: list[str] = Field(default_factory=list, max_length=10000)
    missed_conflict_ids: list[str] = Field(default_factory=list, max_length=1000)
    detected_conflict_ids: list[str] = Field(default_factory=list, max_length=1000)
    complete_coverage_possible: bool
    complete_coverage_claimed: bool
    truncated: bool = False
    truncation_reason: Literal[
        "none", "token-budget", "result-limit", "serialization-limit"
    ] = "none"

    @model_validator(mode="after")
    def truthful_completeness(self) -> "CoverageReport":
        if self.operative_recalled > self.operative_expected:
            raise ValueError("operative_recalled cannot exceed operative_expected")
        if self.operative_expected - self.operative_recalled != len(self.missing_operative_decision_ids):
            raise ValueError("missing operative IDs must account for recall difference")
        if self.complete_coverage_claimed and (
            not self.complete_coverage_possible
            or self.missing_operative_decision_ids
            or self.missed_conflict_ids
            or self.truncated
        ):
            raise ValueError("complete coverage may be claimed only when all coverage invariants hold")
        if self.complete_coverage_possible == (self.basis == "undetermined"):
            raise ValueError("coverage basis must agree with complete_coverage_possible")
        if self.truncated and self.truncation_reason == "none":
            raise ValueError("truncation requires a non-none reason")
        if not self.truncated and self.truncation_reason != "none":
            raise ValueError("a non-none truncation reason requires truncated=true")
        if self.truncated and not self.missing_operative_decision_ids:
            raise ValueError("truncation requires an operative omission list")
        return self


class SecondaryResourceMeasurements(Contract):
    latency_ms: float = Field(ge=0)
    filesystem_reads: int = Field(ge=0)
    index_bytes: int = Field(ge=0)


class RetrievalBenchmarkResult(Contract):
    baseline_id: str = Field(min_length=1, max_length=256)
    mechanism_id: str = Field(min_length=1, max_length=256)
    coverage: CoverageReport
    agent_visible_response: AgentVisibleResponse
    agent_visible_tokens: int = Field(ge=0)
    repeated_token_count: int = Field(ge=0)
    repeated_token_definition: Literal["sum-token-occurrences-after-first-per-token"]
    agent_directed_tool_calls: int = Field(ge=0)
    baseline_reference: str | None = None
    baseline_agent_visible_tokens: int | None = Field(default=None, gt=0)
    reduction_tokens: int | None = None
    relative_reduction: float | None = Field(default=None, ge=-1, le=1)
    task_correctness: Literal["not-evaluated", "pass", "fail"] = "not-evaluated"
    adherence: Literal["not-evaluated", "pass", "fail"] = "not-evaluated"

    @model_validator(mode="after")
    def validate_result(self) -> "RetrievalBenchmarkResult":
        if self.repeated_token_count > self.agent_visible_tokens:
            raise ValueError("repeated_token_count cannot exceed agent_visible_tokens")
        fields = (
            self.baseline_reference,
            self.baseline_agent_visible_tokens,
            self.reduction_tokens,
            self.relative_reduction,
        )
        if self.baseline_reference is None and any(value is not None for value in fields[1:]):
            raise ValueError("reduction fields require a baseline_reference")
        if self.baseline_reference is not None:
            if self.baseline_reference == self.baseline_id:
                raise ValueError("a result cannot compare against itself")
            if any(value is None for value in fields[1:]):
                raise ValueError("baseline comparison requires all reduction fields")
            assert self.baseline_agent_visible_tokens is not None
            assert self.reduction_tokens is not None
            assert self.relative_reduction is not None
            expected_tokens = self.baseline_agent_visible_tokens - self.agent_visible_tokens
            expected_ratio = expected_tokens / self.baseline_agent_visible_tokens
            if self.reduction_tokens != expected_tokens or not math.isclose(
                self.relative_reduction, expected_ratio, rel_tol=0, abs_tol=1e-12
            ):
                raise ValueError("relative reduction must be calculated from the named baseline")
        if self.coverage.complete_coverage_claimed and self.coverage.unsafe_inclusion_decision_ids:
            raise ValueError("complete coverage cannot coexist with unsafe inclusions")
        return self


class RetrievalBenchmarkReport(Contract):
    schema_id: Literal["context-library/retrieval-benchmark-report"] = Field(
        default="context-library/retrieval-benchmark-report", alias="schema"
    )
    schema_version: Literal[1] = 1
    report_id: str = Field(min_length=1, max_length=256)
    task_id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,127}$")
    task_revision: str = Field(min_length=1, max_length=128)
    gold_revision: str = Field(min_length=1, max_length=128)
    gold_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    corpus_revision: str = Field(min_length=1, max_length=128)
    tokenizer: TokenizerIdentity
    results: list[RetrievalBenchmarkResult] = Field(min_length=1, max_length=100)
    secondary_resources: SecondaryResourceMeasurements

    @model_validator(mode="after")
    def validate_report(self) -> "RetrievalBenchmarkReport":
        ids = [result.baseline_id for result in self.results]
        if len(ids) != len(set(ids)):
            raise ValueError("baseline IDs must be unique")
        known = set(ids)
        for result in self.results:
            if result.baseline_reference is not None and result.baseline_reference not in known:
                raise ValueError("baseline_reference must name a result in the same report")
        return self
