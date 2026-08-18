# Context Library tool use cases

This guide explains which Context Library interface to use for common tasks.
The word “tool” covers the read-only MCP tools exposed by the Plugin and the
administrative `clm` commands. The Manager web application and API are the
normal path for human review and canonical changes.

## Choose the interface

| Need | Use | Why |
| --- | --- | --- |
| Check whether shared context is available | `get_library_status` | Fast, read-only availability check |
| Discover the projects available to an agent | `list_project_packs` | Avoids guessing project names or filesystem paths |
| Read the decisions or indexes for one project | `read_project_artifact` | Returns the selected artifact as text |
| Find guidance about a topic | `search_decisions` | Searches decision text and returns provenance |
| Get compact context for a task | `get_task_context` | Resolves applicability for an explicit task signal |
| Inspect full decision records | `read_decision_audit` | Reads on-demand audit detail without injecting it |
| Keep local agent guidance current | `projection.py sync` / `check` | Creates or verifies a consumer-local cache |
| Propose or review a canonical change | Context Library Manager | Applies authentication, review, audit, and policy |
| Administer, recover, migrate, or validate | `clm` | Direct adapter over the typed Maintainer service |

The Plugin and its MCP server are read-only with respect to canonical data.
Do not edit canonical Markdown, use Plugin tools to publish, or use `clm` for
normal agent contributions. Canonical additions and corrections go through
the Manager.

## Agent context lookup

### 1. Confirm that context is available

Use `get_library_status` at the start of a task when the host configuration or
context policy makes availability relevant. It reports whether the configured
`CONTEXT_LIBRARY_ROOT` exists and is readable.

If the result is unavailable, follow the project’s context policy after
checking the installed Plugin runtime. A session-start runtime failure is a
blocking stop with fix, disable, or uninstall recovery; once runtime preflight
is healthy, required context produces an advisory notice while optional or
undetermined context does not block work. Never invent a decision-register
replacement.

### 2. Select a project pack

Use `list_project_packs` when the project is not already explicitly selected.
Choose a returned pack by its `name`. Do not infer a project merely because
there is one directory, and do not search machine-specific checkout paths.

Set `include_incomplete` only when diagnosing library layout or scaffolding;
incomplete directories are not normally useful context sources.

### 3. Read the project context

Use `read_project_artifact` with an explicit project name:

```json
{
  "project": "example",
  "artifact": "decision-register"
}
```

Supported artifacts are:

- `readme` for the project’s overview;
- `decision-register` for authoritative decisions;
- `index-by-category` for topic-oriented navigation; and
- `index-by-date` for chronology and recent changes.

Read the decision register before changing implementation behavior. Use the
indexes to navigate, but cite the register when explaining why a decision
applies. Check supersession notes before relying on an older entry.

### 4. Find a decision by topic

Use `search_decisions` when you know the topic but not the decision ID:

```json
{
  "project": "example",
  "query": "authentication",
  "max_results": 10
}
```

The search covers decision IDs, subjects, decision text, constraints, and
metadata. Treat a match as a starting point: inspect the returned provenance
and the surrounding register entry before applying it. `max_results` must be
between 1 and 50.

## Agent operations

An agent’s job is to use context accurately and route proposed changes to the
right authority. A normal task follows this sequence:

1. **Resolve policy and availability.** Determine whether context is
   `required`, `optional`, `disabled`, or `undetermined`. For allowed access,
   check the library status and select the project explicitly.
1. **Read before acting.** Search for the relevant topic, read the matching
   register entries, and follow supersession chains. Record the decision IDs
   that informed the implementation or recommendation.
1. **Implement within the current guidance.** Treat explicit universal
   decisions projected at session start as constraints. For task-specific work,
   request `get_task_context` with an explicit project and task signal; do not
   inject scoped or conditional guidance without that signal. Do not turn an
   inference or assumption into a stronger directive merely because it seems
   useful.
