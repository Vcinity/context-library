# Context Library

Context Library is one versioned code product with four explicit ownership
boundaries:

- Core owns versioned contracts, canonical parsing, discovery, and read models.
- Maintainer owns deterministic canonical maintenance and the administrative
  `clm` adapter.
- Manager owns authenticated intake, orchestration, review, audit, telemetry,
  and the operator web application.
- The installable Plugin is a self-contained, canonical-read-only integration.

Canonical decision data remains in a separately governed repository. This
monorepo contains no canonical project pack.

## Development

Requirements are Python 3.12+, Poetry, Node.js/npm, `mdl`, and a Chromium
browser available to Playwright.

```sh
make install
make test
make check
make e2e
make smoke
```

Run the Manager locally with:

```sh
make run
```

Use `clm` only for administration, development, migration, recovery, or an
explicitly authorized unattended-maintenance workflow:

```sh
poetry run clm version --json
poetry run clm capabilities --json
```

Normal agents read through the Plugin and submit canonical proposals through
the Manager. They do not edit canonical Markdown or invoke write-capable
Plugin operations.

## Context policy

A consumer may explicitly set context to `required`, `optional`, or
`disabled`. An absent policy is `undetermined` and fail-open. Missing required
context produces an advisory notice; optional and undetermined context do not
interfere; disabled context is silent. Canonical additions and corrections go
through the Manager.

See [ARCHITECTURE.md](ARCHITECTURE.md), [MIGRATION.md](MIGRATION.md), and
[docs/MAINTAINER_ADMINISTRATION.md](docs/MAINTAINER_ADMINISTRATION.md).

For a task-oriented overview of the available Plugin tools, projection
commands, Manager workflow, and `clm` use cases, see
[docs/TOOL_USE_CASES.md](docs/TOOL_USE_CASES.md).
