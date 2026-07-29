from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from context_library_manager.db import Store
from context_library_manager.domain import utc_now
from context_library_manager.telemetry import (
    DEFAULT_PRODUCERS,
    append_event,
    autonomy_metrics,
    derive_slo_state,
    install_manifest,
    record_collector_error,
)

FIXTURES = Path(__file__).parent / "fixtures"
END = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def event(
    at: datetime,
    producer: str,
    event_type: str,
    item_id: str | None = None,
    payload: dict | None = None,
    actor_class: str = "runtime",
) -> dict:
    return {
        "at": at,
        "producer": producer,
        "event_type": event_type,
        "item_id": item_id,
        "payload": payload or {},
        "actor_class": actor_class,
    }


def seed_fixture(store: Store, project: str, target: bool) -> dict:
    values = fixture("autonomy_target_100.json" if target else "autonomy_100.json")
    effective = END - timedelta(minutes=5)
    install_manifest(
        store,
        project,
        "fixture-v1",
        DEFAULT_PRODUCERS,
        effective_at=effective,
    )
    events: list[dict] = []
    for producer in DEFAULT_PRODUCERS:
        for minute in range(6):
            events.append(event(effective + timedelta(minutes=minute), producer, "heartbeat"))

    serial = 0

    def add_item(
        prefix: str,
        *,
        outcome: str | None,
        eligibility: str = "eligible",
        mode: str = "deterministic",
        policy_revision: str = "policy-1",
        intake_actor: str = "automation",
    ) -> None:
        nonlocal serial
        item_id = f"{project}-{prefix}-{serial}"
        intake = effective + timedelta(seconds=15, milliseconds=serial * 10)
        events.append(
            event(
                intake,
                "work",
                "intake-accepted",
                item_id,
                {
                    "policy_revision": policy_revision,
                    "eligibility": eligibility,
                    "exclusion_reason": "policy-required-human" if eligibility == "excluded" else None,
                },
                intake_actor,
            )
        )
        if mode == "cache":
            events.append(
                event(
                    intake + timedelta(seconds=1),
                    "agent",
                    "cache-hit",
                    item_id,
                    {"cache_key": f"cache-{item_id}"},
                )
            )
        elif mode == "agent":
            events.append(
                event(
                    intake + timedelta(seconds=1),
                    "agent",
                    "agent-invocation",
                    item_id,
                    {
                        "invocation_id": f"invocation-{item_id}",
                        "reason": "semantic-applicability-judgment",
                        "deterministic_checks": ["schema", "identity", "topology"],
                        "cache_check": "miss",
                        "inappropriate": False,
                        "tokens": 100,
                        "cost": 0.01,
                    },
                )
            )
        if outcome:
            events.append(
                event(
                    intake + timedelta(seconds=2),
                    "work",
                    "state-transition",
                    item_id,
                    {"from": "queued", "to": outcome},
                )
            )
        serial += 1

    for _ in range(values["deterministic"]):
        add_item("deterministic", outcome="succeeded")
    for _ in range(values["semantic_cache_hits"]):
        add_item("cache", outcome="succeeded", mode="cache")
    if target:
        for _ in range(values["bounded_semantic_agent"]):
            add_item("agent", outcome="succeeded", mode="agent")
    else:
        for _ in range(values["invalid_or_budget_exhausted"]):
            add_item("invalid", outcome="failed")
    for _ in range(values["human_required"]):
        add_item("excluded", outcome=None, eligibility="excluded")

    for entry in sorted(
        events,
        key=lambda item: (
            item["at"],
            item["producer"],
            item["item_id"] or "",
            item["event_type"],
        ),
    ):
        append_event(
            store,
            project,
            entry["producer"],
            entry["event_type"],
            item_id=entry["item_id"],
            actor_class=entry["actor_class"],
            payload=entry["payload"],
            occurred_at=entry["at"],
            commit=False,
        )
    store.db.commit()
    return autonomy_metrics(store, project, window_end=END, production=False)


