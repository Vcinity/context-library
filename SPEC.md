# Context Library Monorepo Specification

## 1. Status and Authority

This document is the implementation authority for the Context Library code
monorepo. The monorepo unifies the existing Context Library Maintainer tool,
Manager Runtime and web application, and Codex Plugin into one versioned
source and release unit.

The canonical Context Library data repository remains separate from this
monorepo. Documentation refers to its configured checkout as
`<canonical-library-root>`.

This specification supersedes the repository-boundary and cross-component
interface provisions of the following source specifications:

- `context-library-tool/SPEC.md`
- `context-library-manager/SPEC.md`
- `context-library-plugin/SPEC.md`

Those documents remain migration evidence for behavior not contradicted here.
The canonical data repository's `SPEC.md` remains authoritative for decision
history, provenance, supersession, indexes, and project-pack content.

Normative terms are **MUST**, **MUST NOT**, **SHOULD**, **SHOULD NOT**, and
**MAY**. A release is complete only when all applicable MUST requirements and
acceptance criteria in this document pass.

## 2. Product Goal

The monorepo MUST provide one coherent Context Library system in which:

- the Manager is the normal human and automation management surface;
- the Maintainer supplies deterministic validation, reconciliation,
  publication, migration, and query behavior;
- the `clm` command remains available for administration, development,
  recovery, and explicitly authorized unattended operation;
- the Plugin supplies read-only organizational context to agents and projects;
- shared schemas and parsing behavior have one source of truth;
- Tool, Manager, and Plugin ship under one release version; and
- canonical decision data remains separately governed and append-only.

The system MUST preserve the established principle that deterministic code
performs every operation that does not require semantic judgment.

The post-deployment operational service-level objective is:

> At least 95% of eligible maintenance items reach a successful terminal
> outcome without human action over a rolling 30-day window, while no agent
> invocation is made for work that the deterministic Runtime can complete
> safely.

This is an autonomy target, not an agent-utilization target. Agent invocation
is a bounded fallback for semantic judgment and SHOULD decrease as
deterministic behavior and safe cache coverage improve.

Eligible items are source batches and candidate work governed by normal
project policy. Categories explicitly requiring human approval are excluded
from the autonomy denominator but MUST remain visible in operational metrics.
Policy exclusions MUST be revisioned and auditable.

Release acceptance proves that this objective is measured correctly; it does
not require waiting 30 days for a new deployment to accumulate production
history. Until a continuous 30-day window exists, a production dashboard MUST
report `history_status` as `insufficient-history`; its overall `slo_state`
follows the precedence defined in Section 11.5. Synthetic fixtures MUST NOT be
represented as production SLO evidence.

## 3. Scope

### 3.1 In scope

- Refactoring the available Tool, Manager, and Plugin implementations into one
  monorepo.
- A shared contract and canonical-artifact parsing package.
- A write-capable Maintainer service layer and `clm` CLI.
- The FastAPI Manager API, processes, and operator web application.
- The installable Codex Plugin, skill, session-start behavior, projection, and
  read-only MCP server.
- One release/version policy and one integrated validation surface.
- Migration compatibility with the existing canonical data repository,
  including its legacy flat-pack layout.
- Explicit context-requirement behavior and missing-context notification.
- Local SQLite operation and PostgreSQL 15+ production compatibility.
- Bounded agent-provider execution behind the Manager.
- Adversarial interface, security, recovery, and provenance testing.

### 3.2 Out of scope

- Copying canonical project decisions into the code monorepo.
- Making the Plugin a writer or maintenance client for the canonical database.
- Replacing the canonical data repository with application database state.
- Letting an individual agent publish independently of Manager or explicit
  administrative `clm` authorization.
- Direct Jira, Confluence, chat, email, or repository connectors in the core.
- A mandatory external model provider for deterministic tests.
- Rewriting or deleting historical decision records during migration.
- Modifying the live shared Context Library as part of monorepo construction
  without separate explicit authorization.

## 4. Governing Decisions

### 4.1 Repository model

The Context Library system MUST use two conceptual repositories:

1. the code monorepo defined by this specification; and
1. one or more separately governed canonical Context Library data
   repositories.

The Plugin, Tool, and Manager MAY share a release schedule because they are
one code product. This remains consistent with the prior decision to separate
the Plugin from the canonical data repository: code distribution and
canonical decision content still live in different repositories.

### 4.2 Canonical write authority

Only the Maintainer service layer may mutate canonical Context Library files.
The normal production call path is:

```text
human or automation
        |
        v
Manager API and review policy
        |
        v
Maintainer application service
        |
        v
canonical Context Library
```

The `clm` CLI is a second adapter over the same Maintainer service. Its
write-capable commands MUST require the same schemas, policy checks,
provenance rules, publication gates, path safety, and audit behavior as the
Manager path.

### 4.3 Plugin authority

The Plugin is a read-side integration. It:

- MAY read canonical data through its bundled read-only MCP server;
- MAY compile explicitly configured context into generated files in a
  consumer worktree;
- MAY notify a user or agent about missing required context;
- MAY direct a user to the Manager to propose or perform an update; and
- MUST NOT mutate, initialize, migrate, repair, or publish canonical Context
  Library data.

