# Issue #49 specification review artifact: full-system offline E2E

Issue: https://github.com/Vcinity/context-library/issues/49  
Authority: `SPEC.md` sections 7, 12, 13, 14, 16.1–16.4, 16.2, 17, 21, and 22.  
Phase: specification review; no implementation is authorized by this artifact alone.

## Scope and non-goals

This issue adds one deterministic `make e2e` acceptance scenario that runs
against temporary synthetic repositories, a temporary Manager database, a
staged Plugin artifact, and local fakes for external boundaries. It is a
black-box orchestration test, not a second production runtime or a replacement
for focused tests. It must exercise the complete issue-required path and emit
machine-readable evidence.

It does not use the live canonical checkout, `sw-build`, external OIDC,
PostgreSQL, model providers, webhooks, network services, or real credentials.
It does not copy canonical decision records into the repository. It does not
weaken authority boundaries or make the Plugin write-capable.

## Requirements and observable contract

The harness must:

1. Build or validate Core, `clm`, Manager, the Plugin manifest/generated
   runtime, and MCP `serverInfo`, and fail on any product-version mismatch.
2. Stage the Plugin into a temporary destination with an explicit marketplace
   identifier and runtime configuration. It must assert source isolation,
   searchable/readable files, absence of secrets and canonical content, and
   canonical-root read-only permissions.
3. Start the staged MCP as a fresh subprocess. It must exercise newline JSON
   and `Content-Length` framing, protocol negotiation, restart, every
   advertised tool, valid responses, invalid requests, and structured errors.
   The advertised tool set is discovered from `tools/list`; omission,
   unexpected process exit, ignored error, or unclassified failure is fatal.
4. Exercise missing, unreadable, malformed, and incompatible runtime
   configuration and prove none is reported healthy. A configured status,
   pack list, artifact read, search, task-context response (including truthful
   budget/truncation), and audit response must be concrete.
5. Drive the Manager’s local-authenticated HTTP/browser boundary through
   project selection, CSRF/session handling, source intake, normalized
   observation, proposal, worker execution, review, publication, query, audit,
   and visible validation/error state. Browser assertions use the existing
   deterministic local frontend and a local browser runner.
6. Verify the published synthetic decision through Core parsing, Manager
   query, staged MCP, consumer projection, projection check, and a second
   idempotent sync.
7. Cover required, optional, undetermined, and disabled applicability,
   including missing and ambiguous context, with their observable notices and
   write behavior.
8. Exercise `clm` version/capabilities/query and explicitly authorized
   mutation boundaries. Prove Manager publication reaches the typed
   Maintainer service and the Plugin cannot mutate canonical data.
9. Inject malformed input, duplicate idempotency, provider timeout,
   publication-stage failure, notification failure, lease expiry, and process
   restart. Assert classification, recovery, no duplicate/lost publication,
   and last-known-good preservation.
10. Attempt MCP/projection traversal, symlink escape, canonical writes, and
    unauthorized consumer writes. Assert rejection and unchanged canonical Git
    digest.

The result is JSON with `schema`, `schema_version`, `product_version`, a
synthetic `fixture_revision`, ordered `phases`, per-phase status/failure
classes/evidence, MCP tool coverage, restart/configuration outcomes, and
`canonical_digest_before`/`canonical_digest_after`. The process exits non-zero
for any failed assertion, ignored MCP error, unclassified subprocess exit, or
digest change.

## Proposed design

Add a single orchestration entry point under `scripts/` and invoke it from the
existing `make e2e` target after the frontend build. Keep protocol driving in
the harness at the subprocess boundary; reuse the packaged Plugin installer,
generated runtime, Core parser, Manager TestClient/HTTP routes, typed
Maintainer service, and projection APIs rather than private implementation
shortcuts. Use a temporary directory with separate `canonical`, `state`,
`consumer`, `plugin`, and `evidence` roots. Initialize the canonical fixture
as a clean synthetic Git repository and record its digest before every
mutation/security phase.

Use deterministic local fakes for OIDC/session identity, agent/provider
responses, notification delivery, and failure injection. Give each phase a
stable name and failure class. The MCP driver must validate JSON-RPC response
IDs, error envelopes, framing, process exit status, and tool coverage; it may
not swallow stderr or treat an absent response as success. Configuration
failure cases should run in isolated staged copies so the valid fixture remains
available for later phases.

The Manager/UI slice should extend the existing smoke workflow with a
browser-visible local-auth and project-selection path, while the HTTP path
retains exact CSRF and idempotency assertions. The recovery phase should use
the existing worker/process seams and local fakes, and record last-known-good
register content plus publication lineage. Applicability and security phases
must use synthetic policy files and symlink/traversal fixtures only.

## Affected components

- `Makefile` (`e2e` target)
- new `scripts/` E2E driver and result contract/fixture as needed
- existing Plugin packaging/MCP and projection boundaries
- existing Manager HTTP/frontend test boundary and deterministic fixtures
- E2E documentation and changelog
- new `reviews/issue-49-full-system-e2e-review.md`

No canonical-data repository or deployment checkout is affected.

## Test and validation strategy

Focused validation will run the new harness directly and with `make e2e`,
including a deliberate mutation/sanity check for ignored MCP errors or
canonical digest changes. Existing `scripts/smoke_context_library.py`, Plugin
checks, Manager workflow tests, contract checks, and frontend tests remain
regression evidence. The root gates required for closure are `make test`,
`make check`, `make e2e`, `make smoke`, `make contracts-check`,
`make plugin-check`, `make package`, and `git diff --check`.

## Risks and unresolved questions

- The current `make e2e` target assumes `@playwright/test`; the implementation
  must either make the locked dependency available or report a deterministic
  repository configuration failure rather than silently omitting browser
  coverage.
- Some existing recovery seams are asynchronous. The harness must use bounded
  deterministic polling or direct local fakes, never sleeps that depend on
  deployment timing.
- If a required public boundary cannot express an issue acceptance criterion,
  the contract or SPEC must be updated before implementation; the harness must
  not replace it with private-state assertions.

## Acceptance evidence

The preserved review artifact must record an independent fresh Claude Code
review covering scope control, contract correctness, test adequacy, authority
boundaries, and all risks above. Implementation begins only after that review
passes and all Critical/High findings are resolved.
