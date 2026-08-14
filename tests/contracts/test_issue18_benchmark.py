from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

from context_library_core.issue18_benchmark import run_issue18_benchmark


ROOT = Path(__file__).parents[2]
FROZEN = (
    "scripts/run_retrieval_benchmark.py",
    "src/context_library_core/benchmark_runner.py",
    "src/context_library_core/retrieval_baselines.py",
    "src/context_library_core/retrieval_safety.py",
    "contracts/fixtures/retrieval-benchmark-targets-v1.json",
    "contracts/fixtures/retrieval-benchmark-gold-v1.json",
)


def frozen_hashes() -> dict[str, str]:
    return {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in FROZEN}


def test_issue18_matrix_is_deterministic_and_shared_inputs_are_explicit(tmp_path):
    first = run_issue18_benchmark(ROOT, tmp_path / "first")
    second = run_issue18_benchmark(ROOT, tmp_path / "second")

    first_bytes = (tmp_path / "first/report.json").read_bytes()
    second_bytes = (tmp_path / "second/report.json").read_bytes()
    assert first_bytes == second_bytes
    assert first["scales"] == [10, 100, 1000, 10000]
    assert [entry["scale"] for entry in first["entries"]] == [10, 100, 1000, 10000]
    assert all(len(entry["methods"]) == 4 for entry in first["entries"])
    assert first["frozen_inputs"] == frozen_hashes()
    assert all(entry["shared_inputs"]["agent_token_budget"] == 1000 for entry in first["entries"])
    assert all(entry["methods"][-1]["method_id"] == "task-context" for entry in first["entries"])


def test_task_context_uses_exact_capsule_accounting_and_retrieval_is_bounded(tmp_path):
    report = run_issue18_benchmark(ROOT, tmp_path / "report")
    for entry in report["entries"]:
        method = entry["methods"][-1]
        response = method["response"]
        content = response["agent_visible_capsule"]["serialized_content"]
        assert response["agent_visible_capsule"]["utf8_byte_count"] == len(content.encode())
        assert response["agent_visible_capsule"]["sha256"] == hashlib.sha256(content.encode()).hexdigest()
        assert method["agent_directed_tool_calls"] == 1
        assert method["secondary_resources"]["filesystem_reads"] == 1
        assert method["secondary_resources"]["index_bytes"] == entry["methods"][0]["secondary_resources"]["index_bytes"]


def test_issue18_cli_strict_reports_concrete_failures_without_mutating_frozen_inputs(tmp_path):
    before = frozen_hashes()
    output = tmp_path / "cli"
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/run_issue18_retrieval.py",
            "--output",
            str(output),
            "--strict",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0
    report = json.loads((output / "report.json").read_text())
    assert report["acceptance"]["failed_methods"]
    assert any(method["safety"]["failures"] for entry in report["entries"] for method in entry["methods"])
    assert report["acceptance"]["task_context_passed"] is True
    assert report["acceptance"]["blocking_failures"] == []
    assert frozen_hashes() == before
