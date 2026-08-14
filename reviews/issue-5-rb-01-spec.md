# Specification review: #5 / RB-01

Status: Revised after `Changes requested`; ready for remote review
Originating issue: #5 — Define context-retrieval benchmark task and report contracts
Authority: `SPEC.md` Sections 8, 9, 12, and 16.6

## Scope and non-goals

Define versioned, deterministic, implementation-independent contracts for
retrieval benchmark tasks, declarative gold classifications, and
machine-readable reports. The contracts support later corpus, baseline,
evaluator, and retrieval work while keeping benchmark labels independent of
the retriever under test.

This issue does not implement retrieval, applicability selectors, a benchmark
runner, tokenizer selection logic, provider-backed evaluation, or canonical
data access. Fixtures contain only synthetic public data.

## Applicable requirements

The task contract MUST identify a stable task, its task and gold revisions,
summary, operation, repository paths or scopes, operative decisions,
judgment-required decisions, excluded decisions, applicable conflicts, and
whether complete coverage is possible. A report MUST separate safety,
coverage, exact agent-visible accounting, repeated-token accounting,
tool-call accounting, and secondary diagnostics. Unsupported versions and
malformed or internally inconsistent records MUST fail closed.

## Resolved contract decisions

### Task and gold binding

The task carries `task_id`, `task_revision`, and `gold_revision`. The gold
binding also carries a content digest (`gold_sha256`) calculated over the
canonical serialized gold-label document. A report repeats `task_id`,
`task_revision`, `gold_revision`, and `gold_sha256`; a report is comparable
only when all four values match the task/gold pair supplied to the run.
`corpus_revision` identifies the synthetic corpus input separately.

### Gold classifications and safety references

Decision IDs are disjoint across `expected_operative_decision_ids`,
`judgment_required_decision_ids`, and `excluded_decision_ids`. Exclusions are
structured records with a decision ID and a reason from a closed vocabulary:
`non-authoritative`, `superseded`, `out-of-scope`, `inapplicable`,
`duplicate`, or `other-reviewed`. A conflict reference identifies a stable
`conflict_id` and its member decision IDs. Unknown decision or conflict
references are invalid; the evaluator must not silently discard them.

### Completeness and truncation

`coverage.complete_coverage_possible` is true only when the task's complete
operative set is available to the run and the task's declared constraints do
not make completeness unknowable. Its `basis` is a closed value that must
agree with the boolean. A report may claim `complete` only when no operative
decision is omitted, no explicit truncation occurred, and all required gold
references are comparable. Otherwise it MUST claim `incomplete`.

Truncation is explicit: `truncated`, an omission list for operative decision
IDs, and a closed reason (`none`, `token-budget`, `result-limit`, or
`serialization-limit`). `truncated=false` requires reason `none` and an empty
omission list; `truncated=true` requires a non-`none` reason. Omitted
operative decisions cannot coexist with a complete claim or perfect recall.

### Token accounting

Each report declares one report-wide pinned reference tokenizer name and
version. It does not select multiple tokenizers or average across models.
Every baseline result stores the exact serialized agent-visible response,
its UTF-8 byte count, and its reference-token count. The byte count MUST equal
the UTF-8 length of the stored response; token counts are measured from that
same response under the report-wide tokenizer.

Repeated tokens are counted deterministically as the token count of the
response-token sequence after removing the first occurrence of each token
value, preserving sequence order. The result also records the deterministic
agent-directed tool-call count.

Relative reduction is tied to an explicitly named baseline result in the same
report. For a result with baseline `B`, it is
`1 - result.agent_visible_tokens / B.agent_visible_tokens`, with the
zero-token denominator rejected. Results without a comparison baseline leave
both the baseline reference and reduction absent. A report rejects unknown
baseline references and duplicate baseline IDs.

### Exact response representation

The response field is the exact UTF-8 serialized text delivered to the agent
at the public boundary, not a reconstructed summary or an internal object.
The contract records a serialization revision and a digest of the UTF-8
bytes. Diagnostics such as filesystem reads, latency, index size, and local
resource counters are separate fields and cannot substitute for the exact
response or its accounting.

## Proposed artifact shape

The implementation may choose names consistent with existing Pydantic
contracts, but the version-1 shape contains these logical groups:

- task identity and gold binding;
- task operation and repository scopes;
- operative, judgment-required, excluded, and conflict classifications;
- coverage basis;
- report identity, corpus revision, and repeated task/gold binding;
- report-wide tokenizer metadata;
- per-baseline exact response, digest, bytes, tokens, repeated tokens,
  tool calls, recall, safety exclusions, missed conflicts, coverage claim,
  truncation, and optional reduction reference; and
- secondary local diagnostics.

All objects reject unknown fields and unsupported schema versions. Contract
serialization uses the repository's existing alias and JSON-schema
generation conventions.

## Affected files and boundaries

Potential implementation locations are:

- `src/context_library_core/contracts.py` for authoritative models and
  invariants;
- `scripts/generate_contracts.py` for generated schema registration;
- `contracts/schemas/` for generated version-1 schemas;
- `contracts/fixtures/` for synthetic positive, malformed, and unsupported
  versions; and
- `tests/contracts/` for black-box fixture and serialization assertions.

No retrieval, applicability, Plugin, Manager, Maintainer, dependency, or
runtime configuration file is in scope for RB-01.

## Validation and end-to-end slice

The end-to-end slice is: synthetic task/gold fixture → contract parser and
generated schema → synthetic report fixture → report parser and exact
serialized-response accounting fields. It remains deterministic and offline.

Required evidence:

1. positive task and report fixtures validate;
2. malformed fixtures fail for overlapping classifications, invalid
   references, inconsistent coverage/truncation, incomparable revisions,
   invalid byte/digest/token accounting, and unknown fields;
3. unsupported schema versions fail;
4. mutation tests alter each critical fixture invariant and assert rejection;
5. generated schemas match the authoritative models; and
6. `make contracts-check` and `git diff --check` pass.

The black-box fixture tests are the principal evidence. Focused model tests
may supplement diagnosis but cannot replace serialized fixture and schema
validation.

## Risks and unresolved questions

- The reference tokenizer package and pinned version belong to the later
  baseline issue; RB-01 records its identity and does not add the dependency.
- Exact response serialization must be implemented at one shared public
  boundary by the later benchmark work; this contract forbids substituting
  internal representations.
- Existing provisional implementation files, if present in another worktree,
  are evidence only and must be reconciled against these decisions after
  approval. They are not approval or part of this planning artifact.

## Approval boundary

This artifact is planning-only. No implementation code, tests, dependencies,
generated contracts, migrations, or runtime configuration are included. The
Project `Spec Gate` must remain `Awaiting approval` until a human explicitly
sets it to `Approved`.
