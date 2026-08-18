from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).parents[2]
PLUGIN = ROOT / "plugins/context-library"
sys.path.insert(0, str(PLUGIN))

import projection  # noqa: E402
from hooks import session_start  # noqa: E402


def configure_runtime(path: Path, library_root: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "schema": "context-library/plugin-runtime-config",
                "schema_version": 1,
                "library_root": str(library_root),
            }
        ),
        encoding="utf-8",
    )


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
    config_path = tmp_path / "runtime-config.json"
    monkeypatch.setattr(session_start.projection.runtime_config, "CONFIG_PATH", config_path)
    load_runtime_config = session_start.projection.runtime_config.load_runtime_config

    assert_blocked(capsys, "missing_config")

    config_path.write_text("{invalid", encoding="utf-8")
    assert_blocked(capsys, "malformed_config")

    def unreadable(_path):
        raise session_start.projection.runtime_config.RuntimeConfigUnreadable("hidden")

    monkeypatch.setattr(session_start.projection.runtime_config, "load_runtime_config", unreadable)
    assert_blocked(capsys, "unreadable_config")
    monkeypatch.setattr(session_start.projection.runtime_config, "load_runtime_config", load_runtime_config)
    monkeypatch.delenv("CONTEXT_LIBRARY_ROOT", raising=False)
    monkeypatch.delenv("CONTEXT_LIBRARY_CONTEXT_REQUIREMENT", raising=False)
    monkeypatch.setenv("CONTEXT_LIBRARY_PROJECT_ROOT", str(root))
    monkeypatch.setattr(session_start.projection.runtime_config, "CONFIG_PATH", config_path)

    configure_runtime(config_path, tmp_path / "missing-root")
    assert_blocked(capsys, "missing_root")

    library_root = tmp_path / "library"
    library_root.mkdir()
    configure_runtime(config_path, library_root)
    monkeypatch.setattr(
        session_start.projection.runtime_config,
        "_root_condition",
        lambda _root: session_start.projection.runtime_config.CONDITION_UNREADABLE_ROOT,
    )
    assert_blocked(capsys, "unreadable_root")


def test_disabled_is_silent_after_healthy_preflight(monkeypatch, tmp_path, capsys):
    root = tmp_path / "activation"
    root.mkdir()
    library_root = tmp_path / "library"
    library_root.mkdir()
    config_path = tmp_path / "runtime-config.json"
    configure_runtime(config_path, library_root)
    monkeypatch.setattr(session_start.projection.runtime_config, "CONFIG_PATH", config_path)
    monkeypatch.setenv("CONTEXT_LIBRARY_PROJECT_ROOT", str(root))
    monkeypatch.setenv("CONTEXT_LIBRARY_CONTEXT_REQUIREMENT", "disabled")

    assert session_start.main() == projection.EXIT_OK
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
