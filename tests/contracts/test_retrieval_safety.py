from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from context_library_core.retrieval_contracts import (
    AgentVisibleResponse,
    CoverageReport,
    RetrievalBenchmarkGold,
    RetrievalBenchmarkReport,
    RetrievalBenchmarkResult,
    RetrievalBenchmarkTask,
    SecondaryResourceMeasurements,
    TokenizerIdentity,
)
from context_library_core.retrieval_safety import (
    EVALUATOR_VERSION,
    ReturnedDecision,
    SafetyFailure,
    SafetyInput,
    evaluate_safety,
)

ROOT = Path(__file__).parents[2]
FIXTURES = ROOT / "contracts/fixtures"


def inputs() -> tuple[RetrievalBenchmarkTask, RetrievalBenchmarkGold]:
    task_payload = json.loads((FIXTURES / "retrieval-benchmark-task-v1.json").read_text())
    gold_payload = json.loads((FIXTURES / "retrieval-benchmark-gold-v1.json").read_text())
    task_payload["applicable_conflicts"] = []
    gold_payload["conflicts"] = []
    for label in gold_payload["labels"]:
        label["conflict_ids"] = []
    return RetrievalBenchmarkTask.model_validate(task_payload), RetrievalBenchmarkGold.model_validate(gold_payload)


def report(task: RetrievalBenchmarkTask, *, decisions: list[str], claimed: bool = False, truncated: bool = False):
    content = json.dumps(
        {"baseline": "synthetic", "decisions": [{"decision_id": item} for item in decisions]},
        sort_keys=True,
        separators=(",", ":"),
    ) + "\n"
    response = AgentVisibleResponse(
        serialization_format="utf-8-json",
        serialized_content=content,
        utf8_byte_count=len(content.encode()),
        sha256=hashlib.sha256(content.encode()).hexdigest(),
    )
    coverage = CoverageReport(
        basis="undetermined",
        operative_expected=1,
        operative_recalled=int("decision-current-interface" in decisions),
        missing_operative_decision_ids=(
            [] if "decision-current-interface" in decisions else ["decision-current-interface"]
        ),
        unsafe_inclusion_decision_ids=[],
        missed_conflict_ids=[],
        detected_conflict_ids=[],
        complete_coverage_possible=False,
        complete_coverage_claimed=claimed,
        truncated=truncated,
        truncation_reason="result-limit" if truncated else "none",
    )
    result = RetrievalBenchmarkResult(
        baseline_id="synthetic",
        mechanism_id="synthetic",
        coverage=coverage,
        agent_visible_response=response,
        agent_visible_tokens=10,
        repeated_token_count=0,
        repeated_token_definition="sum-token-occurrences-after-first-per-token",
        agent_directed_tool_calls=0,
    )
    return RetrievalBenchmarkReport(
        report_id="report-synthetic",
        task_id=task.task_id,
        task_revision=task.task_revision,
        gold_revision=task.gold_revision,
        gold_sha256=task.gold_sha256,
        corpus_revision="synthetic",
        tokenizer=TokenizerIdentity(
            name="synthetic",
            version="1",
            vocabulary_revision="v1",
            accounting_method="synthetic",
        ),
        results=[result],
        secondary_resources=SecondaryResourceMeasurements(latency_ms=0, filesystem_reads=0, index_bytes=0),
    )


def sidecar(decisions: list[tuple[str, str, str | None]]) -> SafetyInput:
    return SafetyInput(
        evaluator_version=EVALUATOR_VERSION,
        returned_decisions=[
            ReturnedDecision(decision_id=identifier, classification=classification, exclusion_reason=reason)
            for identifier, classification, reason in decisions
        ],
    )


def test_valid_result_passes_and_is_deterministic():
    task, gold = inputs()
    current = [("decision-current-interface", "operative", None), ("decision-scope-review", "judgment-required", None)]
    first = evaluate_safety(task, gold, report(task, decisions=[item[0] for item in current]), sidecar(current))
    second = evaluate_safety(task, gold, report(task, decisions=[item[0] for item in current]), sidecar(current))

    assert first["safety_passed"] is True
    assert first == second


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        ("missed", SafetyFailure.MISSED_OPERATIVE.value),
        ("promoted", SafetyFailure.PROMOTED_EXCLUDED.value),
        ("judgment", SafetyFailure.UNREPORTED_JUDGMENT.value),
        ("silent", SafetyFailure.SILENT_OPERATIVE_TRUNCATION.value),
    ],
)
def test_safety_mutations_fail_closed(mutation, code):
    task, gold = inputs()
    if mutation == "missed":
        result = report(task, decisions=["decision-scope-review"])
        supplied = sidecar([("decision-scope-review", "judgment-required", None)])
    elif mutation == "promoted":
        result = report(task, decisions=["decision-current-interface", "decision-old-interface"])
        supplied = sidecar(
            [
                ("decision-current-interface", "operative", None),
                ("decision-old-interface", "excluded", "superseded"),
            ]
        )
    elif mutation == "judgment":
        result = report(task, decisions=["decision-current-interface"])
        supplied = sidecar([("decision-current-interface", "operative", None)])
    else:
        result = report(task, decisions=["decision-scope-review"], truncated=False)
        supplied = sidecar([("decision-scope-review", "judgment-required", None)])

    outcome = evaluate_safety(task, gold, result, supplied)
    assert outcome["safety_passed"] is False
    assert code in {failure["code"] for failure in outcome["failures"]}


def test_hidden_conflict_and_false_complete_claim_fail():
    task_payload = json.loads((FIXTURES / "retrieval-benchmark-task-v1.json").read_text())
    gold_payload = json.loads((FIXTURES / "retrieval-benchmark-gold-v1.json").read_text())
    task = RetrievalBenchmarkTask.model_validate(task_payload)
    gold = RetrievalBenchmarkGold.model_validate(gold_payload)
    result = report(task, decisions=["decision-current-interface"])
    outcome = evaluate_safety(
        task,
        gold,
        result,
        sidecar([("decision-current-interface", "operative", None)]),
    )

    assert outcome["safety_passed"] is False
    assert SafetyFailure.HIDDEN_CONFLICT.value in {failure["code"] for failure in outcome["failures"]}


def test_cli_returns_nonzero_for_unsafe_result(tmp_path):
    task, gold = inputs()
    task_path = tmp_path / "task.json"
    gold_path = tmp_path / "gold.json"
    report_path = tmp_path / "report.json"
    sidecar_path = tmp_path / "sidecar.json"
    output = tmp_path / "evaluation.json"
    task_path.write_text(task.model_dump_json(by_alias=True))
    gold_path.write_text(gold.model_dump_json(by_alias=True))
    report_path.write_text(report(task, decisions=["decision-scope-review"]).model_dump_json(by_alias=True))
    sidecar_path.write_text(sidecar([("decision-scope-review", "judgment-required", None)]).model_dump_json())

    result = subprocess.run(
        [
            sys.executable,
            "scripts/evaluate_retrieval_safety.py",
            "--task",
            str(task_path),
            "--gold",
            str(gold_path),
            "--report",
            str(report_path),
            "--returned-decisions",
            str(sidecar_path),
            "--output",
            str(output),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert json.loads(output.read_text())["safety_passed"] is False
