# Specification checkpoint: #12 / AP-02

Status: specification-only checkpoint  
Originating issue: #12 — Implement the versioned applicability contract and deterministic evaluator  
Dependency: #11 / AP-01 merged (`8c1d93a`)

## Scope and non-goals

Implement the accepted AP-01 vocabulary as one authoritative Core contract and
pure evaluator. The only authoritative selector in version 1 is normalized
`repository_scopes`. The evaluator returns one of `unconditional`, `satisfied`,
`unsatisfied`, or `undetermined`, while preserving decision identity,
declared/effective provenance, source scope, supersession references, and
conflict state.

This issue does not implement task-context retrieval, ranking, inheritance,
canonical writes, or adapter-specific selector semantics. Deferred selectors
(`operation`, `affected_layers`, environment, lifecycle, artifact type, and
actor) are rejected as unsupported rather than silently ignored.

## Proposed versioned contract

Add `context-library/applicability` version 1 in the Core contract source and
generated schemas/runtime. The request contains a normalized task scope and a
decision applicability declaration:

```json
{
  "schema": "context-library/applicability",
  "schema_version": 1,
  "task": {"repository_scopes": ["src/example"]},
  "decision": {
    "decision_id": "rule-1",
    "repository_scopes": ["src/example"],
    "provenance": "explicit",
    "effective_provenance": "explicit",
    "source_scope": "project/example",
    "supersedes": [],
    "conflict_ids": []
  },
  "applies_when": null
}
```

The result contains the state, matched selectors, required selectors, a
machine-readable reason, and the unchanged decision identity/authority fields.
Selectors are exact normalized relative paths or declared project scopes;
absolute paths, traversal, empty values, duplicates, unknown selector keys,
and ambiguous mappings fail validation. A decision with no selector and no
conditional expression is `unconditional`. A scoped decision is `satisfied`
when task scopes intersect according to the explicit normalized scope rule,
`unsatisfied` on a known non-match, and `undetermined` when the required task
signal is absent. `undetermined` is never returned in an operative set.

## Affected components and compatibility

- `src/context_library_core/contracts.py` and a focused Core evaluator module;
- generated contract schemas and Plugin-compatible runtime source;
- synthetic contract fixtures and black-box CLI/contract tests; and
- `contracts/README.md` documentation.

Existing `context-policy` and `context-resolution` contracts remain compatible.
The new family is additive and unsupported schema versions fail closed. Core is
the only evaluator authority; Maintainer, Manager, and Plugin consumers must
use its generated behavior in later parity work.

## End-to-end and negative validation

The stable slice is a versioned JSON request through the Core evaluator to a
machine-readable result and generated schema/runtime validation. Tests cover:

- unconditional, exact/intersecting scope (`satisfied`), and explicit mismatch
  (`unsatisfied`);
- missing task scope (`undetermined`) with an assertion that it cannot become
  operative;
- malformed, absolute, traversal, duplicate, ambiguous, unknown-selector, and
  unsupported-version inputs;
- preservation of provenance, source scope, supersession, and conflict fields;
- deterministic repeated output and generated schema/runtime drift checks; and
- a mutation that changes an undetermined result to operative and is rejected.

All fixtures are synthetic, offline, and canonical-data read-only. No
task-context retrieval or provider evaluation is introduced.

## Risks and unresolved questions

Scope intersection must remain conservative and documented so a parent path
does not accidentally authorize an unrelated sibling. The evaluator will use
the existing canonical path normalization rules and reject mappings it cannot
prove safe. Future selector additions require a new benchmark-backed review
and contract revision rather than widening version 1 silently.

This immutable checkpoint requires fresh independent read-only review before
the autonomous Spec Gate is approved. Implementation files are not included.
