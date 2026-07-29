from __future__ import annotations

# ruff: noqa: E501
import hashlib
import json
from typing import Any

from context_library_core.canonical import parse_register, weakest_provenance
from context_library_core.maintainer_contracts import safe_error

from .config import project_files
from .models import Candidate, ConflictPacket, Finding, Observation, canonical_json, now_utc, timestamp
from .state import State


def reconcile(state: State, settings: dict[str, Any], candidate_id: str | None = None) -> dict[str, Any]:
    root, config, topology, authority = project_files(settings)
    register_text = (root / config.register).read_text(encoding="utf-8")
    canonical = parse_register(register_text) if "<a id=" in register_text else ()
    register_ids = {decision.decision_id for decision in canonical}
    canonical_by_id = {decision.decision_id: decision for decision in canonical}
    rows = state.candidates(settings["project"], "proposed")
    if candidate_id:
        rows = [row for row in rows if row["id"] == candidate_id]
    output = {"ready": [], "conflicted": [], "invalid": [], "duplicate": []}
    for row in rows:
        try:
            candidate = Candidate.model_validate_json(row["payload_json"])
            if candidate.derivation == "synthesized":
                if any(identifier not in register_ids for identifier in candidate.sources):
                    state.transition(
                        candidate.candidate_id, "invalid", "synthesis references an unknown source decision"
                    )
                    output["invalid"].append(candidate.candidate_id)
                    continue
                expected = weakest_provenance(canonical_by_id[item].provenance for item in candidate.sources)
                if candidate.provenance != expected:
                    state.transition(
                        candidate.candidate_id,
                        "invalid",
                        f"synthesis must preserve weakest source provenance {expected}",
                    )
                    output["invalid"].append(candidate.candidate_id)
                    continue
            if any(identifier not in register_ids for identifier in candidate.supersedes + candidate.conflicts_with):
                state.transition(
                    candidate.candidate_id, "invalid", "candidate references an unknown canonical decision"
                )
                output["invalid"].append(candidate.candidate_id)
                continue
            observations = {
                item["id"]: Observation.model_validate_json(item["payload_json"])
                for item in state.observations(candidate.source_observation_ids, candidate.project)
            }
            if any(layer not in topology.layers for layer in candidate.affected_layers):
                state.transition(candidate.candidate_id, "invalid", "unknown affected layer")
                output["invalid"].append(candidate.candidate_id)
                continue
            if any(oid not in observations for oid in candidate.source_observation_ids):
                state.transition(candidate.candidate_id, "invalid", "missing observation")
                output["invalid"].append(candidate.candidate_id)
                continue
            if candidate.provenance == "explicit" and not any(
                o.kind in {"directive", "constraint"} and o.speaker and o.occurred_at for o in observations.values()
            ):
                state.transition(candidate.candidate_id, "invalid", "explicit candidate lacks directive evidence")
                output["invalid"].append(candidate.candidate_id)
                continue
            if candidate.provenance == "inferred" and len(observations) < 2:
                state.transition(candidate.candidate_id, "invalid", "inferred candidate needs two observations")
                output["invalid"].append(candidate.candidate_id)
                continue
            if (
                candidate.applicability.provenance != "explicit"
                and candidate.applicability.confidence < config.policies.minimum_routing_confidence
            ):
                # Keep proposed layers as non-operative suggestions; rendering
                # applies the conservative root-scope fallback.
                pass
            findings = [
                Finding.model_validate_json(item["payload_json"])
                for item in state.db.execute("SELECT * FROM findings WHERE candidate_id=?", (candidate.candidate_id,))
            ]
            peer_conflict = (
                next(
                    (
                        peer
                        for peer in state.candidates(candidate.project)
                        if peer["id"] != candidate.candidate_id
                        and json.loads(peer["payload_json"]).get("conflict_key") == candidate.conflict_key
                        and json.loads(peer["payload_json"]).get("decision") != candidate.decision
                        and peer["state"] in {"ready", "published"}
                    ),
                    None,
                )
                if candidate.conflict_key
                else None
            )
            conflict = next((f for f in findings if f.finding == "conflict"), None)
            if candidate.category in config.policies.human_approval_categories and conflict is None:
                conflict = Finding(
                    schema_version=1,
                    finding="conflict",
                    candidate_id=candidate.candidate_id,
                    canonical_ids=[],
                    confidence=1.0,
                    evidence_observation_ids=candidate.source_observation_ids,
                    reasoning="This category requires human approval under project policy.",
                )
            if peer_conflict and conflict is None:
                peer_is_canonical = peer_conflict["state"] == "published"
                conflict = Finding(
                    schema_version=1,
                    finding="conflict",
                    candidate_id=candidate.candidate_id,
                    canonical_ids=[peer_conflict["id"]] if peer_is_canonical else [],
                    confidence=1.0,
                    evidence_observation_ids=candidate.source_observation_ids,
                    reasoning="Two active candidates claim incompatible directives for the same conflict key.",
                )
            duplicate = next((f for f in findings if f.finding == "duplicate"), None)
            if conflict:
                conflict_reason = conflict.reasoning
                canonical_ids = sorted(conflict.canonical_ids)
                peer_candidate_ids = (
                    [peer_conflict["id"]] if peer_conflict is not None and peer_conflict["state"] != "published" else []
                )
                candidate_ids = sorted({candidate.candidate_id, *peer_candidate_ids})
                seed = {
                    "project": candidate.project,
                    "candidate_ids": candidate_ids,
                    "canonical_ids": canonical_ids,
                    "reason": conflict_reason,
                }
                conflict_id = f"conflict-{candidate.project}-{now_utc():%Y%m%d}-{hashlib.sha256(canonical_json(seed).encode()).hexdigest()[:6]}"
                packet = ConflictPacket(
                    conflict_id=conflict_id,
                    project=candidate.project,
                    status="open",
                    created_at=now_utc(),
                    question=f"Choose the applicable directive for {candidate.subject}.",
                    candidate_ids=candidate_ids,
                    canonical_ids=canonical_ids,
                    reason=conflict_reason,
                    choices=[
                        *[
                            {
                                "value": f"accept:{identifier}",
                                "label": (
                                    f"Accept {candidate.subject}"
                                    if identifier == candidate.candidate_id
                                    else f"Accept candidate {identifier}"
                                ),
                            }
                            for identifier in candidate_ids
                        ],
                        {"value": "retain-current", "label": "Retain current direction"},
                    ],
                    recommendation="retain-current",
                    safe_behavior="Keep the last known-good canonical decision and publish unrelated ready work.",
                )
                state.db.execute(
                    "INSERT OR REPLACE INTO conflicts(id, project, payload_json, status, created_at, updated_at) VALUES (?, ?, ?, 'open', COALESCE((SELECT created_at FROM conflicts WHERE id=?), ?), ?)",
                    (
                        conflict_id,
                        candidate.project,
                        packet.model_dump_json(by_alias=True),
                        conflict_id,
                        timestamp(now_utc()),
                        timestamp(now_utc()),
                    ),
                )
                for peer_id in peer_candidate_ids:
                    state.transition(peer_id, "conflicted", conflict.reasoning)
                state.transition(candidate.candidate_id, "conflicted", conflict.reasoning)
                output["conflicted"].append(candidate.candidate_id)
            elif duplicate:
                state.transition(candidate.candidate_id, "duplicate", duplicate.reasoning)
                output["duplicate"].append(candidate.candidate_id)
            else:
                state.transition(candidate.candidate_id, "ready")
                output["ready"].append(candidate.candidate_id)
        except Exception as exc:
            state.transition(row["id"], "invalid", safe_error(exc))
            output["invalid"].append(row["id"])
    return output
