# Context Library Monorepo Agent Instructions

## Scope and authority

- These instructions apply to the Context Library code monorepo.
- Read `SPEC.md` before changing implementation files. Consult
  `ARCHITECTURE.md`, `README.md`, and the relevant package or test
  documentation when the change crosses a component boundary.
- Treat `SPEC.md` as the implementation authority when source repositories
  disagree.
- Do not apply an unrelated product project pack to this monorepo.
- No applicable canonical Context Library project pack exists for this
  monorepo at present. Do not infer one from the presence of the Plugin or the
  availability of a shared library.

## Roadmap and ticket execution

- GitHub organization Project 1 for this repository's owner is authoritative
  for planned work and sequencing. GitHub issues are the
  units of implementation scope; `SPEC.md` remains authoritative for product
  behavior and acceptance rules.
- Before starting a roadmap issue, read its dependencies and do not implement
  a blocked downstream contract unless the issue or user explicitly changes
  the sequence.
- For long-running roadmap orchestration, follow
  `docs/GITHUB_ORCHESTRATION.md`: the orchestrator owns serialized broker
  access, and subagents consume local snapshots instead of querying GitHub.
- Treat an issue's goal, acceptance criteria, implementation orientation, and
  named specification sections as one bounded work package. Inspect the named
  source and test patterns before adding new structure.
- Spec-driven planning and orchestration tools such as BMAD or GitHub Spec Kit
  MAY refine an issue into a plan and task list. Generated artifacts MUST link
  the originating issue, remain within its scope, and MUST NOT override or
  silently broaden `SPEC.md`, these instructions, or an upstream contract.
- If implementation reveals that accepted behavior or a shared contract must
  change, update `SPEC.md` first. If the change materially expands or
  contradicts the issue, stop and obtain confirmation before implementing it.
- Close an issue only with the validation evidence requested by that issue and
  the applicable root checks. Record intentionally omitted checks and why.

### Autonomous specification quality gate

- Roadmap implementation MUST use two distinct phases for each issue:
  specification review, then implementation. Selecting an issue authorizes
  read-only discovery and creation of its review artifacts only.
- Before changing implementation code, tests, dependencies, generated
  contracts, migrations, or runtime configuration, the orchestrator MUST
  produce inspectable specification artifacts linked from the issue. At
  minimum they describe scope and non-goals, applicable requirements, proposed
  contracts and design, affected files or components, test strategy,
  validation commands, risks, and unresolved questions.
- Specification artifacts MUST be preserved in a user-reviewable location,
  such as a dedicated planning commit or pull request, and MUST comply with
  public-repository and canonical-data boundaries. Ephemeral agent context is
  not sufficient review evidence.
- Before implementation, a fresh read-only Claude Code session MUST assess the
  preserved specification for scope control, contract correctness, test
  adequacy, authority boundaries, and unresolved risks. Invoke `claude` in
  non-interactive plan mode; never use permission bypass for review.
- Use the cheapest currently available Claude model that produces an
  acceptable review efficiently. Select `haiku` at `medium` effort for a
  narrow, low-risk specification with stable contracts; select `sonnet` at
  `medium` for normal code review or any shared-contract, cross-component,
  authority, security, or end-to-end concern. A review is acceptable only when
  it explicitly covers every required dimension, gives a PASS or actionable
  findings, and supports each finding with severity, exact repository
  evidence, violated authority or criterion, impact, and the smallest
  correction. Retry once with a tighter prompt for a format-only failure;
  escalate effort or model only for deficient reasoning or coverage. Record
  the selection rationale, resolved model, effort, attempts, and escalation
  reason.
- The orchestrator MUST verify Claude's cited evidence, resolve all Critical
  and High findings, and record the review evidence. Claude unavailability,
  authentication failure, or exhausted quota is a genuine external blocker;
  do not silently substitute a Codex self-review.
- After the independent review passes, the orchestrator MAY set the GitHub
  Project `Spec Gate` field to `Approved`, post a signed evidence summary, and
  continue directly into implementation without waiting for human action.
  `Approved` records an autonomous quality checkpoint; it does not claim human
  endorsement.
