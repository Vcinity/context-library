from __future__ import annotations

import pytest
from test_overview_shell import app_and_client

from context_library_manager.config import ConfigurationError, Settings
from context_library_manager.configuration import read_model
from context_library_manager.db import Store


def csrf(client, path: str, method: str) -> dict[str, str]:
    response = client.get("/api/v1/session/csrf", params={"method": method, "path": path})
    assert response.status_code == 200
    return {"X-CSRF-Token": response.json()["data"]["csrf_token"]}


def draft(revision: int, changes: dict, key: str = "config-1") -> dict:
    return {
        "schema_version": 1,
        "expected_revision": revision,
        "changes": changes,
        "reason": "validated operator change",
        "idempotency_key": key,
    }


def test_configuration_read_model_is_safe_and_preview_is_non_mutating(tmp_path):
    app, client = app_and_client(tmp_path)
    path = "/api/v1/projects/demo/configuration"
    response = client.get(path)
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["revision"] == 1
    assert data["fields"]["semantic_threshold"]["value"] == 0.5
    rendered = response.text
    for secret in (
        app.state.settings.session_secret,
        app.state.settings.oidc_hs256_secret,
    ):
        assert secret not in rendered
    assert all(set(state) == {"configured"} for state in data["deployment"].values())

    preview = client.post(
        path + "/preview",
        headers=csrf(client, path + "/preview", "POST"),
        json=draft(1, {"semantic_threshold": 0.7}),
    )
    assert preview.status_code == 200
    assert preview.json()["data"] == {
        "valid": True,
        "current_revision": 1,
        "affected_queues": ["semantic"],
        "budget_effects": [],
        "cache_invalidated": True,
        "restart_required": False,
        "changed_fields": ["semantic_threshold"],
        "errors": [],
    }
    assert client.get(path).json()["data"]["revision"] == 1


def test_project_file_setting_provenance_is_preserved(tmp_path, monkeypatch):
    library = tmp_path / "library"
    project = library / "projects" / "demo"
    project.mkdir(parents=True)
    (project / "runtime.yaml").write_text("schema_version: 1\nruntime:\n  worker_concurrency: 2\n")
    monkeypatch.setenv("CLM_LIBRARY_ROOT", str(library))
    monkeypatch.setenv("CLM_PROJECT", "demo")
    settings = Settings.from_env()
    model = read_model(Store(tmp_path / "runtime.db"), settings, "demo")
    assert model["fields"]["worker_concurrency"]["value"] == 2
    assert model["fields"]["worker_concurrency"]["source"] == "project-file"


def test_environment_settings_require_an_explicit_project(tmp_path, monkeypatch):
    monkeypatch.setenv("CLM_LIBRARY_ROOT", str(tmp_path / "library"))
    monkeypatch.delenv("CLM_PROJECT", raising=False)
    with pytest.raises(ConfigurationError, match="project must be explicit"):
        Settings.from_env()


def test_selected_project_loads_its_own_runtime_baseline(tmp_path):
    app, client = app_and_client(tmp_path, projects=("demo", "other"), selected="other")
    runtime = app.state.settings.library_root / "projects" / "other" / "runtime.yaml"
    runtime.write_text("schema_version: 1\ncost:\n  item_token_budget: 9000\nruntime:\n  review_reminder_days: 3\n")
    fields = client.get("/api/v1/projects/other/configuration").json()["data"]["fields"]
    assert fields["item_token_budget"]["value"] == 9000
    assert fields["item_token_budget"]["source"] == "project-file"
    assert fields["review_reminder_days"]["value"] == 3


def test_preview_for_uninitialized_project_has_no_database_side_effect(tmp_path):
    app, client = app_and_client(tmp_path, projects=("demo", "other"), selected="other")
    base = "/api/v1/projects/other/configuration"
    assert client.get(base).status_code == 200
    preview_path = base + "/preview"
    preview = client.post(
        preview_path,
        headers=csrf(client, preview_path, "POST"),
        json=draft(1, {"semantic_threshold": 0.7}, "other-preview"),
    )
    assert preview.status_code == 200
    assert preview.json()["data"]["valid"] is True
    assert not app.state.store.db.execute("SELECT 1 FROM configuration_revisions WHERE project='other'").fetchone()


