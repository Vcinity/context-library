#!/usr/bin/env python3
"""Check or safely synchronize repository-local Context Library guidance."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import projection  # noqa: E402


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


def main() -> None:
    root = projection.activation_root()
    try:
        requirement_setting = projection.context_requirement_setting()
        environment_requirement = requirement_setting.value
    except projection.ProjectionError:
        return
    if environment_requirement == "disabled":
        return
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
        return
    if policy.requirement in {"disabled", "undetermined"}:
        return
    if policy.project is None:
        if policy.requirement == "required":
            required_notice(
                "ambiguous",
                "no project was explicitly selected",
                project=None,
                source=policy.source,
            )
        return
    try:
        projection.check(root)
        print(f"Context Library projection already current in {root}.")
        return
    except projection.CheckError:
        try:
            changed = projection.sync(root)
        except (OSError, projection.ProjectionError) as exc:
            if policy.requirement == "required":
                required_notice(
                    projection.resolution_classification(exc),
                    exc,
                    project=policy.project,
                    source=policy.source,
                )
            return
        print(f"Context Library projection {'updated' if changed else 'already current'} in {root}.")
        return
    except (OSError, projection.ProjectionError) as exc:
        if policy.requirement == "required":
            required_notice(
                projection.resolution_classification(exc),
                exc,
                project=policy.project,
                source=policy.source,
            )
        return


def diagnostics() -> dict[str, object]:
    root = projection.activation_root()
    policy = projection.resolve_context_policy(root)
    result: dict[str, object] = {
        "root": str(root),
        "requirement": policy.requirement,
        "project": policy.project,
        "requirement_source": policy.source,
        "availability": None,
        "source_digest": None,
        "projection_fresh": None,
    }
    if policy.requirement == "disabled":
        return result
    if policy.project is None:
        result["availability"] = "ambiguous" if policy.requirement == "required" else None
        return result
    try:
        compilation = projection.prepare(root)
        result["source_digest"] = compilation.source.digest
        result["availability"] = "available"
        try:
            projection.check(root)
            result["projection_fresh"] = True
        except projection.CheckError:
            result["availability"] = "stale-projection"
            result["projection_fresh"] = False
    except (OSError, projection.ProjectionError) as exc:
        result["availability"] = projection.resolution_classification(exc)
        result["projection_fresh"] = False
    return result


if __name__ == "__main__":
    main()
