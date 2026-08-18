#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HOOK = ROOT / "plugins/context-library/hooks/session_start.py"
FIXTURE_LIBRARY = ROOT / "scripts/plugin/fixtures/projection/library"
MARKER = "<!-- context-library:begin"


def run(
    cwd: Path,
    library: Path,
    override: Path | None = None,
    *,
    environment_requirement: str | None = None,
) -> str:
    env = os.environ.copy()
    env["CONTEXT_LIBRARY_ROOT"] = str(library)
    env.pop("CONTEXT_LIBRARY_PROJECT", None)
    if environment_requirement is None:
        env.pop("CONTEXT_LIBRARY_CONTEXT_REQUIREMENT", None)
    else:
        env["CONTEXT_LIBRARY_CONTEXT_REQUIREMENT"] = environment_requirement
    if override is None:
        env.pop("CONTEXT_LIBRARY_PROJECT_ROOT", None)
    else:
        env["CONTEXT_LIBRARY_PROJECT_ROOT"] = str(override)
    result = subprocess.run(
        [sys.executable, str(HOOK)],
        check=True,
        capture_output=True,
        text=True,
        env=env,
        cwd=cwd,
    )
    return result.stdout


def configure(root: Path, requirement: str, *, project: str | None = "demo") -> None:
    path = root / ".context-library/config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "context-library/context-policy",
        "schema_version": 1,
        "context_requirement": requirement,
        "affected_layers": {"ui": "ui"},
    }
    if project is not None:
        payload["project"] = project
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")


def blocked(root: Path, library: Path, override: Path | None = None) -> tuple[str, str]:
    try:
        run(root, library, override)
    except subprocess.CalledProcessError as exc:
        return exc.stdout, exc.stderr
    raise AssertionError("expected the installed Plugin hook to stop")


def assert_no_interference(root: Path, library: Path, requirement: str | None) -> None:
    root.mkdir()
    (root / "AGENTS.md").write_text("# Human guidance\n", encoding="utf-8")
    if requirement is not None:
        configure(root, requirement, project="absent")
    before = (root / "AGENTS.md").read_bytes()
    output = run(root, library, root)
    assert output == ""
    assert (root / "AGENTS.md").read_bytes() == before
    assert not (root / ".context-library/projection.json").exists()


def main() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        unavailable = base / "unavailable-library"
        available = base / "available-library"
        shutil.copytree(FIXTURE_LIBRARY, available)
        shutil.rmtree(available / "projects/demo")

        assert_no_interference(base / "undetermined", available, None)
        assert_no_interference(base / "optional", available, "optional")
        assert_no_interference(base / "disabled", available, "disabled")

        required = base / "required"
        required.mkdir()
        (required / "AGENTS.md").write_text("# Human guidance\n", encoding="utf-8")
        configure(required, "required")
        before = (required / "AGENTS.md").read_bytes()
        notice = run(required, available, required)
        assert "required-context notice" in notice
        assert "without fabricated context" in notice
        assert "Context Library Manager" in notice
        assert "classification=missing" in notice
        assert "affected_action=session-start projection" in notice
        assert "project=demo" in notice
        assert (required / "AGENTS.md").read_bytes() == before

        missing_project = base / "missing-project"
        missing_project.mkdir()
        configure(missing_project, "required", project=None)
        notice = run(missing_project, available, missing_project)
        assert "no project was explicitly selected" in notice
        assert "classification=ambiguous" in notice

        malformed_disabled = base / "malformed-disabled"
        malformed_disabled.mkdir()
        (malformed_disabled / ".context-library").mkdir()
        (malformed_disabled / ".context-library/config.json").write_text("{invalid", encoding="utf-8")
        assert (
            run(
                malformed_disabled,
                available,
                malformed_disabled,
                environment_requirement="disabled",
            )
            == ""
        )

        invalid_required = base / "invalid-required"
        invalid_required.mkdir()
        configure(invalid_required, "required")
        config = invalid_required / ".context-library/config.json"
        payload = json.loads(config.read_text(encoding="utf-8"))
        payload["schema_version"] = 99
        config.write_text(json.dumps(payload), encoding="utf-8")
        notice = run(invalid_required, available, invalid_required)
        assert "classification=invalid" in notice
        assert "requirement_source=" in notice

        for requirement in (None, "required", "optional", "disabled"):
            inaccessible = base / f"inaccessible-{requirement or 'undetermined'}"
            inaccessible.mkdir()
            if requirement is not None:
                configure(inaccessible, requirement)
            stdout, stderr = blocked(inaccessible, unavailable, inaccessible)
            result = json.loads(stdout)
            assert result["status"] == "blocked"
            assert result["disposition"] == "stop"
            assert result["runtime_condition"] == "missing_root"
            assert result["recovery"] == ["fix_configuration", "disable", "uninstall"]
            assert "stopped session-start work" in stderr
            assert not (inaccessible / ".context-library/projection.json").exists()

    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        library = base / "library"
        shutil.copytree(FIXTURE_LIBRARY, library)
        shutil.rmtree(library / "projects/conflict")

        for requirement in ("required", "optional"):
            root = base / requirement
            root.mkdir()
            configure(root, requirement)
            (root / "AGENTS.md").write_text("# Human\n", encoding="utf-8")
            first = run(root, library, root)
            assert "projection updated" in first
            assert (root / "AGENTS.md").read_text().count(MARKER) == 1
            agents_before = (root / "AGENTS.md").read_bytes()
            sidecar_before = (root / ".context-library/projection.json").read_bytes()
            second = run(root, library, root)
            assert "projection already current" in second
            assert (root / "AGENTS.md").read_bytes() == agents_before
            assert (root / ".context-library/projection.json").read_bytes() == sidecar_before

        git_repo = base / "git-repo"
        nested = git_repo / "src/package"
        nested.mkdir(parents=True)
        subprocess.run(["git", "init"], cwd=git_repo, check=True, capture_output=True)
        configure(git_repo, "optional")
        run(nested, library)
        assert (git_repo / ".context-library/projection.json").is_file()
        assert not (nested / "AGENTS.md").exists()

    print("context library activation hook policy matrix passed")


if __name__ == "__main__":
    main()
