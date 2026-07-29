"""Deterministic local provider used only by synthetic smoke and browser tests."""

from __future__ import annotations

import json
import sys


def main() -> None:
    request = json.load(sys.stdin)
    context = request["task_context"]
    evidence = request.get("evidence", [])
    observation_ids = [item["observation_id"] for item in evidence if item.get("observation_id")]
    decision_id = context.get("decision_id")
    candidate = {
        "schema": "context-library/candidate",
        "schema_version": 1,
        "project": request["project"],
        "candidate_id": "linked-proposal-typescript",
        "subject": "TypeScript browser proposal",
        "category": "user-interface",
        "decision": context["proposed_fields"]["decision"],
        "rationale": context["rationale"],
        "decisionmaker": {
            "identity": "product-owner@example.invalid",
            "display_name": context["authority"],
        },
        "decision_at": "2026-07-28T00:00:00Z",
        "provenance": "explicit",
        "derivation": "direct",
        "source_observation_ids": observation_ids,
        "supersedes": [decision_id] if decision_id else [],
        "applicability": {
            "provenance": "explicit",
            "confidence": 1.0,
            "evidence_observation_ids": observation_ids,
            "reasoning": "The proposal applies to the product UI.",
        },
    }
    print(
        json.dumps(
            {
                "schema_version": 1,
                "run_id": request["run_id"],
                "status": "ok",
                "result": candidate,
                "usage": {"input_tokens": 8, "output_tokens": 24, "estimated_cost": 0},
                "confidence": 1.0,
                "warnings": [],
            }
        )
    )


if __name__ == "__main__":
    main()
