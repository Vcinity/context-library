# Specification checkpoint: #4 / 30-day production autonomy evidence

Status: specification-only checkpoint
Originating issue: #4 — Establish complete 30-day production autonomy evidence
Authority: SPEC.md §11.5 and the existing Manager telemetry/event lineage implementation

## Scope and non-goals

Establish an inspectable production measurement window proving whether the
post-intake autonomy SLO is met. Reconcile the deployed producer manifest,
durable event sequences, heartbeats, watermarks, cohort membership, item
lineage, and replayed materialized state for one continuous rolling 30-day
window. The cohort consists of items intake-accepted in the window plus items
created before the window that were unresolved at its start; items resolved
before the window began are excluded. Publish a report and dashboard/API
evidence that keeps history, telemetry, and SLO states separate and names
every coverage gap.

This issue does not represent fixtures or short windows as production evidence,
backfill missing events from inference, weaken the 60-second heartbeat or
sequence requirements, rewrite historical policy classification, or change
the 95% target. If authoritative production evidence is unavailable, the
result remains `insufficient-history` or `insufficient-telemetry` and the
issue cannot be closed as achieved.

## Evidence contract

The evidence bundle MUST identify deployment and manifest revision, window
start/end, required producers, per-project sequence ranges and watermarks,
heartbeat intervals, intake-accepted cohort items, effective policy revision
and eligibility classification, work/review/policy/agent-invocation/notification
event lineage, replay reconciliation result, coverage gaps, and safe redacted
operator provenance. It MUST report numerator, denominator, exclusions,
deterministic-only completion rate, cache-only semantic completion rate,
model-assisted completion rate, agent cache-hit rate, duplicate-work rate,
median and p95 time-to-terminal-outcome, agent invocation reasons/counts,
inappropriate calls, retry/failure and escalation metrics, backlog age,
deferral, tokens-per-item, and cost-per-decision. Missing evidence for any
MUST-report field is itself a named coverage gap and forces
`insufficient-telemetry`; it is never silently omitted.

The dashboard/API MUST preserve SPEC.md precedence:
`insufficient-telemetry` > `insufficient-history` > `no-data` > `met` >
`missed`. A coverage gap overlapping the window prevents an achieved SLO.
The report MUST distinguish synthetic/local validation from production status
and expose producer, interval, reason, and reconciliation state for each gap.

## End-to-end validation

The principal slice is:

```text
durable production event log + manifest -> cohort reconstruction -> sequence /
heartbeat / watermark validation -> replay reconciliation -> telemetry and
30-day history status -> dashboard/API evidence bundle
```

Deterministic local tests use realistic fakes to prove missing intake policy,
sequence gaps, heartbeat lapses, unknown watermarks, collector errors,
unreconciled replay, carried-in unresolved items, policy revisions, human
interventions, retries, inappropriate invocations, complete telemetry, and
the exact SLO precedence. They MUST NOT be described as production evidence.
Production acceptance additionally requires an actual continuous 30-day
window from the deployed producer manifest and durable events. The required
producer set is immutable for the window and MUST NOT be changed retroactively.
A heartbeat after a gap is not evidence that missing events were recovered;
only durable event recovery plus replay reconciliation can close that gap.

## Affected components and validation

- Manager telemetry aggregation/reconciliation and dashboard/API fields;
- durable event/heartbeat/watermark evidence export and redaction;
- production deployment manifest/runbook and operator report; and
- deterministic telemetry tests plus a redacted production evidence bundle.

Run focused telemetry/replay tests, applicable Manager smoke and API checks,
`PYTHONPATH=src make test` or its deterministic equivalent,
`git diff --check`, and
`PYTHONPATH=src python scripts/verify_production_evidence.py --bundle <redacted-evidence-bundle>`.
The verifier is an implementation deliverable and MUST fail closed on missing
manifest, cohort, sequence, heartbeat, watermark, metric, or replay evidence.
No provider or canonical-data mutation is permitted.

## Risks and unresolved questions

Risks are event loss, producer-set drift, clock/sequence ambiguity, replay
divergence, protected evidence leakage, and accidentally claiming production
success from fixtures. The only unresolved operational question is which
deployed producer manifest revision supplies the window; until that external
fact is available, the issue remains incomplete rather than inferred complete.
This checkpoint contains no implementation.
