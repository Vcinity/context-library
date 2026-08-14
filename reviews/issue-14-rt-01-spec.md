# Specification checkpoint: #14 / RT-01

Status: specification-only checkpoint  
Originating issue: #14 — Define task-context request, response, and compact capsule rendering  
Dependency: #13 / AP-03 merged (`fa3af8f`)

## Scope and non-goals

Define a versioned request/response contract and deterministic compact
renderer for one explicit project-bound task. The response separates
operative directives, unresolved applicability, applicable conflicts,
revision identity, truthful coverage, truncation, and an agent-visible capsule.

This issue does not resolve applicability beyond AP-03, rank optional context,
implement the resolver, add a Plugin MCP tool, or fetch canonical records.
Unknown tokenizers are reported as unverified; no exact budget claim is made
for them.

## Proposed contract

Add versioned `context-library/task-context-request` and
`context-library/task-context-response` families. The request requires:

```json
{
  "schema": "context-library/task-context-request",
  "schema_version": 1,
  "project": "example-project",
  "task_summary": "Change the service boundary",
  "operation": "modify source",
  "repository_scopes": ["src/service"],
  "agent_token_budget": 1200,
  "tokenizer": {"name": "tiktoken", "version": "0.9.0", "vocabulary_revision": "cl100k_base"}
}
```

The response contains `project`, source `revision`, `operative_directives`,
`applicability_uncertainties`, `applicable_conflicts`, a machine-readable
`coverage` object, `truncation`, and a compact `agent_visible_capsule` with
exact UTF-8 byte count, digest, and token count. Every item includes a stable
decision ID; common revision, scope, and provenance metadata is rendered once
in the header and referenced by ID rather than repeated per item.

Coverage states distinguish complete, incomplete, and truncated results.
Truncation names a reason and all omitted operative IDs. If the budget cannot
fit every operative directive, the response is non-operative/incomplete and
must explicitly report the omitted IDs; it must never silently drop them or
claim completeness. The machine-readable fields remain available even when
the compact capsule is empty or over budget.

## Rendering and authority rules

The renderer consumes already-resolved AP-03 results. Only explicit
`unconditional` or `satisfied` decisions enter `operative_directives`;
`undetermined` decisions remain in `applicability_uncertainties`, and
conflicts remain separate. Ordering is deterministic by state priority,
decision ID, and source scope. Rendering is read-only and does not infer
authority from task-summary text or semantic similarity.

The reference tokenizer is pinned to `tiktoken` `0.9.0` / `cl100k_base`, using
the exact serialized capsule. A different tokenizer identity produces a
separate `unverified` budget status and cannot be reported as exact compliance.

## Affected components and end-to-end validation

- Core contracts and compact renderer;
- generated schemas/runtime metadata;
- synthetic positive, malformed, unsupported-version, and insufficient-budget
  fixtures; and
- black-box CLI/file tests with golden capsule bytes.

The principal slice is:

```text
task request + resolved synthetic decisions
  -> Core response contract -> deterministic compact capsule + audit fields
```

Tests assert explicit project binding, stable IDs, metadata de-duplication,
exact tokenizer/byte/digest accounting, deterministic repeated rendering,
truthful complete/incomplete/truncated coverage, and failure when a mutation
silently removes an operative directive. Validation remains offline and uses
synthetic repositories only.

## Compatibility, risks, and review boundary

The new families are additive and unsupported versions fail closed. Existing
AP-02/AP-03 behavior is consumed, not redefined. The renderer cannot promise
universal model optimality; it reports the named tokenizer and exact visible
text. This immutable checkpoint contains no implementation and requires fresh
independent read-only review before autonomous approval.
