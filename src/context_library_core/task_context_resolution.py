from __future__ import annotations

from .applicability import evaluate_applicability
from .canonical import parse_register
from .contracts import ApplicabilityRequest
from .task_context import TaskContextItem, TaskContextRequest, TaskContextResponse, render_task_context


def resolve_task_context(
    register: str,
    request: TaskContextRequest,
    *,
    revision: str,
    source_scope: str,
) -> TaskContextResponse:
    decisions = parse_register(register)
    superseded = {identifier for decision in decisions for identifier in decision.supersedes}
    items: list[TaskContextItem] = []
    for decision in decisions:
        scopes = list(decision.metadata.get("repository_scopes", decision.affected_layers))
        applicability = evaluate_applicability(
            ApplicabilityRequest(
                task={"repository_scopes": request.repository_scopes},
                decision={
                    "decision_id": decision.decision_id,
                    "repository_scopes": scopes,
                    "provenance": decision.provenance,
                    "effective_provenance": decision.provenance,
                    "source_scope": source_scope,
                    "supersedes": list(decision.supersedes),
                    "conflict_ids": list(decision.conflicts_with),
                    "applies_when": decision.applies_when,
                },
            )
        )
        state = applicability.state
        if decision.provenance != "explicit" or decision.decision_id in superseded:
            state = type(state).UNSATISFIED
        items.append(
            TaskContextItem(
                decision_id=decision.decision_id,
                text=decision.decision,
                state=state,
                provenance=decision.provenance,
                effective_provenance=decision.provenance,
                source_scope=source_scope,
                supersedes=list(decision.supersedes),
                conflict_ids=list(decision.conflicts_with),
            )
        )
    return render_task_context(request, items, revision=revision)
