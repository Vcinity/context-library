from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

import context_library_maintainer.publish as publication
from context_library_maintainer.config import resolve_config, scaffold
from context_library_maintainer.models import Candidate, Observation, SourceEnvelope
from context_library_maintainer.publish import (
    PublicationError,
    PublicationLockedError,
    PublicationRecoveryDivergedError,
    PublicationSafetyError,
    _write_recovery,
    publish,
)
from context_library_maintainer.reconcile import reconcile
from context_library_maintainer.state import State


def clean(root) -> bool:
    return not subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=all"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def ready_publication(tmp_path):
    library = tmp_path / "library"
    settings = resolve_config(library, "demo", tmp_path / "state", "owner@example.test")
    scaffold(settings)
    config_path = library / "projects/demo/maintainer.yaml"
    config = yaml.safe_load(config_path.read_text())
    config["policies"]["automatic_publication"] = True
    config_path.write_text(yaml.safe_dump(config, sort_keys=False))
    state = State(tmp_path / "state")
    source_id, _ = state.add_source(
        SourceEnvelope.model_validate(
            {
                "schema_version": 1,
                "external_id": "S-1",
                "source_type": "ticket",
                "uri": "ticket://S-1",
                "title": "Direction",
                "retrieved_at": "2026-07-28T00:00:00Z",
                "content_format": "text",
                "content": "Keep the product private.",
            }
        ),
        "demo",
    )
    state.add_observation(
        Observation.model_validate(
            {
                "source_id": source_id,
                "kind": "directive",
                "excerpt": "Keep the product private.",
                "location": "body",
                "speaker": {"identity": "owner@example.test", "display_name": "Owner"},
                "occurred_at": "2026-07-28T00:00:00Z",
                "agent_interpretation": "Directive",
            }
        ),
        "obs-private",
        "demo",
    )
    state.add_candidate(
        Candidate.model_validate(
            {
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
                "source_observation_ids": ["obs-private"],
                "applicability": {
                    "provenance": "explicit",
                    "confidence": 1,
                    "evidence_observation_ids": ["obs-private"],
                    "reasoning": "Product-wide",
                },
            }
        )
    )
    assert reconcile(state, settings)["ready"] == ["private-product"]
    return state, settings, library


def git_init(root):
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


