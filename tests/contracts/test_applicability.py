from __future__ import annotations

import json
import subprocess
import sys

import pytest
from pydantic import ValidationError

from context_library_core.applicability import evaluate_applicability
from context_library_core.contracts import ApplicabilityRequest, ApplicabilityState


def request(*, task_scopes: list[str], decision_scopes: list[str], applies_when: str | None = None):
    return ApplicabilityRequest(
        task={"repository_scopes": task_scopes},
        decision={
            "decision_id": "rule-current",
            "repository_scopes": decision_scopes,
            "provenance": "explicit",
            "effective_provenance": "explicit",
            "source_scope": "project/example",
            "supersedes": ["rule-old"],
            "conflict_ids": ["conflict-1"],
            "applies_when": applies_when,
        },
    )


@pytest.mark.parametrize(
    ("task", "scopes", "state"),
    [
        ([], [], ApplicabilityState.UNCONDITIONAL),
        (["src/example"], ["src/example"], ApplicabilityState.SATISFIED),
        (["tests/example"], ["src/example"], ApplicabilityState.UNSATISFIED),
        ([], ["src/example"], ApplicabilityState.UNDETERMINED),
    ],
)
def test_evaluator_states_are_deterministic_and_preserves_authority(task, scopes, state):
    result = evaluate_applicability(request(task_scopes=task, decision_scopes=scopes))
    assert result.state == state
    assert result.provenance == "explicit"
    assert result.source_scope == "project/example"
    assert result.supersedes == ["rule-old"]
    assert result.conflict_ids == ["conflict-1"]
    assert (
        result.model_dump_json()
        == evaluate_applicability(request(task_scopes=task, decision_scopes=scopes)).model_dump_json()
    )


def test_conditional_and_undetermined_are_not_operative():
    result = evaluate_applicability(
        request(task_scopes=["src/example"], decision_scopes=[], applies_when="review is complete")
    )
    assert result.state == ApplicabilityState.UNDETERMINED
    assert result.reason == "conditional-unresolved"


@pytest.mark.parametrize(
    "scopes", [["/absolute"], ["src/../example"], ["src//example"], ["src/example", "src/example"]]
)
def test_unsafe_or_ambiguous_scopes_fail_closed(scopes):
    with pytest.raises(ValidationError):
        request(task_scopes=scopes, decision_scopes=[])


def test_cli_emits_versioned_result(tmp_path):
    source = tmp_path / "request.json"
    output = tmp_path / "result.json"
    source.write_text(
        request(task_scopes=["src/example"], decision_scopes=["src/example"]).model_dump_json(by_alias=True)
    )
    completed = subprocess.run(
        [sys.executable, "scripts/evaluate_applicability.py", "--input", str(source), "--output", str(output)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0
    assert json.loads(output.read_text())["state"] == "satisfied"
