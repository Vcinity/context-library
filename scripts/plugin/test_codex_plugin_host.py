#!/usr/bin/env python3
"""Exercise the packaged Plugin through Codex's local MCP configuration path."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
BUILD = ROOT / "scripts/build_plugin.py"
MARKETPLACE = {
    "name": "issue-70-test",
    "interface": {"displayName": "Issue 70 test"},
    "plugins": [
        {
            "name": "context-library",
            "source": {"source": "local", "path": "./plugins/context-library"},
            "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
            "category": "Productivity",
        }
    ],
}


def load_build() -> Any:
    spec = importlib.util.spec_from_file_location("issue70_build", BUILD)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def framed_request(proc: subprocess.Popen[bytes], request_id: int, method: str) -> dict[str, Any]:
    assert proc.stdin and proc.stdout
    body = json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": {}}).encode()
    proc.stdin.write(b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
    proc.stdin.flush()
    header = proc.stdout.readline()
    if not header:
        raise AssertionError(f"handshake failure: server exited with {proc.poll()}")
    if not header.startswith(b"Content-Length:"):
        raise AssertionError(f"handshake failure: unexpected header {header!r}")
    length = int(header.split(b":", 1)[1].strip())
    assert proc.stdout.readline() in {b"\r\n", b"\n"}
    return json.loads(proc.stdout.read(length))


def launch(config: dict[str, Any], *, cwd: Path, env: dict[str, str]) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        [config["command"], *config["args"]],
        cwd=cwd,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def assert_failure_cases(plugin_root: Path, env: dict[str, str]) -> None:
    immediate = {"command": sys.executable, "args": ["-c", "raise SystemExit(7)"]}
    exited = subprocess.run(
        [immediate["command"], *immediate["args"]], cwd=plugin_root, env=env, capture_output=True, text=True
    )
    assert exited.returncode == 7, "immediate process exit was not reported"

    missing = plugin_root / "mcp" / "missing_server.py"
    missing_result = subprocess.run(["python3", str(missing)], cwd=plugin_root, env=env, capture_output=True, text=True)
    assert missing_result.returncode != 0
    assert str(missing) in missing_result.stderr

    broken = launch(immediate, cwd=plugin_root, env=env)
    assert broken.stdin
    broken.wait(timeout=5)
    broken_pipe = False
    try:
        broken.stdin.write(b"x")
        broken.stdin.flush()
    except (BrokenPipeError, OSError):
        broken_pipe = True
    finally:
        if broken.poll() is None:
            broken.kill()
            broken.wait(timeout=5)
    assert broken_pipe, "broken pipe was not reported after immediate MCP process exit"
    assert broken.returncode == 7, "broken-pipe source process did not exit"

    handshake = launch({"command": sys.executable, "args": ["-c", "print('{}')"]}, cwd=plugin_root, env=env)
    try:
        try:
            framed_request(handshake, 1, "initialize")
        except (AssertionError, json.JSONDecodeError, BrokenPipeError, OSError):
            pass
        else:
            raise AssertionError("invalid MCP handshake unexpectedly succeeded")
    finally:
        handshake.kill()
        handshake.wait(timeout=5)


def main() -> int:
    codex = shutil.which("codex")
    if not codex:
        raise SystemExit("Codex CLI is required for the packaged Plugin host regression")

    build = load_build()
    with tempfile.TemporaryDirectory(prefix="context-library-codex-host-") as temp:
        root = Path(temp)
        archive = root / "plugin.zip"
        build.OUTPUT = archive
        assert build.main() == 0

        relocations = (Path("release") / "marketplace", Path("consumer-cache") / "nested" / "install")
        for index, relative in enumerate(relocations):
            case = root / f"case-{index}"
            marketplace = case / relative
            codex_home = case / "codex-home"
            consumer = case / "unrelated-consumer"
            codex_home.mkdir(parents=True)
            consumer.mkdir(parents=True)
            (marketplace / ".agents" / "plugins").mkdir(parents=True)
            (marketplace / "plugins").mkdir()
            with zipfile.ZipFile(archive) as package:
                package.extractall(marketplace / "plugins")
            (marketplace / ".agents/plugins/marketplace.json").write_text(json.dumps(MARKETPLACE), encoding="utf-8")

            env = os.environ.copy()
            env["CODEX_HOME"] = str(codex_home)
            env["CONTEXT_LIBRARY_ROOT"] = str(case / "library")
            (case / "library").mkdir()
            subprocess.run([codex, "plugin", "marketplace", "add", str(marketplace)], env=env, check=True)
            subprocess.run([codex, "plugin", "add", "context-library@issue-70-test"], env=env, check=True)

            listed = subprocess.run(
                [codex, "mcp", "get", "context_library", "--json"],
                cwd=consumer,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )
            config = json.loads(listed.stdout)["transport"]
            assert config["command"] == "python3"
            assert config["args"] == ["./mcp/context_library_server.py"]
            plugin_root = Path(config["cwd"]).resolve()
            assert plugin_root.is_dir()
            assert plugin_root != marketplace / "plugins/context-library"
            assert (plugin_root / config["args"][0][2:]).is_file()

            proc = launch(config, cwd=plugin_root, env=env)
            try:
                initialized = framed_request(proc, 1, "initialize")
                assert initialized["result"]["serverInfo"]["name"] == "context-library"
                tools = framed_request(proc, 2, "tools/list")
                names = {tool["name"] for tool in tools["result"]["tools"]}
                assert {"get_library_status", "search_decisions"} <= names
            finally:
                proc.terminate()
                proc.wait(timeout=5)

            assert_failure_cases(plugin_root, env)

    print("Codex packaged Plugin host regression passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
