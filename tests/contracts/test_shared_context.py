from __future__ import annotations

import yaml
import pytest

from context_library_core.shared_context import SharedContextError, resolve_effective_view


def _pack(root, project: str, decision: str, consumers: list[str] | None = None):
    path = root / "projects" / project
    path.mkdir(parents=True)
    (path / "decision-register.md").write_text(
        f'# Decision Register\n\n<a id="{project}"></a>\n### {project}\n\n- Decision: {decision}\n- Provenance: explicit\n',
        encoding="utf-8",
    )
    (path / "authority.yaml").write_text(
        yaml.safe_dump({"project": project, "shared_context_consumers": consumers or []}), encoding="utf-8"
    )
    return path


def _relationship(path, project: str, parents: list[dict[str, object]]):
    (path / "shared-context-relationships.yaml").write_text(
        yaml.safe_dump(
            {"schema": "context-library/shared-context-relationships", "schema_version": 1,
             "project": project, "revision": "relationships-r1", "parents": parents},
            sort_keys=False,
        ), encoding="utf-8",
    )


def test_effective_view_is_ordered_and_preserves_source_scope(tmp_path):
    shared = _pack(tmp_path, "shared", "shared policy", ["child"])
    child = _pack(tmp_path, "child", "child policy")
    _relationship(child, "child", [{"project": "shared", "order": 10}])

    view = resolve_effective_view(tmp_path, "child")

    assert [item.source_project for item in view.records] == ["shared", "child"]
    assert view.records[0].source_scope == "projects/shared"
    assert len(view.records[0].source_digest) == 64


def test_effective_view_rejects_unauthorized_parent(tmp_path):
    _pack(tmp_path, "shared", "shared policy")
    child = _pack(tmp_path, "child", "child policy")
    _relationship(child, "child", [{"project": "shared"}])

    with pytest.raises(SharedContextError, match="does not authorize"):
        resolve_effective_view(tmp_path, "child")


def test_effective_view_rejects_cycle(tmp_path):
    shared = _pack(tmp_path, "shared", "shared policy", ["child"])
    child = _pack(tmp_path, "child", "child policy", ["child"])
    _relationship(shared, "shared", [{"project": "child"}])
    _relationship(child, "child", [{"project": "shared"}])

    with pytest.raises(SharedContextError, match="cycle"):
        resolve_effective_view(tmp_path, "child")
