#!/usr/bin/env python3
"""Create a deployment-local runtime configuration before Plugin install."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from runtime_config import SCHEMA, SCHEMA_VERSION, RuntimeConfigError, load_runtime_config  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library-root", required=True, type=Path)
    parser.add_argument("--project")
    parser.add_argument("--context-requirement", choices=("required", "optional", "disabled"))
    parser.add_argument("--output", type=Path, default=PLUGIN_ROOT / "runtime-config.json")
    args = parser.parse_args(argv)

    library_root = args.library_root.expanduser().resolve()
    if not library_root.is_dir() or not os.access(library_root, os.R_OK):
        parser.error(f"library root is not a readable directory: {library_root}")
    payload: dict[str, object] = {
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "library_root": str(library_root),
    }
    if args.project is not None:
        payload["project"] = args.project
    if args.context_requirement is not None:
        payload["context_requirement"] = args.context_requirement

    output = args.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=output.parent, delete=False) as stream:
            temporary_name = stream.name
            stream.write(content)
        try:
            load_runtime_config(Path(temporary_name))
        except RuntimeConfigError as exc:
            parser.error(str(exc))
        Path(temporary_name).replace(output)
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
