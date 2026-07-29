from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from context_library_manager.api import create_app
from context_library_manager.config import Settings


def document() -> str:
    with tempfile.TemporaryDirectory(prefix="clm-openapi-") as directory:
        root = Path(directory)
        library = root / "library"
        library.mkdir()
        settings = Settings(
            "sqlite:///" + str(root / "runtime.db"),
            library,
            root / "state",
            "openapi",
            require_oidc=False,
            development_mode=True,
            session_secret="openapi-generation-session-secret",
        )
        schema = create_app(settings).openapi()
    return json.dumps(schema, indent=2, sort_keys=True) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("docs/openapi.json"))
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    generated = document()
    if args.check:
        if not args.output.is_file() or args.output.read_text() != generated:
            raise SystemExit("generated OpenAPI is out of date; run make openapi")
        return
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(generated)


if __name__ == "__main__":
    main()
