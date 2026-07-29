# Context Library Plugin

This plugin packages the shared context-library skill for Codex.

## What It Includes

- `.codex-plugin/plugin.json` for plugin metadata
- `.mcp.json` for the bundled read-only MCP server
- `hooks/hooks.json` and `hooks/session_start.py` for repository-local
  activation
- `mcp/context_library_server.py` for shared-library access tools
- `skills/context-library/SKILL.md` for the agent workflow
- `skills/context-library/references/` for reusable guidance on usage,
  provenance, schema, and project packs

## Distribution

The host configures the separately governed companion-library root.

## MCP Tools

The bundled `context_library` MCP server exposes read-only tools:

- `get_library_status`
- `list_project_packs`
- `read_project_artifact`
- `search_decisions`

Codex exposes these as `mcp__context_library__*` tools.

Set `CONTEXT_LIBRARY_ROOT` to a compatible read-only checkout or fixture.
The Plugin has no fallback library location, and read/search MCP calls require
an explicit project argument. Projection refuses activation roots that overlap
the canonical checkout in either direction.

Install from a local checkout with:

```bash
codex plugin marketplace add .
codex plugin add context-library@context-library
```

Start a new Codex thread after install or reinstall so Codex reloads the
updated skill set.

The trusted session-start hook acts only on explicit context policy and project
binding. Required unavailable context produces an advisory notice. Optional
unavailable context fails open without task interference. Disabled and
undetermined context add no guidance. Git is used only to find an activation
root. The hook never mutates companion-library decision content.

## Constraint Projection

`projection.py` provides separate on-demand operations:

```bash
python3 projection.py sync [--root /workspace/root]
python3 projection.py check [--root /workspace/root]
```

Consumer repositories select a pack in committed
`.context-library/config.json` with a named versioned policy:

```json
{
  "schema": "context-library/context-policy",
  "schema_version": 1,
  "project": "pack-name",
  "context_requirement": "optional",
  "affected_layers": {}
}
```

An optional `affected_layers` object maps exact layer names to relative nested scopes.
`CONTEXT_LIBRARY_PROJECT` overrides the configured project, but does not by
itself authorize projection. `sync` requires a committed policy whose
`context_requirement` is explicitly `required` or `optional`; an absent policy
remains `undetermined` and is non-interfering.

`sync` compiles current explicit decisions into compact plugin-managed
`AGENTS.md` blocks and writes `.context-library/projection.json`. It preserves
human content and refuses locally edited generated blocks. `check` never
writes. Exit status `0` means success/current, `1` means projection check
failed, and `2` means selection, source, parse, conflict, or safety error.

The session-start hook never auto-selects the only available pack and never
adds generic guidance without explicit policy. The companion library remains
canonical, and the MCP server remains read-only.

When the repository is available from a Git-hosted URL, derive the exact local
install commands for the current checkout and the share/discovery URLs with:

```bash
python3 scripts/plugin/print_plugin_urls.py \
  --web-base https://git.example.com
```

Use `--web-base` when `origin` uses an SSH alias and the helper cannot infer
the browser host. Git-hosted URLs are for discoverability and sharing.
Installation still depends on a local checkout because the marketplace source
path is relative.

## Validation

Run:

```bash
make test
```
