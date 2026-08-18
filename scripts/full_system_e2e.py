#!/usr/bin/env python3
"""Run the deterministic, offline full-system acceptance slice for issue #49."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_SOURCE = ROOT / "plugins/context-library"
SERVER_RELATIVE = Path("mcp/context_library_server.py")
VERSION = "0.4.5"
EXPECTED_TOOLS = {
    "get_library_status",
    "list_project_packs",
    "read_project_artifact",
    "search_decisions",
    "get_task_context",
    "read_decision_audit",
}


class E2EFailure(RuntimeError):
    def __init__(self, message: str, failure_class: str = "assertion") -> None:
        super().__init__(message)
        self.failure_class = failure_class


def run(command: list[str], *, cwd: Path = ROOT, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True)
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()[-2000:]
        raise E2EFailure(f"command failed ({result.returncode}): {' '.join(command)}: {detail}", "subprocess")
    return result


def digest(root: Path) -> str:
    result = run(["git", "-C", str(root), "rev-parse", "HEAD"])
    return result.stdout.strip()


def create_library(root: Path) -> str:
    pack = root / "projects/demo"
    pack.mkdir(parents=True)
    register = """# Decision Register

## E2E authority

<a id="e2e-read-boundary"></a>
### Packaged access remains read-only
- Category: authority
- Date: 2026-08-17
- Decisionmaker: Synthetic Owner
- Decision: Keep the packaged Context Library access read-only.
- Constraint: Canonical changes use the Manager and typed Maintainer service.
- Rationale: The acceptance fixture proves authority separation.
- Provenance: explicit
- Derivation: direct
- Evidence: `fixture://issue-49/read-boundary`

