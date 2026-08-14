from __future__ import annotations

import argparse
import json
from pathlib import Path

from context_library_core.retrieval_baselines import run_baselines
from context_library_core.retrieval_contracts import RetrievalBenchmarkGold, RetrievalBenchmarkTask


def main() -> int:
    parser = argparse.ArgumentParser(description="Run deterministic retrieval reference baselines.")
    parser.add_argument("--task", type=Path, required=True)
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--register", type=Path, required=True)
    parser.add_argument("--project", default="synthetic")
    parser.add_argument("--result-limit", type=int, default=10)
    parser.add_argument("--query")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    task = RetrievalBenchmarkTask.model_validate_json(args.task.read_text(encoding="utf-8"))
    gold = RetrievalBenchmarkGold.model_validate_json(args.gold.read_text(encoding="utf-8"))
    report = run_baselines(
        task,
        gold,
        args.register.read_text(encoding="utf-8"),
        project=args.project,
        result_limit=args.result_limit,
        search_query=args.query,
    )
    args.output.write_text(report.model_dump_json(by_alias=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(args.output), "baselines": len(report.results)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