def test_configuration_rejects_unsafe_fields_without_partial_persistence(tmp_path):
    _, client = app_and_client(tmp_path)
    base = "/api/v1/projects/demo/configuration"
    preview_path = base + "/preview"
    for changes in (
        {"database_url": "sqlite:///tmp/unsafe"},
        {"webhook_secret": "exposed"},
        {"worker_concurrency": 0},
        {"library_root": "/tmp"},
    ):
        result = client.post(
            preview_path,
            headers=csrf(client, preview_path, "POST"),
            json=draft(1, changes, f"preview-{next(iter(changes))}"),
        )
        assert result.status_code == 200
        assert result.json()["data"]["valid"] is False
    invalid = client.put(
        base,
        headers=csrf(client, base, "PUT"),
        json=draft(1, {"worker_concurrency": 0}, "invalid-apply"),
    )
    assert invalid.status_code == 422
    assert client.get(base).json()["data"]["revision"] == 1


def test_apply_is_idempotent_stale_safe_and_visible_to_runtime(tmp_path):
    app, client = app_and_client(tmp_path)
    path = "/api/v1/projects/demo/configuration"
    body = draft(1, {"semantic_threshold": 0.7}, "apply-once")
    first = client.put(path, headers=csrf(client, path, "PUT"), json=body)
    duplicate = client.put(path, headers=csrf(client, path, "PUT"), json=body)
    assert first.status_code == duplicate.status_code == 200
    assert first.json()["data"]["revision"] == 2
    assert duplicate.json()["data"]["idempotent"] is True
    fields = client.get(path).json()["data"]["fields"]
    assert fields["semantic_threshold"]["value"] == 0.7
    assert fields["semantic_threshold"]["source"] == "project-file"
    assert fields["project_daily_token_budget"]["source"] == "default"

    stale = client.put(
        path,
        headers=csrf(client, path, "PUT"),
        json=draft(1, {"semantic_threshold": 0.8}, "stale"),
    )
    assert stale.status_code == 409
    rows = app.state.store.db.execute(
        "SELECT COUNT(*) AS count FROM configuration_revisions WHERE project='demo'"
    ).fetchone()
    assert rows["count"] == 2
    overview = client.get("/api/v1/projects/demo/overview").json()["data"]
    assert overview["budget"]["limit"] == app.state.settings.project_daily_token_budget


def test_restart_required_and_rollback_create_immutable_revisions(tmp_path):
    _, client = app_and_client(tmp_path)
    base = "/api/v1/projects/demo/configuration"
    applied = client.put(
        base,
        headers=csrf(client, base, "PUT"),
        json=draft(1, {"worker_concurrency": 2}, "restart-change"),
    )
    assert applied.status_code == 200
    assert applied.json()["data"]["restart_required"] is True

    rollback_path = base + "/rollback"
    rollback_body = {
        "schema_version": 1,
        "expected_revision": 2,
        "target_revision": 1,
        "reason": "restore known-good worker configuration",
        "idempotency_key": "rollback-1",
    }
    rollback = client.post(
        rollback_path,
        headers=csrf(client, rollback_path, "POST"),
        json=rollback_body,
    )
    assert rollback.status_code == 200
    assert rollback.json()["data"]["revision"] == 3
    assert rollback.json()["data"]["rolled_back_from"] == 1
    history = client.get(base + "/history").json()["data"]["items"]
    assert [item["revision"] for item in history] == [3, 2, 1]
    assert history[0]["rolled_back_from"] == 1
    assert client.get(base).json()["data"]["fields"]["worker_concurrency"]["value"] == 4


def test_agent_service_reports_project_effective_limits(tmp_path):
    _, client = app_and_client(tmp_path)
    base = "/api/v1/projects/demo/configuration"
    applied = client.put(
        base,
        headers=csrf(client, base, "PUT"),
        json=draft(
            1,
            {
                "project_daily_token_budget": 400_000,
                "worker_concurrency": 2,
                "lease_seconds": 900,
                "max_attempts": 2,
            },
            "service-limits",
        ),
    )
    assert applied.status_code == 200
    service = client.get("/api/v1/agent-service").json()["data"]
    assert service["project_token_budget"] == 400_000
    assert service["worker_concurrency"] == 2
    assert service["lease_seconds"] == 900
    assert service["retry_limit"] == 2


def test_configuration_page_exposes_island_without_secret_material(tmp_path):
    app, client = app_and_client(tmp_path)
    page = client.get("/configuration")
    assert page.status_code == 200
    assert 'data-island="configuration"' in page.text
    assert "Revision history" not in page.text  # React owns interactive history.
    assert app.state.settings.session_secret not in page.text
