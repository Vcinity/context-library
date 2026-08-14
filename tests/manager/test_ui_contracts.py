from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from context_library_manager.contracts import (
    AgentRunSummary,
    AgentServiceControl,
    AgentServiceStatus,
    AuditResult,
    ConfigurationDraft,
    ContentStatus,
    DecisionSummary,
    NotificationSummary,
    Page,
    ProcessHeartbeat,
    ReviewSummary,
    SessionIdentity,
)
from context_library_manager.db import Store
from context_library_manager.migrations import apply_migrations
from context_library_manager.postgres import PostgresConnection


def test_ui_contracts_are_strict_and_timezone_aware():
    now = datetime.now(timezone.utc)
    identity = SessionIdentity(
        subject="user:reader",
        display_name="Reader",
        capabilities=["read"],
        allowed_projects=["demo"],
        selected_project="demo",
        issued_at=now,
        expires_at=now + timedelta(minutes=15),
        csrf_token="x" * 32,
    )
    assert identity.capabilities == ["read"]
    with pytest.raises(ValidationError):
        SessionIdentity.model_validate({**identity.model_dump(), "unexpected": True})
    with pytest.raises(ValidationError):
        SessionIdentity.model_validate(
            {
                **identity.model_dump(),
                "issued_at": datetime.now(),
            }
        )


def test_paginated_decision_contract_has_bounded_page_size():
    decision = DecisionSummary(
        decision_id="ui-react-over-vue",
        subject="React remained the GUI framework choice",
        decision="Stay on React.",
        provenance="explicit",
        status=ContentStatus.AUTHORITATIVE,
        publication_revision="abc123",
        library_digest="a" * 64,
    )
    page = Page[DecisionSummary](items=[decision], page=1, page_size=25, total=1, next_page=None)
    assert page.items[0].status == "authoritative"
    with pytest.raises(ValidationError):
        Page[DecisionSummary](items=[], page=1, page_size=101, total=0)


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (
            ProcessHeartbeat,
            {
                "process": "worker",
                "instance_id": "worker-1",
                "state": "healthy",
                "observed_at": datetime.now(),
            },
        ),
        (
            AgentRunSummary,
            {
                "run_id": "run-1",
                "work_id": "work-1",
                "project": "demo",
                "status": "running",
                "profile": "cheap",
                "cache_hit": False,
                "input_tokens": 0,
                "output_tokens": 0,
                "cost": 0,
                "started_at": datetime.now(),
            },
        ),
        (
            AuditResult,
            {
                "audit_id": "audit-1",
                "actor": "user:test",
                "action": "test",
                "project": "demo",
                "created_at": datetime.now(),
            },
        ),
    ],
)
def test_all_timestamp_contracts_reject_naive_values(model, payload):
    with pytest.raises(ValidationError):
        model.model_validate(payload)


def test_mutation_contracts_require_reason_revision_and_idempotency():
    with pytest.raises(ValidationError):
        AgentServiceControl(expected_version=1, reason="", idempotency_key="pause-1")
    with pytest.raises(ValidationError):
        ConfigurationDraft(
            expected_revision=0,
            changes={"autonomy.enabled": False},
            reason="operator request",
            idempotency_key="config-1",
        )


