from __future__ import annotations

import argparse
import hashlib
import pprint
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE_ROOT = ROOT / "src"
SOURCE = CORE_ROOT / "context_library_core/canonical.py"
CONTRACT_SOURCE = CORE_ROOT / "context_library_core/contracts.py"
VERSION_SOURCE = CORE_ROOT / "context_library_core/version.py"
TARGET = ROOT / "plugins/context-library/generated/core_runtime.py"

sys.path.insert(0, str(CORE_ROOT))

from context_library_core.contracts import ContextPolicy  # noqa: E402
from context_library_core.version import VERSION  # noqa: E402


def expected() -> bytes:
    source = SOURCE.read_bytes()
    contract_source = CONTRACT_SOURCE.read_bytes()
    version_source = VERSION_SOURCE.read_bytes()
    digest = hashlib.sha256(source + b"\0" + contract_source + b"\0" + version_source).hexdigest()
    policy_schema = pprint.pformat(
        ContextPolicy.model_json_schema(by_alias=True),
        compact=True,
        sort_dicts=True,
        width=120,
    )
    header = (
        "# Generated from context_library_core.canonical; do not edit.\n"
        f"# source-version: {VERSION}\n# source-sha256: {digest}\n"
    ).encode()
    generated_contracts = f'''

# Generated read-only contract metadata used by the self-contained Plugin.
PRODUCT_VERSION = {VERSION!r}
CONTEXT_POLICY_JSON_SCHEMA = {policy_schema}


def validate_context_policy(payload: object) -> dict[str, object]:
    """Validate the Core context-policy/v1 contract without write dependencies."""
    if not isinstance(payload, dict):
        raise ValueError("context policy must be a JSON object")
    schema = CONTEXT_POLICY_JSON_SCHEMA
    properties = schema["properties"]
    unknown = set(payload).difference(properties)
    if unknown:
        raise ValueError(f"unknown context policy field: {{sorted(unknown)[0]}}")
    missing = set(schema.get("required", ())).difference(payload)
    if missing:
        raise ValueError(f"missing context policy field: {{sorted(missing)[0]}}")
    if payload.get("schema") != properties["schema"]["const"]:
        raise ValueError("unsupported context policy schema family")
    if payload.get("schema_version") != properties["schema_version"]["const"]:
        raise ValueError("unsupported context policy schema version")
    requirement = payload.get("context_requirement")
    if requirement not in properties["context_requirement"]["enum"]:
        raise ValueError("invalid context requirement")
    project = payload.get("project")
    if project is not None and (not isinstance(project, str) or not ID_RE.fullmatch(project)):
        raise ValueError("configured project must be a stable lowercase identifier")
    affected = payload.get("affected_layers", {{}})
    if not isinstance(affected, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in affected.items()
    ):
        raise ValueError("context policy affected_layers must map strings to strings")
    return payload
'''.encode()
    return header + source + generated_contracts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    content = expected()
    if args.check:
        if not TARGET.is_file() or TARGET.read_bytes() != content:
            print("generated Plugin Core runtime is stale")
            return 1
        return 0
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_bytes(content)
    init = TARGET.parent / "__init__.py"
    if not init.exists():
        init.write_text('"""Generated read-only Core runtime."""\n', encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
