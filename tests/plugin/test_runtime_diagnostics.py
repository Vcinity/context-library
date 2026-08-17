"""Fresh-process preflight diagnostics: packaged and staged recovery coverage.

These tests exercise the bundled MCP server as a genuinely fresh subprocess
per Plugin runtime condition, proving the deterministic preflight
classification (missing/malformed/unreadable config, missing/unreadable
root, healthy) is consumed identically by the status boundary and by
library-dependent tools, and that a staged deployment recovers across a
process restart after remediation.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).parents[2]
PLUGIN_SOURCE = ROOT / "plugins/context-library"
SERVER_RELATIVE = Path("mcp/context_library_server.py")
CONFIGURE_RELATIVE = Path("scripts/configure.py")


def load_smoke_helpers():
    spec = importlib.util.spec_from_file_location(
        "context_library_smoke_helpers", ROOT / "scripts/plugin/smoke_mcp_server.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SMOKE = load_smoke_helpers()


def stage_plugin(destination: Path) -> Path:
    """Copy the Plugin directory to an isolated location outside the source tree."""
    shutil.copytree(PLUGIN_SOURCE, destination, ignore=shutil.ignore_patterns("runtime-config.json", "__pycache__"))
    assert not (destination / "runtime-config.json").exists()
    return destination


def call_status(server_path: Path, cwd: Path, env: dict[str, str]) -> dict[str, Any]:
    proc = subprocess.Popen(
        [sys.executable, str(server_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=cwd,
        env=env,
    )
    try:
        SMOKE.request(
            proc,
            1,
            "initialize",
            {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "diagnostics-test", "version": "1.0"}},
            framed=True,
        )
        result = SMOKE.request(proc, 2, "tools/call", {"name": "get_library_status"}, framed=True)
        return json.loads(result["content"][0]["text"])
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def call_list_packs_expect_error(server_path: Path, cwd: Path, env: dict[str, str]) -> str:
    proc = subprocess.Popen(
        [sys.executable, str(server_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=cwd,
        env=env,
    )
    try:
        SMOKE.request(
            proc,
            1,
            "initialize",
            {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "diagnostics-test", "version": "1.0"}},
            framed=True,
        )
        payload = {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "list_project_packs", "arguments": {}}}
        proc.stdin.write(SMOKE.encode_framed(payload))
        proc.stdin.flush()
        response = SMOKE.read_framed_response(proc.stdout)
        assert "error" in response
        return str(response["error"]["message"])
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def base_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    for name in ("CONTEXT_LIBRARY_ROOT", "CONTEXT_LIBRARY_PROJECT", "CONTEXT_LIBRARY_CONTEXT_REQUIREMENT"):
        env.pop(name, None)
    if extra:
        env.update(extra)
    return env


def test_fresh_process_reports_missing_config(tmp_path):
    staged = stage_plugin(tmp_path / "staged")
    status = call_status(staged / SERVER_RELATIVE, tmp_path, base_env())
    assert status["condition"] == "missing_config"
    assert status["allowed"] is False
    assert "remediation" in status
    error = call_list_packs_expect_error(staged / SERVER_RELATIVE, tmp_path, base_env())
    assert "not configured" in error


def test_fresh_process_reports_malformed_config(tmp_path):
    staged = stage_plugin(tmp_path / "staged")
    (staged / "runtime-config.json").write_text(json.dumps({"schema": "wrong"}), encoding="utf-8")
    status = call_status(staged / SERVER_RELATIVE, tmp_path, base_env())
    assert status["condition"] == "malformed_config"
    assert status["allowed"] is False
    error = call_list_packs_expect_error(staged / SERVER_RELATIVE, tmp_path, base_env())
    assert "malformed" in error


def test_fresh_process_reports_unreadable_config(tmp_path):
    if os.geteuid() == 0:
        pytest.skip("cannot exercise unreadable files while running as root")
    staged = stage_plugin(tmp_path / "staged")
    config = staged / "runtime-config.json"
    library = tmp_path / "library"
    library.mkdir()
    config.write_text(
        json.dumps(
            {
                "schema": "context-library/plugin-runtime-config",
                "schema_version": 1,
                "library_root": str(library),
            }
        ),
        encoding="utf-8",
    )
    config.chmod(0o000)
    try:
        status = call_status(staged / SERVER_RELATIVE, tmp_path, base_env())
    finally:
        config.chmod(0o644)
    assert status["condition"] == "unreadable_config"
    assert status["allowed"] is False


def test_fresh_process_reports_missing_root(tmp_path):
    staged = stage_plugin(tmp_path / "staged")
    missing_root = tmp_path / "does-not-exist"
    (staged / "runtime-config.json").write_text(
        json.dumps(
            {
                "schema": "context-library/plugin-runtime-config",
                "schema_version": 1,
                "library_root": str(missing_root),
            }
        ),
        encoding="utf-8",
    )
    status = call_status(staged / SERVER_RELATIVE, tmp_path, base_env())
    assert status["condition"] == "missing_root"
    assert status["allowed"] is False
    assert status["exists"] is False
    error = call_list_packs_expect_error(staged / SERVER_RELATIVE, tmp_path, base_env())
    assert "does not exist" in error


def test_fresh_process_reports_unreadable_root(tmp_path):
    if os.geteuid() == 0:
        pytest.skip("cannot exercise unreadable directories while running as root")
    staged = stage_plugin(tmp_path / "staged")
    library = tmp_path / "library"
    library.mkdir()
    (staged / "runtime-config.json").write_text(
        json.dumps(
            {
                "schema": "context-library/plugin-runtime-config",
                "schema_version": 1,
                "library_root": str(library),
            }
        ),
        encoding="utf-8",
    )
    library.chmod(0o000)
    try:
        status = call_status(staged / SERVER_RELATIVE, tmp_path, base_env())
    finally:
        library.chmod(0o755)
    assert status["condition"] == "unreadable_root"
    assert status["allowed"] is False
    assert status["exists"] is True
    assert status["readable"] is False


def test_staged_recovery_across_fresh_process_restart(tmp_path):
    """Stage a synthetic release outside the source tree, invoke it from an
    unrelated working directory, remediate configuration, restart, and prove
    successful recovery without mutating canonical fixtures."""
    staged = stage_plugin(tmp_path / "release")
    unrelated_cwd = tmp_path / "elsewhere"
    unrelated_cwd.mkdir()

    before = call_status(staged / SERVER_RELATIVE, unrelated_cwd, base_env())
    assert before["condition"] == "missing_config"

    library = tmp_path / "synthetic-library"
    library.mkdir()
    (library / "projects/demo").mkdir(parents=True)
    (library / "projects/demo/decision-register.md").write_text("# Decision Register\n", encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            str(staged / CONFIGURE_RELATIVE),
            "--library-root",
            str(library),
            "--project",
            "demo",
            "--output",
            str(staged / "runtime-config.json"),
        ],
        check=True,
        capture_output=True,
        text=True,
        cwd=unrelated_cwd,
    )
    assert completed.returncode == 0

    after = call_status(staged / SERVER_RELATIVE, unrelated_cwd, base_env())
    assert after["condition"] == "healthy"
    assert after["allowed"] is True
    assert after["root"] == str(library.resolve())

    packs_proc = subprocess.Popen(
        [sys.executable, str(staged / SERVER_RELATIVE)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=unrelated_cwd,
        env=base_env(),
    )
    try:
        SMOKE.request(
            packs_proc,
            1,
            "initialize",
            {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "diagnostics-test", "version": "1.0"}},
            framed=True,
        )
        packs = json.loads(
            SMOKE.request(packs_proc, 2, "tools/call", {"name": "list_project_packs"}, framed=True)["content"][0]["text"]
        )
    finally:
        packs_proc.terminate()
        try:
            packs_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            packs_proc.kill()
    assert any(pack["name"] == "demo" for pack in packs["packs"])
