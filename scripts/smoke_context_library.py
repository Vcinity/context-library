"""Exercise Manager -> typed Maintainer -> canonical pack -> Plugin read flow."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from context_library_core.canonical import parse_register
from context_library_maintainer.config import resolve_config, scaffold
from context_library_manager.api import create_app
from context_library_manager.config import Settings
from context_library_manager.worker import Worker

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins/context-library"


def load_plugin_module(name: str, path: Path):
    sys.path.insert(0, str(PLUGIN))
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="context-library-smoke-") as directory:
        temporary = Path(directory)
        library = temporary / "library"
        state = temporary / "maintainer-state"
        settings_data = resolve_config(library, "demo", state, "runtime:smoke")
        scaffold(settings_data)
        (library / "projects/demo/decision-register.md").write_text(
            """# Decision Register

<a id="plugin-read-boundary"></a>
### Plugin read boundary
- Category: authority
- Date: 2026-07-27
- Decisionmaker: Owner
- Decision: Keep the Plugin on the read side.
- Rationale: This is the synthetic last-known-good decision.
- Provenance: explicit
- Derivation: direct
- Evidence: `source:smoke-baseline`

""",
            encoding="utf-8",
        )
        config_path = library / "projects/demo/maintainer.yaml"
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        config["policies"]["automatic_publication"] = True
        config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
        subprocess.run(["git", "init", "-q", str(library)], check=True)
        subprocess.run(
            ["git", "-C", str(library), "config", "user.name", "Context Library Smoke"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(library), "config", "user.email", "smoke@example.invalid"],
            check=True,
        )
        subprocess.run(["git", "-C", str(library), "add", "."], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(library),
                "commit",
                "-qm",
                "baseline",
            ],
            check=True,
        )

        manager_settings = Settings(
            "sqlite:///" + str(temporary / "runtime.db"),
            library,
            state,
            "demo",
            require_oidc=False,
            allow_local_dev_identity=True,
            development_mode=True,
            session_secret="context-library-smoke-session-secret",
        )
        app = create_app(manager_settings)
        client = TestClient(app, base_url="https://testserver")
        login = client.post(
            "/auth/dev-login",
            json={
                "subject": "fixture:smoke",
                "display_name": "Smoke Operator",
                "capabilities": ["admin"],
                "projects": ["demo"],
                "selected_project": "demo",
            },
        )
        if login.status_code != 200:
            raise RuntimeError(login.text)

        def csrf(path: str) -> dict[str, str]:
            response = client.get("/api/v1/session/csrf", params={"method": "POST", "path": path})
            return {"X-CSRF-Token": response.json()["data"]["csrf_token"]}

        source_path = "/api/v1/projects/demo/sources"
        source_response = client.post(
            source_path,
            headers={"Idempotency-Key": "smoke-source", **csrf(source_path)},
            json={
                "external_id": "SMOKE-1",
                "source_type": "project-note",
                "uri": "local://SMOKE-1",
                "title": "Smoke direction",
                "content": "Keep the Plugin canonical-read-only.",
                "content_format": "text",
                "retrieved_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        if source_response.status_code != 200:
            raise RuntimeError(source_response.text)
        source_id = source_response.json()["data"]["maintainer"]["sources"][0]["source_id"]

        contribution_path = "/api/v1/projects/demo/contributions"
        observation_response = client.post(
            contribution_path,
            headers=csrf(contribution_path),
            json={
                "kind": "observation",
                "payload": {
                    "schema_version": 1,
                    "source_id": source_id,
                    "kind": "directive",
                    "excerpt": "Keep the Plugin canonical-read-only.",
                    "location": "body",
                    "speaker": {
                        "identity": "owner@example.invalid",
                        "display_name": "Owner",
                    },
                    "occurred_at": "2026-07-28T00:00:00Z",
                    "agent_interpretation": "Explicit authority boundary",
                },
                "client_idempotency_key": "smoke-observation",
            },
        )
        if observation_response.status_code != 200:
            raise RuntimeError(observation_response.text)
        observation_result = Worker(app.state.store, manager_settings).run_once()
        observation_id = observation_result["maintainer"]["observation_id"]

        proposal_path = "/api/v1/projects/demo/library/proposals"
        library_snapshot = client.get("/api/v1/projects/demo/library/search").json()["data"]
        proposal_response = client.post(
            proposal_path,
            headers=csrf(proposal_path),
            json={
                "operation": "supersede",
                "decision_id": "plugin-read-boundary",
                "proposed_fields": {"decision": "Keep the Plugin canonical-read-only."},
                "rationale": "The Plugin is a read-side integration.",
                "evidence_references": [observation_id],
                "authority": "Owner",
                "publication_intent": True,
                "library_digest": library_snapshot["library_digest"],
                "idempotency_key": "smoke-proposal",
            },
        )
        if proposal_response.status_code != 200:
            raise RuntimeError(proposal_response.text)
        proposal_data = proposal_response.json()["data"]
        prior_agent = os.environ.get("CLM_AGENT_COMMAND")
        os.environ["CLM_AGENT_COMMAND"] = f"{sys.executable} {ROOT / 'scripts/fake_e2e_agent.py'}"
        try:
            normalized = Worker(app.state.store, manager_settings).run_once()
            if normalized["status"] != "queued":
                raise RuntimeError(normalized)
            candidate_result = Worker(app.state.store, manager_settings).run_once()
        finally:
            if prior_agent is None:
                os.environ.pop("CLM_AGENT_COMMAND", None)
            else:
                os.environ["CLM_AGENT_COMMAND"] = prior_agent
        if candidate_result["status"] != "waiting-human":
            raise RuntimeError(candidate_result)
        review = app.state.store.db.execute(
            "SELECT id FROM reviews WHERE work_id=?",
            (candidate_result["work_id"],),
        ).fetchone()
        review_path = f"/api/v1/projects/demo/reviews/{review['id']}/resolve"
        resolution = client.post(
            review_path,
            headers=csrf(review_path),
            json={
                "choice": "adopt-candidate",
                "rationale": "The explicit evidence supports publication.",
                "idempotency_key": "smoke-publication-approval",
            },
        )
        if resolution.status_code != 200:
            raise RuntimeError(resolution.text)
        candidate_result = Worker(app.state.store, manager_settings).run_once()
        if candidate_result["status"] != "succeeded":
            raise RuntimeError(candidate_result)
        proposal_lifecycle = client.get(f"{proposal_path}/{proposal_data['proposal_id']}").json()["data"]["lifecycle"]
        if (
            proposal_lifecycle["work_id"] != proposal_data["work_id"]
            or proposal_lifecycle["review_id"] != review["id"]
            or proposal_lifecycle["state"] != "succeeded"
            or not proposal_lifecycle["publication_id"]
        ):
            raise RuntimeError("proposal lifecycle lost its review or publication linkage")

        register_path = library / "projects/demo/decision-register.md"
        decisions = parse_register(register_path.read_text(encoding="utf-8"))
        if [decision.decision_id for decision in decisions] != [
            "plugin-read-boundary",
            "linked-proposal-typescript",
        ]:
            raise RuntimeError("published decision did not pass Core parser")

        mcp = load_plugin_module(
            "context_library_smoke_mcp",
            PLUGIN / "mcp/context_library_server.py",
        )
        prior_root = os.environ.get("CONTEXT_LIBRARY_ROOT")
        os.environ["CONTEXT_LIBRARY_ROOT"] = str(library)
        try:
            matches = mcp.search_decisions({"project": "demo", "query": "canonical-read-only"})
        finally:
            if prior_root is None:
                os.environ.pop("CONTEXT_LIBRARY_ROOT", None)
            else:
                os.environ["CONTEXT_LIBRARY_ROOT"] = prior_root
        if not matches["matches"]:
            raise RuntimeError("Plugin MCP could not read the published decision")

        consumer = temporary / "consumer"
        consumer.mkdir()
        policy = consumer / ".context-library/config.json"
        policy.parent.mkdir()
        policy.write_text(
            json.dumps(
                {
                    "schema": "context-library/context-policy",
                    "schema_version": 1,
                    "project": "demo",
                    "context_requirement": "required",
                    "affected_layers": {},
                }
            )
            + "\n",
            encoding="utf-8",
        )
        projection = load_plugin_module(
            "context_library_smoke_projection",
            PLUGIN / "projection.py",
        )
        prior_root = os.environ.get("CONTEXT_LIBRARY_ROOT")
        os.environ["CONTEXT_LIBRARY_ROOT"] = str(library)
        try:
            projection.sync(consumer)
            try:
                projection.check(consumer)
            except projection.CheckError as exc:
                sidecar = projection._read_sidecar(consumer, required=True)
                compilation = projection.prepare(consumer)
                expected = projection.build_sidecar(compilation, sidecar["generated_at"])
                differences = sorted(
                    key for key in set(sidecar) | set(expected) if sidecar.get(key) != expected.get(key)
                )
                raise RuntimeError(f"Plugin projection check failed in fields {differences}: {exc}") from exc
        finally:
            if prior_root is None:
                os.environ.pop("CONTEXT_LIBRARY_ROOT", None)
            else:
                os.environ["CONTEXT_LIBRARY_ROOT"] = prior_root
        if "[linked-proposal-typescript]" not in (consumer / "AGENTS.md").read_text():
            raise RuntimeError("Plugin projection omitted the published explicit decision")

        print(
            json.dumps(
                {
                    "source": source_response.json()["data"]["status"],
                    "observation": observation_result["status"],
                    "candidate": candidate_result["status"],
                    "core_parser": "passed",
                    "plugin_mcp": "passed",
                    "plugin_projection": "passed",
                    "canonical_live_checkout_mutation": "none",
                },
                sort_keys=True,
            )
        )


if __name__ == "__main__":
    main()
