# Issue #55 specification review: packaged MCP launch path

## Scope

Make the packaged Context Library MCP launch independent of the caller's
working directory. The Plugin manifest will use the host-supported
`${PLUGIN_ROOT}` expansion already used by the Plugin hook configuration, and
the server path will be resolved from that absolute Plugin-root expansion.
Add an offline packaged-artifact test that stages a release-like Plugin,
launches it from an unrelated directory, and repeats the launch after a
process restart.

## Non-goals

- Do not change MCP protocol behavior or runtime configuration semantics.
- Do not embed a machine-specific absolute path in a public artifact.
- Do not modify canonical data or deployment checkouts.
- Do not claim validation of a specific installed Codex binary beyond the
  repository's host-contract fixture and the existing `${PLUGIN_ROOT}` hook
  usage.

## Applicable requirements

- SPEC §12.1: the Plugin is independently installable, deterministic, and
  self-contained.
- SPEC §12.2: the bundled MCP is read-only.
- SPEC §16: deterministic tests run offline and use temporary fixtures.
- Issue #55: launch from outside the Plugin cache, cover staged/restarted
  processes, preserve relocatability, and expose actionable failures.

## Proposed contract and design

`.mcp.json` will invoke `python3 ${PLUGIN_ROOT}/mcp/context_library_server.py`
and omit the relative `cwd`. `${PLUGIN_ROOT}` is the same host-provided
Plugin-root expansion currently used by `hooks/hooks.json`; it relocates with
the installed Plugin and does not expose deployment paths. The packaging
validator will require this exact launch form.

The test will build a deterministic archive, extract it into a temporary
release destination, substitute the staged destination for `${PLUGIN_ROOT}`
as the host launcher would, and invoke the server from a separate working
directory. It will assert successful initialization, `serverInfo`, and
`tools/list`, then terminate and repeat the launch. A missing staged server
path will be reported as a launch failure by the test rather than being
classified as a canonical-library configuration error.

## Affected files/components

- `plugins/context-library/.mcp.json`
- `scripts/plugin/validate_plugin_repo.py`
- `tests/plugin/test_plugin_packaging.py` or a focused packaged-launch test
- `docs/PLUGIN_DEPLOYMENT.md` if the launch contract needs operator guidance

## Test strategy and commands

- Assert the manifest contains the `${PLUGIN_ROOT}` launch form and no
  relative `cwd`.
- Extract the built archive into a temporary destination and launch from an
  unrelated cwd, twice, using MCP initialization and `tools/list`.
- Run focused Plugin tests, `make plugin-check`, and `git diff --check`.
- Run the applicable root checks after integration.

## Risks and mitigations

- If the host does not expand `${PLUGIN_ROOT}` in MCP commands, the change
  would regress installation. Existing Plugin hook configuration provides the
  repository's host-contract precedent; the validator and packaged launch
  test keep the contract explicit.
- A test that launches source files instead of the archive would miss the
  relocation defect; the test must extract and execute the archive.
- The test must use synthetic temporary data only and never point runtime
  configuration at the canonical library.

## Unresolved questions

None for the bounded repository contract. Installed-host verification remains
deployment evidence and is not substituted by this offline test.
