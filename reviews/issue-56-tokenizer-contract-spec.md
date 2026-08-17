# Issue #56 specification review: complete `get_task_context` tokenizer contract

Issue: https://github.com/Vcinity/context-library/issues/56
Status: Draft specification checkpoint; implementation is not approved by this artifact alone.

## Scope

Update the Plugin MCP `get_task_context` tool advertisement so its nested
`tokenizer` schema exactly describes the request contract already enforced by
the generated runtime: required non-empty string fields `name`, `version`,
`vocabulary_revision`, and `accounting_method`, plus optional `pinned` with a
constant value of `true`. Add documentation examples and a black-box contract
test that compares the advertised schema with handler validation.

## Non-goals

- Changing tokenizer support, accounting semantics, or budget fail-closed
  behavior; those belong to issue #51.
- Adding a second tokenizer schema source or weakening generated-contract
  validation.
- Changing canonical data, Manager behavior, or deployment configuration.

## Applicable requirements

- `SPEC.md` Sections 6, 7, and 8 require a single source of truth for shared
  contracts and compatible versioned interfaces.
- `SPEC.md` Section 4.3 keeps the Plugin read-only.
- Issue #56 requires complete schema publication, valid and invalid examples,
  and deterministic server-level drift coverage.
- `plugins/context-library/generated/core_runtime.py` is generated from the
  contract generator and already validates the tokenizer fields and
  `pinned=true` constraint.

## Proposed contract and design

Define the MCP `inputSchema.properties.tokenizer` object with:

- `name`, `version`, `vocabulary_revision`, and `accounting_method` as string
  properties;
- `pinned` as a boolean property with `const: true` and a default of `true`;
- `required` containing the four identity fields; and
- `additionalProperties: false`.

The handler remains authoritative for runtime validation. The contract test
will construct valid and invalid requests from the advertised schema and
assert that both the schema validator and handler agree for missing fields,
unknown fields, and `pinned=false`. A generated-contract check will ensure the
runtime validator remains synchronized with the declared tokenizer identity.

## Affected files and components

- `plugins/context-library/mcp/context_library_server.py`: MCP schema
  advertisement only.
- `plugins/context-library/skills/context-library/references/usage.md` and/or
  `plugins/context-library/skills/context-library/SKILL.md`: valid and invalid
  request examples.
- `tests/plugin/test_mcp_read_only.py` or a focused sibling test: server-level
  schema/handler agreement.
- Generated artifacts only if the existing generator requires regeneration;
  no hand-edited generated source.

## Test strategy

1. Run a focused Plugin test through the imported server boundary and inspect
   `tools/list` output.
2. Validate one complete request and reject the three required invalid cases
   through the advertised schema and handler.
3. Run `make contracts-check` to prove generated runtime drift checks remain
   deterministic.
4. Run `make plugin-check` and the applicable root test/check targets after
   implementation.

## Validation commands

```sh
poetry run pytest tests/plugin/test_mcp_read_only.py
make contracts-check
make plugin-check
make test
git diff --check
```

## Risks and mitigations

- A schema-only update could diverge from generated validation; the test must
  exercise both boundaries with the same cases.
- Documentation examples could become stale when the contract changes; the
  examples should use the exact field names and be covered by a deterministic
  documentation/contract assertion where existing conventions permit.
- `const` support must remain compatible with the MCP JSON Schema consumers;
  retain the existing `additionalProperties: false` and use standard JSON
  Schema vocabulary already accepted by repository validators.

## Unresolved questions

- Whether the repository's current contract-test helpers already expose a
  reusable JSON Schema validator; implementation should reuse it if present.
- Whether the generated runtime's tokenizer definition can be imported
  directly for the drift assertion without coupling the Plugin to Manager or
  Maintainer code.

## Independent review record

Pending fresh read-only Claude Code review in plan mode. Required reviewer:
`haiku`, medium effort, because this is a narrow stable MCP schema contract
with no authority or deployment behavior change.
