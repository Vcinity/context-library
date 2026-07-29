from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from context_library_maintainer.cli import app
from context_library_maintainer.config import ConfigError, project_files, resolve_config, scaffold
from context_library_maintainer.models import Candidate, Observation, SourceEnvelope, safe_error
from context_library_maintainer.publish import PublicationError, _write_recovery, publish
from context_library_maintainer.reconcile import reconcile
from context_library_maintainer.service import (
    MaintainerApplicationService,
    MaintainerCancelledError,
    MaintainerContext,
    MaintainerTimeoutError,
)
from context_library_maintainer.state import MIGRATIONS, State


def source_payload(content: str = "Keep the product private.") -> dict:
    return {
        "schema_version": 1,
        "external_id": "S-1",
        "source_type": "ticket",
        "uri": "ticket://S-1",
        "title": "Direction",
        "retrieved_at": "2026-07-28T00:00:00Z",
        "content_format": "text",
        "content": content,
    }


def candidate_payload(observation_id: str) -> dict:
    return {
        "schema_version": 1,
        "project": "demo",
        "candidate_id": "private-product",
        "subject": "Private product",
        "category": "product",
        "decision": "Keep the product private.",
        "rationale": "Explicit directive.",
        "decisionmaker": {"identity": "owner@example.test", "display_name": "Owner"},
        "decision_at": "2026-07-28T00:00:00Z",
        "provenance": "explicit",
        "derivation": "direct",
        "source_observation_ids": [observation_id],
        "applicability": {
            "provenance": "explicit",
            "confidence": 1,
            "evidence_observation_ids": [observation_id],
            "reasoning": "Product-wide",
        },
    }


def ready_publication(tmp_path: Path) -> tuple[State, dict, Path]:
    library = tmp_path / "library"
    settings = resolve_config(library, "demo", tmp_path / "state", "owner@example.test")
    scaffold(settings)
    config_path = library / "projects/demo/maintainer.yaml"
    config = yaml.safe_load(config_path.read_text())
    config["policies"]["automatic_publication"] = True
    config_path.write_text(yaml.safe_dump(config, sort_keys=False))
    state = State(tmp_path / "state")
    source = SourceEnvelope.model_validate(source_payload())
    source_id, _ = state.add_source(source, "demo")
    observation = Observation.model_validate(
        {
            "source_id": source_id,
            "kind": "directive",
            "excerpt": source.content,
            "location": "body",
            "speaker": {"identity": "owner@example.test", "display_name": "Owner"},
            "occurred_at": "2026-07-28T00:00:00Z",
            "agent_interpretation": "Directive",
        }
    )
    state.add_observation(observation, "obs-private", "demo")
    state.add_candidate(Candidate.model_validate(candidate_payload("obs-private")))
    assert reconcile(state, settings)["ready"] == ["private-product"]
    return state, settings, library


def git_init(root: Path) -> str:
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            "baseline",
        ],
        check=True,
    )
    return subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_cross_project_evidence_is_rejected_and_candidate_failure_is_atomic(tmp_path):
    state_root = tmp_path / "state"
    service_a = MaintainerApplicationService(
        MaintainerContext(tmp_path / "library", state_root, "project-a", "human:a")
    )
    source_id = service_a.ingest_source(source_payload())["sources"][0]["source_id"]
    service_b = MaintainerApplicationService(
        MaintainerContext(tmp_path / "library", state_root, "project-b", "human:b")
    )
    with pytest.raises(ValueError, match="unknown source"):
        service_b.add_observation(
            {
                "source_id": source_id,
                "kind": "directive",
                "excerpt": "Keep the product private.",
                "location": "body",
                "agent_interpretation": "Cross-project",
            }
        )
    state = State(state_root)
    assert not state.db.execute("SELECT 1 FROM observations WHERE project='project-b'").fetchone()
    with pytest.raises(ValueError, match="outside its project"):
        service_b.add_candidate(candidate_payload("missing-observation") | {"project": "project-b"})
    assert not state.db.execute("SELECT 1 FROM candidates WHERE project='project-b'").fetchone()


def test_intermediate_symlink_escape_is_rejected(tmp_path):
    library = tmp_path / "library"
    outside = tmp_path / "outside"
    library.mkdir()
    outside.mkdir()
    os.symlink(outside, library / "projects")
    settings = resolve_config(library, "demo", tmp_path / "state", "actor")
    with pytest.raises(ConfigError, match="symlinked path component"):
        scaffold(settings)
    assert not (outside / "demo").exists()


