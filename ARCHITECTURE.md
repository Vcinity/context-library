# Architecture

## Authority boundary

```text
human or automation
        |
        v
Manager API and review policy
        |
        v
typed Maintainer application service
        |
        v
separately governed canonical library
```

`clm` is a second, administrative adapter over the same Maintainer service.
The Plugin is read-only with respect to canonical data. Its only permitted
writes are explicitly configured consumer-local generated projections and
Plugin-local diagnostics.

## Dependency direction

```text
Plugin adapters ----------> generated read-only Core subset
Maintainer CLI -----------> Maintainer service ----------> Core
Manager API/workers ------> Maintainer service ----------> Core
Frontend -----------------> Manager HTTP API
```

Core imports no Manager, Maintainer, Plugin, web-framework, database-driver, or
frontend code. Manager does not couple normal operations to `clm` subprocess
command spellings. The Plugin artifact installs without Manager or write
dependencies.

## Data boundary

Canonical project packs remain outside this repository and preserve append-only
history. Mutation tests use synthetic repositories or temporary copies.
Manager orchestration state, Maintainer local state, audit events, telemetry,
and Plugin projections are derived operational data, not canonical decisions.

## Runtime and recovery

Manager API, scheduler, worker, notification, and reconciliation processes can
run separately. SQLite supports deterministic local operation; PostgreSQL 15+
is the production database contract. Publication stages a complete project
pack, validates it, and replaces files atomically or reports restored and
unrestored paths. Dirty canonical worktrees and path hazards fail closed.

Autonomy metrics are reproduced from durable event lineage. History,
telemetry completeness, and overall SLO state are reported separately.
