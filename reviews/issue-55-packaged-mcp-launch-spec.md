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
and omit the relative `cwd`. The design assumes that the host expands
`${PLUGIN_ROOT}` in MCP command arguments, and that this is the correct
variable name for `.mcp.json`, not merely for the existing hook configuration.
The repository currently has no host-contract fixture proving that assumption;
the implementation must preserve the assumption as an explicit deployment
verification item and must not describe the offline test as proof of host
expansion. `${PLUGIN_ROOT}` relocates with the installed Plugin and does not
expose deployment paths. The packaging validator will require this exact
launch form.

The test will build a deterministic archive and exercise two distinct
relocation shapes: (1) a release-specific staged destination produced through
`scripts/install_plugin.py --stage-only`, and (2) a differently nested,
consumer-cache-style destination. Each substitutes its own staged destination
for `${PLUGIN_ROOT}` as the host launcher would and invokes the server from a
separate working directory. Each scenario will assert successful
initialization, `serverInfo`, and `tools/list`, then terminate and repeat the
launch. A missing staged server path will be reported as a host launch failure
with the resolved path and recovery guidance in the test/deployment evidence,
not classified as a canonical-library configuration error.

## Affected files/components

- `plugins/context-library/.mcp.json`
- `scripts/plugin/validate_plugin_repo.py`
- `tests/plugin/test_plugin_packaging.py` or a focused packaged-launch test
- `docs/PLUGIN_DEPLOYMENT.md`

## Test strategy and commands

- Assert the manifest contains the `${PLUGIN_ROOT}` launch form and no
  relative `cwd`.
- Extract the built archive into a temporary destination and launch from an
  unrelated cwd, twice, using MCP initialization and `tools/list`.
- Document the `${PLUGIN_ROOT}` launch contract and the required installed-host
  smoke verification in `docs/PLUGIN_DEPLOYMENT.md`.
- Obtain host-contract evidence from upstream documentation or an authorized
  live smoke test; the archive test alone is not sufficient evidence that the
  host expands the variable.
- Assert that a deliberately missing staged server path is reported with the
  resolved launch path and remediation pointing to the staged destination or
  Plugin reinstall; this is host-launch evidence, not a runtime-config
  condition.
- Run focused Plugin tests, `make plugin-check`, and `git diff --check`.
- Run the applicable root checks after integration.

## Risks and mitigations

- If the host does not expand `${PLUGIN_ROOT}` in MCP commands, or uses a
  different variable name, the change would regress installation. Existing
  Plugin hook configuration is only a repository precedent, not proof of MCP
  argument expansion. The validator, packaged launch test, deployment note,
  and installed-host smoke verification keep the contract explicit.
- Host-level process-spawn failures occur before Plugin Python code runs and
  cannot be classified by `runtime_config.py`. Acceptance evidence must
  distinguish host command-resolution failures (resolved path plus recovery
  guidance) from launched-process startup failures; neither is a
  canonical-root condition.
- A test that launches source files instead of the archive would miss the
  relocation defect; the test must extract and execute the archive.
- The test must use synthetic temporary data only and never point runtime
  configuration at the canonical library.

## Unresolved questions

- Does the installed host expand `${PLUGIN_ROOT}` in `.mcp.json` command
  arguments, and is `${PLUGIN_ROOT}` the supported variable name for this
  manifest? Resolve with upstream host documentation or an authorized live
  smoke test before claiming the issue fully closed.
