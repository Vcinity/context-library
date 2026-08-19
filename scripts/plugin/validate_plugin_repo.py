#!/usr/bin/env python3
from __future__ import annotations

import ast
import json
import re
import sys
import tomllib
from pathlib import Path

from context_library_core.version import VERSION

ROOT = Path(__file__).resolve().parents[2]


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def check_exists(path: Path, label: str) -> None:
    if not path.exists():
        fail(f"missing {label}: {path}")


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        fail(f"unable to read {path}: {exc}")


def validate_plugin_manifest() -> None:
    manifest_path = ROOT / "plugins" / "context-library" / ".codex-plugin" / "plugin.json"
    payload = json.loads(read_text(manifest_path))
    for field in ("name", "version", "description"):
        if not isinstance(payload.get(field), str) or not payload[field].strip():
            fail(f"plugin.json field {field!r} must be a non-empty string")
    if payload["name"] != "context-library":
        fail("plugin name must be 'context-library'")
    if not re.fullmatch(r"\d+\.\d+\.\d+", payload["version"]):
        fail("plugin version must be strict semver")
    if payload["version"] != VERSION:
        fail(f"plugin version must match product version {VERSION}")
    package = json.loads(read_text(ROOT / "package.json"))
    poetry = tomllib.loads(read_text(ROOT / "pyproject.toml"))
    if package.get("version") != VERSION or poetry.get("tool", {}).get("poetry", {}).get("version") != VERSION:
        fail("Python, frontend, and Plugin product versions must match")

    author = payload.get("author")
    if not isinstance(author, dict) or not isinstance(author.get("name"), str) or not author["name"].strip():
        fail("plugin author.name must be present")

    interface = payload.get("interface")
    if not isinstance(interface, dict):
        fail("plugin interface must be an object")
    for field in ("displayName", "shortDescription", "longDescription", "developerName", "category"):
        if not isinstance(interface.get(field), str) or not interface[field].strip():
            fail(f"plugin interface field {field!r} must be a non-empty string")

    default_prompt = interface.get("defaultPrompt")
    if not isinstance(default_prompt, list) or not 1 <= len(default_prompt) <= 3:
        fail("plugin interface.defaultPrompt must contain 1-3 prompts")
    if not all(isinstance(item, str) and item.strip() for item in default_prompt):
        fail("plugin interface.defaultPrompt entries must be non-empty strings")

    capabilities = interface.get("capabilities")
    if not isinstance(capabilities, list) or not all(isinstance(item, str) and item.strip() for item in capabilities):
        fail("plugin interface.capabilities must be an array of strings")

    if payload.get("skills") != "./skills/":
        fail("plugin skills path must be './skills/'")
    if payload.get("mcpServers") != "./.mcp.json":
        fail("plugin mcpServers path must be './.mcp.json'")
    if "hooks" in payload:
        fail("plugin manifest must use convention-based hook discovery")

    skill_md = ROOT / "plugins" / "context-library" / "skills" / "context-library" / "SKILL.md"
    check_exists(skill_md, "plugin skill")
    if not read_text(skill_md).startswith("---\n"):
        fail("skill frontmatter must start at the top of SKILL.md")
    skill = read_text(skill_md)
    if re.search(r"(?:^|[\"'`\s])/(?:home|Users|mnt)/", skill) or "Otherwise check" in skill:
        fail("plugin skill must not direct agents to machine-specific filesystem context")
    policy_position = skill.find("Resolve explicit")
    access_position = skill.find("use the bundled read-only")
    if policy_position < 0 or access_position < 0 or policy_position > access_position:
        fail("plugin skill must resolve policy before Context Library access")

    mcp_path = ROOT / "plugins" / "context-library" / ".mcp.json"
    payload = json.loads(read_text(mcp_path))
    servers = payload.get("mcpServers")
    if not isinstance(servers, dict):
        fail(".mcp.json must define a top-level mcpServers object")
    server = servers.get("context_library")
    if not isinstance(server, dict):
        fail(".mcp.json must define a context_library server under mcpServers")
    if server.get("command") != "python3":
        fail("context_library MCP server command must be python3")
    args = server.get("args")
    if args != ["./mcp/context_library_server.py"]:
        fail("context_library MCP server args must use ./mcp/context_library_server.py")
    if server.get("cwd") != ".":
        fail("context_library MCP server cwd must be '.' (Codex resolves it against the plugin root)")
    check_exists(ROOT / "plugins" / "context-library" / "mcp" / "context_library_server.py", "MCP server")
    generated = read_text(ROOT / "plugins" / "context-library" / "generated" / "core_runtime.py")
    if f"PRODUCT_VERSION = {VERSION!r}" not in generated:
        fail("generated Plugin runtime product version is stale")
    mcp = read_text(ROOT / "plugins" / "context-library" / "mcp" / "context_library_server.py")
    if "SERVER_VERSION = PRODUCT_VERSION" not in mcp:
        fail("MCP server version must use the generated product version")
    check_exists(ROOT / "plugins" / "context-library" / "hooks" / "hooks.json", "plugin hooks")
    check_exists(ROOT / "plugins" / "context-library" / "hooks" / "session_start.py", "session-start hook")
    check_exists(ROOT / "plugins" / "context-library" / "runtime_config.py", "Plugin runtime configuration loader")
    check_exists(ROOT / "plugins" / "context-library" / "scripts" / "configure.py", "Plugin configurator")
    check_exists(ROOT / "scripts" / "install_plugin.py", "Plugin installer")
    check_exists(ROOT / "plugins" / "context-library" / "projection.py", "projection compiler")
    projection = read_text(ROOT / "plugins" / "context-library" / "projection.py")
    if "def ensure_generic(" in projection:
        fail("Plugin projection must not expose generic guidance injection")


