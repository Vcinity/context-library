"""Issue-18-only acceptance adapter for delivered task-context retrieval.

The RB-06 runner and its inputs remain immutable.  This module composes the
frozen generator, baseline runner, and safety evaluator with the delivered
task-context resolver and records the two response contracts side by side.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import tiktoken

from .benchmark_generator import SCALES, generate_pack
from .canonical import Decision, parse_register
from .retrieval_baselines import REFERENCE_TOKENIZER, run_baselines
from .retrieval_contracts import (
    ConflictReference,
    ExclusionReason,
    GoldDecisionLabel,
    RetrievalBenchmarkGold,
    RetrievalBenchmarkTask,
    RetrievalClassification,
)
from .retrieval_safety import ReturnedDecision, SafetyInput, evaluate_safety
from .task_context import TaskContextRequest
from .task_context_resolution import resolve_task_context


ADAPTER_SCHEMA = "context-library/issue-18-retrieval-acceptance"
ADAPTER_VERSION = 1
RESULT_LIMIT = 10
DEFAULT_BUDGET = 1000
_TOKENIZER = tiktoken.get_encoding("cl100k_base")


class Issue18BenchmarkError(ValueError):
    """The bounded acceptance adapter cannot produce a trustworthy report."""


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _dump_digest(value: Any) -> str:
    return _sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())


def _frozen_digest(root: Path, relative: str) -> str:
    return _sha256((root / relative).read_bytes())


def _load_base_inputs(root: Path) -> tuple[RetrievalBenchmarkTask, RetrievalBenchmarkGold]:
    fixtures = root / "contracts/fixtures"
    task = RetrievalBenchmarkTask.model_validate_json(
        (fixtures / "retrieval-benchmark-task-v1.json").read_text(encoding="utf-8")
    )
    gold = RetrievalBenchmarkGold.model_validate_json(
        (fixtures / "retrieval-benchmark-gold-v1.json").read_text(encoding="utf-8")
    )
    return task, gold


def _conflict_groups(decisions: tuple[Decision, ...]) -> tuple[ConflictReference, ...]:
    groups: dict[str, list[str]] = {}
    for decision in decisions:
        if decision.conflict_key:
            groups.setdefault(decision.conflict_key, []).append(decision.decision_id)
    return tuple(
        ConflictReference(conflict_id=key, member_decision_ids=sorted(members))
        for key, members in sorted(groups.items())
        if len(members) > 1
    )


def _case_inputs(
    base_task: RetrievalBenchmarkTask,
    base_gold: RetrievalBenchmarkGold,
    decisions: tuple[Decision, ...],
    scale: int,
) -> tuple[RetrievalBenchmarkTask, RetrievalBenchmarkGold]:
    """Build labels for the exact generated register consumed by both paths."""
    superseded = {identifier for decision in decisions for identifier in decision.supersedes}
    conflicts = _conflict_groups(decisions)
    conflict_by_id = {
        member: [conflict.conflict_id for conflict in conflicts if member in conflict.member_decision_ids]
        for conflict in conflicts
        for member in conflict.member_decision_ids
    }
    labels: list[GoldDecisionLabel] = []
    for decision in decisions:
        conflict_ids = conflict_by_id.get(decision.decision_id, [])
        if decision.decision_id in superseded:
            labels.append(
                GoldDecisionLabel(
                    decision_id=decision.decision_id,
                    classification=RetrievalClassification.EXCLUDED,
                    exclusion_reason=ExclusionReason.SUPERSEDED,
                    conflict_ids=conflict_ids,
                )
            )
        elif decision.provenance != "explicit":
            labels.append(
                GoldDecisionLabel(
                    decision_id=decision.decision_id,
                    classification=RetrievalClassification.EXCLUDED,
                    exclusion_reason=ExclusionReason.NON_AUTHORITATIVE,
                    conflict_ids=conflict_ids,
                )
            )
        elif decision.applies_when:
            labels.append(
                GoldDecisionLabel(
                    decision_id=decision.decision_id,
                    classification=RetrievalClassification.JUDGMENT_REQUIRED,
                    conflict_ids=conflict_ids,
                )
            )
        else:
            labels.append(
                GoldDecisionLabel(
                    decision_id=decision.decision_id,
                    classification=RetrievalClassification.OPERATIVE,
                    conflict_ids=conflict_ids,
                )
            )

    label_payload = [label.model_dump(mode="json") for label in labels]
    conflict_payload = [conflict.model_dump(mode="json") for conflict in conflicts]
    gold_digest = _dump_digest({"labels": label_payload, "conflicts": conflict_payload})
    # The generated pack uses synthetic layer names.  They are deliberately
    # part of the shared task signal so both retrieval paths see identical
    # applicability inputs.
    repository_scopes = ["global", "projects/synthetic"]
    expected = [label.decision_id for label in labels if label.classification == RetrievalClassification.OPERATIVE]
    judgment = [
        label.decision_id for label in labels if label.classification == RetrievalClassification.JUDGMENT_REQUIRED
    ]
    excluded = [label.decision_id for label in labels if label.classification == RetrievalClassification.EXCLUDED]
    # The frozen RB-01 task contract bounds each decision-ID list at 1,000.
    # Keep the full generated gold corpus below, but make the bounded task
    # slice explicit so a 10,000-record run reports resource/coverage limits
    # instead of silently violating the accepted contract.
    task_lists_bounded = len(expected) <= 1000 and len(judgment) <= 1000 and len(excluded) <= 1000
    expected = expected[:1000]
    judgment = judgment[:1000]
    excluded = excluded[:1000]
    task_payload = base_task.model_dump(by_alias=True)
    task_payload.update(
        task_id=f"synthetic-scale-{scale}",
        repository_scopes=repository_scopes,
        expected_operative_decision_ids=expected,
        judgment_required_decision_ids=judgment,
        excluded_decision_ids=excluded,
        applicable_conflicts=conflict_payload,
        complete_coverage_possible=task_lists_bounded and not judgment,
        gold_sha256=gold_digest,
    )
    gold_payload = base_gold.model_dump(by_alias=True)
    gold_payload.update(
        task_id=task_payload["task_id"],
        labels=label_payload,
        conflicts=conflict_payload,
        gold_sha256=gold_digest,
    )
    return (
        RetrievalBenchmarkTask.model_validate(task_payload),
        RetrievalBenchmarkGold.model_validate(gold_payload),
    )


def _returned_decisions(ids: list[str], gold: RetrievalBenchmarkGold) -> SafetyInput:
    labels = {label.decision_id: label for label in gold.labels}
    return SafetyInput(
        returned_decisions=[
            ReturnedDecision(
                decision_id=identifier,
                classification=labels[identifier].classification,
                exclusion_reason=labels[identifier].exclusion_reason,
            )
            for identifier in ids
        ]
    )


def _baseline_method(result: Any, task: RetrievalBenchmarkTask, gold: RetrievalBenchmarkGold, report: Any) -> dict[str, Any]:
    response = json.loads(result.agent_visible_response.serialized_content)
    safety = evaluate_safety(task, gold, report, _returned_decisions([item["decision_id"] for item in response["decisions"]], gold))
    return {
        "method_id": result.baseline_id,
        "mechanism_id": result.mechanism_id,
        "coverage": result.coverage.model_dump(mode="json"),
        "serialization": {
            "format": result.agent_visible_response.serialization_format,
            "utf8_byte_count": result.agent_visible_response.utf8_byte_count,
            "sha256": result.agent_visible_response.sha256,
        },
        "agent_visible_tokens": result.agent_visible_tokens,
        "repeated_token_count": result.repeated_token_count,
        "repeated_token_definition": result.repeated_token_definition,
        "agent_directed_tool_calls": result.agent_directed_tool_calls,
        "secondary_resources": report.secondary_resources.model_dump(mode="json"),
        "safety": safety,
        "exclusions": sorted(task.excluded_decision_ids),
        "target": {
            "relative_reduction_met": result.relative_reduction is None or result.relative_reduction >= 0.2,
            "tool_calls_met": result.agent_directed_tool_calls <= 1,
        },
    }


def _task_context_safety(response: Any, task: RetrievalBenchmarkTask, gold: RetrievalBenchmarkGold) -> dict[str, Any]:
    operative = {item.decision_id for item in response.operative_directives}
    judgment = {item.decision_id for item in response.applicability_uncertainties}
    non_operative = {item.decision_id for item in response.non_operative_directives}
    surfaced = operative | judgment | non_operative
    expected = set(task.expected_operative_decision_ids)
    excluded = set(task.excluded_decision_ids)
    missing = sorted(expected - operative)
    promoted = sorted(operative & excluded)
    missed_conflicts = sorted(
        conflict.conflict_id
        for conflict in gold.conflicts
        if surfaced.intersection(conflict.member_decision_ids)
        and not set(conflict.member_decision_ids).issubset(surfaced)
    )
    detected_conflicts = sorted(
        conflict.conflict_id
        for conflict in gold.conflicts
        if set(conflict.member_decision_ids).issubset(surfaced)
    )
    failures: list[dict[str, Any]] = []

    def add(code: str, identifiers: list[str] | None = None) -> None:
        failures.append({"code": code, "decision_ids": sorted(identifiers or [])})

    if missing:
        add("missed-operative", missing)
    if promoted:
        add("promoted-excluded", promoted)
    unreported = sorted(set(task.judgment_required_decision_ids) - judgment)
    if unreported:
        add("unreported-judgment", unreported)
    if missed_conflicts:
        add("hidden-conflict", missed_conflicts)
    complete_actual = not missing and not promoted and not missed_conflicts
    complete_claimed = response.coverage.complete and task.complete_coverage_possible
    if complete_claimed and not complete_actual:
        add("false-complete-coverage")
    if missing and not response.truncation.truncated:
        add("silent-operative-truncation", missing)
    return {
        "evaluator_version": "rb-05-v1-adapter-task-context",
        "task_id": task.task_id,
        "safety_passed": not failures,
        "failures": failures,
        "coverage": {
            "basis": "task-signal" if task.complete_coverage_possible else "undetermined",
            "operative_expected": len(expected),
            "operative_recalled": len(expected & operative),
            "missing_operative_decision_ids": missing,
            "unsafe_inclusion_decision_ids": promoted,
            "missed_conflict_ids": missed_conflicts,
            "detected_conflict_ids": detected_conflicts,
            "complete_coverage_possible": task.complete_coverage_possible,
            "complete_coverage_claimed": complete_claimed,
            "truncated": response.truncation.truncated,
            "truncation_reason": response.truncation.reason,
        },
        "surfaced": {
            "operative": sorted(operative),
            "judgment_required": sorted(judgment),
            "non_operative": sorted(non_operative),
        },
    }


def _task_context_method(response: Any, task: RetrievalBenchmarkTask, gold: RetrievalBenchmarkGold, baseline_tokens: int, register: str) -> dict[str, Any]:
    content = response.agent_visible_capsule.serialized_content
    encoded = content.encode("utf-8")
    token_count = response.agent_visible_capsule.token_count
    repeated = len(_TOKENIZER.encode(content)) - len(set(_TOKENIZER.encode(content)))
    reduction = baseline_tokens - token_count
    safety = _task_context_safety(response, task, gold)
    return {
        "method_id": "task-context",
        "mechanism_id": "rt-02-core-resolver-renderer",
        "coverage": safety["coverage"],
        "serialization": {
            "format": "task-context-capsule",
            "utf8_byte_count": len(encoded),
            "sha256": _sha256(encoded),
            "response_schema": "context-library/task-context-response",
        },
        "agent_visible_tokens": token_count,
        "repeated_token_count": repeated,
        "repeated_token_definition": "sum-token-occurrences-after-first-per-token",
        "agent_directed_tool_calls": 1,
        "secondary_resources": {
            "latency_ms": 0.0,
            "filesystem_reads": 1,
            "index_bytes": len(register.encode("utf-8")),
            "measurement": "deterministic adapter diagnostics; wall-clock latency omitted",
        },
        "safety": safety,
        "exclusions": sorted(task.excluded_decision_ids),
        "target": {
            "baseline_reference": "full-register",
            "baseline_agent_visible_tokens": baseline_tokens,
            "reduction_tokens": reduction,
            "relative_reduction": reduction / baseline_tokens,
            "relative_reduction_met": reduction / baseline_tokens >= 0.2,
            "tool_calls_met": True,
        },
        "response": response.model_dump(by_alias=True),
    }


def run_issue18_benchmark(
    root: Path,
    output: Path,
    *,
    seed: int = 17,
    agent_token_budget: int = DEFAULT_BUDGET,
    strict: bool = False,
) -> dict[str, Any]:
    """Run the deterministic Issue-18 matrix and write report.json/summary.md."""
    if agent_token_budget < 0:
        raise Issue18BenchmarkError("agent_token_budget must be non-negative")
    targets = json.loads(
        (root / "contracts/fixtures/retrieval-benchmark-targets-v1.json").read_text(encoding="utf-8")
    )
    if targets["scales"] != list(SCALES):
        raise Issue18BenchmarkError("frozen target scales do not match the generator")
    base_task, base_gold = _load_base_inputs(root)
    output.mkdir(parents=True, exist_ok=True)
    entries: list[dict[str, Any]] = []
    frozen_files = (
        "scripts/run_retrieval_benchmark.py",
        "src/context_library_core/benchmark_runner.py",
        "src/context_library_core/retrieval_baselines.py",
        "src/context_library_core/retrieval_safety.py",
        "contracts/fixtures/retrieval-benchmark-targets-v1.json",
        "contracts/fixtures/retrieval-benchmark-gold-v1.json",
    )
    for scale in targets["scales"]:
        pack = generate_pack(output / f".scale-{scale}", scale=scale, seed=seed)
        register = (pack.output / "decision-register.md").read_text(encoding="utf-8")
        decisions = parse_register(register)
        task, gold = _case_inputs(base_task, base_gold, decisions, scale)
        baseline_report = run_baselines(
            task,
            gold,
            register,
            result_limit=RESULT_LIMIT,
            clock=lambda: 0.0,
        )
        baseline_methods = [
            _baseline_method(result, task, gold, baseline_report)
            for result in baseline_report.results
        ]
        full_register = baseline_report.results[0].agent_visible_tokens
        request = TaskContextRequest(
            project="synthetic",
            task_summary=task.summary,
            operation=task.operation,
            repository_scopes=task.repository_scopes,
            agent_token_budget=agent_token_budget,
            tokenizer=REFERENCE_TOKENIZER,
        )
        response = resolve_task_context(register, request, revision=pack.manifest["register_sha256"], source_scope=f"scale-{scale}")
        task_method = _task_context_method(response, task, gold, full_register, register)
        entries.append(
            {
                "scale": scale,
                "corpus_decision_count": len(decisions),
                "seed": seed,
                "register_sha256": pack.manifest["register_sha256"],
                "task": task.model_dump(by_alias=True),
                "task_sha256": _dump_digest(task.model_dump(by_alias=True)),
                "gold": gold.model_dump(by_alias=True),
                "gold_sha256": gold.gold_sha256,
                "methods": [*baseline_methods, task_method],
                "shared_inputs": {
                    "result_limit": RESULT_LIMIT,
                    "agent_token_budget": agent_token_budget,
                    "serialization": "frozen baseline utf-8-json; delivered task-context capsule",
                    "tokenizer": REFERENCE_TOKENIZER.model_dump(mode="json"),
                },
            }
        )
        pack_path = pack.output
        for child in pack_path.iterdir():
            child.unlink()
        pack_path.rmdir()
    payload: dict[str, Any] = {
        "schema": ADAPTER_SCHEMA,
        "schema_version": ADAPTER_VERSION,
        "adapter_revision": "issue-18-r1",
        "target_revision": targets["target_revision"],
        "generator_revision": targets["generator_revision"],
        "corpus_revision": targets["corpus_revision"],
        "frozen_inputs": {relative: _frozen_digest(root, relative) for relative in frozen_files},
        "scales": list(targets["scales"]),
        "entries": entries,
        "limitations": [
            "Synthetic offline evidence only; no provider-backed agent evaluation was run.",
            "Task-context safety is an adapter projection because frozen RB-05 accepts the baseline JSON response contract.",
            "Secondary latency is intentionally omitted from deterministic evidence; filesystem and serialized-size diagnostics remain reported.",
        ],
    }
    report_path = output / "report.json"
    report_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    summary = [
        "# Issue #18 retrieval acceptance",
        "",
        f"Target: `{targets['target_revision']}`",
        "",
        "| Scale | Method | Safety | Tokens | Reduction | Tool calls |",
        "| ---: | --- | --- | ---: | ---: | ---: |",
    ]
    for entry in entries:
        for method in entry["methods"]:
            target = method["target"]
            reduction = target.get("relative_reduction", 0.0)
            summary.append(
                f"| {entry['scale']} | {method['method_id']} | {method['safety']['safety_passed']} | "
                f"{method['agent_visible_tokens']} | {reduction:.3f} | {method['agent_directed_tool_calls']} |"
            )
    (output / "summary.md").write_text("\n".join(summary) + "\n", encoding="utf-8")
    failed = [
        (entry["scale"], method["method_id"])
        for entry in entries
        for method in entry["methods"]
        if not method["safety"]["safety_passed"]
        or not method["target"]["relative_reduction_met"]
        or not method["target"]["tool_calls_met"]
    ]
    payload["acceptance"] = {
        "task_context_passed": not any(method == "task-context" for _, method in failed),
        "failed_methods": [{"scale": scale, "method": method} for scale, method in failed],
        "blocking_failures": [
            {"scale": scale, "method": method} for scale, method in failed if method == "task-context"
        ],
    }
    report_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if strict and payload["acceptance"]["blocking_failures"]:
        raise Issue18BenchmarkError(
            f"{len(payload['acceptance']['blocking_failures'])} task-context entries failed acceptance checks"
        )
    return payload
