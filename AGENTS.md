# Context Library Monorepo Agent Instructions

## Scope and authority

- These instructions apply to the Context Library code monorepo.
- Read `SPEC.md` and `HANDOFF.md` before changing implementation files.
- Treat `SPEC.md` as the implementation authority when source repositories
  disagree.
- Do not apply an unrelated product project pack to this monorepo.
- No applicable canonical Context Library project pack exists for this
  monorepo at present. Do not infer one from the presence of the Plugin or the
  availability of a shared library.

## Canonical-data boundary

- Keep canonical Context Library data in its separately governed repository.
- Treat the configured canonical library root as read-only unless the user
  separately authorizes a canonical-data change.
- Never copy canonical decision records into this code monorepo.
- Use temporary copies or synthetic fixtures for mutation tests.

## Implementation boundary

- Preserve source-repository history and user-owned dirty changes.
- Keep the Plugin incapable of canonical writes.
- Route normal canonical mutations through Manager policy and the typed
  Maintainer service.
- Keep `clm` as the documented administrative adapter.
- Run the root validation commands required by `SPEC.md`.
