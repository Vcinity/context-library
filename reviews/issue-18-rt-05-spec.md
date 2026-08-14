# Specification checkpoint: #18 / frozen retrieval acceptance validation

Status: specification-only checkpoint  
Originating issue: #18 — Validate operative-context retrieval against frozen acceptance targets  
Dependencies: #6–#10 benchmark foundation, plus #16 and #17, merged in
`9ddc856`

## Scope and non-goals

Run the frozen retrieval benchmark against the delivered task-context and
Plugin paths at the committed 10, 100, 1,000, and 10,000-record scales. Compare
the task-context mechanism with every frozen baseline using identical synthetic
corpora, tasks, budgets, serialization, and pinned reference tokenizer. Record
safety and efficiency results without changing targets or claiming model-wide
optimality.

This issue does not tune retrieval behavior, alter benchmark gold labels or
thresholds, add canonical data, weaken safety checks, or convert fixture data
into production evidence. Any failing safety target is reported as a failed
acceptance result, not hidden by a code or target change.

## Acceptance contract and evidence

The authoritative inputs are the frozen artifacts from #6–#10: corpus/generator
outputs, task and gold fixtures, baseline definitions, safety evaluator, runner,
and report schema. The acceptance report MUST separately show, for every scale and
baseline: operative recall, unsafe inclusion, conflict detection, coverage
truthfulness, agent-visible UTF-8 bytes/tokens, repeated tokens, tool calls,
and secondary local-resource diagnostics. It MUST identify numerator,
denominator, exclusions, tokenizer identity, serialization digest, and any
failure or limitation.

Safety gates precede efficiency claims: complete recall of current explicit
operative directives, no superseded/non-authoritative promotion, visible
applicable unresolved conflicts, and truthful complete/incomplete/truncated
coverage are mandatory. The report records whether predeclared token-reduction
and tool-call targets passed at each scale; it never revises those targets.

## End-to-end validation

The principal slice is:

```text
frozen corpus -> generated pack -> benchmark task -> delivered task-context /
Plugin MCP serialization -> frozen baselines -> safety evaluator -> report
```

Run the benchmark offline and assert deterministic repeated reports, exact
scale coverage, pinned tokenizer accounting, and concrete failure states for
malformed/unsupported fixtures. Cross-check one representative task through
Core, Maintainer, Manager, and generated Plugin paths where the existing parity
fixtures support it. Preserve the canonical-data read-only boundary.

## Affected artifacts and commands

- a new Issue-18-only task-context adapter or benchmark entry point and
  benchmark report fixtures;
- `scripts/run_retrieval_benchmark.py`, `benchmark_runner.py`,
  `retrieval_baselines.py`, and target fixtures remain unchanged and are only
  invoked/composed with by the adapter; baseline definitions and thresholds
  cannot change;
- `contracts/fixtures/` and generated schemas only if a validation gap is
  discovered (no gold-label changes);
- focused retrieval, safety, Plugin, and cross-component tests; and
- an inspectable benchmark report attached to the issue/PR.

Validation commands are `PYTHONPATH=src poetry run python
scripts/run_retrieval_benchmark.py --output <temporary-report>`, focused
retrieval/safety tests, `PYTHONPATH=src make contracts-check`,
`PYTHONPATH=src make plugin-check`, `git diff --check`, and the applicable root
smoke/check targets. All external/provider calls are prohibited; temporary
synthetic output is removed after evidence capture.

## Risks and unresolved questions

Risks are accidentally mixing scales, measuring a non-identical serialized
response, treating a secondary local metric as an acceptance gate, or
mistaking fixture evidence for production SLO evidence. The report must name
these limitations and preserve raw machine-readable output. There are no
unresolved contract questions; any environmental limitation is recorded as an
unmet validation item rather than inferred success. This checkpoint contains
no implementation.
