#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
PLUGIN = ROOT / "plugins" / "context-library"
FIXTURE_LIBRARY = ROOT / "scripts" / "plugin" / "fixtures" / "projection" / "library"
sys.path.insert(0, str(PLUGIN))

import projection  # noqa: E402


@contextmanager
def environment(**values: str | None):
    prior = {key: os.environ.get(key) for key in values}
    try:
        for key, value in values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        yield
    finally:
        for key, value in prior.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def snapshot(root: Path) -> dict[str, tuple[bytes, int]]:
    return {
        str(path.relative_to(root)): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


class Workspace:
    def __init__(self, include_conflict: bool = False):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "workspace"
        self.library = Path(self.temporary.name) / "library"
        self.root.mkdir()
        shutil.copytree(FIXTURE_LIBRARY, self.library)
        if not include_conflict:
            shutil.rmtree(self.library / "projects" / "conflict")
        (self.root / ".context-library").mkdir()
        self.configure()

    def configure(self, project: str = "demo", layers: dict[str, str] | None = None) -> None:
        payload = {
            "schema": "context-library/context-policy",
            "schema_version": 1,
            "project": project,
            "context_requirement": "optional",
            "affected_layers": layers if layers is not None else {"ui": "components/ui"},
        }
        (self.root / ".context-library" / "config.json").write_text(
            json.dumps(payload, indent=2) + "\n", encoding="utf-8"
        )

    def close(self) -> None:
        self.temporary.cleanup()


class ProjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = Workspace()
        self.env = environment(
            CONTEXT_LIBRARY_ROOT=str(self.workspace.library),
            CONTEXT_LIBRARY_PROJECT=None,
            CONTEXT_LIBRARY_PROJECT_ROOT=None,
            CONTEXT_LIBRARY_CONTEXT_REQUIREMENT=None,
        )
        self.env.__enter__()

    def tearDown(self) -> None:
        self.env.__exit__(None, None, None)
        self.workspace.close()

    def test_sync_is_deterministic_idempotent_and_preserves_human_bytes(self) -> None:
        prefix = b"# Human heading\n\nExact spacing:  yes\n"
        suffix = b"\nHuman tail without normalization\n"
        agents = self.workspace.root / "AGENTS.md"
        agents.write_bytes(prefix + suffix)

        self.assertTrue(projection.sync(self.workspace.root))
        first = snapshot(self.workspace.root)
        content = agents.read_bytes()
        self.assertTrue(content.startswith(prefix + suffix))
        self.assertFalse(projection.sync(self.workspace.root))
        self.assertEqual(first, snapshot(self.workspace.root))

    def test_sync_preserves_crlf_and_lone_cr_human_bytes(self) -> None:
        for prefix, suffix in (
            (b"# Human heading\r\n\r\nline one\r\n", b"\r\nHuman tail\r\n"),
            (b"# Human heading\r\rline one\r", b"\rHuman tail\r"),
        ):
            with self.subTest(prefix=prefix):
                agents = self.workspace.root / "AGENTS.md"
                agents.write_bytes(prefix + suffix)
                projection.sync(self.workspace.root)
                content = agents.read_bytes()
                self.assertTrue(content.startswith(prefix + suffix))
                (self.workspace.root / projection.SIDECAR_PATH).unlink()
                shutil.rmtree(self.workspace.root / "components")
                agents.unlink()

    def test_sole_legacy_flat_pack_accepts_an_explicit_historical_alias(self) -> None:
        register = self.workspace.library / "projects" / "demo" / "decision-register.md"
        content = register.read_bytes()
        shutil.rmtree(self.workspace.library / "projects")
        legacy = self.workspace.library / "decision-artifacts"
        legacy.mkdir()
        (legacy / "decision-register.md").write_bytes(content)
        self.workspace.configure("previous-project")

        self.assertTrue(projection.sync(self.workspace.root))
        sidecar = json.loads((self.workspace.root / projection.SIDECAR_PATH).read_text(encoding="utf-8"))
        self.assertEqual(sidecar["project"], "previous-project")

    def test_marker_examples_inside_fenced_code_remain_human_content(self) -> None:
        example = b"# Human docs\n\n```markdown\n" + projection._LEGACY_GENERIC_BLOCK.encode() + b"```\n"
        agents = self.workspace.root / "AGENTS.md"
        agents.write_bytes(example)
        projection.sync(self.workspace.root)
        content = agents.read_bytes()
        self.assertTrue(content.startswith(example))
        self.assertEqual(content.count(projection.MARKER_START_PREFIX.encode()), 2)
        first = snapshot(self.workspace.root)
        self.assertFalse(projection.sync(self.workspace.root))
        self.assertEqual(first, snapshot(self.workspace.root))

    def test_explicit_only_sidecar_weakest_provenance_supersession_and_scopes(self) -> None:
        projection.sync(self.workspace.root)
        root_text = (self.workspace.root / "AGENTS.md").read_text(encoding="utf-8")
        nested_text = (self.workspace.root / "components" / "ui" / "AGENTS.md").read_text(encoding="utf-8")
        sidecar = json.loads((self.workspace.root / projection.SIDECAR_PATH).read_text(encoding="utf-8"))

        self.assertIn("[current-root]", root_text)
        self.assertNotIn("former root", root_text)
        self.assertNotIn("inferred-api", root_text)
        self.assertNotIn("assumed-cache", root_text)
        self.assertNotIn("mixed-synthesis", root_text)
        self.assertNotIn("nested-synthesis", root_text)
        self.assertIn("[ui-scope]", nested_text)
        self.assertNotIn("storage", root_text + nested_text)

        excluded = {item["record_id"]: item for item in sidecar["excluded_context"]}
        self.assertEqual(excluded["inferred-api"]["source_provenance"], "inferred")
        self.assertEqual(excluded["assumed-cache"]["source_provenance"], "assumed")
        self.assertEqual(excluded["mixed-synthesis"]["source_provenance"], "inferred")
        self.assertEqual(excluded["mixed-synthesis"]["source_ids"], ["current-root", "inferred-api"])
        self.assertEqual(excluded["nested-synthesis"]["source_provenance"], "inferred")
        self.assertEqual(
            excluded["nested-synthesis"]["source_ids"],
            ["current-root", "inferred-api", "mixed-synthesis"],
        )
        self.assertEqual(excluded["old-root"]["reason"], "superseded")
        self.assertEqual(excluded["unmapped-scope"]["reason"], "unmapped-affected-layer")
        self.assertEqual({item["scope"] for item in sidecar["constraints"]}, {".", "components/ui"})

    def test_check_is_strictly_non_mutating(self) -> None:
        projection.sync(self.workspace.root)
        before = snapshot(self.workspace.root)
        projection.check(self.workspace.root)
        self.assertEqual(before, snapshot(self.workspace.root))

    def test_check_detects_stale_missing_malformed_and_locally_edited(self) -> None:
        cases = ("stale", "missing", "missing_block", "malformed", "malformed_block", "edited")
        for case in cases:
            with self.subTest(case=case):
                workspace = Workspace()
                try:
                    with environment(CONTEXT_LIBRARY_ROOT=str(workspace.library), CONTEXT_LIBRARY_PROJECT=None):
                        projection.sync(workspace.root)
                        if case == "stale":
                            register = workspace.library / "projects" / "demo" / "decision-register.md"
                            register.write_text(register.read_text(encoding="utf-8") + "\n", encoding="utf-8")
                        elif case == "missing":
                            (workspace.root / projection.SIDECAR_PATH).unlink()
                        elif case == "missing_block":
                            (workspace.root / "AGENTS.md").unlink()
                        elif case == "malformed":
                            (workspace.root / projection.SIDECAR_PATH).write_text("{}\n", encoding="utf-8")
                        elif case == "malformed_block":
                            agents = workspace.root / "AGENTS.md"
                            agents.write_text(
                                agents.read_text(encoding="utf-8").replace(projection.MARKER_END, ""), encoding="utf-8"
                            )
                        else:
                            agents = workspace.root / "AGENTS.md"
                            agents.write_text(
                                agents.read_text(encoding="utf-8").replace(
                                    "current root convention", "edited convention"
                                ),
                                encoding="utf-8",
                            )
                        before = snapshot(workspace.root)
                        with self.assertRaises(projection.CheckError):
                            projection.check(workspace.root)
                        self.assertEqual(before, snapshot(workspace.root))
                finally:
                    workspace.close()

    def test_sync_refuses_local_edits_without_mutation(self) -> None:
        projection.sync(self.workspace.root)
        agents = self.workspace.root / "AGENTS.md"
        agents.write_text(
            agents.read_text(encoding="utf-8").replace("current root convention", "local edit"), encoding="utf-8"
        )
        before = snapshot(self.workspace.root)
        with self.assertRaisesRegex(projection.ProjectionError, "locally modified"):
            projection.sync(self.workspace.root)
        self.assertEqual(before, snapshot(self.workspace.root))

    def test_sync_recreates_deleted_generated_file_and_removes_generated_only_orphan(self) -> None:
        projection.sync(self.workspace.root)
        nested = self.workspace.root / "components" / "ui" / "AGENTS.md"
        nested.unlink()
        self.assertTrue(projection.sync(self.workspace.root))
        self.assertIn("[ui-scope]", nested.read_text(encoding="utf-8"))

        self.workspace.configure(layers={})
        self.assertTrue(projection.sync(self.workspace.root))
        self.assertFalse(nested.exists())
        projection.check(self.workspace.root)

    def test_sync_refuses_untracked_legacy_generic_root_block(self) -> None:
        register = self.workspace.library / "projects" / "demo" / "decision-register.md"
        register.write_text(
            '# Register\n\n<a id="nested"></a>\n### Nested\n'
            "- Decision: Keep this nested.\n- Provenance: explicit\n- Affected-Layers: ui\n",
            encoding="utf-8",
        )
        root_agents = self.workspace.root / "AGENTS.md"
        root_agents.write_text(projection._LEGACY_GENERIC_BLOCK, encoding="utf-8")
        before = snapshot(self.workspace.root)
        with self.assertRaisesRegex(projection.ProjectionError, "unmanaged"):
            projection.sync(self.workspace.root)
        self.assertEqual(before, snapshot(self.workspace.root))

    def test_ambiguous_selection_and_conflicting_decisions_fail_safely(self) -> None:
        workspace = Workspace(include_conflict=True)
        try:
            config = workspace.root / ".context-library" / "config.json"
            config.unlink()
            with environment(
                CONTEXT_LIBRARY_ROOT=str(workspace.library),
                CONTEXT_LIBRARY_PROJECT=None,
                CONTEXT_LIBRARY_CONTEXT_REQUIREMENT="optional",
            ):
                before = snapshot(workspace.root)
                with self.assertRaisesRegex(projection.ProjectionError, "ambiguous"):
                    projection.sync(workspace.root)
                self.assertEqual(before, snapshot(workspace.root))
                workspace.configure(project="conflict", layers={})
                before = snapshot(workspace.root)
                with self.assertRaisesRegex(projection.ProjectionError, "conflicting active"):
                    projection.sync(workspace.root)
                self.assertEqual(before, snapshot(workspace.root))
        finally:
            workspace.close()

    def test_unavailable_and_malformed_sources_fail_without_mutation(self) -> None:
        before = snapshot(self.workspace.root)
        with environment(CONTEXT_LIBRARY_ROOT=str(self.workspace.library / "missing")):
            with self.assertRaisesRegex(projection.ProjectionError, "unavailable"):
                projection.sync(self.workspace.root)
        self.assertEqual(before, snapshot(self.workspace.root))

        register = self.workspace.library / "projects" / "demo" / "decision-register.md"
        register.write_text("# no anchored decisions\n", encoding="utf-8")
        with self.assertRaisesRegex(projection.ProjectionError, "no anchored decisions"):
            projection.sync(self.workspace.root)
        self.assertEqual(before, snapshot(self.workspace.root))

    def test_anchorless_decision_content_is_rejected(self) -> None:
        anchored = '<a id="first"></a>\n### First\n- Decision: First constraint.\n- Provenance: explicit\n'
        cases = (
            "### Orphan\n- Decision: Dangerous.\n- Provenance: explicit\n\n" + anchored,
            anchored + "\n### Orphan\n- Decision: Misattributed.\n",
        )
        for text in cases:
            with self.subTest(text=text):
                with self.assertRaisesRegex(projection.ProjectionError, "anchor|unanchored"):
                    projection.parse_decisions(text)

    def test_config_rejects_non_normalized_escaping_duplicate_and_symlink_scopes(self) -> None:
        invalid = ("./components/ui", "components//ui", "components/ui/", "../outside", "/outside")
        for scope in invalid:
            with self.subTest(scope=scope):
                self.workspace.configure(layers={"ui": scope})
                with self.assertRaises(projection.ProjectionError):
                    projection.sync(self.workspace.root)

        self.workspace.configure(layers={"ui": "components/ui", "storage": "components//ui"})
        with self.assertRaises(projection.ProjectionError):
            projection.sync(self.workspace.root)

        outside = Path(self.workspace.temporary.name) / "outside"
        outside.mkdir()
        (self.workspace.root / "linked").symlink_to(outside, target_is_directory=True)
        self.workspace.configure(layers={"ui": "linked"})
        with self.assertRaisesRegex(projection.ProjectionError, "outside|symbolic"):
            projection.sync(self.workspace.root)

    def test_stale_sidecar_scope_cannot_write_through_symlink(self) -> None:
        projection.sync(self.workspace.root)
        nested_dir = self.workspace.root / "components" / "ui"
        nested_bytes = (nested_dir / "AGENTS.md").read_bytes()
        shutil.rmtree(nested_dir)
        outside = Path(self.workspace.temporary.name) / "outside"
        outside.mkdir()
        outside_agents = outside / "AGENTS.md"
        outside_agents.write_bytes(nested_bytes)
        nested_dir.symlink_to(outside, target_is_directory=True)
        self.workspace.configure(layers={})

        with self.assertRaisesRegex(projection.ProjectionError, "outside|symbolic"):
            projection.sync(self.workspace.root)
        self.assertEqual(outside_agents.read_bytes(), nested_bytes)

    def test_unrelated_library_commit_does_not_stale_projection(self) -> None:
        subprocess.run(["git", "init"], cwd=self.workspace.library, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=self.workspace.library, check=True)
        subprocess.run(["git", "config", "user.name", "Projection Test"], cwd=self.workspace.library, check=True)
        subprocess.run(["git", "add", "."], cwd=self.workspace.library, check=True)
        subprocess.run(["git", "commit", "-m", "fixture"], cwd=self.workspace.library, check=True, capture_output=True)
        projection.sync(self.workspace.root)
        before = snapshot(self.workspace.root)

        (self.workspace.library / "noise.txt").write_text("unrelated\n", encoding="utf-8")
        subprocess.run(["git", "add", "noise.txt"], cwd=self.workspace.library, check=True)
        subprocess.run(["git", "commit", "-m", "noise"], cwd=self.workspace.library, check=True, capture_output=True)
        projection.check(self.workspace.root)
        self.assertFalse(projection.sync(self.workspace.root))
        self.assertEqual(before, snapshot(self.workspace.root))

    def test_write_failures_roll_back_every_completed_stage(self) -> None:
        original_atomic_write = projection._atomic_write
        for failure_call in (1, 2, 3):
            with self.subTest(failure_call=failure_call):
                workspace = Workspace()
                try:
                    with environment(CONTEXT_LIBRARY_ROOT=str(workspace.library)):
                        before = snapshot(workspace.root)
                        calls = 0

                        def fail_stage(root: Path, path: Path, data: bytes) -> None:
                            nonlocal calls
                            calls += 1
                            if calls == failure_call:
                                raise OSError(f"injected write failure {failure_call}")
                            original_atomic_write(root, path, data)

                        with mock.patch.object(projection, "_atomic_write", side_effect=fail_stage):
                            with self.assertRaisesRegex(OSError, "injected write failure"):
                                projection.sync(workspace.root)
                        self.assertEqual(before, snapshot(workspace.root))
                finally:
                    workspace.close()

    def test_rollback_failure_reports_updated_restored_and_unrestored_paths(self) -> None:
        original_atomic_write = projection._atomic_write
        calls = 0
        (self.workspace.root / "AGENTS.md").write_text("# Human\n", encoding="utf-8")

        def fail_write_and_rollback(root: Path, path: Path, data: bytes) -> None:
            nonlocal calls
            calls += 1
            if calls in (2, 3):
                raise OSError(f"injected failure {calls}")
            original_atomic_write(root, path, data)

        with mock.patch.object(projection, "_atomic_write", side_effect=fail_write_and_rollback):
            with self.assertRaises(projection.ProjectionError) as caught:
                projection.sync(self.workspace.root)
        message = str(caught.exception)
        self.assertIn("updated paths:", message)
        self.assertIn("restored paths:", message)
        self.assertIn("unrestored paths:", message)

    def test_non_utf8_agents_files_fail_cleanly_for_library_cli_and_hook(self) -> None:
        agents = self.workspace.root / "AGENTS.md"
        projection.sync(self.workspace.root)
        agents.write_bytes(b"# Human\n\xff\xfe\n")
        with self.assertRaisesRegex(projection.ProjectionError, "UTF-8"):
            projection.sync(self.workspace.root)
        with self.assertRaisesRegex(projection.CheckError, "UTF-8"):
            projection.check(self.workspace.root)

        env = os.environ.copy()
        command = [sys.executable, str(PLUGIN / "projection.py"), "sync", "--root", str(self.workspace.root)]
        cli = subprocess.run(command, env=env, capture_output=True, text=True)
        self.assertEqual(cli.returncode, projection.EXIT_ERROR)
        self.assertNotIn("Traceback", cli.stderr)

        hook = subprocess.run(
            [sys.executable, str(PLUGIN / "hooks" / "session_start.py")],
            cwd=self.workspace.root,
            env=env | {"CONTEXT_LIBRARY_PROJECT_ROOT": str(self.workspace.root)},
            capture_output=True,
            text=True,
        )
        self.assertEqual(hook.returncode, 0)
        self.assertNotIn("Traceback", hook.stdout + hook.stderr)
        self.assertEqual(hook.stdout, "")

    def test_environment_project_override_and_stable_cli_exit_codes(self) -> None:
        self.workspace.configure(project="unavailable")
        with environment(CONTEXT_LIBRARY_PROJECT="demo"):
            projection.sync(self.workspace.root)
        command = [sys.executable, str(PLUGIN / "projection.py"), "check", "--root", str(self.workspace.root)]
        env = os.environ.copy()
        env["CONTEXT_LIBRARY_PROJECT"] = "demo"
        current = subprocess.run(command, env=env, capture_output=True, text=True)
        self.assertEqual(current.returncode, projection.EXIT_OK, current.stderr)
        (self.workspace.root / "AGENTS.md").write_text("locally replaced\n", encoding="utf-8")
        failed = subprocess.run(command, env=env, capture_output=True, text=True)
        self.assertEqual(failed.returncode, projection.EXIT_CHECK_FAILED, failed.stderr)

    def test_policy_contract_rejects_unknown_family_version_and_fields(self) -> None:
        config = self.workspace.root / projection.CONFIG_PATH
        cases = (
            {
                "schema": "wrong/context-policy",
                "schema_version": 1,
                "project": "demo",
                "context_requirement": "required",
                "affected_layers": {},
            },
            {
                "schema": "context-library/context-policy",
                "schema_version": 99,
                "project": "demo",
                "context_requirement": "required",
                "affected_layers": {},
            },
            {
                "schema": "context-library/context-policy",
                "schema_version": 1,
                "project": "demo",
                "context_requirement": "required",
                "affected_layers": {},
                "unexpected": True,
            },
        )
        for payload in cases:
            with self.subTest(payload=payload):
                config.write_text(json.dumps(payload), encoding="utf-8")
                with self.assertRaisesRegex(projection.ProjectionError, "schema|unknown"):
                    projection.resolve_context_policy(self.workspace.root)

    def test_disabled_policy_blocks_direct_projection_without_mutation(self) -> None:
        self.workspace.configure()
        config = self.workspace.root / projection.CONFIG_PATH
        payload = json.loads(config.read_text(encoding="utf-8"))
        payload.update(
            {
                "schema": "context-library/context-policy",
                "schema_version": 1,
                "context_requirement": "disabled",
            }
        )
        config.write_text(json.dumps(payload), encoding="utf-8")
        before = snapshot(self.workspace.root)
        with self.assertRaisesRegex(projection.ProjectionError, "disabled"):
            projection.sync(self.workspace.root)
        self.assertEqual(before, snapshot(self.workspace.root))

    def test_undetermined_policy_blocks_direct_projection_without_mutation(self) -> None:
        config = self.workspace.root / projection.CONFIG_PATH
        config.unlink()
        before = snapshot(self.workspace.root)
        with environment(CONTEXT_LIBRARY_PROJECT="demo"):
            policy = projection.resolve_context_policy(self.workspace.root)
            self.assertEqual(policy.requirement, "undetermined")
            with self.assertRaisesRegex(projection.ProjectionError, "explicit required or optional"):
                projection.sync(self.workspace.root)
        self.assertEqual(before, snapshot(self.workspace.root))

    def test_projection_refuses_activation_root_inside_canonical_library(self) -> None:
        for nested in (False, True):
            with self.subTest(nested=nested):
                with tempfile.TemporaryDirectory() as temporary:
                    library = Path(temporary) / "library"
                    shutil.copytree(FIXTURE_LIBRARY, library)
                    activation = library / "consumer" if nested else library
                    activation.mkdir(exist_ok=True)
                    config = activation / projection.CONFIG_PATH
                    config.parent.mkdir(exist_ok=True)
                    config.write_text(
                        json.dumps(
                            {
                                "schema": "context-library/context-policy",
                                "schema_version": 1,
                                "project": "demo",
                                "context_requirement": "optional",
                                "affected_layers": {},
                            }
                        ),
                        encoding="utf-8",
                    )
                    before = snapshot(library)
                    with environment(CONTEXT_LIBRARY_ROOT=str(library)):
                        with self.assertRaisesRegex(projection.ProjectionError, "outside the canonical"):
                            projection.sync(activation)
                    self.assertEqual(before, snapshot(library))

    def test_projection_refuses_canonical_library_inside_activation_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            activation = Path(temporary) / "consumer"
            library = activation / "library"
            shutil.copytree(FIXTURE_LIBRARY, library)
            config = activation / projection.CONFIG_PATH
            config.parent.mkdir(parents=True)
            config.write_text(
                json.dumps(
                    {
                        "schema": "context-library/context-policy",
                        "schema_version": 1,
                        "project": "demo",
                        "context_requirement": "required",
                        "affected_layers": {"ui": "library/projects/demo"},
                    }
                ),
                encoding="utf-8",
            )
            before = snapshot(library)
            with environment(CONTEXT_LIBRARY_ROOT=str(library)):
                with self.assertRaisesRegex(projection.ProjectionError, "outside the canonical"):
                    projection.sync(activation)
            self.assertEqual(before, snapshot(library))

    def test_projection_does_not_follow_symlinked_canonical_pack_parent(self) -> None:
        outside = Path(self.workspace.temporary.name) / "outside-library"
        shutil.copytree(self.workspace.library / "projects", outside)
        projects = self.workspace.library / "projects"
        shutil.rmtree(projects)
        projects.symlink_to(outside, target_is_directory=True)
        before = snapshot(self.workspace.root)
        with self.assertRaisesRegex(projection.ProjectionError, "unavailable|available"):
            projection.sync(self.workspace.root)
        self.assertEqual(before, snapshot(self.workspace.root))

    def test_critical_explicit_only_and_preservation_assertions_are_concrete(self) -> None:
        projection.sync(self.workspace.root)
        root_text = (self.workspace.root / "AGENTS.md").read_text(encoding="utf-8")
        bullets = [line for line in root_text.splitlines() if line.startswith("- `[")]
        self.assertEqual(bullets, ["- `[current-root]` Use the current root convention."])
        naive_regression = [
            decision.decision
            for decision in projection.parse_decisions(
                (self.workspace.library / "projects" / "demo" / "decision-register.md").read_text(encoding="utf-8")
            )
        ]
        self.assertIn("A pre-session helper API may be useful.", naive_regression)
        self.assertNotIn("A pre-session helper API may be useful.", bullets)


if __name__ == "__main__":
    unittest.main(verbosity=2)
