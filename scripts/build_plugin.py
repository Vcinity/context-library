from __future__ import annotations

import argparse
import json
import sys
import zipfile
from pathlib import Path

from context_library_core.version import VERSION

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins/context-library"
OUTPUT = ROOT / f"dist/context-library-plugin-{VERSION}.zip"
RUNTIME_CONFIG_NAME = "runtime-config.json"

sys.path.insert(0, str(PLUGIN))

from runtime_config import load_runtime_config  # noqa: E402


def plugin_files() -> list[Path]:
    return [
        path
        for path in sorted(PLUGIN.rglob("*"))
        if path.is_file()
        and path.name != RUNTIME_CONFIG_NAME
        and "__pycache__" not in path.parts
        and path.suffix not in {".pyc", ".pyo"}
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the independently installable Context Library Plugin.")
    parser.add_argument(
        "--runtime-config",
        type=Path,
        help="Validated deployment-local runtime-config.json to embed in this artifact.",
    )
    args = parser.parse_args(argv or [])
    manifest = json.loads((PLUGIN / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))
    if manifest["version"] != VERSION:
        raise SystemExit("Plugin manifest version does not match release")
    runtime_config: bytes | None = None
    if args.runtime_config is not None:
        path = args.runtime_config.expanduser().resolve()
        load_runtime_config(path)
        runtime_config = path.read_bytes()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    entries = [
        (
            f"context-library/{path.relative_to(PLUGIN)}",
            path.read_bytes(),
            0o755 if path.stat().st_mode & 0o111 else 0o644,
        )
        for path in plugin_files()
    ]
    if runtime_config is not None:
        entries.append((f"context-library/{RUNTIME_CONFIG_NAME}", runtime_config, 0o600))
    with zipfile.ZipFile(OUTPUT, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content, mode in sorted(entries):
            info = zipfile.ZipInfo(name)
            info.date_time = (1980, 1, 1, 0, 0, 0)
            info.external_attr = mode << 16
            archive.writestr(info, content, compress_type=zipfile.ZIP_DEFLATED)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
