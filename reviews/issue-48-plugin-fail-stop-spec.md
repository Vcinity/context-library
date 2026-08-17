# Issue #48 specification review: installed-but-inaccessible Plugin fail-stop

Issue: https://github.com/Vcinity/context-library/issues/48
Status: Draft specification checkpoint; implementation is not approved by this artifact alone.

## Scope

Make the agent-facing Plugin activation/session-start path fail closed when an
installed Plugin is inaccessible due to runtime configuration or canonical-root
failure. The hook must emit a concrete stop-and-notify disposition, prevent
further task work in that turn, and instruct the user to fix configuration,
disable, or uninstall the Plugin. Preserve continuation for absent or
intentionally disabled context policy. Consume #50's machine-readable runtime
conditions without changing their preflight contract.

## Non-goals

- Rewriting #50 runtime preflight classification or MCP status semantics.
- Treating absent/disabled/optional context as inaccessible-installed failure.
- Mutating canonical data, changing MCP read authority, or adding external
  connectors.
- Claiming a stop behavior for hosts that do not install or execute the Plugin
  hook; provide documented diagnostics at the available boundary.

## Applicable requirements

- `SPEC.md` Sections 4.3, 6, 12.5, and 15 require read-only Plugin behavior,
  truthful context availability, safe diagnostics, and explicit policy states.
- Issue #48 requires immediate notification, no further work, recovery options,
  and deterministic distinction among inaccessible-installed, disabled, and
  absent states.
- Issue #50's `runtime_condition` values are the runtime-layer authority:
  `missing_config`, `malformed_config`, `unreadable_config`, `missing_root`,
  and `unreadable_root` indicate installed-runtime failure.

## Proposed contract and design

Extend session-start diagnostics with an explicit installation/runtime state
and a fail-stop disposition. For an installed Plugin with any #50 runtime
failure, return a structured notice containing the runtime condition, a safe
human-readable error, and the three recovery choices: fix configuration,
disable the Plugin, or uninstall it. Mark the disposition as blocking so the
agent-facing hook exits before projection or ordinary task work.

Keep these states distinct:

- absent Plugin: no Plugin boundary is available; existing policy applies;
- disabled context requirement: intentional policy; continuation is allowed;
- installed and healthy: normal context policy applies;
- installed but inaccessible: blocking fail-stop, regardless of optional/
  required context requirement, until fixed, disabled, or uninstalled.

The hook must not expose raw config contents, credentials, canonical text, or
unredacted filesystem errors. Its process exit/status contract must be
deterministic and testable through the hook subprocess and session diagnostics
file/output. #50's runtime condition remains the machine-readable cause; #48
owns only the agent-facing disposition and recovery wording.

## Affected files and components

- `plugins/context-library/hooks/session_start.py`: installation-state
  detection, blocking notice, and safe recovery guidance.
- `plugins/context-library/projection.py`: preserve runtime condition metadata
  and avoid projection writes before fail-stop.
- `plugins/context-library/skills/context-library/SKILL.md` and Plugin README:
  document the stop-and-notify contract and recovery choices.
- `docs/PLUGIN_DEPLOYMENT.md` and `docs/RECOVERY.md`: operator recovery flow.
- `tests/plugin/test_activation_hook.py`, projection policy tests, and a fresh
  subprocess hook test: inaccessible-installed, disabled, absent, healthy,
  malformed, unreadable, and missing-root states.

## Test strategy

1. Exercise the hook as a subprocess with synthetic staged Plugin/runtime
   fixtures and assert machine-readable blocking output and non-success status
   for each #50 inaccessible condition.
2. Assert the notice contains the concrete condition and all three recovery
   options while excluding private canonical content and raw exception detail.
3. Assert absent Plugin, disabled requirement, optional healthy/available, and
   healthy required contexts preserve their documented continuation behavior.
4. Prove no projection or canonical writes occur when the fail-stop fires, and
   prove recovery after configuration remediation or explicit disable/uninstall
   state.

## Validation commands

```sh
poetry run pytest tests/plugin/test_activation_hook.py tests/plugin/test_projection_policy.py tests/plugin/test_runtime_diagnostics.py
make plugin-check
make test
git diff --check
```

## Risks and mitigations

- A hook-level stop must not be confused with an MCP error that the host may
  ignore; test the process/status boundary and visible notice independently.
- Optional context currently fails open in some paths; the installed-runtime
  failure must override that policy only when the Plugin is actually installed
  and inaccessible.
- Hook execution varies by host trust/installation state; document the
  boundary and keep MCP/status diagnostics available as a fallback.
- Stop notices must remain redacted and generic enough for public artifacts;
  include condition and remediation, not private paths or canonical text.

## Unresolved questions

- What exact hook exit code or structured field should represent blocking while
  preserving the host's session-start protocol compatibility?
- How can the test reliably represent an absent Plugin versus an installed
  Plugin in the current hook environment without depending on host internals?
- Whether #50's `get_library_status` status response should be echoed directly
  or reduced to condition plus safe remediation in the hook notice.

## Independent review record

Pending fresh read-only Claude Code review in plan mode. Required reviewer:
`sonnet`, medium effort, because this is security-sensitive agent authority,
hook/process behavior, and coordination with the merged #50 contract.
