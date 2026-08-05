from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone

from test_overview_shell import app_and_client

from context_library_manager.domain import utc_now
from context_library_manager.processes import _heartbeat_loop


def timestamp(seconds_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).isoformat().replace("+00:00", "Z")


def test_production_heartbeat_loop_runs_independently_of_worker_work(tmp_path):
    app, _ = app_and_client(tmp_path)
    stop = threading.Event()
    thread = threading.Thread(
        target=_heartbeat_loop,
        args=("worker", app.state.settings, stop, 0.01),
    )
    thread.start()
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        rows = app.state.store.db.execute(
            "SELECT producer,COUNT(*) AS n FROM telemetry_events WHERE event_type='heartbeat' GROUP BY producer"
        ).fetchall()
        counts = {row["producer"]: row["n"] for row in rows}
        if counts.get("work", 0) >= 2 and counts.get("agent", 0) >= 2:
            break
        time.sleep(0.01)
    stop.set()
    thread.join(timeout=1)
    assert not thread.is_alive()
    rows = app.state.store.db.execute(
        "SELECT producer,COUNT(*) AS n FROM telemetry_events WHERE event_type='heartbeat' GROUP BY producer"
    ).fetchall()
    counts = {row["producer"]: row["n"] for row in rows}
    assert counts["work"] >= 2
    assert counts["agent"] >= 2


def test_runtime_health_reports_process_freshness_backlogs_and_failures(tmp_path):
    app, client = app_and_client(tmp_path)
    store = app.state.store
    for process, age in (
        ("worker", 60),
        ("scheduler", 5),
        ("notification", 120),
        ("reconciliation", 5),
    ):
        store.heartbeat(process, f"{process}-1")
        store.db.execute(
            "UPDATE process_heartbeats SET observed_at=? WHERE process=?",
            (timestamp(age), process),
        )
    work_id, _ = store.add_work("demo", "semantic_task", "retry-health", {}, "test")
    store.db.execute("UPDATE work_items SET state='retryable' WHERE id=?", (work_id,))
    store.db.execute(
        "INSERT INTO reviews VALUES(?,?,?,?,?,?,?,?,?,?)",
        (
            "review-health",
            "demo",
            work_id,
            "q",
            '["a","b"]',
            "resolved",
            "[]",
            "{}",
            utc_now(),
            utc_now(),
        ),
    )
    store.db.execute(
        "INSERT INTO notifications(id,review_id,status,created_at,attempts,last_error) VALUES(?,?,?,?,?,?)",
        ("notice-health", "review-health", "pending", utc_now(), 2, "delivery failed"),
    )
    store.db.commit()

    data = client.get("/api/v1/health").json()["data"]
    states = {item["process"]: item["state"] for item in data["heartbeats"]}
    assert states["api"] == "healthy"
    assert states["worker"] == "degraded"
    assert states["notification"] == "offline"
    assert data["status"] == "offline"
    assert data["retry_backlog"] == 1
    assert data["notification_failures"] == 1
    page = client.get("/health")
    assert page.status_code == 200
    assert "delivery" not in page.text
    assert "Notification failures" in page.text


def test_public_health_omits_project_budget_and_maintenance_details(tmp_path):
    app, client = app_and_client(tmp_path)
    store = app.state.store
    store.db.execute("INSERT INTO project_budgets VALUES(?,?,?,?)", ("demo", "2026-07-17", 5, 9))
    store.event("private-work", "human:private", "retry-requested", {}, "demo")
    store.db.commit()
    public = client.get("/api/v1/health").json()["data"]
    assert public["budgets"] == []
    assert public["last_maintenance_actions"] == []
    assert all(item["details"] == {} for item in public["heartbeats"])
    scoped = client.get("/api/v1/projects/demo/health").json()["data"]
    assert scoped["budgets"][0]["project"] == "demo"


