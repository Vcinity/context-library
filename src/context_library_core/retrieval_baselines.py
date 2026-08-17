from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Callable

import tiktoken

from .canonical import Decision, lexical_tokens, parse_register
from .retrieval_contracts import (
    AgentVisibleResponse,
    CoverageReport,
    RetrievalBenchmarkGold,
    RetrievalBenchmarkReport,
    RetrievalBenchmarkResult,
    RetrievalBenchmarkTask,
    SecondaryResourceMeasurements,
    TokenizerIdentity,
)

REFERENCE_TOKENIZER = TokenizerIdentity(
    name="tiktoken",
    version="0.9.0",
    vocabulary_revision="cl100k_base",
    accounting_method="tiktoken cl100k_base over exact serialized UTF-8 response",
)
SERIALIZATION_FORMAT = "utf-8-json"
BASELINE_IDS = ("full-register", "plugin-substring", "lexical")
_TOKENIZER = tiktoken.get_encoding("cl100k_base")


class RetrievalBaselineError(ValueError):
    pass


@dataclass(frozen=True)
class _Selection:
    baseline_id: str
    mechanism_id: str
    records: tuple[Decision, ...]
    tool_calls: int


def _json_response(
    project: str, task_id: str, baseline_id: str, result_limit: int, records: tuple[Decision, ...]
) -> str:
    payload = {
        "baseline": baseline_id,
        "project": project,
        "result_limit": result_limit,
        "task_id": task_id,
        "decisions": [
            {
                "decision": decision.decision,
                "decision_id": decision.decision_id,
                "provenance": decision.provenance,
                "subject": decision.subject,
            }
            for decision in records
        ],
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"


def _response_metrics(content: str) -> tuple[AgentVisibleResponse, int, int]:
    encoded = content.encode("utf-8")
    tokens = _TOKENIZER.encode(content)
    seen: set[int] = set()
    repeated = 0
    for token in tokens:
        if token in seen:
            repeated += 1
        seen.add(token)
    response = AgentVisibleResponse(
        serialization_format=SERIALIZATION_FORMAT,
        serialized_content=content,
        utf8_byte_count=len(encoded),
        sha256=hashlib.sha256(encoded).hexdigest(),
    )
    return response, len(tokens), repeated


def _searchable(decision: Decision) -> str:
    return "\n".join(
        (
            decision.decision_id,
            decision.subject,
            decision.decision,
            *decision.constraints,
            *(str(value) for value in decision.metadata.values()),
        )
    ).lower()


def _plugin_selection(decisions: tuple[Decision, ...], query: str, limit: int) -> tuple[Decision, ...]:
    needle = query.strip().lower()
    if not needle:
        raise RetrievalBaselineError("search query must be non-empty")
    return tuple(decision for decision in decisions if needle in _searchable(decision))[:limit]


def _lexical_selection(decisions: tuple[Decision, ...], query: str, limit: int) -> tuple[Decision, ...]:
    terms = tuple(sorted(set(lexical_tokens(query))))
    if not terms:
        raise RetrievalBaselineError("lexical query must contain a term")
    ranked = sorted(
        (
            (sum(term in lexical_tokens(_searchable(decision)) for term in terms), decision.decision_id, decision)
            for decision in decisions
        ),
        key=lambda item: (-item[0], item[1]),
    )
    return tuple(item[2] for item in ranked if item[0] > 0)[:limit]


def _coverage(
    task: RetrievalBenchmarkTask,
    gold: RetrievalBenchmarkGold,
    selected: tuple[Decision, ...],
    result_limit: int,
) -> CoverageReport:
    selected_ids = {decision.decision_id for decision in selected}
    expected = set(task.expected_operative_decision_ids)
    missing = sorted(expected - selected_ids)
    known = {label.decision_id for label in gold.labels}
    unsafe = sorted(selected_ids & (known - expected))
    conflicts = {conflict.conflict_id: set(conflict.member_decision_ids) for conflict in gold.conflicts}
    detected = sorted(conflict_id for conflict_id, members in conflicts.items() if members <= selected_ids)
    missed = sorted(
        conflict_id
        for conflict_id, members in conflicts.items()
        if members & selected_ids and not members <= selected_ids
    )
    truncated = len(selected) >= result_limit and bool(missing)
    return CoverageReport(
        basis="gold-labels" if task.complete_coverage_possible else "undetermined",
        operative_expected=len(expected),
        operative_recalled=len(expected & selected_ids),
        missing_operative_decision_ids=missing,
        unsafe_inclusion_decision_ids=unsafe,
        missed_conflict_ids=missed,
        detected_conflict_ids=detected,
        complete_coverage_possible=task.complete_coverage_possible,
        complete_coverage_claimed=task.complete_coverage_possible and not missing and not unsafe and not missed,
        truncated=truncated,
        truncation_reason="result-limit" if truncated else "none",
    )


def run_baselines(
    task: RetrievalBenchmarkTask,
    gold: RetrievalBenchmarkGold,
    register: str,
    *,
    project: str = "synthetic",
    result_limit: int = 10,
    corpus_revision: str = "rb-04-r1",
    search_query: str | None = None,
    clock: Callable[[], float] = time.perf_counter,
) -> RetrievalBenchmarkReport:
    if result_limit < 1:
        raise RetrievalBaselineError("result_limit must be positive")
    if task.task_id != gold.task_id or task.task_revision != gold.task_revision:
        raise RetrievalBaselineError("task and gold identity does not match")
    decisions = parse_register(register)
    started = clock()
    query = search_query or task.summary
    selections = (
        _Selection("full-register", "full-register-injection", decisions[:result_limit], 0),
        _Selection("plugin-substring", "plugin-substring-search", _plugin_selection(decisions, query, result_limit), 1),
        _Selection("lexical", "simple-lexical-overlap", _lexical_selection(decisions, query, result_limit), 1),
    )
    results: list[RetrievalBenchmarkResult] = []
    raw_tokens: dict[str, int] = {}
    for selection in selections:
        content = _json_response(project, task.task_id, selection.baseline_id, result_limit, selection.records)
        response, token_count, repeated = _response_metrics(content)
        raw_tokens[selection.baseline_id] = token_count
        results.append(
            RetrievalBenchmarkResult(
                baseline_id=selection.baseline_id,
                mechanism_id=selection.mechanism_id,
                coverage=_coverage(task, gold, selection.records, result_limit),
                agent_visible_response=response,
                agent_visible_tokens=token_count,
                repeated_token_count=repeated,
                repeated_token_definition="sum-token-occurrences-after-first-per-token",
                agent_directed_tool_calls=selection.tool_calls,
                task_correctness="not-evaluated",
                adherence="not-evaluated",
            )
        )
    baseline_tokens = raw_tokens["full-register"]
    for result in results[1:]:
        result.baseline_reference = "full-register"
        result.baseline_agent_visible_tokens = baseline_tokens
        result.reduction_tokens = baseline_tokens - result.agent_visible_tokens
        result.relative_reduction = result.reduction_tokens / baseline_tokens
    elapsed = max(0.0, (clock() - started) * 1000)
    report = RetrievalBenchmarkReport(
        report_id=f"report-{task.task_id}-{corpus_revision}",
        task_id=task.task_id,
        task_revision=task.task_revision,
        gold_revision=task.gold_revision,
        gold_sha256=task.gold_sha256,
        corpus_revision=corpus_revision,
        tokenizer=REFERENCE_TOKENIZER,
        results=results,
        secondary_resources=SecondaryResourceMeasurements(
            latency_ms=elapsed,
            filesystem_reads=1,
            index_bytes=len(register.encode("utf-8")),
        ),
    )
    return report
