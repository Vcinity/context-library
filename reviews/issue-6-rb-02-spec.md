# Specification review: #6 / RB-02

Status: Specification-only checkpoint for independent review
Originating issue: #6 — Build starter and adversarial retrieval benchmark corpora
Dependency: #5 / RB-01 (merged)
Authority: `SPEC.md` Section 16.6 and the accepted RB-01 contracts

## Scope and non-goals

Create a compact, synthetic public benchmark corpus and deterministic
validation for retrieval safety cases. The corpus will contain authored task,
gold-label, and synthetic decision data that exercise the RB-01 contract while
keeping labels declarative and independent of any retrieval implementation.

This issue does not implement retrieval, applicability evaluation, ranking,
benchmark execution, scale generation, tokenizer accounting, Plugin or
Manager retrieval paths, canonical-library access, or provider-backed agent
evaluation. It does not copy canonical records or task descriptions.

## Applicable requirements

The corpus MUST include a sanitized starter layer and adversarial synthetic
cases covering lexical mismatch, synonyms, plausible distractors,
supersession, conflicts, global and scoped decisions, unresolved
applicability, inadequate token budgets, and tasks with no applicable context.
Every task and gold document MUST conform to RB-01, use stable identifiers,
and remain independently reviewable. The set MUST exercise operative,
judgment-required, excluded, and conflicting classifications. Validation MUST
be deterministic, offline, and fail closed for malformed or internally
inconsistent gold data.

## Corpus design

The authored source will be a small set of JSON fixtures under
`contracts/fixtures/`, using only synthetic project names, decision text,
paths, and identities. Each case is a self-contained scenario with:

- a stable corpus/case identifier and revision;
- one RB-01 task fixture with explicit operation, scopes, a completeness signal
  (`complete_coverage_possible`), and decision references;
- a matching RB-01 gold fixture with operative, judgment-required, excluded,
  and conflict classifications; and
- synthetic decision records sufficient for a parser/evaluator fixture to
  inspect supersession, scope, provenance, and conflict relationships.

Cases will be organized by safety dimension rather than by retrieval method.
The starter layer will include straightforward explicit directives and a
no-applicable-context case. The adversarial layer will include at least:

1. lexical mismatch and synonym wording;
2. a plausible but irrelevant distractor;
3. a supersession chain where only the current decision is operative;
4. an unresolved conflict whose members remain visible;
5. global versus project-scoped decisions;
6. unresolved applicability requiring judgment;
7. an insufficient-budget task represented by an operative set larger than a
   later evaluator's constrained token budget can return; the budget and
   truncation assertion belong to the out-of-scope benchmark-execution issue;
   and
8. a task with no applicable context.

Decision IDs, conflict IDs, and task/gold references are unique within the
corpus and checked for unknown or cross-case references. Gold labels never
depend on output from a retriever under test.

## Validation contract

Add a deterministic corpus validator at a stable repository boundary (a
command or test-invoked module) that parses every committed case through the
RB-01 models and checks cross-fixture invariants. It MUST reject:

- invalid task or gold schemas and unsupported versions;
- duplicate case, task, gold, decision, or conflict identifiers;
- unknown classification references;
- overlapping operative, judgment-required, and excluded classifications;
- conflicts whose members are absent or not represented consistently;
- inconsistent scope, supersession, or applicability metadata; and
- a gold/task pair whose identity or revision binding does not match.

The validator must read only repository-local fixture paths supplied by the
caller, never discover or mutate a canonical library, and must return a
non-zero status on failure. Negative tests will mutate synthetic fixtures in
temporary directories and assert concrete failure messages or error classes.

## Affected components and likely files

- `contracts/fixtures/` — authored synthetic corpus cases and negative copies;
- `contracts/README.md` — corpus layout and validation command;
- `src/context_library_core/` — a small corpus-validation module only if the
  existing contract layer has no suitable public boundary;
- `scripts/` — deterministic validator entry point if a CLI boundary is the
  least coupled option; and
- `tests/contracts/` or `tests/integration/` — fixture validation, mutation,
  and cross-case coverage tests.

No canonical data, runtime configuration, retrieval implementation, generated
Plugin code, or external network dependency is in scope.

## Authority, provenance, and public-data boundaries

The benchmark corpus is synthetic test data, not canonical Context Library
content. Gold labels are benchmark judgments and must not be treated as
publication authority or copied into canonical packs. Any provenance fields
are synthetic identifiers and explanatory metadata only. The corpus and
validator must remain public-repository safe: no private URLs, organization or
customer names, credentials, chat transcripts, or harvested source content.

## Compatibility and migration

RB-02 consumes the merged RB-01 version-1 task and gold contracts without
changing them. New corpus metadata must use additive, versioned fields or
remain outside the contract payload; any contract change would be a material
scope change requiring a refreshed specification checkpoint. Existing
single-project and canonical data paths remain untouched.

## Black-box and end-to-end test strategy

The incremental end-to-end slice is:

```text
synthetic authored corpus -> validator CLI/module -> parsed RB-01 task/gold
```

Tests will invoke the stable validator boundary against a temporary copy of
the committed synthetic corpus and assert:

- all positive starter and adversarial cases validate offline;
- the required safety dimensions are represented by concrete cases;
- malformed, unsupported, duplicate, overlap, dangling-reference, and
  inconsistent-scope mutations fail non-zero with a useful diagnostic;
- repeated validation produces the same result and does not modify inputs; and
- no canonical path or network access is required.

The tests will also assert that classifications remain declarative by
checking that validation succeeds without importing a retrieval or ranking
module. Focused model tests may supplement diagnosis, but the corpus boundary
is the principal acceptance evidence.

## Validation commands

- `poetry run pytest -q tests/contracts tests/integration/test_shared_contract_fixtures.py`
- the new focused corpus-validator test command;
- `make contracts-check`;
- `poetry run ruff check` on changed Python files; and
- `git diff --check`.

All committed tests must be deterministic, offline, and use temporary
synthetic copies for mutation. No provider-backed evaluation is required.

## Risks, alternatives, and unresolved questions

- A large hand-authored corpus would duplicate data and make review harder;
  compact cases with explicit dimensions are preferred.
- Embedding a new contract family would broaden RB-02; the default is to
  compose the accepted RB-01 task/gold records and keep corpus metadata
  separate.
- The later scale-generator issue (#7) will create 10/100/1,000/10,000-record
  variants; RB-02 must not preempt that generator or freeze its distribution.
- The exact validator CLI name is an implementation detail unless existing
  Makefile conventions make a stable command preferable; its exit status and
  diagnostics are the contract.

## Review and approval boundary

This is an immutable, specification-only checkpoint. Independent read-only
review must assess scope control, RB-01 compatibility, safety-case adequacy,
authority and public-data boundaries, and black-box validation coverage before
the Project `Spec Gate` may be autonomously set to `Approved`. No
implementation files are included in this checkpoint.
