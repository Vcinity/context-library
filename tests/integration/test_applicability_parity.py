from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from context_library_core.applicability import evaluate_applicability as core_evaluate
from context_library_core.contracts import ApplicabilityRequest, ApplicabilityState
from context_library_maintainer.applicability import evaluate_applicability as maintainer_evaluate
from context_library_manager.applicability import evaluate_applicability as manager_evaluate


def request(task_scopes: list[str], decision_scopes: list[str], applies_when: str | None = None):
    return ApplicabilityRequest(
        task={"repository_scopes": task_scopes},
        decision={
            "decision_id": "parity-rule",
            "repository_scopes": decision_scopes,
            "provenance": "explicit",
            "effective_provenance": "inferred" if applies_when else "explicit",
            "source_scope": "project/parity",
            "supersedes": ["parity-old"],
            "conflict_ids": ["parity-conflict"],
            "applies_when": applies_when,
        },
    )


def test_core_maintainer_manager_and_generated_plugin_are_observably_identical():
    plugin_path = Path(__file__).parents[2] / "plugins/context-library/generated/core_runtime.py"
    spec = importlib.util.spec_from_file_location("parity_plugin_runtime", plugin_path)
    assert spec and spec.loader
    plugin = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = plugin
    spec.loader.exec_module(plugin)
    for fixture in (
        request([], []),
        request(["src/example"], ["src/example"]),
        request(["tests/example"], ["src/example"]),
        request([], ["src/example"]),
    ):
        core = core_evaluate(fixture).model_dump(mode="json")
        assert maintainer_evaluate(fixture).model_dump(mode="json") == core
        assert manager_evaluate(fixture).model_dump(mode="json") == core
        plugin_result = plugin.evaluate_applicability(fixture.model_dump(by_alias=True))
        assert {key: plugin_result[key] for key in ("decision_id", "state", "reason", "matched_selectors")} == {
            key: core[key] for key in ("decision_id", "state", "reason", "matched_selectors")
        }
        assert plugin_result["effective_provenance"] == core["effective_provenance"]
        assert plugin_result["source_scope"] == core["source_scope"]


def test_undetermined_and_unsatisfied_are_not_operative():
    assert core_evaluate(request([], ["src/example"])).state == ApplicabilityState.UNDETERMINED
    assert core_evaluate(request(["tests/example"], ["src/example"])).state == ApplicabilityState.UNSATISFIED
