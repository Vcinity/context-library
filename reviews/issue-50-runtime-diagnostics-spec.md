# Issue #50 specification review: first-use Plugin runtime diagnostics

Issue: https://github.com/Vcinity/context-library/issues/50
Status: Draft specification checkpoint; implementation is not approved by this artifact alone.

## Scope

Add a deterministic Plugin runtime preflight that validates the deployment-local
runtime configuration and configured canonical root before normal MCP use.
Expose machine-readable status and actionable, redacted diagnostics for missing,
malformed, unreadable, missing-root, and unreadable-root conditions. Keep valid
configured operation unchanged and define the handoff to issue #48's
installed-but-inaccessible fail-stop behavior.

## Non-goals

- Changing canonical data or adding Plugin write capability.
- Replacing the deployment-local configuration model or embedding machine-
  specific paths in public artifacts.
- Implementing #48's agent/session fail-stop policy beyond the status and error
  contract it consumes.
- Adding external service calls or production-only health dependencies.

## Applicable requirements

- `SPEC.md` Sections 4.3, 6, 7, 12.5, and 15 require a read-only Plugin,
  dependency direction, versioned machine-readable contracts, truthful context
  availability states, and safe redacted errors.
- Issue #50 requires validation of config presence/schema/root existence/
  readability, actionable remediation, distinct machine-readable conditions,
  and fresh staged-install recovery coverage.
- Existing `runtime_config.py` is the configuration parser authority; existing
  MCP and projection paths must consume one preflight result rather than
  duplicating classification logic.

## Proposed contract and design

Add a public preflight function that returns a stable status object with a
condition enum such as `healthy`, `missing_config`, `malformed_config`,
`unreadable_config`, `missing_root`, and `unreadable_root`, plus safe fields for
config path, expected environment override, remediation, and whether normal
MCP use is allowed. Do not include raw config contents, credentials, or
canonical file contents.

The preflight condition enum is a runtime-config/root-layer classification
evaluated before content resolution. It is distinct from the existing SPEC
§12.5 content-resolution vocabulary (`available`, `missing`, `unreadable`,
`invalid`, `ambiguous`, `stale-projection`). Session-start diagnostics must
surface both layers without contradictory health claims: the runtime condition
is the cause when preflight blocks content resolution, while the §12.5
classification applies only after a readable runtime is available. The
classification consumed by #48 must preserve this runtime condition rather
than collapsing it into a generic `missing` state.

Missing configuration remains distinguishable from an explicitly disabled
context requirement. A valid configuration whose root is inaccessible is an
installed-runtime failure and must produce a structured MCP error; #48 may
consume the same classification to stop and notify before user work. An absent
or intentionally disabled Plugin remains governed by existing continuation
policy.

The preflight must validate the root with the same resolved-path and read
permission checks used by the MCP status boundary. Environment overrides must
retain their documented precedence and must not hide malformed bundled config
when no override is active.

## Affected files and components

- `plugins/context-library/runtime_config.py`: shared diagnostic/preflight
  classification.
- `plugins/context-library/mcp/context_library_server.py`: structured status
  and error boundary.
- `plugins/context-library/hooks/session_start.py` and projection path: consume
  the status without canonical writes or private-content leakage.
- `plugins/context-library/scripts/configure.py` and deployment/user docs:
  actionable remediation text.
- `docs/PLUGIN_DEPLOYMENT.md`: keep the documented
  `context library root is not configured` example and recovery guidance in
  sync with the final condition/remediation contract.
- `tests/plugin/test_runtime_config.py`, MCP subprocess tests, and staged
  packaging tests: fresh install, first call, remediation, restart, recovery.

## Test strategy

1. Add black-box preflight tests for every required condition and environment
   precedence, asserting exact condition values and redacted actionable output.
2. Exercise the packaged MCP subprocess for status and first library-dependent
   call under missing, malformed, unreadable, missing-root, unreadable-root,
   and healthy configurations.
3. Stage a synthetic release artifact outside the source tree, invoke it from
   an unrelated working directory, remediate configuration, restart, and prove
   successful recovery without mutating canonical fixtures.
4. Add regression coverage distinguishing valid disabled/absent policy from
   installed-but-inaccessible runtime failure for #48 coordination.
5. Replace the old exception-based assertion in
   `tests/plugin/test_mcp_read_only.py::test_mcp_requires_explicit_root_and_project`
   deliberately: decide whether `get_library_status` remains an exception
   boundary while the new preflight returns status, or make the status object
   the single public behavior and update the test accordingly. No hybrid
   behavior is acceptable.

## Validation commands

```sh
poetry run pytest tests/plugin/test_runtime_config.py tests/plugin/test_mcp_read_only.py
make contracts-check
make plugin-check
make test
git diff --check
```

## Risks and mitigations

- Diagnostics can accidentally disclose private paths or config contents;
  expose only normalized condition, config-path label, and remediation.
- A preflight that disagrees with MCP operations creates false health; route
  both through one classifier and test fresh-process behavior.
- Permission checks vary by user and platform; use deterministic synthetic
  fixtures and explicit readable/unreadable fakes where OS permissions are
  unreliable.
- #48 may require an agent-facing stop disposition; keep this issue's contract
  machine-readable and let #48 own policy wording and turn termination.
- No preserved `issue-48-*` specification artifact currently exists in
  `reviews/`. The #50/#48 classification interface is provisional until #48
  receives its own specification gate; if that gate changes the shared
  condition contract, this checkpoint must be refreshed before implementation.

## Unresolved questions

- Whether the status condition enum belongs in a new generated contract or in
  the existing `get_library_status` response while preserving compatibility.
- Whether unreadable config should be detected distinctly from malformed config
  when the process can stat but cannot read the file.
- Which exact command text is safe and portable for remediation across staged
  and consumer-cache destinations; documentation must avoid host-specific paths.

## Independent review record

Pending fresh read-only Claude Code review in plan mode. Required reviewer:
`sonnet`, medium effort, because this is shared Plugin runtime behavior,
security/redaction-sensitive diagnostics, and coordination with #48.
