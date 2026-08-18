# Issue #48 specification review: installed-but-inaccessible Plugin fail-stop

Issue: https://github.com/Vcinity/context-library/issues/48
Status: specification checkpoint after the explicit authority decision to stop
when an installed Plugin runtime is inaccessible.

## Scope

Make the trusted Plugin session-start path fail closed when its installed
runtime cannot read valid deployment configuration or the configured library
root. The hook emits a structured blocking result, exits non-zero, and gives
the user safe fix, disable, or uninstall recovery choices before projection
or task work can continue.

## Non-goals

- Changing #50 runtime condition meanings or MCP status behavior.
- Blocking a host where the Plugin is absent and therefore has no hook.
- Blocking a healthy optional or undetermined context policy.
- Treating successfully resolved explicit `disabled` policy as an error.
- Mutating canonical data, changing MCP read authority, or adding connectors.

## Authority decision

The prior SPEC advisory rule conflicted with the issue's requirement that an
installed broken Plugin must not look operational. The user resolved that
conflict by authorizing the SPEC change in commit `6b4fd71`. SPEC sections
12.5–12.7 and 13.2 now distinguish healthy context unavailability from
installed-runtime failure. The latter is a stop condition regardless of
optional/required/undetermined context policy.

## Contract and design

1. Run `runtime_config.preflight()` before policy resolution or projection.
2. For `missing_config`, `malformed_config`, `unreadable_config`,
   `missing_root`, or `unreadable_root`, emit a concise redacted message and
   machine-readable fields: schema version, `status=blocked`,
   `disposition=stop`, and `runtime_condition`.
3. Exit with status `2`; do not write projection or canonical data.
4. Evaluate an explicit disabled override only when runtime preflight is
   healthy. A valid disabled policy then remains silent and successful.
5. After healthy preflight, preserve existing policy behavior: required
   unavailable context is advisory, optional/undetermined is non-interfering,
   and healthy projection may synchronize safely.

## Affected files

- `SPEC.md`, `README.md`, and `CHANGELOG.md` for the authority and user
  contract.
- `plugins/context-library/hooks/session_start.py` for preflight, stop output,
  and exit status.
- Plugin skill/README and deployment/recovery docs for operator guidance.
- Activation and subprocess tests for every runtime condition and policy.

## Test strategy

- Exercise the hook subprocess for all five blocking runtime conditions and
  assert status `2`, structured output, recovery choices, and no writes.
- Assert healthy disabled, optional, undetermined, and required paths retain
  their existing behavior.
- Assert recovery after configuration/root remediation and preserve the
  absent-Plugin host-boundary distinction.
- Run focused tests and the repository Plugin/root gates.

## Risks and mitigations

- A host may ignore command output or non-zero status; keep the result both
  machine-readable and human-readable and document the host boundary.
- First-use installations without configuration now stop intentionally; the
  message provides the configure/disable/uninstall choices.
- Runtime diagnostics must remain redacted; tests reject raw exception detail
  and canonical content.
