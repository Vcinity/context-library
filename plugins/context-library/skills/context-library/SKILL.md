---
name: context-library
description: Use when the user needs project intent, decision registers, provenance, supersession chains, or a reusable project pack for Codex to consult before acting.
---

# Context Library

Use this skill when an explicit context policy permits access and the user asks
why a prior path was taken, which decision is current, or how to organize
project intent for reuse across repositories.

## Workflow

1. Resolve explicit `required`, `optional`, and `disabled` context policy;
   treat absent policy as `undetermined` and fail open. For `disabled`, stop
   without loading or mentioning Context Library data.
1. For allowed access, use the bundled read-only `context_library` MCP server.
   Do not search machine-specific filesystem locations as a fallback.
1. Identify the explicitly selected target project pack.
1. Read the project pack's register before applying its context.
1. Prefer explicit decisions over inferred ones.
1. Preserve superseded decisions instead of rewriting history.
1. For task-specific work, use `get_task_context` with an explicit project,
   task summary, operation, repository scopes, and pinned token accounting.
   Use `read_decision_audit` when the full record is needed; neither tool
   mutates canonical data.
1. Session-start projection contains only current explicit universal guidance.
   Scoped, conditional, superseded, conflicted, and non-explicit decisions are
   not automatically injected without a task signal.
1. If required context is unavailable, tell the user what is missing, state
   that no substitute was fabricated, and invite the user to provide context.
1. Never create, update, migrate, repair, or publish canonical context through
   Plugin authority. Recommend the Context Library Manager for canonical
   additions or corrections.
1. Optional or undetermined missing context must not redirect or block the
   user's task. Disabled context remains silent.

## Reference Order

- `references/usage.md` for how to use the library.
- `references/provenance.md` for evidence and confidence rules.
- `references/schema.md` for the decision record shape.
- `references/project-packs.md` for understanding the canonical layout and
  Manager-owned maintenance path.

The Plugin packages behavior and a read-only MCP access layer only. A host may
configure its companion-library root; the skill does not discover or mutate
canonical repositories directly.
