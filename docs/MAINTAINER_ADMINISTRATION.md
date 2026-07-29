# Maintainer Agent Workflow

Operate the project through `clm`; never edit canonical Context Library files
directly. Repeat this loop:

Configure the canonical checkout and logical project explicitly with
`--library-root`/`--project` or `CONTEXT_LIBRARY_ROOT`/`CLM_PROJECT`. There is
no machine-specific default and no implicit project selection.

1. Run `clm status --json`.
1. Gather source material using available connectors and submit version-1
   envelopes with `clm ingest`.
1. Run `clm work next --json`.
1. Submit exact evidence observations with `clm observe add`.
1. Submit candidates with `clm candidate add`.
1. Submit semantic relationship findings with `clm finding add`.
1. Run `clm reconcile --json`.
1. Inspect conflicts; continue unrelated work while they wait for humans.
1. Run `clm publish --ready --publish --json` only when authorized.
1. Run `clm validate --json` before completion.

Decision provenance is `explicit` when a source directly records a directive
from an identifiable decision-maker, `inferred` when the agent synthesizes
intent from evidence, and `assumed` when an evidence gap is acknowledged.
Applicability provenance is classified independently. Provenance is never
strengthened during synthesis. Explicit candidates need directive or
constraint evidence; inferred candidates need two observations (or two
independent passages); synthesized records cite every source and inherit the
weakest provenance.

Automatic publication requires valid schemas and evidence, valid project
configuration/topology/authority, no active conflict or ambiguous supersession,
resolved references, correct provenance, parser compatibility, and a valid
staged project pack. High-confidence routing may publish affected layers;
uncertain routing falls back to root scope with non-operative suggestions.

Escalate only conflicting applicable explicit directives, ambiguous authority,
unsupported supersession, materially different interpretations, broadened
meaning, or policy-required review. Consequence alone is not a review gate.

Example payloads are documented in `SPEC.md` Sections 11–13 and can be passed
with `--file` or `--stdin`. Resolution commands are emitted verbatim in each
conflict packet.

Minimal payload examples:

```json
{"schema_version":1,"external_id":"EXAMPLE-410","source_type":"ticket","uri":"ticket://EXAMPLE-410","title":"Password UX","retrieved_at":"2026-07-16T01:00:00Z","content_format":"markdown","content":"Keep password changes in the application UI."}
```

```json
{"schema_version":1,"source_id":"src_example","kind":"directive","excerpt":"Keep password changes in product UI.","location":"description","speaker":{"identity":"person@example.com","display_name":"Example Person"},"occurred_at":"2026-07-15T14:04:00Z","agent_interpretation":"Explicit ownership directive."}
```

```json
{"schema_version":1,"project":"example","candidate_id":"auth-application-owned-ux","subject":"Application owns password UX","category":"authentication","decision":"Keep password changes inside application-owned UI and APIs.","rationale":"The source explicitly assigns ownership.","decisionmaker":{"identity":"person@example.com","display_name":"Example Person"},"decision_at":"2026-07-15T14:04:00Z","provenance":"explicit","derivation":"direct","source_observation_ids":["obs_example"],"applicability":{"provenance":"inferred","confidence":0.94,"evidence_observation_ids":["obs_example"],"reasoning":"UI and authentication boundaries are named."}}
```

```json
{"schema_version":1,"finding":"conflict","candidate_id":"auth-product-owned-ux","canonical_ids":["auth-existing-ux"],"confidence":0.96,"evidence_observation_ids":["obs_example"],"reasoning":"The directives choose different owners."}
```

```text
clm conflict resolve conflict-example-20260716-abcdef --choice retain-current --actor person@example.com --rationale "Keep the last known-good directive."
```
