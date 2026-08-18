# Issue #47 specification review: restart-safe health aggregation

## Scope

Change Manager runtime health aggregation so replaced producer instances do
not remain active health inputs after restart. For each expected producer role,
the aggregate will evaluate the newest observed heartbeat instance; a newly
started healthy instance supersedes an older offline instance for that role.
The response will retain the selected instance identity and observation time
for diagnosis. Add a black-box HTTP regression covering a stale pre-restart
instance and a fresh post-restart instance.

## Non-goals

- Do not delete heartbeat history or mutate operator state as a side effect of
  reading health.
- Do not weaken freshness thresholds or treat an actually stale newest
  instance as healthy.
- Do not change telemetry completeness, durable event lineage, or canonical
  data behavior.
- Do not change unrelated project health, UI, or deployment contracts except
  where they consume the corrected aggregate.

## Applicable requirements

- Issue #47: active producer instances determine aggregate status after
  restart; stale replaced instances must not force `offline`.
- SPEC §15: diagnostics must explain runtime health without becoming canonical
  audit records.
- SPEC §16: deterministic offline tests use local synthetic stores and stable
  HTTP boundaries.
- AGENTS.md: Manager changes require focused black-box tests and the root
  validation surface.

## Proposed contract and design

Change `runtime_health_data()` to select the newest heartbeat row per producer
process before applying `heartbeat_health()`. The selected row remains
visible with its actual `instance_id`, `observed_at`, and freshness state.
Missing expected processes still produce the existing `not-observed` offline
entry. If the newest instance is stale, the role remains degraded/offline;
the fix only removes superseded historical instances from the active
aggregation.

Use a portable SQL window query supported by the repository's SQLite and
PostgreSQL targets. Preserve read-only behavior and existing public redaction.

## Affected files/components

- `src/context_library_manager/api.py`
- `tests/manager/test_runtime_observability.py` or `tests/manager/test_runtime.py`
- `docs/DEPLOYMENT.md` if the operational health explanation needs updating

## Test strategy and commands

- Seed an offline old instance and a fresh healthy replacement for the same
  process, call `GET /api/v1/health`, and assert aggregate `healthy` plus the
  replacement `instance_id`.
- Assert a genuinely stale newest instance remains non-healthy.
- Assert existing missing-process and public-redaction behavior remains.
- Run focused Manager tests, then `make test`, `make check`, `make e2e`,
  `make smoke`, `make contracts-check`, `make plugin-check`, `make package`,
  and `git diff --check` as applicable.

## Risks

- If multiple active replicas exist for one process role, selecting only the
  newest row could hide a separate unhealthy replica. The current issue and
  observed deployment use one effective producer per role; preserve selected
  identity in the response and add a follow-up if replica-aware health is
  required by deployment evidence.
- SQL portability must be checked against the supported SQLite and PostgreSQL
  dialects; use a window function rather than engine-specific syntax.

## Unresolved questions

None for the bounded single-effective-instance contract. Replica-aware
aggregation remains outside this issue unless current deployment evidence
shows multiple active instances per role.
