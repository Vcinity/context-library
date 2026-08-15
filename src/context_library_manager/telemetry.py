from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any, Iterable

SUCCESS = {"succeeded", "published"}
TERMINAL = SUCCESS | {"failed", "canceled", "rejected"}
QUALIFYING_HUMAN_EVENTS = {
    "review-resolved",
    "human-review",
    "human-approval",
    "human-resolution",
    "evidence-edited",
    "candidate-edited",
    "policy-override",
    "manual-retry",
    "retry-requested",
    "manual-requeue",
    "human-cancellation",
    "terminal-override",
}
DEFAULT_PRODUCERS = ("work", "review", "policy", "agent", "notification")
EVIDENCE_PRODUCERS = ("work", "review", "policy", "agent-invocation", "notification")
EVIDENCE_PRODUCER_ALIASES = {"agent": "agent-invocation"}


def _utc(value: datetime | str | None = None) -> str:
    if value is None:
        parsed = datetime.now(timezone.utc)
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        parsed = value
    if parsed.tzinfo is None:
        raise ValueError("telemetry timestamps require timezone information")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def install_manifest(
    store,
    project: str,
    revision: str,
    required_producers: Iterable[str] = DEFAULT_PRODUCERS,
    *,
    effective_at: datetime | str | None = None,
) -> None:
    producers = sorted(set(required_producers))
    if not producers:
        raise ValueError("telemetry manifest requires at least one producer")
    store.db.execute(
        "INSERT INTO telemetry_manifests(id,project,revision,required_producers,effective_at) "
        "VALUES(?,?,?,?,?) ON CONFLICT(project,revision) DO NOTHING",
        (
            f"manifest_{store.digest([project, revision])[:24]}",
            project,
            revision,
            json.dumps(producers),
            _utc(effective_at),
        ),
    )
    store.db.commit()


def append_event(
    store,
    project: str,
    producer: str,
    event_type: str,
    *,
    item_id: str | None = None,
    actor_class: str = "runtime",
    payload: dict[str, Any] | None = None,
    occurred_at: datetime | str | None = None,
    project_sequence: int | None = None,
    producer_sequence: int | None = None,
    commit: bool = True,
) -> dict[str, Any]:
    at = _utc(occurred_at)
    payload = payload or {}
    with store._write_lock:
        if project_sequence is None:
            counter = store.db.execute(
                "INSERT INTO telemetry_project_counters(project,next_sequence) VALUES(?,2) "
                "ON CONFLICT(project) DO UPDATE SET "
                "next_sequence=telemetry_project_counters.next_sequence+1 "
                "RETURNING next_sequence",
                (project,),
            ).fetchone()
            project_sequence = int(counter["next_sequence"]) - 1
        else:
            store.db.execute(
                "INSERT INTO telemetry_project_counters(project,next_sequence) VALUES(?,?) "
                "ON CONFLICT(project) DO UPDATE SET next_sequence="
                "CASE WHEN telemetry_project_counters.next_sequence<excluded.next_sequence "
                "THEN excluded.next_sequence ELSE telemetry_project_counters.next_sequence END",
                (project, project_sequence + 1),
            )
        if producer_sequence is None:
            counter = store.db.execute(
                "INSERT INTO telemetry_producer_counters(project,producer,next_sequence) VALUES(?,?,2) "
                "ON CONFLICT(project,producer) DO UPDATE SET "
                "next_sequence=telemetry_producer_counters.next_sequence+1 "
                "RETURNING next_sequence",
                (project, producer),
            ).fetchone()
            producer_sequence = int(counter["next_sequence"]) - 1
        else:
            store.db.execute(
                "INSERT INTO telemetry_producer_counters(project,producer,next_sequence) VALUES(?,?,?) "
                "ON CONFLICT(project,producer) DO UPDATE SET next_sequence="
                "CASE WHEN telemetry_producer_counters.next_sequence<excluded.next_sequence "
                "THEN excluded.next_sequence ELSE telemetry_producer_counters.next_sequence END",
                (project, producer, producer_sequence + 1),
            )
        event_id = f"telemetry_{store.digest([project, project_sequence, producer, producer_sequence])[:24]}"
        store.db.execute(
            "INSERT INTO telemetry_events(id,project,project_sequence,producer,producer_sequence,"
            "item_id,event_type,actor_class,payload,occurred_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                event_id,
                project,
                project_sequence,
                producer,
                producer_sequence,
                item_id,
                event_type,
                actor_class,
                json.dumps(payload, sort_keys=True),
                at,
            ),
        )
        store.db.execute(
            "INSERT INTO telemetry_watermarks(project,producer,producer_sequence,occurred_at) "
            "VALUES(?,?,?,?) ON CONFLICT(project,producer) DO UPDATE SET "
            "producer_sequence=excluded.producer_sequence,occurred_at=excluded.occurred_at",
            (project, producer, producer_sequence, at),
        )
        if item_id and event_type == "intake-accepted":
            store.db.execute(
                "INSERT INTO telemetry_item_state(item_id,project,intake_at,policy_revision,"
                "eligibility,current_state,last_event_sequence,updated_at) VALUES(?,?,?,?,?,?,?,?) "
                "ON CONFLICT(item_id) DO NOTHING",
                (
                    item_id,
                    project,
                    at,
                    str(payload.get("policy_revision", "")),
                    str(payload.get("eligibility", "")),
                    "queued",
                    project_sequence,
                    at,
                ),
            )
        elif item_id:
            state = payload.get("to") if event_type == "state-transition" else None
            if state:
                store.db.execute(
                    "UPDATE telemetry_item_state SET current_state=?,last_event_sequence=?,updated_at=? "
                    "WHERE item_id=?",
                    (state, project_sequence, at, item_id),
                )
            else:
                store.db.execute(
                    "UPDATE telemetry_item_state SET last_event_sequence=?,updated_at=? WHERE item_id=?",
                    (project_sequence, at, item_id),
                )
        if commit:
            store.db.commit()
    return {
        "id": event_id,
        "project_sequence": project_sequence,
        "producer_sequence": producer_sequence,
    }