def heartbeat_span(store: Store, project: str, start: datetime, end: datetime) -> None:
    install_manifest(store, project, "focused-v1", DEFAULT_PRODUCERS, effective_at=start)
    point = start
    while point <= end:
        for producer in DEFAULT_PRODUCERS:
            append_event(store, project, producer, "heartbeat", occurred_at=point)
        point += timedelta(seconds=30)


def test_baseline_fixture_reports_honest_90_of_95(tmp_path):
    result = seed_fixture(Store(tmp_path / "baseline.db"), "baseline", False)
    assert result["numerator"] == 90
    assert result["denominator"] == 95
    assert result["exclusions"] == 5
    assert result["rate"] == pytest.approx(90 / 95)
    assert result["deterministic_only"] == 70
    assert result["cache_only"] == 20
    assert result["model_assisted"] == 0
    assert result["tokens"] == 0
    assert result["agent_invocations"] == 0
    assert result["inappropriate_agent_invocations"] == 0
    assert result["telemetry_status"] == "complete"
    assert result["history_status"] == "insufficient-history"
    assert result["slo_state"] == "insufficient-history"
    assert result["fixture_target_achieved"] is False


def test_target_fixture_reports_95_of_95_with_only_five_provider_calls(tmp_path):
    result = seed_fixture(Store(tmp_path / "target.db"), "target", True)
    assert result["numerator"] == result["denominator"] == 95
    assert result["exclusions"] == 5
    assert result["deterministic_only"] == 70
    assert result["cache_only"] == 20
    assert result["model_assisted"] == 5
    assert result["agent_invocations"] == 5
    assert result["agent_invocation_items"] == 5
    assert result["inappropriate_agent_invocations"] == 0
    assert result["tokens"] == 500
    assert result["fixture_target_achieved"] is True
    assert result["slo_state"] == "insufficient-history"


def test_human_intake_does_not_count_but_post_intake_intervention_does(tmp_path):
    store = Store(tmp_path / "human.db")
    seed_fixture(store, "human", True)
    intake = END - timedelta(minutes=2)
    for item_id, intervene in (("human-intake", False), ("human-after", True)):
        append_event(
            store,
            "human",
            "work",
            "intake-accepted",
            item_id=item_id,
            actor_class="human",
            payload={"policy_revision": "policy-1", "eligibility": "eligible"},
            occurred_at=intake,
        )
        if intervene:
            append_event(
                store,
                "human",
                "review",
                "human-intervention",
                item_id=item_id,
                actor_class="human",
                payload={"action": "review-resolved"},
                occurred_at=intake + timedelta(seconds=1),
            )
        append_event(
            store,
            "human",
            "work",
            "state-transition",
            item_id=item_id,
            payload={"from": "queued", "to": "succeeded"},
            occurred_at=intake + timedelta(seconds=2),
        )
    result = autonomy_metrics(store, "human", window_end=END, production=False)
    assert result["denominator"] == 97
    assert result["numerator"] == 96
    assert result["human_assisted"] == 1


def test_eligibility_is_intake_pinned_and_segmented(tmp_path):
    store = Store(tmp_path / "policy.db")
    seed_fixture(store, "policy", True)
    at = END - timedelta(minutes=1)
    append_event(
        store,
        "policy",
        "work",
        "intake-accepted",
        item_id="old-policy",
        payload={"policy_revision": "policy-old", "eligibility": "eligible"},
        occurred_at=at,
    )
    append_event(
        store,
        "policy",
        "policy",
        "policy-changed",
        item_id="old-policy",
        payload={"revision": "policy-new", "eligibility": "excluded"},
        occurred_at=at + timedelta(seconds=1),
    )
    append_event(
        store,
        "policy",
        "work",
        "state-transition",
        item_id="old-policy",
        payload={"from": "queued", "to": "succeeded"},
        occurred_at=at + timedelta(seconds=2),
    )
    result = autonomy_metrics(store, "policy", window_end=END, production=False)
    assert result["policy_segments"]["policy-old"]["denominator"] == 1
    assert result["policy_segments"]["policy-old"]["numerator"] == 1
    assert "policy-new" not in result["policy_segments"]


