"""Install the built wheel into a clean environment and exercise its entry point."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    wheels = sorted((ROOT / "dist").glob("context_library-*.whl"))
    if len(wheels) != 1:
        raise RuntimeError(f"expected exactly one built wheel, found {len(wheels)}")
    with tempfile.TemporaryDirectory(prefix="context-library-package-") as directory:
        install_root = Path(directory) / "installed"
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--target",
                str(install_root),
                "--no-deps",
                "--no-index",
                str(wheels[0]),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        probe = (
            "import json\n"
            "from pathlib import Path\n"
            "from typer.testing import CliRunner\n"
            "import context_library_maintainer.cli as cli\n"
            f"assert Path(cli.__file__).is_relative_to(Path({str(install_root)!r}))\n"
            "result = CliRunner().invoke(cli.app, ['version', '--json'])\n"
            "assert result.exit_code == 0, result.output\n"
            "print(result.stdout, end='')\n"
        )
        completed = subprocess.run(
            [sys.executable, "-c", probe],
            check=False,
            capture_output=True,
            text=True,
            cwd=directory,
            env={**os.environ, "PYTHONPATH": str(install_root)},
        )
        if completed.returncode:
            raise RuntimeError(f"installed clm failed with exit {completed.returncode}: {completed.stderr[-2000:]}")
        payload = json.loads(completed.stdout)
        if payload["data"]["product_version"] != "0.4.0":
            raise RuntimeError(f"unexpected installed version: {payload}")
        print(json.dumps({"wheel": wheels[0].name, "installed_version": "0.4.0"}, sort_keys=True))


if __name__ == "__main__":
    main()