def test_ui_fixture_covers_required_state_partitions():
    fixture = json.loads((Path(__file__).parent / "fixtures" / "ui_scenarios.json").read_text())
    decisions = [DecisionSummary.model_validate(item) for item in fixture["decisions"]]
    assert {item.status for item in decisions} == {item.value for item in ContentStatus}
    assert {user["capability"] for user in fixture["users"]} == {
        "read",
        "review",
        "maintain",
        "admin",
    }
    service = [
        AgentServiceStatus.model_validate(
            {
                **item,
                "project_token_budget": 1000,
                "project_tokens_used": 100,
            }
        )
        for item in fixture["service_snapshots"]
    ]
    assert {(item.state, item.health) for item in service} == {
        ("running", "healthy"),
        ("paused", "healthy"),
        ("draining", "healthy"),
        ("running", "degraded"),
        ("running", "offline"),
    }
    runs = [AgentRunSummary.model_validate(item) for item in fixture["runs"]]
    assert {item.status for item in runs} == {
        "running",
        "failed",
        "expired",
        "canceled",
        "succeeded",
    }
    assert next(item for item in runs if item.run_id == "run-cache").cache_hit
    reviews = [ReviewSummary.model_validate(item) for item in fixture["reviews"]]
    assert {item.status for item in reviews} == {"open", "resolved", "stale"}
    notices = [NotificationSummary.model_validate(item) for item in fixture["notifications"]]
    assert {item.status for item in notices} == {"pending", "delivered", "failed"}
    assert {item["source"] for item in fixture["configuration"]} == {
        "default",
        "project-file",
        "environment",
        "secret-store",
    }
    assert fixture["conflicts"]["stale_revision"]["error"] == "revision-conflict"
    assert fixture["conflicts"]["duplicate_idempotency"]["same_response"] is True


