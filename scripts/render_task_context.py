from __future__ import annotations

import argparse
import json
from pathlib import Path

from context_library_core.task_context import TaskContextItem, TaskContextRequest, render_task_context


def main() -> int:
    parser = argparse.ArgumentParser(description="Render a deterministic task-context capsule.")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = json.loads(args.input.read_text(encoding="utf-8"))
    request = TaskContextRequest.model_validate(payload["request"])
    items = [TaskContextItem.model_validate(item) for item in payload["items"]]
    response = render_task_context(request, items, revision=payload["revision"])
    args.output.write_text(response.model_dump_json(by_alias=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "complete": response.coverage.complete}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
