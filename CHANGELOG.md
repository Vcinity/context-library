# Changelog

## Unreleased

## 0.3.4 - 2026-08-08

- Add deployment-time Plugin runtime configuration shared by the MCP server
  and session-start hook, while preserving environment-variable overrides.
- Allow deployments to assign a marketplace name without changing the public
  marketplace default.
- Document Codex hook review/trust and new-thread requirements for installs
  performed through the Plugin menu.

## 0.3.3 - 2026-08-07

- Add Manager harvest batch intake for coordinated library harvesting.

## 0.3.2

- Consolidate Core, Maintainer, Manager, frontend, and Plugin into one
  versioned monorepo release.
- Establish a typed Maintainer service and keep `clm` as its administrative
  adapter.
- Generate the Plugin read runtime from the authoritative Core parser.
- Add explicit required, optional, disabled, and undetermined context policy.
- Add event-derived autonomy and agent-cost telemetry with coverage evidence.
- Harden transactional replay, crash recovery, confidential-data boundaries,
  concurrent claims, publication evidence, and supervised runtime heartbeats.
- Close both adversarial review cycles with zero unaccepted Critical, High, or
  Medium findings.
- Replace organization-specific sample identities and machine-local paths with
  neutral, portable examples.
- Preserve legacy flat-pack aliases generically and ship a neutral,
  independently installable community marketplace entry.
