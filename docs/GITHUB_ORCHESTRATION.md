# GitHub orchestration access

## Purpose

Long-running roadmap orchestration uses one authenticated GitHub identity.
Parallel agents must not multiply API traffic or independently mutate roadmap
state. The orchestrator owns a serialized access broker and gives subagents
filesystem snapshots instead of GitHub credentials or live-query duties.

## Broker contract

`scripts/github_broker.py` is the only approved command path for agent-driven
GitHub CLI calls during an orchestration run.

- Every invocation takes a process-shared file lock before calling `gh`.
- Read results may be cached under a caller-provided key and TTL. Cache files
  live outside the repository by default and may be read repeatedly by
  subagents without consuming API quota.
- Mutations bypass the read cache, remain serialized, and are separated by at
  least one second.
- Rate-limit failures honor an explicit retry delay when present and otherwise
  use bounded exponential backoff. Only the broker retries; subagents do not.
- The broker runs one requested `gh` command and exits. It does not poll,
  launch another Codex process, or survive independently as a background
  worker.

## Agent boundaries

The orchestrator may use the broker for GitHub reads and writes. Subagents
must not invoke `gh`, GitHub connectors, the broker, or independent pollers.
The orchestrator should refresh one cached snapshot, pass its path to relevant
subagents, and serialize any resulting external updates after synthesis.

Specification approval is an autonomous, evidence-backed orchestration step,
not a polling operation. The orchestrator publishes an immutable,
specification-only checkpoint in a draft pull request, obtains independent
read-only review through a fresh Claude Code session, resolves Critical and
High findings, records the evidence, and then adds implementation commits
without rewriting the checkpoint. Claude review uses `haiku` at `medium` for
narrow, low-risk specifications with stable contracts and `sonnet` at `medium`
for normal review or cross-component and authority-sensitive work. It escalates
serially only when the result fails the required coverage or evidence rubric.
Explicit human feedback remains authoritative, but absent feedback does not
pause the run.

## Usage

Refresh and then reuse a read snapshot with one stable cache key:

```sh
python scripts/github_broker.py run --cache-key issue-5 --cache-ttl 0 \
  -- gh issue view 5 --json title,body,state
python scripts/github_broker.py cache-path issue-5
python scripts/github_broker.py run --cache-key issue-5 --cache-ttl 300 \
  -- gh issue view 5 --json title,body,state
```

Do not reuse a cache key for a different query shape. Mark all API writes so
the broker applies mutation pacing:

```sh
python scripts/github_broker.py run --mutating \
  -- gh issue comment 5 --body-file /path/to/sanitized-comment.md
```

The default broker directory is private to the current local user under
`/tmp`. Set `GITHUB_BROKER_DIR` when separate orchestration runs require
separate locks and caches. Use `--retry-delays` before the action to override
the bounded default retry schedule.

## Validation

Black-box tests execute the broker as a subprocess against a fake `gh`
executable. They must prove serialized concurrent access, cache reuse,
rate-limit retry behavior, mutation pacing, misuse rejection, and absence of a
polling action without network access.
