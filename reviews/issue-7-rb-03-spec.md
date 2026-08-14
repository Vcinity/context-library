# Specification review: #7 / RB-03

Status: Specification-only checkpoint for independent review
Originating issue: #7 — Build deterministic retrieval benchmark scale generator
Dependency: #5 / RB-01 (merged); corpus cases from #6 are available
Authority: SPEC.md Sections 9 and 16.6

## Scope and non-goals

Add deterministic benchmark-support generation that materializes synthetic
canonical-format project packs at exactly 10, 100, 1,000, and 10,000 decision
records. The generator will preserve a small set of known gold operative
decisions and add seeded distractors and relationship structure, then write
only to a caller-selected temporary/output directory.

This issue does not modify the canonical library, implement retrieval or
ranking, run agent evaluations, define new retrieval contracts, choose a
tokenizer, change acceptance thresholds, or add a production write path. It
does not commit generated 10,000-record packs; compact generator source and
tests are the repository artifacts.

## Applicable requirements

Each requested scale MUST produce exactly the requested number of valid
canonical decisions, with deterministic IDs, ordering, metadata, indexes, and
content digests for a pinned seed and configuration. Generated packs MUST
retain the known operative gold decisions and contain controlled
supersession, conflict, scope, and applicability distributions. Repeating the
same invocation MUST be byte-identical. Generation MUST be offline and
validate through the existing canonical parser, parse_register. The generator
does not require validate_projection_compatibility for unresolved-conflict
fixtures; any such fixture must use applies_when or non-overlapping
affected_layers so it remains structurally valid without being treated as an
active projection conflict. The generator MUST fail closed for invalid
scale/configuration and MUST not require or discover a canonical data root.

## Proposed contract and design

Create a small generator module and CLI boundary that accepts:

- one of the exact scales 10, 100, 1000, or 10000;
- a pinned integer seed;
- a versioned generator configuration;
- an explicit output directory; and
- a synthetic project identifier.

The output is a temporary canonical project pack containing a
decision-register.md plus the same deterministic indexes produced by the
existing Maintainer publication/index logic. A manifest records generator
version, seed, configuration digest, requested scale, actual decision count,
ordered decision IDs, register digest, and index digests. The manifest is
diagnostic benchmark metadata, not canonical Context Library data.

The authored gold subset is defined compactly in source rather than duplicated
at every scale. Generated decisions use stable synthetic identifiers derived
from (generator_version, seed, case, ordinal) and deterministic ordering.
Identifiers MUST conform to the canonical decision-ID pattern
^[a-z0-9][a-z0-9._-]*$.
The relationship planner allocates bounded distributions for:

- current explicit operative gold decisions;
- controlled distractors;
- superseded chains with a current terminal decision;
- explicit conflicts with stable conflict keys;
- global and project-scoped affected layers; and
- explicit versus unresolved applicability signals.

The planner must reject a configuration that cannot satisfy the requested
distribution without silently changing the seed or target count. It must
never generate a supersession or conflict reference to an absent decision,
and supersession chains MUST be acyclic.

## Affected components and boundaries

- src/context_library_core/ or scripts/ — deterministic generator and
  manifest/validation boundary, following the least coupled existing pattern;
- tests/ — black-box CLI/output tests and canonical parser validation;
- contracts/README.md or benchmark documentation — invocation and manifest
  contract only if needed; and
- existing src/context_library_maintainer/publish.py index conventions —
  reused or mirrored through a narrow public helper without adding mutation
  authority.

No Manager, Plugin, runtime configuration, migration, dependency, or
canonical-library file is in scope.

## Authority, provenance, and public-data boundaries

All generated content is synthetic benchmark data. Synthetic decisions,
identities, paths, dates, conflict keys, and provenance must not resemble or
copy private or canonical records. The generator accepts an explicit output
path and must reject a configured canonical-library root or an existing
non-empty directory unless the caller explicitly permits a test output
directory. Even a permitted non-empty directory must pass the same path-safety
checks: no symlink escape and no overwrite of pre-existing unrelated files.
It performs no network access, canonical lock, publication, or remote mutation.

Gold decisions remain benchmark labels and do not grant publication authority.
Generated indexes and manifests are derived artifacts and are never treated as
canonical source records.

## Compatibility and migration

The generator consumes the accepted RB-01 task/gold contracts and the
synthetic corpus from #6 without modifying either contract family. The
canonical parser remains the authority for register syntax and relationship
invariants. Generator metadata is additive and versioned; changing its
serialization or seed derivation requires a new generator version and golden
expectation update, not a silent rebaseline.

## Black-box and incremental end-to-end strategy

The incremental slice is:

    pinned seed/configuration -> generator CLI -> temporary canonical pack
    -> canonical parser/index validation -> manifest and digests

Tests will invoke the stable boundary for every required scale and assert:

- the parsed decision count equals the requested scale exactly;
- gold operative IDs are present and their current records remain valid;
- relationship counts and scope/applicability distributions are within the
  declared configuration, with exact deterministic counts where configured;
- all generated references resolve and canonical parsing succeeds;
- two independent output directories from the same seed/configuration are
  byte-identical file-for-file;
- changing the seed changes the generated digest while preserving the target
  count and validity;
- invalid scales, impossible distributions, output traversal, and canonical
  root attempts fail non-zero; and
- no network or canonical-data mutation occurs.

The tests use temporary synthetic directories and never commit generated
records. A focused sanity mutation removes a generated decision or alters an
index and asserts parser/manifest validation fails.

## Validation commands

- focused generator and canonical-parser tests at all four scales;
- poetry run ruff check on changed Python and test files;
- make contracts-check if contract/generated files are touched; and
- git diff --check.

The issue does not claim benchmark safety or token-efficiency results; those
remain later benchmark-runner and acceptance issues.

## Risks, alternatives, and unresolved questions

- Generating full packs during tests may be slow at 10,000 records; tests may
  use a compact deterministic renderer, but must still parse every generated
  decision and assert byte identity.
- Reusing private publication helpers could accidentally import mutation
  authority; prefer a pure renderer or a narrowly scoped index helper.
- Fixed relationship distributions can become impossible at small scales;
  the configuration must state exact versus minimum counts and fail clearly
  when impossible.
- The manifest format is diagnostic and should not become a new canonical
  contract unless a later issue explicitly approves that expansion.

## Review and approval boundary

This is an immutable specification-only checkpoint. A fresh independent
read-only review must assess scope, deterministic contract, canonical parser
compatibility, authority boundaries, public-data hygiene, and black-box
coverage before the Project Spec Gate may be autonomously set to Approved.
No implementation files are included in this checkpoint.
