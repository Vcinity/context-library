from __future__ import annotations

from .contracts import ApplicabilityRequest, ApplicabilityResult, ApplicabilityState


def evaluate_applicability(request: ApplicabilityRequest) -> ApplicabilityResult:
    decision = request.decision
    task_scopes = set(request.task.repository_scopes)
    decision_scopes = set(decision.repository_scopes)
    required = ["repository_scopes"] if decision_scopes else []
    matched = sorted(task_scopes & decision_scopes)
    if not decision_scopes and decision.applies_when is None:
        state = ApplicabilityState.UNCONDITIONAL
        reason = "none"
    elif decision.applies_when is not None:
        state = ApplicabilityState.UNDETERMINED
        reason = "conditional-unresolved"
    elif not task_scopes:
        state = ApplicabilityState.UNDETERMINED
        reason = "missing-task-signal"
    elif matched:
        state = ApplicabilityState.SATISFIED
        reason = "none"
    else:
        state = ApplicabilityState.UNSATISFIED
        reason = "scope-mismatch"
    return ApplicabilityResult(
        decision_id=decision.decision_id,
        state=state,
        matched_selectors={"repository_scopes": matched} if matched else {},
        required_selectors=required,
        reason=reason,
        provenance=decision.provenance,
        effective_provenance=decision.effective_provenance,
        source_scope=decision.source_scope,
        supersedes=decision.supersedes,
        conflict_ids=decision.conflict_ids,
    )
