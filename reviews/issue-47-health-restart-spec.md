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
- SPEC §15 and §20: operational diagnostics and documentation must explain
  runtime health without becoming canonical audit records.
- SPEC §16: deterministic offline tests use local synthetic stores and stable
  HTTP boundaries.
- AGENTS.md: Manager changes require focused black-box tests and the root
  validation surface.

## Proposed contract and design

Change process startup heartbeat identity and shared heartbeat selection logic
so `runtime_health_data()` and `agent_service_summary()` aggregate service
health per producer role
across the documented multi-replica topology. Select exactly one newest row by
`observed_at` per process using a portable bounded SQL window query, then apply
the existing `heartbeat_health()` helper once to that row. A fresh post-restart
instance therefore supersedes stale historical rows, while another healthy
replica can continue to represent an otherwise available role. Each process
instance must generate a unique identifier that cannot collide across hosts,
containers, or PID reuse; it is created once and reused for that process's
heartbeat loop. The selected row remains visible with its actual
`instance_id`, `observed_at`, and freshness state. Missing expected processes
still produce the existing `not-observed`
offline entry. Telemetry completeness remains responsible for detecting
missing producer instances; this endpoint reports role-level service health.

Use one SQL selection helper shared by both Manager consumers, applying the
existing `heartbeat_health()` thresholds only after the bounded per-process
selection; do not add a second SQL freshness threshold. Preserve read-only
behavior and existing public redaction.

## Affected files/components

- `src/context_library_manager/api.py` (`runtime_health_data()` and
  `agent_service_summary()`)
- `src/context_library_manager/processes.py`
- `tests/manager/test_runtime_observability.py`
- `frontend/runtime-live.tsx`
- `frontend/runtime-live.test.tsx`
- `docs/DEPLOYMENT.md`

## Test strategy and commands

- Seed an offline old instance and a fresh healthy replacement for the same
  process, call the project-scoped health endpoint, and assert aggregate
  `healthy` plus the replacement `instance_id`.
- Seed two instances for one process and assert role-level health uses the
  newest observed instance while preserving the selected identity in the
  response.
- Assert the public `/api/v1/health` endpoint still redacts instance IDs to
  `observed`/`not-observed` while the project-scoped endpoint exposes the
  diagnostic identity to authorized operators.
- Assert the agent-service summary uses the same selected worker health as the
  runtime health endpoints, preventing duplicate heartbeat-selection logic.
- Assert the runtime health UI renders one selected row per process after a
  restart rather than accumulated historical instance rows.
- Assert two heartbeat loops/process instances with the same role and reused
  PID input still generate distinct instance IDs and durable rows.
- Assert a genuinely stale newest instance remains non-healthy.
- Assert existing missing-process and public-redaction behavior remains.
- Run focused Manager tests, then `make test`, `make check`, `make e2e`,
  `make smoke`, `make contracts-check`, `make plugin-check`, `make package`,
  and `git diff --check` as applicable.

## Risks

- Role-level health intentionally answers whether the service role currently
  has a fresh producer, not whether every replica is healthy. Missing or late
  producer heartbeats remain telemetry-completeness failures under
  `docs/DEPLOYMENT.md` and SPEC §11.5.
- Instance IDs must include a per-process unique value rather than only a role
  and reusable PID; otherwise concurrent containers can overwrite one
  another's durable row and invalidate the multi-replica contract.
- Document the unique instance identity and project-scoped diagnostic field in
  `docs/DEPLOYMENT.md`; this is required operator guidance, not a conditional
  follow-up.
- Restart-safe selection does not prune historical `process_heartbeats` rows.
  This issue explicitly accepts that existing retention behavior as a
  separately tracked follow-up; the implementation must not delete history as
  part of a health read, and a future retention change must preserve the
  diagnostic evidence contract.
- SQL portability must be checked against the supported SQLite and PostgreSQL
  dialects; use a portable window function rather than engine-specific syntax.

## Unresolved questions

The bounded issue contract does not include heartbeat retention/pruning;
unbounded historical-row growth is a known operational follow-up. Replica
aware role-level aggregation is included as specified above.