def test_v1_sqlite_database_upgrades_without_losing_history(tmp_path):
    path = tmp_path / "v1.db"
    db = sqlite3.connect(path)
    initial = (Path(__file__).parents[2] / "src/context_library_manager/migrations/sqlite/001_initial.sql").read_text()
    db.executescript(initial)
    db.execute("CREATE TABLE runtime_migrations (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)")
    db.execute("INSERT INTO runtime_migrations VALUES('v1', '2026-01-01T00:00:00Z')")
    db.execute(
        "INSERT INTO work_items VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "work_preserved",
            "demo",
            "source_batch",
            "legacy-key",
            "succeeded",
            "{}",
            0,
            None,
            None,
            None,
            "2026-01-01T00:00:00Z",
            "2026-01-01T00:00:00Z",
        ),
    )
    db.execute(
        "INSERT INTO reviews VALUES(?,?,?,?,?,?,?,?,?,?)",
        (
            "review_preserved",
            "demo",
            "work_preserved",
            "Choose",
            '["a", "b"]',
            "resolved",
            '["obs-1"]',
            '{"choice": "a"}',
            "2026-01-01T00:00:00Z",
            "2026-01-02T00:00:00Z",
        ),
    )
    db.execute(
        "INSERT INTO notifications (id, review_id, status, created_at, attempts, delivered_at) VALUES(?,?,?,?,?,?)",
        (
            "notice_preserved",
            "review_preserved",
            "delivered",
            "2026-01-01T00:00:00Z",
            1,
            "2026-01-01T00:01:00Z",
        ),
    )
    db.execute(
        "INSERT INTO publication_history VALUES(?,?,?,?,?,?,?,?)",
        (
            "publication_preserved",
            "demo",
            "work_preserved",
            "succeeded",
            "digest",
            "revision",
            None,
            "2026-01-02T00:00:00Z",
        ),
    )
    db.execute(
        "INSERT INTO audit_events VALUES(?,?,?,?,?,?,?)",
        (
            "audit_preserved",
            "work_preserved",
            "demo",
            "user:test",
            "resolved",
            "{}",
            "2026-01-02T00:00:00Z",
        ),
    )
    db.commit()
    db.close()

    store = Store(path)
    assert store.work("demo", "work_preserved")["state"] == "succeeded"
    for table, ident in (
        ("reviews", "review_preserved"),
        ("notifications", "notice_preserved"),
        ("publication_history", "publication_preserved"),
        ("audit_events", "audit_preserved"),
    ):
        assert store.db.execute(f"SELECT id FROM {table} WHERE id=?", (ident,)).fetchone()
    versions = {row["version"] for row in store.db.execute("SELECT version FROM runtime_migrations")}
    assert {"v1", "001_initial", "002_ui"} <= versions
    required_tables = {
        "browser_sessions",
        "idempotency_records",
        "agent_service_state",
        "process_heartbeats",
        "work_cancellations",
        "configuration_revisions",
        "configuration_changes",
        "library_proposals",
    }
    tables = {row["name"] for row in store.db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert required_tables <= tables
    store.close()


def test_postgres_migrations_generate_server_appropriate_sql():
    class RecordingConnection:
        def __init__(self):
            self.statements = []

        def execute(self, statement, params=()):
            self.statements.append((statement, params))

    wrapper = PostgresConnection.__new__(PostgresConnection)
    wrapper.connection = RecordingConnection()
    root = Path(__file__).parents[2] / "src/context_library_manager/migrations/postgres"
    for path in sorted(root.glob("*.sql")):
        wrapper.executescript(path.read_text())
    statements = [statement for statement, _ in wrapper.connection.statements]
    assert len(statements) == len(list(root.glob("*.sql")))
    assert all("CREATE TABLE" in statement or "ALTER TABLE" in statement for statement in statements)
    assert all("?" not in statement for statement in statements)
    assert all("AUTOINCREMENT" not in statement for statement in statements)
    assert all("BEGIN IMMEDIATE" not in statement for statement in statements)
    assert any("ON CONFLICT(singleton) DO NOTHING" in item for item in statements)


def test_postgres_migration_lock_precedes_bootstrap_table_creation():
    class Cursor:
        def fetchone(self):
            return None

        def fetchall(self):
            return []

    class RecordingConnection:
        dialect = "postgres"

        def __init__(self):
            self.statements = []

        def execute(self, statement, params=()):
            self.statements.append(statement)
            return Cursor()

        def executescript(self, script):
            self.statements.append(script)

        def commit(self):
            pass

        def rollback(self):
            pass

    db = RecordingConnection()
    apply_migrations(db)

    lock_index = next(index for index, statement in enumerate(db.statements) if "pg_advisory_xact_lock" in statement)
    table_index = next(
        index
        for index, statement in enumerate(db.statements)
        if "CREATE TABLE IF NOT EXISTS runtime_migrations" in statement
    )
    assert lock_index < table_index


def test_packaged_migration_assets_are_discoverable():
    import context_library_manager.migrations as migrations

    root = Path(migrations.__file__).parent
    for dialect in ("sqlite", "postgres"):
        names = sorted(path.name for path in (root / dialect).glob("*.sql"))
        assert names == [
            "001_initial.sql",
            "002_ui.sql",
            "003_review_metadata.sql",
            "004_runtime_observability.sql",
            "005_agent_run_reservations.sql",
            "006_telemetry_lineage.sql",
            "007_notification_claims.sql",
            "008_project_lifecycle.sql",
        ]


def test_concurrent_sqlite_startup_serializes_migrations(tmp_path):
    path = tmp_path / "concurrent-runtime.db"

    def start_store(_index):
        store = Store(path)
        versions = store.db.execute("SELECT version FROM runtime_migrations ORDER BY version").fetchall()
        store.close()
        return [row["version"] for row in versions]

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(start_store, range(4)))
    assert all(
        versions
        == [
            "001_initial",
            "002_ui",
            "003_review_metadata",
            "004_runtime_observability",
            "005_agent_run_reservations",
            "006_telemetry_lineage",
            "007_notification_claims",
            "008_project_lifecycle",
        ]
        for versions in results
    )


def test_openapi_contains_ui_contract_routes(tmp_path):
    from test_overview_shell import app_and_client

    app, _ = app_and_client(tmp_path)
    paths = app.openapi()["paths"]
    required = {
        "/api/v1/session",
        "/api/v1/projects/{project}/overview",
        "/api/v1/projects/{project}/library/search",
        "/api/v1/projects/{project}/library/proposals",
        "/api/v1/projects/{project}/reviews/{review_id}/resolve",
        "/api/v1/agent-service/{action}",
        "/api/v1/projects/{project}/agent-runs/{run_id}/cancel",
        "/api/v1/projects/{project}/configuration",
        "/api/v1/projects/{project}/configuration/preview",
        "/api/v1/projects/{project}/configuration/history",
        "/api/v1/projects/{project}/health",
    }
    assert required <= set(paths)
