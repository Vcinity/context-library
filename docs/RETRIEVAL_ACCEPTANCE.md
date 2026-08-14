# Retrieval acceptance evidence

Issue #18 has a bounded, offline-only entry point for comparing the delivered
task-context resolver with the frozen retrieval baselines:

```sh
PYTHONPATH=src poetry run python scripts/run_issue18_retrieval.py \
  --output /tmp/context-library-issue18-report
```

The adapter generates the committed 10, 100, 1,000, and 10,000-record packs
once per scale, derives one synthetic task and gold case from each exact
register, and feeds that shared input to the unchanged baseline runner and the
delivered task-context resolver. It uses the frozen `tiktoken` `cl100k_base`
identity, the declared agent budget, and exact response serialization. The
output contains `report.json` and `summary.md`; generated scale packs are
removed after each entry is recorded.

`--strict` returns a non-zero status when any method fails a safety or frozen
efficiency check. A failure is evidence, not a target adjustment. Provider
evaluation is not invoked, and secondary latency is omitted from deterministic
acceptance evidence; serialized size, filesystem reads, safety failures,
coverage numerator/denominator, exclusions, and tokenizer identity remain in
the machine-readable report.

The frozen RB-05 evaluator accepts the baseline JSON response contract. For the
delivered task-context capsule, the adapter applies the same safety failure
classes to the task-context response sections and records that projection as
`rb-05-v1-adapter-task-context`.
