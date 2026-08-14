# Specification checkpoint: #2 / shared-context inheritance

Status: specification-only checkpoint  
Originating issue: #2 — Add shared-context inheritance across project packs  
Dependency: #1 / multi-project Manager support, merged as `e18e04d`

## Scope and non-goals

Define an explicit, versioned relationship contract allowing a project pack to
consume selected shared parent packs without copying their canonical records.
Implement deterministic effective-context resolution and validation for
missing parents, cycles, duplicate identities, ambiguous graphs, precedence,
supersession, conflicts, and provenance. Preserve source ownership on every
effective decision.

This issue does not redesign canonical storage, publish inherited records into
the child pack, infer relationships from filesystem layout, add retrieval
ranking, or authorize Plugin/Manager canonical writes. The Plugin remains
read-only and the Maintainer writes only the owning pack.

## Applicable authority and proposed contract

Add a versioned `context-library/shared-context-relationships` artifact in
`projects/<project>/shared-context-relationships.yaml` with:

```json
{
  "schema": "context-library/shared-context-relationships",
  "schema_version": 1,
  "project": "example-project",
  "revision": "relationships-r1",
  "parents": [
    {"project": "shared-project", "required": true, "order": 10}
  ]
}
```

Relationships are explicit configuration, not discovery. Parent identifiers
are unique, normalized project IDs; order is deterministic and cannot grant
authority. A parent MUST explicitly authorize the child in its
`authority.yaml` `shared_context_consumers` allow-list; absent authorization,
the relationship fails closed. Relationships are transitive: a child sees
grandparent records after recursively resolving parents. Effective traversal
is depth-first in ascending edge order, with project ID as the stable tie
breaker; duplicate identities are rejected unless source and content are
byte-equivalent. A missing required parent, cycle, duplicate edge, or
ambiguous relationship fails closed. Legacy packs with no artifact remain
standalone.

For a transitive chain, every ancestor MUST authorize the ultimate child
consumer in `shared_context_consumers`; authorizing only the immediate child
does not authorize re-sharing to a grandchild. The resolver checks this rule at
each hop and fails closed when any ancestor omits the ultimate consumer.

The `required` field defaults to `true` when omitted; `order` defaults to
zero. Both defaults are deterministic and fail closed for malformed values.

The resolver returns a read-only effective view. Each decision carries its
owning project, source pack identity/digest, provenance, derivation,
supersession references, and original decision ID. It never rewrites a source
record or strengthens inherited provenance. Same-identity records are rejected
unless their source and content are byte-equivalent; disagreements remain
visible to normal conflict policy. Supersession is resolved only by explicit
canonical references within the effective graph, and cross-scope conflicts are
reported rather than silently chosen.

## Affected components

- Core relationship contract and deterministic graph resolver;
- canonical pack discovery/query effective-view boundary;
- Maintainer validation/query/index/publication safeguards, with writes scoped
  to the owning pack;
- Manager read/search/proposal/review/audit responses, exposing source scope;
- generated Plugin runtime and read-only MCP/projection behavior; and
- shared synthetic fixtures and cross-component black-box tests.

The contract is anchored in SPEC.md §9.2 and §14: the named file is part of
the target project-pack layout, and parent authorization is required to avoid
cross-project evidence access and provenance laundering.

No canonical data is committed. Tests use temporary synthetic pack trees.

## End-to-end validation

The principal slice is:

```text
explicit relationship fixture -> Core effective view -> Maintainer query
  -> Manager read/search/proposal/review/audit -> generated Plugin
  read/projection
```

Positive cases cover one parent, multiple ordered parents, standalone legacy
packs, and two parents sharing a common grandparent (a diamond graph) with a
single deduplicated entry. Negative cases cover missing required parent,
unauthorized parent (child absent from `shared_context_consumers`), cycle,
unauthorized transitive parent (grandparent authorizes the immediate child but
not the grandchild), duplicate identity, ambiguous graph, conflicting
inherited decisions, supersession, and provenance preservation. Assertions compare observable
decision IDs, source scope, status, conflict visibility, and non-mutating
Plugin output across all consumers. A mutation test proves that filesystem
adjacency alone does not create inheritance.

## Compatibility, safety, and unresolved questions

The artifact is additive and versioned; unsupported versions fail closed.
Existing project configuration remains valid without relationships. Effective
views must not be cached across relationship or source-digest revisions.
Provider-backed evaluation is out of scope. The downstream retrieval roadmap
must consume this contract rather than inventing cross-scope precedence. Open
risks to validate during implementation are resolver cost for deep graphs and
consistency when a parent revision or authorization allow-list changes during
a read; the implementation must bind one consistent source-digest and
authorization-state snapshot per effective-view request and report
bounded-resource failures explicitly.

Before implementation, independent review must confirm graph semantics,
authority/provenance preservation, cross-component parity, public-data
hygiene, and the complete black-box slice. This checkpoint contains no code.
