# Specification checkpoint: #16 / RT-03

Status: specification-only checkpoint  
Originating issue: #16 — Expose read-only Plugin task-context and full-record tools  
Dependency: #15 / RT-02 merged (`132ec35`)

## Scope and non-goals

Add two canonical-read-only Plugin MCP tools: one explicit-project
`get_task_context` tool returning the RT-01 compact response, and one
`read_decision_audit` tool returning stable full-record detail for selected
decision IDs. Both operate only inside the configured canonical read root and
require explicit project binding.

This issue does not mutate canonical data, add Manager/Maintainer write paths,
infer a project from the only available pack, rank or reinterpret Core
applicability, or inject task-specific context at session start.

## Proposed tool contracts

`get_task_context` accepts the RT-01 request fields plus an explicit `project`
and a bounded revision/read policy. It selects that project’s register,
delegates parsing/resolution/rendering to the packaged Core behavior, and
returns the RT-01 response including the exact agent-visible capsule.

`read_decision_audit` accepts `project`, one or more stable `decision_ids`, and
an optional `include_related` flag. It returns decision text, rationale,
evidence references, declared/effective provenance, source scope, supersession,
conflict, applicability, and register revision. It never returns write-capable
commands or credentials and rejects IDs/path components that escape the
selected pack.

Both tools fail closed for missing/ambiguous projects, unreadable or malformed
registers, unsupported contract versions, path traversal, and absent IDs.
Advertised tool schemas are explicit and `additionalProperties: false`.

## End-to-end validation

The principal slice is:

```text
explicit project + synthetic task -> packaged Plugin MCP get_task_context
  -> compact capsule -> read_decision_audit stable full record
```

Tests assert normal responses do not contain full-register text, audit fetches
preserve authority fields, explicit project selection is mandatory even with
one pack, traversal and write-attempt mutations fail without filesystem
changes, and MCP serialization matches RT-01 byte/token accounting. Existing
Plugin smoke, packaging, contracts, and read-only tests remain green.

## Affected components and validation commands

- `plugins/context-library/mcp/context_library_server.py` and generated runtime
  integration;
- Plugin MCP read-only and traversal tests;
- synthetic temporary pack fixtures; and
- contracts/plugin documentation.

Validation commands are `PYTHONPATH=src poetry run pytest -q
tests/plugin/test_mcp_read_only.py tests/plugin/test_applicability_parity.py`,
`PYTHONPATH=src make plugin-check`, `PYTHONPATH=src make contracts-check`, and
`git diff --check`. All tests are offline and canonical-data read-only.

## Compatibility, risks, and review boundary

The tools are additive; existing MCP tools retain their schemas. The packaged
runtime must use the same Core-generated contract and resolver semantics, not
duplicate selector logic. A compact response is the only normal task-context
output; full records are on-demand. Risks are accidental full-register leakage
and an audit path becoming a write authority; negative tests must fail both.
Unresolved questions are whether audit responses should cap related records by
count or token budget, and which evidence fields require redaction in a future
private deployment. This immutable checkpoint contains no implementation and
requires fresh independent read-only review before autonomous approval.
