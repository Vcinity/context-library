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
                "name": "tiktoken",
                "version": "0.9.0",
                "vocabulary_revision": "cl100k_base",
                "accounting_method": "tiktoken cl100k_base",
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


def test_mcp_search_exact_match_and_response_structure(tmp_path, monkeypatch):
    server = load_server()
    root = library_fixture(tmp_path)
    monkeypatch.setenv("CONTEXT_LIBRARY_ROOT", str(root))
    result = server.search_decisions({"project": "demo", "query": "read-only"})

    assert result["schema"] == "context-library/search-decisions-response"
    assert result["schema_version"] == 1
    assert result["project"] == "demo"
    assert result["query"] == "read-only"
    assert result["path"].endswith("projects/demo/decision-register.md")
    assert result["diagnostic"] == "exact"
    assert result["truncated"] is False
    assert result["total_matches"] == 1

    assert len(result["matches"]) == 1
    match = result["matches"][0]
    assert match["decision_id"] == "read-only"
    assert match["subject"] == "Read-only Plugin"
    assert match["excerpt"] == "Keep Plugin access read-only."
    assert match["provenance"] == "explicit"
    assert match["match_mode"] == "exact"
    assert set(match["matched_terms"]) == {"read", "only"}
    assert match["superseded"] == []
    assert match["superseded_by"] == []
    assert match["applicability"] == "unconditional"


def test_mcp_search_lexical_fallback_with_matched_terms(tmp_path, monkeypatch):
    server = load_server()
    root = library_fixture(tmp_path)
    monkeypatch.setenv("CONTEXT_LIBRARY_ROOT", str(root))
    result = server.search_decisions({"project": "demo", "query": "plugin boundary"})

    assert result["diagnostic"] == "lexical"
    assert result["truncated"] is False
    assert result["total_matches"] >= 1

    for match in result["matches"]:
        assert match["match_mode"] == "lexical"
        assert len(match["matched_terms"]) > 0


def test_mcp_search_no_match_returns_empty_with_diagnostic(tmp_path, monkeypatch):
    server = load_server()
    root = library_fixture(tmp_path)
    monkeypatch.setenv("CONTEXT_LIBRARY_ROOT", str(root))
    result = server.search_decisions({"project": "demo", "query": "xyzabc123notfound"})

    assert result["diagnostic"] == "no-match"
    assert result["matches"] == []
    assert result["truncated"] is False
    assert result["total_matches"] == 0


def test_mcp_search_respects_max_results_with_truncation(tmp_path, monkeypatch):
    server = load_server()
    root = library_fixture(tmp_path)

    register_text = (
        "# Decision Register\n\n"
        '<a id="first"></a>\n'
        "### First decision\n\n"
        "- Decision: Keep Plugin access read-only.\n"
        "- Provenance: explicit\n"
        '\n<a id="second"></a>\n'
        "### Second decision\n\n"
        "- Decision: Keep all services read-only.\n"
        "- Provenance: explicit\n"
        '\n<a id="third"></a>\n'
        "### Third decision\n\n"
        "- Decision: Keep data read-only.\n"
        "- Provenance: explicit\n"
    )
    pack = root / "projects/demo"
    (pack / "decision-register.md").write_text(register_text)

    monkeypatch.setenv("CONTEXT_LIBRARY_ROOT", str(root))
    result = server.search_decisions({"project": "demo", "query": "read-only", "max_results": 2})

    assert len(result["matches"]) == 2
    assert result["truncated"] is True
    assert result["total_matches"] == 3


