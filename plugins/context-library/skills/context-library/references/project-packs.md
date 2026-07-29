# Project Packs

A project pack is the project-specific part of the context library.

The host supplies the companion-library root. The Plugin does not discover
machine-specific filesystem locations.

## Canonical Layout

The Context Library Manager and its typed Maintainer service own these
canonical artifacts:

- `projects/<project-name>/README.md`
- `projects/<project-name>/decision-register.md`
- `projects/<project-name>/index-by-category.md`
- `projects/<project-name>/index-by-date.md`

## Maintenance Rules

- Plugin tools never create or update these files.
- Propose additions or corrections through the Context Library Manager.
- The Manager preserves old decisions, adds supersession evidence, and keeps
  generated indexes aligned.
