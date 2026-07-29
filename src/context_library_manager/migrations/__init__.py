from __future__ import annotations

import sqlite3
from pathlib import Path

from context_library_manager.domain import utc_now


def apply_migrations(db) -> None:
    dialect = "sqlite" if isinstance(db, sqlite3.Connection) or getattr(db, "dialect", None) == "sqlite" else "postgres"
    db.execute("CREATE TABLE IF NOT EXISTS runtime_migrations (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)")
    db.commit()
    applied = {row["version"] for row in db.execute("SELECT version FROM runtime_migrations").fetchall()}
    if "v1" in applied and "001_initial" not in applied:
        db.execute(
            (
                "INSERT OR IGNORE INTO runtime_migrations(version, applied_at) VALUES(?, ?)"
                if dialect == "sqlite"
                else "INSERT INTO runtime_migrations(version, applied_at) VALUES(?, ?) ON CONFLICT(version) DO NOTHING"
            ),
            ("001_initial", utc_now()),
        )
        db.commit()
        applied.add("001_initial")

    root = Path(__file__).parent / dialect
    paths = sorted(root.glob("[0-9][0-9][0-9]_*.sql"))
    if not paths or paths[0].stem != "001_initial":
        raise RuntimeError(f"packaged {dialect} migrations are missing")
    packaged = {path.stem for path in paths}
    unsupported = applied - packaged - {"v1"}
    if unsupported:
        raise RuntimeError(
            "runtime database schema is newer than this release: "
            f"{', '.join(sorted(unsupported))}; upgrade the Manager or restore a compatible backup"
        )
    for path in paths:
        version = path.stem
        if version in applied:
            continue
        sql = path.read_text(encoding="utf-8")
        try:
            if dialect == "sqlite":
                db.execute("BEGIN IMMEDIATE")
                if db.execute("SELECT 1 FROM runtime_migrations WHERE version=?", (version,)).fetchone():
                    db.commit()
                    continue
                for statement in (item.strip() for item in sql.split(";")):
                    if statement:
                        db.execute(statement)
                db.execute(
                    "INSERT INTO runtime_migrations(version, applied_at) VALUES(?, ?)",
                    (version, utc_now()),
                )
                db.commit()
            else:
                db.execute("BEGIN")
                db.execute(
                    "SELECT pg_advisory_xact_lock(hashtext(?))",
                    ("context-library-manager:migrations",),
                ).fetchone()
                if db.execute("SELECT 1 FROM runtime_migrations WHERE version=?", (version,)).fetchone():
                    db.commit()
                    continue
                db.executescript(sql)
                db.execute(
                    "INSERT INTO runtime_migrations(version, applied_at) VALUES(?, ?)",
                    (version, utc_now()),
                )
                db.commit()
        except Exception:
            db.rollback()
            raise
