#!/usr/bin/env python3
"""Check or safely synchronize repository-local Context Library guidance."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import projection  # noqa: E402

BLOCKING_RUNTIME_CONDITIONS = frozenset(
    {
        projection.runtime_config.CONDITION_MISSING_CONFIG,
        projection.runtime_config.CONDITION_MALFORMED_CONFIG,
        projection.runtime_config.CONDITION_UNREADABLE_CONFIG,
        projection.runtime_config.CONDITION_MISSING_ROOT,
        projection.runtime_config.CONDITION_UNREADABLE_ROOT,
    }
)


def required_notice(
    classification: str,
    error: BaseException | str,
    *,
    project: str | None,
    source: str | None,
) -> None:
    project_text = project or "unselected project"
    source_text = source or "explicit requirement signal"
    print(
        "Context Library required-context notice: "
        f"project={project_text}; requirement_source={source_text}; "
        f"classification={classification}; affected_action=session-start projection; "
        f"{error}. The task may proceed without fabricated context; no substitute "
        "context was fabricated. "
        "Provide relevant context if you want it applied, and use the Context Library "
        "Manager for canonical additions or corrections."
    )


def blocking_runtime_notice(status: object) -> None:
    condition = getattr(status, "condition")
    remediation = getattr(status, "remediation") or "Fix the Plugin runtime configuration or access."
    message = (
        "Context Library Plugin stopped session-start work because its installed "
        f"runtime is inaccessible ({condition}). Fix the configuration or library "
        "access, explicitly disable the context policy, or uninstall the Plugin "
        "before continuing."
    )
    print(
        json.dumps(
            {
                "schema": "context-library/session-start-result",
                "schema_version": 1,
                "status": "blocked",
                "disposition": "stop",
                "runtime_condition": condition,
                "message": message,
                "remediation": remediation,
                "recovery": ["fix_configuration", "disable", "uninstall"],
            },
            sort_keys=True,
        )
    )
    print(message, file=sys.stderr)


def main() -> int:
    root = projection.activation_root()
    preflight = projection.runtime_config.preflight()
    if preflight.condition in BLOCKING_RUNTIME_CONDITIONS:
        blocking_runtime_notice(preflight)
        return projection.EXIT_ERROR
    try:
        requirement_setting = projection.context_requirement_setting()
        environment_requirement = requirement_setting.value
    except projection.ProjectionError:
        return projection.EXIT_OK
    if environment_requirement == "disabled":
        return projection.EXIT_OK
    try:
        policy = projection.resolve_context_policy(root)
    except (OSError, projection.ProjectionError) as exc:
        requirement = environment_requirement
        source = requirement_setting.source
        if isinstance(exc, projection.PolicyError):
            requirement = requirement or exc.requirement
            source = source or exc.source
        if requirement == "required":
            required_notice(projection.resolution_classification(exc), exc, project=None, source=source)
        return projection.EXIT_OK
    if policy.requirement in {"disabled", "undetermined"}:
        return projection.EXIT_OK
    if policy.project is None:
        if policy.requirement == "required":
            required_notice(
                "ambiguous",
                "no project was explicitly selected",
                project=None,
                source=policy.source,
            )
        return projection.EXIT_OK
    try:
        projection.check(root, automatic=True)
        print(f"Context Library projection already current in {root}.")
        return projection.EXIT_OK
    except projection.CheckError:
        try:
            changed = projection.sync(root, automatic=True)
        except (OSError, projection.ProjectionError) as exc:
            if policy.requirement == "required":
                required_notice(
                    projection.resolution_classification(exc),
                    exc,
                    project=policy.project,
                    source=policy.source,
                )
            return projection.EXIT_OK
        print(f"Context Library projection {'updated' if changed else 'already current'} in {root}.")
        return projection.EXIT_OK
    except (OSError, projection.ProjectionError) as exc:
        if policy.requirement == "required":
            required_notice(
                projection.resolution_classification(exc),
                exc,
                project=policy.project,
                source=policy.source,
            )
        return projection.EXIT_OK

    return projection.EXIT_OK


def diagnostics() -> dict[str, object]:
    root = projection.activation_root()
    preflight = projection.runtime_config.preflight()
    result: dict[str, object] = {
        "root": str(root),
        "requirement": None,
        "project": None,
        "requirement_source": None,
        "availability": None,
        "runtime_condition": preflight.condition if not preflight.allowed else None,
        "installation_state": "healthy" if preflight.allowed else "inaccessible",
        "disposition": "continue" if preflight.allowed else "stop",
        "source_digest": None,
        "projection_fresh": None,
    }
    if not preflight.allowed:
        result["availability"] = "runtime-inaccessible"
        return result
    policy = projection.resolve_context_policy(root)
    result["requirement"] = policy.requirement
    result["project"] = policy.project
    result["requirement_source"] = policy.source
    if policy.requirement == "disabled":
        return result
    if policy.project is None:
        result["availability"] = "ambiguous" if policy.requirement == "required" else None
        return result
    try:
        compilation = projection.prepare(root, automatic=True)
        result["source_digest"] = compilation.source.digest
        result["availability"] = "available"
        try:
            projection.check(root, automatic=True)
            result["projection_fresh"] = True
        except projection.CheckError:
            result["availability"] = "stale-projection"
            result["projection_fresh"] = False
    except (OSError, projection.ProjectionError) as exc:
        result["availability"] = projection.resolution_classification(exc)
        result["runtime_condition"] = getattr(exc, "runtime_condition", None)
        result["projection_fresh"] = False
    return result


if __name__ == "__main__":
    raise SystemExit(main())
