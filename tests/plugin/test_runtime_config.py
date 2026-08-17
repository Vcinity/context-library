from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
PLUGIN = ROOT / "plugins/context-library"
sys.path.insert(0, str(PLUGIN))

import projection  # noqa: E402
import runtime_config  # noqa: E402


def config_payload(**overrides: object) -> dict[str, object]:
    return {
        "schema": runtime_config.SCHEMA,
        "schema_version": runtime_config.SCHEMA_VERSION,
        "library_root": "/srv/context-library",
        **overrides,
    }


def test_runtime_config_resolves_bundled_values_with_environment_precedence(tmp_path, monkeypatch):
    path = tmp_path / "runtime-config.json"
    path.write_text(
        json.dumps(config_payload(project="demo", context_requirement="optional")),
        encoding="utf-8",
    )
    monkeypatch.delenv("CONTEXT_LIBRARY_ROOT", raising=False)
    assert runtime_config.setting("library_root", path).value == "/srv/context-library"
    assert runtime_config.setting("library_root", path).source == str(path)
    monkeypatch.setenv("CONTEXT_LIBRARY_ROOT", "/override/library")
    assert runtime_config.setting("library_root", path) == runtime_config.Setting("/override/library", "environment")


def test_bundled_config_is_shared_by_projection_and_mcp(tmp_path, monkeypatch):
    library = tmp_path / "library"
    library.mkdir()
    path = tmp_path / "runtime-config.json"
    path.write_text(
        json.dumps(config_payload(library_root=str(library), project="demo", context_requirement="required")),
        encoding="utf-8",
    )
    for name in runtime_config.FIELDS.values():
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(runtime_config, "CONFIG_PATH", path)

    assert projection.library_root() == library
    policy = projection.resolve_context_policy(tmp_path / "consumer")
    assert (policy.requirement, policy.project, policy.source) == ("required", "demo", str(path))

    server_path = PLUGIN / "mcp/context_library_server.py"
    spec = importlib.util.spec_from_file_location("configured_context_library_mcp", server_path)
    assert spec and spec.loader
    server = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(server)
    assert server.library_root() == library


@pytest.mark.parametrize(
    "overrides,match",
    [
        ({"unexpected": "value"}, "unknown"),
        ({"project": "Not Stable"}, "stable lowercase"),
        ({"context_requirement": "sometimes"}, "invalid context requirement"),
        ({"library_root": "relative/library"}, "absolute path"),
    ],
)
def test_runtime_config_rejects_invalid_values(tmp_path, overrides, match):
    path = tmp_path / "runtime-config.json"
    path.write_text(json.dumps(config_payload(**overrides)), encoding="utf-8")
    with pytest.raises(runtime_config.RuntimeConfigError, match=match):
        runtime_config.load_runtime_config(path)


def test_configure_script_creates_valid_deployment_local_config(tmp_path):
    library = tmp_path / "library"
    library.mkdir()
    output = tmp_path / "runtime-config.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(PLUGIN / "scripts/configure.py"),
            "--library-root",
            str(library),
            "--project",
            "demo",
            "--context-requirement",
            "required",
            "--output",
            str(output),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert completed.stdout.strip() == str(output)
    assert runtime_config.load_runtime_config(output) == {
        "library_root": str(library),
        "project": "demo",
        "context_requirement": "required",
    }
    assert output.stat().st_mode & 0o777 == 0o644


def test_configure_script_renames_deployment_marketplace(tmp_path):
    library = tmp_path / "library"
    library.mkdir()
    output = tmp_path / "plugin/runtime-config.json"
    marketplace = tmp_path / "marketplace/.agents/plugins/marketplace.json"
    marketplace.parent.mkdir(parents=True)
    marketplace.write_text(
        json.dumps(
            {
                "name": "context-library",
                "interface": {"displayName": "Context Library"},
                "plugins": [
                    {
                        "name": "context-library",
                        "source": {"source": "local", "path": "./plugins/context-library"},
                        "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
                        "category": "Productivity",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            sys.executable,
            str(PLUGIN / "scripts/configure.py"),
            "--library-root",
            str(library),
            "--output",
            str(output),
            "--marketplace-name",
            "team-context",
            "--marketplace-path",
            str(marketplace),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert completed.stdout.splitlines() == [str(output), str(marketplace)]
    payload = json.loads(marketplace.read_text(encoding="utf-8"))
    assert payload["name"] == "team-context"
    assert payload["interface"] == {"displayName": "Context Library"}
    assert payload["plugins"][0]["source"] == {"source": "local", "path": "./plugins/context-library"}
    assert marketplace.stat().st_mode & 0o777 == 0o644


def test_configure_script_preserves_existing_config_when_new_values_are_invalid(tmp_path):
    library = tmp_path / "library"
    library.mkdir()
    output = tmp_path / "runtime-config.json"
    original = json.dumps(config_payload()) + "\n"
    output.write_text(original, encoding="utf-8")
    completed = subprocess.run(
        [
            sys.executable,
            str(PLUGIN / "scripts/configure.py"),
            "--library-root",
            str(library),
            "--project",
            "Not Stable",
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 2
    assert "stable lowercase identifier" in completed.stderr
    assert output.read_text(encoding="utf-8") == original


def test_configure_script_rejects_invalid_marketplace_before_writing_runtime_config(tmp_path):
    library = tmp_path / "library"
    library.mkdir()
    output = tmp_path / "runtime-config.json"
    marketplace = tmp_path / "marketplace.json"
    marketplace.write_text('{"name":"old","plugins":[]}\n', encoding="utf-8")
    completed = subprocess.run(
        [
            sys.executable,
            str(PLUGIN / "scripts/configure.py"),
            "--library-root",
            str(library),
            "--output",
            str(output),
            "--marketplace-name",
            "team-context",
            "--marketplace-path",
            str(marketplace),
        ],
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 2
    assert "exactly one context-library" in completed.stderr
    assert not output.exists()
    assert marketplace.read_text(encoding="utf-8") == '{"name":"old","plugins":[]}\n'
