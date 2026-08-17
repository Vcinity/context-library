# Usage

Use the context library as a shared map from project intent to implementation choices.

## Agents

- Resolve context policy first. Disabled context is not loaded or mentioned.
- Prefer the bundled `context_library` MCP server when direct filesystem
  access is sandboxed or unavailable.
- Check the decision register before changing code.
- Do not search machine-specific filesystem paths for companion-library
  checkouts.
- Look for supersession notes before reusing an older idea.
- Treat explicit decisions as current unless a newer decision supersedes them.
- Use `get_task_context` for task-specific retrieval with an explicit project
  and task signal. Use `read_decision_audit` for on-demand full records.
- Session-start projection contains only current explicit universal constraints;
  it does not inject scoped or conditional context without a task signal.
- If required context is unavailable, notify the user and proceed only as
  allowed by higher-level instructions without fabricating a substitute.
- Optional or undetermined missing context does not interfere.
- Use the Context Library Manager for canonical additions and corrections;
  Plugin tools are read-only.

## Auditing

- Trace decisions by subject, date, decisionmaker, rationale, and evidence.
- Follow supersession chains rather than rewriting old entries.

## Documentation

- Cite the register instead of reconstructing the discussion.
- Keep the artifact append-only.

## `get_task_context` MCP Tool

Retrieve task-specific context from a project's decision register. The tokenizer field identifies the model whose token budget applies to the returned context.

### Valid request

```json
{
  "project": "example-project",
  "task_summary": "Add end-to-end API tests",
  "operation": "modify source",
  "repository_scopes": ["tests/e2e"],
  "agent_token_budget": 8000,
  "tokenizer": {
    "name": "tiktoken",
    "version": "0.9.0",
    "vocabulary_revision": "cl100k_base",
    "accounting_method": "offline"
  }
}
```

The tokenizer's `pinned` field defaults to `true` and enforces deterministic token counting:

```json
{
  "project": "example-project",
  "task_summary": "Add end-to-end API tests",
  "operation": "modify source",
  "repository_scopes": ["tests/e2e"],
  "agent_token_budget": 8000,
  "tokenizer": {
    "name": "tiktoken",
    "version": "0.9.0",
    "vocabulary_revision": "cl100k_base",
    "accounting_method": "offline",
    "pinned": true
  }
}
```

### Invalid requests

Missing required tokenizer field:

```json
{
  "project": "example-project",
  "task_summary": "Add end-to-end API tests",
  "operation": "modify source",
  "repository_scopes": ["tests/e2e"],
  "agent_token_budget": 8000,
  "tokenizer": {
    "name": "tiktoken",
    "version": "0.9.0",
    "vocabulary_revision": "cl100k_base"
  }
}
```

Unknown tokenizer field:

```json
{
  "project": "example-project",
  "task_summary": "Add end-to-end API tests",
  "operation": "modify source",
  "repository_scopes": ["tests/e2e"],
  "agent_token_budget": 8000,
  "tokenizer": {
    "name": "tiktoken",
    "version": "0.9.0",
    "vocabulary_revision": "cl100k_base",
    "accounting_method": "offline",
    "truncation_mode": "none"
  }
}
```

Invalid `pinned` value (must be `true`):

```json
{
  "project": "example-project",
  "task_summary": "Add end-to-end API tests",
  "operation": "modify source",
  "repository_scopes": ["tests/e2e"],
  "agent_token_budget": 8000,
  "tokenizer": {
    "name": "tiktoken",
    "version": "0.9.0",
    "vocabulary_revision": "cl100k_base",
    "accounting_method": "offline",
    "pinned": false
  }
}
```

## `read_decision_audit` MCP Tool

Read full canonical records for explicitly selected decision IDs. This tool supports on-demand access to complete decision details without needing a task signal.

### Valid request

```json
{
  "project": "example-project",
  "decision_ids": ["2025-03-api-versioning", "2025-02-database-choice"]
}
```

Include related decisions (supersedes, superseded_by) with `include_related`:

```json
{
  "project": "example-project",
  "decision_ids": ["2025-03-api-versioning"],
  "include_related": true
}
```

Full request with explicit schema:

```json
{
  "schema": "context-library/decision-audit-response",
  "schema_version": 1,
  "project": "example-project",
  "decision_ids": ["2025-03-api-versioning", "2025-02-database-choice"],
  "include_related": false
}
```

## Repository Projection

- Configure explicit `project` and `context_requirement` values in
  `.context-library/config.json`, or supply equivalent host policy.
- Use `projection.py sync` for on-demand updates and `projection.py check` for
  non-mutating pre-commit or CI validation.
- Treat generated `AGENTS.md` blocks and `.context-library/projection.json` as
  reviewable caches, never as canonical decision content.
- Ratify inferred or assumed guidance by appending a new explicit source
  decision; do not strengthen generated provenance.
