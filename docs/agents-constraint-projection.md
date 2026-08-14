# AGENTS.md Constraint Projection Design

## Status

Implemented. The compiler and command interface live in
`plugins/context-library/projection.py`; the session-start integration lives in
`plugins/context-library/hooks/session_start.py`.

## Problem

Before this implementation, the plugin added only generic guidance telling
agents to consult the Context Library. The EXAMPLE-410 experiment showed that
retrieval alone is not enough: an agent can cite a relevant architectural
decision and still produce a design that conflicts with project intent.

Loading the complete register into every task is also undesirable. It consumes
tokens, reduces the salience of the few constraints that matter, and risks
turning inferred or assumed context into apparently authoritative guidance.

## Direction

Treat the plugin as a context compiler:

```text
companion decision register
        |
        v
applicability and supersession resolution
        |
        +--> compact explicit constraints --> AGENTS.md
        |
        +--> provenance and excluded context --> projection sidecar
```

The companion library remains authoritative. The local artifacts are generated
and replaceable.

## Agent-Facing Hot Path

The generated `AGENTS.md` block contains only current explicit universal
decisions during automatic session-start projection. Keep it short and direct:

```markdown
<!-- context-library:begin
project: example
source-digest: sha256:<digest>
projection-digest: sha256:<digest>
-->
## Project Constraints

- `[auth-application-owned-ux]` End-user authentication UX must be application-owned.
- `[auth-application-owned-ux]` Never expose identity-provider-hosted required-action pages.
- `[auth-token-proxy]` Browser code uses application-owned authentication interfaces.
<!-- context-library:end -->
```

Do not include rationale, evidence excerpts, decisionmaker history, inferred
guidance, assumptions, or a provenance legend in the hot path. Stable decision
IDs provide the link to those details.

Aim for roughly 5-15 non-overlapping constraints in the automatic hot path.
Affected-layer, conditional, superseded, conflicted, inferred, and assumed
guidance stays out of that path. Retrieve scoped or conditional context with
the explicit task-context MCP request when a task signal is available, or use
an explicitly requested on-demand operation.

## Provenance Sidecar

Store audit detail outside the active instruction path, recommended at:

```text
.context-library/projection.json
```

The initial sidecar shape should include:

```json
{
  "schema_version": 1,
  "project": "example",
  "source": {
    "pack": "decision-artifacts",
    "revision": "<revision-or-null>",
    "digest": "sha256:<digest>"
  },
  "generated_at": "<RFC3339 timestamp>",
  "config_digest": "sha256:<digest>",
  "projection_digest": "sha256:<digest>",
  "constraints": [
    {
      "text": "End-user authentication UX must be application-owned.",
      "source_ids": ["auth-application-owned-ux"],
      "source_provenance": "explicit",
      "derivation": "condensed",
      "scope": "."
    }
  ],
  "excluded_context": [
    {
      "text": "A pre-session password API may be required.",
      "source_ids": ["auth-application-owned-ux", "auth-token-proxy"],
      "source_provenance": "inferred",
      "derivation": "synthesized",
      "reason": "non-authoritative"
    }
  ],
  "source_decisions": [
    {
      "id": "auth-product-owned-ux",
      "provenance": "explicit",
      "effective_provenance": "explicit",
      "derivation": "condensed",
      "source_ids": ["auth-product-owned-ux"]
    }
  ],
  "artifacts": [
    {
      "scope": ".",
      "path": "AGENTS.md",
      "block_digest": "sha256:<digest>"
    }
  ]
}
```

`source_provenance` and `derivation` are separate dimensions:

- source provenance: `explicit`, `inferred`, `assumed`
- derivation: `direct`, `condensed`, `synthesized`

Every synthesized item cites all sources and inherits the weakest source
provenance. Unlabeled generated statements are invalid.

## Synchronization and Checking

Provide two user-visible operations, regardless of their final CLI or plugin
command spelling.

### Sync

- locate the activation root and project pack
- parse current decisions and supersession chains
- select applicable explicit decisions
- render deterministic constraint blocks
- write the sidecar
- update only plugin-managed `AGENTS.md` blocks
- preserve human content byte-for-byte outside managed blocks
- refuse replacement when a managed block has unexplained local edits

### Check

- perform no writes
- verify source and projection digests
- verify the block matches the sidecar
- detect missing, malformed, stale, or edited projections
- verify that no inferred or assumed item entered `AGENTS.md`
- return a stable nonzero result and concise remediation on failure

Freshness is content-driven. The source revision is the selected register's Git
blob identifier when available, not the library repository's commit. Refresh
whenever that revision, source digest, supersession state, applicability
metadata, or project configuration changes. A maximum age such as one day or
one week is a fallback check, not the primary invalidation mechanism.

## Implemented Interface

From this repository, run:

```bash
python3 plugins/context-library/projection.py sync [--root /workspace/root]
python3 plugins/context-library/projection.py check [--root /workspace/root]
```

Without `--root`, both operations use `CONTEXT_LIBRARY_PROJECT_ROOT`, then the
current Git root, then the current directory. `sync` serializes concurrent
synchronizations, performs safety preflight, and atomically replaces individual
files. It preserves bytes outside managed blocks, refuses symlinked targets,
ignores marker examples inside fenced Markdown, and refuses a generated block
whose digest no longer matches the prior sidecar. A failed multi-file update is
rolled back; any rollback failure names the restored and unrestored paths. A
deleted generated file is recreated, and a generated-only file is removed when
its scope is no longer projected. `check` performs no writes.

