from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from context_library_core.benchmark_runner import run_benchmark

ROOT = Path(__file__).parents[2]


def test_runner_emits_all_scales_and_repeated_reports_are_identical(tmp_path):
    first = run_benchmark(ROOT, tmp_path / "first")
    run_benchmark(ROOT, tmp_path / "second")

    assert len(first["entries"]) == 12
    assert {entry["scale"] for entry in first["entries"]} == {10, 100, 1000, 10000}
    assert (tmp_path / "first/report.json").read_bytes() == (tmp_path / "second/report.json").read_bytes()
    assert (tmp_path / "first/summary.md").read_text().startswith("# Retrieval benchmark")


def test_runner_cli_returns_nonzero_when_strict_safety_is_requested(tmp_path):
    output = tmp_path / "report"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/run_retrieval_benchmark.py",
            "--output",
            str(output),
            "--strict",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    report = json.loads((output / "report.json").read_text())
    assert any(not entry["safety"]["safety_passed"] for entry in report["entries"])
