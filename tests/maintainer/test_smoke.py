import json

import yaml
from typer.testing import CliRunner

from context_library_maintainer.cli import app
from context_library_maintainer.config import resolve_config, scaffold
from context_library_maintainer.models import Candidate, Finding, Observation, SourceEnvelope
from context_library_maintainer.publish import publish
from context_library_maintainer.reconcile import reconcile
from context_library_maintainer.state import State


def test_source_idempotence(tmp_path):
    state = State(tmp_path / "state")
    source = SourceEnvelope.model_validate(
        {
            "schema_version": 1,
            "external_id": "T-1",
            "source_type": "ticket",
            "uri": "jira://T-1",
            "title": "Example",
            "retrieved_at": "2026-07-16T00:00:00Z",
            "content_format": "markdown",
            "content": "Keep the UI product-owned.",
        }
    )
    first = state.add_source(source, "demo")
    second = state.add_source(source, "demo")
    assert first[0].startswith("src_") and len(first[0]) == 28
    assert second == (first[0], False)


def test_init_and_json_status(tmp_path):
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "init",
            "--library-root",
            str(tmp_path / "library"),
            "--state-root",
            str(tmp_path / "state"),
            "--project",
            "demo",
            "--json",
        ],
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "ok"
    status = runner.invoke(
        app,
        [
            "status",
            "--library-root",
            str(tmp_path / "library"),
            "--state-root",
            str(tmp_path / "state"),
            "--project",
            "demo",
            "--json",
        ],
    )
    assert status.exit_code == 0
    assert json.loads(status.stdout)["data"]["queue_state"] == "empty"


def test_ready_candidate_publishes_register_and_indexes(tmp_path):
    library = tmp_path / "library"
    state = tmp_path / "state"
    settings = resolve_config(library, "demo", state, "agent:test")
    scaffold(settings)
    config_path = library / "projects/demo/maintainer.yaml"
    config = yaml.safe_load(config_path.read_text())
    config["policies"]["automatic_publication"] = True
    config_path.write_text(yaml.safe_dump(config, sort_keys=False))
    db = State(state)
    source = SourceEnvelope.model_validate(
        {
            "schema_version": 1,
            "external_id": "T-2",
            "source_type": "chat",
            "uri": "teams://T-2",
            "title": "Direction",
            "retrieved_at": "2026-07-16T00:00:00Z",
            "content_format": "text",
            "content": "Keep the product-owned UI.",
        }
    )
    source_id, _ = db.add_source(source, "demo")
    observation = Observation.model_validate(
        {
            "schema_version": 1,
            "source_id": source_id,
            "kind": "directive",
            "excerpt": "Keep the product-owned UI.",
            "location": "message 1",
            "speaker": {"identity": "person@example.com", "display_name": "Person"},
            "occurred_at": "2026-07-16T00:00:00Z",
            "agent_interpretation": "Ownership directive",
        }
    )
    obs_id = "obs_test_ownership"
    db.add_observation(observation, obs_id, "demo")
    candidate = Candidate.model_validate(
        {
            "schema_version": 1,
            "project": "demo",
            "candidate_id": "product-owned-ui",
            "subject": "Product owns UI",
            "category": "ui",
            "decision": "Keep the product-owned UI.",
            "rationale": "The directive is explicit.",
            "decisionmaker": {"identity": "person@example.com", "display_name": "Person"},
            "decision_at": "2026-07-16T00:00:00Z",
            "provenance": "explicit",
            "derivation": "direct",
            "source_observation_ids": [obs_id],
            "applicability": {
                "provenance": "explicit",
                "confidence": 1.0,
                "evidence_observation_ids": [obs_id],
                "reasoning": "Product scope",
            },
        }
    )
    db.add_candidate(candidate)
    assert reconcile(db, settings)["ready"] == [candidate.candidate_id]
    result = publish(db, settings, ready_only=True, no_commit=True)
    assert result["published"] == [candidate.candidate_id]
    register = (library / "projects/demo/decision-register.md").read_text()
    assert '<a id="product-owned-ui"></a>' in register
    assert (library / "projects/demo/index-by-category.md").is_file()


def test_conflict_is_durable_and_not_published_as_decision(tmp_path):
    library = tmp_path / "library"
    state = tmp_path / "state"
    settings = resolve_config(library, "demo", state, "agent:test")
    scaffold(settings)
    db = State(state)
    source = SourceEnvelope.model_validate(
        {
            "schema_version": 1,
            "external_id": "T-3",
            "source_type": "ticket",
            "uri": "jira://T-3",
            "title": "Conflict",
            "retrieved_at": "2026-07-16T00:00:00Z",
            "content_format": "text",
            "content": "Choose A.",
        }
    )
    source_id, _ = db.add_source(source, "demo")
    observation = Observation.model_validate(
        {
            "schema_version": 1,
            "source_id": source_id,
            "kind": "directive",
            "excerpt": "Choose A.",
            "location": "body",
            "speaker": {"identity": "person@example.com", "display_name": "Person"},
            "occurred_at": "2026-07-16T00:00:00Z",
            "agent_interpretation": "Directive",
        }
    )
    obs_id = "obs_conflict"
    db.add_observation(observation, obs_id, "demo")
    candidate = Candidate.model_validate(
        {
            "schema_version": 1,
            "project": "demo",
            "candidate_id": "conflicting-direction",
            "subject": "Direction",
            "category": "product",
            "decision": "Choose A.",
            "rationale": "Conflicting evidence.",
            "decisionmaker": {"identity": "person@example.com", "display_name": "Person"},
            "decision_at": "2026-07-16T00:00:00Z",
            "provenance": "explicit",
            "derivation": "direct",
            "source_observation_ids": [obs_id],
            "conflict_key": "direction.choice",
            "applicability": {
                "provenance": "explicit",
                "confidence": 1.0,
                "evidence_observation_ids": [obs_id],
                "reasoning": "Product",
            },
        }
    )
    db.add_candidate(candidate)
    db.add_finding(
        Finding(
            schema_version=1,
            finding="conflict",
            candidate_id=candidate.candidate_id,
            canonical_ids=["existing-direction"],
            confidence=0.99,
            evidence_observation_ids=[obs_id],
            reasoning="Different directive.",
        )
    )
    result = reconcile(db, settings)
    assert result["conflicted"] == [candidate.candidate_id]
    conflict = db.db.execute("SELECT id FROM conflicts WHERE status='open'").fetchone()
    assert conflict is not None
    assert publish(db, settings, ready_only=True, no_commit=True)["conflicts"] == [conflict[0]]
    assert f'<a id="{candidate.candidate_id}"></a>' not in (library / "projects/demo/decision-register.md").read_text()
