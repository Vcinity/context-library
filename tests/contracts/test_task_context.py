from __future__ import annotations

import hashlib
import json
import subprocess
import sys

import pytest
from pydantic import ValidationError

from context_library_core.retrieval_contracts import TokenizerIdentity
from context_library_core.task_context import (
    TaskContextItem,
    TaskContextRequest,
    render_task_context,
)


def request(budget: int = 1000, *, unknown_tokenizer: bool = False) -> TaskContextRequest:
    return TaskContextRequest(
        project="example-project",
        task_summary="Change the service boundary",
        operation="modify source",
        repository_scopes=["src/service"],
        agent_token_budget=budget,
        tokenizer=TokenizerIdentity(
            name="other" if unknown_tokenizer else "tiktoken",
            version="1" if unknown_tokenizer else "0.9.0",
            vocabulary_revision="other" if unknown_tokenizer else "cl100k_base",
            accounting_method="unknown" if unknown_tokenizer else "tiktoken cl100k_base",
        ),
    )


def items() -> list[TaskContextItem]:
    common = {
        "provenance": "explicit",
        "effective_provenance": "explicit",
        "source_scope": "project/example",
        "supersedes": [],
        "conflict_ids": ["conflict-1"],
    }
    return [
        TaskContextItem(decision_id="rule-current", text="Use the current interface.", state="satisfied", **common),
        TaskContextItem(decision_id="rule-review", text="Review may be required.", state="undetermined", **common),
        TaskContextItem(decision_id="rule-other", text="Other scope.", state="unsatisfied", **common),
    ]


def test_renderer_separates_states_and_accounts_exact_capsule():
    result = render_task_context(request(), items(), revision="rev-1")
    content = result.agent_visible_capsule.serialized_content
    assert [item.decision_id for item in result.operative_directives] == ["rule-current"]
    assert [item.decision_id for item in result.applicability_uncertainties] == ["rule-review"]
    assert [item.decision_id for item in result.non_operative_directives] == ["rule-other"]
    assert result.coverage.complete is True
    assert result.agent_visible_capsule.utf8_byte_count == len(content.encode())
    assert result.agent_visible_capsule.sha256 == hashlib.sha256(content.encode()).hexdigest()


def test_insufficient_budget_is_truthful_and_never_silent():
    result = render_task_context(request(1), items(), revision="rev-1")
    assert result.truncation.truncated is True
    assert result.coverage.complete is False
    assert result.coverage.omitted_operative_decision_ids == ["rule-current"]
    assert result.agent_visible_capsule.serialized_content == ""


def test_unknown_tokenizer_is_separate_from_exact_budget_claim():
    result = render_task_context(request(1000, unknown_tokenizer=True), items(), revision="rev-1")
    assert result.coverage.budget_status == "unverified"
    assert result.agent_visible_capsule.budget_status == "unverified"


def test_response_rejects_false_complete_coverage():
    result = render_task_context(request(), items(), revision="rev-1")
    payload = result.model_dump(by_alias=True)
    payload["coverage"]["complete"] = True
    payload["coverage"]["omitted_operative_decision_ids"] = ["rule-current"]
    with pytest.raises(ValidationError):
        type(result).model_validate(payload)


def test_renderer_cli_is_a_stable_black_box(tmp_path):
    source = tmp_path / "input.json"
    output = tmp_path / "response.json"
    source.write_text(
        json.dumps(
            {
                "request": request().model_dump(by_alias=True),
                "items": [item.model_dump(mode="json") for item in items()],
                "revision": "rev-1",
            }
        )
    )
    completed = subprocess.run(
        [sys.executable, "scripts/render_task_context.py", "--input", str(source), "--output", str(output)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0
    assert json.loads(output.read_text())["schema"] == "context-library/task-context-response"