Repository-local generated projections are caches, not canonical data writes.

### 4.4 Agent authority

The normal agent path is contribution through the Manager. Direct agent use of
`clm` is restricted to documented development, recovery, migration, or
explicitly authorized unattended-maintenance workflows.

No agent may strengthen provenance, silently resolve a genuine authority
conflict, or edit canonical Markdown directly.

### 4.5 Harvester integration

Private context harvesters integrate through the versioned
`context-library/harvest-batch` contract documented in
`contracts/HARVESTER_LIBRARY_V1.md`.

The contract is proposal-only. It MUST contain redacted source evidence,
observations, unreviewed candidates, and findings as applicable. It MUST NOT
grant a harvester canonical write authority, contact-notification authority,
or permission to bypass Manager policy and Maintainer publication gates.

The public repository owns the contract and its compatibility policy. Private
harvester repositories own source adapters, transports, model providers, and
deployment configuration. Cross-repository behavior changes MUST update the
versioned contract and its generated schema.

## 5. Required Repository Structure

The implemented monorepo MUST converge on this structure or an equivalently
clear structure documented in `ARCHITECTURE.md`:

```text
.
├── AGENTS.md
├── ARCHITECTURE.md
├── CHANGELOG.md
├── IMPLEMENTATION_PROMPT.md
├── Makefile
├── README.md
├── SPEC.md
├── pyproject.toml
├── poetry.lock
├── package.json
├── package-lock.json
├── contracts/
│   ├── README.md
│   ├── schemas/
│   └── fixtures/
├── src/
│   ├── context_library_core/
│   ├── context_library_maintainer/
│   └── context_library_manager/
├── frontend/
├── plugins/
│   └── context-library/
├── scripts/
├── tests/
│   ├── contracts/
│   ├── maintainer/
│   ├── manager/
│   ├── plugin/
│   ├── integration/
│   └── adversarial/
├── e2e/
└── reviews/
```

The final code organization MUST make ownership visible:

- `context_library_core` owns shared contracts, canonical JSON, parsing,
  redaction primitives, project-pack discovery, and read models.
- `context_library_maintainer` owns durable maintenance state,
  reconciliation, conflict handling, publication, and the `clm` adapter.
- `context_library_manager` owns orchestration state, HTTP and browser
  surfaces, authentication, review, notification, agent dispatch, cost, and
  runtime processes.
- `plugins/context-library` owns Codex packaging, the read-only MCP adapter,
  context activation policy, consumer-local projection, and agent guidance.

## 6. Dependency and Authority Direction

Production dependencies MUST point inward:

```text
Plugin adapters ----------> generated/read-only Core subset
Maintainer CLI -----------> Maintainer service ----------> Core
Manager API/workers ------> Maintainer service ----------> Core
Frontend -----------------> Manager HTTP API
```

The Core MUST NOT import Manager, Maintainer, Plugin, FastAPI, database
drivers, or frontend code.

The Maintainer MUST NOT import Manager or Plugin code.

The Manager MAY import the Maintainer application service directly. It MUST
NOT depend on shell construction or implementation-derived CLI command names.

The Plugin MUST be deployable without installing the Manager or write-capable
Maintainer. Any shared code required by the Plugin MUST be packaged as a
deterministically generated read-only runtime subset.

## 7. Release and Versioning Contract

### 7.1 One product version

The monorepo MUST declare one strict semantic version. The same version MUST
appear in:

- Python package metadata;
- Manager API metadata;
- `clm version --json`;
- the Plugin manifest;
- the Plugin MCP `serverInfo`; and
- generated release artifacts.

Component-specific build metadata MAY be exposed separately, but it MUST NOT
create incompatible component release lines.

### 7.2 Compatibility

Internal components at the same release version MUST be compatible.
Compatibility across different monorepo versions is not required unless a
release note explicitly promises it.

Persisted Runtime state, Maintainer state, canonical project-pack content, and
public Manager APIs still require migration compatibility. A release MUST:

- migrate supported persisted state forward safely;
- refuse unsupported newer schemas with an actionable error;
- preserve canonical decision history;
- document irreversible migrations; and
- provide rollback or recovery instructions.

### 7.3 Contract versions

`schema_version` MUST identify a named contract family, not act as an
unqualified global number. Machine-readable payloads MUST be associated with a
stable schema identifier such as:

```json
{
  "schema": "context-library/source-envelope",
  "schema_version": 1
}
```

The exact field representation MAY differ when a protocol already supplies a
schema namespace, but validation MUST always know both the family and version.

## 8. Shared Contract System

### 8.1 Sources of truth

The monorepo MUST maintain one source of truth for:

- source envelopes;
- observations;
- candidates;
- semantic findings;
- conflict packets and resolutions;
- project-pack discovery;
- canonical decision records;
- query results;
- Maintainer command envelopes and errors;
- agent-provider requests and responses;
- context-requirement state;
- Plugin missing-context notices; and
- Manager HTTP request and response models.

Pydantic models MAY be the executable source, provided deterministic JSON
Schema artifacts are generated and checked for drift.

### 8.2 Canonical parsing

