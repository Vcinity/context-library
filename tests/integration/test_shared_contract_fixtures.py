from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from context_library_core.canonical import CanonicalParseError, parse_register
from context_library_core.contracts import ContextPolicy
from context_library_maintainer.query import query_library
from context_library_manager.config import Settings
from context_library_manager.library import LibraryError, LibraryReader

ROOT = Path(__file__).parents[2]
FIXTURES = ROOT / "contracts/fixtures"
PLUGIN_ROOT = ROOT / "plugins/context-library"


def plugin_runtime():
    sys.path.insert(0, str(PLUGIN_ROOT))
    path = PLUGIN_ROOT / "generated/core_runtime.py"
    spec = importlib.util.spec_from_file_location("shared_fixture_plugin_runtime", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def fixture_library(tmp_path: Path, fixture: str) -> Path:
    library = tmp_path / "library"
    pack = library / "projects/demo"
    pack.mkdir(parents=True)
    (pack / "decision-register.md").write_bytes((FIXTURES / fixture).read_bytes())
    return library


def test_core_maintainer_manager_and_plugin_execute_the_same_positive_register_fixture(tmp_path):
    text = (FIXTURES / "register-positive.md").read_text(encoding="utf-8")
    expected = ["product-explicit", "product-inferred", "product-assumed"]
    assert [item.decision_id for item in parse_register(text)] == expected

    library = fixture_library(tmp_path, "register-positive.md")
    maintained = query_library(library, "demo", page_size=100)
    assert [item["decision_id"] for item in maintained["items"]] == sorted(expected)

    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'manager.db'}",
        library_root=library,
        state_root=tmp_path / "manager-state",
        project="demo",
        require_oidc=False,
        allow_local_dev_identity=True,
        development_mode=True,
        session_secret="shared-fixture-session-secret",
    )
    _, managed = LibraryReader(settings).search(page_size=100)
    assert [item.decision_id for item in managed.items] == sorted(expected)

    runtime = plugin_runtime()
    assert [item.decision_id for item in runtime.parse_register(text)] == expected


def test_core_maintainer_manager_and_plugin_reject_the_same_cycle_fixture(tmp_path):
    text = (FIXTURES / "register-supersession-cycle.md").read_text(encoding="utf-8")
    with pytest.raises(CanonicalParseError, match="supersession cycle"):
        parse_register(text)

    library = fixture_library(tmp_path, "register-supersession-cycle.md")
    with pytest.raises(CanonicalParseError, match="supersession cycle"):
        query_library(library, "demo")

    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'manager.db'}",
        library_root=library,
        state_root=tmp_path / "manager-state",
        project="demo",
        require_oidc=False,
        allow_local_dev_identity=True,
        development_mode=True,
        session_secret="shared-fixture-session-secret",
    )
    with pytest.raises(LibraryError, match="supersession cycle"):
        LibraryReader(settings).search()

    runtime = plugin_runtime()
    with pytest.raises(runtime.CanonicalParseError, match="supersession cycle"):
        runtime.parse_register(text)


@pytest.mark.parametrize("name", ["required", "optional", "disabled"])
def test_core_and_plugin_execute_the_same_named_context_policy_fixtures(name):
    payload = json.loads((FIXTURES / f"context-policy-{name}.json").read_text(encoding="utf-8"))
    assert ContextPolicy.model_validate(payload).context_requirement == name
    assert plugin_runtime().validate_context_policy(payload)["context_requirement"] == name
