from __future__ import annotations

import argparse
import json
from pathlib import Path

from context_library_core.benchmark_runner import BenchmarkRunnerError, run_benchmark


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the deterministic retrieval benchmark matrix.")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()
    try:
        result = run_benchmark(Path.cwd(), args.output, seed=args.seed, strict=args.strict)
    except (BenchmarkRunnerError, OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps({"output": str(args.output), "entries": len(result["entries"])}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
