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

- The public [Context Library Roadmap](https://github.com/orgs/Vcinity/projects/1)
  is authoritative for planned work and sequencing. GitHub issues are the
  units of implementation scope; `SPEC.md` remains authoritative for product
  behavior and acceptance rules.
- Before starting a roadmap issue, read its dependencies and do not implement
  a blocked downstream contract unless the issue or user explicitly changes
  the sequence.
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

### Human specification approval gate

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
- After producing the artifacts, set the GitHub Project `Spec Gate` field to
  `Awaiting approval`, report the review location, and stop. Only explicit
  human approval may set the gate to `Approved`; an agent MUST NOT approve its
  own specification or infer approval from silence.
- Implementation may begin only when the issue's `Spec Gate` is `Approved`.
  Material changes to the approved scope, contract, or design reset the gate
  to `Awaiting approval` and require another human review before work resumes.

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
