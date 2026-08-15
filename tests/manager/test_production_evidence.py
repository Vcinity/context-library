from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from test_overview_shell import app_and_client

SCRIPT = Path(__file__).parents[2] / "scripts" / "verify_production_evidence.py"


def bundle(*, denominator: int = 0, rate: float | None = None, slo: str = "no-data") -> dict:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = start + timedelta(days=30)
    metrics = {
        "numerator": 0,
        "denominator": denominator,
        "exclusions": 0,
        "rate": rate,
        "slo_state": slo,
        "deterministic_only": 0,
        "cache_only": 0,
        "model_assisted": 0,
        "agent_cache_hit_rate": None,
        "duplicate_work_rate": 0.0,
        "median_terminal_seconds": None,
        "p95_terminal_seconds": None,
        "agent_invocation_rate": None,
        "agent_invocation_reasons": {},
        "inappropriate_agent_invocations": 0,
        "retry_rate": 0.0,
        "failure_rate": 0.0,
        "human_escalation_by_reason": {},
        "deferred_budget_rate": 0.0,
        "tokens_per_item": None,
        "cost_per_decision": None,
        "policy_segments": {},
    }
    return {
        "schema": "context-library/production-evidence-bundle",
        "schema_version": 1,
        "project": "demo",
        "production": True,
        "manifest": {
            "revision": "deploy-v1",
            "required_producers": ["work", "review", "policy", "agent-invocation", "notification"],
            "immutable_for_window": True,
        },
        "window": {"start": start.isoformat(), "end": end.isoformat(), "days": 30},
        "cohort": {"item_ids": [], "rule": "intake-accepted in window plus unresolved at window start"},
        "telemetry": {
            "status": "complete",
            "coverage_gaps": [],
            "sequence_ranges": {
                producer: {"first": 1, "last": 1, "count": 1}
                for producer in ["work", "review", "policy", "agent-invocation", "notification"]
            },
            "watermarks": {
                producer: 1 for producer in ["work", "review", "policy", "agent-invocation", "notification"]
            },
            "heartbeat_intervals_seconds": {
                producer: 60 for producer in ["work", "review", "policy", "agent-invocation", "notification"]
            },
            "replay_reconciled": True,
        },
        "history": {"status": "complete", "production_window": True},
        "metrics": metrics,
    }


def run_verifier(path: Path) -> tuple[int, dict]:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--bundle", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode, json.loads(result.stdout)


def test_verifier_accepts_complete_empty_window_as_no_data(tmp_path):
    path = tmp_path / "bundle.json"
    path.write_text(json.dumps(bundle()), encoding="utf-8")
    code, result = run_verifier(path)
    assert code == 0
    assert result == {"errors": [], "valid": True}


def test_verifier_rejects_met_without_zero_inappropriate_calls(tmp_path):
    path = tmp_path / "bundle.json"
    value = bundle(denominator=10, rate=1.0, slo="met")
    value["metrics"]["inappropriate_agent_invocations"] = 1
    path.write_text(json.dumps(value), encoding="utf-8")
    code, result = run_verifier(path)
    assert code == 2
    assert "metrics:met-invariant-failed" in result["errors"]


def test_autonomy_evidence_api_preserves_insufficient_production_status(tmp_path):
    _, client = app_and_client(tmp_path)
    response = client.get("/api/v1/projects/demo/autonomy/evidence")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["schema"] == "context-library/production-evidence-bundle"
    assert data["production"] is True
    assert data["metrics"]["slo_state"] in {"insufficient-history", "insufficient-telemetry", "no-data"}
