# Specification checkpoint: #13 / AP-03

Status: specification-only checkpoint  
Originating issue: #13 — Establish applicability parity across components and rebaseline retrieval  
Dependency: #12 / AP-02 merged (`c4c79eb`)

## Scope and non-goals

Prove and enforce that Core, Maintainer query, Manager read responses, and the
generated Plugin runtime expose identical AP-02 applicability states for the
same synthetic fixture. Add one shared parity fixture and adapter boundary;
adapters may serialize or transport the result but may not reinterpret scope
matching, conditional uncertainty, provenance, supersession, or conflicts.

This issue does not add selectors, implement task-context retrieval, rank
decisions, change canonical data, or broaden the AP-02 contract. The accepted
exact repository-scope rule remains the only authoritative selector.

## Proposed design

Create a versioned synthetic fixture containing four decisions: unconditional,
exactly matching scope, explicit scope mismatch, and conditional unresolved
applicability. Each decision also carries provenance, source scope,
supersession, and conflict metadata. A parity harness submits the same
`ApplicabilityRequest` through:

- the Core evaluator;
- a Maintainer query/read adapter;
- the Manager read serialization boundary; and
- generated Plugin runtime validation/evaluation from
  `scripts/generate_plugin_runtime.py`.

The harness compares canonical JSON result fields, not private object identity.
Every adapter must preserve `decision_id`, state, reason, matched selectors,
required selectors, provenance, source scope, supersession, and conflict IDs.
Any divergence fails the test and identifies the adapter and field.
Extending the existing generated Plugin evaluator template to emit these
already-defined AP-02 fields is explicitly in scope. This is completion of the
generated field surface, not a new selector or applicability semantic, so it
does not broaden the AP-01 vocabulary or require a new benchmark review.

Plugin projection checks use the same fixture to assert that only
`unconditional` and `satisfied` explicit guidance is eligible for operative
projection; `unsatisfied` and `undetermined` remain excluded/auditable. No
projection writes canonical data.

## Benchmark rebaseline and measure

Run the committed RB-06 benchmark command at scales 10, 100, 1,000, and
10,000 after parity passes. The accepted selector earns its complexity by the
declared safety measure: it distinguishes the named global/scoped and
no-applicable-context cases without promoting excluded or unresolved guidance.
The rebaseline records unchanged token accounting and a safety envelope with
no new failure class; it does not retune frozen efficiency targets.

## Affected components and validation

- shared fixture and parity harness under `tests/integration/`;
- Maintainer/Manager read adapter calls to Core applicability;
- `scripts/generate_plugin_runtime.py` and its generated runtime field surface;
- generated Plugin runtime drift and parity checks;
- `contracts/README.md` stable applicability documentation; and
- benchmark report evidence at all committed scales.

The principal end-to-end slice is:

```text
same fixture -> Core -> Maintainer -> Manager -> generated Plugin
            -> identical JSON applicability result -> projection eligibility
```

Validation is offline and synthetic: focused parity tests, generated-runtime
checks, `PYTHONPATH=src make contracts-check`, applicable Plugin checks,
`make retrieval-benchmark`, and `git diff --check`. Negative tests mutate one
adapter's state or provenance and assert a concrete parity failure. No
provider-backed scoring or canonical mutation is permitted.

## Compatibility, risks, and review boundary

The AP-02 contract remains additive and versioned. Existing consumers that do
not request applicability retain current behavior. A missing or unsupported
applicability result fails closed rather than being treated as operative.
Adapter-specific duplication is a rejected alternative because it can drift
from Core. This immutable checkpoint contains no implementation and requires a
fresh independent read-only review before autonomous approval.