- Material changes to the accepted scope, contract, design, or test strategy
  reset the gate to `Drafting` and require refreshed artifacts and independent
  review. Explicit human feedback or `Changes requested` always takes
  precedence and MUST be resolved before implementation continues.
- Stop for human direction only when applicable authorities conflict, scope
  would materially expand, a consequential choice has no defensible answer in
  the issue or repository, required credentials or permissions are missing,
  or an action exceeds the granted safety boundary.

### Roadmap testing strategy

- Treat black-box, behavior-first testing through stable boundaries as the
  default for roadmap work. Prefer the root CLI, MCP protocol, Manager HTTP
  API, generated artifacts, files, reports, logs, exit status, and other
  consumer-visible behavior over assertions against private helpers or
  internal object structure.
- End-to-end testing is a principal delivery goal, not final-ticket cleanup.
  Every approved specification MUST name the end-to-end slice added or
  extended by that issue, and implementation MUST keep that slice executable
  as the feature grows.
- Use focused unit tests where they materially improve deterministic edge-case
  coverage or diagnosis, but do not accept unit-only evidence when the
  behavior can be exercised through a stable public boundary.
- Retrieval-roadmap end-to-end coverage MUST grow through these boundaries:
  benchmark fixture or generator to baseline execution and report; shared
  applicability fixtures through every consumer; and task request through the
  packaged read-only Plugin MCP process to the rendered capsule and on-demand
  audit record.
- End-to-end tests MUST remain deterministic and offline. Use realistic local
  fakes for provider or host boundaries, assert concrete outputs and failure
  states, and preserve the canonical-data read-only rule.

## Context Manager suite isolation

- When working specifically on the Context Manager suite—including
  `src/context_library_manager`, `frontend`, Manager tests, and Manager
  end-to-end flows—do not invoke, install, load, or otherwise use the Context
  Manager plugin.
- Do not use that plugin's generated projection, MCP server, or session-start
  behavior as implementation input or test infrastructure for Context Manager
  work. Use the checked-in source, contracts, fixtures, and deterministic
  local test doubles instead.
- This restriction is scoped to Context Manager suite work; it does not
  prohibit changes to the separately defined, read-only Context Library
  Plugin when the task explicitly targets that component.

## Canonical-data boundary

- Keep canonical Context Library data in its separately governed repository.
- Treat the configured canonical library root as read-only unless the user
  separately authorizes a canonical-data change.
- Never copy canonical decision records into this code monorepo.
- Use temporary copies or synthetic fixtures for mutation tests.

## Implementation boundary

- Preserve source-repository history and user-owned dirty changes.
- Keep the Plugin incapable of canonical writes.
- Route normal canonical mutations through Manager policy and the typed
  Maintainer service.
- Keep `clm` as the documented administrative adapter.
- Preserve the dependency direction: Core is inward, Maintainer depends on
  Core, Manager depends on the Maintainer service, and the frontend depends
  on the Manager API.

## Change and validation requirements

- Keep changes localized, preserve user-owned dirty work, and do not modify
  the separately governed canonical library unless the user explicitly
  authorizes it.
- Use synthetic repositories or temporary copies for mutation and recovery
  tests. Never commit canonical decision records to this code repository.

## Public-repository hygiene

- Treat this repository as public source. Do not commit company names,
  customer names, internal project names, private URLs, email addresses,
  chat transcripts, issue contents, credentials, tokens, or other
  organization-specific details.
- Use synthetic fixtures, generic examples, and redacted metadata for tests,
  documentation, demonstrations, and examples.
- Keep deployment-specific connectors, tenant identifiers, contact data, and
  harvested source content outside the repository unless explicitly sanitized
  and authorized for publication.
- Before committing a change, scan new or modified files for accidental
  private data and report any uncertainty instead of guessing.
- For code changes, run the narrowest relevant tests first, then run the root
  validation required by the specification: `make test`, `make check`,
  `make e2e`, `make smoke`, `make contracts-check`, `make plugin-check`,
  `make package`, and `git diff --check` as applicable.
- Report commands that were run, their results, and any omitted validation or
  external integration limits.
