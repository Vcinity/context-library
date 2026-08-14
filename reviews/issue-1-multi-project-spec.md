# Specification review: #1 / multi-project Manager deployment

Status: Draft for human approval
Originating issue: #1 — Support multiple project packs in one Manager deployment
Authority: `SPEC.md` Sections 4, 5, 6, 8, 9, 11, 12, and 16

## Scope and non-goals

This issue defines the versioned configuration, runtime ownership, isolation,
fair scheduling, lifecycle, and compatibility contracts needed for one Manager
deployment to govern multiple explicitly enrolled project packs.

It does not implement shared-context inheritance, applicability selection,
retrieval, new canonical data, direct external connectors, or a second
authorization model. The Plugin remains read-only and the Maintainer remains
the only normal canonical mutation authority.

## Required behavior

- A versioned deployment configuration contains an explicit, non-empty,
  duplicate-free `managed_projects` list. Each entry names a stable project
  identifier and its canonical library root; discovery may validate entries but
  MUST NOT enroll an unlisted project.
- The legacy `CLM_PROJECT` configuration is normalized to one managed-project
  entry with equivalent behavior. Supplying both legacy and new forms is
  rejected unless they describe the same single project unambiguously.
- An authenticated session's effective project scope is the intersection of
  configured projects and identity claims. A requested project outside that
  intersection is rejected and audited; project selection is never trusted
  solely from a URL, cookie, or form field.
- API, browser, proposal, review, publication, configuration, audit, health,
  search, and telemetry operations carry an explicit project scope. Responses
  do not combine project data unless a documented administrative aggregate
  endpoint explicitly requests it and labels every result by project.
- One shared worker/scheduler/reconciliation/notification process pool uses a
  fair project-aware queue. A bounded per-project lease/concurrency allowance
  and round-robin or equivalent starvation-free selection prevent one busy
  project from monopolizing workers.
- Runtime state, budgets, caches, leases, reviews, policy revisions, audit
  lineage, telemetry events, publication locks, and service lifecycle are
  independently keyed by project. No mutable singleton service state may
  silently apply to every project.
- A project pause, drain, budget exhaustion, dirty canonical checkout,
  publication recovery failure, or worker exception isolates that project and
  leaves unrelated project queues eligible to proceed. Shared process failure
  remains observable and recoverable without cross-project data reuse.
- Canonical writes acquire a per-project lock and revalidate the project
  enrollment, authority, library root, and current digest immediately before
  publication. Evidence, reviews, cache results, authorizations, and
  idempotency keys cannot be replayed under another project.
- Adding a project validates its safe canonical root, initializes its scoped
  state and telemetry manifest, and records an audited configuration revision
  before scheduling work. Removing a project first stops new claims, drains or
  safely leases in-flight work, records the removal, and leaves historical
  project data auditable; it does not delete canonical data.

## Proposed versioned configuration

The deployment configuration remains schema version 1 and adds an explicit
`managed_projects` object to the deployment-owned configuration. Each entry
contains:

```yaml
schema_version: 1
managed_projects:
  - id: demo
    library_root: /srv/context-library/demo
    state_namespace: demo
    enabled: true
```

`id` is a stable lowercase identifier. `library_root` is absolute, non-root,
non-symlinked, and validated as the separately governed canonical checkout.
`state_namespace` is unique and immutable after enrollment. `enabled: false`
retains the project for audit/history but excludes it from new scheduling.
Deployment-wide bounds (database, worker pool, identity provider) remain
deployment-owned; project budgets and policies remain project-scoped.

The normalized internal model is a `ProjectRegistry` snapshot. Runtime users
receive an immutable `ProjectContext` containing the registry entry, scoped
settings, and project identifier. No code may obtain a project context by
mutating a shared `Settings.project` value in place.

## Scheduling and lifecycle invariants

The scheduler maintains one queue per enabled project and selects claims with
starvation-free weighted round-robin, subject to each project's configured
concurrency, budget, pause/drain state, and lease capacity. A project with no
eligible work is skipped without delaying other projects. Lease recovery keeps
the project identity in the lease and rejects cross-project acknowledgements.

Lifecycle transitions are `enabled`, `paused`, `draining`, `disabled`, and
`error`. A transition is versioned, idempotent, actor-attributed, and emitted
as a project-scoped audit event. `draining` stops new work but permits safe
completion or recovery of in-flight work; `disabled` is the terminal scheduling
state until an explicit audited re-enable.

## API and data boundaries

The existing `/api/v1/projects/{project}/...` boundary remains the stable
consumer-facing shape. Project listing and selection return only the effective
identity/configuration intersection. Every database query and cache key in the
multi-project path includes project identity. Aggregate health and telemetry,
if added, are read-only administrative views with explicit per-project rows;
they cannot be used as a mutation target.

## End-to-end validation slice

The principal black-box slice is:

1. A synthetic two-project deployment configuration is loaded and exposes only
   its explicit project set;
1. Two authenticated identities exercise project selection and denied
   cross-project requests through the Manager HTTP/browser boundary;
1. Concurrent work is scheduled through one shared worker pool with bounded
   fairness, independent budgets, leases, reviews, and cache namespaces;
1. One project is paused, made dirty, drained, restarted, and recovered while
   the other continues; and
1. Independent publication and telemetry reports retain project-local audit
   lineage and cannot be resolved with the other project's identifiers.

Required negative cases include an unlisted discovered pack, duplicate project
IDs, unsafe or symlinked roots, cross-project lease/review/idempotency reuse,
identity scope escalation, starvation under a saturated project, restart with
an in-flight lease, removal during drain, and publication failure isolation.
Tests use synthetic repositories or temporary copies and realistic local fakes;
they never mutate canonical data or require external services.

## Affected components

- `src/context_library_manager/config.py` and configuration contracts for the
  versioned registry and legacy normalization;
- Manager auth/session and API routing for effective project scope;
- Manager store, worker, scheduler, notification, reconciliation, and service
  lifecycle for project-keyed state and fair shared scheduling;
- Maintainer application context and publication locks for project identity;
- database migrations and audit/telemetry read models where singleton state is
  currently assumed;
- frontend project selection and aggregate/status views;
- contract schemas, synthetic fixtures, Manager integration tests, and
  browser-driven tests.

## Risks and unresolved questions

- Existing deployments may have state tables or cache keys that lack a
  project dimension; migration must preserve the legacy project without
  guessing ownership of ambiguous rows.
- The exact fairness quantum and per-project concurrency defaults should be
  selected during implementation from measured queue behavior, but the
  starvation-free invariant is normative.
- Runtime configuration changes need an operator-facing review and rollback
  surface; implementation must not silently hot-reload a canonical root.

## Approval boundary

This artifact is planning-only. No implementation code, tests, dependencies,
migrations, or runtime configuration changes are included. Implementation may
begin only after the GitHub Project `Spec Gate` is explicitly set to
`Approved`.
