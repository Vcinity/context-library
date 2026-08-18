from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).parents[2]
PLUGIN = ROOT / "plugins/context-library"
sys.path.insert(0, str(PLUGIN))

import projection  # noqa: E402
from hooks import session_start  # noqa: E402


def assert_blocked(capsys, condition: str) -> None:
    assert session_start.main() == projection.EXIT_ERROR
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "blocked"
    assert result["disposition"] == "stop"
    assert result["runtime_condition"] == condition
    assert result["recovery"] == ["fix_configuration", "disable", "uninstall"]


def test_all_inaccessible_runtime_conditions_stop_before_policy(monkeypatch, tmp_path, capsys):
    root = tmp_path / "activation"
    root.mkdir()
    monkeypatch.setenv("CONTEXT_LIBRARY_PROJECT_ROOT", str(root))
    monkeypatch.delenv("CONTEXT_LIBRARY_ROOT", raising=False)
    monkeypatch.delenv("CONTEXT_LIBRARY_CONTEXT_REQUIREMENT", raising=False)
    runtime_config = session_start.projection.runtime_config

    for condition in (
        runtime_config.CONDITION_MISSING_CONFIG,
        runtime_config.CONDITION_MALFORMED_CONFIG,
        runtime_config.CONDITION_UNREADABLE_CONFIG,
        runtime_config.CONDITION_MISSING_ROOT,
        runtime_config.CONDITION_UNREADABLE_ROOT,
    ):
        monkeypatch.setattr(
            runtime_config,
            "preflight",
            lambda condition=condition: runtime_config.RuntimePreflight(
                condition, False, "redacted", None, None, "safe remediation"
            ),
        )
        assert_blocked(capsys, condition)


def test_disabled_is_silent_after_healthy_preflight(monkeypatch, tmp_path, capsys):
    root = tmp_path / "activation"
    root.mkdir()
    monkeypatch.setenv("CONTEXT_LIBRARY_PROJECT_ROOT", str(root))
    monkeypatch.setenv("CONTEXT_LIBRARY_CONTEXT_REQUIREMENT", "disabled")
    runtime_config = session_start.projection.runtime_config
    monkeypatch.setattr(
        runtime_config,
        "preflight",
        lambda: runtime_config.RuntimePreflight(
            "healthy", True, "redacted", "environment", str(tmp_path), None
        ),
    )

    assert session_start.main() == projection.EXIT_OK
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
