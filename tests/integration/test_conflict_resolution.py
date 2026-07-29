from __future__ import annotations

import json

import pytest
import yaml

from context_library_maintainer.config import resolve_config, scaffold
from context_library_maintainer.service import MaintainerApplicationService, MaintainerContext
from context_library_maintainer.state import State


def test_conflict_waits_while_unrelated_work_publishes_and_human_resolution_preserves_history(tmp_path):
    library = tmp_path / "library"
    state = tmp_path / "state"
    settings = resolve_config(library, "demo", state, "human:owner")
    scaffold(settings)
    config_path = library / "projects/demo/maintainer.yaml"
    config = yaml.safe_load(config_path.read_text())
    config["policies"]["automatic_publication"] = True
    config_path.write_text(yaml.safe_dump(config, sort_keys=False))
    register = library / "projects/demo/decision-register.md"
    original = (
        '# Decision Register\n\n<a id="current-direction"></a>\n### Current direction\n\n'
        "- Category: product\n"
        "- Decision: Keep the current direction.\n"
        "- Provenance: explicit\n"
        "- Conflict-Key: product.direction\n\n"
    )
    register.write_text(original)
    service = MaintainerApplicationService(MaintainerContext(library, state, "demo", "human:owner"))
    source_id = service.ingest_source(
        {
            "external_id": "R-1",
            "source_type": "project-note",
            "uri": "local://R-1",
            "title": "New direction",
            "retrieved_at": "2026-07-28T00:00:00Z",
            "content_format": "text",
            "content": "Choose the new direction. Keep logs local.",
        }
    )["sources"][0]["source_id"]
    observation_id = service.add_observation(
        {
            "source_id": source_id,
            "kind": "directive",
            "excerpt": "Choose the new direction.",
            "location": "sentence 1",
            "speaker": {"identity": "owner@example.test", "display_name": "Owner"},
            "occurred_at": "2026-07-28T00:00:00Z",
            "agent_interpretation": "Explicit replacement direction.",
        }
    )["observation_id"]

    def candidate(identifier: str, decision: str, conflict_key: str | None = None) -> dict:
        return {
            "project": "demo",
            "candidate_id": identifier,
            "subject": identifier.replace("-", " "),
            "category": "product",
            "decision": decision,
            "rationale": "Explicit directive.",
            "decisionmaker": {"identity": "owner@example.test", "display_name": "Owner"},
            "decision_at": "2026-07-28T00:00:00Z",
            "provenance": "explicit",
            "derivation": "direct",
            "source_observation_ids": [observation_id],
            "conflict_key": conflict_key,
            "applicability": {
                "provenance": "explicit",
                "confidence": 1,
                "evidence_observation_ids": [observation_id],
                "reasoning": "Product-wide.",
            },
        }

    service.add_candidate(candidate("new-direction", "Choose the new direction.", "product.direction"))
    service.add_finding(
        {
            "finding": "conflict",
            "candidate_id": "new-direction",
            "canonical_ids": ["current-direction"],
            "confidence": 1,
            "evidence_observation_ids": [observation_id],
            "reasoning": "The directives choose different directions.",
        }
    )
    assert service.reconcile()["conflicted"] == ["new-direction"]
    service.add_candidate(candidate("local-logs", "Keep logs local."))
    assert service.reconcile()["ready"] == ["local-logs"]
    first_publication = service.publish_ready(no_commit=True)
    assert first_publication["published"] == ["local-logs"]
    assert "Keep the current direction." in register.read_text()
    assert '<a id="new-direction"></a>' not in register.read_text()

    conflict_id = service.conflict_list()["conflicts"][0]["id"]
    resolved = service.conflict_resolve(
        conflict_id,
        "accept:new-direction",
        "Owner explicitly selected the replacement.",
    )
    resolution_id = resolved["resolution_candidate_id"]
    durable = State(state)
    before = {
        table: durable.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in ("sources", "observations", "candidates", "conflicts")
    }
    durable.db.close()
    with pytest.raises(KeyError):
        service.conflict_resolve(conflict_id, "accept:new-direction", "replayed review")
    durable = State(state)
    after = {
        table: durable.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in ("sources", "observations", "candidates", "conflicts")
    }
    durable.db.close()
    assert after == before
    reconciled = service.reconcile(resolution_id)
    assert reconciled["ready"] == [resolution_id]
    service.publish_ready(no_commit=True)
    final = register.read_text()
    assert original in final
    assert f'<a id="{resolution_id}"></a>' in final
    assert "- Supersedes: `current-direction`" in final


