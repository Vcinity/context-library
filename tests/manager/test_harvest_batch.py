from __future__ import annotations

from datetime import datetime, timezone

from test_auth_sessions import bearer
from test_overview_shell import app_and_client

from context_library_core.maintainer_contracts import HarvestBatch
from context_library_maintainer.config import resolve_config, scaffold
from context_library_maintainer.state import State
from context_library_manager.config import Settings
from context_library_manager.db import Store
from context_library_manager.service import intake_harvest_batch
from context_library_manager.worker import Worker


def _settings(tmp_path):
    settings = Settings(
        "sqlite:///" + str(tmp_path / "runtime.db"),
        tmp_path / "library",
        tmp_path / "state",
        "demo",
        require_oidc=False,
        allow_local_dev_identity=True,
        development_mode=True,
        session_secret="harvest-batch-test-secret",
    )
    scaffold(resolve_config(settings.library_root, "demo", settings.state_root, "test"))
    return settings


def _batch() -> HarvestBatch:
    timestamp = datetime(2026, 8, 5, 12, tzinfo=timezone.utc)
    return HarvestBatch.model_validate(
        {
            "schema": "context-library/harvest-batch",
            "schema_version": 1,
            "batch_id": "batch-synthetic-chat",
            "idempotency_key": "harvest-synthetic-chat-v1",
            "project": "demo",
            "produced_at": timestamp.isoformat().replace("+00:00", "Z"),
            "redacted": True,
            "canonical_write": False,
            "sources": [
                {
                    "schema": "context-library/source-envelope",
                    "schema_version": 1,
                    "external_id": "teams-chat:synthetic-1",
                    "source_type": "chat",
                    "uri": "teams://chat/synthetic-1",
                    "title": "Synthetic retry discussion",
                    "retrieved_at": timestamp.isoformat().replace("+00:00", "Z"),
                    "content_format": "text",
                    "content": "Retries must use the local queue.",
                }
            ],
            "observations": [
                {
                    "schema": "context-library/observation",
                    "schema_version": 1,
                    "observation_id": "obs-synthetic-1",
                    "source_id": "teams-chat:synthetic-1",
                    "kind": "directive",
                    "excerpt": "Retries must use the local queue.",
                    "location": "teams://chat/synthetic-1#message-1",
                    "speaker": {"identity": "person-synthetic-1", "display_name": "Synthetic Person"},
                    "occurred_at": timestamp.isoformat().replace("+00:00", "Z"),
                    "agent_interpretation": "The message states an explicit retry convention.",
                }
            ],
            "candidates": [
                {
                    "schema": "context-library/candidate",
                    "schema_version": 1,
                    "project": "demo",
                    "candidate_id": "candidate-synthetic-retry",
                    "subject": "Retry behavior",
                    "category": "directive",
                    "decision": "Retries must use the local queue.",
                    "rationale": "The team selected the local queue to preserve consistent retry behavior.",
                    "decisionmaker": {"identity": "person-synthetic-1", "display_name": "Synthetic Person"},
                    "decision_at": timestamp.isoformat().replace("+00:00", "Z"),
                    "provenance": "explicit",
                    "derivation": "direct",
                    "source_observation_ids": ["obs-synthetic-1"],
                    "sources": ["teams-chat:synthetic-1"],
                    "conflict_key": "Retry behavior",
                    "applicability": {
                        "provenance": "explicit",
                        "confidence": 0.98,
                        "evidence_observation_ids": ["obs-synthetic-1"],
                        "reasoning": "The directive is explicit and has direct message evidence.",
                    },
                    "review": {"status": "unreviewed"},
                }
            ],
            "findings": [],
        }
    )


def test_harvest_batch_materializes_redacted_proposal_and_is_idempotent(tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.db_path)
    batch = _batch()

    accepted = intake_harvest_batch(store, settings, "demo", batch, "m2m:harvester")
    replay = intake_harvest_batch(store, settings, "demo", batch, "m2m:harvester")
    assert accepted["status"] == "pending"
    assert replay["idempotent"] is True

    result = Worker(store, settings, owner="worker-test").run_once()
    assert result["status"] == "succeeded", result
    review_result = Worker(store, settings, owner="worker-test").run_once()
    assert review_result["status"] == "waiting-human", review_result
    assert store.db.execute("SELECT COUNT(*) FROM reviews WHERE project='demo'").fetchone()[0] == 1
    assert result["maintainer"]["candidates"] == [{"candidate_id": "candidate-synthetic-retry", "created": True}]

    state = State(settings.state_root)
    try:
        source_count = state.db.execute("SELECT COUNT(*) FROM sources WHERE project='demo'").fetchone()[0]
        observation_count = state.db.execute("SELECT COUNT(*) FROM observations WHERE project='demo'").fetchone()[0]
        candidate = state.db.execute("SELECT state FROM candidates WHERE id='candidate-synthetic-retry'").fetchone()
        assert source_count == 1
        assert observation_count == 1
        assert candidate[0] == "ready"
    finally:
        state.db.close()


def test_harvest_batch_api_accepts_machine_identity_without_browser_csrf(tmp_path):
    app, client = app_and_client(tmp_path)
    scaffold(resolve_config(app.state.settings.library_root, "demo", app.state.settings.state_root, "test"))
    body = _batch().model_dump(mode="json", by_alias=True)
    headers = {
        "Authorization": bearer(
            {
                "sub": "harvester:synthetic",
                "name": "Synthetic Harvester",
                "token_class": "m2m",
                "roles": ["maintain"],
                "project": "demo",
                "exp": 4_000_000_000,
            }
        ),
        "Idempotency-Key": "harvest-synthetic-chat-v1",
    }
    path = "/api/v1/projects/demo/harvest-batches"
    first = client.post(path, headers=headers, json=body)
    second = client.post(path, headers=headers, json=body)
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["data"]["status"] == "pending"
    assert second.json()["data"]["idempotent"] is True
    result = Worker(app.state.store, app.state.settings, owner="worker-api-test").run_once()
    assert result["status"] == "succeeded", result
