from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
SERVER_PATH = ROOT / "plugins/context-library/mcp/context_library_server.py"


def load_server():
    spec = importlib.util.spec_from_file_location("context_library_plugin_mcp", SERVER_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def snapshot(root: Path) -> dict[str, bytes]:
    return {str(path.relative_to(root)): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}


def library_fixture(tmp_path: Path) -> Path:
    root = tmp_path / "library"
    pack = root / "projects/demo"
    pack.mkdir(parents=True)
    (pack / "README.md").write_text("# Demo\n")
    (pack / "decision-register.md").write_text(
        "# Decision Register\n\n"
        '<a id="read-only"></a>\n'
        "### Read-only Plugin\n\n"
        "- Decision: Keep Plugin access read-only.\n"
        "- Provenance: explicit\n"
        "- Rationale: Canonical writes belong to the Manager.\n"
        "- Evidence: observation-read-only\n"
        "- Affected Layers: plugins/context-library\n"
        "\n"
        '<a id="conditional"></a>\n'
        "### Conditional decision\n\n"
        "- Decision: Review the deployment tier.\n"
        "- Provenance: explicit\n"
        "- Applies-When: deployment tier is known\n"
    )
    (pack / "index-by-category.md").write_text("# Category\n")
    (pack / "index-by-date.md").write_text("# Date\n")
    return root


def test_every_advertised_mcp_tool_is_non_mutating(tmp_path, monkeypatch):
    server = load_server()
    root = library_fixture(tmp_path)
    monkeypatch.setenv("CONTEXT_LIBRARY_ROOT", str(root))
    before = snapshot(root)
    invocations = {
        "get_library_status": {},
        "list_project_packs": {},
        "read_project_artifact": {"project": "demo", "artifact": "decision-register"},
        "search_decisions": {"project": "demo", "query": "read-only"},
        "get_task_context": {
            "project": "demo",
            "task_summary": "Update the Plugin boundary",
            "operation": "modify source",
            "repository_scopes": ["plugins/context-library"],
            "agent_token_budget": 1000,
            "tokenizer": {
                "name": "other",
                "version": "1",
                "vocabulary_revision": "other",
                "accounting_method": "offline",
            },
        },
        "read_decision_audit": {"project": "demo", "decision_ids": ["read-only"]},
    }
    assert set(server.TOOLS) == set(invocations)
    for name, arguments in invocations.items():
        server.TOOLS[name]["handler"](arguments)
        assert snapshot(root) == before, name


def test_mcp_path_traversal_cannot_escape_library_root(tmp_path, monkeypatch):
    server = load_server()
    root = library_fixture(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "decision-register.md").write_text("secret")
    monkeypatch.setenv("CONTEXT_LIBRARY_ROOT", str(root))
    with pytest.raises(server.McpError, match="escapes"):
        server.read_project_artifact({"project": "../../outside", "artifact": "decision-register"})
    assert (outside / "decision-register.md").read_text() == "secret"


def test_mcp_distribution_ast_contains_no_mutating_file_operation():
    source = SERVER_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    mutating_methods = {
        "chmod",
        "hardlink_to",
        "mkdir",
        "rename",
        "replace",
        "rmdir",
        "symlink_to",
        "touch",
        "unlink",
        "write_bytes",
        "write_text",
    }
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Attribute):
            assert node.func.attr not in mutating_methods
        if isinstance(node.func, ast.Name) and node.func.id == "open":
            mode_node = (
                node.args[1]
                if len(node.args) > 1
                else next(
                    (keyword.value for keyword in node.keywords if keyword.arg == "mode"),
                    None,
                )
            )
            if isinstance(mode_node, ast.Constant) and isinstance(mode_node.value, str):
                assert not set(mode_node.value).intersection("wax+")
    assert "context_library_maintainer" not in source
    assert ".splitlines()" not in ast.unparse(tree)


def test_mcp_search_uses_authoritative_decision_read_model(tmp_path, monkeypatch):
    server = load_server()
    root = library_fixture(tmp_path)
    monkeypatch.setenv("CONTEXT_LIBRARY_ROOT", str(root))
    result = server.search_decisions({"project": "demo", "query": "read-only"})
    assert result["matches"] == [
        {
            "decision_id": "read-only",
            "subject": "Read-only Plugin",
            "excerpt": "Keep Plugin access read-only.",
            "provenance": "explicit",
        }
    ]


