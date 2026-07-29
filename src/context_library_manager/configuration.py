from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

import yaml

from .config import ConfigurationError, Settings
from .db import Store
from .domain import utc_now

EDITABLE_FIELDS: dict[str, dict[str, Any]] = {
    "autonomy_enabled": {"type": "boolean", "group": "Processing"},
    "semantic_threshold": {
        "type": "number",
        "minimum": 0,
        "maximum": 1,
        "group": "Processing",
    },
    "excluded_categories": {
        "type": "string-list",
        "max_items": 50,
        "group": "Processing",
    },
    "worker_concurrency": {
        "type": "integer",
        "minimum": 1,
        "group": "Processing",
        "restart_required": True,
    },
    "lease_seconds": {
        "type": "integer",
        "minimum": 1,
        "maximum": 86400,
        "group": "Processing",
    },
    "max_attempts": {
        "type": "integer",
        "minimum": 1,
        "maximum": 20,
        "group": "Processing",
    },
    "item_token_budget": {"type": "integer", "minimum": 1, "group": "Budgets"},
    "project_daily_token_budget": {
        "type": "integer",
        "minimum": 1,
        "group": "Budgets",
    },
    "user_daily_token_budget": {
        "type": "integer",
        "minimum": 1,
        "group": "Budgets",
    },
    "cheap_profile_max_tokens": {
        "type": "integer",
        "minimum": 1,
        "group": "Budgets",
    },
    "standard_profile_max_tokens": {
        "type": "integer",
        "minimum": 1,
        "group": "Budgets",
    },
    "cache_enabled": {"type": "boolean", "group": "Budgets"},
    "notifications_enabled": {"type": "boolean", "group": "Notifications"},
    "in_app_notifications": {"type": "boolean", "group": "Notifications"},
    "review_reminder_days": {
        "type": "integer",
        "minimum": 1,
        "maximum": 365,
        "group": "Notifications",
    },
}


def initial_values(settings: Settings) -> dict[str, Any]:
    values = {name: getattr(settings, name) for name in EDITABLE_FIELDS}
    values["excluded_categories"] = list(values["excluded_categories"])
    return values


def project_baseline(settings: Settings, project: str) -> Settings:
    if project == settings.project:
        return settings
    path = settings.library_root / "projects" / project / "runtime.yaml"
    if not path.is_file():
        return replace(settings, project=project)
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    mapping = {
        "runtime": {
            "worker_concurrency": "worker_concurrency",
            "lease_seconds": "lease_seconds",
            "review_reminder_days": "review_reminder_days",
        },
        "autonomy": {
            "enabled": "autonomy_enabled",
            "semantic_threshold": "semantic_threshold",
            "excluded_categories": "excluded_categories",
        },
        "cost": {
            "project_daily_token_budget": "project_daily_token_budget",
            "user_daily_token_budget": "user_daily_token_budget",
            "item_token_budget": "item_token_budget",
            "cheap_profile_max_tokens": "cheap_profile_max_tokens",
            "standard_profile_max_tokens": "standard_profile_max_tokens",
            "max_attempts_per_item": "max_attempts",
            "cache_enabled": "cache_enabled",
        },
        "notifications": {
            "enabled": "notifications_enabled",
            "in_app": "in_app_notifications",
        },
    }
    changes = {
        mapping[section][name]: value
        for section, entries in raw.items()
        if section in mapping and isinstance(entries, dict)
        for name, value in entries.items()
        if name in mapping[section]
    }
    candidate, errors = validate_candidate(settings, initial_values(settings), changes)
    if errors:
        raise ConfigurationError(f"invalid project runtime configuration: {errors}")
    candidate["excluded_categories"] = tuple(candidate["excluded_categories"])
    sources = {**settings.field_sources, **dict.fromkeys(changes, "project-file")}
    return replace(settings, project=project, field_sources=sources, **candidate)


