#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from generated.core_runtime import (  # noqa: E402
    PRODUCT_VERSION,
    build_decision_audit,
    discover_packs,
    parse_register,
    resolve_pack,
    resolve_task_context,
)
import runtime_config  # noqa: E402

PROTOCOL_VERSION = "2024-11-05"
SUPPORTED_PROTOCOL_VERSIONS = {
    "2024-11-05",
    "2025-03-26",
    "2025-06-18",
    "2025-11-25",
    "2026-07-28",
}
SERVER_VERSION = PRODUCT_VERSION

ARTIFACTS = {
    "readme": "README.md",
    "decision-register": "decision-register.md",
    "index-by-category": "index-by-category.md",
    "index-by-date": "index-by-date.md",
}


class McpError(Exception):
    def __init__(self, message: str, code: int = -32000) -> None:
        super().__init__(message)
        self.code = code


_CONDITION_SUMMARIES = {
    runtime_config.CONDITION_MISSING_CONFIG: "context library root is not configured",
    runtime_config.CONDITION_MALFORMED_CONFIG: "context library runtime configuration is malformed",
    runtime_config.CONDITION_UNREADABLE_CONFIG: "context library runtime configuration is unreadable",
}


def _root_unavailable_message(status: runtime_config.RuntimePreflight) -> str:
    summary = _CONDITION_SUMMARIES.get(status.condition)
    if summary is None:
        if status.condition == runtime_config.CONDITION_MISSING_ROOT:
            summary = f"context library root does not exist: {status.library_root}"
        else:
            summary = f"context library root is not readable: {status.library_root}"
    if status.remediation:
        return f"{summary}. {status.remediation}"
    return summary


def library_root() -> Path:
    status = runtime_config.preflight()
    if not status.allowed:
        raise McpError(_root_unavailable_message(status))
    assert status.library_root is not None
    return Path(status.library_root).expanduser().resolve()


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise McpError(f"missing file: {path}") from exc
    except OSError as exc:
        raise McpError(f"unable to read {path}: {exc}") from exc


def read_register(project: str) -> tuple[str, str, str]:
    root = library_root()
    pack = project_pack_path(root, project)
    path = safe_child(pack, "decision-register.md")
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8")
    except FileNotFoundError as exc:
        raise McpError(f"missing decision register for project {project!r}") from exc
    except UnicodeDecodeError as exc:
        raise McpError(f"decision register for project {project!r} is not valid UTF-8") from exc
    except OSError as exc:
        raise McpError(f"unable to read decision register for project {project!r}: {exc}") from exc
    revision = f"sha256:{hashlib.sha256(raw).hexdigest()}"
    source_scope = path.parent.relative_to(root).as_posix()
    return text, revision, source_scope


def safe_child(root: Path, *parts: str) -> Path:
    base = root.resolve()
    target = base.joinpath(*parts).resolve()
    if target != base and base not in target.parents:
        raise McpError("path escapes context library root")
    return target


def project_pack_path(root: Path, project: str) -> Path:
    normalized = project.strip()
    if not normalized:
        raise McpError("project must be non-empty")
    if normalized in {"active", "default", "decision-artifacts"}:
        normalized = "legacy"
    selected = resolve_pack(discover_packs(root), normalized)
    if selected is None:
        return safe_child(root, "projects", normalized)
    return safe_child(root, *selected.register_path.parent.relative_to(root.resolve()).parts)


def pack_has_decision_artifacts(path: Path) -> bool:
    return (path / "decision-register.md").exists()


def get_library_status(_args: dict[str, Any] | None = None) -> dict[str, Any]:
    """Report the runtime preflight condition without raising.

    This tool is a status boundary, not an exception boundary: it is meant to
    be safe to call first, before normal library-dependent tools, so an agent
    can diagnose a missing, malformed, unreadable, missing-root, or
    unreadable-root deployment and receive redacted, actionable remediation.
    Every other MCP tool remains exception-based (McpError) because it needs
    the root to actually perform a read.
    """
    status = runtime_config.preflight()
    result: dict[str, Any] = {
        "schema": "context-library/plugin-runtime-status",
        "schema_version": 1,
        "condition": status.condition,
        "allowed": status.allowed,
        "config_path": status.config_path,
    }
    if status.config_source is not None:
        result["config_source"] = status.config_source
    if status.remediation is not None:
        result["remediation"] = status.remediation
    if status.library_root is not None:
        root = Path(status.library_root)
        readable = status.condition == runtime_config.CONDITION_HEALTHY
        result["root"] = str(root)
        result["exists"] = status.condition != runtime_config.CONDITION_MISSING_ROOT
        result["readable"] = readable
        result["active_pack"] = "legacy" if readable and (root / "decision-artifacts").exists() else None
    return result


def list_project_packs(args: dict[str, Any] | None = None) -> dict[str, Any]:
    args = args or {}
    include_incomplete = bool(args.get("include_incomplete", False))
    root = library_root()

    packs: list[dict[str, Any]] = [
        {
            "name": pack.project,
            "path": str(pack.register_path.parent),
            "kind": "legacy-flat-pack" if pack.location == "decision-artifacts" else "project-pack",
            "has_decision_register": pack.register_path.is_file(),
            "compatibility_locations": list(pack.compatibility_locations),
        }
        for pack in discover_packs(root, include_incomplete=include_incomplete)
    ]
    return {"root": str(root), "packs": packs}