There MUST be exactly one authoritative canonical decision-register parser.
The Tool, Manager, contract tests, and Plugin packaging process MUST derive
their behavior from it.

The Plugin distribution MAY contain a generated copy so it remains
self-contained. Generated Plugin code MUST:

- identify its source version and digest;
- be reproduced by one documented command;
- fail a non-mutating check when stale; and
- contain no write-capable Maintainer behavior.

Independent handwritten Markdown parsers for Tool query, Plugin projection,
and Plugin MCP access are prohibited.

### 8.3 Contract fixtures

`contracts/fixtures/` MUST contain positive and negative fixtures covering:

- explicit, inferred, and assumed provenance;
- direct and synthesized records;
- supersession and cycles;
- legacy and project-pack layouts;
- malformed and anchorless content;
- CRLF, lone-CR, and UTF-8 failures;
- secret-like evidence metadata;
- ambiguous project selection;
- unavailable context; and
- required, optional, disabled, and undetermined context policy.

Every consumer MUST run against the same fixtures.

## 9. Canonical Data Repository Contract

### 9.1 Separation

Canonical project packs and decision artifacts MUST remain outside the code
monorepo. Tests MUST use temporary copies or synthetic fixtures and MUST NOT
mutate the live shared checkout.

The development root MUST be supplied through configuration or environment
and is represented in documentation as:

```text
<canonical-library-root>
```

No machine-specific absolute path may be required.

### 9.2 Supported layouts

Read behavior MUST support the legacy flat-pack layout during migration:

```text
decision-artifacts/
```

The target layout is:

```text
projects/<project>/
├── README.md
├── decision-register.md
├── index-by-category.md
├── index-by-date.md
├── maintainer.yaml
├── topology.yaml
└── authority.yaml
```

Pack discovery MUST NOT return the same logical project twice. Legacy
selection MUST be represented as a compatibility location for one logical
pack.

Writing MUST target the project-pack layout. Migration from the legacy layout
MUST be explicit, dry-run by default, byte-preserving for existing decision
records, and separately authorized for a real canonical checkout.

### 9.3 Append-only behavior

Publication MUST:

- preserve existing decision-record bytes unless a format migration is
  explicitly authorized;
- add new decisions and supersession relationships rather than erase history;
- preserve all evidence and weakest provenance;
- regenerate aligned indexes deterministically;
- validate Plugin parser/projection compatibility before commit;
- refuse dirty worktrees and path hazards; and
- be atomic or report every unrestored path.

## 10. Maintainer Application Service

### 10.1 Responsibility

The Maintainer service MUST own:

- configuration, topology, and authority validation;
- local durable maintenance state;
- source ingestion and identity;
- observations, candidates, and findings;
- deterministic reconciliation;
- work leasing;
- conflicts and human resolutions;
- canonical rendering and indexes;
- publication staging and commit behavior;
- read/query behavior over canonical project packs; and
- validation and migration.

### 10.2 Stable Python boundary

Manager MUST call a typed Maintainer application-service boundary for normal
in-process operation. The service MUST return typed results rather than
captured stdout.

The service MUST support transaction, timeout, cancellation, and audit
boundaries appropriate to the operation. Long or isolatable publication work
MAY execute in a dedicated process through a structured adapter.

### 10.3 `clm` CLI

`clm` MUST be a thin adapter over the same service. Canonical commands are:

```text
clm version
clm capabilities
clm init
clm migrate legacy-pack
clm ingest
clm observe add
clm candidate add
clm finding add
clm work next
clm work renew
clm work release
clm reconcile
clm publish
clm conflict list
clm conflict show
clm conflict resolve
clm status
clm validate
clm query
clm maintain
```

Implementation-derived aliases such as `ingest-cmd`, `reconcile-cmd`, and
`publish-cmd` MUST NOT be used by internal consumers and SHOULD be removed
before the first monorepo release.

With `--json`, stdout MUST contain one validated envelope and no prose.
Diagnostics MUST go to stderr. Exit codes MUST retain these meanings:

| Code | Meaning |
| --- | --- |
| `0` | Requested operation completed successfully |
| `1` | Valid operation found pending, stale, invalid, or conflicted work |
| `2` | Usage, configuration, schema, or safety error |
| `3` | Atomic publication or rollback failure |
| `4` | Active lease or project lock prevented the operation |

`clm version --json` and `clm capabilities --json` MUST expose the product
version, supported schema families, canonical layout versions, and enabled
features.

## 11. Manager Runtime

### 11.1 Responsibility

The Manager MUST provide:

- deterministic-first intake and routing;
- bounded semantic-agent execution only when required;
- budgets, caching, attribution, and cancellation;
- durable review and notification;
- authenticated human and M2M APIs;
- a server-rendered and progressively enhanced operator UI;
- runtime health, audit, and publication recovery;
- revisioned project policy; and
- the exclusive normal management interface for canonical updates.

### 11.2 Technology

The Manager MUST retain:

- Python 3.12 or later;
- Poetry-managed Python dependencies;
- FastAPI and Pydantic v2;
- SQLite for deterministic local tests;
- PostgreSQL 15+ for multi-replica deployment;
- server-rendered HTML;
- React 18 or later with TypeScript for interactive regions;
- Vite-built content-hashed same-origin assets; and
- OIDC-compatible deployed identity.

