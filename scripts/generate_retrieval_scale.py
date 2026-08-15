from __future__ import annotations

import argparse
import json
from pathlib import Path

from context_library_core.benchmark_generator import SCALES, generate_pack


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a deterministic synthetic retrieval benchmark pack.")
    parser.add_argument("--scale", type=int, choices=SCALES, required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--project", default="synthetic")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--canonical-root", type=Path)
    parser.add_argument("--allow-existing", action="store_true")
    args = parser.parse_args()
    pack = generate_pack(
        args.output,
        scale=args.scale,
        seed=args.seed,
        project=args.project,
        canonical_root=args.canonical_root,
        allow_existing=args.allow_existing,
    )
    print(json.dumps(pack.manifest, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
