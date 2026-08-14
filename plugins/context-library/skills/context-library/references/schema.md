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

Only current explicit universal decisions can become automatic constraints:
there must be no affected layer, conditional applicability, supersession, or
unresolved conflict. Scoped, conditional, superseded, conflicted, inferred,
and assumed records remain auditable but do not enter the session-start
`AGENTS.md` hot path. Use the explicit task-context request when a task signal
makes scoped context relevant. Synthesized records inherit the weakest
provenance of all transitive sources.
