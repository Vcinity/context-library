#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from generated.core_runtime import PRODUCT_VERSION, discover_packs, parse_register, resolve_pack  # noqa: E402
from runtime_config import RuntimeConfigError, setting  # noqa: E402

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


def library_root() -> Path:
    try:
        configured = setting("library_root").value
    except RuntimeConfigError as exc:
        raise McpError(str(exc)) from exc
    if not configured:
        raise McpError("context library root is not configured")
    return Path(configured).expanduser().resolve()


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise McpError(f"missing file: {path}") from exc
    except OSError as exc:
        raise McpError(f"unable to read {path}: {exc}") from exc


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
    root = library_root()
    exists = root.exists()
    return {
        "root": str(root),
        "exists": exists,
        "readable": os.access(root, os.R_OK) if exists else False,
        "active_pack": "legacy" if (root / "decision-artifacts").exists() else None,
    }


def list_project_packs(args: dict[str, Any] | None = None) -> dict[str, Any]:
    args = args or {}
    include_incomplete = bool(args.get("include_incomplete", False))
    root = library_root()
    if not root.exists():
        raise McpError(f"context library root does not exist: {root}")

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


TOOLS: dict[str, dict[str, Any]] = {
    "get_library_status": {
        "description": "Report whether the configured shared context library root exists and is readable.",
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
