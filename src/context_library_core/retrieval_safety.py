from __future__ import annotations

import json
from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from .contracts import Contract
from .retrieval_contracts import (
    ExclusionReason,
    RetrievalBenchmarkGold,
    RetrievalBenchmarkReport,
    RetrievalBenchmarkTask,
    RetrievalClassification,
)

EVALUATOR_VERSION = "rb-05-v1"


class SafetyFailure(StrEnum):
    MISSED_OPERATIVE = "missed-operative"
    PROMOTED_EXCLUDED = "promoted-excluded"
    HIDDEN_CONFLICT = "hidden-conflict"
    FALSE_COMPLETE_COVERAGE = "false-complete-coverage"
    SILENT_OPERATIVE_TRUNCATION = "silent-operative-truncation"
    UNREPORTED_JUDGMENT = "unreported-judgment"
    IDENTITY_MISMATCH = "identity-mismatch"
    RESPONSE_MISMATCH = "returned-response-mismatch"


class ReturnedDecision(Contract):
    evaluator_version: Literal["rb-05-v1"] = EVALUATOR_VERSION
    decision_id: str = Field(min_length=1, max_length=256)
    classification: RetrievalClassification
    exclusion_reason: ExclusionReason | None = None

    @model_validator(mode="after")
    def reason_matches_classification(self) -> "ReturnedDecision":
        if self.classification == RetrievalClassification.EXCLUDED and self.exclusion_reason is None:
            raise ValueError("excluded returned decisions require an exclusion reason")
        if self.classification != RetrievalClassification.EXCLUDED and self.exclusion_reason is not None:
            raise ValueError("only excluded returned decisions may have an exclusion reason")
        return self


class SafetyInput(Contract):
    evaluator_version: Literal["rb-05-v1"] = EVALUATOR_VERSION
    returned_decisions: list[ReturnedDecision] = Field(max_length=10000)

    @model_validator(mode="after")
    def unique_decisions(self) -> "SafetyInput":
        identifiers = [item.decision_id for item in self.returned_decisions]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("returned decision IDs must be unique")
        return self


class SafetyEvaluationError(ValueError):
    pass


def _failure(code: SafetyFailure, identifiers: list[str] | None = None) -> dict[str, object]:
    return {"code": code.value, "decision_ids": sorted(identifiers or [])}


def evaluate_safety(
    task: RetrievalBenchmarkTask,
    gold: RetrievalBenchmarkGold,
    report: RetrievalBenchmarkReport,
    safety_input: SafetyInput,
) -> dict[str, object]:
    failures: dict[SafetyFailure, set[str]] = {}
    if (
        report.task_id != task.task_id
        or report.task_revision != task.task_revision
        or report.gold_revision != gold.gold_revision
        or report.gold_sha256 != gold.gold_sha256
        or task.task_id != gold.task_id
        or task.task_revision != gold.task_revision
    ):
        failures[SafetyFailure.IDENTITY_MISMATCH] = set()
    if not report.results:
        raise SafetyEvaluationError("report must contain a result")
    result = report.results[0]
    try:
        response = json.loads(result.agent_visible_response.serialized_content)
        response_ids = [item["decision_id"] for item in response["decisions"]]
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise SafetyEvaluationError("agent-visible response is not a valid baseline response") from exc
    sidecar_ids = [item.decision_id for item in safety_input.returned_decisions]
    if response_ids != sidecar_ids:
        failures[SafetyFailure.RESPONSE_MISMATCH] = set(response_ids) ^ set(sidecar_ids)

    labels = {label.decision_id: label for label in gold.labels}
    returned = set(sidecar_ids)
    operative = set(task.expected_operative_decision_ids)
    judgment = set(task.judgment_required_decision_ids)
    excluded = set(task.excluded_decision_ids)
    missing = operative - returned
    if missing:
        failures[SafetyFailure.MISSED_OPERATIVE] = missing
    promoted = returned & excluded
    if promoted:
        failures[SafetyFailure.PROMOTED_EXCLUDED] = promoted
    unreported = judgment - {
        item.decision_id
        for item in safety_input.returned_decisions
        if item.classification == RetrievalClassification.JUDGMENT_REQUIRED
    }
    if unreported:
        failures[SafetyFailure.UNREPORTED_JUDGMENT] = unreported

    for conflict in gold.conflicts:
        members = set(conflict.member_decision_ids)
        if members & returned and not members <= returned:
            failures.setdefault(SafetyFailure.HIDDEN_CONFLICT, set()).add(conflict.conflict_id)

    complete_actual = not missing and not (returned & excluded) and not any(
        set(conflict.member_decision_ids) & returned and not set(conflict.member_decision_ids) <= returned
        for conflict in gold.conflicts
    )
    if result.coverage.complete_coverage_claimed and (
        not task.complete_coverage_possible or not complete_actual
    ):
        failures[SafetyFailure.FALSE_COMPLETE_COVERAGE] = set()
    if missing and not result.coverage.truncated:
        failures[SafetyFailure.SILENT_OPERATIVE_TRUNCATION] = missing

    for item in safety_input.returned_decisions:
        label = labels.get(item.decision_id)
        if label is None or item.classification != label.classification:
            failures.setdefault(SafetyFailure.PROMOTED_EXCLUDED, set()).add(item.decision_id)

    ordered = [
        _failure(code, sorted(failures[code]))
        for code in SafetyFailure
        if code in failures
    ]
    return {
        "evaluator_version": EVALUATOR_VERSION,
        "task_id": task.task_id,
        "report_id": report.report_id,
        "safety_passed": not ordered,
        "failures": ordered,
        "coverage": result.coverage.model_dump(mode="json"),
    }
