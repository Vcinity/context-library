from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Type

from pydantic import BaseModel

from context_library_core.contracts import (
    AgentProviderRequest,
    AgentProviderResponse,
    CapabilityEnvelope,
    CommandEnvelope,
    ContextPolicy,
    ContextResolution,
    MissingContextNotice,
    VersionEnvelope,
)
from context_library_core.maintainer_contracts import (
    Candidate,
    ConflictPacket,
    ConflictResolution,
    Finding,
    HarvestBatch,
    Observation,
    SourceEnvelope,
)
from context_library_core.manager_contracts import ProposalSubmission, SessionIdentity

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "contracts/schemas"
MODELS: dict[str, Type[BaseModel]] = {
    "agent-provider-request-v1": AgentProviderRequest,
    "agent-provider-response-v1": AgentProviderResponse,
    "candidate-v1": Candidate,
    "capabilities-v1": CapabilityEnvelope,
    "context-policy-v1": ContextPolicy,
    "context-resolution-v1": ContextResolution,
    "conflict-packet-v1": ConflictPacket,
    "conflict-resolution-v1": ConflictResolution,
    "finding-v1": Finding,
    "harvest-batch-v1": HarvestBatch,
    "maintainer-command-v1": CommandEnvelope,
    "manager-proposal-v1": ProposalSubmission,
    "manager-session-v1": SessionIdentity,
    "missing-context-notice-v1": MissingContextNotice,
    "observation-v1": Observation,
    "source-envelope-v1": SourceEnvelope,
    "version-v1": VersionEnvelope,
}


def rendered(model: Type[BaseModel]) -> bytes:
    return (json.dumps(model.model_json_schema(), ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    failures: list[str] = []
    for name, model in MODELS.items():
        target = OUTPUT / f"{name}.json"
        content = rendered(model)
        if args.check:
            if not target.is_file() or target.read_bytes() != content:
                failures.append(str(target.relative_to(ROOT)))
        else:
            OUTPUT.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
    if failures:
        print("generated contract drift: " + ", ".join(failures))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