def record_collector_error(
    store,
    project: str,
    reason: str,
    start: datetime | str,
    end: datetime | str,
    *,
    producer: str | None = None,
    reconciled: bool = False,
) -> str:
    identifier = f"coverage_{store.digest([project, producer, reason, _utc(start), _utc(end)])[:24]}"
    store.db.execute(
        "INSERT INTO telemetry_collector_errors(id,project,producer,gap_start,gap_end,reason,reconciled) "
        "VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET reconciled=excluded.reconciled",
        (identifier, project, producer, _utc(start), _utc(end), reason, int(reconciled)),
    )
    store.db.commit()
    return identifier


def _gap(
    gaps: list[dict[str, Any]],
    producer: str | None,
    start: str,
    end: str,
    reason: str,
    reconciled: bool = False,
) -> None:
    gaps.append(
        {
            "producer": producer,
            "start": start,
            "end": end,
            "reason": reason,
            "reconciliation_state": "reconciled" if reconciled else "unreconciled",
        }
    )


def _coverage(
    store,
    project: str,
    start: datetime,
    end: datetime,
    cohort: dict[str, dict[str, Any]],
    events: list[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]], str | None, datetime | None]:
    gaps: list[dict[str, Any]] = []
    start_text, end_text = _utc(start), _utc(end)
    manifests = store.db.execute(
        "SELECT revision,required_producers,effective_at FROM telemetry_manifests "
        "WHERE project=? AND effective_at<=? ORDER BY effective_at",
        (project, end_text),
    ).fetchall()
    manifest_revision: str | None = None
    history_start: datetime | None = None
    segments: list[tuple[datetime, datetime, list[str]]] = []
    if not manifests:
        _gap(gaps, None, start_text, end_text, "missing-required-producer-manifest")
    else:
        history_start = _parse(manifests[0]["effective_at"])
        applicable = [row for row in manifests if _parse(row["effective_at"]) <= end]
        for index, row in enumerate(applicable):
            row_start = max(start, _parse(row["effective_at"]))
            row_end = min(end, _parse(applicable[index + 1]["effective_at"])) if index + 1 < len(applicable) else end
            if row_start < row_end and (
                index == len(applicable) - 1 or _parse(applicable[index + 1]["effective_at"]) > start
            ):
                segments.append((row_start, row_end, json.loads(row["required_producers"])))
        manifest_revision = applicable[-1]["revision"]
    coverage_start = max(start, history_start) if history_start else start
    coverage_events = [event for event in events if coverage_start <= _parse(event["occurred_at"]) <= end]

    sequences = [event["project_sequence"] for event in coverage_events]
    predecessor = store.db.execute(
        "SELECT MAX(project_sequence) AS sequence FROM telemetry_events WHERE project=? AND occurred_at<?",
        (project, _utc(coverage_start)),
    ).fetchone()
    expected_first = int(predecessor["sequence"] or 0) + 1
    if sequences and sequences[0] != expected_first:
        _gap(gaps, None, start_text, end_text, f"missing-project-sequence:{expected_first}")
    for prior, current in zip(sequences, sequences[1:]):
        if current != prior + 1:
            _gap(gaps, None, start_text, end_text, f"missing-project-sequence:{prior + 1}")
    for prior, current in zip(coverage_events, coverage_events[1:]):
        if _parse(current["occurred_at"]) < _parse(prior["occurred_at"]):
            _gap(
                gaps,
                current["producer"],
                current["occurred_at"],
                prior["occurred_at"],
                "clock-ordering",
            )

    required = sorted({producer for _, _, producers in segments for producer in producers})
    for producer in required:
        producer_events = [event for event in coverage_events if event["producer"] == producer]
        producer_sequences = [event["producer_sequence"] for event in producer_events]
        predecessor = store.db.execute(
            "SELECT MAX(producer_sequence) AS sequence FROM telemetry_events "
            "WHERE project=? AND producer=? AND occurred_at<?",
            (project, producer, _utc(coverage_start)),
        ).fetchone()
        expected_first = int(predecessor["sequence"] or 0) + 1
        if producer_sequences and producer_sequences[0] != expected_first:
            _gap(
                gaps,
                producer,
                start_text,
                end_text,
                f"missing-producer-sequence:{expected_first}",
            )
        for prior, current in zip(producer_sequences, producer_sequences[1:]):
            if current != prior + 1:
                _gap(
                    gaps,
                    producer,
                    start_text,
                    end_text,
                    f"missing-producer-sequence:{prior + 1}",
                )
        for segment_start, segment_end, producers in segments:
            if producer not in producers:
                continue
            heartbeats = [
                _parse(event["occurred_at"])
                for event in events
                if event["producer"] == producer
                and event["event_type"] == "heartbeat"
                and segment_start - timedelta(seconds=60) <= _parse(event["occurred_at"]) <= segment_end
            ]
            if not heartbeats:
                _gap(gaps, producer, _utc(segment_start), _utc(segment_end), "missing-heartbeat")
                continue
            heartbeats.sort()
            points = [
                segment_start,
                *[point for point in heartbeats if segment_start <= point <= segment_end],
                segment_end,
            ]
            for left, right in zip(points, points[1:]):
                if (right - left).total_seconds() > 60:
                    _gap(gaps, producer, _utc(left), _utc(right), "late-heartbeat")
        maximum = max(
            (
                event["producer_sequence"]
                for event in events
                if event["producer"] == producer and _parse(event["occurred_at"]) <= end
            ),
            default=0,
        )
        watermark = store.db.execute(
            "SELECT producer_sequence FROM telemetry_watermarks WHERE project=? AND producer=?",
            (project, producer),
        ).fetchone()
        if not watermark or watermark["producer_sequence"] < maximum:
            _gap(gaps, producer, start_text, end_text, "unknown-producer-watermark")

    for item_id, item in cohort.items():
        intake = item.get("intake")
        if not intake:
            _gap(gaps, "work", start_text, end_text, f"missing-intake:{item_id}")
            continue
        if not intake["payload"].get("policy_revision"):
            _gap(gaps, "policy", intake["occurred_at"], end_text, f"missing-intake-policy-revision:{item_id}")
        if intake["payload"].get("eligibility") not in {"eligible", "excluded"}:
            _gap(gaps, "policy", intake["occurred_at"], end_text, f"missing-eligibility:{item_id}")
        expected = "queued"
        last_sequence = intake["project_sequence"]
        for event in item["events"]:
            if event["event_type"] != "state-transition":
                last_sequence = event["project_sequence"]
                continue
            payload = event["payload"]
            if payload.get("from") != expected:
                _gap(gaps, "work", event["occurred_at"], event["occurred_at"], f"item-lineage:{item_id}")
                break
            expected = payload.get("to", "")
            last_sequence = event["project_sequence"]
        materialized = store.db.execute(
            "SELECT current_state,last_event_sequence FROM telemetry_item_state WHERE item_id=?",
            (item_id,),
        ).fetchone()
        if (
            not materialized
            or materialized["current_state"] != expected
            or materialized["last_event_sequence"] != last_sequence
        ):
            _gap(gaps, "work", intake["occurred_at"], end_text, f"replay-reconciliation:{item_id}")

    cohort_ids = set(cohort)
    reviews = store.db.execute(
        "SELECT id,work_id,status,created_at FROM reviews WHERE project=? AND created_at<=?",
        (project, end_text),
    ).fetchall()
    for review in reviews:
        if review["work_id"] not in cohort_ids:
            continue
        matching = [
            event
            for event in events
            if event["producer"] == "review"
            and event["payload"].get("review_id") == review["id"]
            and _parse(event["occurred_at"]) <= end
        ]
        replayed = "resolved" if any(event["event_type"] == "review-resolved" for event in matching) else "open"
        if not matching or replayed != review["status"]:
            _gap(
                gaps,
                "review",
                review["created_at"],
                end_text,
                f"review-replay-reconciliation:{review['id']}",
            )

    notifications = store.db.execute(
        "SELECT n.id,n.status,n.created_at,r.work_id FROM notifications n "
        "JOIN reviews r ON r.id=n.review_id WHERE r.project=? AND n.created_at<=?",
        (project, end_text),
    ).fetchall()
    for notification in notifications:
        if notification["work_id"] not in cohort_ids:
            continue
        matching = [
            event
            for event in events
            if event["producer"] == "notification"
            and event["payload"].get("notification_id") == notification["id"]
            and _parse(event["occurred_at"]) <= end
        ]
        replayed = "pending"
        for event in matching:
            replayed = str(event["payload"].get("status", replayed))
        if not matching or replayed != notification["status"]:
            _gap(
                gaps,
                "notification",
                notification["created_at"],
                end_text,
                f"notification-replay-reconciliation:{notification['id']}",
            )

    agent_runs = store.db.execute(
        "SELECT ar.id,ar.work_id,ar.status,ar.created_at FROM agent_runs ar "
        "JOIN work_items w ON w.id=ar.work_id WHERE w.project=? AND ar.created_at<=?",
        (project, end_text),
    ).fetchall()
    for run in agent_runs:
        if run["work_id"] not in cohort_ids:
            continue
        matching = [
            event
            for event in events
            if event["producer"] == "agent"
            and event["payload"].get("invocation_id") == run["id"]
            and _parse(event["occurred_at"]) <= end
        ]
        replayed = None
        for event in matching:
            if event["event_type"] == "agent-invocation":
                replayed = "running"
            elif event["event_type"] == "agent-cancel-requested":
                replayed = "cancel-requested"
            elif event["event_type"] == "cache-hit":
                replayed = str(event["payload"].get("status", "ok"))
            elif event["event_type"] == "agent-completed":
                replayed = str(event["payload"].get("status"))
        if replayed != run["status"]:
            _gap(gaps, "agent", run["created_at"], end_text, f"agent-replay-reconciliation:{run['id']}")

    cancellations = store.db.execute(
        "SELECT wc.id,wc.work_id,wc.agent_run_id,wc.state,wc.requested_at "
        "FROM work_cancellations wc JOIN work_items w ON w.id=wc.work_id "
        "WHERE w.project=? AND wc.requested_at<=?",
        (project, end_text),
    ).fetchall()
    for cancellation in cancellations:
        if cancellation["work_id"] not in cohort_ids:
            continue
        matching = [
            event
            for event in events
            if event["producer"] == "agent"
            and event["payload"].get("invocation_id") == cancellation["agent_run_id"]
            and event["event_type"] in {"agent-cancel-requested", "agent-completed"}
            and _parse(event["occurred_at"]) <= end
        ]
        replayed = None
        for event in matching:
            if event["event_type"] == "agent-cancel-requested":
                replayed = "cancel-requested"
            elif event["payload"].get("status") == "canceled":
                replayed = "canceled"
        if replayed != cancellation["state"]:
            _gap(
                gaps,
                "agent",
                cancellation["requested_at"],
                end_text,
                f"cancellation-replay-reconciliation:{cancellation['id']}",
            )

    policy = store.db.execute(
        "SELECT revision,created_at FROM policy_revisions WHERE project=? AND created_at<=? "
        "ORDER BY CAST(revision AS INTEGER) DESC,created_at DESC LIMIT 1",
        (project, end_text),
    ).fetchone()
    if policy:
        policy_events = [
            event
            for event in events
            if event["producer"] == "policy"
            and event["event_type"] in {"policy-snapshot", "policy-revision-applied"}
            and _parse(event["occurred_at"]) <= end
        ]
        replayed_revision = str(policy_events[-1]["payload"].get("revision")) if policy_events else None
        if replayed_revision != str(policy["revision"]):
            _gap(
                gaps,
                "policy",
                policy["created_at"],
                end_text,
                f"policy-replay-reconciliation:{policy['revision']}",
            )

    errors = store.db.execute(
        "SELECT producer,gap_start,gap_end,reason,reconciled FROM telemetry_collector_errors "
        "WHERE project=? AND gap_start<=? AND gap_end>=? AND reconciled=0",
        (project, end_text, start_text),
    ).fetchall()
    for error in errors:
        _gap(
            gaps,
            error["producer"],
            error["gap_start"],
            error["gap_end"],
            error["reason"],
            bool(error["reconciled"]),
        )
    return ("complete" if not gaps else "insufficient-telemetry"), gaps, manifest_revision, history_start