def test_project_health_does_not_expose_other_project_activity(tmp_path):
    app, client = app_and_client(tmp_path, projects=("demo", "other"))
    store = app.state.store
    other_work, _ = store.add_work("other", "semantic_task", "other-retry", {}, "test")
    store.db.execute("UPDATE work_items SET state='retryable' WHERE id=?", (other_work,))
    store.event(other_work, "human:other", "retry-requested", {}, "other")
    store.db.commit()
    scoped = client.get("/api/v1/projects/demo/health").json()["data"]
    assert scoped["retry_backlog"] == 0
    assert all(item["project"] == "demo" for item in scoped["last_maintenance_actions"])


def test_review_payload_redacts_secret_bearing_field_names(tmp_path):
    app, client = app_and_client(tmp_path)
    store = app.state.store
    work_id, _ = store.add_work(
        "demo",
        "semantic_task",
        "secret-review",
        {
            "client_secret": "client-value",
            "authorization": "Bearer auth-value",
            "nested": {"webhook_secret": "hook-value", "safe": "visible"},
        },
        "test",
    )
    review_id = store.create_review("demo", work_id, "Inspect safely", ["retain-current", "retry"], [], "test")
    detail = client.get(f"/api/v1/projects/demo/reviews/{review_id}")
    assert detail.status_code == 200
    rendered = detail.text
    assert "client-value" not in rendered
    assert "auth-value" not in rendered
    assert "hook-value" not in rendered
    assert "visible" in rendered


def test_publication_failure_is_redacted_and_preserves_recovery_guidance(tmp_path):
    app, client = app_and_client(tmp_path)
    store = app.state.store
    store.db.execute(
        "INSERT INTO publication_history(id,project,status,digest,git_revision,created_at) VALUES(?,?,?,?,?,?)",
        ("publication-good", "demo", "succeeded", "a" * 64, "good-rev", timestamp(60)),
    )
    store.db.execute(
        "INSERT INTO publication_history(id,project,status,error,created_at) VALUES(?,?,?,?,?)",
        ("publication-failed", "demo", "failed", "api_key=do-not-render", utc_now()),
    )
    store.db.commit()
    api = client.get("/api/v1/projects/demo/publications").json()["data"]
    assert api["items"][0]["status"] == "failed"
    assert "do-not-render" not in api["items"][0]["error"]
    assert api["items"][1]["git_revision"] == "good-rev"
    page = client.get("/publications")
    assert page.status_code == 200
    assert "Publication failed" in page.text
    assert "last known-good content remains canonical" in page.text
    assert "do-not-render" not in page.text


def test_audit_search_is_scoped_paginated_indexed_and_redacted(tmp_path):
    app, client = app_and_client(tmp_path, projects=("demo", "other"))
    store = app.state.store
    for index in range(3):
        store.event(
            None,
            "human:auditor",
            "configuration-updated",
            {
                "run_id": f"work-run-{index}",
                "capability": "admin",
                "policy_revision": index + 1,
                "before_reference": f"policy:{index}",
                "after_reference": f"policy:{index + 1}",
                "secret": "never-visible",
            },
            "demo",
        )
    store.event(None, "human:other", "configuration-updated", {}, "other")
    store.db.commit()

    response = client.get(
        "/api/v1/projects/demo/audit",
        params={
            "actor": "human:auditor",
            "action": "configuration-updated",
            "page_size": 2,
        },
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == 3
    assert len(data["items"]) == 2
    assert data["next_page"] == 2
    assert all(item["project"] == "demo" for item in data["items"])
    assert all("secret" not in item["payload"] for item in data["items"])
    assert data["items"][0]["run_id"].startswith("work-run-")
    assert client.get("/api/v1/projects/demo/audit", params={"capability": "not-real"}).status_code == 422

    plan = store.db.execute(
        "EXPLAIN QUERY PLAN SELECT * FROM audit_events WHERE project=? AND actor=? ORDER BY created_at DESC,id LIMIT ?",
        ("demo", "human:auditor", 25),
    ).fetchall()
    assert "idx_audit_project_actor_time" in " ".join(str(tuple(row)) for row in plan)
