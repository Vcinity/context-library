#!/usr/bin/env python3
"""Fail-closed structural verifier for a production autonomy evidence bundle."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

REQUIRED_PRODUCERS = {"work", "review", "policy", "agent-invocation", "notification"}
VALID_HISTORY = {"complete", "insufficient-history"}
VALID_TELEMETRY = {"complete", "insufficient-telemetry"}
VALID_SLO = {"insufficient-telemetry", "insufficient-history", "no-data", "met", "missed"}
REQUIRED_METRICS = {
    "numerator",
    "denominator",
    "exclusions",
    "rate",
    "deterministic_only",
    "cache_only",
    "model_assisted",
    "agent_cache_hit_rate",
    "duplicate_work_rate",
    "median_terminal_seconds",
    "p95_terminal_seconds",
    "agent_invocation_rate",
    "agent_invocation_reasons",
    "inappropriate_agent_invocations",
    "retry_rate",
    "failure_rate",
    "human_escalation_by_reason",
    "deferred_budget_rate",
    "tokens_per_item",
    "cost_per_decision",
}


def parse_timestamp(value: Any, label: str, errors: list[str]) -> datetime | None:
    if not isinstance(value, str):
        errors.append(f"{label}:missing-timestamp")
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        errors.append(f"{label}:invalid-timestamp")
        return None
    if parsed.tzinfo is None:
        errors.append(f"{label}:timestamp-without-timezone")
        return None
    return parsed


def verify(bundle: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if bundle.get("schema") != "context-library/production-evidence-bundle":
        errors.append("schema:unsupported")
    if bundle.get("schema_version") != 1:
        errors.append("schema_version:unsupported")
    if not isinstance(bundle.get("project"), str) or not bundle["project"]:
        errors.append("project:missing")
    if bundle.get("production") is not True:
        errors.append("production-evidence-required")

    window = bundle.get("window")
    if not isinstance(window, dict):
        errors.append("window:missing")
        start = end = None
    else:
        start = parse_timestamp(window.get("start"), "window.start", errors)
        end = parse_timestamp(window.get("end"), "window.end", errors)
        if start and end:
            duration = end - start
            if duration < timedelta(days=30):
                errors.append("window:less-than-30-days")
            if window.get("days") != 30:
                errors.append("window:declared-days-not-30")

    manifest = bundle.get("manifest")
    if not isinstance(manifest, dict):
        errors.append("manifest:missing")
    else:
        if not isinstance(manifest.get("revision"), str) or not manifest["revision"]:
            errors.append("manifest:revision-missing")
        producers = set(manifest.get("required_producers", []))
        if producers != REQUIRED_PRODUCERS:
            errors.append("manifest:required-producer-set-mismatch")
        if manifest.get("immutable_for_window") is not True:
            errors.append("manifest:not-immutable-for-window")

    cohort = bundle.get("cohort")
    if not isinstance(cohort, dict) or not isinstance(cohort.get("item_ids"), list):
        errors.append("cohort:item-ids-missing")
    elif len(set(cohort["item_ids"])) != len(cohort["item_ids"]):
        errors.append("cohort:item-ids-not-unique")
    if not isinstance(cohort, dict) or not isinstance(cohort.get("rule"), str) or not cohort["rule"]:
        errors.append("cohort:rule-missing")

    telemetry = bundle.get("telemetry")
    if not isinstance(telemetry, dict):
        errors.append("telemetry:missing")
    else:
        telemetry_status = telemetry.get("status")
        if telemetry_status not in VALID_TELEMETRY:
            errors.append("telemetry:invalid-status")
        gaps = telemetry.get("coverage_gaps")
        if not isinstance(gaps, list):
            errors.append("telemetry:coverage-gaps-missing")
        elif telemetry_status == "complete" and gaps:
            errors.append("telemetry:complete-with-coverage-gaps")
        elif telemetry_status == "insufficient-telemetry" and not gaps:
            errors.append("telemetry:insufficient-without-named-gap")
        if not isinstance(telemetry.get("sequence_ranges"), dict):
            errors.append("telemetry:sequence-ranges-missing")
        if not isinstance(telemetry.get("watermarks"), dict):
            errors.append("telemetry:watermarks-missing")
        if not isinstance(telemetry.get("heartbeat_intervals_seconds"), dict):
            errors.append("telemetry:heartbeat-evidence-missing")
        if not isinstance(telemetry.get("replay_reconciled"), bool):
            errors.append("telemetry:replay-status-missing")
        if telemetry_status == "complete":
            for field in ("sequence_ranges", "watermarks", "heartbeat_intervals_seconds"):
                values = telemetry.get(field, {})
                missing = REQUIRED_PRODUCERS - set(values)
                if missing:
                    errors.append(f"telemetry:{field}-missing-producers:{','.join(sorted(missing))}")

    history = bundle.get("history")
    if not isinstance(history, dict) or history.get("status") not in VALID_HISTORY:
        errors.append("history:invalid-status")
    elif history.get("production_window") is not True:
        errors.append("history:production-window-required")

    metrics = bundle.get("metrics")
    if not isinstance(metrics, dict):
        errors.append("metrics:missing")
    else:
        errors.extend(f"metrics:{name}:missing" for name in sorted(REQUIRED_METRICS - metrics.keys()))
        denominator = metrics.get("denominator")
        rate = metrics.get("rate")
        inappropriate = metrics.get("inappropriate_agent_invocations")
        slo = metrics.get("slo_state")
        if slo not in VALID_SLO:
            errors.append("metrics:slo-state-invalid")
        telemetry_status = bundle.get("telemetry", {}).get("status")
        history_status = bundle.get("history", {}).get("status")
        if telemetry_status == "insufficient-telemetry" and slo != "insufficient-telemetry":
            errors.append("metrics:slo-precedence-telemetry")
        elif (
            telemetry_status == "complete"
            and history_status == "insufficient-history"
            and slo != "insufficient-history"
        ):
            errors.append("metrics:slo-precedence-history")
        if denominator == 0:
            if rate is not None or slo == "met":
                errors.append("metrics:empty-window-must-be-no-data")
        if slo == "met" and (not isinstance(rate, (int, float)) or rate < 0.95 or inappropriate != 0):
            errors.append("metrics:met-invariant-failed")
        if metrics.get("policy_segments") is not None and not isinstance(metrics["policy_segments"], dict):
            errors.append("metrics:policy-segments-invalid")

    if start and end and end < start:
        errors.append("window:end-before-start")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", required=True, type=Path)
    args = parser.parse_args()
    try:
        bundle = json.loads(args.bundle.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(json.dumps({"valid": False, "errors": [f"bundle:{type(exc).__name__}"]}))
        return 2
    errors = verify(bundle) if isinstance(bundle, dict) else ["bundle:not-object"]
    print(json.dumps({"valid": not errors, "errors": errors}, sort_keys=True))
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