### 11.3 Agent-provider contract

Agent providers MUST remain adapters over one versioned JSON request and
response contract. Requests MUST contain minimum relevant redacted evidence,
an immutable prompt revision, a task type, a model profile, and a bounded
budget.

Deterministic work, cache hits, schema failures, exact duplicates, publication
staging, and retry scheduling MUST invoke no model.

### 11.4 Contributions

Individual agents submit evidence-backed proposals through the Manager. They
MUST NOT publish, resolve authority conflicts, or write canonical files.

The contribution API MUST be idempotent, project-scoped, authenticated,
audited, budgeted, and schema-compatible with central workers.

### 11.5 Autonomy and agent-call efficiency

The Manager MUST calculate daily and rolling 30-day autonomy metrics from
persisted work, review, policy, and agent-invocation events:

```text
autonomy_rate =
    eligible_items_reaching_success_without_human_action
    / eligible_items_in_measurement_cohort
```

For a window ending at time `T`, the measurement cohort MUST contain:

- every eligible item created during `[T - window, T]`; and
- every older eligible item that was unresolved at `T - window`.

An unresolved, queued, leased, running, deferred, or waiting item remains in
the denominator. It MUST NOT disappear merely because it has not reached a
terminal state or because it was created before the window. An older item
resolved before the window began is not part of the cohort.

The autonomy measurement boundary begins at the durable `intake-accepted`
event, after submission, authentication, schema validation, and initial
persistence succeed. Human creation, submission, clarification, or correction
before and including that event is intake and MUST NOT count as intervention.

After `intake-accepted`, a qualifying human intervention is any human-authored
event that changes the item's processing or decision outcome, including
review, approval, resolution, evidence or candidate editing, policy override,
manual retry, requeue, cancellation, or terminal-state override. Read-only
inspection, receiving a notice, or supplying the original intake does not
count as intervention.

An item counts in the numerator only when it reaches a successful terminal
outcome without a qualifying post-intake human intervention. A human-resolved
item that later succeeds MUST NOT be counted as autonomous.

Eligibility and exclusion MUST be evaluated once using the project-policy
revision effective at `intake-accepted`. The item MUST persist that revision
and its resulting eligibility classification. Later policy changes MUST NOT
retroactively add or remove the item from its cohort. Metrics MUST segment
counts by intake policy revision, and carried-in unresolved items retain their
original revision. A deliberate reclassification creates a new auditable work
item; it MUST NOT rewrite the original item's metric history.

The Manager MUST separately report:

- numerator, denominator, exclusions, date window, and policy revision;
- deterministic-only completion rate;
- cache-only semantic completion rate;
- model-assisted completion rate;
- agent invocation rate and invocation reason;
- inappropriate agent-invocation count;
- agent cache-hit rate;
- tokens per eligible item;
- cost per published decision;
- human escalation rate by reason;
- retry and failure rate;
- duplicate-work rate;
- median and p95 time to terminal outcome;
- non-terminal eligible backlog by age;
- percentage of items deferred by budget; and
- telemetry completeness and any known collection gaps.

The inappropriate agent-invocation count MUST be zero. Every provider call
MUST identify the semantic task that required it and the deterministic and
cache checks already attempted. Agent calls MUST be counted per unique
invocation and per unique eligible item so retries cannot obscure usage.

Metrics MUST be derived from persisted event lineage rather than inferred only
from final work state, item type, or an unversioned payload category. The
dashboard MUST NOT present the 95% target as achieved when telemetry is
incomplete.

The dashboard and API MUST report three separate fields:

- `history_status`: `complete` only after a continuous 30-day production
  measurement span exists; otherwise `insufficient-history`;
- `telemetry_status`: `complete` only when the evidence requirements below
  hold for the evaluated span; otherwise `insufficient-telemetry`; and
- `slo_state`: one of `insufficient-telemetry`, `insufficient-history`,
  `no-data`, `met`, or `missed`.

`slo_state` MUST be derived in this precedence order:

1. `insufficient-telemetry` when `telemetry_status` is not `complete`,
   regardless of history duration.
1. `insufficient-history` when telemetry is complete but `history_status` is
   not `complete`.
1. `no-data` when telemetry and history are complete but the denominator is
   zero.
1. `met` when telemetry and history are complete, the denominator is non-zero,
   the autonomy rate is at least `0.95`, and inappropriate agent invocations
   equal zero.
1. `missed` otherwise.

Shorter-window metrics MAY be shown before history is complete, but they MUST
NOT be presented as the operational SLO. Empty complete windows MUST report no
rate and `no-data`, not `met`.

Telemetry for a measurement window is complete only when all of the following
are true:

- every cohort item has a durable `intake-accepted` event, intake policy
  revision, eligibility classification, and contiguous state-event lineage;
- work, review, policy, agent-invocation, and notification producers write
  monotonically increasing per-project sequence numbers to the durable event
  log;
- a revisioned deployment manifest identifies every required producer for
  enabled features, and that set cannot change retroactively for a window;
