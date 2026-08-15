# Specification checkpoint: #3 / reviewed publication authorization

Status: specification-only checkpoint
Originating issue: #3 — Separate reviewed publication authorization from automatic publication
Dependencies: #1 multi-project Manager support, merged; current Maintainer and Manager publication paths

## Scope and non-goals

Add an explicit, capability-checked publication authorization bound to one
review and exact candidate set. Permit the Manager to publish an approved
candidate while the project’s `automatic_publication` policy remains disabled.
Keep automatic workers restricted to their own policy gate and prevent any
authorization replay or broadening across candidates, projects, actors, policy
revisions, or idempotency keys.

This issue does not add Plugin or CLI publication authority, bypass review,
change canonical record ownership, enable automatic publication implicitly, or
alter unrelated proposal/reconciliation behavior.

## Contract and authority design

The typed Maintainer service accepts a versioned publication authorization
request with `schema: context-library/publication-authorization` and
`schema_version: 1`. The envelope contains project, exact candidate IDs and
their candidate digests, review ID and approved review revision, the approving
actor's identity and the named `publication:authorize` capability, policy
revision, expiration/replay identity, and idempotency key. It rejects missing,
stale, denied, revoked,
cross-project, mismatched, expired, reused, or broadened authorizations before
any canonical write. The Manager creates the request only after an audited
approval and binds the exact values; the Maintainer records the authorization
lineage in the publication audit. Automatic workers cannot consume this human
authorization. A review revoked before first use is rejected and leaves
canonical bytes unchanged.

An authorization is stale if the project's current policy revision differs from
the revision recorded at approval time, independent of the
`automatic_publication` value itself.

Publication remains atomic, idempotent for the exact request, and scoped to the
owning project. Retry/recovery reuses only the exact authorization lineage and
never requires broadening its candidate set. `automatic_publication` remains
false before, during, and after an explicitly authorized publication. Each
mutating audit record includes actor, project, operation, input identity,
policy revision, result, affected records, and a safe error classification;
rejections record the same fields without secret evidence. The authorization
approval and resulting publication events are linked into the Manager's
autonomy/human-intervention event lineage with the approved item IDs and
review identity; any item published through this path is classified as having
human intervention and is excluded from the autonomous numerator.

The Manager authorization endpoint preserves the existing authenticated
session boundary, CSRF protection, and browser/M2M credential separation. A
break-glass `clm publish` operation must honor the same authorization and
publication gates; if it publishes a candidate with a pending Manager
authorization, it consumes or invalidates that authorization atomically and
records the administrative actor in the same lineage. It may not race to
reuse or duplicate the envelope.

## Affected components

- typed Maintainer publication contracts, service, policy, and audit records;
- Manager approval/publication API and capability binding;
- worker/automatic-publication guards;
- CLI and Plugin negative-authority tests; and
- synthetic integration fixtures for approval, denial, replay, concurrency,
  stale authorization, retry, and recovery.

## End-to-end validation

The principal slice is:

```text
candidate -> Manager review approval -> exact authorization envelope ->
Maintainer publication -> audit/recovery record
```

Positive coverage proves approved publication succeeds with automatic
publication disabled and exact retry is idempotent. Negative coverage proves
denial, stale review/policy, changed candidate digest, changed project, changed
actor/capability, replay, broadened candidate set, concurrent ready candidates,
automatic worker reuse, CLI, and Plugin write attempts fail without canonical
mutation. Recovery proves the authorization lineage remains exact after a
partial publication failure.

## Validation commands, risks, and unresolved questions

Run focused Manager/Maintainer integration and recovery tests, the relevant
CLI/Plugin read-only tests, `PYTHONPATH=src make contracts-check`, `make test`
or the narrow deterministic equivalent, `git diff --check`, and applicable
smoke checks. Tests use synthetic repositories and offline local fakes only.

Risks are authorization replay, candidate-set widening, accidental policy
toggle, and audit lineage loss during recovery. Every rejection must be
observable with a safe error classification and must leave canonical bytes
unchanged. The implementation must preserve the named schema and capability
as stable contract identifiers; token/credential handling follows existing
redaction rules. This checkpoint contains no code.
