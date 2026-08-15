from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Any

from .domain import utc_now
from .migrations import apply_migrations
from .security import filter_confidential_value, sanitize_value


def _safe_payload(value: Any) -> Any:
    return sanitize_value(value)


class ProjectLifecycleConflict(RuntimeError):
    def __init__(self, current: dict[str, object]):
        super().__init__("project lifecycle version conflict")
        self.current = current


class LockedSQLiteCursor:
    def __init__(self, cursor: sqlite3.Cursor, lock: threading.RLock):
        self._cursor = cursor
        self._lock = lock
        self._held = cursor.description is not None
        if not self._held:
            lock.release()

    def _release(self) -> None:
        if self._held:
            self._held = False
            self._lock.release()

    def fetchone(self):
        try:
            return self._cursor.fetchone()
        finally:
            self._release()

    def fetchall(self):
        try:
            return self._cursor.fetchall()
        finally:
            self._release()

    def __iter__(self):
        return iter(self.fetchall())

    def close(self) -> None:
        try:
            self._cursor.close()
        finally:
            self._release()

    def __getattr__(self, name):
        return getattr(self._cursor, name)

    def __del__(self):
        self._release()


class LockedSQLiteConnection:
    """Serialize SQLite statements and result reads across request threads."""

    dialect = "sqlite"

    def __init__(self, connection: sqlite3.Connection):
        self._connection = connection
        self._lock = threading.RLock()

    def execute(self, sql: str, parameters=()) -> LockedSQLiteCursor:
        self._lock.acquire()
        try:
            return LockedSQLiteCursor(self._connection.execute(sql, parameters), self._lock)
        except Exception:
            self._lock.release()
            raise

    def commit(self) -> None:
        with self._lock:
            self._connection.commit()

    def executescript(self, sql: str) -> None:
        with self._lock:
            self._connection.executescript(sql)

    def rollback(self) -> None:
        with self._lock:
            self._connection.rollback()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    @property
    def total_changes(self) -> int:
        with self._lock:
            return self._connection.total_changes


