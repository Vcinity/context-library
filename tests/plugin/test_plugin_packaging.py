from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
BUILD_PATH = ROOT / "scripts/build_plugin.py"
INSTALL_PATH = ROOT / "scripts/install_plugin.py"
MARKETPLACE_PATH = ROOT / ".agents/plugins/marketplace.json"


def load_build_module():
    spec = importlib.util.spec_from_file_location("context_library_plugin_build", BUILD_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_install_module():
    spec = importlib.util.spec_from_file_location("context_library_plugin_install", INSTALL_PATH)
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
    assert marketplace["interface"]["displayName"] == "Vcinity Engineering"
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


def test_plugin_installer_stages_configured_marketplace_without_mutating_source(tmp_path):
    install = load_install_module()
    library = tmp_path / "library"
    library.mkdir()
    destination = tmp_path / "marketplace"
    source_marketplace = MARKETPLACE_PATH.read_bytes()

    assert (
        install.main(
            [
                "--destination",
                str(destination),
                "--library-root",
                str(library),
                "--marketplace-name",
                "local-context",
                "--project",
                "demo",
                "--context-requirement",
                "optional",
                "--stage-only",
            ]
        )
        == 0
    )

    assert MARKETPLACE_PATH.read_bytes() == source_marketplace
    marketplace = json.loads((destination / ".agents/plugins/marketplace.json").read_text())
    assert marketplace["name"] == "local-context"
    runtime_config = json.loads((destination / "plugins/context-library/runtime-config.json").read_text())
    assert runtime_config == {
        "context_requirement": "optional",
        "library_root": str(library),
        "project": "demo",
        "schema": "context-library/plugin-runtime-config",
        "schema_version": 1,
    }
    assert (destination / "plugins/context-library/.codex-plugin/plugin.json").is_file()
    assert not (destination / "src").exists()


def test_plugin_installer_registers_staged_marketplace(tmp_path):
    install = load_install_module()
    library = tmp_path / "library"
    library.mkdir()
    destination = tmp_path / "marketplace"
    codex_commands: list[list[str]] = []

    def runner(command, *, check, text):
        if command[0] == sys.executable:
            return subprocess.run(command, check=check, text=text)
        codex_commands.append(command)
        return subprocess.CompletedProcess(command, 0)

    assert (
        install.main(
            ["--destination", str(destination), "--library-root", str(library)],
            runner=runner,
        )
        == 0
    )
    assert codex_commands == [
        ["codex", "plugin", "marketplace", "add", str(destination)],
        ["codex", "plugin", "add", "context-library@context-library"],
    ]


def test_plugin_installer_refuses_existing_destination(tmp_path):
    install = load_install_module()
    library = tmp_path / "library"
    library.mkdir()
    destination = tmp_path / "marketplace"
    destination.mkdir()
    sentinel = destination / "keep.txt"
    sentinel.write_text("owned", encoding="utf-8")

    with pytest.raises(SystemExit):
        install.main(
            [
                "--destination",
                str(destination),
                "--library-root",
                str(library),
                "--stage-only",
            ]
        )
    assert sentinel.read_text(encoding="utf-8") == "owned"


def test_plugin_installer_does_not_publish_partial_destination(tmp_path):
    install = load_install_module()
    library = tmp_path / "library"
    library.mkdir()
    destination = tmp_path / "marketplace"

    def failing_runner(command, *, check, text):
        raise subprocess.CalledProcessError(2, command)

    with pytest.raises(subprocess.CalledProcessError):
        install.main(
            [
                "--destination",
                str(destination),
                "--library-root",
                str(library),
                "--stage-only",
            ],
            runner=failing_runner,
        )
    assert not destination.exists()


def test_make_plugin_install_stages_marketplace(tmp_path):
    library = tmp_path / "library"
    library.mkdir()
    destination = tmp_path / "marketplace"

    subprocess.run(
        [
            "make",
            "plugin-install",
            f"PLUGIN_DEST={destination}",
            f"LIBRARY_ROOT={library}",
            "STAGE_ONLY=1",
        ],
        cwd=ROOT,
        check=True,
        text=True,
    )
    runtime_config = json.loads((destination / "plugins/context-library/runtime-config.json").read_text())
    assert runtime_config["library_root"] == str(library)


def test_plugin_installer_runs_from_documented_sparse_source(tmp_path):
    sparse_source = tmp_path / "sparse-source"
    (sparse_source / ".agents/plugins").mkdir(parents=True)
    (sparse_source / "plugins").mkdir()
    (sparse_source / "scripts").mkdir()
    shutil.copy2(MARKETPLACE_PATH, sparse_source / ".agents/plugins/marketplace.json")
    shutil.copytree(ROOT / "plugins/context-library", sparse_source / "plugins/context-library")
    shutil.copy2(INSTALL_PATH, sparse_source / "scripts/install_plugin.py")

    library = tmp_path / "library"
    library.mkdir()
    destination = tmp_path / "marketplace"
    subprocess.run(
        [
            sys.executable,
            str(sparse_source / "scripts/install_plugin.py"),
            "--destination",
            str(destination),
            "--library-root",
            str(library),
            "--stage-only",
        ],
        check=True,
        text=True,
    )
    assert (destination / ".agents/plugins/marketplace.json").is_file()
    assert (destination / "plugins/context-library/runtime-config.json").is_file()
