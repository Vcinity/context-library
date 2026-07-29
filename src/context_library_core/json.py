from __future__ import annotations

import json
from datetime import datetime
from typing import Any


def canonical_json(value: Any) -> str:
    """Return deterministic UTF-8-safe JSON without non-finite numbers."""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=lambda item: item.isoformat() if isinstance(item, datetime) else str(item),
    )
