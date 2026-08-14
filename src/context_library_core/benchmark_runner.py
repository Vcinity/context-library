from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path

from .benchmark_generator import SCALES, generate_pack
from .retrieval_baselines import run_baselines
from .retrieval_contracts import RetrievalBenchmarkGold, RetrievalBenchmarkTask
from .retrieval_safety import ReturnedDecision, SafetyInput, evaluate_safety


class BenchmarkRunnerError(ValueError):
    pass


def _fixture_inputs(root: Path) -> tuple[RetrievalBenchmarkTask, RetrievalBenchmarkGold]:
    task = RetrievalBenchmarkTask.model_validate_json(
        (root / "contracts/fixtures/retrieval-benchmark-task-v1.json").read_text()
    )
    gold = RetrievalBenchmarkGold.model_validate_json(
        (root / "contracts/fixtures/retrieval-benchmark-gold-v1.json").read_text()
    )
    return task, gold


def _register(gold: RetrievalBenchmarkGold, scale: int | None = None) -> str:
    labels = list(gold.labels)
    if scale:
        labels = [*labels[:1], *labels[1:]]
        labels.extend(
            type(labels[0])(decision_id=f"rb06-generated-{index:05d}", classification="operative")
            for index in range(max(0, scale - len(labels)))
        )
    blocks = [
        f'<a id="{label.decision_id}"></a>\n### Synthetic {label.decision_id}\n'
        "- Category: Synthetic benchmark\n- Provenance: explicit\n"
        f"- Decision: Guidance for {label.decision_id}.\n- Derivation: direct\n"
        "- Affected Layers: global\n\n"
        for label in labels
    ]
    return "# Synthetic runner register\n\n" + "".join(blocks)


def _scale_inputs(
    task: RetrievalBenchmarkTask, gold: RetrievalBenchmarkGold, scale: int
) -> tuple[RetrievalBenchmarkTask, RetrievalBenchmarkGold]:
    register_ids = [f"rb06-generated-{index:05d}" for index in range(max(0, scale - 4))]
    labels = [
        type(gold.labels[0])(
            decision_id=item.decision_id,
            classification=item.classification,
            exclusion_reason=item.exclusion_reason,
            conflict_ids=[],
        )
        for item in [*gold.labels[:1], *gold.labels[1:2]]
    ]
    labels.extend(type(labels[0])(decision_id=item, classification="operative") for item in register_ids)
    digest = hashlib.sha256(
        json.dumps([item.model_dump(mode="json") for item in labels], sort_keys=True).encode()
    ).hexdigest()
    payload = task.model_dump(by_alias=True)
    payload.update(
        task_id=f"synthetic-scale-{scale}",
        expected_operative_decision_ids=[item.decision_id for item in labels],
        judgment_required_decision_ids=[],
        excluded_decision_ids=[],
        applicable_conflicts=[],
        complete_coverage_possible=True,
        gold_sha256=digest,
    )
    gold_payload = gold.model_dump(by_alias=True)
    gold_payload.update(
        task_id=payload["task_id"],
        labels=[item.model_dump(mode="json") for item in labels],
        conflicts=[],
        gold_sha256=digest,
    )
    return RetrievalBenchmarkTask.model_validate(payload), RetrievalBenchmarkGold.model_validate(gold_payload)


def run_benchmark(root: Path, output: Path, *, seed: int = 17, strict: bool = False) -> dict[str, object]:
    targets = json.loads((root / "contracts/fixtures/retrieval-benchmark-targets-v1.json").read_text())
    if targets["scales"] != list(SCALES):
        raise BenchmarkRunnerError("target scales do not match generator scales")
    base_task, base_gold = _fixture_inputs(root)
    output.mkdir(parents=True, exist_ok=True)
    entries: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix="rb06-") as temporary:
        for scale in targets["scales"]:
            # RB-01 task lists are bounded; scale packs measure corpus growth
            # against the stable synthetic task/gold contract.
            task, gold = base_task, base_gold
            pack = generate_pack(Path(temporary) / f"scale-{scale}", scale=scale, seed=seed)
            report = run_baselines(
                task,
                gold,
                (pack.output / "decision-register.md").read_text(),
                result_limit=10,
                clock=lambda: 0.0,
            )
            for result in report.results:
                response = json.loads(result.agent_visible_response.serialized_content)
                sidecar = SafetyInput(
                    returned_decisions=[
                        ReturnedDecision(decision_id=item["decision_id"], classification="operative")
                        for item in response["decisions"]
                    ]
                )
                safety = evaluate_safety(task, gold, report, sidecar)
                reduction = result.relative_reduction if result.baseline_reference else 0.0
                entries.append(
                    {
                        "scale": scale,
                        "baseline_id": result.baseline_id,
                        "report": report.model_dump(mode="json"),
                        "safety": safety,
                        "target": {
                            "relative_reduction_met": reduction >= targets["minimum_relative_reduction"]
                            or result.baseline_reference is None,
                            "tool_calls_met": result.agent_directed_tool_calls
                            <= targets["maximum_agent_directed_tool_calls"],
                        },
                    }
                )
    payload = {
        "schema": "context-library/retrieval-benchmark-run",
        "schema_version": 1,
        "target_revision": targets["target_revision"],
        "entries": entries,
    }
    (output / "report.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    summary = [
        "# Retrieval benchmark",
        "",
        f"Target: `{targets['target_revision']}`",
        "",
        "| Scale | Baseline | Safety | Reduction |",
        "| ---: | --- | --- | ---: |",
    ]
    for entry in entries:
        result = entry["report"]["results"][0]
        summary.append(
            f"| {entry['scale']} | {entry['baseline_id']} | {entry['safety']['safety_passed']} | "
            f"{result['relative_reduction'] or 0:.3f} |"
        )
    (output / "summary.md").write_text("\n".join(summary) + "\n")
    failed_safety = [entry for entry in entries if not entry["safety"]["safety_passed"]]
    if strict and failed_safety:
        raise BenchmarkRunnerError(f"{len(failed_safety)} benchmark entries failed safety evaluation")
    return payload