- every required producer writes a heartbeat through the same durable path at
  least once every 60 seconds continuously while the deployment contributes
  production SLO telemetry;
- stored producer watermarks and sequence ranges prove that no event or
  heartbeat is missing for any interval in the window;
- the collector records no unresolved parse, persistence, clock-ordering, or
  reconciliation error; and
- the current materialized state reconciles exactly with replay of the
  covered event log.

A missing sequence, heartbeat lapse greater than 60 seconds, absent intake
policy revision, unknown producer watermark, unreconciled item, or collector
outage creates a named coverage gap. Any coverage gap overlapping the window
MUST set `telemetry_status` and `slo_state` to
`insufficient-telemetry`; it MUST NOT be silently interpolated. The dashboard
MUST expose each gap's producer, start, end, reason, and reconciliation state.
After the gap is repaired from authoritative durable events, completeness MAY
be recomputed; a heartbeat alone is not evidence that missing events were
recovered.

## 12. Plugin Contract

### 12.1 Distribution

The Plugin MUST remain an independently installable artifact within the
monorepo and share the monorepo product version.

It MUST include:

- a valid strict-semver Plugin manifest;
- a marketplace entry;
- a read-only MCP server;
- the Context Library skill and references;
- a session-start hook;
- consumer-local projection and check operations; and
- deterministic packaging and validation.

### 12.2 Absolute canonical read-only rule

The Plugin MUST NOT:

- create or modify a project pack;
- write canonical Markdown or indexes;
- initialize or migrate a canonical repository;
- open a write-capable Manager or Maintainer session;
- submit a contribution automatically;
- resolve a conflict;
- publish a decision;
- transport a credential granting those operations; or
- imply that a generated projection is canonical.

The Plugin MCP tool set MUST be read-only by construction. A test MUST prove
that every advertised MCP tool is non-mutating and that path traversal cannot
reach content outside the configured library root.

### 12.3 Consumer-local writes

The Plugin MAY write only these classes of consumer-local generated data:

- marked `AGENTS.md` constraint blocks;
- `.context-library/projection.json`;
- Plugin-local cache or diagnostic state explicitly permitted by the host.

Automatic consumer-local writes require explicit project binding and a
context policy other than `disabled`. The Plugin MUST preserve human-authored
content, refuse locally modified generated blocks, and fail safely on
ambiguous or unsafe scopes.

### 12.4 Context requirement policy

A consuming workspace MAY declare:

```json
{
  "schema": "context-library/context-policy",
  "schema_version": 1,
  "project": "example-project",
  "context_requirement": "required",
  "affected_layers": {}
}
```

`context_requirement` is one of:

- `required`: organizational context is expected; absence requires notice;
- `optional`: use context when available but do not require notice when
  unavailable; or
- `disabled`: do not load, project, or mention organizational context.

When no explicit policy applies, the state is `undetermined`. It MUST behave
like `optional` for non-interference, but MUST remain distinguishable in
diagnostics.

Requirement MUST be established by explicit repository configuration,
explicit organizational policy supplied by the host, or an equivalent
auditable signal. It MUST NOT be inferred merely because:

- the Plugin is installed;
- the library contains exactly one pack;
- a shared filesystem path exists; or
- the current directory is a Git repository.

### 12.5 Missing-context behavior

The Plugin MUST classify resolution as:

- `available`;
- `missing`;
- `unreadable`;
- `invalid`;
- `ambiguous`; or
- `stale-projection`.

When required context is not `available`, the Plugin MUST make a concise notice
available to the user and agent. The notice MUST include:

- the project or requirement signal when known;
- the failure classification without exposing secrets;
- which action proceeded or could be affected;
- that no substitute context was fabricated;
- an invitation for the user to provide relevant context; and
- a recommendation to use the Manager when canonical context should be
  created or updated.

The Plugin MUST NOT itself perform that update.

Missing required context does not create Plugin write authority. Unless a
higher-level repository or organizational instruction explicitly makes
missing context a stop condition, the notice is advisory and the user or agent
may proceed.

For optional or undetermined context, unavailability MUST NOT block, redirect,
or inject generic instructions into the agent's task. The skill MAY permit the
agent to disclose that it proceeded without organizational context when that
fact is materially relevant and to give the user an opportunity to supply it.

Disabled context MUST remain silent.

### 12.6 Session-start behavior

The session-start hook MUST:

1. determine the activation root safely;
1. resolve an explicit context-requirement signal;
1. check availability without canonical writes;
1. synchronize a configured projection only when allowed and safe;
1. notify on unavailable required context; and
1. otherwise fail open without adding generic guidance.

The hook MUST NOT auto-select a project merely because it is the only available
pack.

### 12.7 Skill behavior

The skill MUST teach agents to:

- consult relevant current explicit decisions when available;
- preserve supersession and provenance;
- recognize when context is required, optional, disabled, or undetermined;
- never create or update canonical context through the Plugin;
- notify the user when required context is unavailable;
- continue without interference when context is not required;
- disclose materially relevant missing context when appropriate;
- invite the user to provide missing context; and
- recommend the Manager for canonical additions or corrections.

## 13. User and Agent Workflows

### 13.1 Normal human workflow

