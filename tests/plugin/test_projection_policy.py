from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).parents[2]
PLUGIN = ROOT / "plugins/context-library"
sys.path.insert(0, str(PLUGIN))

import projection  # noqa: E402
from hooks import session_start  # noqa: E402


def _library(tmp_path: Path) -> Path:
    register = tmp_path / "library/projects/demo/decision-register.md"
    register.parent.mkdir(parents=True)
    register.write_text(
        """# Decisions

<a id="universal"></a>
### Universal

- Decision: Keep the read-only boundary.
- Constraint: Keep the read-only boundary.
- Provenance: explicit

<a id="scoped"></a>
### Scoped

- Decision: Use the service adapter in the backend.
- Constraint: Use the service adapter in the backend.
- Provenance: explicit
- Affected Layers: backend

<a id="conditional"></a>
### Conditional

- Decision: Apply the deployment rule when the tier is known.
- Constraint: Apply the deployment rule when the tier is known.
- Provenance: explicit
- Applies-When: deployment tier is known

<a id="inferred"></a>
### Inferred

- Decision: Treat inferred guidance as non-authoritative.
- Constraint: Treat inferred guidance as non-authoritative.
- Provenance: inferred

<a id="old"></a>
### Old

- Decision: Use the old boundary.
- Constraint: Use the old boundary.
- Provenance: explicit

<a id="current"></a>
### Current

- Decision: Use the current boundary.
- Constraint: Use the current boundary.
- Provenance: explicit
- Supersedes: `old`

<a id="conflict-a"></a>
### Conflict A

- Decision: Use one conflicting policy.
- Constraint: Use one conflicting policy.
- Provenance: explicit
- Conflicts-With: `conflict-b`

<a id="conflict-b"></a>
### Conflict B

- Decision: Use another conflicting policy.
- Constraint: Use another conflicting policy.
- Provenance: explicit
""",
        encoding="utf-8",
    )
    return tmp_path / "library"


def _consumer(tmp_path: Path) -> Path:
    root = tmp_path / "consumer"
    (root / ".context-library").mkdir(parents=True)
    (root / ".context-library/config.json").write_text(
        json.dumps(
            {
                "schema": "context-library/context-policy",
                "schema_version": 1,
                "project": "demo",
                "context_requirement": "optional",
                "affected_layers": {"backend": "services"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return root


def test_automatic_projection_contains_only_current_universal_explicit_guidance(tmp_path, monkeypatch):
    library = _library(tmp_path)
    consumer = _consumer(tmp_path)
    monkeypatch.setenv("CONTEXT_LIBRARY_ROOT", str(library))

    compilation = projection.prepare(consumer, automatic=True)
    assert {item.source_ids for item in compilation.constraints} == {("universal",), ("current",)}
    excluded = {item["record_id"]: item["reason"] for item in compilation.excluded_context}
    assert excluded == {
        "conditional": "unevaluated-applicability",
        "conflict-a": "conflicted",
        "conflict-b": "conflicted",
        "inferred": "non-authoritative",
        "old": "superseded",
        "scoped": "scoped",
    }


def test_session_start_has_no_task_signal_and_does_not_inject_scoped_or_conditional_context(tmp_path, monkeypatch):
    library = _library(tmp_path)
    consumer = _consumer(tmp_path)
    monkeypatch.setenv("CONTEXT_LIBRARY_ROOT", str(library))
    monkeypatch.setenv("CONTEXT_LIBRARY_PROJECT_ROOT", str(consumer))
    monkeypatch.delenv("CONTEXT_LIBRARY_TASK_SUMMARY", raising=False)
    monkeypatch.delenv("CONTEXT_LIBRARY_REPOSITORY_SCOPES", raising=False)

    session_start.main()

    projected = (consumer / "AGENTS.md").read_text(encoding="utf-8")
    assert "Keep the read-only boundary." in projected
    assert "Use the current boundary." in projected
    assert "Use the service adapter in the backend." not in projected
    assert "Apply the deployment rule when the tier is known." not in projected
    assert "Treat inferred guidance as non-authoritative." not in projected
