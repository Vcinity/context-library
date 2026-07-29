from __future__ import annotations

from pathlib import Path

from context_library_core.canonical import decision_ids as parse_register


def validate_pack(root: Path, register: str) -> dict[str, object]:
    from .config import safe_path

    register_path = root / register
    safe_path(root, register_path)
    text = register_path.read_text(encoding="utf-8")
    ids = parse_register(text)
    required = ["index-by-category.md", "index-by-date.md", "index-by-layer.md", "supersession-index.md"]
    missing = [name for name in required if not (root / name).is_file()]
    if missing:
        raise ValueError(f"generated indexes are missing: {', '.join(missing)}")
    if ids:
        from .publish import _indexes

        expected = _indexes(text)
        for name, content in expected.items():
            safe_path(root, root / name)
            if (root / name).read_text(encoding="utf-8") != content:
                raise ValueError(f"generated index is stale: {name}")
    return {"valid": True, "decision_ids": ids, "indexes": required}
