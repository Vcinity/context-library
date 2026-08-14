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

from context_library_core.contracts import ApplicabilityRequest, ContextPolicy  # noqa: E402
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
    applicability_schema = pprint.pformat(
        ApplicabilityRequest.model_json_schema(by_alias=True),
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
APPLICABILITY_REQUEST_JSON_SCHEMA = {applicability_schema}


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


def evaluate_applicability(payload: object) -> dict[str, object]:
    """Evaluate the Core v1 repository-scope rule without write dependencies."""
    if not isinstance(payload, dict) or payload.get("schema") != "context-library/applicability":
        raise ValueError("unsupported applicability schema family")
    if payload.get("schema_version") != 1:
        raise ValueError("unsupported applicability schema version")
    task = payload.get("task")
    decision = payload.get("decision")
    if not isinstance(task, dict) or not isinstance(decision, dict):
        raise ValueError("applicability task and decision are required objects")
    task_scopes = task.get("repository_scopes", [])
    decision_scopes = decision.get("repository_scopes", [])
    if not all(
        isinstance(item, str)
        and item
        and not item.startswith("/")
        and ".." not in item.split("/")
        for item in (*task_scopes, *decision_scopes)
    ):
        raise ValueError("repository scopes must be safe relative paths")
    if len(task_scopes) != len(set(task_scopes)) or len(decision_scopes) != len(set(decision_scopes)):
        raise ValueError("repository scopes must be unique")
    matched = sorted(set(task_scopes) & set(decision_scopes))
    if not decision_scopes and decision.get("applies_when") is None:
        state, reason = "unconditional", "none"
    elif decision.get("applies_when") is not None:
        state, reason = "undetermined", "conditional-unresolved"
    elif not task_scopes:
        state, reason = "undetermined", "missing-task-signal"
    elif matched:
        state, reason = "satisfied", "none"
    else:
        state, reason = "unsatisfied", "scope-mismatch"
    return {{
        "decision_id": decision.get("decision_id"),
        "state": state,
        "reason": reason,
        "matched_selectors": {{"repository_scopes": matched}} if matched else {{}},
    }}
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