def derive_slo_state(
    history_status: str,
    telemetry_status: str,
    denominator: int,
    rate: float | None,
    inappropriate_invocations: int,
) -> str:
    if telemetry_status != "complete":
        return "insufficient-telemetry"
    if history_status != "complete":
        return "insufficient-history"
    if denominator == 0:
        return "no-data"
    if rate is not None and rate >= 0.95 and inappropriate_invocations == 0:
        return "met"
    return "missed"


def autonomy_metrics(
    store,
    project: str,
    *,
    window_end: datetime | str | None = None,
    window_days: int = 30,
    production: bool = True,
) -> dict[str, Any]:
    end = _parse(_utc(window_end))
    start = end - timedelta(days=window_days)
    rows = store.db.execute(
        "SELECT project_sequence,producer,producer_sequence,item_id,event_type,actor_class,"
        "payload,occurred_at FROM telemetry_events WHERE project=? AND occurred_at<=? "
        "ORDER BY project_sequence",
        (project, _utc(end)),
    ).fetchall()
    events = [{**dict(row), "payload": json.loads(row["payload"])} for row in rows]
    items: dict[str, dict[str, Any]] = defaultdict(lambda: {"events": []})
    for event in events:
        if not event["item_id"]:
            continue
        item = items[event["item_id"]]
        item["events"].append(event)
        if event["event_type"] == "intake-accepted":
            item["intake"] = event

    cohort: dict[str, dict[str, Any]] = {}
    for item_id, item in items.items():
        intake = item.get("intake")
        if not intake:
            if any(start <= _parse(event["occurred_at"]) <= end for event in item["events"]):
                cohort[item_id] = item
            continue
        intake_at = _parse(intake["occurred_at"])
        state_at_start = "queued"
        for event in item["events"]:
            if event["event_type"] == "state-transition" and _parse(event["occurred_at"]) < start:
                state_at_start = event["payload"].get("to", state_at_start)
        if start <= intake_at <= end or (intake_at < start and state_at_start not in TERMINAL):
            cohort[item_id] = item

    excluded = {
        item_id: item
        for item_id, item in cohort.items()
        if item.get("intake", {}).get("payload", {}).get("eligibility") == "excluded"
    }
    eligible = {item_id: item for item_id, item in cohort.items() if item_id not in excluded}
    successful: set[str] = set()
    modes = Counter()
    latencies: list[float] = []
    backlog_ages: list[float] = []
    policy_segments: dict[str, dict[str, int]] = defaultdict(
        lambda: {"numerator": 0, "denominator": 0, "exclusions": 0}
    )
    unique_invocations: set[str] = set()
    invocation_items: set[str] = set()
    inappropriate_invocations = 0
    tokens = 0
    cost = 0.0
    retry_count = 0
    duplicate_count = 0
    escalation_reasons: Counter[str] = Counter()
    invocation_reasons: Counter[str] = Counter()
    deferred_budget_items: set[str] = set()
    published_items: set[str] = set()

    for item_id, item in cohort.items():
        intake = item.get("intake")
        revision = str(intake["payload"].get("policy_revision", "missing")) if intake else "missing"
        if item_id in excluded:
            policy_segments[revision]["exclusions"] += 1
            continue
        policy_segments[revision]["denominator"] += 1
        final_state = "queued"
        final_at = end
        has_cache = False
        item_invocations = 0
        human = False
        for event in item["events"]:
            when = _parse(event["occurred_at"])
            if when > end:
                continue
            if event["event_type"] == "state-transition":
                final_state = event["payload"].get("to", final_state)
                final_at = when
                retry_count += int(final_state in {"retryable", "queued"} and event["payload"].get("from") != "queued")
                if final_state == "waiting-human":
                    reason = str(event["payload"].get("error_class") or "unspecified")
                    escalation_reasons[reason] += 1
                    if reason == "budget-exhausted":
                        deferred_budget_items.add(item_id)
            elif event["event_type"] == "cache-hit":
                has_cache = True
            elif event["event_type"] == "duplicate":
                duplicate_count += 1
            elif event["event_type"] == "human-intervention" or (
                event["actor_class"] == "human" and event["event_type"] in QUALIFYING_HUMAN_EVENTS
            ):
                human = True
            elif event["event_type"] == "agent-invocation":
                invocation_id = str(event["payload"].get("invocation_id", event["project_sequence"]))
                unique_invocations.add(invocation_id)
                invocation_items.add(item_id)
                item_invocations += 1
                invocation_reasons[str(event["payload"].get("reason", "unspecified"))] += 1
                inappropriate_invocations += int(bool(event["payload"].get("inappropriate")))
                tokens += int(event["payload"].get("tokens", 0))
                cost += float(event["payload"].get("cost", 0))
            elif event["event_type"] == "agent-completed":
                tokens += int(event["payload"].get("tokens", 0))
                cost += float(event["payload"].get("cost", 0))
            elif event["event_type"] == "publication-succeeded":
                published_items.add(item_id)
        if final_state in SUCCESS and not human:
            successful.add(item_id)
            policy_segments[revision]["numerator"] += 1
        if final_state in SUCCESS:
            mode = (
                "human-assisted"
                if human
                else "model-assisted"
                if item_invocations
                else "cache-only"
                if has_cache
                else "deterministic-only"
            )
            modes[mode] += 1
            if intake:
                latencies.append(max(0.0, (final_at - _parse(intake["occurred_at"])).total_seconds()))
        elif intake:
            backlog_ages.append(max(0.0, (end - _parse(intake["occurred_at"])).total_seconds()))

    telemetry_status, gaps, manifest_revision, history_start = _coverage(store, project, start, end, cohort, events)
    history_status = (
        "complete"
        if production and history_start is not None and end - history_start >= timedelta(days=30)
        else "insufficient-history"
    )
    denominator = len(eligible)
    numerator = len(successful)
    rate = numerator / denominator if denominator else None
    slo_state = derive_slo_state(
        history_status,
        telemetry_status,
        denominator,
        rate,
        inappropriate_invocations,
    )

    latencies.sort()
    p95 = latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))] if latencies else None
    policy = store.db.execute(
        "SELECT revision FROM policy_revisions WHERE project=? "
        "ORDER BY CAST(revision AS INTEGER) DESC, created_at DESC LIMIT 1",
        (project,),
    ).fetchone()
    result = {
        "production": production,
        "cohort_item_ids": sorted(cohort),
        "cohort_rule": (
            "intake-accepted in window plus unresolved at window start; "
            "items resolved before window start excluded"
        ),
        "numerator": numerator,
        "denominator": denominator,
        "exclusions": len(excluded),
        "rate": rate,
        "target": 0.95,
        "window_days": window_days,
        "window_start": _utc(start),
        "window_end": _utc(end),
        "policy_revision": policy["revision"] if policy else manifest_revision or "unavailable",
        "policy_segments": dict(policy_segments),
        "history_status": history_status,
        "telemetry_status": telemetry_status,
        "slo_state": slo_state,
        "telemetry_complete": telemetry_status == "complete",
        "coverage_gaps": gaps,
        "deterministic_completed": modes["deterministic-only"],
        "deterministic_only": modes["deterministic-only"],
        "cache_only": modes["cache-only"],
        "model_assisted": modes["model-assisted"],
        "human_assisted": modes["human-assisted"],
        "failed_or_unresolved": denominator - sum(modes.values()),
        "agent_invocations": len(unique_invocations),
        "agent_invocation_items": len(invocation_items),
        "agent_invocation_rate": len(invocation_items) / denominator if denominator else None,
        "unique_invocation_rate": len(unique_invocations) / denominator if denominator else None,
        "agent_invocation_reasons": dict(sorted(invocation_reasons.items())),
        "inappropriate_agent_invocations": inappropriate_invocations,
        "cache_hits": sum(event["event_type"] == "cache-hit" for item in eligible.values() for event in item["events"]),
        "tokens": tokens,
        "tokens_per_eligible_item": tokens / denominator if denominator else None,
        "tokens_per_item": tokens / denominator if denominator else None,
        "cost": cost,
        "cost_per_published_decision": cost / len(published_items) if published_items else None,
        "cost_per_decision": cost / denominator if denominator else None,
        "human_escalation_rate": (
            sum(
                1
                for item in eligible.values()
                if any(
                    event["event_type"] == "state-transition" and event["payload"].get("to") == "waiting-human"
                    for event in item["events"]
                )
            )
            / denominator
            if denominator
            else None
        ),
        "human_escalation_by_reason": dict(sorted(escalation_reasons.items())),
        "retry_rate": retry_count / denominator if denominator else None,
        "failure_rate": (
            sum(
                1
                for item in eligible.values()
                if any(
                    event["event_type"] == "state-transition"
                    and event["payload"].get("to") in {"failed", "canceled", "rejected"}
                    for event in item["events"]
                )
            )
            / denominator
            if denominator
            else None
        ),
        "retry_or_failure_rate": (
            len(
                {
                    item_id
                    for item_id, item in eligible.items()
                    if item_id not in successful
                    or any(
                        event["event_type"] == "state-transition" and event["payload"].get("to") == "retryable"
                        for event in item["events"]
                    )
                }
            )
            / denominator
            if denominator
            else None
        ),
        "duplicate_work_rate": duplicate_count / denominator if denominator else None,
        "median_terminal_seconds": median(latencies) if latencies else None,
        "p95_terminal_seconds": p95,
        "non_terminal_backlog_by_age_seconds": sorted(backlog_ages),
        "deferred_budget_items": len(deferred_budget_items),
        "deferred_budget_rate": len(deferred_budget_items) / denominator if denominator else None,
        "agent_cache_hit_rate": (
            modes["cache-only"] / (modes["cache-only"] + modes["model-assisted"])
            if modes["cache-only"] + modes["model-assisted"]
            else None
        ),
        "fixture_target_achieved": (
            not production
            and telemetry_status == "complete"
            and denominator > 0
            and rate is not None
            and rate >= 0.95
            and inappropriate_invocations == 0
        ),
    }
    daily_start = end - timedelta(days=1)
    daily_cohort: dict[str, dict[str, Any]] = {}
    for item_id, item in items.items():
        intake = item.get("intake")
        if not intake:
            continue
        state_at_start = "queued"
        for event in item["events"]:
            if event["event_type"] == "state-transition" and _parse(event["occurred_at"]) < daily_start:
                state_at_start = event["payload"].get("to", state_at_start)
        if daily_start <= _parse(intake["occurred_at"]) <= end or (
            _parse(intake["occurred_at"]) < daily_start and state_at_start not in TERMINAL
        ):
            daily_cohort[item_id] = item
    daily_excluded = {
        item_id for item_id, item in daily_cohort.items() if item["intake"]["payload"].get("eligibility") == "excluded"
    }
    daily_eligible = set(daily_cohort) - daily_excluded
    daily_successful: set[str] = set()
    for item_id in daily_eligible:
        state = "queued"
        human = False
        for event in daily_cohort[item_id]["events"]:
            if _parse(event["occurred_at"]) > end:
                continue
            if event["event_type"] == "state-transition":
                state = event["payload"].get("to", state)
            if event["event_type"] == "human-intervention":
                human = True
        if state in SUCCESS and not human:
            daily_successful.add(item_id)
    daily_denominator = len(daily_eligible)
    result["daily"] = {
        "numerator": len(daily_successful),
        "denominator": daily_denominator,
        "exclusions": len(daily_excluded),
        "rate": len(daily_successful) / daily_denominator if daily_denominator else None,
        "window_start": _utc(end - timedelta(days=1)),
        "window_days": 1,
    }
    store.db.execute(
        "INSERT INTO autonomy_metrics(id,project,window_start,window_days,payload,created_at) "
        "VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,created_at=excluded.created_at",
        (
            f"metrics_{store.digest([project, _utc(start), _utc(end)])[:24]}",
            project,
            _utc(start),
            window_days,
            json.dumps(result, sort_keys=True),
            _utc(),
        ),
    )
    store.db.commit()
    return result