def read_project_artifact(args: dict[str, Any] | None = None) -> dict[str, Any]:
    args = args or {}
    project = str(args.get("project", "")).strip()
    if not project:
        raise McpError("project must be explicitly selected")
    artifact = str(args.get("artifact", "decision-register"))
    if artifact not in ARTIFACTS:
        raise McpError(f"unknown artifact {artifact!r}; expected one of {sorted(ARTIFACTS)}")
    root = library_root()
    pack = project_pack_path(root, project)
    path = safe_child(pack, ARTIFACTS[artifact])
    text = read_text(path)
    return {
        "root": str(root),
        "project": project,
        "artifact": artifact,
        "path": str(path),
        "text": text,
    }


def search_decisions(args: dict[str, Any] | None = None) -> dict[str, Any]:
    args = args or {}
    query = str(args.get("query", "")).strip()
    if not query:
        raise McpError("query must be non-empty")
    project = str(args.get("project", "")).strip()
    if not project:
        raise McpError("project must be explicitly selected")
    max_results = int(args.get("max_results", 10))
    if max_results < 1 or max_results > 50:
        raise McpError("max_results must be between 1 and 50")

    artifact = read_project_artifact({"project": project, "artifact": "decision-register"})
    decisions = parse_register(artifact["text"])
    matches: list[dict[str, Any]] = []
    query_lower = query.lower()
    for decision in decisions:
        searchable = "\n".join(
            (
                decision.decision_id,
                decision.subject,
                decision.decision,
                *decision.constraints,
                *(str(value) for value in decision.metadata.values()),
            )
        )
        if query_lower in searchable.lower():
            matches.append(
                {
                    "decision_id": decision.decision_id,
                    "subject": decision.subject,
                    "excerpt": decision.decision,
                    "provenance": decision.provenance,
                }
            )
            if len(matches) >= max_results:
                break

    return {
        "project": project,
        "query": query,
        "path": artifact["path"],
        "matches": matches,
    }


def get_task_context(args: dict[str, Any] | None = None) -> dict[str, Any]:
    args = dict(args or {})
    project = str(args.get("project", "")).strip()
    if not project:
        raise McpError("project must be explicitly selected")
    request_project = args.get("project")
    if request_project != project:
        raise McpError("project must be a stable lowercase identifier")
    register, revision, source_scope = read_register(project)
    try:
        return resolve_task_context(args, register, revision=revision, source_scope=source_scope)
    except (ValueError, KeyError) as exc:
        raise McpError(str(exc)) from exc


def read_decision_audit(args: dict[str, Any] | None = None) -> dict[str, Any]:
    args = args or {}
    project = str(args.get("project", "")).strip()
    if not project:
        raise McpError("project must be explicitly selected")
    schema = args.get("schema", "context-library/decision-audit-response")
    if schema != "context-library/decision-audit-response":
        raise McpError("unsupported decision-audit schema family")
    if args.get("schema_version", 1) != 1:
        raise McpError("unsupported decision-audit schema version")
    if "include_related" in args and not isinstance(args["include_related"], bool):
        raise McpError("include_related must be a boolean")
    decision_ids = args.get("decision_ids")
    if not isinstance(decision_ids, list):
        raise McpError("decision_ids must be a list")
    try:
        register, revision, source_scope = read_register(project)
        return build_decision_audit(
            register,
            project=project,
            revision=revision,
            source_scope=source_scope,
            decision_ids=decision_ids,
            include_related=bool(args.get("include_related", False)),
        )
    except (ValueError, KeyError) as exc:
        raise McpError(str(exc)) from exc


