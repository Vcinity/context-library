from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from context_library_core.retrieval_corpus import CorpusValidationError, validate_corpus


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a synthetic retrieval benchmark corpus.")
    parser.add_argument("corpus", type=Path)
    args = parser.parse_args()
    try:
        result = validate_corpus(args.corpus)
    except CorpusValidationError as exc:
        print(f"corpus validation failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
