# Roadmap

This roadmap tracks agreed goals that are not yet complete. It is not a
release calendar and does not assign work to a particular version. `SPEC.md`
remains the authority for current behavior, interfaces, safety rules, and
acceptance criteria. Completed roadmap items move to `CHANGELOG.md`.

## Multi-project Manager

Status: planned

Allow one Manager deployment to govern multiple explicitly configured project
packs. The current runtime is initialized with one `CLM_PROJECT`, and its
worker, scheduler, reconciliation, notification, configuration, and heartbeat
loops are bound to that project. Running a separate Manager stack for every
additional project is a transitional deployment constraint, not the target
architecture.

Acceptance criteria:

- A versioned Manager configuration declares the set of managed projects;
  pack discovery alone must not silently enroll a project for mutation.
- API and browser sessions expose only projects allowed by both Manager
  configuration and authenticated identity scope.
- One deployment provides project selection for search, proposals, reviews,
  publications, configuration, audit, health, and telemetry.
- Worker, scheduler, reconciliation, and notification processes schedule work
  across managed projects fairly, without requiring one process group per
  project.
- Project policy, budgets, caches, leases, reviews, publication authority,
  audit lineage, and SLO telemetry remain independently scoped.
- A failure, pause, drain, budget exhaustion, dirty canonical pack, or
  publication recovery event in one project does not block unrelated projects.
- Canonical writes retain per-project locks and authority checks; no request,
  evidence record, review, cache result, or authorization can cross a project
  boundary accidentally.
- Adding or removing a managed project is audited, safe for in-flight work,
  and does not require a new Manager deployment.
- Existing single-project configuration remains supported through a defined
  compatibility migration.
- PostgreSQL and browser-driven tests exercise concurrent work in at least two
  projects, including isolation, fairness, restart recovery, and independent
  publication.

Multi-project management and shared-context inheritance are complementary:
the Manager goal governs several packs in one runtime, while inheritance
defines how selected context scopes compose within a project's effective
read model.

## Shared-context inheritance

Status: planned

Allow a project pack to consume explicitly selected shared context without
copying organization-wide or cross-project decisions into every project pack.
The effective context must remain deterministic, attributable, and safe for
all read and write paths.

Acceptance criteria:

- A versioned canonical contract declares shared-context relationships
  explicitly; installation, filesystem layout, or the presence of one pack
  must not imply inheritance.
- Core resolves the effective context deterministically and rejects missing
  required parents, cycles, duplicate identities, and ambiguous graphs.
- Every effective decision retains its owning scope, evidence, provenance,
  supersession history, and canonical source identity.
- Project-specific and inherited disagreements remain visible to normal
  conflict policy; inheritance must not silently strengthen provenance or
  overwrite a decision.
- Maintainer validation, reconciliation, query, indexing, and publication
  operate on the effective context while writes remain scoped to the pack
  that owns the changed record.
- Manager search, proposal preview, review, audit, and publication show the
  source scope of inherited decisions.
- Plugin MCP results and projections expose the same effective context and
  source scope while remaining canonical-read-only.
- Legacy project packs continue to behave as standalone packs until they opt
  into inheritance.
- Shared positive and negative fixtures cover multiple parents, ordering,
  conflicts, cycles, missing parents, supersession, and provenance.

The design must preserve the authority and dependency boundaries in
[`SPEC.md`](SPEC.md), especially Sections 4, 6, 8, 9, and 12.

## Review-authorized publication

Status: correctness gap

Make an administrator's audited Manager approval sufficient to publish the
approved candidate without temporarily enabling automatic publication for the
project. Automatic publication policy and human-authorized publication are
different authorities and must remain independently enforceable.

Acceptance criteria:

- The typed Maintainer service accepts an explicit, capability-checked
  publication authorization for an exact candidate set.
- Manager binds the authorization to the review, actor, project, candidate,
  policy revision, and idempotency key.
- An approved publication succeeds while `automatic_publication` remains
  disabled before, during, and after the operation.
- Automatic workers cannot reuse a human authorization for another candidate
  or publish unrelated ready work.
- Retry and recovery preserve the authorization lineage without requiring a
  second approval or broadening its scope.
- CLI and Plugin paths do not gain implicit publication authority.
- Integration tests cover approval, denial, stale authorization, replay,
  retry, concurrent ready candidates, and publication recovery.

This closes the gap between the publication policy described in `SPEC.md`
Sections 4.2, 10, 11.4, and 13.1 and the current automatic-publication gate.

## Production autonomy evidence

Status: operational evidence pending

Establish a complete rolling 30-day production measurement window for the
post-deployment autonomy objective already defined in `SPEC.md` Section 11.5.
The telemetry implementation and deterministic fixtures are complete; a
fixture or partial production window is not production SLO evidence.

Acceptance criteria:

- The deployed producer manifest, event sequences, heartbeats, watermarks,
  item lineage, and replay reconciliation remain complete for the full
  window.
- Every coverage gap is named and prevents an achieved claim until repaired
  from authoritative durable events.
- The dashboard reports separate history, telemetry, and SLO states using the
  specified precedence.
- At least 95% of eligible items reach successful terminal outcomes without a
  qualifying post-intake human intervention.
- The inappropriate agent-invocation count remains zero.
- Operators can reproduce the numerator, denominator, exclusions, and agent
  usage from persisted event lineage without exposing protected evidence.

## Roadmap discipline

Add an item only when it is backed by `SPEC.md`, an accepted decision, or an
explicit user direction. Keep deployment-specific configuration and canonical
organization content outside this public product roadmap. A roadmap item is
complete only when its acceptance criteria and the applicable root validation
commands pass.
