from __future__ import annotations

import fcntl

# SQL DDL and parameterized statements are intentionally kept readable.
# ruff: noqa: E501
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .models import Candidate, Finding, Observation, SourceEnvelope, canonical_json, now_utc, timestamp

MIGRATIONS = [
    """
    CREATE TABLE IF NOT EXISTS migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS sources(id TEXT PRIMARY KEY, identity_key TEXT UNIQUE NOT NULL, source_type TEXT NOT NULL, uri TEXT NOT NULL, external_id TEXT NOT NULL, title TEXT NOT NULL, payload_json TEXT NOT NULL, content TEXT, redacted_content TEXT, retained_excerpts_json TEXT NOT NULL, content_digest TEXT NOT NULL, predecessor_id TEXT REFERENCES sources(id), created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS observations(id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id), payload_json TEXT NOT NULL, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS candidates(id TEXT PRIMARY KEY, project TEXT NOT NULL, payload_json TEXT NOT NULL, state TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS candidate_sources(candidate_id TEXT NOT NULL REFERENCES candidates(id), source_id TEXT, observation_id TEXT REFERENCES observations(id), PRIMARY KEY(candidate_id, source_id, observation_id));
    CREATE TABLE IF NOT EXISTS findings(id INTEGER PRIMARY KEY AUTOINCREMENT, candidate_id TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS candidate_events(id INTEGER PRIMARY KEY AUTOINCREMENT, candidate_id TEXT NOT NULL REFERENCES candidates(id), from_state TEXT, to_state TEXT NOT NULL, reason TEXT, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS conflicts(id TEXT PRIMARY KEY, project TEXT NOT NULL, payload_json TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, actor TEXT NOT NULL, command TEXT NOT NULL, options_json TEXT NOT NULL, started_at TEXT NOT NULL, ended_at TEXT, status TEXT, error_class TEXT, touched_json TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS publications(id INTEGER PRIMARY KEY AUTOINCREMENT, project TEXT NOT NULL, run_id TEXT NOT NULL, phase TEXT NOT NULL, digest TEXT, payload_json TEXT NOT NULL, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS locks(project TEXT PRIMARY KEY, actor TEXT NOT NULL, acquired_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS leases(item_type TEXT NOT NULL, item_id TEXT NOT NULL, actor TEXT NOT NULL, leased_at TEXT NOT NULL, expires_at TEXT NOT NULL, PRIMARY KEY(item_type, item_id));
    """,
    """
    ALTER TABLE sources ADD COLUMN project TEXT NOT NULL DEFAULT '';
    ALTER TABLE observations ADD COLUMN project TEXT NOT NULL DEFAULT '';
    CREATE INDEX IF NOT EXISTS idx_sources_project ON sources(project, created_at);
    CREATE INDEX IF NOT EXISTS idx_observations_project ON observations(project, created_at);
    """,
]


