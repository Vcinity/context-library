# Applicability vocabulary evidence: #11 / AP-01

Status: specification/evidence checkpoint  
Originating issue: #11 — Analyze applicability selector utility and define the minimal vocabulary  
Dependency: #10 / RB-06 merged (`9ae2c71`)

## Decision

The minimum authoritative vocabulary justified by the current frozen corpus is:

1. `repository_scopes`: normalized repository path or project scope.

`operation` and `affected_layers` were evaluated but are explicitly deferred:
the current corpus does not isolate either signal from scope or lexical
effects. They remain candidates for a future benchmark revision, not
authoritative selectors in AP-02.

The accepted selector is conjunctive with any future accepted selectors. A decision with no selector is
unconditional. A selector is `satisfied` only when the task supplies a matching
value; it is `unsatisfied` on an explicit mismatch and `undetermined` when the
task lacks the required signal. Undetermined guidance remains visible as
judgment-required context and MUST NOT be promoted to operative guidance.

Free-text task terms and semantic similarity remain search/ranking hints only;
they cannot establish applicability or authority.

## Benchmark evidence

| Selector | Required evidence | Simpler alternative | Result |
| --- | --- | --- | --- |
| repository scope/path | `synthetic-global-scoped`, `synthetic-no-applicable-context` | global-only matching | Accepted: distinguishes project-local from unrelated guidance. |
| operation | no current RB-02 case isolates operation with scope held constant | task-summary substring | Deferred: no evidence; do not make authoritative. |
| affected layer | no current RB-02 case carries independent affected-layer metadata | repository scope alone | Deferred: no evidence; do not make authoritative. |
| environment | no current RB-02 case requires a distinct environment value | add a free-text environment field | Rejected for AP-01: no declared safety improvement; reserve for a benchmark case. |
| lifecycle phase | no current RB-02 case requires phase resolution | infer phase from prose | Rejected: no evidence and unsafe inference. |
| artifact type | no current RB-02 case requires artifact-type resolution | substring matching | Rejected: no evidence and duplicates operation/path signals. |
| actor | no current RB-02 case requires identity-specific applicability | infer actor from task prose | Rejected: authority and identity are separate policy inputs. |

The unresolved-applicability case proves the distinction between a known
operative decision and a conditional decision whose applicability is not
resolved. Supersession, conflict, and exclusion remain separate gold/safety
dimensions; no selector may override those authority rules. Re-running the
RB-06 baseline matrix with these semantics produces no change to existing
gold classifications because the accepted selectors are already represented
by the task's explicit scope signal; any future reclassification
must create a new gold revision and rerun RB-06.

## Contract proposal for AP-02

The versioned contract should carry an optional applicability object per
decision:

```json
{
  "schema_version": 1,
  "state": "unconditional | satisfied | unsatisfied | undetermined",
  "matched_selectors": {"repository_scopes": ["src/example"]},
  "required_selectors": ["repository_scopes"],
  "reason": "missing-task-signal"
}
```

Selector values are structured, normalized strings or enumerated operation
classes. Unknown selectors are rejected. The contract must preserve source
scope and provenance, keep unresolved state separate from operative output,
and never treat a free-text match as authoritative.

## Scope, boundaries, and validation

This issue changes no implementation or generated contract. It does not add
environment, lifecycle, artifact, or actor semantics without benchmark
evidence. The next contract issue (#12) must validate the proposal against
positive, mismatch, missing-signal, malformed, and unsupported-version cases.

The evidence slice is the offline `make retrieval-benchmark` report plus this
named-case matrix. Validation is deterministic, requires no provider or
canonical data, and preserves public-repository hygiene.

## Review boundary

This checkpoint is immutable and must receive fresh independent read-only
review for scope control, evidence sufficiency, authority semantics,
downstream contract compatibility, and black-box coverage before the
autonomous Spec Gate is set to Approved.
