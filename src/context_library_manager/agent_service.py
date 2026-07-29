from __future__ import annotations

import json
from datetime import datetime, timezone

from .db import Store
from .domain import utc_now


class ServiceConflict(Exception):
    def __init__(self, code: str, message: str, current: dict | None = None):
        self.code = code
        self.message = message
        self.current = current or {}


TRANSITIONS = {
    "pause": {"running": "paused", "paused": "paused"},
    "resume": {"paused": "running", "draining": "running", "running": "running"},
    "drain": {"running": "draining", "draining": "draining", "paused": "paused"},
}


def control_service(
    store: Store,
    action: str,
    expected_version: int,
    reason: str,
    idempotency_key: str,
    actor: str,
    project: str | None = None,
) -> dict:
    route = f"agent-service:{action}"
    digest = store.digest([action, expected_version, reason])
    with store._write_lock:
        prior = store.db.execute(
            "SELECT request_digest,response FROM idempotency_records WHERE actor=? "
            "AND project IS NULL AND route=? AND idempotency_key=?",
            (actor, route, idempotency_key),
        ).fetchone()
        if prior:
            if prior["request_digest"] != digest:
                raise ServiceConflict("idempotency-conflict", "idempotency key used for different content")
            result = json.loads(prior["response"])
            result["idempotent"] = True
            return result
        current = dict(store.service_state())
        if current["version"] != expected_version:
            raise ServiceConflict("revision-conflict", "service version is stale", current)
        target = TRANSITIONS.get(action, {}).get(current["state"])
        if target is None:
            raise ServiceConflict(
                "invalid-transition",
                f"cannot {action} service while {current['state']}",
                current,
            )
        changed = target != current["state"]
        version = current["version"] + int(changed)
        if changed:
            updated = store.db.execute(
                "UPDATE agent_service_state SET state=?,version=?,reason=?,actor=?,updated_at=? "
                "WHERE singleton=1 AND version=?",
                (target, version, reason, actor, utc_now(), expected_version),
            )
            if updated.rowcount != 1:
                store.db.rollback()
                raise ServiceConflict("revision-conflict", "service version changed concurrently")
            store.event(
                None,
                actor,
                f"agent-service-{action}",
                {"from": current["state"], "to": target, "reason": reason},
                project,
            )
        result = {
            "previous_state": current["state"],
            "state": target,
            "version": version,
            "idempotent": not changed,
        }
        store.db.execute(
            "INSERT INTO idempotency_records VALUES(?,?,?,?,?,?,?,?,?)",
            (
                f"idem_{store.digest([actor, route, idempotency_key])[:24]}",
                actor,
                None,
                route,
                idempotency_key,
                digest,
                200,
                json.dumps(result),
                utc_now(),
            ),
        )
        store.db.commit()
        return result


def complete_drain(store: Store, actor: str) -> bool:
    with store._write_lock:
        current = store.service_state()
        if current["state"] != "draining":
            return False
        active = store.db.execute(
            "SELECT 1 FROM agent_runs WHERE status IN ('running','cancel-requested') LIMIT 1"
        ).fetchone()
        if active:
            return False
        store.db.execute(
            "UPDATE agent_service_state SET state='paused',version=version+1,reason=?,"
            "actor=?,updated_at=? WHERE singleton=1 AND state='draining'",
            ("drain complete", actor, utc_now()),
        )
        store.event(None, actor, "agent-service-drain-complete", {})
        store.db.commit()
        return True


def heartbeat_health(observed_at: str | None) -> str:
    if not observed_at:
        return "offline"
    try:
        observed = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - observed).total_seconds()
    except (TypeError, ValueError):
        return "offline"
    if age <= 30:
        return "healthy"
    if age <= 90:
        return "degraded"
    return "offline"
