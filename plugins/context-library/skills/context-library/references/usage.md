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

## Repository Projection

- Configure explicit `project` and `context_requirement` values in
  `.context-library/config.json`, or supply equivalent host policy.
- Use `projection.py sync` for on-demand updates and `projection.py check` for
  non-mutating pre-commit or CI validation.
- Treat generated `AGENTS.md` blocks and `.context-library/projection.json` as
  reviewable caches, never as canonical decision content.
- Ratify inferred or assumed guidance by appending a new explicit source
  decision; do not strengthen generated provenance.
