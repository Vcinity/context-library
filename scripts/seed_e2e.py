import yaml

from context_library_maintainer.config import resolve_config, scaffold
from context_library_maintainer.models import Observation, SourceEnvelope
from context_library_maintainer.state import State
from context_library_manager.config import Settings
from context_library_manager.db import Store
from context_library_manager.domain import utc_now

REGISTER = """# Decision Register

## User interface

<a id="ui-react"></a>
### React remained the GUI framework choice
- Category: user-interface
- Date: 2026-07-28
- Decisionmaker: E2E Product Owner
- Decision: Continue using React and TypeScript.
- Constraint: Keep the Manager UI on React.
- Rationale: This synthetic record exercises the local browser workflow.
- Provenance: explicit
- Derivation: direct
- Evidence: `ticket://E2E-UI-1`
"""


def main() -> None:
    settings = Settings.from_env()
    maintainer_settings = resolve_config(
        settings.library_root,
        settings.project,
        settings.state_root,
        "fixture:e2e",
    )
    scaffold(maintainer_settings)
    config_path = settings.library_root / "projects" / settings.project / "maintainer.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["policies"]["automatic_publication"] = True
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    (settings.library_root / "projects" / settings.project / "decision-register.md").write_text(
        REGISTER,
        encoding="utf-8",
    )
    maintainer_state = State(settings.state_root)
    source_id, _ = maintainer_state.add_source(
        SourceEnvelope.model_validate(
            {
                "external_id": "E2E-UI-42",
                "source_type": "ticket",
                "uri": "ticket://UI-42",
                "title": "TypeScript proposal authority",
                "retrieved_at": "2026-07-28T00:00:00Z",
                "content_format": "text",
                "content": "Continue using React and TypeScript.",
            }
        ),
        settings.project,
    )
    maintainer_state.add_observation(
        Observation.model_validate(
            {
                "source_id": source_id,
                "kind": "directive",
                "excerpt": "Continue using React and TypeScript.",
                "location": "ticket body",
                "speaker": {
                    "identity": "product-owner@example.invalid",
                    "display_name": "Product Owner",
                },
                "occurred_at": "2026-07-28T00:00:00Z",
                "agent_interpretation": "Explicit UI direction.",
            }
        ),
        "e2e-proposal-observation",
        settings.project,
    )
    maintainer_state.db.commit()
    maintainer_state.db.close()
    store = Store(settings.storage_target)
    try:
        for key, question in (
            ("e2e-review", "Resolve E2E evidence conflict"),
            ("e2e-review-nojs", "Resolve no-JavaScript evidence conflict"),
        ):
            work_id, _ = store.add_work(
                settings.project,
                "candidate_task",
                key,
                {
                    "category": "product",
                    "urgency": "high",
                    "review_reason": "evidence-conflict",
                    "owner": "fixture-reviewer",
                    "source_type": "ticket",
                    "alternatives": ["retain current", "adopt candidate"],
                    "recommendation": "Retain the current canonical decision.",
                },
                "fixture:e2e",
            )
            work = store.work(settings.project, work_id)
            if work["state"] == "queued":
                store.transition(settings.project, work_id, "leased", "fixture:e2e")
                store.transition(settings.project, work_id, "running", "fixture:e2e")
                store.transition(settings.project, work_id, "waiting-human", "fixture:e2e")
            store.create_review(
                settings.project,
                work_id,
                question,
                ["retain-current", "adopt-candidate"],
                ["ticket://E2E-1 Product owner evidence"],
                "fixture:e2e",
            )
        failed, _ = store.add_work(settings.project, "semantic_task", "e2e-failed", {}, "fixture:e2e")
        store.db.execute("UPDATE work_items SET state='failed' WHERE id=?", (failed,))
        active, _ = store.add_work(settings.project, "e2e_cancellable_task", "e2e-active", {}, "fixture:e2e")
        if store.work(settings.project, active)["state"] == "queued":
            store.transition(settings.project, active, "leased", "fixture:worker")
            store.transition(settings.project, active, "running", "fixture:worker")
            store.start_agent_run(
                "agentrun-e2e-active",
                active,
                "cheap",
                "fixture:worker",
                "1",
                "e2e-cache",
            )
        store.db.commit()
        store.db.execute(
            "INSERT INTO publication_history(id,project,status,digest,git_revision,created_at) "
            "VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING",
            (
                "publication-e2e-good",
                settings.project,
                "succeeded",
                "a" * 64,
                "e2e-good",
                utc_now(),
            ),
        )
        store.db.execute(
            "INSERT INTO publication_history(id,project,status,error,created_at) "
            "VALUES(?,?,?,?,?) ON CONFLICT(id) DO NOTHING",
            (
                "publication-e2e-failed",
                settings.project,
                "failed",
                "api_key=e2e-secret",
                utc_now(),
            ),
        )
        store.event(
            None,
            "fixture:operator",
            "runtime-e2e-observed",
            {
                "capability": "admin",
                "before_reference": "runtime:old",
                "after_reference": "runtime:new",
            },
            settings.project,
        )
        store.db.commit()
    finally:
        store.close()


if __name__ == "__main__":
    main()
