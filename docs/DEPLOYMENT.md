# Manager deployment

## Supported topology

Run the Manager against PostgreSQL 15 or newer for a multi-replica deployment.
SQLite is for one-process development and tests. Deploy these process roles:

- API
- worker
- reconciliation
- notification
- scheduler

Start each role as a supervised long-running process:

```text
poetry run python -m context_library_manager.processes worker
poetry run python -m context_library_manager.processes reconcile
poetry run python -m context_library_manager.processes notifications
poetry run python -m context_library_manager.processes scheduler
```

Each role emits durable heartbeats on an independent loop and repeats work
every 30 seconds. `CLM_PROCESS_INTERVAL_SECONDS` may be set from 1 through 45
seconds. `CLM_PROCESS_ONCE=true` is reserved for local diagnostics and tests,
not production.

The agent adapter is a separate bounded process invoked only for semantic work.
Every deployed producer in the active telemetry manifest must emit a heartbeat
at least every 60 seconds. Missing or late heartbeats make telemetry incomplete
and prevent an SLO claim.

## Required configuration

Set `CLM_LIBRARY_ROOT` to the separately governed canonical checkout and
`CLM_STATE_ROOT` to Maintainer state outside that checkout. Set
`CLM_DATABASE_URL`, an explicit `CLM_PROJECT`, and a stable
`CLM_SESSION_SECRET`. Manager startup fails closed when the project argument
and environment variable are both absent. Production
browser login requires OIDC issuer, audience, client, token, authorization, and
JWKS configuration. Do not put commands, credentials, or secret values in the
canonical repository's `runtime.yaml`.

The canonical checkout needs a configured Git identity and a clean worktree.
Normal Manager publication calls the typed Maintainer service, which validates,
locks, stages, atomically replaces, validates again, and commits the canonical
change.

## Release procedure

1. Back up the runtime database, Maintainer state, and canonical Git remote.
1. Run `make check`, `make e2e`, `make smoke`, and `make package`.
1. Apply the release to one instance and allow packaged migrations to finish.
1. Verify `/health`, producer heartbeats, telemetry coverage, and the current
   product/schema versions.
1. Roll the remaining instances without mixing incompatible product versions.

The application refuses a database with an unknown newer migration. Upgrade the
application or restore a compatible backup; never edit the migration ledger to
bypass this check.
