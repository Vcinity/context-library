# Issue #51 specification review: fail closed on unverified token accounting

Issue: https://github.com/Vcinity/context-library/issues/51
Status: Draft specification checkpoint; implementation is not approved by this artifact alone.

## Scope

Make `get_task_context` fail closed when the requested pinned tokenizer cannot
be verified by the packaged runtime. The selected contract is rejection of
unsupported or unavailable tokenizer identities before a response is
rendered. Preserve deterministic truncation for the supported `tiktoken`
identity and expose truthful verified accounting in the response and audit
surfaces.

## Non-goals

- Adding support for new tokenizer implementations or downloading model data.
- Changing the tokenizer request shape established by issue #56.
- Altering canonical records, Manager policy, projection writes, or provider
  behavior.
- Treating synthetic fixtures as production SLO evidence.

## Applicable requirements

- `SPEC.md` Sections 4.3, 6, 7, and 8 require a read-only Plugin, inward
  dependency direction, versioned contracts, and one shared contract source.
- `SPEC.md` Section 12 requires truthful coverage and safe failure behavior.
- Issue #51 requires no `complete=true` with zero tokens for non-empty content
  when accounting is unverified, deterministic over-budget behavior, and
  coverage of supported, unavailable, malformed, and unsupported identities.
- Issue #56's merged tokenizer schema is the request-boundary prerequisite.

## Proposed contract and design

Reject a request with a structured MCP error when the tokenizer is malformed,
unpinned, unsupported, or unavailable. The only verified identity remains the
packaged `tiktoken` `0.9.0` / `cl100k_base` identity with the documented
accounting method. A valid supported request computes the token count using
the packaged encoder before applying the budget; if over budget, it returns a
deterministic incomplete/truncated result with omitted IDs and a truthful
`budget_status=verified`. No unverified result is returned.

Keep the response and generated contract fields explicit: `budget_status`
must distinguish verified results, `token_count` must describe the returned
capsule, and `complete` must be false whenever content is omitted. Error
messages must identify the unsupported condition without exposing canonical
content or credentials.

## Affected files and components

- `src/context_library_core/task_context.py`: authoritative rendering and
  response invariants.
- `scripts/generate_plugin_runtime.py`: generated read-only implementation.
- `plugins/context-library/generated/core_runtime.py`: regenerated artifact,
  never hand-edited.
- `plugins/context-library/mcp/context_library_server.py`: structured MCP
  error boundary if needed.
- `tests/contracts/test_task_context.py`, `tests/plugin/test_mcp_read_only.py`,
  and packaged MCP subprocess tests: black-box supported/unsupported,
  malformed, unavailable, and over-budget cases.
- Plugin skill/usage documentation and generated response schema as needed.

## Test strategy

1. Add contract-level tests for supported accounting, malformed identity,
   unsupported identity, unavailable encoder, and over-budget content.
2. Add Plugin MCP tests against the packaged/generated runtime and subprocess
   boundary, asserting structured errors and no false complete claim.
3. Assert deterministic output across repeated supported over-budget requests,
   including omitted decision IDs and budget status.
4. Run generated contract checks and the complete applicable Plugin/Core test
   surfaces.

## Validation commands

```sh
poetry run pytest tests/contracts/test_task_context.py tests/plugin/test_mcp_read_only.py
make contracts-check
make plugin-check
make test
git diff --check
```

## Risks and mitigations

- Existing callers may rely on `unverified` responses; document the breaking
  fail-closed behavior and test actionable errors.
- Core and generated Plugin behavior can drift; regenerate artifacts and test
  both the source and packaged boundaries.
- A missing tokenizer dependency must not be mistaken for an empty capsule;
  isolate dependency availability in a deterministic validation path.
- The scope must not expand into new tokenizer support; reject and document
  unsupported identities instead.

## Unresolved questions

- Whether the unavailable case should use a distinct machine-readable error
  code or a stable message family already used by MCP errors.
- Whether response schema changes are required beyond documenting that
  `unverified` is no longer a successful response state.
- Whether the existing generated runtime helper or Core renderer should own
  the supported-identity predicate; implementation must preserve dependency
  direction and avoid duplicated policy.

## Independent review record

Pending fresh read-only Claude Code review in plan mode. Required reviewer:
`sonnet`, medium effort, because this is a shared Core/Plugin contract and
fail-closed safety behavior.
