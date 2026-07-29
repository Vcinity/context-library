# Migration Provenance

This monorepo refactors three read-only source repositories into one release.
The source repositories remain independent, unmodified migration evidence.
Canonical Context Library decision data is not imported.

## Verified source baselines

Verified on 2026-07-28 before import:

| Component | Source | Branch | Commit | Worktree |
| --- | --- | --- | --- | --- |
| Maintainer | `../context-library-tool` | `master` | `8b2990a834b5e6647838ad39435040168518cfcf` | clean |
| Manager | `../context-library-manager` | `master` | `32cee4b01426a7472c52676cc049476a58f1653a` | clean |
| Plugin | `../context-library-plugin` | `master` | `2c434171951792e392719004ce7ca10af7f73454` | dirty; committed `HEAD` is the import baseline |

The canonical data checkout remains separately governed, read-only migration
input. No canonical decision record or project pack is copied into this
monorepo, and mutation tests use synthetic repositories or temporary copies.

## Source-to-destination map

| Source path | Destination | Disposition |
| --- | --- | --- |
| `context-library-tool/src/context_library_tool/` | `src/context_library_maintainer/` | Refactored behind the typed Maintainer service and shared Core contracts |
| `context-library-tool/tests/` | `tests/maintainer/` and integration tests | Test intent retained and expanded |
| `context-library-tool/AGENT_WORKFLOW.md` | `docs/MAINTAINER_ADMINISTRATION.md` | Retained as an explicitly administrative workflow; normal Manager traffic does not use the CLI adapter |
| `context-library-tool/README.md` | Root `README.md` and Maintainer administration documentation | Consolidated for the unified release |
| `context-library-manager/src/context_library_manager/` | `src/context_library_manager/` | Refactored to call the typed Maintainer service |
| `context-library-manager/frontend/` | `frontend/` | Imported operator UI |
| `context-library-manager/e2e/` | `e2e/` | Imported browser journeys |
| `context-library-manager/tests/` | `tests/manager/` | Test intent retained and expanded |
| `context-library-manager/docs/openapi.json` | `docs/openapi.json` | Generated API contract retained with drift checks |
| `context-library-manager/AGENT_WORKFLOW.md` | `docs/DEPLOYMENT.md` and root `README.md` | Consolidated into the in-process Manager worker and operator workflow |
| `context-library-manager/.env.example` | `docs/DEPLOYMENT.md` | Replaced by an explicit environment-variable reference with no machine-specific canonical-root default |
| `context-library-manager/README.md` | Root `README.md` | Consolidated for one release and one developer interface |
| `context-library-manager/PLAN.md`, `UI_PLAN.md`, `UI_SPEC.md` | `SPEC.md`, frontend, E2E, and tests | Historical planning input inspected; not imported as competing authority |
| `context-library-plugin/plugins/context-library/` | `plugins/context-library/` | Imported from committed `HEAD`, then refactored as a self-contained read-only artifact |
| `context-library-plugin/.agents/plugins/marketplace.json` | `.agents/plugins/marketplace.json` | Replaced with neutral community marketplace metadata |
| `context-library-plugin/scripts/` | `scripts/plugin/` or root validation scripts | Test and packaging intent retained |
| `context-library-plugin/docs/agents-constraint-projection.md` | `docs/agents-constraint-projection.md` | Retained and updated for the generated read runtime |
| Component `Makefile`, package manifests, and lock files | Root `Makefile`, `pyproject.toml`, `poetry.lock`, and npm manifests | Replaced by the unified pinned build |
| Caches, runtime state, browser logs, PID files, and test results | None | Deliberately excluded as ephemeral, machine-owned output |

Duplicate source specifications and component-level build manifests remain
provenance evidence in their source repositories. `SPEC.md` in this monorepo
is authoritative.

The Typer CLI now performs argument parsing and envelope emission only for
canonical operations. Initialization, ingestion, work leasing, reconciliation,
publication, conflict resolution, validation, maintenance, and legacy
migration execute through `MaintainerApplicationService`. Conflict packets and
resolutions are versioned Core contracts.

## Dirty Plugin source preservation

The Plugin source worktree contained user-owned changes before migration:

- `AGENTS.md` has an uncommitted generic generated Context Library block.
  Current-file SHA-256:
  `f99f93942a0302957366bb1686cc7300ede928de55844f056d440bc94c793d65`.
- `plugins/context-library/.codex-plugin/plugin.json` changes version `0.2.8`
  to `0.2.8+codex.20260716010744` and reformats `capabilities`.
  Current-file SHA-256:
  `e8329dfcf0bc262047870b399907c67406660774c42361e4f5c7e034f355aacb`.
- `REVIEW.md` is untracked and records an earlier adversarial review.
  Current-file SHA-256:
  `95c87e7b04663063cc043a13c1a6080d3a561d2c864327cefd3068041b703ac7`.

These files remain untouched in the source repository. They are not imported
because their disposition is user-owned and the reproducible baseline is the
committed Plugin `HEAD`. Importing any of them later requires explicit user
direction.

## Preservation rules

- Never reset, clean, rewrite, archive, or delete a source repository.
- Never use a source repository or the live canonical checkout as migration
  scratch space.
- Preserve historical canonical decision bytes during authorized migrations.
- Record deliberate source behavior omissions here as they are identified.
