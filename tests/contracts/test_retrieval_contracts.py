from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from context_library_core.retrieval_contracts import (
    RetrievalBenchmarkGold,
    RetrievalBenchmarkReport,
    RetrievalBenchmarkTask,
)

ROOT = Path(__file__).parents[2]
FIXTURES = ROOT / "contracts/fixtures"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def test_task_gold_and_report_fixtures_are_bound_and_machine_readable():
    task = RetrievalBenchmarkTask.model_validate(load("retrieval-benchmark-task-v1.json"))
    gold = RetrievalBenchmarkGold.model_validate(load("retrieval-benchmark-gold-v1.json"))
    report = RetrievalBenchmarkReport.model_validate(load("retrieval-benchmark-report-v1.json"))

    assert task.task_id == gold.task_id == report.task_id
    assert task.task_revision == gold.task_revision == report.task_revision
    assert task.gold_revision == gold.gold_revision == report.gold_revision
    assert report.results[1].coverage.complete_coverage_claimed is True
    assert report.results[1].relative_reduction == 0.5


@pytest.mark.parametrize(
    "name",
    ["retrieval-benchmark-task-invalid.json", "retrieval-benchmark-gold-unsupported.json"],
)
def test_unsupported_versions_are_rejected(name):
    model = RetrievalBenchmarkTask if "task" in name else RetrievalBenchmarkGold
    with pytest.raises(ValidationError):
        model.model_validate(load(name))


def test_task_classification_mutation_fails_closed():
    payload = load("retrieval-benchmark-task-v1.json")
    payload["excluded_decision_ids"].append(payload["expected_operative_decision_ids"][0])
    with pytest.raises(ValidationError, match="multiple classifications"):
        RetrievalBenchmarkTask.model_validate(payload)


def test_malformed_report_with_unknown_field_fails_closed():
    with pytest.raises(ValidationError, match="unexpected_private_field"):
        RetrievalBenchmarkReport.model_validate(load("retrieval-benchmark-report-malformed.json"))


def test_gold_conflict_reference_mutation_fails_closed():
    payload = load("retrieval-benchmark-gold-v1.json")
    payload["labels"][0]["conflict_ids"] = ["missing-conflict"]
    with pytest.raises(ValidationError, match="unknown conflicts"):
        RetrievalBenchmarkGold.model_validate(payload)


def test_report_false_completeness_and_silent_truncation_fail():
    payload = load("retrieval-benchmark-report-v1.json")
    coverage = payload["results"][1]["coverage"]
    coverage["complete_coverage_claimed"] = False
    coverage["truncated"] = True
    coverage["truncation_reason"] = "none"
    coverage["missing_operative_decision_ids"] = ["decision-current-interface"]
    coverage["operative_recalled"] = 0
    with pytest.raises(ValidationError, match="truncation requires"):
        RetrievalBenchmarkReport.model_validate(payload)

    payload = load("retrieval-benchmark-report-v1.json")
    payload["results"][1]["coverage"]["operative_recalled"] = 0
    payload["results"][1]["coverage"]["missing_operative_decision_ids"] = ["decision-current-interface"]
    with pytest.raises(ValidationError, match="complete coverage"):
        RetrievalBenchmarkReport.model_validate(payload)


def test_exact_serialized_response_mutation_fails():
    payload = load("retrieval-benchmark-report-v1.json")
    payload["results"][1]["agent_visible_response"]["sha256"] = hashlib.sha256(b"different").hexdigest()
    with pytest.raises(ValidationError, match="sha256"):
        RetrievalBenchmarkReport.model_validate(payload)


def test_relative_reduction_mutation_fails():
    payload = load("retrieval-benchmark-report-v1.json")
    payload["results"][1]["relative_reduction"] = 0.25
    with pytest.raises(ValidationError, match="relative reduction"):
        RetrievalBenchmarkReport.model_validate(payload)
