from __future__ import annotations

from context_library_core.applicability import evaluate_applicability as _evaluate
from context_library_core.contracts import ApplicabilityRequest, ApplicabilityResult


def evaluate_applicability(request: ApplicabilityRequest) -> ApplicabilityResult:
    """Maintainer read adapter; Core remains the sole applicability authority."""
    return _evaluate(request)
