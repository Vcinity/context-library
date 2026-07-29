# Decision Schema

Each decision entry should carry:

- `subject`
- `category`
- `decision`
- `date`
- `decisionmaker`
- `rationale`
- `evidence`
- `provenance`
- `supersedes` when applicable

Optional additions:

- `affected_layers`
- `tags`
- `confidence`
- `constraint` or `constraints` for compact projection-ready wording
- `derivation`: `direct`, `condensed`, or `synthesized`
- `sources` for every synthesized contributor
- `conflicts_with` or `conflict_key`
- `applies_when`
- `review`

Stable Markdown anchors such as `<a id="decision-id"></a>` identify records.
Use exact identifiers in `supersedes`, `sources`, and conflict references when
the projection compiler must resolve them. Narrative history remains useful to
readers but is not guessed into identifiers.

Only current explicit decisions can become automatic constraints. Synthesized
records inherit the weakest provenance of all transitive sources. Conditional
decisions and affected layers without an exact repository mapping remain in the
provenance sidecar rather than entering `AGENTS.md`.
