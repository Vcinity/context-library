from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from context_library_core.canonical import discover_packs

ROOT = Path(__file__).parents[2]


def run_script(relative: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ROOT / relative)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_manager_typed_maintainer_publication_is_readable_by_core_and_plugin():
    result = json.loads(run_script("scripts/smoke_context_library.py").stdout)
    assert result == {
        "candidate": "succeeded",
        "canonical_live_checkout_mutation": "none",
        "core_parser": "passed",
        "observation": "succeeded",
        "plugin_mcp": "passed",
        "plugin_projection": "passed",
        "source": "succeeded",
    }
    worker = (ROOT / "src/context_library_manager/worker.py").read_text(encoding="utf-8")
    assert "MaintainerApplicationService" in worker
    assert "maintainer_command" not in worker
    assert '["clm"' not in worker


def test_plugin_missing_context_and_projection_scenarios_are_cross_checked():
    activation = run_script("scripts/plugin/test_activation_hook.py")
    projection = run_script("scripts/plugin/test_projection.py")
    assert "activation hook policy matrix passed" in activation.stdout
    assert "OK" in projection.stderr


def test_legacy_flat_pack_is_one_logical_pack(tmp_path):
    register = tmp_path / "decision-artifacts/decision-register.md"
    register.parent.mkdir(parents=True)
    register.write_text(
        '# Register\n\n<a id="legacy"></a>\n### Legacy\n\n'
        "- Decision: Preserve legacy compatibility.\n"
        "- Provenance: explicit\n",
        encoding="utf-8",
    )
    packs = discover_packs(tmp_path)
    assert [(pack.project, pack.location) for pack in packs] == [("legacy", "decision-artifacts")]


def test_generated_plugin_runtime_matches_authoritative_core():
    check = subprocess.run(
        [sys.executable, str(ROOT / "scripts/generate_plugin_runtime.py"), "--check"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert check.returncode == 0