1. Use the Manager to search canonical context.
1. Inspect provenance, evidence, and supersession.
1. Preview an evidence-backed proposal.
1. Submit with an idempotency key.
1. Allow deterministic checks and bounded semantic work to run.
1. Resolve only genuine human-authority reviews.
1. Inspect publication and audit evidence.

### 13.2 Normal individual-agent workflow

1. Read Plugin-provided organizational context when available.
1. If required context is unavailable, notify the user.
1. Gather evidence with authorized connectors or user-provided material.
1. Submit observations or proposals through the Manager.
1. Never publish or edit canonical files directly.
1. Continue unrelated user work while a management review waits.

### 13.3 Administrative Maintainer workflow

Direct `clm` operation is permitted for:

- initial setup;
- local development;
- data migration;
- incident recovery;
- deterministic validation;
- break-glass administration; or
- explicitly authorized unattended maintenance.

The administrative workflow MUST be documented separately from normal Plugin
guidance so an agent cannot confuse read-side Plugin authority with
write-capable Maintainer authority.

## 14. Security and Safety

The monorepo MUST:

- reject path traversal and symlink escapes;
- preserve dirty worktrees;
- avoid shell interpolation of source or agent content;
- cap subprocess time and output where a subprocess boundary remains;
- redact secret-bearing fields from UI, logs, audit, review, and Plugin
  notices;
- require authentication, project scope, capability, CSRF, and idempotency as
  applicable;
- keep browser sessions distinct from M2M bearer tokens;
- prevent cross-project evidence access;
- use restrictive same-origin content security policy;
- use atomic writes and complete rollback diagnostics;
- preserve CRLF and lone-CR human-authored consumer content;
- refuse malformed generated markers and locally edited generated blocks; and
- ensure Plugin artifacts contain no canonical write capability.

Destructive migration of source repositories or canonical data is prohibited.
Old repositories remain untouched until the user separately approves archival
or retirement.

## 15. Observability and Audit

Mutating Manager and Maintainer operations MUST emit durable audit records with
actor, project, operation, input identity, policy revision, result, affected
records, and safe error classification.

Autonomy telemetry MUST retain enough event identity to reproduce every
reported numerator, denominator, exclusion, human action, cache use, provider
call, token count, cost, and terminal-state transition. Operators MUST be able
to drill from an aggregate metric to the contributing work items without
exposing secret evidence. Metric snapshots are derived operational data, not
canonical Context Library decisions.

The Plugin MUST expose diagnostic state sufficient to explain:

- selected project and requirement source;
- context availability classification;
- canonical source digest when available;
- projection freshness;
- whether a notice was emitted; and
- whether the agent proceeded without context.

Plugin diagnostics MUST NOT become canonical audit records and MUST contain no
secret source content.

## 16. Testing Requirements

All deterministic tests MUST run offline. External OIDC, webhook, PostgreSQL,
model-provider, and canonical-host behavior MUST use realistic local fakes
unless an explicit integration environment is authorized.

### 16.1 Required test layers

- Core unit and canonical-parser tests.
- Contract-schema generation and drift checks.
- Maintainer service and CLI parity tests.
- Manager service, API, migration, auth, worker, and frontend tests.
- Plugin manifest, MCP, skill, activation, and projection tests.
- Cross-component integration tests.
- Browser-driven primary workflow tests.
- Failure-injection and adversarial tests.

### 16.2 Required integration scenarios

The suite MUST prove:

1. Manager intake reaches the Maintainer service without shell command-name
   coupling.
1. An unambiguous explicit candidate publishes to a temporary project pack.
1. A conflict creates a review and does not replace the last known-good
   decision.
1. Unrelated ready work publishes while a conflict waits.
1. Human resolution creates new explicit evidence and preserves history.
1. The resulting pack is readable by Manager query and Plugin MCP.
1. Plugin projection includes only applicable current explicit constraints.
1. Required missing context produces notice and no canonical write.
1. Optional and undetermined missing context do not interfere.
1. Disabled context remains silent.
1. Legacy flat-pack data resolves as one logical pack.
1. Generated Plugin shared code matches its authoritative Core source.

### 16.3 Negative and recovery cases

Tests MUST cover:

- malformed and unknown schemas;
- unsupported schema family versions;
- secret leakage attempts;
- duplicate and replayed idempotency keys;
- dirty canonical worktrees;
- symlink and path escapes;
- concurrent reviewers and publishers;
- lease expiry and process restart;
- provider timeout, malformed output, and over-budget response;
- notification outage;
- write failure at every publication stage;
- rollback failure with named restored and unrestored paths;
- stale, missing, locally edited, and malformed Plugin projections;
- required-context source absence and ambiguity;
- direct attempts by Plugin code to open canonical files for write; and
- incompatible generated Plugin runtime code.

### 16.4 Sanity checks

Critical tests SHOULD include a mutation or equivalent sanity check showing
that plausible breakage would cause them to fail.

### 16.5 Autonomy and agent-cost fixtures

The suite MUST include a reproducible 100-item baseline fixture containing:

- at least 70 deterministic-only items;
- at least 20 semantic items completed from cache with no new provider call;
- at least 5 policy-required human conflicts excluded from the denominator;
  and
