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

from context_library_core.contracts import (  # noqa: E402
    ApplicabilityRequest,
    ContextPolicy,
    DecisionAuditResponse,
)
from context_library_core.task_context import TaskContextRequest, TaskContextResponse  # noqa: E402
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
    task_context_request_schema = pprint.pformat(
        TaskContextRequest.model_json_schema(by_alias=True), compact=True, sort_dicts=True, width=120
    )
    task_context_response_schema = pprint.pformat(
        TaskContextResponse.model_json_schema(by_alias=True), compact=True, sort_dicts=True, width=120
    )
    decision_audit_schema = pprint.pformat(
        DecisionAuditResponse.model_json_schema(by_alias=True), compact=True, sort_dicts=True, width=120
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
TASK_CONTEXT_REQUEST_JSON_SCHEMA = {task_context_request_schema}
TASK_CONTEXT_RESPONSE_JSON_SCHEMA = {task_context_response_schema}
DECISION_AUDIT_RESPONSE_JSON_SCHEMA = {decision_audit_schema}


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
        "required_selectors": ["repository_scopes"] if decision_scopes else [],
        "provenance": decision.get("provenance"),
        "effective_provenance": decision.get("effective_provenance"),
        "source_scope": decision.get("source_scope"),
        "supersedes": decision.get("supersedes", []),
        "conflict_ids": decision.get("conflict_ids", []),
    }}
'''.encode()
    task_runtime = r'''

# Generated task-context and audit helpers.  Keep this code dependency-free so
# the independently installable Plugin can preserve Core semantics without
# importing the write-capable application packages.
import hashlib