def test_mcp_requires_explicit_root_and_project(tmp_path, monkeypatch):
    server = load_server()
    monkeypatch.delenv("CONTEXT_LIBRARY_ROOT", raising=False)
    with pytest.raises(server.McpError, match="not configured"):
        server.get_library_status({})
    root = library_fixture(tmp_path)
    monkeypatch.setenv("CONTEXT_LIBRARY_ROOT", str(root))
    with pytest.raises(server.McpError, match="explicitly selected"):
        server.read_project_artifact({"artifact": "decision-register"})
    with pytest.raises(server.McpError, match="explicitly selected"):
        server.search_decisions({"query": "read-only"})


def test_mcp_sole_legacy_flat_pack_accepts_an_explicit_historical_alias(tmp_path, monkeypatch):
    server = load_server()
    root = tmp_path / "library"
    pack = root / "decision-artifacts"
    pack.mkdir(parents=True)
    (pack / "decision-register.md").write_text(
        "# Decision Register\n\n"
        '<a id="legacy-read"></a>\n'
        "### Legacy read\n\n"
        "- Decision: Preserve compatible reads.\n"
        "- Provenance: explicit\n"
    )
    monkeypatch.setenv("CONTEXT_LIBRARY_ROOT", str(root))
    result = server.read_project_artifact({"project": "previous-project", "artifact": "decision-register"})
    assert result["project"] == "previous-project"
    assert "Preserve compatible reads." in result["text"]


def test_task_context_and_audit_are_black_box_contract_boundaries(tmp_path, monkeypatch):
    server = load_server()
    root = library_fixture(tmp_path)
    monkeypatch.setenv("CONTEXT_LIBRARY_ROOT", str(root))
    task = server.get_task_context(
        {
            "project": "demo",
            "task_summary": "Update the Plugin boundary",
            "operation": "modify source",
            "repository_scopes": ["plugins/context-library"],
            "agent_token_budget": 1000,
            "tokenizer": {
                "name": "other",
                "version": "1",
                "vocabulary_revision": "other",
                "accounting_method": "offline",
            },
        }
    )
    assert task["schema"] == "context-library/task-context-response"
    assert [item["decision_id"] for item in task["operative_directives"]] == ["read-only"]
    assert "Canonical writes belong to the Manager." not in task["agent_visible_capsule"]["serialized_content"]
    assert task["agent_visible_capsule"]["utf8_byte_count"] == len(
        task["agent_visible_capsule"]["serialized_content"].encode()
    )

    audit = server.read_decision_audit({"project": "demo", "decision_ids": ["read-only"]})
    assert audit["schema"] == "context-library/decision-audit-response"
    record = audit["records"][0]
    assert record["rationale"] == "Canonical writes belong to the Manager."
    assert record["evidence"] == ["observation-read-only"]
    assert record["source_scope"] == "projects/demo"
    assert record["applicability"]["state"] == "undetermined"


@pytest.mark.parametrize(
    "tool,args,match",
    [
        ("get_task_context", {"task_summary": "x"}, "explicitly selected"),
        (
            "get_task_context",
            {
                "project": "demo",
                "task_summary": "x",
                "operation": "x",
                "repository_scopes": ["plugins/context-library"],
                "agent_token_budget": 10,
                "tokenizer": {
                    "name": "other",
                    "version": "1",
                    "vocabulary_revision": "other",
                    "accounting_method": "offline",
                },
                "schema_version": 2,
            },
            "unsupported task-context schema version",
        ),
        ("read_decision_audit", {"project": "demo", "decision_ids": ["missing"]}, "unknown decision ID"),
        ("read_decision_audit", {"project": "../../outside", "decision_ids": ["x"]}, "escapes"),
    ],
)
def test_new_tools_fail_closed(tmp_path, monkeypatch, tool, args, match):
    server = load_server()
    root = library_fixture(tmp_path)
    monkeypatch.setenv("CONTEXT_LIBRARY_ROOT", str(root))
    with pytest.raises(server.McpError, match=match):
        server.TOOLS[tool]["handler"](args)