1. **Report context gaps.** If required context is unavailable, tell the user
   what is missing and why it may matter. Continue only within the task’s
   policy; do not fabricate a substitute decision.
1. **Propose changes through the Manager.** When the library is incomplete,
   stale, or contradictory, submit a proposal with its source, evidence,
   rationale, affected scope, and provenance. Leave publication to Manager
   review and Maintainer policy.
1. **Handle conflicts explicitly.** Do not silently choose between applicable
   explicit directives or resolve authority conflicts. Surface the conflict,
   continue unrelated work where possible, and wait for an authorized human
   resolution.
1. **Verify the result.** After implementation, check the relevant behavior
   and cite the decisions used. If a consumer projection is part of the
   workflow, run `projection.py check` rather than treating generated files as
   the source of truth.

In shorthand:

```text
policy → availability → project selection → search/read → work
                                      ↘ missing context or conflict → notify/propose
                                                        proposal → Manager review → publish
```

### What an agent may do

- Read project packs and decision artifacts through the read-only Plugin MCP
  server.
- Use explicit context to guide implementation, review, and explanations.
- Create or check consumer-local projections when the repository policy allows
  it.
- Request compact task context with `get_task_context` when a task signal is
  available, and inspect full records with `read_decision_audit` on demand.
- Notify the user about missing required context or unresolved conflicts.
- Submit evidence and proposals through the Manager.
- Use `clm` for documented development, recovery, migration, or explicitly
  authorized unattended maintenance.

### What an agent must not do

- Edit canonical Markdown or canonical decision records directly.
- Publish a decision independently of the Manager or authorized `clm` flow.
- Strengthen `inferred` or `assumed` provenance into `explicit` provenance.
- Silently resolve an authority conflict, supersession ambiguity, or materially
  different interpretation.
- Treat a generated `AGENTS.md` projection or local cache as canonical data.

## Consumer-local projection

Use the Plugin projection when a consumer repository needs compact guidance in
its own `AGENTS.md` files.

```sh
python3 plugins/context-library/projection.py sync --root /workspace/my-project
python3 plugins/context-library/projection.py check --root /workspace/my-project
```

`sync` requires an explicit `.context-library/config.json` policy with a
selected project and a `required` or `optional` context requirement. It writes
only consumer-local generated guidance and projection metadata. `check` is
safe for CI or pre-commit use and never writes. A changed generated block is a
safety error; review the local edit rather than overwriting it. Session-start
automatic sync projects only current explicit universal constraints. Scoped or
conditional guidance requires an explicit on-demand operation or task-context
request; it is never selected from the absence of a task signal.

## Canonical maintenance

### Normal human or agent contribution

Use the Manager to submit a proposal, review candidates and conflicts, and
authorize publication. The Manager calls the typed Maintainer service; agents
must not bypass that policy path.

The usual flow is:

```text
source or observation → candidate → reconciliation → review → publication
```

### Administrative or recovery work

Use `clm` when the task is administration, development, migration, recovery,
or explicitly authorized unattended maintenance. Set the library root and
project explicitly, or configure `CONTEXT_LIBRARY_ROOT` and `CLM_PROJECT`.

Common commands are:

```sh
poetry run clm status --json
poetry run clm query --q "authentication" --json
poetry run clm validate --json
```

For a maintenance run, the normal sequence is `ingest`, `work next`,
`observe add`, `candidate add`, `finding add`, `reconcile`, conflict review,
and—only with authorization—`publish --ready --publish`. Use
`migrate legacy-pack` only for an explicitly authorized migration. Never run a
write-capable command against the live canonical checkout without confirming
the target, project, and authorization.

## What not to use

- Do not use MCP tools to create, update, migrate, repair, or publish canonical
  data.
- Do not treat generated projections as canonical decisions.
- Do not call `clm` from normal Manager operations; Manager uses the typed
  service directly.
- Do not apply a search result without checking project selection, provenance,
  and supersession.