def _require_object(value: object, message: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(message)
    return value


def validate_task_context_request(payload: object) -> dict[str, object]:
    data = _require_object(payload, "task-context request must be a JSON object")
    schema = TASK_CONTEXT_REQUEST_JSON_SCHEMA
    properties = schema["properties"]
    unknown = set(data).difference(properties)
    if unknown:
        raise ValueError(f"unknown task-context field: {sorted(unknown)[0]}")
    if data.get("schema", "context-library/task-context-request") != "context-library/task-context-request":
        raise ValueError("unsupported task-context schema family")
    if data.get("schema_version", 1) != 1:
        raise ValueError("unsupported task-context schema version")
    required = (
        "project",
        "task_summary",
        "operation",
        "repository_scopes",
        "agent_token_budget",
        "tokenizer",
    )
    missing = [name for name in required if name not in data]
    if missing:
        raise ValueError(f"missing task-context field: {missing[0]}")
    project = data["project"]
    if not isinstance(project, str) or not re.fullmatch(r"^[a-z][a-z0-9-]*$", project):
        raise ValueError("project must be a stable lowercase identifier")
    for name in ("task_summary", "operation"):
        if not isinstance(data[name], str) or not data[name].strip():
            raise ValueError(f"{name} must be non-empty")
    scopes = data["repository_scopes"]
    if not isinstance(scopes, list) or not scopes:
        raise ValueError("repository_scopes must be a non-empty list")
    if any(
        not isinstance(item, str)
        or not item
        or item.startswith("/")
        or "\\" in item
        or any(part in {"", ".", ".."} for part in item.split("/"))
        for item in scopes
    ):
        raise ValueError("repository scopes must be non-empty relative paths")
    if len(scopes) != len(set(scopes)):
        raise ValueError("repository scopes must be unique")
    if not isinstance(data["agent_token_budget"], int) or isinstance(data["agent_token_budget"], bool) or data["agent_token_budget"] < 0:
        raise ValueError("agent_token_budget must be a non-negative integer")
    tokenizer = _require_object(data["tokenizer"], "tokenizer must be an object")
    allowed_tokenizer = {"name", "version", "vocabulary_revision", "accounting_method", "pinned"}
    if set(tokenizer).difference(allowed_tokenizer):
        raise ValueError("unknown tokenizer field")
    if tokenizer.get("pinned", True) is not True:
        raise ValueError("tokenizer must be pinned")
    if any(not isinstance(tokenizer.get(name), str) or not tokenizer[name] for name in allowed_tokenizer - {"pinned"}):
        raise ValueError("tokenizer identity fields must be non-empty strings")
    data["tokenizer"] = dict(tokenizer)
    data["tokenizer"].setdefault("pinned", True)
    return data


def _task_item(decision: Decision, state: str, source_scope: str) -> dict[str, object]:
    return {
        "decision_id": decision.decision_id,
        "text": decision.decision,
        "state": state,
        "provenance": decision.provenance,
        "effective_provenance": decision.provenance,
        "source_scope": source_scope,
        "supersedes": list(decision.supersedes),
        "conflict_ids": list(decision.conflicts_with),
    }


def _render_task_context(payload: dict[str, object], decisions: tuple[Decision, ...], *, revision: str, source_scope: str) -> dict[str, object]:
    scopes = payload["repository_scopes"]
    superseded = {identifier for decision in decisions for identifier in decision.supersedes}
    items: list[dict[str, object]] = []
    for decision in decisions:
        decision_scopes = list(decision.affected_layers)
        evaluation = evaluate_applicability({
            "schema": "context-library/applicability",
            "schema_version": 1,
            "task": {"repository_scopes": scopes},
            "decision": {
                "decision_id": decision.decision_id,
                "repository_scopes": decision_scopes,
                "provenance": decision.provenance,
                "effective_provenance": decision.provenance,
                "source_scope": source_scope,
                "supersedes": list(decision.supersedes),
                "conflict_ids": list(decision.conflicts_with),
                "applies_when": decision.applies_when,
            },
        })
        state = str(evaluation["state"])
        if decision.provenance != "explicit" or decision.decision_id in superseded:
            state = "unsatisfied"
        items.append(_task_item(decision, state, source_scope))
    ordered = sorted(items, key=lambda item: (str(item["state"]), str(item["decision_id"]), str(item["source_scope"])))
    operative = [item for item in ordered if item["state"] in {"unconditional", "satisfied"}]
    uncertainties = [item for item in ordered if item["state"] == "undetermined"]
    non_operative = [item for item in ordered if item["state"] == "unsatisfied"]
    tokenizer = payload["tokenizer"]
    budget_status = "unverified"
    encoder = None
    if (
        tokenizer.get("name") == "tiktoken"
        and tokenizer.get("version") == "0.9.0"
        and tokenizer.get("vocabulary_revision") == "cl100k_base"
    ):
        try:
            import tiktoken
            encoder = tiktoken.get_encoding("cl100k_base")
            budget_status = "verified"
        except (ImportError, ValueError):
            pass
    capsule_lines = [f"# Task context: {payload['project']}", f"revision: {revision}", "", "## Operative directives"]
    capsule_lines.extend(f"- [{item['decision_id']}] {item['text']}" for item in operative)
    capsule = "\n".join(capsule_lines) + "\n"
    token_count = len(encoder.encode(capsule)) if encoder is not None else 0
    omitted = []
    if token_count > payload["agent_token_budget"]:
        omitted = [str(item["decision_id"]) for item in operative]
        capsule = ""
        token_count = 0
    encoded = capsule.encode("utf-8")
    return {
        "schema": "context-library/task-context-response",
        "schema_version": 1,
        "project": payload["project"],
        "revision": revision,
        "operative_directives": operative,
        "applicability_uncertainties": uncertainties,
        "non_operative_directives": non_operative,
        "applicable_conflicts": sorted({str(conflict) for item in operative + uncertainties + non_operative for conflict in item["conflict_ids"]}),
        "coverage": {
            "operative_expected": len(operative),
            "operative_included": len(operative) - len(omitted),
            "omitted_operative_decision_ids": omitted,
            "complete": not omitted,
            "budget_status": budget_status,
        },
        "truncation": {
            "truncated": bool(omitted),
            "reason": "token-budget" if omitted else "none",
            "omitted_operative_decision_ids": omitted,
        },
        "agent_visible_capsule": {
            "serialized_content": capsule,
            "utf8_byte_count": len(encoded),
            "sha256": hashlib.sha256(encoded).hexdigest(),
            "token_count": token_count,
            "tokenizer": tokenizer,
            "budget_status": budget_status,
        },
    }


def resolve_task_context(payload: object, register: str, *, revision: str, source_scope: str) -> dict[str, object]:
    request = validate_task_context_request(payload)
    return _render_task_context(request, parse_register(register), revision=revision, source_scope=source_scope)


def _audit_applicability(decision: Decision) -> dict[str, object]:
    scopes = list(decision.affected_layers)
    if not scopes and decision.applies_when is None:
        state, reason = "unconditional", "none"
    elif decision.applies_when is not None:
        state, reason = "undetermined", "conditional-unresolved"
    else:
        state, reason = "undetermined", "missing-task-signal"
    return {
        "state": state,
        "reason": reason,
        "matched_selectors": {},
        "required_selectors": ["repository_scopes"] if scopes else [],
    }


def _effective_provenances(decisions: tuple[Decision, ...]) -> dict[str, str]:
    by_id = {decision.decision_id: decision for decision in decisions}
    resolved: dict[str, str] = {}
    visiting: set[str] = set()

    def resolve(identifier: str) -> str:
        if identifier in resolved:
            return resolved[identifier]
        if identifier in visiting:
            raise ValueError(f"synthesis provenance cycle includes {identifier}")
        visiting.add(identifier)
        decision = by_id[identifier]
        values = [decision.provenance]
        if decision.derivation == "synthesized":
            values.extend(resolve(source_id) for source_id in decision.source_ids)
        visiting.remove(identifier)
        resolved[identifier] = min(values, key=PROVENANCE_RANK.__getitem__)
        return resolved[identifier]

    for identifier in by_id:
        resolve(identifier)
    return resolved


def build_decision_audit(register: str, *, project: str, revision: str, source_scope: str, decision_ids: list[str], include_related: bool = False) -> dict[str, object]:
    decisions = parse_register(register)
    by_id = {decision.decision_id: decision for decision in decisions}
    if not decision_ids or len(decision_ids) > 100 or len(decision_ids) != len(set(decision_ids)):
        raise ValueError("decision_ids must contain between 1 and 100 unique IDs")
    if any(not isinstance(identifier, str) or not ID_RE.fullmatch(identifier) for identifier in decision_ids):
        raise ValueError("decision IDs must be stable identifiers")
    if not isinstance(include_related, bool):
        raise ValueError("include_related must be a boolean")
    selected = set(decision_ids)
    unknown = selected.difference(by_id)
    if unknown:
        raise ValueError(f"unknown decision ID: {sorted(unknown)[0]}")
    if include_related:
        for decision in decisions:
            references = set(decision.supersedes) | set(decision.conflicts_with) | set(decision.source_ids)
            if decision.decision_id in selected or references.intersection(selected):
                selected.add(decision.decision_id)
    effective_provenance = _effective_provenances(decisions)
    records = []
    for decision in decisions:
        if decision.decision_id not in selected:
            continue
        metadata = decision.metadata
        records.append({
            "decision_id": decision.decision_id,
            "subject": decision.subject,
            "category": decision.category,
            "decision": decision.decision,
            "constraints": list(decision.constraints),
            "rationale": metadata.get("rationale"),
            "evidence": list(metadata.get("evidence", ())),
            "provenance": decision.provenance,
            "effective_provenance": effective_provenance[decision.decision_id],
            "derivation": decision.derivation,
            "source_ids": list(decision.source_ids),
            "source_scope": source_scope,
            "supersedes": list(decision.supersedes),
            "conflict_ids": list(decision.conflicts_with),
            "conflict_key": decision.conflict_key,
            "affected_layers": list(decision.affected_layers),
            "applies_when": decision.applies_when,
            "confidence": decision.confidence,
            "review": None if decision.review == "review_status" else decision.review,
            "applicability": _audit_applicability(decision),
        })
    return {
        "schema": "context-library/decision-audit-response",
        "schema_version": 1,
        "project": project,
        "revision": revision,
        "records": records,
    }
'''
    return header + source + generated_contracts + task_runtime.encode()


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
