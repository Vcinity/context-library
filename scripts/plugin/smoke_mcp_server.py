#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "plugins" / "context-library" / "mcp" / "context_library_server.py"
PROTOCOL_VERSIONS = ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25", "2026-07-28")


def encode_json_line(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, separators=(",", ":")).encode("utf-8") + b"\n"


def encode_framed(payload: dict[str, Any]) -> bytes:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return b"Content-Length: " + str(len(body)).encode("ascii") + b"\r\n\r\n" + body


def read_framed_response(stream: Any) -> dict[str, Any]:
    line = stream.readline()
    if not line:
        raise AssertionError("server closed stdout before responding")
    assert line.startswith(b"Content-Length:"), "server did not use MCP Content-Length framing"
    length = int(line.split(b":", 1)[1].strip())
    separator = stream.readline()
    assert separator in {b"\r\n", b"\n"}, f"unexpected MCP header separator: {separator!r}"
    body = stream.read(length)
    assert len(body) == length, "server closed stdout before full response body"
    return json.loads(body.decode("utf-8"))


def read_json_line_response(stream: Any) -> dict[str, Any]:
    line = stream.readline()
    if not line:
        raise AssertionError("server closed stdout before responding")
    assert not line.startswith(b"Content-Length:"), "server unexpectedly used framed response"
    return json.loads(line.decode("utf-8"))


def request(
    proc: subprocess.Popen[bytes],
    counter: int,
    method: str,
    params: dict[str, Any] | None = None,
    *,
    framed: bool,
) -> dict[str, Any]:
    assert proc.stdin is not None
    assert proc.stdout is not None
    payload = {"jsonrpc": "2.0", "id": counter, "method": method, "params": params or {}}
    proc.stdin.write(encode_framed(payload) if framed else encode_json_line(payload))
    proc.stdin.flush()
    response = read_framed_response(proc.stdout) if framed else read_json_line_response(proc.stdout)
    if "error" in response:
        raise AssertionError(response["error"])
    return response["result"]


def create_fixture(root: Path) -> None:
    artifacts = root / "decision-artifacts"
    artifacts.mkdir(parents=True)
    (artifacts / "README.md").write_text("# Fixture Pack\n", encoding="utf-8")
    (artifacts / "decision-register.md").write_text(
        "# Decision Register\n\n"
        "## Access Model\n\n"
        '<a id="access-read-only-mcp"></a>\n'
        "### Shared context library access\n\n"
        "- Decision: Use an MCP server for sandbox-safe read-only access.\n"
        "- Provenance: explicit\n",
        encoding="utf-8",
    )
    (artifacts / "index-by-category.md").write_text("# Category Index\n", encoding="utf-8")
    (artifacts / "index-by-date.md").write_text("# Date Index\n", encoding="utf-8")


def run_smoke(root: Path, *, framed: bool, protocol_version: str = "2025-06-18") -> None:
    env = os.environ.copy()
    env["CONTEXT_LIBRARY_ROOT"] = str(root)
    proc = subprocess.Popen(
        [sys.executable, str(SERVER)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    try:
        initialized = request(
            proc,
            1,
            "initialize",
            {
                "protocolVersion": protocol_version,
                "capabilities": {},
                "clientInfo": {"name": "smoke-test", "version": "1.0"},
            },
            framed=framed,
        )
        assert initialized["protocolVersion"] == protocol_version
        assert initialized["serverInfo"]["name"] == "context-library"

        tools = request(proc, 2, "tools/list", framed=framed)["tools"]
        tool_names = {tool["name"] for tool in tools}
        expected = {"get_library_status", "list_project_packs", "read_project_artifact", "search_decisions"}
        assert expected <= tool_names

        status = json.loads(
            request(proc, 3, "tools/call", {"name": "get_library_status"}, framed=framed)["content"][0]["text"]
        )
        assert status["exists"] is True
        assert status["readable"] is True

        packs = json.loads(
            request(proc, 4, "tools/call", {"name": "list_project_packs"}, framed=framed)["content"][0]["text"]
        )
        assert any(pack["name"] == "legacy" for pack in packs["packs"])

        artifact = json.loads(
            request(
                proc,
                5,
                "tools/call",
                {
                    "name": "read_project_artifact",
                    "arguments": {"project": "legacy", "artifact": "decision-register"},
                },
                framed=framed,
            )["content"][0]["text"]
        )
        assert "Use an MCP server" in artifact["text"] or "Decision Register" in artifact["text"]

        results = json.loads(
            request(
                proc,
                6,
                "tools/call",
                {"name": "search_decisions", "arguments": {"project": "legacy", "query": "MCP", "max_results": 5}},
                framed=framed,
            )["content"][0]["text"]
        )
        assert isinstance(results["matches"], list)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, help="Existing context-library root to test.")
    args = parser.parse_args()

    if args.root:
        for protocol_version in PROTOCOL_VERSIONS:
            run_smoke(args.root, framed=True, protocol_version=protocol_version)
        run_smoke(args.root, framed=False)
    else:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            create_fixture(root)
            for protocol_version in PROTOCOL_VERSIONS:
                run_smoke(root, framed=True, protocol_version=protocol_version)
            run_smoke(root, framed=False)
    print("context library MCP smoke test passed")


if __name__ == "__main__":
    main()