def ensure_initial_revision(store: Store, settings: Settings, project: str) -> None:
    row = store.db.execute("SELECT 1 FROM configuration_revisions WHERE project=? LIMIT 1", (project,)).fetchone()
    if row:
        return
    now = utc_now()
    store.db.execute(
        "INSERT INTO configuration_revisions(id,project,revision,actor,reason,values_json,created_at) "
        "VALUES(?,?,?,?,?,?,?) ON CONFLICT(project,revision) DO NOTHING",
        (
            f"configuration_{store.digest([project, 1])[:24]}",
            project,
            1,
            "runtime:initial",
            "initial effective configuration",
            json.dumps(initial_values(settings), sort_keys=True),
            now,
        ),
    )
    store.db.commit()


def current_revision(store: Store, project: str) -> tuple[int, dict[str, Any]]:
    row = store.db.execute(
        "SELECT revision,values_json FROM configuration_revisions WHERE project=? ORDER BY revision DESC LIMIT 1",
        (project,),
    ).fetchone()
    if not row:
        raise ConfigurationError("configuration revision is unavailable")
    return int(row["revision"]), json.loads(row["values_json"])


def resolved_revision(store: Store, settings: Settings, project: str) -> tuple[int, dict[str, Any]]:
    row = store.db.execute(
        "SELECT revision,values_json FROM configuration_revisions WHERE project=? ORDER BY revision DESC LIMIT 1",
        (project,),
    ).fetchone()
    if not row:
        return 1, initial_values(settings)
    return int(row["revision"]), json.loads(row["values_json"])


def effective_settings(store: Store, settings: Settings, project: str) -> Settings:
    baseline = project_baseline(settings, project)
    _, values = resolved_revision(store, baseline, project)
    values = {**initial_values(baseline), **values}
    values["excluded_categories"] = tuple(values["excluded_categories"])
    return replace(baseline, **values)