def test_newer_state_schema_is_refused(tmp_path):
    root = tmp_path / "state"
    root.mkdir()
    db = sqlite3.connect(root / "state.sqlite3")
    db.execute("CREATE TABLE migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
    db.execute("INSERT INTO migrations VALUES (?, 'now')", (len(MIGRATIONS) + 1,))
    db.commit()
    db.close()
    with pytest.raises(RuntimeError, match="newer than supported"):
        State(root)


def test_publication_refuses_any_dirty_git_state_and_commits_clean_state(tmp_path):
    state, settings, library = ready_publication(tmp_path)
    baseline = git_init(library)
    (library / "untracked-user-file").write_text("mine")
    with pytest.raises(PublicationError, match="dirty"):
        publish(state, settings)
    assert state.candidates("demo", "ready")
    (library / "untracked-user-file").unlink()
    result = publish(state, settings)
    head = subprocess.run(
        ["git", "-C", str(library), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert result["published"] == ["private-product"]
    assert head != baseline
    assert not subprocess.run(
        ["git", "-C", str(library), "status", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def test_publication_rolls_back_commit_files_and_index_when_state_write_fails(tmp_path, monkeypatch):
    state, settings, library = ready_publication(tmp_path)
    baseline = git_init(library)
    original = (library / "projects/demo/decision-register.md").read_bytes()

    def fail_transition(*_args, **_kwargs):
        raise sqlite3.OperationalError("injected state failure")

    monkeypatch.setattr(state, "transition", fail_transition)
    with pytest.raises(PublicationError) as caught:
        publish(state, settings)
    assert not caught.value.unrestored
    assert (library / "projects/demo/decision-register.md").read_bytes() == original
    head = subprocess.run(
        ["git", "-C", str(library), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert head == baseline
    assert not subprocess.run(
        ["git", "-C", str(library), "status", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def test_next_publication_recovers_an_interrupted_file_replacement(tmp_path):
    state, settings, library = ready_publication(tmp_path)
    git_init(library)
    register = library / "projects/demo/decision-register.md"
    original = register.read_bytes()
    head = subprocess.run(
        ["git", "-C", str(library), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    partial = b"partial process-crash write"
    _write_recovery(
        state,
        settings,
        "demo",
        {register: original},
        head,
        replacements={register: partial},
    )
    register.write_bytes(partial)
    result = publish(state, settings)
    assert result["published"] == ["private-product"]
    assert "partial process-crash write" not in register.read_text()
    assert not (state.root / "demo.publication-recovery.json").exists()


def test_legacy_migration_is_dry_run_by_default_and_byte_preserving_when_authorized(tmp_path):
    library = tmp_path / "library"
    source = library / "decision-artifacts/decision-register.md"
    source.parent.mkdir(parents=True)
    content = (
        b'# Register\r\n\r\n<a id="legacy-one"></a>\r\n### Legacy\r\n'
        b"- Decision: Keep bytes.\r\n- Provenance: explicit\r\n"
    )
    source.write_bytes(content)
    settings = resolve_config(library, "legacy", tmp_path / "state", "admin")
    scaffold(settings)
    destination = library / "projects/legacy/decision-register.md"
    destination.unlink()
    runner = CliRunner()
    base = [
        "--library-root",
        str(library),
        "--state-root",
        str(tmp_path / "state"),
        "--project",
        "legacy",
        "migrate",
        "legacy-pack",
        "--publish",
    ]
    denied = runner.invoke(app, base)
    assert denied.exit_code == 2
    assert not destination.exists()
    allowed = runner.invoke(app, [*base, "--authorize-live-migration"])
    assert allowed.exit_code == 0, allowed.output
    assert destination.read_bytes() == content


def test_durable_error_redaction_removes_secret_values():
    message = safe_error("provider token=top-secret password=hunter2 authorization=Bearer-abc")
    assert "top-secret" not in message
    assert "hunter2" not in message
    assert "Bearer-abc" not in message
    assert message.count("[REDACTED]") == 3


def test_typed_service_honors_cancellation_and_timeout_boundaries(tmp_path):
    cancelled = MaintainerApplicationService(
        MaintainerContext(
            tmp_path / "library",
            tmp_path / "cancel-state",
            "demo",
            "actor",
            cancelled=lambda: True,
        )
    )
    with pytest.raises(MaintainerCancelledError):
        cancelled.ingest_source(source_payload())
    timed_out = MaintainerApplicationService(
        MaintainerContext(
            tmp_path / "library",
            tmp_path / "timeout-state",
            "demo",
            "actor",
            timeout_seconds=-1,
        )
    )
    with pytest.raises(MaintainerTimeoutError):
        timed_out.ingest_source(source_payload())


def test_project_files_rejects_symlinked_conflict_parent(tmp_path):
    settings = resolve_config(tmp_path / "library", "demo", tmp_path / "state", "actor")
    scaffold(settings)
    root, *_ = project_files(settings)
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "conflicts").rmdir()
    os.symlink(outside, root / "conflicts")
    state = State(tmp_path / "state")
    state.db.execute(
        "INSERT INTO conflicts VALUES(?,?,?,?,?,?)",
        ("conflict-demo", "demo", json.dumps({"conflict_id": "conflict-demo"}), "open", "now", "now"),
    )
    with pytest.raises(ConfigError, match="symlinked path component"):
        publish(state, settings, no_commit=True)


def test_cli_is_an_adapter_over_typed_service_without_direct_state_or_file_mutation():
    source = Path(__file__).parents[2].joinpath("src/context_library_maintainer/cli.py").read_text()
    assert "MaintainerApplicationService" in source
    for forbidden in (
        "State(",
        ".db.execute(",
        "scaffold(",
        "write_text(",
        "write_bytes(",
        "os.replace(",
    ):
        assert forbidden not in source


@pytest.mark.parametrize(
    "arguments",
    [
        ["query", "--q", "example", "--json"],
        ["query", "--library-root", "/tmp", "--q", "example", "--json"],
    ],
)
def test_cli_missing_explicit_configuration_is_one_typed_json_error(arguments, monkeypatch):
    for name in ("CONTEXT_LIBRARY_ROOT", "CLM_PROJECT", "CLM_JSON"):
        monkeypatch.delenv(name, raising=False)
    result = CliRunner().invoke(app, arguments)
    assert result.exit_code == 2
    assert result.exception is not None
    assert "Traceback" not in result.stdout
    lines = result.stdout.splitlines()
    assert len(lines) == 1
    envelope = json.loads(lines[0])
    assert envelope["command"] == "query"
    assert envelope["status"] == "error"
    assert envelope["errors"][0]["code"] == "configuration"
