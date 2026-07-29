# Recovery procedures

## Publication failure

Publication errors preserve the last known-good register and report updated,
restored, and unrestored paths. Stop automatic publication if any path is
unrestored. Restore it from the current Git `HEAD`, inspect the index and
worktree, run `clm validate`, and only then resume.

If a process stops after a canonical commit but before Maintainer state is
finalized, rerun publication. Before advancing `HEAD`, the Maintainer durably
records the exact commit object and publication-state transition it owns. On
restart it accepts only that exact commit with the expected target bytes,
reconciles candidate and publication state transactionally, and removes the
recovery record without appending a duplicate decision. A different commit,
an unrelated dirty path, or changed target bytes remains a hard refusal for
manual recovery.

Only one publisher may hold a project's lock. A concurrent attempt fails with
the typed publication-lock error before changing Git, project-pack files, or
Maintainer state; retry it after the active publisher releases the lock.

Do not use `git reset --hard` as a recovery shortcut. Preserve unrelated user
changes and resolve a dirty worktree deliberately.

## Work and agent recovery

The reconciliation process expires stale leases, settles outstanding agent
reservations, and records every recovery transition in audit and telemetry
lineage. Operator retry uses a durable idempotency record. Confirm the work
item, agent run, cancellation record, budget reservation, and telemetry
materialized state agree before manually overriding a terminal state.

## Telemetry gaps

The autonomy endpoint names each gap's producer, interval, reason, and
reconciliation state. Repair the upstream producer or replay the missing
durable events, then record reconciliation. Never interpolate missing
sequences or use synthetic fixtures as production history.

## Database restore

Restore the runtime database and canonical checkout to mutually consistent
backups. Start one Manager instance, allow migrations to run under the
migration lock, verify audit and telemetry replay, then restore the remaining
process roles. An unsupported newer schema is a hard stop.
