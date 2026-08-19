# Issue #70 specification checkpoint: relocatable Codex Plugin MCP launch

Issue: https://github.com/Vcinity/context-library/issues/70
Status: specification-only checkpoint; implementation is not approved by this artifact alone.

## Scope

Replace the Plugin MCP manifest launch contract that currently passes the
literal `${PLUGIN_ROOT}` token to Codex CLI. The supported contract must be
relocatable across packaged installation directories and must work from an
unrelated caller working directory through the actual Codex Plugin/MCP host
path.

The checkpoint covers the source manifest, package validation, the packaged
artifact regression, release/deployment validation wiring, and public
deployment documentation. It includes concrete success and failure evidence
for the host boundary.

## Non-goals

- Editing or depending on any installed plugin cache as a permanent fix.
- Relying on the consumer's current working directory.
- Test-only expansion or substitution of manifest arguments.
- Changing canonical Context Library data, runtime authority, or MCP tool
  semantics.
- Deploying or publishing a plugin release as part of issue #70.
- Claiming support for host behavior not exercised by the checked-in test.

## Applicable requirements and authority

- `AGENTS.md` requires the Plugin to remain independently deployable,
  canonical-read-only, public-safe, and validated at stable boundaries.
- `SPEC.md` Sections 6, 7, 12.1, 12.2, 12.6, 12.7, and 17 require an
  independently packaged read-only MCP, deterministic packaging, explicit
  host trust behavior, and deterministic root validation.
- Issue #70 requires a Codex-supported relocatable launch mechanism, unchanged
  packaged command/arguments in the regression, two relocation paths,
  unrelated cwd, successful `initialize` and `tools/list`, and explicit
  immediate-exit, missing-path, broken-pipe, and handshake failure coverage.
- Commit `409c4dc` and tag `v0.4.5` are regression baselines. The test must
  fail against both because they retain the literal token contract and the
  pre-expanding test.

## Proposed contract and design

Use a launch contract Codex actually honors for plugin MCP entries: the
manifest command and arguments must identify a relocatable executable/script
without host-variable interpolation. The selected mechanism will be verified
against the installed packaged artifact by invoking Codex CLI from an
unrelated directory, with the exact command and argument arrays read from the
installed manifest and passed unchanged.

The regression harness will:

1. build the deterministic package;
2. extract it to two distinct nested installation roots;
3. stage the plugin through the repository's installer/marketplace flow as
   needed for the real host path;
4. launch Codex with the installed plugin enabled from an unrelated consumer
   directory;
5. assert successful MCP `initialize` and `tools/list`, including the
   expected read-only inventory; and
6. run deterministic negative fixtures for immediate process exit, missing
   server path, broken pipe, and handshake failure, asserting concrete
   diagnostics and non-success outcomes.

The harness must not call `replace`, interpolate, or otherwise rewrite any
manifest command or argument. If the host is unavailable, the test fails
closed rather than silently downgrading to a direct server subprocess; the
repository may retain a separate direct protocol smoke test for diagnosis.
The release/plugin validation target invokes this host regression before a
package can be treated as releasable.

## Affected components

- `plugins/context-library/.mcp.json`: supported source launch contract.
- `scripts/plugin/validate_plugin_repo.py`: manifest and host-contract checks.
- `tests/plugin/test_plugin_packaging.py` or a focused companion:
  installed-package, Codex-host, relocation, and failure-case regression.
- `scripts/plugin/` or `scripts/` host-test helper, only if a reusable
  subprocess/diagnostic boundary is needed.
- `Makefile`: include the host regression in plugin/release validation.
- `docs/PLUGIN_DEPLOYMENT.md` and related public deployment guidance:
  describe only the tested Codex behavior and trust/reload requirements.
- `reviews/issue-70-codex-mcp-launch-spec.md`: preserved checkpoint; it must
  remain unchanged when implementation commits are added.

No Context Manager suite or canonical repository is in scope.

## End-to-end slice

Packaged source → deterministic archive → two relocated installed plugin
roots → Codex launched from unrelated cwd with unchanged manifest command and
args → MCP `initialize` → `tools/list`; the same host boundary also exercises
server exit, missing executable/path, broken pipe, and invalid-handshake
failures. The test consumes only synthetic local library/config fixtures and
asserts no canonical writes.

## Test strategy

- Focused manifest/packaging tests assert the exact command/args loaded from
  each installed artifact and forbid token substitution.
- Codex-host regression uses the locally available `codex` executable and a
  controlled plugin staging/configuration boundary; it must fail clearly when
  Codex is absent or the host contract changes.
- Protocol assertions require successful `initialize` and `tools/list`, and
  verify the expected read-only tool names.
- Negative tests use realistic local fake launchers/processes and assert exit,
  stderr/diagnostic classification, broken pipe, and handshake failures.
- Existing direct MCP read-only, packaging, plugin-check, smoke, package, and
  full e2e checks remain required. Tests remain deterministic/offline apart
  from the local Codex executable and do not mutate installed caches.

## Validation commands

```sh
poetry run pytest tests/plugin/test_plugin_packaging.py tests/plugin/test_mcp_read_only.py tests/plugin/test_runtime_diagnostics.py
make plugin-check
make contracts-check
make smoke
make e2e
make package
make test
make check
git diff --check
mdl AGENTS.md ARCHITECTURE.md CHANGELOG.md IMPLEMENTATION_PROMPT.md MIGRATION.md README.md SPEC.md docs/DEPLOYMENT.md docs/PLUGIN_DEPLOYMENT.md docs/RECOVERY.md docs/TOOL_USE_CASES.md reviews/issue-70-codex-mcp-launch-spec.md
```

## Risks and mitigations

- Codex may reject a seemingly portable launch form; derive the contract from
  the host behavior and preserve the raw host output in validation evidence.
- A direct subprocess test can pass while Codex fails; the installed artifact
  through the actual host path is mandatory completion evidence.
- Failure diagnostics can be flaky if processes are not drained; use bounded
  timeouts, captured stderr, and deterministic fake process states.
- Staging must not alter the source tree or installed cache; use temporary
  extraction roots and explicit cleanup.
- Public docs must not contain private deployment paths or credentials.

## Unresolved questions

- Which exact Codex-supported relocatable command form is available in the
  installed host version, to be established during implementation discovery?
- Which host invocation/configuration flags expose plugin MCP initialization
  and tool listing in a stable machine-readable way?
- Whether the release check should be a required `plugin-check` subcommand or
  a separate package prerequisite, subject to the existing Makefile contract.

## Independent review record

Required fresh read-only Claude Code plan review: model `sonnet`, effort
`medium`, because this is a cross-component packaged Plugin/Codex-host
contract with end-to-end and authority-boundary implications. Review outcome,
attempts, evidence, and any resolved findings will be recorded here before
implementation.