TOOLS: dict[str, dict[str, Any]] = {
    "get_library_status": {
        "description": (
            "Report the runtime preflight condition (healthy, missing_config, malformed_config, "
            "unreadable_config, missing_root, or unreadable_root) with redacted, actionable "
            "remediation. Safe to call first; never raises for a bad deployment state."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "handler": get_library_status,
    },
    "list_project_packs": {
        "description": "List project packs available in the shared context library.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "include_incomplete": {
                    "type": "boolean",
                    "description": "Include scaffold directories that do not yet have decision-register.md.",
                }
            },
            "additionalProperties": False,
        },
        "handler": list_project_packs,
    },
    "read_project_artifact": {
        "description": "Read a context-library artifact for a project pack.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "artifact": {
                    "type": "string",
                    "enum": sorted(ARTIFACTS),
                    "default": "decision-register",
                },
            },
            "required": ["project"],
            "additionalProperties": False,
        },
        "handler": read_project_artifact,
    },
    "search_decisions": {
        "description": "Search a project's decision register for matching decision text.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "query": {"type": "string"},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
            },
            "required": ["project", "query"],
            "additionalProperties": False,
        },
        "handler": search_decisions,
    },
    "get_task_context": {
        "description": "Resolve an explicitly project-bound task into the compact RT-01 context response.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "schema": {"type": "string", "const": "context-library/task-context-request", "default": "context-library/task-context-request"},
                "schema_version": {"type": "integer", "const": 1, "default": 1},
                "project": {"type": "string"},
                "task_summary": {"type": "string"},
                "operation": {"type": "string"},
                "repository_scopes": {"type": "array", "items": {"type": "string"}},
                "agent_token_budget": {"type": "integer", "minimum": 0},
                "tokenizer": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Tokenizer name (e.g., 'tiktoken')"},
                        "version": {"type": "string", "description": "Tokenizer version (e.g., '0.9.0')"},
                        "vocabulary_revision": {
                            "type": "string",
                            "description": "Vocabulary identifier (e.g., 'cl100k_base')",
                        },
                        "accounting_method": {
                            "type": "string",
                            "description": "Method for counting tokens (e.g., 'offline')",
                        },
                        "pinned": {
                            "type": "boolean",
                            "const": True,
                            "default": True,
                            "description": "Must be true to support deterministic token counting",
                        },
                    },
                    "required": ["name", "version", "vocabulary_revision", "accounting_method"],
                    "additionalProperties": False,
                },
            },
            "required": ["project", "task_summary", "operation", "repository_scopes", "agent_token_budget", "tokenizer"],
            "additionalProperties": False,
        },
        "handler": get_task_context,
    },
    "read_decision_audit": {
        "description": "Read full canonical records for explicitly selected decision IDs without canonical writes.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "schema": {"type": "string", "const": "context-library/decision-audit-response", "default": "context-library/decision-audit-response"},
                "schema_version": {"type": "integer", "const": 1, "default": 1},
                "project": {"type": "string"},
                "decision_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 100},
                "include_related": {"type": "boolean", "default": False},
            },
            "required": ["project", "decision_ids"],
            "additionalProperties": False,
        },
        "handler": read_decision_audit,
    },
}


def tool_descriptions() -> list[dict[str, Any]]:
    return [
        {
            "name": name,
            "description": payload["description"],
            "inputSchema": payload["inputSchema"],
        }
        for name, payload in TOOLS.items()
    ]


def call_tool(name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
    tool = TOOLS.get(name)
    if tool is None:
        raise McpError(f"unknown tool: {name}", code=-32602)
    result = tool["handler"](arguments or {})
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(result, indent=2, sort_keys=True),
            }
        ],
        "isError": False,
    }


def handle_request(method: str, params: dict[str, Any] | None) -> Any:
    params = params or {}
    if method == "initialize":
        requested_version = params.get("protocolVersion")
        protocol_version = requested_version if requested_version in SUPPORTED_PROTOCOL_VERSIONS else PROTOCOL_VERSION
        return {
            "protocolVersion": protocol_version,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "context-library", "version": SERVER_VERSION},
        }
    if method == "tools/list":
        return {"tools": tool_descriptions()}
    if method == "tools/call":
        return call_tool(str(params.get("name", "")), params.get("arguments") or {})
    raise McpError(f"unsupported method: {method}", code=-32601)


def encode_json_line(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, separators=(",", ":")).encode("utf-8") + b"\n"


def encode_framed_message(payload: dict[str, Any]) -> bytes:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return b"Content-Length: " + str(len(body)).encode("ascii") + b"\r\n\r\n" + body


def read_message(stream: Any) -> tuple[dict[str, Any], bool] | None:
    line = stream.readline()
    if line == b"":
        return None
    if line.lower().startswith(b"content-length:"):
        try:
            content_length = int(line.split(b":", 1)[1].strip())
        except ValueError as exc:
            raise McpError("invalid Content-Length header", code=-32700) from exc
        while True:
            header = stream.readline()
            if header == b"":
                raise McpError("unexpected EOF while reading MCP headers", code=-32700)
            if header in {b"\r\n", b"\n"}:
                break
        body = stream.read(content_length)
        if len(body) != content_length:
            raise McpError("unexpected EOF while reading MCP body", code=-32700)
        try:
            return json.loads(body.decode("utf-8")), True
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise McpError(f"invalid JSON-RPC message: {exc}", code=-32700) from exc
    try:
        return json.loads(line.decode("utf-8")), False
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise McpError(f"invalid JSON-RPC message: {exc}", code=-32700) from exc


def serve() -> None:
    stdin = sys.stdin.buffer
    stdout = sys.stdout.buffer
    while True:
        request_id = None
        framed = True
        try:
            message = read_message(stdin)
            if message is None:
                return
            request, framed = message
            request_id = request.get("id")
            if request_id is None:
                continue
            result = handle_request(str(request.get("method", "")), request.get("params") or {})
            response = {"jsonrpc": "2.0", "id": request_id, "result": result}
        except McpError as exc:
            response = {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": exc.code, "message": str(exc)},
            }
        except Exception as exc:  # pragma: no cover - last-resort protocol error guard.
            response = {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32603, "message": f"internal error: {exc}"},
            }
        if framed:
            stdout.write(encode_framed_message(response))
        else:
            stdout.write(encode_json_line(response))
        stdout.flush()


if __name__ == "__main__":
    serve()
