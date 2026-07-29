from __future__ import annotations

from typing import Any


class PostgresConnection:
    """Small DB-API compatibility layer for the Runtime repository methods."""

    def __init__(self, url: str):
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise RuntimeError("psycopg is required for PostgreSQL runtime storage") from exc
        self.connection = psycopg.connect(url, row_factory=dict_row)
        self.dialect = "postgres"

    @staticmethod
    def _sql(sql: str) -> str:
        return sql.replace("BEGIN IMMEDIATE", "BEGIN").replace("?", "%s").replace("MAX(0,", "GREATEST(0,")

    def executescript(self, script: str) -> None:
        try:
            self.connection.execute(script, prepare=False)
        except TypeError:
            # Minimal local fakes may expose only the DB-API execute signature.
            self.connection.execute(script)

    def execute(self, sql: str, params: tuple[Any, ...] = ()):
        return self.connection.execute(self._sql(sql), params)

    def commit(self) -> None:
        self.connection.commit()

    def rollback(self) -> None:
        self.connection.rollback()

    def close(self) -> None:
        self.connection.close()
