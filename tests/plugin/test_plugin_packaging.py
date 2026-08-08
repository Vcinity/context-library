from __future__ import annotations

import hashlib
import importlib.util
import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).parents[2]
BUILD_PATH = ROOT / "scripts/build_plugin.py"
MARKETPLACE_PATH = ROOT / ".agents/plugins/marketplace.json"


def load_build_module():
    spec = importlib.util.spec_from_file_location("context_library_plugin_build", BUILD_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_plugin_archive_is_deterministic_and_excludes_runtime_caches(tmp_path):
    build = load_build_module()
    build.OUTPUT = tmp_path / build.OUTPUT.name
    assert build.main() == 0
    first = hashlib.sha256(build.OUTPUT.read_bytes()).hexdigest()
    assert build.main() == 0
    second = hashlib.sha256(build.OUTPUT.read_bytes()).hexdigest()
    assert first == second
    with zipfile.ZipFile(build.OUTPUT) as archive:
        names = archive.namelist()
    assert names == sorted(names)
    assert not any("__pycache__" in name or name.endswith((".pyc", ".pyo")) for name in names)
    assert "context-library/runtime-config.json" not in names


def test_plugin_archive_embeds_runtime_config_only_when_explicitly_requested(tmp_path):
    build = load_build_module()
    build.OUTPUT = tmp_path / build.OUTPUT.name
    library = tmp_path / "library"
    library.mkdir()
    runtime_config = tmp_path / "runtime-config.json"
    runtime_config.write_text(
        json.dumps(
            {
                "schema": "context-library/plugin-runtime-config",
                "schema_version": 1,
                "library_root": str(library),
                "project": "demo",
                "context_requirement": "optional",
            }
        ),
        encoding="utf-8",
    )
    assert build.main(["--runtime-config", str(runtime_config)]) == 0
    with zipfile.ZipFile(build.OUTPUT) as archive:
        assert archive.namelist() == sorted(archive.namelist())
        embedded = json.loads(archive.read("context-library/runtime-config.json"))
    assert embedded["library_root"] == str(library)


def test_community_marketplace_entry_installs_the_local_plugin():
    marketplace = json.loads(MARKETPLACE_PATH.read_text(encoding="utf-8"))
    assert marketplace["name"] == "context-library"
    assert marketplace["interface"]["displayName"] == "Context Library"
    assert marketplace["plugins"] == [
        {
            "name": "context-library",
            "source": {"source": "local", "path": "./plugins/context-library"},
            "policy": {
                "installation": "AVAILABLE",
                "authentication": "ON_INSTALL",
            },
            "category": "Productivity",
        }
    ]