def production_evidence_bundle(
    store,
    project: str,
    *,
    window_end: datetime | str | None = None,
    window_days: int = 30,
    production: bool = True,
) -> dict[str, Any]:
    """Return the redaction-safe, verifier-facing production evidence bundle."""
    metrics = autonomy_metrics(
        store,
        project,
        window_end=window_end,
        window_days=window_days,
        production=production,
    )
    manifest = store.db.execute(
        "SELECT revision,required_producers,effective_at FROM telemetry_manifests "
        "WHERE project=? AND effective_at<=? ORDER BY effective_at DESC LIMIT 1",
        (project, metrics["window_end"]),
    ).fetchone()
    required = []
    manifest_effective = None
    manifest_revision = None
    if manifest:
        required = sorted(
            {EVIDENCE_PRODUCER_ALIASES.get(item, item) for item in json.loads(manifest["required_producers"])}
        )
        manifest_effective = manifest["effective_at"]
        manifest_revision = manifest["revision"]
    events = store.db.execute(
        "SELECT producer,producer_sequence,occurred_at,event_type FROM telemetry_events "
        "WHERE project=? AND occurred_at>=? AND occurred_at<=? ORDER BY project_sequence",
        (project, metrics["window_start"], metrics["window_end"]),
    ).fetchall()
    sequence_ranges: dict[str, dict[str, int]] = {}
    heartbeat_intervals: dict[str, float | None] = {}
    for raw_producer in {row["producer"] for row in events} | set(required):
        producer = EVIDENCE_PRODUCER_ALIASES.get(raw_producer, raw_producer)
        producer_events = [
            row
            for row in events
            if EVIDENCE_PRODUCER_ALIASES.get(row["producer"], row["producer"]) == producer
        ]
        sequences = [int(row["producer_sequence"]) for row in producer_events]
        if sequences:
            sequence_ranges[producer] = {"first": min(sequences), "last": max(sequences), "count": len(sequences)}
        heartbeat_times = sorted(
            _parse(row["occurred_at"])
            for row in producer_events
            if row["event_type"] == "heartbeat"
        )
        heartbeat_intervals[producer] = (
            max((right - left).total_seconds() for left, right in zip(heartbeat_times, heartbeat_times[1:]))
            if len(heartbeat_times) > 1
            else None
        )
    watermarks = {
        EVIDENCE_PRODUCER_ALIASES.get(row["producer"], row["producer"]): int(row["producer_sequence"])
        for row in store.db.execute(
            "SELECT producer,producer_sequence FROM telemetry_watermarks WHERE project=?", (project,)
        )
    }
    return {
        "schema": "context-library/production-evidence-bundle",
        "schema_version": 1,
        "project": project,
        "production": production,
        "manifest": {
            "revision": manifest_revision,
            "effective_at": manifest_effective,
            "required_producers": required,
            "immutable_for_window": True,
        },
        "window": {
            "start": metrics["window_start"],
            "end": metrics["window_end"],
            "days": window_days,
        },
        "cohort": {
            "item_ids": metrics["cohort_item_ids"],
            "rule": metrics["cohort_rule"],
        },
        "telemetry": {
            "status": metrics["telemetry_status"],
            "coverage_gaps": metrics["coverage_gaps"],
            "sequence_ranges": sequence_ranges,
            "watermarks": watermarks,
            "heartbeat_intervals_seconds": heartbeat_intervals,
            "replay_reconciled": not any(
                "replay-reconciliation" in str(gap.get("reason", "")) for gap in metrics["coverage_gaps"]
            ),
        },
        "history": {
            "status": metrics["history_status"],
            "production_window": production,
        },
        "metrics": metrics,
    }
