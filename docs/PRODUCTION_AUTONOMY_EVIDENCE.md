# Production autonomy evidence runbook

This runbook describes how an operator produces the redacted evidence bundle
for the 30-day autonomy SLO. It does not turn local fixtures, short windows,
elapsed time, comments, commits, or dashboard activity into production
evidence.

## Required window

Use one continuous UTC window of at least 30 days. The cohort is:

- items accepted by intake during the window; and
- items accepted before the window that were unresolved at the window start.

Items resolved before the window began are excluded. Preserve the deployed
manifest revision and required producer set for the entire window; do not
change it retroactively.

## Collect and verify

1. Export the Manager read-only evidence endpoint for the project:

   ```text
   GET /api/v1/projects/{project}/autonomy/evidence
   ```

1. Store the response as a protected working artifact. Redact operator
   identifiers and evidence content according to the repository's existing
   redaction policy. Never commit raw production events, credentials, or
   customer data.

1. Verify the redacted bundle locally:

   ```text
   PYTHONPATH=src python scripts/verify_production_evidence.py \
     --bundle <redacted-evidence-bundle.json>
   ```

The verifier fails closed unless the bundle contains the immutable manifest,
30-day window, exact cohort rule, sequence ranges, watermarks, heartbeat
evidence, named coverage gaps, replay status, all required metrics, policy
revision segments, and the SLO invariants. A complete telemetry window with a
zero denominator reports `no-data`, never `met`; `met` also requires a rate of
at least 95% and zero inappropriate invocations.

## Interpreting the result

The precedence is:

```text
insufficient-telemetry > insufficient-history > no-data > met > missed
```

Any missing sequence, heartbeat, watermark, manifest, policy lineage, replay
reconciliation, or required metric must be named in `coverage_gaps` and keeps
the result at `insufficient-telemetry`. A heartbeat after a gap is not proof
that missing events were recovered.

The issue may be closed only after an authoritative, redacted production
bundle passes verification and its recorded window is genuinely complete.
Synthetic fixtures and local test output remain validation evidence, not
production evidence.
