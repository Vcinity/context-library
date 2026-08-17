# Issue #53 specification review: synchronized Plugin MCP tool inventory

Issue: https://github.com/Vcinity/context-library/issues/53
Status: Draft specification checkpoint; implementation is not approved by this artifact alone.

## Scope

Synchronize the public Plugin README and usage guidance with every registered
MCP tool, including the README inventory entry for `get_task_context` and the
missing usage example for `read_decision_audit`, and add a
deterministic documentation consistency check that fails when a registered tool
is omitted or an undocumented inventory entry is added.

## Non-goals

- Changing MCP handlers, schemas, search semantics, or runtime diagnostics
  already covered by issues #50–#52.
- Adding tools or changing Plugin authority.
- Replacing the full usage reference with generated machine output.

## Applicable requirements

- `SPEC.md` Sections 4.3, 6, 7, and 8 require a read-only Plugin, stable
  versioned contracts, and one source of truth for public interfaces.
- Issue #53 requires complete inventory, valid examples for the two omitted
  tools, and a check comparing documentation with registered `TOOLS` names.
- The registered `TOOLS` mapping in `plugins/context-library/mcp/context_library_server.py`
  is the runtime inventory authority.

## Proposed contract and design

Maintain a marked, machine-readable inventory block in
`plugins/context-library/README.md` with one line per registered tool and a
stable purpose token. Add a checker that imports/parses the registered `TOOLS`
mapping and the README block, compares exact names, rejects duplicates and
unknown names, and verifies that the usage reference contains required example
sections for every tool. Keep prose descriptions human-maintained and avoid
copying private deployment details.

Retain the existing complete `get_task_context` usage examples and add a
concise valid `read_decision_audit` example in the same `usage.md` per-tool
pattern, using its schema, project, and selected decision IDs. Link search
semantics to #52's documented exact/lexical behavior without reimplementing it
here.

## Affected files and components

- `plugins/context-library/README.md`: complete inventory and examples.
- `plugins/context-library/skills/context-library/references/usage.md`: valid
  examples and cross-links.
- `scripts/plugin/validate_plugin_repo.py` or a focused checker: inventory
  consistency boundary.
- `tests/plugin/` and `Makefile` `plugin-check`: deterministic check coverage.

## Test strategy

1. Assert every registered `TOOLS` name appears exactly once in the marked
   README inventory and every inventory name is registered.
2. Assert the two new examples contain required fields and are parseable JSON.
3. Add a negative test using a temporary modified README or synthetic mapping
   proving omissions, duplicates, and undocumented additions fail.
4. Run the checker against the packaged Plugin path, not only source imports.

## Validation commands

```sh
poetry run pytest tests/plugin/test_plugin_packaging.py tests/plugin/test_mcp_read_only.py
make plugin-check
make test
git diff --check
```

## Risks and mitigations

- A prose-only checker can drift from runtime names; use a clearly delimited
  inventory block and compare exact registered names.
- Examples can become stale when schemas change; parse them through the same
  handler validation boundary or generated schema fixtures.
- Keep #53 documentation-only and avoid reworking #52 search behavior.

## Unresolved questions

- Whether the inventory block should be generated entirely or remain
  human-maintained with a checker; the proposed minimal block preserves readable
  documentation while enforcing names.
- README owns the marked name/purpose inventory; `usage.md` owns full per-tool
  examples, following the existing `get_task_context` pattern.

## Independent review record

Pending fresh read-only Claude Code review in plan mode. Required reviewer:
`haiku`, medium effort, because this is a narrow documentation/checker change.
