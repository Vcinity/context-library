from __future__ import annotations

import json
import zipfile
from pathlib import Path

from context_library_core.version import VERSION

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins/context-library"
OUTPUT = ROOT / f"dist/context-library-plugin-{VERSION}.zip"


def plugin_files() -> list[Path]:
    return [
        path
        for path in sorted(PLUGIN.rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts and path.suffix not in {".pyc", ".pyo"}
    ]


def main() -> int:
    manifest = json.loads((PLUGIN / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))
    if manifest["version"] != VERSION:
        raise SystemExit("Plugin manifest version does not match release")
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(OUTPUT, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in plugin_files():
            info = zipfile.ZipInfo(f"context-library/{path.relative_to(PLUGIN)}")
            info.date_time = (1980, 1, 1, 0, 0, 0)
            mode = 0o755 if path.stat().st_mode & 0o111 else 0o644
            info.external_attr = mode << 16
            archive.writestr(info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