def test_unresolved_and_carried_in_work_remains_in_denominator(tmp_path):
    store = Store(tmp_path / "carry.db")
    seed_fixture(store, "carry", True)
    start = END - timedelta(days=30)
    append_event(
        store,
        "carry",
        "work",
        "intake-accepted",
        item_id="carried-stalled",
        payload={"policy_revision": "policy-1", "eligibility": "eligible"},
        occurred_at=start - timedelta(days=1),
    )
    append_event(
        store,
        "carry",
        "work",
        "intake-accepted",
        item_id="new-stalled",
        payload={"policy_revision": "policy-1", "eligibility": "eligible"},
        occurred_at=END - timedelta(minutes=1),
    )
    result = autonomy_metrics(store, "carry", window_end=END, production=False)
    assert result["denominator"] == 97
    assert result["numerator"] == 95
    assert len(result["non_terminal_backlog_by_age_seconds"]) == 2


@pytest.mark.parametrize(
    ("mutation", "expected_reason"),
    [
        ("sequence", "missing-project-sequence"),
        ("heartbeat", "late-heartbeat"),
        ("collector", "collector-outage"),
        ("policy", "missing-intake-policy-revision"),
        ("reconciliation", "replay-reconciliation"),
    ],
)
def test_coverage_gaps_prevent_target_claim(tmp_path, mutation, expected_reason):
    store = Store(tmp_path / f"{mutation}.db")
    seed_fixture(store, "gaps", True)
    if mutation == "sequence":
        row = store.db.execute(
            "SELECT id FROM telemetry_events WHERE project='gaps' ORDER BY project_sequence LIMIT 1 OFFSET 2"
        ).fetchone()
        store.db.execute("DELETE FROM telemetry_events WHERE id=?", (row["id"],))
    elif mutation == "heartbeat":
        store.db.execute(
            "UPDATE telemetry_events SET occurred_at=? WHERE project='gaps' "
            "AND producer='notification' AND event_type='heartbeat' "
            "AND producer_sequence=3",
            ((END - timedelta(minutes=2, seconds=30)).isoformat(),),
        )
    elif mutation == "collector":
        record_collector_error(
            store,
            "gaps",
            "collector-outage",
            END - timedelta(minutes=2),
            END - timedelta(minutes=1),
            producer="work",
        )
    elif mutation == "policy":
        store.db.execute(
            "UPDATE telemetry_events SET payload=? WHERE id=("
            "SELECT id FROM telemetry_events WHERE project='gaps' AND event_type='intake-accepted' LIMIT 1)",
            ('{"eligibility":"eligible"}',),
        )
    else:
        store.db.execute(
            "UPDATE telemetry_item_state SET current_state='failed' WHERE item_id=("
            "SELECT item_id FROM telemetry_events WHERE project='gaps' "
            "AND event_type='intake-accepted' LIMIT 1)"
        )
    store.db.commit()
    result = autonomy_metrics(store, "gaps", window_end=END, production=False)
    assert result["telemetry_status"] == "insufficient-telemetry"
    assert result["slo_state"] == "insufficient-telemetry"
    assert result["fixture_target_achieved"] is False
    assert any(expected_reason in gap["reason"] for gap in result["coverage_gaps"])


