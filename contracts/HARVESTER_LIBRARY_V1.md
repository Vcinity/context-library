# Harvester/Library Contract v1

## Purpose

`context-library/harvest-batch` is the public, versioned boundary between a
private context harvester and the Context Library Manager/Maintainer system.
It lets the harvester contribute evidence and proposals without gaining
canonical write authority.

The executable model is `HarvestBatch` in Core. The generated JSON Schema is
[`schemas/harvest-batch-v1.json`](schemas/harvest-batch-v1.json).

## Direction and authority

```text
private harvester
        |
        | context-library/harvest-batch v1
        v
Context Library Manager review intake
        |
        v
typed Maintainer service
```

The batch is proposal-only. `canonical_write` is always `false`, candidates
are unreviewed, and the Manager remains responsible for authentication,
policy, conflict review, audit, and publication.

## Envelope

The top-level payload MUST contain:

- `schema`: `context-library/harvest-batch`;
- `schema_version`: `1`;
- stable `batch_id` and `idempotency_key`;
- `project` and `produced_at`;
- `redacted: true`;
- `canonical_write: false`;
- `sources`, `observations`, `candidates`, and `findings` arrays.

The nested entries use the existing versioned Core contracts:

- `source-envelope` for redacted source evidence;
- `observation` for extracted evidence tied to a source. Each emitted
  observation SHOULD include a stable `observation_id`; candidates and
  findings use that ID when citing evidence;
- `candidate` for an unreviewed context proposal;
- `finding` for duplicate, supersession, relationship, or conflict signals.

Within one batch, an observation's `source_id` MUST equal the corresponding
source envelope's `external_id`. Candidate projects MUST equal the batch
project. Findings MUST reference candidates in the same batch.

Empty candidate and finding arrays are valid: a batch may contain source
evidence that yields no reusable context.

## Privacy

Every source envelope MUST be redacted before it enters this contract. The
batch MUST NOT contain credentials, tokens, private contact details, tenant
configuration, or unredacted source text. Deployment-specific metadata belongs
outside the canonical Context Library contract.

The contract does not authorize the harvester to resolve contacts, notify
people, publish decisions, or edit canonical files. A harvester may preserve a
role or source identity in proposal evidence for Manager-side resolution.

## Idempotency and replay

`batch_id` identifies the logical source batch. `idempotency_key` identifies
the exact proposal submission. Replaying the same key MUST be safe and MUST
not create duplicate source, observation, candidate, or finding records.

Changing source content, evidence, or proposal meaning requires a new
idempotency key while retaining the source's stable external identity.

## Compatibility

Consumers MUST reject an unknown schema family or unsupported major contract
version. Additive optional fields require a new minor compatibility policy;
changes to required fields, authority semantics, provenance, or nested schema
families require a new contract version.

The private harvester may use its own internal input and review formats, but
the Manager-facing integration MUST produce this contract before submission.

## Synthetic example

```json
{
  "schema": "context-library/harvest-batch",
  "schema_version": 1,
  "batch_id": "batch-synthetic-001",
  "idempotency_key": "harvest-synthetic-001",
  "project": "synthetic-project",
  "produced_at": "2026-08-03T12:00:00Z",
  "redacted": true,
  "canonical_write": false,
  "sources": [
    {
      "schema": "context-library/source-envelope",
      "schema_version": 1,
      "external_id": "thread-synthetic-001",
      "source_type": "chat",
      "uri": "https://example.invalid/thread-synthetic-001",
      "title": "Synthetic discussion",
      "retrieved_at": "2026-08-03T12:00:00Z",
      "content_format": "text",
      "content": "Use the narrow interface."
    }
  ],
  "observations": [
    {
      "schema": "context-library/observation",
      "schema_version": 1,
      "source_id": "thread-synthetic-001",
      "kind": "directive",
      "excerpt": "Use the narrow interface.",
      "location": "message-1",
      "agent_interpretation": "Synthetic directive."
    }
  ],
  "candidates": [],
  "findings": []
}
```