def validate_candidate(
    settings: Settings, current: dict[str, Any], changes: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    errors: list[dict[str, str]] = []
    unknown = sorted(set(changes) - set(EDITABLE_FIELDS))
    for field in unknown:
        errors.append({"field": field, "message": "unknown or deployment-owned field"})
    candidate = {**initial_values(settings), **current}
    for name, value in changes.items():
        rule = EDITABLE_FIELDS.get(name)
        if not rule:
            continue
        expected = rule["type"]
        valid_type = (
            (expected == "boolean" and isinstance(value, bool))
            or (expected == "integer" and isinstance(value, int) and not isinstance(value, bool))
            or (expected == "number" and isinstance(value, (int, float)) and not isinstance(value, bool))
            or (expected == "string-list" and isinstance(value, list) and all(isinstance(item, str) for item in value))
        )
        if not valid_type:
            errors.append({"field": name, "message": f"must be {expected}"})
            continue
        if "minimum" in rule and value < rule["minimum"]:
            errors.append({"field": name, "message": f"must be at least {rule['minimum']}"})
            continue
        if "maximum" in rule and value > rule["maximum"]:
            errors.append({"field": name, "message": f"must be at most {rule['maximum']}"})
            continue
        if expected == "string-list" and (
            len(value) > rule["max_items"] or any(not item.strip() or len(item) > 100 for item in value)
        ):
            errors.append({"field": name, "message": "contains too many or invalid categories"})
            continue
        candidate[name] = value
    if candidate.get("worker_concurrency", 1) > settings.worker_concurrency:
        errors.append(
            {
                "field": "worker_concurrency",
                "message": "exceeds deployment concurrency bound",
            }
        )
    if candidate.get("user_daily_token_budget", 0) > candidate.get("project_daily_token_budget", 0):
        errors.append(
            {
                "field": "user_daily_token_budget",
                "message": "must not exceed project budget",
            }
        )
    if candidate.get("item_token_budget", 0) > candidate.get("user_daily_token_budget", 0):
        errors.append(
            {
                "field": "item_token_budget",
                "message": "must not exceed user daily budget",
            }
        )
    for profile in ("cheap_profile_max_tokens", "standard_profile_max_tokens"):
        if candidate.get(profile, 0) > candidate.get("item_token_budget", 0):
            errors.append({"field": profile, "message": "must not exceed item budget"})
    if not errors:
        try:
            runtime_values = dict(candidate)
            runtime_values["excluded_categories"] = tuple(runtime_values["excluded_categories"])
            replace(
                settings,
                **runtime_values,
            )
        except (ConfigurationError, TypeError, ValueError) as exc:
            errors.append({"field": "configuration", "message": str(exc)})
    return candidate, errors


def impact(
    store: Store,
    settings: Settings,
    project: str,
    expected_revision: int,
    changes: dict[str, Any],
) -> dict[str, Any]:
    settings = project_baseline(settings, project)
    revision, values = resolved_revision(store, settings, project)
    candidate, errors = validate_candidate(settings, values, changes)
    if revision != expected_revision:
        errors.insert(
            0,
            {
                "field": "expected_revision",
                "message": f"current revision is {revision}",
            },
        )
    changed = {name for name, value in candidate.items() if values.get(name) != value}
    return {
        "valid": not errors and bool(changed),
        "current_revision": revision,
        "affected_queues": ["semantic"]
        if changed & {"autonomy_enabled", "semantic_threshold", "excluded_categories"}
        else [],
        "budget_effects": sorted(changed & {name for name in changed if "budget" in name}),
        "cache_invalidated": bool(changed & {"cache_enabled", "semantic_threshold"}),
        "restart_required": any(EDITABLE_FIELDS[name].get("restart_required") for name in changed),
        "changed_fields": sorted(changed),
        "errors": errors or ([] if changed else [{"field": "changes", "message": "no effective changes"}]),
    }


def read_model(store: Store, settings: Settings, project: str) -> dict[str, Any]:
    settings = project_baseline(settings, project)
    revision, stored = resolved_revision(store, settings, project)
    baseline = initial_values(settings)
    values = {**baseline, **stored}
    fields = {
        name: {
            "value": values[name],
            "source": (
                "project-file" if values[name] != baseline[name] else settings.field_sources.get(name, "default")
            ),
            "editable": True,
            "secret_state": "none",
            "restart_required": bool(rule.get("restart_required")),
            "constraints": rule,
        }
        for name, rule in EDITABLE_FIELDS.items()
    }
    deployment = {
        "database": {"configured": True},
        "library_root": {"configured": bool(settings.library_root)},
        "agent_provider": {"configured": False},
        "oidc": {"configured": bool(settings.oidc_issuer or settings.oidc_hs256_secret)},
        "webhook": {"configured": bool(settings.webhook_url and settings.webhook_secret)},
        "session_protection": {"configured": bool(settings.session_secret)},
    }
    return {
        "project": project,
        "revision": revision,
        "fields": fields,
        "deployment": deployment,
    }


def history(store: Store, project: str) -> list[dict[str, Any]]:
    rows = store.db.execute(
        "SELECT revision,actor,reason,values_json,rolled_back_from,created_at "
        "FROM configuration_revisions WHERE project=? ORDER BY revision DESC",
        (project,),
    ).fetchall()
    return [
        {
            "revision": int(row["revision"]),
            "actor": row["actor"],
            "reason": row["reason"],
            "values": json.loads(row["values_json"]),
            "rolled_back_from": row["rolled_back_from"],
            "created_at": row["created_at"],
        }
        for row in rows
    ]


def apply_revision(
    store: Store,
    settings: Settings,
    project: str,
    actor: str,
    route: str,
    expected_revision: int,
    reason: str,
    idempotency_key: str,
    changes: dict[str, Any] | None = None,
    target_revision: int | None = None,
) -> tuple[dict[str, Any], bool]:
    settings = project_baseline(settings, project)
    ensure_initial_revision(store, settings, project)
    request_payload = {
        "expected_revision": expected_revision,
        "changes": changes,
        "target_revision": target_revision,
        "reason": reason,
    }
    request_digest = store.digest(request_payload)
    with store._write_lock:
        prior = store.db.execute(
            "SELECT request_digest,response FROM idempotency_records WHERE actor=? "
            "AND project=? AND route=? AND idempotency_key=?",
            (actor, project, route, idempotency_key),
        ).fetchone()
        if prior:
            if prior["request_digest"] != request_digest:
                raise ConfigurationError("idempotency-conflict")
            return json.loads(prior["response"]), True
        database = store.db
        database.execute("BEGIN IMMEDIATE" if database.__class__.__name__ != "PostgresConnection" else "BEGIN")
        try:
            revision, current = current_revision(store, project)
            if revision != expected_revision:
                raise ConfigurationError(f"revision-conflict:{revision}")
            rolled_back_from = None
            if target_revision is not None:
                target = database.execute(
                    "SELECT values_json FROM configuration_revisions WHERE project=? AND revision=?",
                    (project, target_revision),
                ).fetchone()
                if not target:
                    raise ConfigurationError("target-revision-not-found")
                candidate = json.loads(target["values_json"])
                candidate, errors = validate_candidate(settings, current, candidate)
                rolled_back_from = target_revision
            else:
                candidate, errors = validate_candidate(settings, current, changes or {})
            if errors:
                raise ConfigurationError(json.dumps(errors, sort_keys=True))
            changed = {
                name: (current.get(name), value) for name, value in candidate.items() if current.get(name) != value
            }
            if not changed:
                raise ConfigurationError("no-effective-changes")
            new_revision = revision + 1
            revision_id = f"configuration_{store.digest([project, new_revision])[:24]}"
            now = utc_now()
            database.execute(
                "INSERT INTO configuration_revisions VALUES(?,?,?,?,?,?,?,?)",
                (
                    revision_id,
                    project,
                    new_revision,
                    actor,
                    reason,
                    json.dumps(candidate, sort_keys=True),
                    rolled_back_from,
                    now,
                ),
            )
            for name, (before, after) in changed.items():
                database.execute(
                    "INSERT INTO configuration_changes VALUES(?,?,?,?,?,?,?)",
                    (
                        f"config_change_{store.digest([revision_id, name])[:24]}",
                        revision_id,
                        name,
                        json.dumps(before, sort_keys=True),
                        json.dumps(after, sort_keys=True),
                        "project-file",
                        int(bool(EDITABLE_FIELDS[name].get("restart_required"))),
                    ),
                )
            database.execute(
                "INSERT INTO policy_revisions(id,project,revision,payload,created_at) VALUES(?,?,?,?,?)",
                (
                    f"policy_{store.digest([project, new_revision])[:24]}",
                    project,
                    str(new_revision),
                    json.dumps(candidate, sort_keys=True),
                    now,
                ),
            )
            store.event(
                None,
                actor,
                "configuration-updated",
                {
                    "capability": "admin",
                    "policy_revision": new_revision,
                    "before_reference": f"configuration:{project}:{revision}",
                    "after_reference": f"configuration:{project}:{new_revision}",
                    "changed_fields": sorted(changed),
                    "rolled_back_from": rolled_back_from,
                },
                project,
            )
            from .telemetry import append_event

            append_event(
                store,
                project,
                "policy",
                "policy-revision-applied",
                actor_class="human" if actor.startswith("human:") else "automation",
                payload={"revision": str(new_revision), "configuration_revision": new_revision},
                commit=False,
            )
            response = {
                "project": project,
                "revision": new_revision,
                "changed_fields": sorted(changed),
                "restart_required": any(EDITABLE_FIELDS[name].get("restart_required") for name in changed),
                "rolled_back_from": rolled_back_from,
                "audit_reference": f"configuration:{project}:{new_revision}",
            }
            database.execute(
                "INSERT INTO idempotency_records VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    f"idem_{store.digest([actor, project, route, idempotency_key])[:24]}",
                    actor,
                    project,
                    route,
                    idempotency_key,
                    request_digest,
                    200,
                    json.dumps(response, sort_keys=True),
                    now,
                ),
            )
            database.commit()
        except Exception:
            database.rollback()
            concurrent = database.execute(
                "SELECT request_digest,response FROM idempotency_records WHERE actor=? "
                "AND project=? AND route=? AND idempotency_key=?",
                (actor, project, route, idempotency_key),
            ).fetchone()
            if concurrent:
                if concurrent["request_digest"] != request_digest:
                    raise ConfigurationError("idempotency-conflict")
                return json.loads(concurrent["response"]), True
            current = database.execute(
                "SELECT revision FROM configuration_revisions WHERE project=? ORDER BY revision DESC LIMIT 1",
                (project,),
            ).fetchone()
            if current and int(current["revision"]) != expected_revision:
                raise ConfigurationError(f"revision-conflict:{current['revision']}")
            raise
    return response, False