class State:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        os.chmod(root, 0o700)
        self.path = root / "state.sqlite3"
        self.db = sqlite3.connect(self.path, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        os.chmod(self.path, 0o600)
        self.migrate()

    def migrate(self) -> None:
        self.db.execute("CREATE TABLE IF NOT EXISTS migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
        current = self.db.execute("SELECT COALESCE(MAX(version), 0) FROM migrations").fetchone()[0]
        if current > len(MIGRATIONS):
            raise RuntimeError(f"maintainer state schema {current} is newer than supported schema {len(MIGRATIONS)}")
        for version, sql in enumerate(MIGRATIONS, 1):
            if version <= current:
                continue
            self.db.executescript(sql)
            self.db.execute("INSERT INTO migrations VALUES (?, ?)", (version, timestamp(now_utc())))

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield self.db
        except BaseException:
            self.db.rollback()
            raise
        else:
            try:
                self.db.commit()
            except BaseException:
                self.db.rollback()
                raise

    @contextmanager
    def project_lock(self, project: str, actor: str) -> Iterator[None]:
        lock_path = self.root / f"{project}.lock"
        with lock_path.open("a+") as stream:
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError(f"project lock is held for {project}") from exc
            self.db.execute(
                "INSERT OR REPLACE INTO locks(project, actor, acquired_at) VALUES (?, ?, ?)",
                (project, actor, timestamp(now_utc())),
            )
            try:
                yield
            finally:
                self.db.execute("DELETE FROM locks WHERE project=? AND actor=?", (project, actor))
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def run_start(self, actor: str, command: str, options: dict[str, Any]) -> str:
        run_id = "run_" + uuid.uuid4().hex
        self.db.execute(
            "INSERT INTO runs(id, actor, command, options_json, started_at, touched_json) VALUES (?, ?, ?, ?, ?, ?)",
            (run_id, actor, command, canonical_json(options), timestamp(now_utc()), "[]"),
        )
        return run_id

    def run_end(
        self, run_id: str, status: str, error_class: str | None = None, touched: list[str] | None = None
    ) -> None:
        self.db.execute(
            "UPDATE runs SET ended_at=?, status=?, error_class=?, touched_json=? WHERE id=?",
            (timestamp(now_utc()), status, error_class, canonical_json(touched or []), run_id),
        )

    def add_source(self, source: SourceEnvelope, project: str, retain: bool = True) -> tuple[str, bool]:
        redacted = source.content
        from .models import redact

        redacted = redact(source.content, source.secret_spans)
        content_digest = __import__("hashlib").sha256(source.content.encode()).hexdigest()
        identity = (
            __import__("hashlib")
            .sha256(
                (
                    project
                    + "\0"
                    + source.source_type
                    + "\0"
                    + source.uri
                    + "\0"
                    + source.external_id
                    + "\0"
                    + content_digest
                ).encode()
            )
            .hexdigest()
        )
        source_id = "src_" + identity[:24]
        found = self.db.execute("SELECT id FROM sources WHERE identity_key=?", (identity,)).fetchone()
        if found:
            return found[0], False
        predecessor = self.db.execute(
            "SELECT id FROM sources WHERE project=? AND source_type=? AND uri=? AND external_id=? "
            "ORDER BY created_at DESC LIMIT 1",
            (project, source.source_type, source.uri, source.external_id),
        ).fetchone()
        stored = redacted if retain else None
        persisted = source.model_dump()
        persisted["content"] = redacted
        persisted["secret_spans"] = []

        self.db.execute(
            "INSERT INTO sources"
            "(id,identity_key,source_type,uri,external_id,title,payload_json,content,redacted_content,"
            "retained_excerpts_json,content_digest,predecessor_id,created_at,project)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                source_id,
                identity,
                source.source_type,
                source.uri,
                source.external_id,
                source.title,
                canonical_json(persisted),
                stored,
                redacted if retain else None,
                canonical_json([item.model_dump() for item in source.retained_excerpts]),
                content_digest,
                predecessor[0] if predecessor else None,
                timestamp(now_utc()),
                project,
            ),
        )
        return source_id, True

    def add_observation(self, observation: Observation, observation_id: str, project: str) -> None:
        source = self.db.execute(
            "SELECT redacted_content, retained_excerpts_json FROM sources WHERE id=? AND project=?",
            (observation.source_id, project),
        ).fetchone()
        if source is None:
            raise ValueError(f"unknown source: {observation.source_id}")
        retained = json.loads(source[1])
        if source[0] is not None and observation.excerpt not in source[0]:
            raise ValueError("observation excerpt is not an exact substring of redacted source content")
        if source[0] is None and not any(
            observation.excerpt == item["excerpt"] and observation.location == item["location"] for item in retained
        ):
            raise ValueError("observation excerpt is not a retained source excerpt")
        from .models import model_json

        self.db.execute(
            "INSERT INTO observations(id,source_id,payload_json,created_at,project) VALUES (?, ?, ?, ?, ?)",
            (observation_id, observation.source_id, model_json(observation), timestamp(now_utc()), project),
        )

    def add_candidate(self, candidate: Candidate) -> None:
        now = timestamp(now_utc())
        from .models import model_json

        linked = self.observations(candidate.source_observation_ids, candidate.project)
        if {row["id"] for row in linked} != set(candidate.source_observation_ids):
            raise ValueError("candidate references an observation outside its project")
        self.db.execute(
            "INSERT INTO candidates VALUES (?, ?, ?, 'proposed', ?, ?)",
            (candidate.candidate_id, candidate.project, model_json(candidate), now, now),
        )
        for oid in candidate.source_observation_ids:
            self.db.execute(
                "INSERT OR IGNORE INTO candidate_sources(candidate_id, observation_id) VALUES (?, ?)",
                (candidate.candidate_id, oid),
            )

    def add_finding(self, finding: Finding, project: str | None = None) -> None:
        from .models import model_json

        if project is not None:
            candidate = self.db.execute(
                "SELECT 1 FROM candidates WHERE id=? AND project=?",
                (finding.candidate_id, project),
            ).fetchone()
            if not candidate:
                raise ValueError("finding references a candidate outside its project")
        self.db.execute(
            "INSERT INTO findings(candidate_id, payload_json, created_at) VALUES (?, ?, ?)",
            (finding.candidate_id, model_json(finding), timestamp(now_utc())),
        )

    def candidates(self, project: str, state: str | None = None) -> list[sqlite3.Row]:
        query = (
            "SELECT * FROM candidates WHERE project=?" + (" AND state=?" if state else "") + " ORDER BY created_at, id"
        )
        return list(self.db.execute(query, (project, state) if state else (project,)))

    def observations(self, ids: list[str], project: str | None = None) -> list[sqlite3.Row]:
        if not ids:
            return []
        marks = ",".join("?" for _ in ids)
        query = f"SELECT * FROM observations WHERE id IN ({marks})"
        parameters: list[str] = list(ids)
        if project is not None:
            query += " AND project=?"
            parameters.append(project)
        return list(self.db.execute(query, parameters))

    def transition(self, candidate_id: str, to_state: str, reason: str | None = None) -> None:
        current = self.db.execute("SELECT state FROM candidates WHERE id=?", (candidate_id,)).fetchone()
        if not current:
            raise ValueError(f"unknown candidate: {candidate_id}")
        self.db.execute(
            "UPDATE candidates SET state=?, updated_at=? WHERE id=?", (to_state, timestamp(now_utc()), candidate_id)
        )
        self.db.execute(
            "INSERT INTO candidate_events(candidate_id, from_state, to_state, reason, created_at) VALUES (?, ?, ?, ?, ?)",
            (candidate_id, current[0], to_state, reason, timestamp(now_utc())),
        )

    def status(self, project: str) -> dict[str, Any]:
        def count(state: str) -> int:
            return self.db.execute(
                "SELECT COUNT(*) FROM candidates WHERE project=? AND state=?", (project, state)
            ).fetchone()[0]

        open_conflicts = self.db.execute(
            "SELECT COUNT(*) FROM conflicts WHERE project=? AND status='open'", (project,)
        ).fetchone()[0]
        total_sources = self.db.execute(
            "SELECT COUNT(*) FROM sources s WHERE s.project=? "
            "AND NOT EXISTS (SELECT 1 FROM observations o WHERE o.source_id=s.id AND o.project=?)",
            (project, project),
        ).fetchone()[0]
        queue = (
            "invalid"
            if count("invalid")
            else "conflict-only"
            if open_conflicts and not count("ready") and not count("proposed")
            else "actionable"
            if count("ready") or count("proposed")
            else "empty"
        )
        return {
            "schema_version": 1,
            "project": project,
            "sources_unprocessed": total_sources,
            "candidates_proposed": count("proposed"),
            "candidates_ready": count("ready"),
            "conflicts_open": open_conflicts,
            "oldest_open_conflict_at": None,
            "last_publication_at": None,
            "library_valid": True,
            "queue_state": queue,
        }
