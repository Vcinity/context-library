# Specification review: #10 / RB-06

Status: Specification-only checkpoint for independent review  
Originating issue: #10 — Add retrieval benchmark runner, reports, and frozen acceptance targets  
Dependencies: #7 / RB-03, #8 / RB-04, and #9 / RB-05 (all merged)

## Scope and non-goals

Implement the reproducible offline benchmark entry point that runs the
authored RB-02 corpus and the RB-03 generated scales through every RB-04
baseline and the RB-05 safety evaluator. It emits deterministic per-case,
per-baseline machine-readable reports plus a human-readable summary, and
enforces the frozen efficiency targets below.

This issue does not implement task-context retrieval, applicability metadata,
ranking, provider-backed agent scoring, canonical writes, or threshold tuning
against any RT implementation result. Provider evaluation remains an explicit,
separate optional workflow and cannot replace offline safety evaluation.

## Applicable requirements

The design implements SPEC.md Section 16.6: exact agent-visible token
accounting, named tokenizer identity, operative recall, unsafe inclusion,
conflicts, truthful completeness, explicit truncation, tool calls, and local
diagnostics. Safety is an independent release gate; efficiency cannot offset a
failed RB-05 result. The root Makefile receives a deterministic
`retrieval-benchmark` target, while ordinary unit tests do not invoke the
full scale matrix.

## Proposed contract and design

Add a versioned, public `retrieval-benchmark-targets-v1` artifact containing:

- target revision and corpus/generator revisions;
- the pinned reference tokenizer (`tiktoken` `0.9.0`, `cl100k_base`);
- required scales 10, 100, 1000, and 10000;
- maximum one agent-directed tool call for a baseline and zero for the
  full-register baseline;
- minimum 20% token reduction for substring and lexical baselines against
  full-register at each generated scale, measured only when the safety gate
  passes; and
- mandatory safety pass, operative recall of 100%, no unsafe inclusion,
  hidden conflict, false complete claim, or silent truncation.

The efficiency values are frozen in this checkpoint before any RT result is
examined. They are acceptance targets, not claims that a baseline currently
passes them. The runner reports failures without changing the artifact.

The runner will:

1. load and validate each RB-02 task/gold case and synthesize the local
   register from its sanitized decisions;
2. generate each RB-03 scale into a temporary owned directory using the
   pinned seed and configuration;
3. run `full-register`, `plugin-substring`, and `lexical` through the shared
   RB-04 accounting boundary;
4. build the required RB-05 evaluator sidecar from the exact returned JSON
   IDs and declarative gold labels, then evaluate every result;
5. write stable JSON reports containing schema revisions, tokenizer identity,
   baseline metrics, safety envelopes, target checks, and digests, plus a
   concise Markdown summary; and
6. return non-zero if any safety or frozen efficiency target fails.

The command accepts explicit corpus, seed, scale, and output-directory
arguments. The Make target supplies the committed synthetic corpus, pinned
seed, all required scales, and a temporary output directory. Output paths are
never the canonical library and the command performs no network access.
Repeated runs with the same inputs must be byte-identical, including report
ordering, generated-pack digests, and Markdown formatting.

## Affected components and boundaries

- `src/context_library_core/benchmark_runner.py` — deterministic orchestration
  and target evaluation;
- `scripts/run_retrieval_benchmark.py` — stable CLI/file boundary;
- `contracts/fixtures/retrieval-benchmark-targets-v1.json` and generated
  schema documentation — frozen target artifact;
- `Makefile` and `contracts/README.md` — reproduction and interpretation;
- `tests/contracts/` and `tests/integration/` — report and exit-status tests.

No Manager, Maintainer, Plugin runtime, dependency, migration, or canonical
data changes are in scope.

## Authority, provenance, and public-data hygiene

All committed inputs and expected outputs are synthetic or sanitized. Gold
labels remain independent benchmark truth. The runner may read temporary
generated packs but must not import a retriever under evaluation, infer
authority from ranking, or mutate canonical data. Reports distinguish offline
deterministic evidence from optional provider-backed evaluation and must not
claim universal tokenizer/model optimality.

## Compatibility and migration

RB-01 report and RB-05 evaluator contracts remain unchanged. The targets and
runner report are new versioned benchmark artifacts. Changing a target,
serialization, tokenizer, or pass/fail rule requires a new target/report
revision; historical reports remain interpretable under their recorded
revisions. `make retrieval-benchmark` is additive to existing root commands.

## Black-box and end-to-end validation

The principal slice is:

    committed corpus + frozen targets
      -> make retrieval-benchmark / runner CLI
      -> deterministic JSON and Markdown reports
      -> exit 0 for an all-safe matrix, non-zero for a mutated unsafe result

Tests will assert exact report bytes across repeated runs, all three baselines
and all four scales are present, tokenizer and revision fields are populated,
target failures are concrete, and an injected omitted-operative or false-
complete mutation returns non-zero without changing the frozen target file.
Focused tests cover malformed target/report inputs, output path safety, and
provider evaluation being excluded from offline status.

## Validation commands

- focused benchmark-runner, target, and CLI tests;
- `make retrieval-benchmark` twice with byte-identical outputs;
- existing RB-03, RB-04, and RB-05 contract/evaluator tests;
- `poetry run ruff check` and format checks on changed files;
- `make contracts-check` if generated schemas are changed; and
- `git diff --check`.

## Risks, alternatives, and unresolved questions

- A runner that trusts self-reported coverage could hide unsafe retrieval;
  RB-05 remains the independent gate and is always invoked.
- Scale generation can make normal tests slow; full-scale execution is kept in
  the explicit Make target while tests use a small deterministic matrix and a
  CLI fake/mutation boundary.
- The 20% efficiency target may fail for a baseline at a specific scale; that
  is intentionally visible evidence, not a reason to revise the target.
- The authored RB-02 corpus and generated RB-03 scale packs have different
  gold shapes; the runner normalizes both into the existing task/gold/report
  contracts without copying canonical records.

## Review and approval boundary

This is an immutable specification-only checkpoint. Fresh independent
read-only review must assess scope control, target independence, report and
CLI compatibility, safety/authority boundaries, public-data hygiene, and
black-box coverage before the autonomous Spec Gate is set to Approved.
Implementation files are not included in this checkpoint.
