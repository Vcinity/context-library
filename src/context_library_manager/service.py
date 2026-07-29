from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from context_library_maintainer.service import MaintainerApplicationService, MaintainerContext

from .config import Settings
from .db import Store
from .domain import RouteRequest, Source, utc_now
from .routing import route


class SourceIdempotencyConflict(ValueError):
    pass


def _resume_source_request(
    store: Store,
    settings: Settings,
    project: str,
    actor: str,
    request_digest: str,
    prior,
) -> tuple[dict, str | None]:
    if prior["request_digest"] != request_digest:
        raise SourceIdempotencyConflict("idempotency key was used for different source content")
    response = json.loads(prior["response"])
    work_id = response["work_id"]
    current = store.work(project, work_id)
    if current and current["state"] in {"succeeded", "failed"}:
        return {**response, "created": False}, None
    if (
        current
        and current["state"] in {"leased", "running"}
        and current["lease_expires"]
        and current["lease_expires"] <= datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    ):
        store.recover_expired(
            project,
            actor,
            settings.max_attempts,
            settings.item_token_budget,
            settings.cheap_profile_max_tokens,
            settings.standard_profile_max_tokens,
        )
        current = store.work(project, work_id)
    if not current or current["state"] != "queued":
        return {**response, "created": False}, None
    return response, work_id


def intake_source(
    store: Store,
    settings: Settings,
    project: str,
    source: Source,
    actor: str,
    idempotency_key: str | None = None,
) -> dict:
    key = idempotency_key or hashlib.sha256(source.model_dump_json().encode()).hexdigest()
    route_name = f"POST:/api/v1/projects/{project}/sources"
    request_digest = store.digest(source.model_dump(mode="json"))
    with store._write_lock:
        prior = store.db.execute(
            "SELECT request_digest,response FROM idempotency_records WHERE actor=? "
            "AND project=? AND route=? AND idempotency_key=?",
            (actor, project, route_name, key),
        ).fetchone()
        if prior:
            response, work_id = _resume_source_request(
                store,
                settings,
                project,
                actor,
                request_digest,
                prior,
            )
            if work_id is None:
                return response
            created = False
        else:
            database = store.db
            database.execute("BEGIN IMMEDIATE" if database.__class__.__name__ != "PostgresConnection" else "BEGIN")
            try:
                work_id, created = store.add_work(
                    project,
                    "source_batch",
                    key,
                    source.model_dump(mode="json"),
                    actor,
                    commit=False,
                )
                response = {"work_id": work_id, "created": created, "status": "pending"}
                database.execute(
                    "INSERT INTO idempotency_records VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        f"idem_{store.digest([actor, project, route_name, key])[:24]}",
                        actor,
                        project,
                        route_name,
                        key,
                        request_digest,
                        202,
                        json.dumps(response, sort_keys=True),
                        utc_now(),
                    ),
                )
                database.commit()
            except Exception:
                database.rollback()
                concurrent = database.execute(
                    "SELECT request_digest,response FROM idempotency_records WHERE actor=? "
                    "AND project=? AND route=? AND idempotency_key=?",
                    (actor, project, route_name, key),
                ).fetchone()
                if not concurrent:
                    raise
                response, work_id = _resume_source_request(
                    store,
                    settings,
                    project,
                    actor,
                    request_digest,
                    concurrent,
                )
                if work_id is None:
                    return response
                created = False
    lease = store.claim_work(project, work_id, actor, settings.lease_seconds)
    if lease is None:
        return {**response, "created": False}
    decision = route(RouteRequest(operation="source", input_tokens=len(source.content) // 4))
    store.event(work_id, actor, "routed", decision.model_dump())
    store.transition(project, work_id, "running", actor)
    clm_source = {"schema_version": 1, **source.model_dump(mode="json")}
    maintainer = MaintainerApplicationService(
        MaintainerContext(
            library_root=settings.library_root,
            state_root=settings.state_root,
            project=project,
            actor=actor,
        )
    )
    try:
        result = maintainer.ingest_source(clm_source)
    except Exception as exc:
        store.transition(project, work_id, "failed", actor, type(exc).__name__)
        response = {
            "work_id": work_id,
            "created": created,
            "status": "failed",
            "maintainer": type(exc).__name__,
        }
        store.db.execute(
            "UPDATE idempotency_records SET response_status=?,response=? WHERE actor=? "
            "AND project=? AND route=? AND idempotency_key=?",
            (500, json.dumps(response, sort_keys=True), actor, project, route_name, key),
        )
        store.db.commit()
        return response
    store.transition(project, work_id, "succeeded", actor)
    response = {
        "work_id": work_id,
        "created": created,
        "status": "succeeded",
        "maintainer": result,
    }
    store.db.execute(
        "UPDATE idempotency_records SET response_status=?,response=? WHERE actor=? "
        "AND project=? AND route=? AND idempotency_key=?",
        (200, json.dumps(response, sort_keys=True), actor, project, route_name, key),
    )
    store.db.commit()
    return response
