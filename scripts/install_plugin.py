from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable, Sequence

ROOT = Path(__file__).resolve().parents[1]
SOURCE_MARKETPLACE = ROOT / ".agents/plugins/marketplace.json"
SOURCE_PLUGIN = ROOT / "plugins/context-library"
RUNTIME_CONFIG_NAME = "runtime-config.json"

CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


def copy_plugin(source: Path, destination: Path) -> None:
    def ignored(_directory: str, names: list[str]) -> set[str]:
        return {
            name
            for name in names
            if name == RUNTIME_CONFIG_NAME or name == "__pycache__" or name.endswith((".pyc", ".pyo"))
        }

    shutil.copytree(source, destination, ignore=ignored)


def marketplace_name(path: Path) -> str:
    payload = json.loads(path.read_text(encoding="utf-8"))
    name = payload.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError(f"marketplace manifest has no valid name: {path}")
    return name


def run_command(command: Sequence[str], *, runner: CommandRunner) -> None:
    runner(list(command), check=True, text=True)


def main(argv: list[str] | None = None, *, runner: CommandRunner = subprocess.run) -> int:
    parser = argparse.ArgumentParser(
        description="Stage, configure, and install the Context Library Plugin marketplace."
    )
    parser.add_argument("--destination", required=True, type=Path)
    parser.add_argument("--library-root", required=True, type=Path)
    parser.add_argument("--marketplace-name")
    parser.add_argument("--project")
    parser.add_argument("--context-requirement", choices=("required", "optional", "disabled"))
    parser.add_argument(
        "--stage-only",
        action="store_true",
        help="Create the configured marketplace without registering or installing it.",
    )
    args = parser.parse_args(argv)

    destination = args.destination.expanduser().resolve()
    library_root = args.library_root.expanduser().resolve()
    if destination.exists():
        parser.error(f"destination already exists: {destination}")
    if not library_root.is_dir():
        parser.error(f"library root is not a readable directory: {library_root}")
    try:
        destination.relative_to(library_root)
    except ValueError:
        pass
    else:
        parser.error("destination must not be inside the canonical library root")
    try:
        library_root.relative_to(destination)
    except ValueError:
        pass
    else:
        parser.error("canonical library root must not be inside the destination")

    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{destination.name}-", dir=destination.parent) as temporary:
        staging_root = Path(temporary) / "marketplace"
        staged_marketplace = staging_root / ".agents/plugins/marketplace.json"
        staged_plugin = staging_root / "plugins/context-library"
        staged_marketplace.parent.mkdir(parents=True)
        shutil.copy2(SOURCE_MARKETPLACE, staged_marketplace)
        copy_plugin(SOURCE_PLUGIN, staged_plugin)

        configure = [
            sys.executable,
            str(staged_plugin / "scripts/configure.py"),
            "--library-root",
            str(library_root),
        ]
        if args.marketplace_name is not None:
            configure.extend(("--marketplace-name", args.marketplace_name))
        if args.project is not None:
            configure.extend(("--project", args.project))
        if args.context_requirement is not None:
            configure.extend(("--context-requirement", args.context_requirement))
        run_command(configure, runner=runner)

        selected_marketplace = marketplace_name(staged_marketplace)
        staging_root.replace(destination)

    if not args.stage_only:
        run_command(("codex", "plugin", "marketplace", "add", str(destination)), runner=runner)
        run_command(
            ("codex", "plugin", "add", f"context-library@{selected_marketplace}"),
            runner=runner,
        )

    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