- at least 5 invalid or budget-exhausted eligible items.

For the minimum distribution above, the expected numerator is `90`, the
denominator is `95`, the exclusion count is `5`, and the autonomy rate is
`90 / 95`. This fixture validates honest calculation and MUST NOT be presented
as achieving the 95% target.

The suite MUST also include a 100-item target-achievement fixture containing
70 deterministic successes, 20 cache-only semantic successes, 5 successful
bounded semantic-agent items, and 5 policy-required human items excluded from
the denominator. Its expected numerator and denominator are both `95`.
Deterministic and cache-only items MUST consume zero new agent tokens, and no
more than the 5 bounded semantic items may invoke a provider.

Negative variants MUST prove that:

- human submission and intake do not remove an otherwise autonomous item from
  the numerator;
- a qualifying post-intake human intervention followed by success removes that
  item from the numerator;
- an agent call added to deterministic or cache-hit work makes the
  inappropriate-invocation assertion fail;
- eligibility remains pinned to the policy revision at `intake-accepted`, and
  metrics segment items by that revision;
- retries do not inflate unique-item agent-use rates;
- unresolved and carried-in stalled items remain in the denominator;
- a missing sequence, late heartbeat, collector outage, missing intake policy
  revision, or reconciliation failure makes telemetry incomplete;
- incomplete telemetry prevents a target-achieved claim; and
- the rolling metric is reproducible from persisted events.

## 17. Build and Developer Interface

Python dependencies MUST be managed with Poetry. Frontend dependencies MUST be
locked. The root `Makefile` MUST provide at least:

```text
make install
make test
make lint
make check
make contracts
make contracts-check
make plugin-build
make plugin-check
make ui-build
make e2e
make smoke
make package
```

`make test` MUST resolve pinned dependencies and run the full deterministic
suite. `make check` MUST run lint, full tests, generated-artifact drift checks,
frontend production build, OpenAPI drift, Plugin packaging checks, and
`git diff --check`.

Commands MUST fail non-zero on failure.

## 18. Source-Repository Migration

### 18.1 Inputs

The implementation MUST inspect and refactor from:

```text
../context-library-tool
../context-library-manager
../context-library-plugin
<canonical-library-root>
```

The first three are code sources. The fourth is read-only canonical data and
test input, not source to copy into the monorepo.

### 18.2 Preservation

Migration MUST:

- record the exact source commit of every clean code repository;
- record and preserve pre-existing uncommitted source changes;
- never reset, clean, delete, or rewrite a source repository;
- preserve copyright, notices, documentation, and behavior;
- identify deliberate omissions;
- preserve test intent while consolidating duplicate tests;
- document source-to-destination paths in `MIGRATION.md`; and
- retain commit provenance where practical without making history
  preservation a prerequisite for correct code.

The known dirty Plugin worktree MUST be treated as user-owned input. The
implementation agent MUST inspect it and obtain direction before importing or
discarding any uncommitted content whose disposition is not already explicit.

### 18.3 Refactoring order

Implementation SHOULD proceed in this order:

1. establish root tooling and shared contracts;
1. import the canonical parser and read models;
1. import Maintainer state and service behavior;
1. make `clm` a thin service adapter;
1. import Manager persistence and application behavior;
1. replace Manager subprocess coupling with the typed service;
1. import frontend and browser tests;
1. import Plugin packaging and projection;
1. generate the Plugin read-only runtime subset from Core;
1. implement context-requirement and notice behavior;
1. add integrated packaging, CI, and release versioning;
1. run migration, integration, and adversarial validation; and
1. update release documentation.

Each phase MUST leave the relevant tests passing before the next phase starts.

## 19. Adversarial Review

At least two independent adversarial review cycles MUST occur:

1. after shared contracts and the initial migration are integrated; and
1. after all acceptance checks pass.

Each cycle MUST inspect:

- authority and provenance laundering;
- Plugin canonical write capability;
- required-context false positives and task interference;
- contract and schema drift;
- duplicate parser behavior;
- path, symlink, and transaction safety;
- authentication and cross-project authorization;
- secret leakage;
- idempotency and concurrency;
- cancellation, budget, notification, and restart recovery;
- migration loss or unintentional behavior change; and
- test adequacy, including vacuous assertions.

Reviewers MUST be read-only during the review. Findings MUST be recorded under
`reviews/` with severity, evidence, reproduction, affected requirement, and
recommended correction.

Every confirmed Critical, High, and Medium finding MUST be fixed or explicitly
accepted by the user. After fixes, a reviewer that did not implement the fix
MUST verify closure. The final state MUST contain no unaccepted Critical,
High, or Medium finding.

One reviewer in each cycle MUST be Claude Code using the latest available
Claude Opus model at `xhigh` effort, or a user-approved stronger replacement.
The invocation MUST use read-only or plan permissions and MUST NOT use
permission bypass. The exact Claude version, resolved model, effort, prompt,
and output MUST be recorded.

If a required Claude review cannot complete because the available Claude token
or usage quota is exhausted, the cycle MUST use the Codex adversarial review
fallback instead. Token exhaustion MUST be recorded with the safe error or
quota evidence; secrets and credentials MUST NOT be recorded.

