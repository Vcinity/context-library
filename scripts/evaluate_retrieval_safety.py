from __future__ import annotations

import argparse
import json
from pathlib import Path

from context_library_core.retrieval_contracts import (
    RetrievalBenchmarkGold,
    RetrievalBenchmarkReport,
    RetrievalBenchmarkTask,
)
from context_library_core.retrieval_safety import SafetyInput, evaluate_safety


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate retrieval safety invariants.")
    parser.add_argument("--task", type=Path, required=True)
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--returned-decisions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    task = RetrievalBenchmarkTask.model_validate_json(args.task.read_text(encoding="utf-8"))
    gold = RetrievalBenchmarkGold.model_validate_json(args.gold.read_text(encoding="utf-8"))
    report = RetrievalBenchmarkReport.model_validate_json(args.report.read_text(encoding="utf-8"))
    safety_input = SafetyInput.model_validate_json(args.returned_decisions.read_text(encoding="utf-8"))
    result = evaluate_safety(task, gold, report, safety_input)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "safety_passed": result["safety_passed"]}, sort_keys=True))
    return 0 if result["safety_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