def test_mcp_search_reveals_supersession_and_applicability(tmp_path, monkeypatch):
    server = load_server()
    root = library_fixture(tmp_path)

    register_text = (
        "# Decision Register\n\n"
        '<a id="old"></a>\n'
        "### Old decision\n\n"
        "- Decision: Keep Plugin access read-only.\n"
        "- Provenance: explicit\n"
        '\n<a id="new"></a>\n'
        "### New decision\n\n"
        "- Decision: Plugin read-only with stricter enforcement.\n"
        "- Provenance: explicit\n"
        "- Supersedes: old\n"
        '\n<a id="conditional"></a>\n'
        "### Conditional decision\n\n"
        "- Decision: Plugin read-only when enforcing.\n"
        "- Provenance: explicit\n"
        "- Applies-When: enforcement mode is active\n"
    )
    pack = root / "projects/demo"
    (pack / "decision-register.md").write_text(register_text)

    monkeypatch.setenv("CONTEXT_LIBRARY_ROOT", str(root))
    result = server.search_decisions({"project": "demo", "query": "read-only"})

    assert result["diagnostic"] == "exact"
    assert result["total_matches"] >= 2

    # Find the old decision match
    old_match = next((m for m in result["matches"] if m["decision_id"] == "old"), None)
    assert old_match is not None
    assert "new" in old_match["superseded_by"]

    # Find the new decision match
    new_match = next((m for m in result["matches"] if m["decision_id"] == "new"), None)
    assert new_match is not None
    assert "old" in new_match["superseded"]

    # Find the conditional match
    cond_match = next((m for m in result["matches"] if m["decision_id"] == "conditional"), None)
    assert cond_match is not None
    assert cond_match["applicability"] == "undetermined"


def test_mcp_requires_explicit_root_and_project(tmp_path, monkeypatch):
    server = load_server()
    monkeypatch.delenv("CONTEXT_LIBRARY_ROOT", raising=False)
    # get_library_status is a status boundary, not an exception boundary: a
    # missing/malformed/unreadable runtime is reported as structured,
    # redacted, actionable diagnostics rather than raised.
    status = server.get_library_status({})
    assert status["condition"] == "missing_config"
    assert status["allowed"] is False
    assert "remediation" in status
    assert "root" not in status
    with pytest.raises(server.McpError, match="not configured"):
        server.list_project_packs({})
    root = library_fixture(tmp_path)
    monkeypatch.setenv("CONTEXT_LIBRARY_ROOT", str(root))
    healthy_status = server.get_library_status({})
    assert healthy_status["condition"] == "healthy"
    assert healthy_status["allowed"] is True
    assert healthy_status["exists"] is True
    assert healthy_status["readable"] is True
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
                "name": "tiktoken",
                "version": "0.9.0",
                "vocabulary_revision": "cl100k_base",
                "accounting_method": "tiktoken cl100k_base",
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
            "name": "tiktoken",
            "version": "0.9.0",
            "vocabulary_revision": "cl100k_base",
            "accounting_method": "tiktoken cl100k_base",
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


def test_mcp_task_context_rejects_unsupported_tokenizer(tmp_path, monkeypatch):
    server = load_server()
    root = library_fixture(tmp_path)
    monkeypatch.setenv("CONTEXT_LIBRARY_ROOT", str(root))
    with pytest.raises(server.McpError, match="unsupported tokenizer identity"):
        server.get_task_context(
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


def test_mcp_task_context_rejects_unavailable_encoder(tmp_path, monkeypatch):
    server = load_server()
    root = library_fixture(tmp_path)
    monkeypatch.setenv("CONTEXT_LIBRARY_ROOT", str(root))
    import tiktoken

    monkeypatch.setattr(tiktoken, "get_encoding", lambda _name: (_ for _ in ()).throw(OSError("private cache path")))
    with pytest.raises(server.McpError, match="tokenizer encoder is unavailable") as error:
        server.get_task_context(
            {
                "project": "demo",
                "task_summary": "Update the Plugin boundary",
                "operation": "modify source",
                "repository_scopes": ["plugins/context-library"],
                "agent_token_budget": 1000,
                "tokenizer": {
                    "name": "tiktoken",
                    "version": "0.9.0",
                    "vocabulary_revision": "cl100k_base",
                    "accounting_method": "offline",
                },
            }
        )
    assert "private cache path" not in str(error.value)


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
