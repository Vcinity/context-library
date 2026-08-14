# Specification checkpoint: #15 / RT-02

Status: specification-only checkpoint  
Originating issue: #15 — Implement deterministic operative-context resolution  
Dependency: #14 / RT-01 merged (`54318c1`)

## Scope and non-goals

Implement the inward Core resolver that parses one effective project register,
resolves explicit supersession, AP-03 applicability, and conflicts, then feeds
the RT-01 response/renderer. Every current explicit directive that is
deterministically applicable is retained before any optional ordering. Unknown
or unresolved applicability remains visible but non-operative.

This issue does not add ranking, semantic similarity, canonical writes, MCP
transport, or cross-project inheritance. Relevance may order optional items
only after the complete operative and conflict sets are established.

## Deterministic resolution rules

1. Parse anchored canonical records with the existing safe parser and bind the
   request to the explicit project pack/revision.
1. Resolve exact supersession references transitively; superseded records are
   non-operative and remain auditable.
1. Evaluate repository scopes through AP-02 exact membership, with AP-03 parity
   guaranteeing the same result across adapters. Conditional or missing-signal
   results remain `undetermined`.
1. Retain every current explicit `unconditional` or `satisfied` record in the
   operative set, regardless of lexical relevance or token ranking.
1. Detect applicable conflicts from canonical conflict references and retain
   all members in a separate conflict channel; never choose a winner by rank.
1. Sort deterministically by state priority, decision ID, and source scope,
   then pass the complete sets to RT-01 budget handling.

Revision identity is returned unchanged; the rendered capsule retains its own
exact `sha256` accounting. The resolver is pure, offline, and read-only.
Malformed references, cycles, duplicate IDs,
unsafe paths, and ambiguous packs fail closed.

## Coverage and budget behavior

The RT-01 response is complete only when every operative ID is present and no
applicable conflict or unsafe inclusion is hidden. The RT-01 renderer must
first remove optional rationale/candidate material (which RT-01 currently does
not yet model) before operative directives. If the budget remains insufficient,
operative IDs are explicitly listed in truncation and the response cannot claim
completeness; no omission is silent. Conflict IDs are aggregated from
operative, uncertain, and non-operative references so an applicable conflict
cannot disappear because one member is out of scope.

## Affected components and end-to-end validation

- `src/context_library_core/` resolver and canonical effective-view boundary;
- Maintainer query adapter consuming the Core resolver;
- RT-01 response/renderer integration;
- synthetic register fixtures and black-box resolver CLI/file tests; and
- retrieval safety/benchmark matrix at scales 10, 100, 1,000, and 10,000.

The principal slice is:

```text
synthetic project register + task request
  -> Core resolver -> RT-01 response/capsule -> RB-05 safety envelope
```

Tests cover current explicit recall, supersession, unresolved applicability,
conflicts, deterministic repeated output, malformed/safety failures, and a
sanity mutation proving lexical ranking cannot remove an operative directive.
Concrete validation commands are `PYTHONPATH=src poetry run pytest -q
tests/contracts/test_task_context_resolution.py`, `PYTHONPATH=src make
retrieval-benchmark`, `PYTHONPATH=src make contracts-check`, `git diff
--check`, and `PYTHONPATH=src poetry run ruff check src tests scripts`.

## Compatibility and review boundary

The resolver consumes AP-02/AP-03 and RT-01 contracts without changing their
versioned shapes. Existing canonical query behavior remains available for
legacy callers. All fixtures are synthetic and canonical-data read-only. This
immutable checkpoint contains no implementation and requires fresh independent
read-only review before autonomous approval.

Risks are that optional-content budgeting could weaken operative coverage, or
that conflict aggregation could omit a non-operative member; tests must
mutate both cases and fail closed. Unresolved questions are whether RT-01
should expose optional rationale as a separate field or only as renderer
metadata, and whether source revisions need a separate digest in a future
contract revision. Neither changes the current safety rules.
