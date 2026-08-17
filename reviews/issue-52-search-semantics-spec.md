# Issue #52 specification review: deterministic multi-term decision search

Issue: https://github.com/Vcinity/context-library/issues/52
Status: Draft specification checkpoint; implementation is not approved by this artifact alone.

## Scope

Define and implement one deterministic agent-facing `search_decisions`
contract for compound queries. Use normalized lexical term matching with stable
ranking and tie-breaking, while preserving exact-substring behavior as a
stronger match signal. Return a truthful structured diagnostic when no exact
match exists but term matches do, and a distinct no-match result when nothing
matches.

## Non-goals

- Semantic/model-assisted search, fuzzy embeddings, or external indexes.
- Canonical writes or changes to supersession/applicability authority.
- Changing the separate retrieval benchmark baseline contracts.
- Hiding superseded or inapplicable records without an explicit documented
  filter policy.

## Applicable requirements

- `SPEC.md` Sections 4.3, 6, 8, and 12 require read-only Plugin behavior,
  deterministic shared parsing, and truthful retrieval coverage.
- Issue #52 requires a documented exact/term contract, deterministic ordering,
  tests for exact/compound/partial/superseded/inapplicable/no-match cases, and
  no silent absence claim.
- The MCP server's `search_decisions` is the public boundary; canonical parsing
  remains owned by the generated Core read model.

## Proposed contract and design

Normalize query terms with one shared deterministic lowercase lexical token
helper extracted into `src/context_library_core/canonical.py`, which is already
part of the generated Plugin source digest; the retrieval baseline and Plugin
MCP search must both use that helper. Score each decision by: exact
complete-substring match first; then number
of distinct query terms present in the searchable decision fields; then stable
decision-register order and `decision_id` as a final tie-break. Return the
existing match fields plus machine-readable `match_mode`, `matched_terms`, and
`diagnostic` values. A compound query with term matches but no exact substring
must say that term matching was used; a true no-match returns an empty list and
`diagnostic=no-match`. Preserve all parsed records and make superseded/
applicability state visible rather than silently claiming active authority.

Keep the result deterministic, bounded by `max_results`, read-only, and
backward-compatible for exact single-substring queries. Document whether
ordering is register order for exact matches and score/order for lexical
fallback.

## Affected files and components

- `src/context_library_core/canonical.py` and
  `scripts/generate_plugin_runtime.py`: shared lexical token helper and
  deterministic generated-runtime inclusion.
- `plugins/context-library/mcp/context_library_server.py`: search algorithm and
  response contract/schema.
- `plugins/context-library/skills/context-library/references/usage.md`,
  `SKILL.md`, and Plugin README: query semantics/examples.
- `tests/plugin/test_mcp_read_only.py` and packaged MCP smoke/subprocess tests:
  black-box exact, compound, partial, superseded/inapplicable, and no-match
  behavior.
- Generated read-only Core only if an existing shared lexical helper is
  extended; do not duplicate authoritative parsing.

## Test strategy

1. Add synthetic register cases for exact subject, compound terms, lexical
   partials, superseded records, inapplicable records, true no-match, stable
   ties, and max-result truncation.
2. Assert response diagnostics distinguish exact, lexical fallback, and no
   match and include concrete matched terms.
3. Run repeated calls and packaged MCP subprocess calls to prove byte-stable
   ordering and read-only canonical preservation.

## Validation commands

```sh
poetry run pytest tests/plugin/test_mcp_read_only.py
make plugin-check
make test
git diff --check
```

## Risks and mitigations

- Adding lexical fallback can change result ordering; document exact ordering
  and lock it with black-box tests.
- Returning superseded/inapplicable matches could be misread as operative;
  retain provenance/state fields or explicit diagnostic labels.
- Query tokenization must remain local and deterministic; avoid external
  language services or fuzzy scoring.

## Unresolved questions

- The response MUST carry a named `schema` and `schema_version` under SPEC
  §7.3 and §8.1 (for example, `context-library/search-decisions-response`,
  version 1), additive to the existing `matches` field. The implementation
  must decide only the exact field names and envelope placement; it cannot
  ship an unversioned diagnostic payload.
- Whether all query terms must match for lexical fallback or partial coverage
  should be returned with a coverage score; the implementation must choose one
  and document it before code changes.
- Whether existing README inventory work in #53 should own the final tool
  documentation section, requiring coordination without merging scopes.

## Independent review record

Pending fresh read-only Claude Code review in plan mode. Required reviewer:
`haiku`, medium effort, because this is a narrow deterministic read/query
contract with no authority or deployment behavior change.
