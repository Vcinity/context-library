#!/usr/bin/env python3
"""Create a deployment-local runtime configuration before Plugin install."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from runtime_config import SCHEMA, SCHEMA_VERSION, RuntimeConfigError, load_runtime_config  # noqa: E402

DEFAULT_MARKETPLACE_PATH = PLUGIN_ROOT.parents[1] / ".agents/plugins/marketplace.json"
NAME_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as stream:
            temporary_name = stream.name
            stream.write(content)
        Path(temporary_name).replace(path)
        os.chmod(path, 0o644)
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)


def renamed_marketplace(path: Path, name: str) -> str:
    if not NAME_RE.fullmatch(name):
        raise ValueError("marketplace name must be a stable lowercase identifier")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"unable to read valid marketplace manifest {path}: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("plugins"), list):
        raise ValueError(f"marketplace manifest {path} must define a plugins array")
    matching = [
        entry for entry in payload["plugins"] if isinstance(entry, dict) and entry.get("name") == "context-library"
    ]
    if len(matching) != 1:
        raise ValueError("marketplace must contain exactly one context-library plugin entry")
    source = matching[0].get("source")
    if source != {"source": "local", "path": "./plugins/context-library"}:
        raise ValueError("context-library marketplace source must remain ./plugins/context-library")
    payload["name"] = name
    return json.dumps(payload, indent=2) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library-root", required=True, type=Path)
    parser.add_argument("--project")
    parser.add_argument("--context-requirement", choices=("required", "optional", "disabled"))
    parser.add_argument("--output", type=Path, default=PLUGIN_ROOT / "runtime-config.json")
    parser.add_argument("--marketplace-name")
    parser.add_argument("--marketplace-path", type=Path, default=DEFAULT_MARKETPLACE_PATH)
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
    content = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    marketplace_path = args.marketplace_path.expanduser().resolve()
    marketplace_content: str | None = None
    if args.marketplace_name is not None:
        try:
            marketplace_content = renamed_marketplace(marketplace_path, args.marketplace_name)
        except ValueError as exc:
            parser.error(str(exc))

    output.parent.mkdir(parents=True, exist_ok=True)
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
        os.chmod(output, 0o644)
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)
    if marketplace_content is not None:
        atomic_write(marketplace_path, marketplace_content)
    print(output)
    if marketplace_content is not None:
        print(marketplace_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