def test_inappropriate_agent_call_and_retries_cannot_hide_usage(tmp_path):
    store = Store(tmp_path / "invocations.db")
    seed_fixture(store, "invocations", True)
    item_id = store.db.execute(
        "SELECT item_id FROM telemetry_events WHERE project='invocations' "
        "AND event_type='intake-accepted' AND item_id LIKE '%deterministic%' LIMIT 1"
    ).fetchone()["item_id"]
    at = END - timedelta(seconds=30)
    for invocation in ("retry-1", "retry-2"):
        append_event(
            store,
            "invocations",
            "agent",
            "agent-invocation",
            item_id=item_id,
            payload={
                "invocation_id": invocation,
                "reason": "unnecessary",
                "deterministic_checks": [],
                "cache_check": "hit",
                "inappropriate": True,
                "tokens": 10,
                "cost": 0.01,
            },
            occurred_at=at,
        )
    result = autonomy_metrics(store, "invocations", window_end=END, production=False)
    assert result["agent_invocations"] == 7
    assert result["agent_invocation_items"] == 6
    assert result["inappropriate_agent_invocations"] == 2
    assert result["fixture_target_achieved"] is False


@pytest.mark.parametrize(
    ("history", "telemetry", "denominator", "rate", "inappropriate", "expected"),
    [
        ("insufficient-history", "insufficient-telemetry", 95, 1.0, 0, "insufficient-telemetry"),
        ("insufficient-history", "complete", 95, 1.0, 0, "insufficient-history"),
        ("complete", "complete", 0, None, 0, "no-data"),
        ("complete", "complete", 95, 1.0, 0, "met"),
        ("complete", "complete", 95, 1.0, 1, "missed"),
        ("complete", "complete", 95, 0.94, 0, "missed"),
    ],
)
def test_slo_state_precedence(history, telemetry, denominator, rate, inappropriate, expected):
    assert derive_slo_state(history, telemetry, denominator, rate, inappropriate) == expected


def test_failure_and_escalation_rates_use_their_own_transitions(tmp_path):
    store = Store(tmp_path / "rates.db")
    start = datetime.now(timezone.utc) - timedelta(minutes=1)
    failed, _ = store.add_work("rates", "candidate_task", "failed", {}, "automation:test")
    store.transition("rates", failed, "leased", "worker")
    store.transition("rates", failed, "running", "worker")
    store.transition("rates", failed, "failed", "worker")
    assisted, _ = store.add_work("rates", "candidate_task", "assisted", {}, "automation:test")
    store.transition("rates", assisted, "leased", "worker")
    store.transition("rates", assisted, "running", "worker")
    store.transition("rates", assisted, "waiting-human", "worker", "authority")
    store.event(assisted, "human:reviewer", "review-resolved", {"choice": "retain-current"}, "rates")
    store.transition("rates", assisted, "succeeded", "human:reviewer")
    end = datetime.now(timezone.utc) + timedelta(seconds=1)
    heartbeat_span(store, "rates", start, end)
    result = autonomy_metrics(store, "rates", window_end=end, window_days=1, production=False)
    assert result["failure_rate"] == pytest.approx(0.5)
    assert result["human_escalation_rate"] == pytest.approx(0.5)


def test_review_materialized_state_without_event_creates_named_gap(tmp_path):
    store = Store(tmp_path / "review-gap.db")
    start = datetime.now(timezone.utc) - timedelta(minutes=1)
    work_id, _ = store.add_work("review-gap", "candidate_task", "one", {}, "automation:test")
    review_id = store.create_review("review-gap", work_id, "Choose", ["a", "b"], [], "worker")
    store.db.execute("UPDATE reviews SET status='resolved' WHERE id=?", (review_id,))
    store.db.commit()
    end = datetime.now(timezone.utc) + timedelta(seconds=1)
    heartbeat_span(store, "review-gap", start, end)
    result = autonomy_metrics(store, "review-gap", window_end=end, window_days=1, production=False)
    assert result["telemetry_status"] == "insufficient-telemetry"
    assert any(gap["reason"] == f"review-replay-reconciliation:{review_id}" for gap in result["coverage_gaps"])