def test_peer_ready_conflict_resolution_never_supersedes_a_noncanonical_candidate(tmp_path):
    library = tmp_path / "library"
    state = tmp_path / "state"
    settings = resolve_config(library, "demo", state, "human:owner")
    scaffold(settings)
    service = MaintainerApplicationService(MaintainerContext(library, state, "demo", "human:owner"))
    source_id = service.ingest_source(
        {
            "external_id": "peer-conflict",
            "source_type": "other",
            "uri": "local://peer-conflict",
            "title": "Peer conflict",
            "retrieved_at": "2026-07-28T00:00:00Z",
            "content_format": "text",
            "content": "Choose A or B.",
        }
    )["sources"][0]["source_id"]
    observation_id = service.add_observation(
        {
            "source_id": source_id,
            "kind": "directive",
            "excerpt": "Choose A or B.",
            "location": "body",
            "speaker": {"identity": "owner@example.test", "display_name": "Owner"},
            "occurred_at": "2026-07-28T00:00:00Z",
            "agent_interpretation": "Conflicting explicit choices.",
        }
    )["observation_id"]

    def candidate(identifier: str, decision: str) -> dict:
        return {
            "project": "demo",
            "candidate_id": identifier,
            "subject": identifier,
            "category": "product",
            "decision": decision,
            "rationale": "Explicit directive.",
            "decisionmaker": {"identity": "owner@example.test", "display_name": "Owner"},
            "decision_at": "2026-07-28T00:00:00Z",
            "provenance": "explicit",
            "derivation": "direct",
            "source_observation_ids": [observation_id],
            "conflict_key": "product.peer-choice",
            "applicability": {
                "provenance": "explicit",
                "confidence": 1,
                "evidence_observation_ids": [observation_id],
                "reasoning": "Product-wide.",
            },
        }

    service.add_candidate(candidate("candidate-a", "Choose A."))
    assert service.reconcile()["ready"] == ["candidate-a"]
    service.add_candidate(candidate("candidate-b", "Choose B."))
    assert service.reconcile()["conflicted"] == ["candidate-b"]
    packet = service.conflict_show(service.conflict_list()["conflicts"][0]["id"])
    assert packet.canonical_ids == []
    assert packet.candidate_ids == ["candidate-a", "candidate-b"]
    durable = State(state)
    assert {row["id"] for row in durable.candidates("demo", "conflicted")} == {
        "candidate-a",
        "candidate-b",
    }
    durable.db.close()

    resolved = service.conflict_resolve(
        packet.conflict_id,
        "accept:candidate-b",
        "Owner selected B.",
    )
    resolution_id = resolved["resolution_candidate_id"]
    result = service.reconcile(resolution_id)
    assert result["ready"] == [resolution_id]
    durable = State(state)
    resolution = next(row for row in durable.candidates("demo", "ready") if row["id"] == resolution_id)
    payload = json.loads(resolution["payload_json"])
    assert payload["supersedes"] == []
    assert {row["id"] for row in durable.candidates("demo", "rejected")} == {
        "candidate-a",
        "candidate-b",
    }
    history = {
        identifier: [
            row["to_state"]
            for row in durable.db.execute(
                "SELECT to_state FROM candidate_events WHERE candidate_id=? ORDER BY id",
                (identifier,),
            )
        ]
        for identifier in ("candidate-a", "candidate-b", resolution_id)
    }
    assert history == {
        "candidate-a": ["ready", "conflicted", "rejected"],
        "candidate-b": ["conflicted", "rejected"],
        resolution_id: ["ready"],
    }
    resolved_packet = service.conflict_show(packet.conflict_id)
    assert resolved_packet.status == "resolved"
    assert resolved_packet.resolution is not None
    assert resolved_packet.resolution.resolution_candidate_id == resolution_id
    assert resolved_packet.resolution.resolution_source_id
    durable.db.close()
