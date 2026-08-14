from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from context_library_manager.api import create_app
from context_library_manager.config import ConfigurationError, ManagedProject, Settings
from context_library_manager.scheduler import (
    FairProjectScheduler,
    ProjectLifecycle,
    ProjectRuntimeRegistry,
)


def project(name: str) -> ManagedProject:
    return ManagedProject(name, Path("/srv") / name, name)


def test_fair_scheduler_rotates_across_eligible_projects_and_isolates_pause():
    registry = ProjectRuntimeRegistry([project("alpha"), project("beta")])
    scheduler = FairProjectScheduler(registry)
    assert [scheduler.next_project(("alpha", "beta")) for _ in range(4)] == [
        "alpha",
        "beta",
        "alpha",
        "beta",
    ]
    registry.transition("alpha", ProjectLifecycle.PAUSED)
    assert [scheduler.next_project(("alpha", "beta")) for _ in range(3)] == ["beta", "beta", "beta"]
    registry.transition("alpha", ProjectLifecycle.ENABLED)
    assert scheduler.next_project(("alpha", "beta")) == "alpha"


def test_registry_rejects_invalid_lifecycle_and_unknown_projects():
    registry = ProjectRuntimeRegistry([project("alpha")])
    with pytest.raises(KeyError, match="not enrolled"):
        registry.get("outside")
    with pytest.raises(ValueError, match="invalid project lifecycle"):
        registry.transition("alpha", ProjectLifecycle.DISABLED)


def test_settings_reject_duplicate_or_unmanaged_default_projects(tmp_path):
    with pytest.raises(ConfigurationError, match="unique"):
        Settings(
            "sqlite:///" + str(tmp_path / "runtime.db"),
            tmp_path / "library",
            tmp_path / "state",
            "alpha",
            managed_projects=(project("alpha"), project("alpha")),
        )
    with pytest.raises(ConfigurationError, match="default project"):
        Settings(
            "sqlite:///" + str(tmp_path / "runtime.db"),
            tmp_path / "library",
            tmp_path / "state",
            "outside",
            managed_projects=(project("alpha"),),
        )


def test_versioned_registry_enrolls_only_explicit_projects(tmp_path, monkeypatch):
    library = tmp_path / "library"
    for name in ("alpha", "beta", "gamma", "discovered"):
        (library / "projects" / name).mkdir(parents=True)
    config_file = tmp_path / "manager.yaml"
    config_file.write_text(
        "schema_version: 1\n"
        "managed_projects:\n"
        "  - id: alpha\n"
        f"    library_root: {library / 'projects' / 'alpha'}\n"
        "    state_namespace: alpha\n"
        "  - id: beta\n"
        f"    library_root: {library / 'projects' / 'beta'}\n"
        "    state_namespace: beta\n"
        "  - id: gamma\n"
        f"    library_root: {library / 'projects' / 'gamma'}\n"
        "    state_namespace: gamma\n"
        "    enabled: false\n"
    )
    monkeypatch.setenv("CLM_LIBRARY_ROOT", str(library))
    monkeypatch.setenv("CLM_PROJECT", "alpha")
    monkeypatch.setenv("CLM_CONFIG_FILE", str(config_file))
    settings = replace(
        Settings.from_env(),
        database_url="sqlite:///" + str(tmp_path / "runtime.db"),
        require_oidc=False,
        allow_local_dev_identity=True,
        development_mode=True,
        session_secret="multi-project-session-secret",
    )
    assert settings.explicit_project_registry is True
    assert settings.managed_project_ids == ("alpha", "beta")

    app = create_app(settings)
    client = TestClient(app, base_url="https://testserver")
    login = client.post(
        "/auth/dev-login",
        json={
            "subject": "fixture:admin",
            "display_name": "Fixture Admin",
            "capabilities": ["admin"],
            "projects": ["alpha", "beta", "discovered"],
            "selected_project": "alpha",
        },
    )
    assert login.status_code == 200
    assert client.get("/api/v1/projects/beta/overview").status_code == 200
    assert client.get("/api/v1/projects/discovered/overview").status_code == 404
    assert client.get("/api/v1/projects/gamma/overview").status_code == 404
    enrolled = app.state.store.db.execute("SELECT id FROM projects ORDER BY id").fetchall()
    assert [row["id"] for row in enrolled] == ["alpha", "beta", "gamma"]
    gamma = app.state.store.db.execute("SELECT active FROM projects WHERE id='gamma'").fetchone()
    assert gamma["active"] == 0
