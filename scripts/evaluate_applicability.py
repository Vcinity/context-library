from __future__ import annotations

import argparse
import json
from pathlib import Path

from context_library_core.applicability import evaluate_applicability
from context_library_core.contracts import ApplicabilityRequest


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate one deterministic applicability request.")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    request = ApplicabilityRequest.model_validate_json(args.input.read_text(encoding="utf-8"))
    result = evaluate_applicability(request)
    args.output.write_text(result.model_dump_json(by_alias=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "state": result.state}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
