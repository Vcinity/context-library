from __future__ import annotations

import hashlib
import hmac
import json
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone

from .security import safe_error_class


def enqueue_reminders(store, reminder_days: int = 7, project: str | None = None) -> int:
    """Re-open the single outbox record for an overdue open-review reminder."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=reminder_days)).isoformat().replace("+00:00", "Z")
    query = (
        "SELECT n.id,r.project,r.work_id FROM notifications n JOIN reviews r ON r.id=n.review_id "
        "WHERE r.status='open' AND n.status='delivered' AND "
        "COALESCE(n.delivered_at,n.created_at) <= ?"
    )
    parameters: tuple[object, ...] = (cutoff,)
    if project is not None:
        query += " AND r.project=?"
        parameters += (project,)
    rows = store.db.execute(query, parameters).fetchall()
    for row in rows:
        store.db.execute(
            "UPDATE notifications SET status='pending', attempts=0, next_attempt=NULL, "
            "last_error=NULL,created_at=?,delivered_at=NULL,claim_owner=NULL,"
            "claimed_at=NULL,claim_expires=NULL WHERE id=?",
            (datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), row["id"]),
        )
        from .telemetry import append_event

        append_event(
            store,
            row["project"],
            "notification",
            "notification-reminder-enqueued",
            item_id=row["work_id"],
            payload={"notification_id": row["id"], "status": "pending"},
            commit=False,
        )
    store.db.commit()
    return len(rows)


def deliver_pending(
    store,
    webhook_url: str | None = None,
    webhook_secret: str | None = None,
    *,
    owner: str | None = None,
    claim_seconds: int = 60,
    project: str | None = None,
) -> dict[str, int]:
    owner = owner or f"notification:{uuid.uuid4().hex}"
    claim_seconds = max(claim_seconds, 15)
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    query = (
        "SELECT n.*,r.project,r.work_id FROM notifications n "
        "JOIN reviews r ON r.id=n.review_id WHERE n.status='pending' "
        "AND (n.next_attempt IS NULL OR n.next_attempt <= ?) "
        "AND (n.claim_owner IS NULL OR n.claim_expires <= ?)"
    )
    parameters: tuple[object, ...] = (now, now)
    if project is not None:
        query += " AND r.project=?"
        parameters += (project,)
    query += " ORDER BY n.created_at LIMIT 100"
    rows = store.db.execute(query, parameters).fetchall()
    delivered = 0
    failed = 0
    for row in rows:
        claim_now = datetime.now(timezone.utc)
        claim_at = claim_now.isoformat().replace("+00:00", "Z")
        claim_expires = (claim_now + timedelta(seconds=claim_seconds)).isoformat().replace("+00:00", "Z")
        with store._write_lock:
            store.db.execute("BEGIN IMMEDIATE" if store.db.__class__.__name__ != "PostgresConnection" else "BEGIN")
            try:
                claimed = store.db.execute(
                    "UPDATE notifications SET claim_owner=?,claimed_at=?,"
                    "claim_expires=? WHERE id=? AND "
                    "status='pending' AND (next_attempt IS NULL OR next_attempt <= ?) "
                    "AND (claim_owner IS NULL OR claim_expires <= ?)",
                    (owner, claim_at, claim_expires, row["id"], claim_at, claim_at),
                )
                if claimed.rowcount != 1:
                    store.db.rollback()
                    continue
                store.db.commit()
            except Exception:
                store.db.rollback()
                raise
        try:
            body = json.dumps({"notification_id": row["id"], "review_id": row["review_id"]}).encode()
            if webhook_url:
                if not webhook_secret:
                    raise RuntimeError("webhook signing secret is not configured")
                timestamp = str(int(datetime.now(timezone.utc).timestamp()))
                signature = hmac.new(
                    webhook_secret.encode(),
                    f"{timestamp}.".encode() + body,
                    hashlib.sha256,
                ).hexdigest()
                request = urllib.request.Request(
                    webhook_url,
                    data=body,
                    headers={
                        "Content-Type": "application/json",
                        "X-Runtime-Signature": f"sha256={signature}",
                        "X-Runtime-Timestamp": timestamp,
                    },
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=10) as response:
                    if response.status >= 300:
                        raise RuntimeError(f"webhook returned {response.status}")
            store.db.execute(
                "UPDATE notifications SET status='delivered',delivered_at=?,claim_owner=NULL,"
                "claimed_at=NULL,claim_expires=NULL WHERE id=? AND status='pending' "
                "AND claim_owner=?",
                (
                    datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                    row["id"],
                    owner,
                ),
            )
            from .telemetry import append_event

            append_event(
                store,
                row["project"],
                "notification",
                "notification-delivered",
                item_id=row["work_id"],
                payload={"notification_id": row["id"], "status": "delivered"},
                commit=False,
            )
            store.db.commit()
            delivered += 1
        except Exception as exc:
            error_class = safe_error_class(exc)
            attempts = row["attempts"] + 1
            if attempts >= 5:
                store.db.execute(
                    "UPDATE notifications SET status='failed',attempts=?,last_error=?,"
                    "claim_owner=NULL,claimed_at=NULL,claim_expires=NULL "
                    "WHERE id=? AND status='pending' AND claim_owner=?",
                    (attempts, error_class, row["id"], owner),
                )
            else:
                next_attempt = (
                    (datetime.now(timezone.utc) + timedelta(days=2 ** (attempts - 1) / 1440))
                    .isoformat()
                    .replace("+00:00", "Z")
                )
                store.db.execute(
                    "UPDATE notifications SET status='pending',attempts=?,next_attempt=?,last_error=?,"
                    "claim_owner=NULL,claimed_at=NULL,claim_expires=NULL "
                    "WHERE id=? AND status='pending' AND claim_owner=?",
                    (attempts, next_attempt, error_class, row["id"], owner),
                )
            from .telemetry import append_event

            append_event(
                store,
                row["project"],
                "notification",
                "notification-failed" if attempts >= 5 else "notification-retry-scheduled",
                item_id=row["work_id"],
                payload={
                    "notification_id": row["id"],
                    "status": "failed" if attempts >= 5 else "pending",
                    "error_class": error_class,
                },
                commit=False,
            )
            store.db.commit()
            failed += 1
    return {"delivered": delivered, "failed": failed}
