# Specification review: #9 / RB-05

Status: Specification-only checkpoint for independent review
Originating issue: #9 — Implement retrieval safety evaluator and sanity mutations
Dependencies: #5 / RB-01 and synthetic corpus from #6 (merged)
Authority: SPEC.md Section 16.6 and the accepted retrieval contracts

## Scope and non-goals

Implement an implementation-independent evaluator that compares one
retrieval-benchmark result against its task and declarative gold labels. It
will produce a machine-readable pass/fail safety result and a non-zero CLI
status for any safety violation.

Safety is a release gate, not a weighted score. The evaluator must fail for
one missed deterministically operative directive, promoted superseded,
non-authoritative, conflicting, or inapplicable guidance, hidden applicable
conflicts, false complete coverage, or silent operative truncation. It must
report judgment-required applicability separately from operative guidance.

This issue does not implement or tune a retriever, ranking, applicability
resolution, token accounting, provider-backed agent scoring, or a new
canonical write path. It does not average safety and efficiency into one
score.

## Proposed evaluator contract

The evaluator accepts version-1 task and gold objects plus one complete
RetrievalBenchmarkReport result. Bare RetrievalBenchmarkResult input is not
accepted because report-level identity is required. It compares:

- expected operative IDs against returned decision IDs;
- judgment-required IDs against a separate unresolved/judgment channel;
- excluded IDs and their structured reasons against returned guidance;
- gold conflict members against detected and missed conflict IDs;
- the result's coverage/truncation claim against the actual omission set; and
- report task/gold/tokenizer identity binding.

RB-05 adds a versioned evaluator-input sidecar containing a structured
returned_decisions list. Each entry contains a decision ID, a
gold-derived classification (operative, judgment-required, or excluded), and
an exclusion reason when applicable. The evaluator parses the exact
agent-visible JSON response, extracts its returned decision IDs, and requires
that set and order to agree with the sidecar before it applies the gold
classification. A self-reported CoverageReport cannot substitute for this
reconciliation. The sidecar is required input for RB-05 and is not added to
the version-1 RB-01 report schema; absence is a fail-closed evaluator error.

It emits a deterministic envelope containing evaluator version, task/result
identity, safety_passed, ordered failure codes, concrete missing/promoted/
hidden IDs, unresolved applicability IDs, and the checked coverage state.
Failure codes are closed and stable: missed-operative, promoted-excluded,
hidden-conflict, false-complete-coverage, silent-operative-truncation,
unreported-judgment, and identity-mismatch. No failure is downgraded because
another metric improved.

The evaluator derives safety from the declarative gold and returned result,
never from retriever implementation metadata. It must reject malformed
inputs rather than treating missing fields as safe.

## Affected components and boundaries

- src/context_library_core/ or scripts/ — pure evaluator and CLI boundary;
- evaluator-input sidecar model/fixture — structured returned decision
  metadata with an RB-05 evaluator version, without changing RB-01;
- contracts/fixtures/ — positive and deliberately failing result cases;
- tests/contracts/ or tests/integration/ — black-box evaluator and mutation
  tests; and
- benchmark documentation — failure codes and invocation.

The evaluator may reuse existing CoverageReport invariants but must not
silently trust a retriever's complete_coverage_claimed field. It recomputes
the relevant sets from the returned decision records and gold labels, then
checks that the reported coverage agrees.

No Manager, Maintainer mutation, Plugin implementation, dependency, migration,
or canonical data is in scope.

## Authority and public-data boundaries

Inputs are synthetic task/gold/corpus/report artifacts and temporary generated
packs only. Gold labels are benchmark truth, not publication authority or
canonical decisions. The evaluator is pure, offline, read-only, and performs
no network or filesystem writes. It must not import or call any retriever
under evaluation.

## Compatibility

The evaluator consumes RB-01 task, gold, and report contracts without changing
their schema families. Its versioned evaluator-input sidecar and output
envelope are benchmark diagnostic artifacts and must carry evaluator versions.
Adding a new failure code or changing pass/fail semantics requires a versioned
evaluator change and refreshed fixtures, not a silent reinterpretation of
historical results.

## Black-box and end-to-end validation

The incremental slice is:

    synthetic task/gold + baseline result
      -> evaluator CLI
      -> deterministic safety envelope and process status

Tests will assert:

- a valid result with complete operative recall and truthful coverage passes;
- omitting each operative directive fails with missed-operative;
- returning each superseded, excluded, conflicting, or inapplicable decision
  fails with promoted-excluded;
- hiding each applicable conflict fails with hidden-conflict;
- a false complete claim and silent operative truncation fail separately;
- unresolved applicability is reported separately and cannot become operative;
- report/task/gold/tokenizer identity mismatches fail closed;
- omitting a judgment-required decision from the unresolved/judgment channel
  fails with unreported-judgment;
- repeated evaluations are byte-identical and offline; and
- failure ordering and codes are deterministic.

Each mutation starts from a positive synthetic result and changes one safety
invariant, proving that the test would fail for plausible benchmark regressions.

## Validation commands

- focused evaluator and mutation tests;
- evaluator CLI positive and negative exit-status tests;
- existing contract and shared-fixture tests;
- poetry run ruff check on changed files; and
- git diff --check.

Provider-backed scoring is intentionally excluded.

## Risks, alternatives, and unresolved questions

- Reusing a result's self-reported coverage without recomputation would permit
  a retriever to declare an unsafe result safe; the evaluator explicitly
  recomputes the critical sets.
- Treating every excluded result as the same reason would lose diagnostics;
  the output retains decision IDs and gold exclusion reasons.
- A result can expose unresolved applicability in different field shapes;
  RB-05 will use the existing judgment-required decision channel and reject
  any unrecognized representation rather than infer safety.
- The evaluator must remain independent of each baseline so a shared bug
  cannot make all baselines pass.

## Review and approval boundary

This is an immutable specification-only checkpoint. Independent read-only
review must assess release-gate semantics, gold-label independence,
contract compatibility, authority/public-data boundaries, and black-box
mutation coverage before the Project Spec Gate may be autonomously set to
Approved. No implementation files are included in this checkpoint.