def validate_marketplace_manifest() -> None:
    path = ROOT / ".agents" / "plugins" / "marketplace.json"
    payload = json.loads(read_text(path))
    if payload.get("name") != "context-library":
        fail("marketplace name must be 'context-library'")
    if not isinstance(payload.get("interface"), dict):
        fail("marketplace interface must be an object")
    if payload["interface"].get("displayName") != "Vcinity Engineering":
        fail("marketplace displayName must be 'Vcinity Engineering'")
    plugins = payload.get("plugins")
    if not isinstance(plugins, list) or len(plugins) != 1:
        fail("marketplace must contain exactly one plugin entry")
    entry = plugins[0]
    if entry.get("name") != "context-library":
        fail("marketplace plugin entry must target context-library")
    source = entry.get("source")
    if (
        not isinstance(source, dict)
        or source.get("source") != "local"
        or source.get("path") != "./plugins/context-library"
    ):
        fail("marketplace source must point at ./plugins/context-library")
    policy = entry.get("policy")
    if not isinstance(policy, dict):
        fail("marketplace policy must be an object")
    if policy.get("installation") != "AVAILABLE":
        fail("marketplace installation policy must be AVAILABLE")
    if policy.get("authentication") != "ON_INSTALL":
        fail("marketplace authentication policy must be ON_INSTALL")
    if entry.get("category") != "Productivity":
        fail("marketplace category must be Productivity")


def validate_reference_files() -> None:
    base = ROOT / "plugins" / "context-library" / "skills" / "context-library" / "references"
    for name in ("usage.md", "provenance.md", "schema.md", "project-packs.md"):
        check_exists(base / name, f"reference file {name}")


def validate_mcp_tools_inventory() -> None:
    readme_path = ROOT / "plugins" / "context-library" / "README.md"
    readme_text = read_text(readme_path)

    # Extract marked inventory from README
    start_marker = "<!-- CONTEXT_LIBRARY_TOOLS_INVENTORY -->"
    end_marker = "<!-- /CONTEXT_LIBRARY_TOOLS_INVENTORY -->"
    start_idx = readme_text.find(start_marker)
    end_idx = readme_text.find(end_marker)
    if start_idx < 0 or end_idx < 0:
        fail("missing marked CONTEXT_LIBRARY_TOOLS_INVENTORY block in README.md")

    inventory_block = readme_text[start_idx + len(start_marker) : end_idx]
    readme_tools = set()
    for line in inventory_block.strip().split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # Extract tool name from "- `tool-name`: description" format
        match = re.match(r'-\s*`([^`]+)`', line)
        if match:
            tool_name = match.group(1)
            if tool_name in readme_tools:
                fail(f"duplicate tool {tool_name!r} in README inventory")
            readme_tools.add(tool_name)

    # Extract registered tools from context_library_server.py
    mcp_server_path = ROOT / "plugins" / "context-library" / "mcp" / "context_library_server.py"
    mcp_tree = ast.parse(read_text(mcp_server_path), filename=str(mcp_server_path))
    tools_value = None
    for node in ast.walk(mcp_tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(target, ast.Name) and target.id == "TOOLS" for target in targets):
                tools_value = node.value
                break

    if not isinstance(tools_value, ast.Dict):
        fail("could not find TOOLS dictionary in context_library_server.py")

    registered_tools = {
        key.value
        for key in tools_value.keys
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
    }

    # Check for omissions and undocumented additions
    missing_from_readme = registered_tools - readme_tools
    if missing_from_readme:
        missing_list = sorted(missing_from_readme)
        fail(f"registered tools missing from README inventory: {missing_list}")

    undocumented = readme_tools - registered_tools
    if undocumented:
        undocumented_list = sorted(undocumented)
        fail(f"README inventory contains undocumented tools: {undocumented_list}")


def main() -> None:
    check_exists(ROOT / "README.md", "repo README")
    check_exists(ROOT / "SPEC.md", "repo SPEC")
    check_exists(ROOT / "Makefile", "Makefile")
    check_exists(ROOT / ".agents" / "plugins" / "marketplace.json", "marketplace manifest")
    check_exists(ROOT / "plugins" / "context-library" / ".codex-plugin" / "plugin.json", "plugin manifest")
    check_exists(ROOT / "plugins" / "context-library" / ".mcp.json", "MCP manifest")
    check_exists(ROOT / "plugins" / "context-library" / "README.md", "plugin README")

    validate_plugin_manifest()
    validate_marketplace_manifest()
    validate_reference_files()
    validate_mcp_tools_inventory()

    print("context library plugin validation passed")


if __name__ == "__main__":
    main()
