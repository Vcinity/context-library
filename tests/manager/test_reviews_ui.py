import re
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
from test_auth_sessions import bearer
from test_overview_shell import app_and_client, csrf

from context_library_manager.configuration import apply_revision
from context_library_manager.domain import utc_now


def login(app, capability, subject):
    client = TestClient(app, base_url="https://testserver")
    response = client.post(
        "/auth/dev-login",
        json={
            "subject": subject,
            "display_name": subject,
            "capabilities": [capability],
            "projects": ["demo"],
        },
    )
    assert response.status_code == 200
    return client


def create_review(app, key, *, urgency="normal", reason="evidence-conflict"):
    work_id, _ = app.state.store.add_work(
        "demo",
        "candidate_task",
        key,
        {
            "urgency": urgency,
            "review_reason": reason,
            "owner": "operator-one",
            "source_type": "ticket",
            "alternatives": ["keep", "replace"],
            "recommendation": "Keep the evidence-backed option.",
            "nested": {"token": "must-not-leak", "note": "password=also-secret"},
        },
        "test",
    )
    return app.state.store.create_review(
        "demo",
        work_id,
        f"Resolve {key}",
        ["retain-current", "adopt-candidate"],
        ["ticket://SAFE token=evidence-secret"],
        "worker",
    )


def test_review_filters_pagination_detail_and_redaction(tmp_path):
    app, client = app_and_client(tmp_path)
    first = create_review(app, "first", urgency="critical", reason="conflict")
    create_review(app, "second", urgency="normal", reason="missing-authority")

    page = client.get(
        "/api/v1/projects/demo/reviews",
        params={"status": "open", "urgency": "critical", "page_size": 1},
    )
    assert page.status_code == 200
    data = page.json()["data"]
    assert data["total"] == 1
    assert [item["id"] for item in data["items"]] == [first]
    assert data["items"][0]["candidates"] == ["keep", "replace"]
    assert data["items"][0]["sla_seconds"] > 0
    assert "must-not-leak" not in page.text
    assert "also-secret" not in page.text
    assert "evidence-secret" not in page.text

    invalid = client.get("/api/v1/projects/demo/reviews", params={"urgency": "invented"})
    assert invalid.status_code == 422
    html = client.get("/reviews?status=open&urgency=critical")
    assert html.status_code == 200
    assert "Resolve first" in html.text
    assert "Resolve second" not in html.text
    plan = app.state.store.db.execute(
        "EXPLAIN QUERY PLAN SELECT r.id,r.project,r.work_id,r.question,r.status,"
        "r.created_at,r.updated_at,COALESCE(m.urgency,'normal') AS urgency,"
        "COALESCE(m.reason,'evidence-conflict') AS reason,m.source,m.owner,"
        "w.state AS work_state,w.payload AS work_payload FROM reviews r "
        "LEFT JOIN review_metadata m ON m.review_id=r.id "
        "LEFT JOIN work_items w ON w.id=r.work_id WHERE r.project=? "
        "ORDER BY r.status,r.created_at,r.id LIMIT ? OFFSET ?",
        ("demo", 25, 0),
    ).fetchall()
    assert all("TEMP B-TREE" not in row["detail"] for row in plan)


def test_review_sla_uses_effective_project_reminder_interval(tmp_path):
    app, client = app_and_client(tmp_path)
    review_id = create_review(app, "project-sla")
    apply_revision(
        app.state.store,
        app.state.settings,
        "demo",
        "human:admin",
        "test:review-sla",
        1,
        "shorten review interval",
        "review-sla",
        changes={"review_reminder_days": 2},
    )
    listing = client.get("/api/v1/projects/demo/reviews").json()["data"]
    assert listing["items"][0]["sla_seconds"] == 2 * 86400
    detail = client.get(f"/api/v1/projects/demo/reviews/{review_id}").json()["data"]
    assert detail["sla_seconds"] == 2 * 86400


def test_review_resolution_capability_idempotency_audit_and_race(tmp_path):
    app, _ = app_and_client(tmp_path)
    review_id = create_review(app, "resolve")
    path = f"/api/v1/projects/demo/reviews/{review_id}/resolve"
    body = {
        "choice": "retain-current",
        "rationale": "The current option has stronger evidence.",
        "idempotency_key": "resolve-once",
    }

    reader = login(app, "read", "reader")
    denied = reader.post(path, headers=csrf(reader, path), json=body)
    assert denied.status_code == 403

    reviewer = login(app, "review", "reviewer-one")
    first = reviewer.post(path, headers=csrf(reviewer, path), json=body)
    duplicate = reviewer.post(path, headers=csrf(reviewer, path), json=body)
    assert first.status_code == duplicate.status_code == 200
    assert duplicate.json()["data"]["idempotent"] is True
    audit_id = first.json()["data"]["audit_event_id"]
    assert audit_id and duplicate.json()["data"]["audit_event_id"] == audit_id
    assert (
        app.state.store.db.execute("SELECT COUNT(*) FROM review_evidence WHERE review_id=?", (review_id,)).fetchone()[0]
        == 1
    )

    conflict = reviewer.post(
        path,
        headers=csrf(reviewer, path),
        json={**body, "rationale": "Different", "idempotency_key": "resolve-once"},
    )
    assert conflict.status_code == 409
    assert conflict.json()["errors"][0]["code"] == "idempotency-conflict"

    other = login(app, "review", "reviewer-two")
    raced = other.post(
        path,
        headers=csrf(other, path),
        json={**body, "idempotency_key": "other-key"},
    )
    assert raced.status_code == 409
    assert raced.json()["errors"][0]["code"] == "review-already-resolved"
    event = app.state.store.db.execute(
        "SELECT event_type,created_at FROM audit_events WHERE id=?", (audit_id,)
    ).fetchone()
    assert event["event_type"] == "review-resolved"
    assert event["created_at"] <= utc_now()