The Codex fallback for each unavailable Claude review MUST:

- use at least two fresh, independent, read-only Codex reviewer agents;
- prevent the primary implementer and fix authors from counting as reviewers;
- divide and then cover the complete review rubric in this section;
- require exact file evidence, reproduction, violated requirement, severity,
  and smallest correction for every finding;
- normalize and deduplicate findings under `reviews/`;
- reproduce findings locally before treating them as confirmed; and
- use a fresh reviewer that did not implement a fix to verify closure.

The Codex fallback is equivalent acceptance evidence only for Claude token or
usage-quota exhaustion. It MUST meet the same completion gate of no unaccepted
Critical, High, or Medium findings and MUST NOT be reduced to a single
implementing agent's self-review.

## 20. Documentation Requirements

The monorepo MUST maintain:

- `README.md`: installation, development, operation, and component overview;
- `ARCHITECTURE.md`: dependency and authority boundaries;
- `AGENTS.md`: concise repository instructions and Context Library projection;
- `MIGRATION.md`: source commits and source-to-destination mapping;
- `CHANGELOG.md`: product releases and migrations;
- API and CLI reference generated from code where practical;
- `contracts/HARVESTER_LIBRARY_V1.md`: private harvester integration boundary;
- Plugin missing-context and management-remediation guidance; and
- production deployment and recovery procedures.

Normal-agent instructions, administrative Maintainer instructions, and Plugin
read-side instructions MUST be separate, plainly labeled, and consistent with
Section 4.

## 21. Acceptance Criteria

The monorepo is accepted only when:

1. Tool, Manager, and Plugin report one strict semantic version.
1. No canonical decision content is committed in the monorepo.
1. Core supplies the only authoritative canonical parser.
1. Generated Plugin parser/runtime code passes a non-mutating drift check.
1. Manager calls a typed Maintainer service rather than CLI implementation
   aliases.
1. `clm` exposes only documented canonical command names to internal
   consumers and emits validated versioned envelopes.
1. `clm version` and `clm capabilities` expose compatibility information.
1. The live canonical checkout is never modified by deterministic tests.
1. Legacy flat-pack data appears as one logical pack.
1. Plugin MCP tools are read-only by construction and test.
1. Plugin guidance never tells an agent to create or update a canonical pack.
1. Explicitly required missing context produces a safe user-visible notice.
1. Optional or undetermined missing context does not interfere with the task.
1. Disabled context remains silent.
1. Canonical changes are available through the Manager with review, audit,
   idempotency, provenance, and publication safety.
1. Direct `clm` mutation is documented as an administrative path, not normal
   Plugin authority.
1. The 100-item baseline fixture reports `90/95`, five exclusions, zero new
   tokens for deterministic and cache-only work, and does not claim the target
   is achieved.
1. The 100-item target-achievement fixture reports `95/95`, five exclusions,
   and no provider calls outside its five bounded semantic items.
1. Rolling autonomy telemetry is reproducible from persisted event lineage,
   exposes the evidence behind the 95% figure, and refuses an achieved claim
   when telemetry is incomplete.
1. Human intake does not count as intervention, while every qualifying
   post-intake human action does.
1. Eligibility is pinned to the policy revision effective at
   `intake-accepted`, retained for carried-in work, and segmented by revision.
1. Unresolved eligible work, including work carried in from before the rolling
   window, remains in the autonomy denominator.
1. Contiguous event sequences, producer heartbeats and watermarks, complete
   item lineage, and successful replay reconciliation prove telemetry
   completeness; every detected gap prevents an SLO result.
1. SLO output reports separate history and telemetry status and applies the
   deterministic precedence in Section 11.5.
1. Release acceptance requires the metric implementation and fixtures, not an
   observed 30-day production result; `history_status` remains
   `insufficient-history` until a complete 30-day window exists while
   `slo_state` follows Section 11.5 precedence.
1. The inappropriate agent-invocation count is zero.
1. Source-repository commits and user-owned dirty changes are accounted for in
   `MIGRATION.md`.
1. Root `make test`, `make check`, `make e2e`, and `make smoke` pass offline
   except for explicitly documented local service fakes.
1. The full integration scenarios in Section 16.2 pass.
1. Two adversarial review cycles are complete, with each cycle including
   either a Claude Opus `xhigh` review or the required Codex fallback when
   Claude token or usage quota was exhausted.
1. No unaccepted Critical, High, or Medium review finding remains.
1. The final worktree is clean except for explicitly documented generated or
   release artifacts.

## 22. Completion Definition

Implementation is complete when the acceptance criteria pass and a fresh
agent can:

- install the monorepo;
- query the separate canonical library;
- run the Manager locally;
- exercise the primary browser workflow;
- use `clm` for an explicitly authorized administrative workflow;
- install the Plugin;
- observe correct required/optional/disabled missing-context behavior;
- prove the Plugin cannot write canonical data;
- run all validation from the root Makefile; and
- explain every source repository's migration disposition.

The work MUST NOT be declared complete merely because files were copied into
one directory or existing tests still pass independently. Completion requires
one contract system, one release, integrated behavior, explicit authority
boundaries, and adversarially reviewed recovery and security.
