from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Callable, Iterable

from .config import ManagedProject


class ProjectLifecycle(StrEnum):
    ENABLED = "enabled"
    PAUSED = "paused"
    DRAINING = "draining"
    DISABLED = "disabled"
    ERROR = "error"


@dataclass
class ProjectRuntime:
    configuration: ManagedProject
    lifecycle: ProjectLifecycle = ProjectLifecycle.ENABLED


class ProjectRuntimeRegistry:
    """Explicit project enrollment and lifecycle state for shared processes."""

    def __init__(self, projects: Iterable[ManagedProject]):
        entries = list(projects)
        if not entries:
            raise ValueError("at least one managed project is required")
        if len({entry.id for entry in entries}) != len(entries):
            raise ValueError("managed project IDs must be unique")
        self._projects = {entry.id: ProjectRuntime(entry) for entry in entries}

    def get(self, project: str) -> ProjectRuntime:
        try:
            return self._projects[project]
        except KeyError as exc:
            raise KeyError(f"project is not enrolled: {project}") from exc

    def enabled(self) -> tuple[str, ...]:
        return tuple(
            project
            for project, runtime in self._projects.items()
            if runtime.configuration.enabled and runtime.lifecycle == ProjectLifecycle.ENABLED
        )

    def transition(self, project: str, target: ProjectLifecycle) -> ProjectLifecycle:
        runtime = self.get(project)
        current = runtime.lifecycle
        allowed = {
            ProjectLifecycle.ENABLED: {ProjectLifecycle.PAUSED, ProjectLifecycle.DRAINING, ProjectLifecycle.ERROR},
            ProjectLifecycle.PAUSED: {ProjectLifecycle.ENABLED, ProjectLifecycle.DRAINING, ProjectLifecycle.ERROR},
            ProjectLifecycle.DRAINING: {ProjectLifecycle.DISABLED, ProjectLifecycle.ERROR},
            ProjectLifecycle.DISABLED: {ProjectLifecycle.ENABLED},
            ProjectLifecycle.ERROR: {ProjectLifecycle.PAUSED, ProjectLifecycle.DRAINING},
        }
        if target != current and target not in allowed[current]:
            raise ValueError(f"invalid project lifecycle transition {current} -> {target}")
        runtime.lifecycle = target
        return current


class FairProjectScheduler:
    """Starvation-free round-robin selection over eligible project queues."""

    def __init__(self, registry: ProjectRuntimeRegistry):
        self.registry = registry
        self._cursor = 0

    def next_project(self, eligible: Iterable[str]) -> str | None:
        active = tuple(project for project in self.registry.enabled() if project in set(eligible))
        if not active:
            return None
        index = self._cursor % len(active)
        selected = active[index]
        self._cursor = (index + 1) % len(active)
        return selected

    def claim_next(
        self,
        store,
        owner: str,
        eligible: Iterable[str],
        lease_seconds: int,
        claim: Callable[..., object] | None = None,
    ):
        """Claim at most one item, preserving the selected project's identity."""

        claim = claim or store.claim
        candidates = tuple(eligible)
        for _ in range(len(candidates)):
            project = self.next_project(candidates)
            if project is None:
                return None
            row = claim(project, owner, lease_seconds)
            if row is not None:
                return project, row
            candidates = tuple(item for item in candidates if item != project) + (project,)
        return None