class Store:
    def __init__(self, path: Path | str):
        self._write_lock = threading.RLock()
        if isinstance(path, str) and path.startswith(("postgresql://", "postgres://")):
            from .postgres import PostgresConnection

            self.db = PostgresConnection(path)
        else:
            local_path = Path(path)
            local_path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(local_path, check_same_thread=False)
            connection.row_factory = sqlite3.Row
            self.db = LockedSQLiteConnection(connection)
        apply_migrations(self.db)

    @staticmethod
    def digest(value: Any) -> str:
        return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def add_work(
        self,
        project: str,
        item_type: str,
        key: str,
        payload: dict[str, Any],
        actor: str,
        commit: bool = True,
    ) -> tuple[str, bool]:
        payload = dict(payload)
        policy = self.db.execute(
            "SELECT revision,payload FROM policy_revisions WHERE project=? "
            "ORDER BY CAST(revision AS INTEGER) DESC,created_at DESC LIMIT 1",
            (project,),
        ).fetchone()
        policy_revision = str(payload.get("policy_revision") or (policy["revision"] if policy else "1"))
        excluded_categories: set[str] = set()
        if policy:
            try:
                excluded_categories = set(json.loads(policy["payload"]).get("excluded_categories", []))
            except (TypeError, ValueError, json.JSONDecodeError):
                excluded_categories = set()
        eligibility = payload.get("metric_eligibility")
        if eligibility not in {"eligible", "excluded"}:
            eligibility = (
                "excluded"
                if payload.get("category") in excluded_categories or payload.get("policy_required_human")
                else "eligible"
            )
        payload["policy_revision"] = policy_revision
        payload["metric_eligibility"] = eligibility
        prompt_revision = str(payload.get("prompt_revision", "1"))
        normalized_digest = self.digest(payload)
        canonical_key = ":".join(
            [
                project,
                item_type,
                key,
                normalized_digest,
                policy_revision,
                prompt_revision,
            ]
        )
        row = self.db.execute("SELECT id FROM work_items WHERE idempotency_key=?", (canonical_key,)).fetchone()
        if row:
            return row["id"], False
        now = utc_now()
        ident = f"work_{self.digest([project, item_type, canonical_key])[:24]}"
        self.db.execute(
            "INSERT INTO work_items VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                ident,
                project,
                item_type,
                canonical_key,
                "queued",
                json.dumps(payload),
                0,
                None,
                None,
                None,
                now,
                now,
            ),
        )
        self.event(ident, actor, "created", {"item_type": item_type})
        from .telemetry import append_event

        append_event(
            self,
            project,
            "work",
            "intake-accepted",
            item_id=ident,
            actor_class="human" if actor.startswith("human:") else "automation",
            payload={
                "policy_revision": policy_revision,
                "eligibility": eligibility,
                "exclusion_reason": payload.get("exclusion_reason"),
            },
            commit=False,
        )
        if commit:
            self.db.commit()
        return ident, True

    def event(
        self,
        work_id: str | None,
        actor: str,
        event_type: str,
        payload: dict[str, Any],
        project: str | None = None,
    ):
        payload = _safe_payload(payload)
        created_at = utc_now()
        self.db.execute(
            "INSERT INTO events(work_id,actor,event_type,payload,created_at) VALUES(?,?,?,?,?)",
            (work_id, actor, event_type, json.dumps(payload), created_at),
        )
        event_project = project
        if work_id and event_project is None:
            row = self.db.execute("SELECT project FROM work_items WHERE id=?", (work_id,)).fetchone()
            event_project = row["project"] if row else None
        self.db.execute(
            "INSERT INTO audit_events(id,work_id,project,actor,event_type,payload,created_at,"
            "capability,run_id,policy_revision,before_reference,after_reference) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING",
            (
                f"audit_{uuid.uuid4().hex}",
                work_id,
                event_project,
                actor,
                event_type,
                json.dumps(payload),
                created_at,
                payload.get("capability"),
                payload.get("run_id"),
                payload.get("policy_revision"),
                payload.get("before_reference"),
                payload.get("after_reference"),
            ),
        )
        if (
            work_id
            and event_type
            in {
                "review-resolved",
                "publication-authorized",
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
            and actor.startswith("human:")
        ):
            intake = self.db.execute(
                "SELECT 1 FROM telemetry_events WHERE item_id=? AND event_type='intake-accepted'",
                (work_id,),
            ).fetchone()
            if intake and event_project:
                from .telemetry import append_event

                append_event(
                    self,
                    event_project,
                    "review",
                    "human-intervention",
                    item_id=work_id,
                    actor_class="human",
                    payload={"action": event_type},
                    commit=False,
                )

    def transition(
        self,
        project: str,
        work_id: str,
        state: str,
        actor: str,
        error: str | None = None,
        *,
        commit: bool = True,
    ) -> None:
        allowed = {
            "queued": {"leased", "failed"},
            "leased": {"running", "queued", "expired"},
            "running": {
                "queued",
                "succeeded",
                "waiting-human",
                "retryable",
                "failed",
                "cancel-requested",
                "expired",
            },
            "cancel-requested": {"canceled"},
            "canceled": {"queued"},
            "retryable": {"queued", "failed", "waiting-human"},
            "expired": {"queued", "failed"},
            "waiting-human": {"succeeded", "queued"},
            "succeeded": set(),
            "failed": {"queued"},
        }
        row = self.work(project, work_id)
        if not row:
            raise KeyError(work_id)
        if state not in allowed.get(row["state"], set()):
            raise ValueError(f"invalid work transition {row['state']} -> {state}")
        self.db.execute(
            "UPDATE work_items SET state=?, last_error=?, updated_at=? WHERE id=?",
            (state, _safe_payload(error), utc_now(), work_id),
        )
        if state == "retryable":
            self.db.execute("UPDATE work_items SET attempts=attempts+1 WHERE id=?", (work_id,))
        self.event(
            work_id,
            actor,
            "state-changed",
            {"from": row["state"], "to": state, "error": error},
        )
        from .telemetry import append_event

        append_event(
            self,
            project,
            "work",
            "state-transition",
            item_id=work_id,
            actor_class="human" if actor.startswith("human:") else "automation",
            payload={"from": row["state"], "to": state, "error_class": error},
            commit=False,
        )
        if commit:
            self.db.commit()

    def claim(
        self,
        project: str,
        owner: str,
        lease_seconds: int = 1800,
        exclude_ids: set[str] | None = None,
        deterministic_only: bool = False,
        agent_concurrency_limit: int | None = None,
    ) -> sqlite3.Row | None:
        with self._write_lock:
            return self._claim(
                project,
                owner,
                lease_seconds,
                exclude_ids or set(),
                deterministic_only,
                agent_concurrency_limit,
            )

    def claim_work(
        self,
        project: str,
        work_id: str,
        owner: str,
        lease_seconds: int = 1800,
    ):
        from datetime import datetime, timedelta, timezone

        with self._write_lock:
            self.db.execute("BEGIN IMMEDIATE")
            query = "SELECT * FROM work_items WHERE project=? AND id=?"
            if self.db.__class__.__name__ == "PostgresConnection":
                query += " FOR UPDATE"
            row = self.db.execute(query, (project, work_id)).fetchone()
            if not row or row["state"] != "queued":
                self.db.rollback()
                return None
            expires = (datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)).isoformat().replace("+00:00", "Z")
            updated = self.db.execute(
                "UPDATE work_items SET state='leased',lease_owner=?,lease_expires=?,updated_at=? "
                "WHERE project=? AND id=? AND state='queued'",
                (owner, expires, utc_now(), project, work_id),
            )
            if updated.rowcount != 1:
                self.db.rollback()
                return None
            self.db.execute(
                "INSERT INTO leases VALUES(?,?,?) ON CONFLICT(work_id) DO UPDATE SET "
                "owner=excluded.owner,expires_at=excluded.expires_at",
                (work_id, owner, expires),
            )
            self.event(work_id, owner, "leased", {"expires_at": expires})
            from .telemetry import append_event

            append_event(
                self,
                project,
                "work",
                "state-transition",
                item_id=work_id,
                actor_class="automation",
                payload={"from": "queued", "to": "leased", "error_class": None},
                commit=False,
            )
            self.db.commit()
            return self.work(project, work_id)

    def _claim(
        self,
        project: str,
        owner: str,
        lease_seconds: int,
        exclude_ids: set[str],
        deterministic_only: bool,
        agent_concurrency_limit: int | None,
    ):
        self.db.execute("BEGIN IMMEDIATE")
        if self.db.__class__.__name__ == "PostgresConnection":
            self.db.execute("SELECT id FROM projects WHERE id=? FOR UPDATE", (project,)).fetchone()
        deterministic_pattern = '%"clm_payload": %'
        deterministic_predicate = (
            "(item_type IN ('source_batch','publication_task') "
            "OR (item_type IN ('observation_task','candidate_task') "
            "AND payload LIKE ?))"
        )
        predicate_parameters: tuple[Any, ...] = (deterministic_pattern,)
        if agent_concurrency_limit is not None and not deterministic_only:
            active_agent_work = self.db.execute(
                "SELECT COUNT(*) AS n FROM work_items WHERE project=? "
                "AND state IN ('leased','running','cancel-requested') AND NOT " + deterministic_predicate,
                (project, *predicate_parameters),
            ).fetchone()["n"]
            deterministic_only = active_agent_work >= agent_concurrency_limit
        exclusions = ""
        parameters: tuple[Any, ...] = (project,)
        if deterministic_only:
            parameters += predicate_parameters
        if exclude_ids:
            exclusions = f" AND id NOT IN ({','.join('?' for _ in exclude_ids)})"
            parameters += tuple(sorted(exclude_ids))
        dispatch_filter = ""
        if deterministic_only:
            dispatch_filter = " AND " + deterministic_predicate
        claim_query = (
            "SELECT * FROM work_items WHERE project=? AND state='queued'"
            + dispatch_filter
            + exclusions
            + " ORDER BY created_at LIMIT 1"
        )
        if self.db.__class__.__name__ == "PostgresConnection":
            claim_query = (
                "SELECT * FROM work_items WHERE project=? AND state='queued'"
                + dispatch_filter
                + exclusions
                + " ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED"
            )
        row = self.db.execute(claim_query, parameters).fetchone()
        if not row:
            self.db.commit()
            return None
        from datetime import datetime, timedelta, timezone

        expires = (datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)).isoformat().replace("+00:00", "Z")
        updated = self.db.execute(
            "UPDATE work_items SET state='leased', lease_owner=?, lease_expires=?, updated_at=? "
            "WHERE id=? AND state='queued'",
            (owner, expires, utc_now(), row["id"]),
        )
        if updated.rowcount != 1:
            self.db.rollback()
            return None
        self.db.execute(
            "INSERT INTO leases VALUES(?,?,?) ON CONFLICT(work_id) "
            "DO UPDATE SET owner=excluded.owner, expires_at=excluded.expires_at",
            (row["id"], owner, expires),
        )
        self.event(row["id"], owner, "leased", {"expires_at": expires})
        from .telemetry import append_event

        append_event(
            self,
            project,
            "work",
            "state-transition",
            item_id=row["id"],
            actor_class="automation",
            payload={"from": "queued", "to": "leased", "error_class": None},
            commit=False,
        )
        self.db.commit()
        return self.work(project, row["id"])

    def heartbeat(self, process: str, instance_id: str, state: str = "healthy", details=None) -> None:
        self.db.execute(
            "INSERT INTO process_heartbeats(process,instance_id,state,details,observed_at) "
            "VALUES(?,?,?,?,?) ON CONFLICT(process,instance_id) DO UPDATE SET "
            "state=excluded.state,details=excluded.details,observed_at=excluded.observed_at",
            (process, instance_id, state, json.dumps(details or {}), utc_now()),
        )
        project = (details or {}).get("project")
        producer = {
            "worker": "work",
            "scheduler": "policy",
            "notification": "notification",
            "reconciliation": "review",
            "agent": "agent",
        }.get(process)
        if project and producer:
            from .telemetry import append_event

            append_event(
                self,
                project,
                producer,
                "heartbeat",
                payload={"instance_id": instance_id, "state": state},
                commit=False,
            )
        self.db.commit()

    def service_state(self):
        return self.db.execute(
            "SELECT state,version,reason,actor,updated_at FROM agent_service_state WHERE singleton=1"
        ).fetchone()

    def project_lifecycle(self, project: str):
        return self.db.execute(
            "SELECT id,active,lifecycle,lifecycle_version,updated_at FROM projects WHERE id=?",
            (project,),
        ).fetchone()

    def transition_project_lifecycle(
        self,
        project: str,
        lifecycle: str,
        expected_version: int,
        actor: str,
        reason: str,
    ) -> dict[str, object]:
        allowed = {
            "enabled": {"paused", "draining", "error"},
            "paused": {"enabled", "draining", "error"},
            "draining": {"disabled", "error"},
            "disabled": {"enabled"},
            "error": {"paused", "draining"},
        }
        if lifecycle not in allowed:
            raise ValueError(f"invalid project lifecycle: {lifecycle}")
        row = self.project_lifecycle(project)
        if not row:
            raise KeyError(project)
        if row["lifecycle_version"] != expected_version:
            raise ProjectLifecycleConflict(dict(row))
        if lifecycle != row["lifecycle"] and lifecycle not in allowed[row["lifecycle"]]:
            raise ValueError(f"invalid project lifecycle transition {row['lifecycle']} -> {lifecycle}")
        next_version = expected_version if lifecycle == row["lifecycle"] else expected_version + 1
        self.db.execute(
            "UPDATE projects SET lifecycle=?,lifecycle_version=?,active=?,updated_at=? "
            "WHERE id=? AND lifecycle_version=?",
            (
                lifecycle,
                next_version,
                int(lifecycle != "disabled"),
                utc_now(),
                project,
                expected_version,
            ),
        )
        if lifecycle != row["lifecycle"]:
            self.event(
                None,
                actor,
                "project-lifecycle-transition",
                {
                    "from": row["lifecycle"],
                    "to": lifecycle,
                    "reason": reason,
                    "expected_version": expected_version,
                    "version": next_version,
                },
                project,
            )
        self.db.commit()
        current = self.project_lifecycle(project)
        return dict(current)

    def start_agent_run(
        self,
        run_id: str,
        work_id: str,
        profile: str,
        actor: str,
        prompt_revision: str,
        cache_key: str,
        provider: str = "local-command",
        reserved_tokens: int = 0,
        invocation_reason: str = "semantic-judgment-required",
        deterministic_checks: tuple[str, ...] = ("schema", "identity", "topology"),
        cache_check: str = "miss",
    ) -> None:
        now = utc_now()
        self.db.execute(
            "INSERT INTO agent_runs(id,work_id,profile,status,input_tokens,output_tokens,"
            "cache_hit,cost,created_at,actor,prompt_revision,cache_key,provider,started_at) "
            "VALUES(?,?,?,?,0,0,0,0,?,?,?,?,?,?)",
            (
                run_id,
                work_id,
                profile,
                "running",
                now,
                actor,
                prompt_revision,
                cache_key,
                provider,
                now,
            ),
        )
        if reserved_tokens:
            self.db.execute(
                "UPDATE agent_runs SET reserved_tokens=? WHERE id=?",
                (reserved_tokens, run_id),
            )
        work = self.db.execute("SELECT project FROM work_items WHERE id=?", (work_id,)).fetchone()
        if work:
            from .telemetry import append_event

            append_event(
                self,
                work["project"],
                "agent",
                "agent-invocation",
                item_id=work_id,
                payload={
                    "invocation_id": run_id,
                    "reason": invocation_reason,
                    "deterministic_checks": list(deterministic_checks),
                    "cache_check": cache_check,
                    "inappropriate": not bool(invocation_reason) or cache_check != "miss" or not deterministic_checks,
                    "tokens": 0,
                    "cost": 0,
                },
                commit=False,
            )
        self.db.commit()

    def finish_agent_run(
        self,
        run_id: str,
        status: str,
        input_tokens: int,
        output_tokens: int,
        cost: float = 0.0,
        *,
        commit: bool = True,
    ) -> None:
        self.db.execute(
            "UPDATE agent_runs SET status=?,input_tokens=?,output_tokens=?,cost=?,finished_at=? WHERE id=?",
            (status, input_tokens, output_tokens, cost, utc_now(), run_id),
        )
        row = self.db.execute(
            "SELECT w.project,ar.work_id FROM agent_runs ar JOIN work_items w ON w.id=ar.work_id WHERE ar.id=?",
            (run_id,),
        ).fetchone()
        if row:
            from .telemetry import append_event

            append_event(
                self,
                row["project"],
                "agent",
                "agent-completed",
                item_id=row["work_id"],
                payload={
                    "invocation_id": run_id,
                    "status": status,
                    "tokens": input_tokens + output_tokens,
                    "cost": cost,
                },
                commit=False,
            )
        if commit:
            self.db.commit()

    def cancellation_requested(self, run_id: str) -> bool:
        return bool(
            self.db.execute(
                "SELECT 1 FROM work_cancellations WHERE agent_run_id=? AND state='cancel-requested'",
                (run_id,),
            ).fetchone()
        )

    def acknowledge_cancellation(
        self,
        project: str,
        work_id: str,
        run_id: str,
        actor: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> None:
        with self._write_lock:
            self.db.execute("BEGIN IMMEDIATE" if self.db.__class__.__name__ != "PostgresConnection" else "BEGIN")
            try:
                now = utc_now()
                changed = self.db.execute(
                    "UPDATE work_cancellations SET state='canceled',acknowledged_at=? "
                    "WHERE agent_run_id=? AND work_id=? AND state='cancel-requested'",
                    (now, run_id, work_id),
                )
                if changed.rowcount != 1:
                    self.db.rollback()
                    return
                self.finish_agent_run(
                    run_id,
                    "canceled",
                    input_tokens,
                    output_tokens,
                    commit=False,
                )
                row = self.work(project, work_id)
                if row and row["state"] == "cancel-requested":
                    self.transition(
                        project,
                        work_id,
                        "canceled",
                        actor,
                        "operator-canceled",
                        commit=False,
                    )
                self.event(
                    work_id,
                    actor,
                    "cancellation-acknowledged",
                    {"run_id": run_id},
                    project,
                )
                self.db.commit()
            except Exception:
                self.db.rollback()
                raise

    def recover_expired(
        self,
        project: str,
        actor: str,
        max_attempts: int,
        item_token_budget: int = 16_000,
        cheap_profile_max_tokens: int = 2_000,
        standard_profile_max_tokens: int = 8_000,
    ) -> int:
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        rows = self.db.execute(
            "SELECT id, attempts FROM work_items WHERE project=? "
            "AND state IN ('leased','running') AND lease_expires <= ?",
            (project, now),
        ).fetchall()
        recovered = 0
        for row in rows:
            stale_runs = self.db.execute(
                "SELECT id,actor,profile,reserved_tokens FROM agent_runs WHERE work_id=? "
                "AND status IN ('running','cancel-requested')",
                (row["id"],),
            ).fetchall()
            for run in stale_runs:
                profile_limit = cheap_profile_max_tokens if run["profile"] == "cheap" else standard_profile_max_tokens
                reserved = run["reserved_tokens"] or min(item_token_budget, profile_limit)
                self.settle_budget(
                    project,
                    run["actor"],
                    reserved,
                    0,
                )
                self.finish_agent_run(run["id"], "error", 0, 0)
                self.event(
                    row["id"],
                    actor,
                    "agent-run-recovered-after-lease-expiry",
                    {"run_id": run["id"]},
                )
            self.transition(project, row["id"], "expired", actor, "lease-expired", commit=False)
            self.event(row["id"], actor, "lease-expired", {})
            if row["attempts"] + 1 < max_attempts:
                self.db.execute(
                    "UPDATE work_items SET attempts=attempts+1, "
                    "lease_owner=NULL, lease_expires=NULL, updated_at=? WHERE id=?",
                    (utc_now(), row["id"]),
                )
                self.transition(project, row["id"], "queued", actor, "lease-expired", commit=False)
                self.event(row["id"], actor, "requeued-after-expiry", {})
            else:
                self.transition(project, row["id"], "failed", actor, "lease-attempt-limit", commit=False)
            recovered += 1
        cancellations = self.db.execute(
            "SELECT w.id,wc.agent_run_id,ar.actor,ar.profile,ar.reserved_tokens "
            "FROM work_items w JOIN work_cancellations wc ON wc.work_id=w.id "
            "JOIN agent_runs ar ON ar.id=wc.agent_run_id "
            "WHERE w.project=? AND w.state='cancel-requested' "
            "AND wc.state='cancel-requested' AND w.lease_expires <= ?",
            (project, now),
        ).fetchall()
        for row in cancellations:
            profile_limit = cheap_profile_max_tokens if row["profile"] == "cheap" else standard_profile_max_tokens
            reserved = row["reserved_tokens"] or min(item_token_budget, profile_limit)
            self.settle_budget(
                project,
                row["actor"],
                reserved,
                0,
            )
            self.finish_agent_run(row["agent_run_id"], "canceled", 0, 0)
            self.db.execute(
                "UPDATE work_cancellations SET state='canceled',acknowledged_at=? "
                "WHERE agent_run_id=? AND state='cancel-requested'",
                (utc_now(), row["agent_run_id"]),
            )
            self.transition(project, row["id"], "canceled", actor, "lease-expired", commit=False)
            self.db.execute(
                "UPDATE work_items SET lease_owner=NULL,lease_expires=NULL,updated_at=? WHERE id=?",
                (utc_now(), row["id"]),
            )
            self.event(row["id"], actor, "cancellation-recovered-after-expiry", {})
            recovered += 1
        self.db.commit()
        return recovered

    def requeue_retryable(self, project: str, actor: str, max_attempts: int) -> int:
        rows = self.db.execute(
            "SELECT id, attempts FROM work_items WHERE project=? AND state='retryable'",
            (project,),
        ).fetchall()
        requeued = 0
        for row in rows:
            if row["attempts"] < max_attempts:
                self.transition(project, row["id"], "queued", actor, "scheduled-retry", commit=False)
                self.event(row["id"], actor, "retryable-requeued", {})
                requeued += 1
            else:
                self.transition(project, row["id"], "failed", actor, "attempt-limit", commit=False)
        self.db.commit()
        return requeued

    def reserve_budget(self, project: str, actor: str, tokens: int, project_limit: int, user_limit: int) -> bool:
        from datetime import datetime, timezone

        with self._write_lock:
            self.db.execute("BEGIN IMMEDIATE")
            day = datetime.now(timezone.utc).date().isoformat()
            self.db.execute(
                "INSERT INTO project_budgets(project,day) VALUES(?,?) ON CONFLICT(project) DO NOTHING",
                (project, day),
            )
            self.db.execute(
                "INSERT INTO user_budgets(actor,day) VALUES(?,?) ON CONFLICT(actor) DO NOTHING",
                (actor, day),
            )
            # Do not discard an in-flight reservation at midnight. A new day
            # begins once the prior reservation has settled.
            self.db.execute(
                "UPDATE project_budgets SET day=?, spent_tokens=0 WHERE project=? AND day<>? AND reserved_tokens=0",
                (day, project, day),
            )
            self.db.execute(
                "UPDATE user_budgets SET day=?, spent_tokens=0 WHERE actor=? AND day<>? AND reserved_tokens=0",
                (day, actor, day),
            )
            project_update = self.db.execute(
                "UPDATE project_budgets SET reserved_tokens=reserved_tokens+? "
                "WHERE project=? AND reserved_tokens+spent_tokens+?<=?",
                (tokens, project, tokens, project_limit),
            )
            user_update = self.db.execute(
                "UPDATE user_budgets SET reserved_tokens=reserved_tokens+? "
                "WHERE actor=? AND reserved_tokens+spent_tokens+?<=?",
                (tokens, actor, tokens, user_limit),
            )
            if project_update.rowcount != 1 or user_update.rowcount != 1:
                self.db.rollback()
                return False
            self.db.commit()
            return True

    def settle_budget(self, project: str, actor: str, reserved: int, actual: int) -> None:
        with self._write_lock:
            self.db.execute("BEGIN IMMEDIATE")
            self.db.execute(
                "UPDATE project_budgets SET reserved_tokens=MAX(0,reserved_tokens-?), "
                "spent_tokens=spent_tokens+? WHERE project=?",
                (reserved, actual, project),
            )
            self.db.execute(
                "UPDATE user_budgets SET reserved_tokens=MAX(0,reserved_tokens-?), "
                "spent_tokens=spent_tokens+? WHERE actor=?",
                (reserved, actual, actor),
            )
            self.db.commit()

    def cache_get(self, cache_key: str):
        return self.db.execute("SELECT * FROM agent_cache WHERE cache_key=?", (cache_key,)).fetchone()

    def cache_put(
        self,
        cache_key: str,
        project: str,
        payload: dict,
        input_tokens: int,
        output_tokens: int,
    ) -> None:
        payload = filter_confidential_value(payload)
        self.db.execute(
            "INSERT INTO agent_cache VALUES (?,?,?,?,?,?) ON CONFLICT(cache_key) "
            "DO UPDATE SET project=excluded.project, payload=excluded.payload, "
            "input_tokens=excluded.input_tokens, output_tokens=excluded.output_tokens, "
            "created_at=excluded.created_at",
            (
                cache_key,
                project,
                json.dumps(payload),
                input_tokens,
                output_tokens,
                utc_now(),
            ),
        )
        self.db.commit()

    def record_agent_run(
        self,
        run_id: str,
        work_id: str,
        profile: str,
        status: str,
        input_tokens: int,
        output_tokens: int,
        cache_hit: bool,
        cost: float = 0.0,
        actor: str = "",
        prompt_revision: str = "1",
        cache_key: str | None = None,
        provider: str = "local-command",
        parent_run: str | None = None,
    ) -> None:
        self.db.execute(
            "INSERT INTO agent_runs(id,work_id,profile,status,input_tokens,output_tokens,cache_hit,cost,created_at,"
            "actor,prompt_revision,cache_key,provider,parent_run,started_at,finished_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                run_id,
                work_id,
                profile,
                status,
                input_tokens,
                output_tokens,
                int(cache_hit),
                cost,
                utc_now(),
                actor,
                prompt_revision,
                cache_key,
                provider,
                parent_run,
                utc_now(),
                utc_now(),
            ),
        )
        work = self.db.execute("SELECT project FROM work_items WHERE id=?", (work_id,)).fetchone()
        if work:
            from .telemetry import append_event

            append_event(
                self,
                work["project"],
                "agent",
                "cache-hit" if cache_hit else "agent-invocation",
                item_id=work_id,
                payload=(
                    {
                        "cache_key": cache_key or run_id,
                        "invocation_id": run_id,
                        "status": status,
                    }
                    if cache_hit
                    else {
                        "invocation_id": run_id,
                        "reason": "semantic-judgment-required",
                        "deterministic_checks": ["schema", "identity", "cache"],
                        "cache_check": "miss",
                        "inappropriate": False,
                        "tokens": input_tokens + output_tokens,
                        "cost": cost,
                    }
                ),
                commit=False,
            )
        self.db.commit()

    def create_review(
        self,
        project: str,
        work_id: str,
        question: str,
        choices: list[str],
        evidence: list[str],
        actor: str,
    ) -> str:
        existing = self.db.execute("SELECT id FROM reviews WHERE work_id=?", (work_id,)).fetchone()
        if existing:
            return existing["id"]
        if len(choices) < 2 or len(choices) > 4:
            raise ValueError("reviews require two to four choices")
        ident = f"review_{self.digest([project, work_id])[:24]}"
        now = utc_now()
        self.db.execute(
            "INSERT INTO reviews VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                ident,
                project,
                work_id,
                question,
                json.dumps(choices),
                "open",
                json.dumps(evidence),
                None,
                now,
                now,
            ),
        )
        notification_id = f"notification_{self.digest(ident)[:24]}"
        self.db.execute(
            "INSERT INTO notifications(id,review_id,status,created_at) VALUES (?,?,?,?)",
            (notification_id, ident, "pending", now),
        )
        work = self.work(project, work_id)
        payload = json.loads(work["payload"]) if work else {}
        self.db.execute(
            "INSERT INTO review_metadata(review_id,urgency,reason,source,owner) "
            "VALUES(?,?,?,?,?) ON CONFLICT(review_id) DO NOTHING",
            (
                ident,
                payload.get("urgency", "normal"),
                payload.get("review_reason", "evidence-conflict"),
                payload.get("source_type"),
                payload.get("owner"),
            ),
        )
        self.event(work_id, actor, "review-created", {"review_id": ident, "question": question})
        from .telemetry import append_event

        append_event(
            self,
            project,
            "review",
            "review-created",
            item_id=work_id,
            payload={"review_id": ident},
            commit=False,
        )
        append_event(
            self,
            project,
            "notification",
            "notification-enqueued",
            item_id=work_id,
            payload={"notification_id": notification_id, "review_id": ident, "status": "pending"},
            commit=False,
        )
        self.db.commit()
        return ident

    def work(self, project: str, work_id: str | None = None):
        if work_id:
            return self.db.execute("SELECT * FROM work_items WHERE project=? AND id=?", (project, work_id)).fetchone()
        return self.db.execute(
            "SELECT * FROM work_items WHERE project=? ORDER BY created_at DESC",
            (project,),
        ).fetchall()

    def overview(self, project: str) -> dict[str, Any]:
        rows = self.work(project)
        counts = {}
        for row in rows:
            counts[row["state"]] = counts.get(row["state"], 0) + 1
        reviews = self.db.execute(
            "SELECT COUNT(*) AS n FROM reviews WHERE project=? AND status='open'",
            (project,),
        ).fetchone()["n"]
        from .metrics import autonomy_metrics

        autonomy = autonomy_metrics(self, project)
        attention = {
            "review": reviews,
            "failed": counts.get("failed", 0),
            "retryable": counts.get("retryable", 0),
            "stale": self.db.execute(
                "SELECT COUNT(*) AS n FROM work_items WHERE project=? "
                "AND lease_expires IS NOT NULL AND lease_expires < ? "
                "AND state IN ('leased','running')",
                (project, utc_now()),
            ).fetchone()["n"],
            "blocked": counts.get("waiting-human", 0),
        }
        publication = self.db.execute(
            "SELECT id,status,digest,git_revision,created_at FROM publication_history "
            "WHERE project=? ORDER BY created_at DESC LIMIT 1",
            (project,),
        ).fetchone()
        budget = self.db.execute(
            "SELECT reserved_tokens,spent_tokens,day FROM project_budgets WHERE project=? ORDER BY day DESC LIMIT 1",
            (project,),
        ).fetchone()
        service = self.db.execute(
            "SELECT state,version,updated_at FROM agent_service_state WHERE singleton=1"
        ).fetchone()
        recent = self.db.execute(
            "SELECT id,event_type,actor,work_id,created_at FROM audit_events "
            "WHERE project=? ORDER BY created_at DESC LIMIT 8",
            (project,),
        ).fetchall()
        return {
            "project": project,
            "queue": counts,
            "open_reviews": reviews,
            "attention": {**attention, "total": sum(attention.values())},
            "last_publication": dict(publication) if publication else None,
            "budget": dict(budget) if budget else {"reserved_tokens": 0, "spent_tokens": 0, "day": None},
            "agent_service": dict(service),
            "recent_activity": [dict(row) for row in recent],
            "telemetry_complete": autonomy["telemetry_complete"],
            "autonomy": autonomy,
        }

    def close(self):
        self.db.close()