Exit statuses are stable:

- `0`: synchronization succeeded or the projection is current
- `1`: `check` found a missing, malformed, stale, or edited projection
- `2`: source selection, configuration, parsing, conflict, or safe-update error

Configure a consumer repository with committed
`.context-library/config.json`:

```json
{
  "project": "example",
  "affected_layers": {
    "ui": "components/ui",
    "api": "services/api"
  }
}
```

`CONTEXT_LIBRARY_PROJECT` overrides `project`. Each `affected_layers` entry is
an exact, normalized source-layer-to-relative-directory mapping. Alternate
spellings such as `./components/ui`, `components//ui`, absolute paths, parent
traversal, duplicate target paths, and symlinked targets are rejected. A
decision with no affected layer projects at the activation root. A decision
with any unmapped layer is retained in the sidecar but is not projected.

The compiler discovers `projects/<project>/decision-register.md` and retains
compatibility with the existing `decision-artifacts/decision-register.md`
layout for the neutral `legacy` pack. Source selection without configuration is
allowed only when exactly one pack is available. When that flat pack is the
only available pack, an explicit historical project binding resolves to it so
existing consumers remain readable without hard-coding a private project name.

The conservative Markdown parser requires a stable anchor immediately before
each level-three decision heading and a valid `Provenance` field. Decision-like
headings or fields outside anchored regions are rejected rather than silently
omitted or attributed to a neighboring record. Existing
`Decision` text is rendered directly when no explicit `Constraint` or
`Constraints` field is present. Exact identifiers in `Supersedes`,
`Conflicts-With`, and synthesized `Sources` fields are resolved; narrative
supersession prose is retained as source content but is not guessed into an ID.
Conditional `Applies-When` decisions and decisions with unmapped affected
layers remain auditable exclusions until their applicability is deterministic.

The version 1 sidecar is `.context-library/projection.json`. In addition to the
projection and artifact digests, it contains `source_decisions` with declared
and effective provenance. Synthesized provenance resolves transitively and
inherits the weakest source.

## Trigger Policy

| Trigger | Default behavior |
| --- | --- |
| On demand | Sync using the requested projection mode |
| Plugin installation or first session | Universal-only sync when project is unambiguous |
| Later session start | Check, then universal-only sync safely when stale |
| Pre-commit | Check only |
| CI | Check only and fail stale projections |
| Post-checkout or post-merge | Optional sync integration |
| Daily or weekly schedule | Optional fallback refresh |

Pre-commit, CI, post-checkout, post-merge, and scheduled integrations remain
optional and are not installed automatically. Use the documented `check`
command in pre-commit or CI. Maximum-age fallback is currently disabled;
source and configuration digests are authoritative.

Pre-commit and CI must never modify the worktree. Git integrations are optional;
non-Git workspaces remain supported.

## Safety and Gating

Automatic sync stops and reports instead of overwriting when:

- the project pack cannot be selected confidently
- active explicit decisions conflict
- a generated block was edited outside the plugin
- source content is unavailable and freshness cannot be established
- parsing or provenance is incomplete
- condensation would strengthen the authority of the source

Only explicit current universal decisions can enter automatic `AGENTS.md`
guidance. Scoped, conditional, superseded, conflicted, inferred, and assumed
material stays in the sidecar or is returned by explicit task-context/audit
requests. Ratification creates a new explicit decision in the companion
library and preserves the earlier record.

## Source Schema Requirements

Reliable compilation needs stable decision IDs and benefits from these fields:

- `provenance`
- `affected_layers`
- `tags`
- `strength`
- `applies_when`
- `prohibited_approaches`
- `required_evidence`
- `exception_authority`
- `supersedes`

The compiler must remain conservative when optional fields are absent. It must
not infer a mandatory constraint merely because a decision sounds important.

## Repository and Distribution Boundary

This plugin repository owns compiler behavior, projection formats, hooks, and
validation. It does not own canonical project decisions. Source decisions and
human ratification remain in the companion library repository.

Generated universal projection artifacts should normally be committed in
consumer repositories so constraints work offline and changes are reviewable.
Richer scoped or task-specific context remains explicit, local, and on demand;
session start has no task signal and must not inject it.

## Validation Scenarios

At minimum, tests must prove:

- deterministic output for identical inputs
- byte-for-byte preservation of human `AGENTS.md` content
- idempotent sync
- non-mutating check
- stale and locally edited block detection
- explicit-only hot-path projection
- inferred and assumed exclusion with preserved sidecar labels
- weakest-provenance inheritance for synthesis
- supersession removes obsolete projected constraints
- ambiguous and conflicting context fails safely
- root and nested scope projection
- CRLF and lone-CR preservation outside generated blocks
- normalized, non-escaping, non-symlinked target enforcement
- rollback at every write stage with explicit partial-state diagnostics
- malformed anchorless decision rejection
- unchanged-register freshness across unrelated library commits
- explicit-root, Git-root, workspace-root, and non-Git activation behavior

## Example Failure Prevented

For EXAMPLE-410, an explicit constraint such as:

```text
[auth-application-owned-ux] Never expose identity-provider-hosted authentication or
required-action pages.
```

would have appeared directly in the applicable `web-ui/AGENTS.md`. The agent
could still consult the full record for rationale, but a plan routing users to
the identity provider's password-change page would conflict with a high-salience local
instruction before implementation began.
