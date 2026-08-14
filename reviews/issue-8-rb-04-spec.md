# Specification review: #8 / RB-04

Status: Specification-only checkpoint for independent review
Originating issue: #8 — Implement retrieval baselines and reference token accounting
Dependencies: #5 / RB-01, #6 / RB-02, and #7 / RB-03 (merged)
Authority: SPEC.md Section 16.6 and the accepted retrieval benchmark contracts

## Scope and non-goals

Implement a deterministic, offline benchmark runner for three fair reference
baselines over the synthetic RB-02 cases and RB-03 generated packs:

1. full-register injection;
2. the current Plugin substring-search behavior; and
3. one documented simple lexical retrieval baseline.

Every baseline uses the same task, corpus, result limit, and exact
agent-visible serialization boundary. The runner emits the accepted
retrieval-benchmark-report contract with pinned token, byte, reduction,
repeated-token, tool-call, and secondary-resource measurements.

This issue does not implement semantic retrieval, applicability resolution,
ranking beyond the simple lexical baseline, MCP task-context tools, provider
calls, agent-quality evaluation, target-threshold selection, or a universal
model/tokenizer claim. It does not mutate or read the separately governed
canonical library.

## Applicable requirements and resolved measurement decisions

The reference tokenizer is pinned as a Poetry dependency and report identity:
tiktoken==0.9.0, encoding cl100k_base, with an explicit tokenizer revision
and accounting-method string. Token counts are calculated only from the exact
serialized UTF-8 response text that the baseline returns. The runner never
averages counts across tokenizers or models.

The shared response boundary is a canonical JSON object serialized with sorted
keys, UTF-8, and one trailing newline. It contains the project, task ID,
baseline ID, result-limit metadata, ordered decision records, and truthful
coverage/truncation fields. Full-register injection and search baselines
therefore differ only in selected content and their declared tool-call count,
not in serialization rules.

The Plugin baseline reproduces the current substring search semantics:
case-insensitive substring matching over the decision ID, subject, decision,
constraints, and metadata, in canonical register order, bounded by the same
result limit. The lexical baseline tokenizes the task summary/operation/path
signal into normalized terms, scores records by deterministic term overlap,
breaks ties by canonical decision ID, and reports only the selected records.
Neither baseline may remove a deterministically operative gold decision in a
way that is hidden by a complete-coverage claim.

Repeated tokens use the RB-01 definition: the token count of token occurrences
after the first occurrence of each token value, preserving sequence order.
Agent-directed tool-call count is explicit: full-register injection is zero,
while search baselines count one tool call per baseline invocation (one search
request producing one response containing all selected matches). Bytes,
raw tokens, reduction against a named baseline, repeated tokens, and local
latency/filesystem/index diagnostics remain separate report fields.

The existing RB-01 report places latency, filesystem reads, and index size in
one SecondaryResourceMeasurements object at report level. RB-04 treats these
as run-level aggregate diagnostics over the identical benchmark invocation,
not as baseline-specific comparison values and not as substitutes for the
per-result token/byte/tool-call fields. The implementation must reuse the
existing reduction invariant (or remove an unused duplicate helper) rather
than maintain two independent reduction calculations.

## Contracts and affected components

The runner accepts a version-1 task, gold, corpus/pack path, baseline set,
result limit, and pinned tokenizer configuration. It emits one versioned
report containing one result per baseline and a stable report/corpus revision.
Reports are rejected if task/gold identity, tokenizer identity, response
digest, bytes, or reduction fields do not match the supplied inputs.

Likely files:

- src/context_library_core/ or scripts/ for pure baseline and accounting
  functions plus the runner boundary;
- tests/ for black-box baseline/report tests;
- contracts/fixtures/ for deterministic baseline report fixtures and
  negative accounting mutations;
- pyproject.toml and poetry.lock for the single pinned tokenizer; and
- benchmark documentation for invocation and limitations.

No Manager, Maintainer mutation, Plugin write path, migration, or canonical
library file is in scope.

## Authority, provenance, and public-data boundaries

Only synthetic RB-02/RB-03 fixtures and temporary generated packs are inputs.
The runner accepts explicit paths, rejects paths that escape the caller's
temporary benchmark root, and performs no network or canonical writes. Gold
labels describe benchmark truth; they do not authorize publication or
strengthen provenance. Baseline outputs and reports are diagnostic artifacts,
not canonical decisions.

## Compatibility and migration

The runner consumes RB-01 task, gold, and report models without changing their
schema families. The pinned tokenizer identity is report data and remains
separate from future tokenizer/model runs. Any serialization or accounting
change requires a new serialization revision and refreshed deterministic
fixtures; it must not silently rebaseline historical results.

## Black-box and end-to-end validation

The incremental end-to-end slice is:

    synthetic task/gold + generated pack
      -> selected baseline runner
      -> exact serialized response
      -> pinned-token report

Tests will run all three baselines at 10, 100, 1,000, and 10,000 records and
assert:

- identical task, pack, result limit, and serialization inputs across
  baselines;
- deterministic report bytes on repeated runs;
- exact UTF-8 byte counts and tokenizer counts from stored response text;
- correct repeated-token and tool-call counts;
- valid named-baseline reduction fields without model averaging;
- deterministic baseline ordering/ties and explicit coverage/truncation states;
- malformed response/accounting/tokenizer mutations fail closed; and
- no network access or canonical mutation occurs.

The tests include a sanity mutation that changes one serialized response after
measurement and proves the report validator fails. They also assert that a
result-limit omission of operative decisions is reported as incomplete or
truncated rather than complete.

## Validation commands

- focused benchmark baseline/report tests at every RB-03 scale;
- poetry run pytest -q for the deterministic suite;
- poetry run ruff check on changed code and tests;
- make contracts-check;
- tokenizer lock/dependency consistency; and
- git diff --check.

Provider-backed agent evaluation and efficiency-target claims are intentionally
omitted; this issue establishes comparable reference measurements.

## Risks, alternatives, and unresolved questions

- Tokenizer package behavior can change across releases; the pinned Poetry
  version, encoding name, and report identity must travel together.
- Importing Plugin implementation code directly could violate package
  isolation; the substring baseline should mirror the documented observable
  semantics through a small shared test boundary.
- A lexical baseline can return relevant-looking but non-operative content;
  report safety fields must remain authoritative and must not claim complete
  coverage from ranking alone.
- Full-register responses may exceed practical agent budgets; the runner
  records that result honestly and does not change the frozen task or target.

## Review and approval boundary

This is an immutable specification-only checkpoint. Independent read-only
review must assess tokenizer and serialization correctness, baseline fairness,
contract compatibility, authority/public-data boundaries, and black-box
coverage before the Project Spec Gate may be autonomously set to Approved.
No implementation files are included in this checkpoint.
