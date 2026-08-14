from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
import tiktoken
from pydantic import ValidationError

from context_library_core.retrieval_baselines import run_baselines
from context_library_core.retrieval_contracts import RetrievalBenchmarkGold, RetrievalBenchmarkTask

ROOT = Path(__file__).parents[2]
FIXTURES = ROOT / "contracts/fixtures"


def register() -> str:
    return """# Synthetic benchmark

<a id="decision-current-interface"></a>
### Current interface
- Category: Synthetic
- Provenance: explicit
- Decision: Current interface guidance is versioned.
- Derivation: direct
- Affected Layers: global

<a id="decision-old-interface"></a>
### Old interface
- Category: Synthetic
- Provenance: explicit
- Decision: Old interface guidance is superseded.
- Derivation: direct
- Supersedes: decision-current-interface
- Affected Layers: global

<a id="decision-scope-review"></a>
### Scope review
- Category: Synthetic
- Provenance: inferred
- Decision: Scope review may be required.
- Derivation: direct
- Affected Layers: tests

<a id="decision-outside-repo"></a>
### Outside repository
- Category: Synthetic
- Provenance: explicit
- Decision: Unrelated deployment guidance.
- Derivation: direct
- Affected Layers: deploy
"""


def inputs() -> tuple[RetrievalBenchmarkTask, RetrievalBenchmarkGold]:
    task = RetrievalBenchmarkTask.model_validate(
        json.loads((FIXTURES / "retrieval-benchmark-task-v1.json").read_text())
    )
    gold = RetrievalBenchmarkGold.model_validate(
        json.loads((FIXTURES / "retrieval-benchmark-gold-v1.json").read_text())
    )
    return task, gold


def test_three_baselines_share_exact_accounting_boundary():
    task, gold = inputs()
    report = run_baselines(
        task,
        gold,
        register(),
        result_limit=10,
        search_query="current interface",
        clock=lambda: 10.0,
    )
    rendered = json.dumps(report.model_dump(by_alias=True), sort_keys=True)

    assert [result.baseline_id for result in report.results] == [
        "full-register",
        "plugin-substring",
        "lexical",
    ]
    assert report.tokenizer.name == "tiktoken"
    assert report.secondary_resources.latency_ms == 0
    for result in report.results:
        content = result.agent_visible_response.serialized_content
        assert result.agent_visible_response.utf8_byte_count == len(content.encode())
        assert result.agent_visible_response.sha256 == hashlib.sha256(content.encode()).hexdigest()
        assert result.agent_visible_tokens == len(tiktoken.get_encoding("cl100k_base").encode(content))
    assert report.model_validate_json(rendered).model_dump() == report.model_dump()


def test_result_limit_is_truthful_and_repeated_runs_are_deterministic():
    task, gold = inputs()
    first = run_baselines(task, gold, register(), result_limit=1, search_query="current interface", clock=lambda: 2.0)
    second = run_baselines(task, gold, register(), result_limit=1, search_query="current interface", clock=lambda: 2.0)

    assert first.model_dump_json(by_alias=True) == second.model_dump_json(by_alias=True)
    assert first.results[0].coverage.complete_coverage_claimed is False
    assert first.results[0].coverage.complete_coverage_possible is False
    assert first.results[1].agent_directed_tool_calls == 1


def test_response_mutation_fails_contract_validation():
    task, gold = inputs()
    report = run_baselines(task, gold, register(), search_query="current interface")
    payload = json.loads(report.model_dump_json(by_alias=True))
    payload["results"][0]["agent_visible_response"]["serialized_content"] += "changed"

    with pytest.raises(ValidationError, match="utf8_byte_count|sha256"):
        type(report).model_validate(payload)


def test_cli_emits_a_machine_readable_report(tmp_path):
    task_path = tmp_path / "task.json"
    gold_path = tmp_path / "gold.json"
    register_path = tmp_path / "decision-register.md"
    output = tmp_path / "report.json"
    task_path.write_text((FIXTURES / "retrieval-benchmark-task-v1.json").read_text())
    gold_path.write_text((FIXTURES / "retrieval-benchmark-gold-v1.json").read_text())
    register_path.write_text(register())

    result = subprocess.run(
        [
            sys.executable,
            "scripts/run_retrieval_baselines.py",
            "--task",
            str(task_path),
            "--gold",
            str(gold_path),
            "--register",
            str(register_path),
            "--query",
            "current interface",
            "--output",
            str(output),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert json.loads(output.read_text())["schema"] == "context-library/retrieval-benchmark-report"
