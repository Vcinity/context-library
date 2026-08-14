from __future__ import annotations

import argparse
import json
from pathlib import Path

from context_library_core.issue18_benchmark import Issue18BenchmarkError, run_issue18_benchmark


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the bounded Issue-18 retrieval acceptance matrix.")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--agent-token-budget", type=int, default=1000)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()
    try:
        report = run_issue18_benchmark(
            Path.cwd(),
            args.output,
            seed=args.seed,
            agent_token_budget=args.agent_token_budget,
            strict=args.strict,
        )
    except (Issue18BenchmarkError, OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps({"output": str(args.output), "entries": len(report["entries"])}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
