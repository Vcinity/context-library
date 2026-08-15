from __future__ import annotations

from context_library_core.task_context import TaskContextRequest
from context_library_core.task_context_resolution import resolve_task_context

REGISTER = """# Synthetic register

<a id="rule-current"></a>
### Current
- Provenance: explicit
- Decision: Use the current interface.
- Supersedes: rule-old
- Affected Layers: src/service

<a id="rule-old"></a>
### Old
- Provenance: explicit
- Decision: Use the old interface.
- Affected Layers: src/service

<a id="rule-conditional"></a>
### Conditional
- Provenance: explicit
- Decision: Review the deployment tier.
- Applies-When: tier is known
- Affected Layers: src/service

<a id="rule-other"></a>
### Other
- Provenance: explicit
- Decision: Use another scope.
- Affected Layers: tests
"""


def request(budget=1000):
    return TaskContextRequest(
        project="example-project",
        task_summary="Change the service boundary",
        operation="modify source",
        repository_scopes=["src/service"],
        agent_token_budget=budget,
        tokenizer={
            "name": "tiktoken",
            "version": "0.9.0",
            "vocabulary_revision": "cl100k_base",
            "accounting_method": "tiktoken cl100k_base",
        },
    )


def test_resolution_preserves_operative_uncertain_and_superseded_states():
    result = resolve_task_context(REGISTER, request(), revision="rev-1", source_scope="project/example")
    assert [item.decision_id for item in result.operative_directives] == ["rule-current"]
    assert [item.decision_id for item in result.applicability_uncertainties] == ["rule-conditional"]
    assert {item.decision_id for item in result.non_operative_directives} == {"rule-old", "rule-other"}
    assert result.revision == "rev-1"


def test_resolution_is_deterministic_and_budget_truthful():
    first = resolve_task_context(REGISTER, request(1), revision="rev-1", source_scope="project/example")
    second = resolve_task_context(REGISTER, request(1), revision="rev-1", source_scope="project/example")
    assert first.model_dump_json() == second.model_dump_json()
    assert first.truncation.truncated is True
    assert first.coverage.omitted_operative_decision_ids == ["rule-current"]