def form_fields(page_text):
    return {
        "csrf_token": re.search(r"name='csrf_token' value='([^']+)'", page_text).group(1),
        "idempotency_key": re.search(r'name="idempotency_key" value="([^"]+)"', page_text).group(1),
    }


def test_no_javascript_resolution_has_actor_idempotency_links_and_conflict_evidence(
    tmp_path,
):
    app, _ = app_and_client(tmp_path)
    first_id = create_review(app, "nojs-first")
    reviewer = login(app, "review", "nojs-reviewer")
    page = reviewer.get(f"/reviews/{first_id}")
    fields = form_fields(page.text)
    body = {
        **fields,
        "choice": "retain-current",
        "rationale": "No-JS evidence supports the current option.",
    }
    result = reviewer.post(f"/reviews/{first_id}/resolve", data=body)
    repeat = reviewer.post(f"/reviews/{first_id}/resolve", data=body)
    assert result.status_code == repeat.status_code == 200
    assert "View audit event" in result.text
    assert "View resulting work" in result.text
    assert (
        app.state.store.db.execute(
            "SELECT COUNT(*) FROM review_evidence WHERE review_id=? AND kind='human-resolution'",
            (first_id,),
        ).fetchone()[0]
        == 1
    )
    actor = app.state.store.db.execute(
        "SELECT actor FROM audit_events WHERE work_id=(SELECT work_id FROM reviews WHERE id=?) "
        "AND event_type='review-resolved'",
        (first_id,),
    ).fetchone()["actor"]
    assert actor == "human:nojs-reviewer"

    raced_id = create_review(app, "nojs-race")
    losing_page = reviewer.get(f"/reviews/{raced_id}")
    losing_fields = form_fields(losing_page.text)
    winner = login(app, "review", "winner")
    api_path = f"/api/v1/projects/demo/reviews/{raced_id}/resolve"
    won = winner.post(
        api_path,
        headers=csrf(winner, api_path),
        json={
            "choice": "adopt-candidate",
            "rationale": "Winning evidence.",
            "idempotency_key": "winner-key",
        },
    )
    assert won.status_code == 200
    lost = reviewer.post(
        f"/reviews/{raced_id}/resolve",
        data={
            **losing_fields,
            "choice": "retain-current",
            "rationale": "Losing rationale must still be evidence.",
        },
    )
    assert lost.status_code == 409
    assert "Another reviewer resolved this item" in lost.text
    assert "adopt-candidate" in lost.text
    conflict = app.state.store.db.execute(
        "SELECT payload FROM review_evidence WHERE review_id=? AND kind='competing-resolution'",
        (raced_id,),
    ).fetchone()
    assert "Losing rationale must still be evidence" in conflict["payload"]
    assert "human:nojs-reviewer" in conflict["payload"]
    repeated_loss = reviewer.post(
        f"/reviews/{raced_id}/resolve",
        data={
            **losing_fields,
            "choice": "retain-current",
            "rationale": "Losing rationale must still be evidence.",
        },
    )
    assert repeated_loss.status_code == 409
    assert (
        app.state.store.db.execute(
            "SELECT COUNT(*) FROM audit_events WHERE work_id=(SELECT work_id FROM reviews WHERE id=?) "
            "AND event_type='review-resolution-conflict'",
            (raced_id,),
        ).fetchone()[0]
        == 1
    )


def test_simultaneous_reviewers_get_one_winner_and_one_conflict(tmp_path):
    app, _ = app_and_client(tmp_path)
    review_id = create_review(app, "simultaneous")
    clients = [TestClient(app, base_url="https://testserver") for _ in range(2)]
    path = f"/api/v1/projects/demo/reviews/{review_id}/resolve"
    headers = [
        {
            "Authorization": bearer(
                {
                    "sub": f"concurrent-{index}",
                    "preferred_username": f"concurrent-{index}",
                    "roles": ["reviewer"],
                    "projects": ["demo"],
                    "token_class": "human",
                    "exp": 4_000_000_000,
                }
            )
        }
        for index in range(2)
    ]

    def submit(index):
        return clients[index].post(
            path,
            headers=headers[index],
            json={
                "choice": "retain-current" if index == 0 else "adopt-candidate",
                "rationale": f"Concurrent rationale {index}",
                "idempotency_key": f"concurrent-{index}",
            },
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(submit, range(2)))
    assert sorted(response.status_code for response in responses) == [200, 409]
    kinds = app.state.store.db.execute(
        "SELECT kind,COUNT(*) AS n FROM review_evidence WHERE review_id=? GROUP BY kind",
        (review_id,),
    ).fetchall()
    assert {row["kind"]: row["n"] for row in kinds} == {
        "competing-resolution": 1,
        "human-resolution": 1,
    }
