from __future__ import annotations

from context_library_core.contracts import ApplicabilityRequest, ApplicabilityResult
from context_library_maintainer.applicability import evaluate_applicability as _evaluate


def evaluate_applicability(request: ApplicabilityRequest) -> ApplicabilityResult:
    """Manager read adapter; it delegates to the Maintainer/Core evaluator."""
    return _evaluate(request)