def test_pending_review_notification_replays_without_a_reconciliation_gap(tmp_path):
    store = Store(tmp_path / "notification-complete.db")
    start = datetime.now(timezone.utc) - timedelta(minutes=1)
    install_manifest(store, "notification-complete", "focused-v1", DEFAULT_PRODUCERS, effective_at=start)
    for point in (start, start + timedelta(seconds=30)):
        for producer in DEFAULT_PRODUCERS:
            append_event(store, "notification-complete", producer, "heartbeat", occurred_at=point)
    work_id, _ = store.add_work("notification-complete", "candidate_task", "one", {}, "automation:test")
    review_id = store.create_review(
        "notification-complete",
        work_id,
        "Choose",
        ["retain", "adopt"],
        [],
        "worker",
    )
    notification = store.db.execute(
        "SELECT id FROM notifications WHERE review_id=?",
        (review_id,),
    ).fetchone()
    event = store.db.execute(
        "SELECT payload FROM telemetry_events WHERE producer='notification' "
        "AND event_type='notification-enqueued' AND item_id=?",
        (work_id,),
    ).fetchone()
    assert json.loads(event["payload"]) == {
        "notification_id": notification["id"],
        "review_id": review_id,
        "status": "pending",
    }
    end = datetime.now(timezone.utc) + timedelta(seconds=1)
    for producer in DEFAULT_PRODUCERS:
        append_event(store, "notification-complete", producer, "heartbeat", occurred_at=end)
    result = autonomy_metrics(
        store,
        "notification-complete",
        window_end=end,
        window_days=1,
        production=False,
    )
    assert result["telemetry_status"] == "complete"
    assert not any("notification-replay-reconciliation" in gap["reason"] for gap in result["coverage_gaps"])


def test_cancellation_materialized_state_is_reconciled_from_agent_events(tmp_path):
    store = Store(tmp_path / "cancellation-replay.db")
    project = "cancellation-replay"
    start = datetime.now(timezone.utc) - timedelta(seconds=2)
    install_manifest(store, project, "focused-v1", DEFAULT_PRODUCERS, effective_at=start)
    for producer in DEFAULT_PRODUCERS:
        append_event(store, project, producer, "heartbeat", occurred_at=start)
    work_id, _ = store.add_work(project, "candidate_task", "cancel", {}, "automation:test")
    store.claim_work(project, work_id, "worker")
    store.transition(project, work_id, "running", "worker")
    store.start_agent_run("run-cancel", work_id, "cheap", "worker", "1", "cache")
    store.transition(project, work_id, "cancel-requested", "human:operator")
    store.db.execute(
        "INSERT INTO work_cancellations VALUES(?,?,?,?,?,?,?,?,?)",
        (
            "cancel-one",
            work_id,
            "run-cancel",
            "cancel-requested",
            "human:operator",
            "stop",
            "cancel-key",
            utc_now(),
            None,
        ),
    )
    append_event(
        store,
        project,
        "agent",
        "agent-cancel-requested",
        item_id=work_id,
        payload={"invocation_id": "run-cancel", "status": "cancel-requested"},
        commit=False,
    )
    store.db.commit()
    store.acknowledge_cancellation(project, work_id, "run-cancel", "worker")
    end = datetime.now(timezone.utc) + timedelta(seconds=1)
    for producer in DEFAULT_PRODUCERS:
        append_event(store, project, producer, "heartbeat", occurred_at=end)

    complete = autonomy_metrics(store, project, window_end=end, window_days=1, production=False)
    assert complete["telemetry_status"] == "complete"
    assert not any("cancellation-replay-reconciliation" in gap["reason"] for gap in complete["coverage_gaps"])

    store.db.execute(
        "UPDATE work_cancellations SET state='cancel-requested',acknowledged_at=NULL WHERE id='cancel-one'"
    )
    store.db.commit()
    divergent = autonomy_metrics(store, project, window_end=end, window_days=1, production=False)
    assert divergent["telemetry_status"] == "insufficient-telemetry"
    assert any(gap["reason"] == "cancellation-replay-reconciliation:cancel-one" for gap in divergent["coverage_gaps"])