def test_recovery_record_failure_is_typed_and_leaves_pack_and_state_unchanged(tmp_path, monkeypatch):
    state, settings, library = ready_publication(tmp_path)
    baseline = git_init(library)
    original = (library / "projects/demo/decision-register.md").read_bytes()

    def fail_recovery(*_args, **_kwargs):
        raise OSError("injected recovery write failure")

    monkeypatch.setattr(publication, "_write_recovery", fail_recovery)
    with pytest.raises(PublicationError, match="failed safely at recovery-record"):
        publish(state, settings)
    assert (library / "projects/demo/decision-register.md").read_bytes() == original
    assert state.candidates("demo", "ready")
    assert (
        subprocess.run(
            ["git", "-C", str(library), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        == baseline
    )
    assert clean(library)


def test_state_failure_rolls_back_all_candidate_transitions(tmp_path, monkeypatch):
    state, settings, library = ready_publication(tmp_path)
    first = Candidate.model_validate_json(state.candidates("demo", "ready")[0]["payload_json"])
    state.add_candidate(
        first.model_copy(
            update={
                "candidate_id": "private-product-second",
                "subject": "Second private direction",
            }
        )
    )
    assert reconcile(state, settings, "private-product-second")["ready"] == ["private-product-second"]
    git_init(library)
    original_transition = state.transition
    calls = 0

    def fail_second_transition(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected second transition failure")
        return original_transition(*args, **kwargs)

    monkeypatch.setattr(state, "transition", fail_second_transition)
    with pytest.raises(PublicationError):
        publish(state, settings)
    assert {row["id"] for row in state.candidates("demo", "ready")} == {
        "private-product",
        "private-product-second",
    }
    assert not state.candidates("demo", "published")
    assert clean(library)


def test_publication_rejects_staged_register_incompatible_with_plugin_projection(tmp_path):
    state, settings, library = ready_publication(tmp_path)
    register = library / "projects/demo/decision-register.md"
    cycle = (
        '# Decision Register\n\n<a id="cycle-a"></a>\n### A\n'
        "- Decision: A.\n- Provenance: explicit\n- Supersedes: `cycle-b`\n\n"
        '<a id="cycle-b"></a>\n### B\n'
        "- Decision: B.\n- Provenance: explicit\n- Supersedes: `cycle-a`\n\n"
    )
    register.write_text(cycle)
    with pytest.raises(PublicationSafetyError, match="incompatible"):
        publish(state, settings)
    assert register.read_text() == cycle
    assert state.candidates("demo", "ready")


def test_restart_recovery_refuses_a_human_edit_to_a_publication_target(tmp_path):
    state, settings, library = ready_publication(tmp_path)
    head = git_init(library)
    register = library / "projects/demo/decision-register.md"
    original = register.read_bytes()
    staged = b"# Decision Register\n\nstaged publication\n"
    _write_recovery(
        state,
        settings,
        "demo",
        {register: original},
        head,
        replacements={register: staged},
    )
    human_edit = b"# Decision Register\n\nhuman recovery edit\n"
    register.write_bytes(human_edit)

    with pytest.raises(PublicationRecoveryDivergedError) as caught:
        publish(state, settings)

    assert caught.value.stage == "recovery-validation"
    assert caught.value.unrestored == ["projects/demo/decision-register.md"]
    assert register.read_bytes() == human_edit
    assert (state.root / "demo.publication-recovery.json").exists()


def test_restart_recovery_refuses_a_post_crash_commit(tmp_path):
    state, settings, library = ready_publication(tmp_path)
    head = git_init(library)
    register = library / "projects/demo/decision-register.md"
    original = register.read_bytes()
    staged = b"# Decision Register\n\nstaged publication\n"
    _write_recovery(
        state,
        settings,
        "demo",
        {register: original},
        head,
        replacements={register: staged},
    )
    register.write_bytes(staged)
    subprocess.run(["git", "-C", str(library), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(library),
            "-c",
            "user.name=Human",
            "-c",
            "user.email=human@example.invalid",
            "commit",
            "-qm",
            "post-crash work",
        ],
        check=True,
    )
    post_crash_head = subprocess.run(
        ["git", "-C", str(library), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    with pytest.raises(PublicationRecoveryDivergedError) as caught:
        publish(state, settings)

    assert caught.value.unrestored == [".git/HEAD"]
    assert (
        subprocess.run(
            ["git", "-C", str(library), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        == post_crash_head
    )
    assert register.read_bytes() == staged


def test_restart_reconciles_exact_known_maintainer_commit_without_duplicate(tmp_path, monkeypatch):
    state, settings, library = ready_publication(tmp_path)
    baseline = git_init(library)
    original_transaction = state.transaction
    calls = 0

    def crash_before_state_transaction():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise SystemExit("simulated process crash after commit")
        return original_transaction()

    monkeypatch.setattr(state, "transaction", crash_before_state_transaction)
    with pytest.raises(SystemExit, match="simulated process crash"):
        publish(state, settings)

    committed = subprocess.run(
        ["git", "-C", str(library), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert committed != baseline
    assert clean(library)
    assert state.candidates("demo", "ready")
    recovery = state.root / "demo.publication-recovery.json"
    assert recovery.exists()
    state.db.close()

    restarted = State(tmp_path / "state")
    result = publish(restarted, settings)

    assert result["recovered"] is True
    assert result["published"] == ["private-product"]
    assert not restarted.candidates("demo", "ready")
    assert [row["id"] for row in restarted.candidates("demo", "published")] == ["private-product"]
    assert (
        restarted.db.execute("SELECT COUNT(*) FROM publications WHERE project='demo' AND phase='published'").fetchone()[
            0
        ]
        == 1
    )
    assert (library / "projects/demo/decision-register.md").read_text().count('<a id="private-product"></a>') == 1
    assert (
        subprocess.run(
            ["git", "-C", str(library), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        == committed
    )
    assert not recovery.exists()
    assert clean(library)


def test_concurrent_publisher_is_typed_nonmutating_and_retry_succeeds(tmp_path):
    first, settings, library = ready_publication(tmp_path)
    baseline = git_init(library)
    original = snapshot_targets(library)
    second = State(tmp_path / "state")

    with first.project_lock("demo", "publisher-one"):
        with pytest.raises(PublicationLockedError, match="publication lock"):
            publish(second, {**settings, "actor": "publisher-two"})
        assert snapshot_targets(library) == original
        assert [row["id"] for row in second.candidates("demo", "ready")] == ["private-product"]
        assert not second.candidates("demo", "published")
        assert (
            subprocess.run(
                ["git", "-C", str(library), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            == baseline
        )
        assert not (second.root / "demo.publication-recovery.json").exists()

    result = publish(second, {**settings, "actor": "publisher-two"})
    assert result["published"] == ["private-product"]
    assert not second.candidates("demo", "ready")
    assert [row["id"] for row in second.candidates("demo", "published")] == ["private-product"]
    assert clean(library)


def test_restart_recovery_refuses_unrelated_dirty_paths(tmp_path):
    state, settings, library = ready_publication(tmp_path)
    head = git_init(library)
    register = library / "projects/demo/decision-register.md"
    original = register.read_bytes()
    staged = b"# Decision Register\n\nstaged publication\n"
    _write_recovery(
        state,
        settings,
        "demo",
        {register: original},
        head,
        replacements={register: staged},
    )
    register.write_bytes(staged)
    unrelated = library / "projects/demo/maintainer.yaml"
    unrelated.write_text(unrelated.read_text() + "\n# human edit\n")

    with pytest.raises(PublicationRecoveryDivergedError) as caught:
        publish(state, settings)

    assert caught.value.unrestored == ["projects/demo/maintainer.yaml"]
    assert "# human edit" in unrelated.read_text()
    assert register.read_bytes() == staged


def test_rollback_failure_reports_stage_and_named_unrestored_path(tmp_path, monkeypatch):
    state, settings, library = ready_publication(tmp_path)
    git_init(library)
    original_replace = publication.os.replace

    def fail_state(*args, **kwargs):
        raise OSError("injected state failure")

    def fail_register_rollback(source, destination):
        if str(source).endswith(".decision-register.md.clm-rollback"):
            raise OSError("injected rollback failure")
        return original_replace(source, destination)

    monkeypatch.setattr(state, "transition", fail_state)
    monkeypatch.setattr(publication.os, "replace", fail_register_rollback)

    with pytest.raises(PublicationError) as caught:
        publish(state, settings)

    assert caught.value.stage == "state-transaction"
    assert caught.value.unrestored == ["projects/demo/decision-register.md"]
    assert "state-transaction" in str(caught.value)
    assert (state.root / "demo.publication-recovery.json").exists()


PUBLICATION_TARGETS = [
    "decision-register.md",
    "index-by-category.md",
    "index-by-date.md",
    "index-by-layer.md",
    "supersession-index.md",
]


def snapshot_targets(library: Path) -> dict[str, bytes | None]:
    root = library / "projects/demo"
    return {name: (root / name).read_bytes() if (root / name).exists() else None for name in PUBLICATION_TARGETS}


def assert_last_known_good(state, library: Path, baseline: str, before: dict[str, bytes | None]) -> None:
    root = library / "projects/demo"
    assert {
        name: (root / name).read_bytes() if (root / name).exists() else None for name in PUBLICATION_TARGETS
    } == before
    assert [row["id"] for row in state.candidates("demo", "ready")] == ["private-product"]
    assert not state.candidates("demo", "published")
    assert (
        subprocess.run(
            ["git", "-C", str(library), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        == baseline
    )
    assert clean(library)


@pytest.mark.parametrize("target_name", PUBLICATION_TARGETS)
def test_each_publication_replacement_failure_restores_last_known_good(tmp_path, monkeypatch, target_name):
    state, settings, library = ready_publication(tmp_path)
    baseline = git_init(library)
    before = snapshot_targets(library)
    original_replace = publication.os.replace

    def fail_selected_replacement(source, destination):
        source_path = Path(source)
        destination_path = Path(destination)
        if source_path.name == f".{target_name}.clm-tmp" and destination_path.name == target_name:
            raise OSError(f"injected replacement failure for {target_name}")
        return original_replace(source, destination)

    monkeypatch.setattr(publication.os, "replace", fail_selected_replacement)
    with pytest.raises(PublicationError) as caught:
        publish(state, settings)

    assert caught.value.stage == f"replace:projects/demo/{target_name}"
    assert not caught.value.unrestored
    assert_last_known_good(state, library, baseline, before)
    assert not (state.root / "demo.publication-recovery.json").exists()


@pytest.mark.parametrize("stage", ["git-add", "git-commit", "recovery-cleanup"])
def test_each_nonreplacement_publication_stage_failure_restores_last_known_good(tmp_path, monkeypatch, stage):
    state, settings, library = ready_publication(tmp_path)
    baseline = git_init(library)
    before = snapshot_targets(library)

    if stage == "git-add":
        original_git = publication._git

        def fail_git_add(args, root, **kwargs):
            if args[0] == "add":
                raise OSError("injected git-add failure")
            return original_git(args, root, **kwargs)

        monkeypatch.setattr(publication, "_git", fail_git_add)
    elif stage == "git-commit":
        original_run = publication.subprocess.run

        def fail_git_commit(command, *args, **kwargs):
            if command[:2] == ["git", "commit-tree"]:
                raise subprocess.CalledProcessError(1, command)
            return original_run(command, *args, **kwargs)

        monkeypatch.setattr(publication.subprocess, "run", fail_git_commit)
    else:
        original_remove = publication._remove_recovery
        calls = 0

        def fail_first_cleanup(path):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError("injected recovery cleanup failure")
            return original_remove(path)

        monkeypatch.setattr(publication, "_remove_recovery", fail_first_cleanup)

    with pytest.raises(PublicationError) as caught:
        publish(state, settings)

    assert caught.value.stage == stage
    assert not caught.value.unrestored
    assert_last_known_good(state, library, baseline, before)
    assert not (state.root / "demo.publication-recovery.json").exists()


@pytest.mark.parametrize("target_name", PUBLICATION_TARGETS)
def test_each_named_file_rollback_failure_is_reported_without_laundering_state(tmp_path, monkeypatch, target_name):
    state, settings, library = ready_publication(tmp_path)
    baseline = git_init(library)
    before = snapshot_targets(library)
    original_replace = publication.os.replace

    def fail_state(*_args, **_kwargs):
        raise OSError("injected state failure")

    def fail_selected_rollback(source, destination):
        if Path(source).name == f".{target_name}.clm-rollback":
            raise OSError(f"injected rollback failure for {target_name}")
        return original_replace(source, destination)

    monkeypatch.setattr(state, "transition", fail_state)
    monkeypatch.setattr(publication.os, "replace", fail_selected_rollback)
    with pytest.raises(PublicationError) as caught:
        publish(state, settings)

    relative = f"projects/demo/{target_name}"
    assert caught.value.stage == "state-transaction"
    assert caught.value.unrestored == [relative]
    assert relative not in caught.value.restored
    assert [row["id"] for row in state.candidates("demo", "ready")] == ["private-product"]
    assert not state.candidates("demo", "published")
    assert (state.root / "demo.publication-recovery.json").exists()
    assert (
        subprocess.run(
            ["git", "-C", str(library), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        == baseline
    )
    for name, content in before.items():
        path = library / "projects/demo" / name
        actual = path.read_bytes() if path.exists() else None
        if name != target_name:
            assert actual == content


def test_git_rollback_failure_names_head_and_index_and_preserves_maintainer_state(tmp_path, monkeypatch):
    state, settings, library = ready_publication(tmp_path)
    git_init(library)
    original_git = publication._git

    def fail_reset(args, root, **kwargs):
        if args[:2] == ["reset", "--mixed"]:
            raise OSError("injected git rollback failure")
        return original_git(args, root, **kwargs)

    def fail_state(*_args, **_kwargs):
        raise OSError("injected state failure")

    monkeypatch.setattr(publication, "_git", fail_reset)
    monkeypatch.setattr(state, "transition", fail_state)
    with pytest.raises(PublicationError) as caught:
        publish(state, settings)

    assert caught.value.stage == "state-transaction"
    assert caught.value.unrestored[:2] == [".git/HEAD", ".git/index"]
    assert [row["id"] for row in state.candidates("demo", "ready")] == ["private-product"]
    assert not state.candidates("demo", "published")
    assert (state.root / "demo.publication-recovery.json").exists()
