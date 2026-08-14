from __future__ import annotations

import argparse
import json
from pathlib import Path

from context_library_core.task_context import TaskContextRequest
from context_library_core.task_context_resolution import resolve_task_context


def main() -> int:
    parser = argparse.ArgumentParser(description="Resolve deterministic operative task context.")
    parser.add_argument("--register", type=Path, required=True)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--source-scope", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    request = TaskContextRequest.model_validate_json(args.request.read_text(encoding="utf-8"))
    result = resolve_task_context(
        args.register.read_text(encoding="utf-8"),
        request,
        revision=args.revision,
        source_scope=args.source_scope,
    )
    args.output.write_text(result.model_dump_json(by_alias=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "complete": result.coverage.complete}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
