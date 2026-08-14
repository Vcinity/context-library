# Specification checkpoint: #17 / projection policy and retrieval guidance

Status: specification-only checkpoint  
Originating issue: #17 — Align projection policy, Plugin skill, and retrieval documentation  
Dependency: #16 / read-only Plugin task-context tools, merged in `9ddc856`

## Scope and non-goals

Make the accepted task-context request the normal agent-facing retrieval path.
Classify only genuinely universal constraints as eligible for automatic
projection, using the accepted benchmark vocabulary and evidence already in
the repository. Update the Plugin skill, references, projection design,
tool-use documentation, and deployment guidance so they describe one
consistent read-only workflow.

This issue does not change canonical records, add write authority, remove all
projection, invent new applicability selectors, or inject task-specific
context at session start without an explicit task signal. Existing required,
optional, disabled, and undetermined policy states remain supported.

## Authority and policy contract

Automatic projection is limited to decisions that are explicitly universal:
explicit provenance, current/non-superseded status, no unresolved
applicability condition, and no project- or repository-specific scope. A
decision with a repository scope, conditional applicability, non-explicit
provenance, supersession, conflict, or missing applicability evidence is not
eligible for automatic projection. The task-context MCP tool is the normal
path for task-specific context and requires explicit project binding.

Projection remains a derived, auditable artifact and never canonical data.
The Plugin remains read-only. Session-start behavior may describe the tool and
policy but MUST NOT select or inject task-specific decisions without a task
signal. Documentation MUST distinguish universal projected guidance from
task-context responses and on-demand audit records.

## Affected components

- `plugins/context-library/projection.py` and its policy tests;
- Plugin skill and references;
- `docs/TOOL_USE_CASES.md`, `docs/agents-constraint-projection.md`, and
  `docs/PLUGIN_DEPLOYMENT.md`;
- activation, projection, MCP, and packaging tests; and
- generated/read-only documentation checks where applicable.

## End-to-end validation

The principal slice is:

```text
synthetic universal/scoped/conditional fixture -> projection policy ->
Plugin activation/session guidance -> explicit task-context MCP request ->
compact capsule -> on-demand audit record
```

Positive cases cover one universal constraint and an explicit task-context
request. Negative cases cover scoped, conditional, superseded, conflicted,
non-explicit, disabled, and no-task-signal inputs. Assertions prove that
projection contains only eligible universal guidance, task-specific decisions
remain available through the MCP tool, session start does not inject them, and
all outputs remain read-only and consistent with the shared contracts.

## Validation commands, risks, and unresolved questions

Run the focused projection, activation, MCP, and packaging tests, Markdown
lint, `PYTHONPATH=src make plugin-check`, `PYTHONPATH=src make contracts-check`,
and `git diff --check`. Tests remain offline and use synthetic temporary packs.

Risks are documentation drift, accidental token-heavy full-register guidance,
and misclassifying a scoped rule as universal. The implementation must retain
the existing policy-state behavior and fail closed on malformed policy.
Unresolved questions are limited to the exact presentation wording for
disabled and undetermined states; their semantics are fixed by SPEC.md and
existing contracts. This checkpoint contains no implementation.