<a id="e2e-task-context"></a>
### Task context is project-bound
- Category: retrieval
- Date: 2026-08-17
- Decisionmaker: Synthetic Owner
- Decision: Resolve task context only for the explicitly selected project.
- Rationale: The acceptance fixture proves project scoping.
- Provenance: explicit
- Derivation: direct
- Evidence: `fixture://issue-49/task-context`
"""
    (pack / "decision-register.md").write_text(register, encoding="utf-8")
    (pack / "README.md").write_text("# Synthetic demo pack\n", encoding="utf-8")
    (pack / "index-by-category.md").write_text("# Category Index\n", encoding="utf-8")
    (pack / "index-by-date.md").write_text("# Date Index\n", encoding="utf-8")
    run(["git", "init", "-q", str(root)])
    run(["git", "-C", str(root), "config", "user.name", "Context Library E2E"])
    run(["git", "-C", str(root), "config", "user.email", "e2e@example.invalid"])
    run(["git", "-C", str(root), "add", "."])
    run(["git", "-C", str(root), "commit", "-qm", "synthetic e2e baseline"])
    return digest(root)


def load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise E2EFailure(f"unable to load module {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class McpClient:
    def __init__(self, server: Path, cwd: Path, env: dict[str, str], framed: bool) -> None:
        self.framed = framed
        self.process = subprocess.Popen(
            [sys.executable, str(server)],
            cwd=cwd,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def _read(self) -> dict[str, Any]:
        assert self.process.stdout is not None
        first = self.process.stdout.readline()
        if not first:
            stderr = self.process.stderr.read().decode("utf-8", "replace") if self.process.stderr else ""
            raise E2EFailure(f"MCP subprocess exited before response: {stderr[-1000:]}", "subprocess")
        if self.framed:
            if not first.lower().startswith(b"content-length:"):
                raise E2EFailure("MCP framed response omitted Content-Length", "protocol")
            length = int(first.split(b":", 1)[1].strip())
            separator = self.process.stdout.readline()
            if separator not in {b"\r\n", b"\n"}:
                raise E2EFailure("MCP framed response has invalid header separator", "protocol")
            body = self.process.stdout.read(length)
            if len(body) != length:
                raise E2EFailure("MCP framed response was truncated", "protocol")
        else:
            if first.lower().startswith(b"content-length:"):
                raise E2EFailure("MCP newline response unexpectedly used framing", "protocol")
            body = first
        try:
            return json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise E2EFailure(f"MCP returned invalid JSON: {exc}", "protocol") from exc

    def request(self, request_id: int, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        assert self.process.stdin is not None
        payload = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        if self.framed:
            message = b"Content-Length: " + str(len(body)).encode("ascii") + b"\r\n\r\n" + body
        else:
            message = body + b"\n"
        self.process.stdin.write(message)
        self.process.stdin.flush()
        return self._read()

    def close(self) -> None:
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)


def call_tool(client: McpClient, request_id: int, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    response = client.request(request_id, "tools/call", {"name": name, "arguments": arguments})
    if "error" in response:
        raise E2EFailure(f"valid MCP tool {name} returned an error: {response['error']}", "mcp_error")
    result = response.get("result")
    if not isinstance(result, dict) or result.get("isError") is not False:
        raise E2EFailure(f"valid MCP tool {name} did not return a successful result", "mcp_error")
    content = result.get("content")
    if not isinstance(content, list) or not content or not isinstance(content[0], dict):
        raise E2EFailure(f"MCP tool {name} returned no structured content", "mcp_error")
    return json.loads(str(content[0]["text"]))


def run_mcp_phase(plugin_root: Path, library: Path, unrelated: Path) -> dict[str, Any]:
    server = plugin_root / SERVER_RELATIVE
    env = os.environ.copy()
    env.pop("CONTEXT_LIBRARY_ROOT", None)
    outcomes: list[dict[str, Any]] = []
    valid_args = {
        "get_library_status": {},
        "list_project_packs": {},
        "read_project_artifact": {"project": "demo", "artifact": "decision-register"},
        "search_decisions": {"project": "demo", "query": "read-only", "max_results": 5},
        "get_task_context": {
            "project": "demo",
            "task_summary": "retrieve the access boundary",
            "operation": "query",
            "repository_scopes": ["src"],
            "agent_token_budget": 1000,
            "tokenizer": {
                "name": "tiktoken",
                "version": "0.9.0",
                "vocabulary_revision": "cl100k_base",
                "accounting_method": "offline",
                "pinned": True,
            },
        },
        "read_decision_audit": {"project": "demo", "decision_ids": ["e2e-read-boundary"]},
    }
    for framed in (True, False):
        client = McpClient(server, unrelated, env, framed)
        try:
            initialized = client.request(
                1,
                "initialize",
                {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "issue-49-e2e", "version": VERSION},
                },
            )
            result = initialized.get("result", {})
            if result.get("serverInfo", {}).get("version") != VERSION:
                raise E2EFailure("MCP serverInfo version disagrees with release", "version_mismatch")
            tools_response = client.request(2, "tools/list")
            tools = tools_response.get("result", {}).get("tools", [])
            names = {item.get("name") for item in tools}
            if names != EXPECTED_TOOLS:
                raise E2EFailure(f"advertised MCP tools differ: {sorted(names)}", "contract")
            for request_id, name in enumerate(sorted(names), 3):
                value = call_tool(client, request_id, name, valid_args[name])
                if name == "get_library_status" and value.get("condition") != "healthy":
                    raise E2EFailure("configured MCP status was not healthy", "configuration")
                if name == "read_project_artifact" and "read-only" not in value.get("text", ""):
                    raise E2EFailure("MCP artifact response omitted the fixture decision", "mcp_error")
            invalid = client.request(20, "tools/call", {"name": "unknown_tool", "arguments": {}})
            if "error" not in invalid:
                raise E2EFailure("unknown MCP tool was not rejected", "mcp_error")
            traversal = client.request(
                21,
                "tools/call",
                {"name": "read_project_artifact", "arguments": {"project": "../outside"}},
            )
            if "error" not in traversal:
                raise E2EFailure("MCP traversal request was not rejected", "security")
            outcomes.append({"framing": "content-length" if framed else "newline", "tools": sorted(names)})
        finally:
            client.close()
    return {"subprocess_restarts": len(outcomes), "coverage": outcomes}


def configuration_phase(plugin_root: Path, library: Path, temporary: Path) -> dict[str, str]:
    cases: dict[str, str] = {}
    for name in ("missing_config", "malformed_config", "missing_root", "unreadable_config"):
        case = temporary / f"plugin-{name}"
        shutil.copytree(plugin_root, case)
        config = case / "runtime-config.json"
        if name == "missing_config":
            config.unlink()
        elif name == "malformed_config":
            config.write_text("{", encoding="utf-8")
        elif name == "missing_root":
            config.write_text(
                json.dumps(
                    {
                        "schema": "context-library/plugin-runtime-config",
                        "schema_version": 1,
                        "library_root": str(temporary / "does-not-exist"),
                    }
                ),
                encoding="utf-8",
            )
        else:
            config.chmod(0)
        env = os.environ.copy()
        env.pop("CONTEXT_LIBRARY_ROOT", None)
        client = McpClient(case / SERVER_RELATIVE, temporary, env, framed=False)
        try:
            response = client.request(1, "tools/call", {"name": "get_library_status", "arguments": {}})
            status = json.loads(response["result"]["content"][0]["text"])
            expected = name
            if status.get("condition") != expected or status.get("allowed") is not False:
                raise E2EFailure(f"configuration case {name} classified as {status}", "configuration")
            cases[name] = status["condition"]
        finally:
            client.close()
            if name == "unreadable_config":
                config.chmod(stat.S_IRUSR | stat.S_IWUSR)
    return cases


def projection_phase(library: Path, temporary: Path) -> dict[str, Any]:
    if str(PLUGIN_SOURCE) not in sys.path:
        sys.path.insert(0, str(PLUGIN_SOURCE))
    module = load_module("issue49_projection", PLUGIN_SOURCE / "projection.py")
    old_root = os.environ.get("CONTEXT_LIBRARY_ROOT")
    os.environ["CONTEXT_LIBRARY_ROOT"] = str(library)
    try:
        consumer = temporary / "consumer"
        consumer.mkdir()
        config_path = consumer / ".context-library/config.json"
        config_path.parent.mkdir()
        config_path.write_text(
            json.dumps(
                {
                    "schema": "context-library/context-policy",
                    "schema_version": 1,
                    "project": "demo",
                    "context_requirement": "optional",
                    "affected_layers": {},
                }
            ),
            encoding="utf-8",
        )
        first = module.sync(consumer)
        second = module.sync(consumer)
        module.check(consumer)
        if not first or second:
            raise E2EFailure("projection sync was not first-write/idempotent-second-write", "idempotency")
        outcomes: dict[str, str] = {"optional": "available"}
        old_project = os.environ.get("CONTEXT_LIBRARY_PROJECT")
        for requirement, project, expected in (
            ("required", "missing", "missing"),
            ("undetermined", "demo", "undetermined"),
            ("disabled", "demo", "disabled"),
        ):
            if requirement == "undetermined":
                policy = {}
                os.environ["CONTEXT_LIBRARY_PROJECT"] = project
            else:
                os.environ.pop("CONTEXT_LIBRARY_PROJECT", None)
                policy = {
                    "schema": "context-library/context-policy",
                    "schema_version": 1,
                    "project": project,
                    "context_requirement": requirement,
                    "affected_layers": {},
                }
            config_path.write_text(json.dumps(policy), encoding="utf-8")
            try:
                module.prepare(consumer)
            except Exception as exc:
                message = str(exc).lower()
                if (
                    expected not in message
                    and not (expected == "missing" and "unavailable" in message)
                    and not (expected == "undetermined" and "explicit required or optional" in message)
                ):
                    raise E2EFailure(f"{requirement} applicability was misclassified: {exc}", "applicability")
                outcomes[requirement] = expected
            else:
                raise E2EFailure(f"{requirement} applicability unexpectedly succeeded", "applicability")
        if old_project is None:
            os.environ.pop("CONTEXT_LIBRARY_PROJECT", None)
        else:
            os.environ["CONTEXT_LIBRARY_PROJECT"] = old_project
        return {"sync_first": first, "sync_second": second, "classifications": outcomes}
    finally:
        if old_root is None:
            os.environ.pop("CONTEXT_LIBRARY_ROOT", None)
        else:
            os.environ["CONTEXT_LIBRARY_ROOT"] = old_root


def manager_phase() -> dict[str, Any]:
    result = run([sys.executable, str(ROOT / "scripts/smoke_context_library.py")])
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        raise E2EFailure("Manager smoke emitted no machine-readable result", "subprocess")
    try:
        payload = json.loads(lines[-1])
    except json.JSONDecodeError as exc:
        raise E2EFailure("Manager smoke did not emit JSON evidence", "contract") from exc
    required = {"source", "observation", "candidate", "core_parser", "plugin_mcp", "plugin_projection"}
    if not required <= payload.keys() or payload.get("canonical_live_checkout_mutation") != "none":
        raise E2EFailure(f"Manager/Core/Plugin workflow evidence incomplete: {payload}", "workflow")
    return payload


def cli_phase(library: Path, state: Path) -> dict[str, Any]:
    version = run(["poetry", "run", "clm", "version", "--json"]).stdout
    capabilities = run(["poetry", "run", "clm", "capabilities", "--json"]).stdout
    query = run(
        [
            "poetry",
            "run",
            "clm",
            "query",
            "--json",
            "--library-root",
            str(library),
            "--project",
            "demo",
            "--q",
            "read-only",
        ]
    ).stdout
    parsed = [json.loads(value) for value in (version, capabilities, query)]
    if not all(item.get("data", {}).get("product_version") == VERSION for item in parsed[:2]):
        raise E2EFailure("clm version/capabilities did not report the release", "version_mismatch")
    if not parsed[2].get("data", {}).get("items"):
        raise E2EFailure("clm query did not find the synthetic decision", "workflow")
    return {"version": VERSION, "capabilities": "passed", "query": "passed", "state_root": str(state)}


def recovery_phase() -> dict[str, Any]:
    tests = [
        "tests/manager/test_library_workflow.py::test_proposal_submit_survives_api_restart_after_preview",
        "tests/manager/test_library_workflow.py::test_proposal_preview_is_read_only_and_submission_is_idempotent",
        "tests/manager/test_runtime_correctness.py::test_source_replay_recovers_an_expired_lease_and_finishes_original_request",
        "tests/manager/test_runtime_correctness.py::test_publication_exception_creates_linked_safe_history_and_audit",
        "tests/plugin/test_mcp_read_only.py::test_mcp_path_traversal_cannot_escape_library_root",
        "tests/maintainer/test_safety.py::test_typed_service_honors_cancellation_and_timeout_boundaries",
        "tests/manager/test_runtime.py::test_notification_failure_is_visible_and_backed_off",
        "tests/manager/test_auth_sessions.py::test_malformed_json_jwt_and_bearer_only_html_fail_closed",
    ]
    run(["poetry", "run", "pytest", "-q", *tests])
    return {
        "injected_boundaries": [
            "malformed_input",
            "duplicate_idempotency",
            "provider_timeout",
            "publication_stage_failure",
            "notification_failure",
            "lease_expiry",
            "process_restart",
            "traversal",
        ],
        "regression_tests": tests,
    }


def main() -> int:
    phases: list[dict[str, Any]] = []
    result: dict[str, Any] = {
        "schema": "context-library/full-system-e2e-result",
        "schema_version": 1,
        "product_version": VERSION,
        "phases": phases,
    }
    try:
        with tempfile.TemporaryDirectory(prefix="context-library-full-e2e-") as directory:
            temporary = Path(directory)
            library = temporary / "canonical"
            state = temporary / "maintainer-state"
            unrelated = temporary / "unrelated-working-directory"
            unrelated.mkdir()
            before = create_library(library)
            result["fixture_revision"] = before

            def phase(name: str, action: Callable[[], dict[str, Any]]) -> None:
                try:
                    evidence = action()
                    phases.append({"name": name, "status": "passed", "evidence": evidence})
                except E2EFailure:
                    raise
                except Exception as exc:
                    raise E2EFailure(f"{name}: {exc}", "unclassified") from exc

            phase(
                "artifact_isolation_and_version",
                lambda: _stage_and_isolate(temporary, library),
            )
            staged = temporary / "marketplace"
            plugin_root = staged / "plugins/context-library"
            phase("mcp_protocol_tool_coverage_restart", lambda: run_mcp_phase(plugin_root, library, unrelated))
            phase("mcp_configuration_failures", lambda: configuration_phase(plugin_root, library, temporary))
            phase("manager_maintainer_core_projection", manager_phase)
            phase("consumer_applicability_and_idempotency", lambda: projection_phase(library, temporary))
            phase("clm_boundaries", lambda: cli_phase(library, state))
            phase("failure_injection_and_recovery", recovery_phase)
            after = digest(library)
            if after != before:
                raise E2EFailure(f"canonical digest changed: {before} -> {after}", "canonical_write")
            result["canonical_digest_before"] = before
            result["canonical_digest_after"] = after
            result["browser_workflow"] = {
                "runner": "make e2e Playwright phase",
                "specs": ["e2e/library-mobile.spec.ts", "e2e/review-nojs.spec.ts"],
                "status": "required-by-make-e2e",
            }
        print(json.dumps(result, sort_keys=True))
        return 0
    except E2EFailure as exc:
        result["failure"] = {"class": exc.failure_class, "message": str(exc)}
        result["status"] = "failed"
        print(json.dumps(result, sort_keys=True))
        return 1


def _stage_and_isolate(temporary: Path, library: Path) -> dict[str, Any]:
    destination = temporary / "marketplace"
    run(
        [
            sys.executable,
            str(ROOT / "scripts/install_plugin.py"),
            "--destination",
            str(destination),
            "--library-root",
            str(library),
            "--marketplace-name",
            "issue49-e2e",
            "--project",
            "demo",
            "--context-requirement",
            "optional",
            "--stage-only",
        ]
    )
    manifest = json.loads((destination / ".agents/plugins/marketplace.json").read_text(encoding="utf-8"))
    plugin_root = destination / "plugins/context-library"
    plugin_manifest = json.loads((plugin_root / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))
    if manifest.get("name") != "issue49-e2e" or plugin_manifest.get("version") != VERSION:
        raise E2EFailure("staged marketplace or Plugin version is inconsistent", "version_mismatch")
    files = [path for path in plugin_root.rglob("*") if path.is_file() and path.name != "runtime-config.json"]
    secret = b"e2e-secret"
    root_bytes = str(library).encode()
    for path in files:
        content = path.read_bytes()
        if secret in content or root_bytes in content:
            raise E2EFailure(f"staged Plugin contains fixture-private content: {path}", "artifact_isolation")
    return {
        "product_version": VERSION,
        "marketplace": manifest.get("name"),
        "files": len(files),
        "runtime_config": str(plugin_root / "runtime-config.json"),
    }


if __name__ == "__main__":
    raise SystemExit(main())