def test_generated_task_context_matches_core_renderer(tmp_path, monkeypatch):
    server = load_server()
    root = library_fixture(tmp_path)
    monkeypatch.setenv("CONTEXT_LIBRARY_ROOT", str(root))
    register, revision, source_scope = server.read_register("demo")
    payload = {
        "project": "demo",
        "task_summary": "Update the Plugin boundary",
        "operation": "modify source",
        "repository_scopes": ["plugins/context-library"],
        "agent_token_budget": 1000,
        "tokenizer": {
            "name": "other",
            "version": "1",
            "vocabulary_revision": "other",
            "accounting_method": "offline",
        },
    }
    generated = server.resolve_task_context(payload, register, revision=revision, source_scope=source_scope)
    from context_library_core.task_context import TaskContextRequest
    from context_library_core.task_context_resolution import resolve_task_context

    core = resolve_task_context(
        register,
        TaskContextRequest.model_validate(payload),
        revision=revision,
        source_scope=source_scope,
    ).model_dump(mode="json", by_alias=True)
    assert generated == core


def test_mcp_get_task_context_tokenizer_schema_contract(tmp_path, monkeypatch):
    server = load_server()
    root = library_fixture(tmp_path)
    monkeypatch.setenv("CONTEXT_LIBRARY_ROOT", str(root))
    from generated.core_runtime import TASK_CONTEXT_REQUEST_JSON_SCHEMA

    schema = server.TOOLS["get_task_context"]["inputSchema"]
    tokenizer_schema = schema["properties"]["tokenizer"]
    authoritative = TASK_CONTEXT_REQUEST_JSON_SCHEMA["$defs"]["TokenizerIdentity"]

    assert "name" in tokenizer_schema["properties"]
    assert "version" in tokenizer_schema["properties"]
    assert "vocabulary_revision" in tokenizer_schema["properties"]
    assert "accounting_method" in tokenizer_schema["properties"]
    assert "pinned" in tokenizer_schema["properties"]

    assert tokenizer_schema["required"] == ["name", "version", "vocabulary_revision", "accounting_method"]
    assert tokenizer_schema["additionalProperties"] is False
    assert tokenizer_schema["properties"]["pinned"]["const"] is True
    assert tokenizer_schema["required"] == authoritative["required"]
    assert tokenizer_schema["additionalProperties"] == authoritative["additionalProperties"]
    for field in tokenizer_schema["properties"]:
        for constraint in ("type", "const", "default"):
            if constraint in authoritative["properties"][field]:
                assert (
                    tokenizer_schema["properties"][field][constraint]
                    == authoritative["properties"][field][constraint]
                )

    base_request = {
        "project": "demo",
        "task_summary": "Update the Plugin boundary",
        "operation": "modify source",
        "repository_scopes": ["plugins/context-library"],
        "agent_token_budget": 1000,
    }

    valid_tokenizer = {
        "name": "tiktoken",
        "version": "0.9.0",
        "vocabulary_revision": "cl100k_base",
        "accounting_method": "offline",
    }

    result = server.get_task_context({**base_request, "tokenizer": valid_tokenizer})
    assert result["schema"] == "context-library/task-context-response"

    result = server.get_task_context({**base_request, "tokenizer": {**valid_tokenizer, "pinned": True}})
    assert result["schema"] == "context-library/task-context-response"

    with pytest.raises(server.McpError, match="tokenizer identity fields must be non-empty strings"):
        server.get_task_context({**base_request, "tokenizer": {
            "name": "tiktoken",
            "version": "0.9.0",
            "vocabulary_revision": "cl100k_base",
        }})

    with pytest.raises(server.McpError, match="unknown tokenizer field"):
        server.get_task_context({**base_request, "tokenizer": {
            **valid_tokenizer,
            "truncation_mode": "none",
        }})

    with pytest.raises(server.McpError, match="tokenizer must be pinned"):
        server.get_task_context({**base_request, "tokenizer": {
            **valid_tokenizer,
            "pinned": False,
        }})
