from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .retrieval_contracts import RetrievalBenchmarkGold, RetrievalBenchmarkTask

CORPUS_SCHEMA = "context-library/retrieval-benchmark-corpus"
CORPUS_VERSION = 1
REQUIRED_DIMENSIONS = frozenset(
    {
        "lexical-mismatch",
        "synonyms",
        "distractor",
        "supersession",
        "conflict",
        "global-and-scoped",
        "unresolved-applicability",
        "insufficient-budget",
        "no-applicable-context",
    }
)


class CorpusValidationError(ValueError):
    pass


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CorpusValidationError(f"{label} must be an object")
    return value


def _exact_keys(value: dict[str, Any], expected: set[str], label: str) -> None:
    unknown = set(value) - expected
    missing = expected - set(value)
    if unknown:
        raise CorpusValidationError(f"{label} has unknown fields: {sorted(unknown)}")
    if missing:
        raise CorpusValidationError(f"{label} is missing fields: {sorted(missing)}")


def _parse_model(model: type[Any], payload: Any, label: str) -> Any:
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        raise CorpusValidationError(f"{label} is invalid: {exc}") from exc


def validate_corpus(path: Path) -> dict[str, Any]:
    """Validate a repository-local synthetic RB-02 corpus."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CorpusValidationError(f"unable to read corpus: {exc}") from exc

    root = _mapping(payload, "corpus")
    _exact_keys(root, {"schema", "schema_version", "corpus_revision", "cases"}, "corpus")
    if root["schema"] != CORPUS_SCHEMA or root["schema_version"] != CORPUS_VERSION:
        raise CorpusValidationError("unsupported corpus schema or version")
    cases = root["cases"]
    if not isinstance(cases, list) or not cases:
        raise CorpusValidationError("corpus cases must be a non-empty list")

    case_ids: set[str] = set()
    dimension_coverage: set[str] = set()
    task_ids: set[str] = set()
    gold_ids: set[str] = set()
    decision_ids: set[str] = set()
    conflict_ids: set[str] = set()

    for index, raw_case in enumerate(cases):
        case = _mapping(raw_case, f"case {index}")
        _exact_keys(case, {"case_id", "case_revision", "dimensions", "task", "gold", "decisions"}, f"case {index}")
        case_id = case["case_id"]
        if not isinstance(case_id, str) or not case_id or case_id in case_ids:
            raise CorpusValidationError(f"duplicate or invalid case_id: {case_id!r}")
        case_ids.add(case_id)
        dimensions = case["dimensions"]
        if not isinstance(dimensions, list) or not dimensions or not all(isinstance(item, str) for item in dimensions):
            raise CorpusValidationError(f"case {case_id!r} dimensions must be a non-empty string list")
        if len(dimensions) != len(set(dimensions)):
            raise CorpusValidationError(f"case {case_id!r} dimensions must be unique")
        dimension_coverage.update(dimensions)

        task = _parse_model(RetrievalBenchmarkTask, case["task"], f"case {case_id} task")
        gold = _parse_model(RetrievalBenchmarkGold, case["gold"], f"case {case_id} gold")
        if task.task_id != gold.task_id or task.task_revision != gold.task_revision:
            raise CorpusValidationError(f"case {case_id!r} task and gold identity does not match")
        if task.gold_revision != gold.gold_revision or task.gold_sha256 != gold.gold_sha256:
            raise CorpusValidationError(f"case {case_id!r} task and gold revision binding does not match")
        if task.task_id in task_ids or gold.task_id in gold_ids:
            raise CorpusValidationError(f"duplicate task or gold identity: {task.task_id!r}")
        task_ids.add(task.task_id)
        gold_ids.add(gold.task_id)

        labels = {label.decision_id: label for label in gold.labels}
        expected = set(task.expected_operative_decision_ids)
        judgment = set(task.judgment_required_decision_ids)
        excluded = set(task.excluded_decision_ids)
        if set(labels) != expected | judgment | excluded:
            raise CorpusValidationError(f"case {case_id!r} task and gold classifications differ")
        for decision_id in expected:
            if labels[decision_id].classification.value != "operative":
                raise CorpusValidationError(f"case {case_id!r} operative label is inconsistent")
        for decision_id in judgment:
            if labels[decision_id].classification.value != "judgment-required":
                raise CorpusValidationError(f"case {case_id!r} judgment label is inconsistent")
        for decision_id in excluded:
            if labels[decision_id].classification.value != "excluded":
                raise CorpusValidationError(f"case {case_id!r} excluded label is inconsistent")

        decisions = case["decisions"]
        if not isinstance(decisions, list) or not decisions:
            raise CorpusValidationError(f"case {case_id!r} decisions must be a non-empty list")
        local_decisions: set[str] = set()
        for decision_index, raw_decision in enumerate(decisions):
            decision = _mapping(raw_decision, f"case {case_id} decision {decision_index}")
            _exact_keys(
                decision,
                {"decision_id", "text", "scope", "relation", "supersedes"},
                f"case {case_id} decision",
            )
            decision_id = decision["decision_id"]
            if not isinstance(decision_id, str) or not decision_id or decision_id in local_decisions:
                raise CorpusValidationError(f"case {case_id!r} has duplicate or invalid decision_id")
            local_decisions.add(decision_id)
            if not isinstance(decision["text"], str) or not decision["text"]:
                raise CorpusValidationError(f"case {case_id!r} decision text must be non-empty")
            if not isinstance(decision["scope"], str) or not decision["scope"]:
                raise CorpusValidationError(f"case {case_id!r} decision scope must be non-empty")
            if not isinstance(decision["relation"], str) or not decision["relation"]:
                raise CorpusValidationError(f"case {case_id!r} decision relation must be non-empty")
            supersedes = decision["supersedes"]
            if supersedes is not None and not isinstance(supersedes, str):
                raise CorpusValidationError(f"case {case_id!r} supersedes must be a string or null")
        if local_decisions != set(labels):
            raise CorpusValidationError(f"case {case_id!r} decision records and gold labels differ")
        if decision_ids & local_decisions:
            raise CorpusValidationError(
                f"decision identity crosses corpus cases: {sorted(decision_ids & local_decisions)}"
            )
        decision_ids.update(local_decisions)

        local_conflicts = {conflict.conflict_id for conflict in gold.conflicts}
        if conflict_ids & local_conflicts:
            raise CorpusValidationError(
                f"conflict identity crosses corpus cases: {sorted(conflict_ids & local_conflicts)}"
            )
        conflict_ids.update(local_conflicts)

    missing_dimensions = REQUIRED_DIMENSIONS - dimension_coverage
    if missing_dimensions:
        raise CorpusValidationError(f"corpus is missing required dimensions: {sorted(missing_dimensions)}")
    return {
        "schema": CORPUS_SCHEMA,
        "schema_version": CORPUS_VERSION,
        "corpus_revision": root["corpus_revision"],
        "case_count": len(cases),
        "dimensions": sorted(dimension_coverage),
    }
