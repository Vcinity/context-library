from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from .models import SourceEnvelope, digest
from .state import State


def envelopes_from(path: Path | None = None, directory: Path | None = None, stdin: str | None = None) -> Iterable[dict]:
    supplied = sum(value is not None for value in (path, directory, stdin))
    if supplied != 1:
        raise ValueError("exactly one of file, directory, or stdin is required")
    if path:
        yield json.loads(path.read_text(encoding="utf-8"))
    elif stdin is not None:
        yield json.loads(stdin)
    else:
        for item in sorted(directory.iterdir(), key=lambda p: p.as_posix().encode()):
            if item.is_file() and item.suffix.lower() == ".json":
                yield json.loads(item.read_text(encoding="utf-8"))


def ingest(state: State, payloads: Iterable[dict], project: str, retain: bool = True) -> list[dict]:
    results = []
    batch_bytes = 0
    for raw in payloads:
        encoded = json.dumps(raw, ensure_ascii=False).encode("utf-8")
        if len(encoded) > 10 * 1024 * 1024:
            raise ValueError("source envelope exceeds 10 MiB")
        batch_bytes += len(encoded)
        if batch_bytes > 100 * 1024 * 1024:
            raise ValueError("source batch exceeds 100 MiB")
        source = SourceEnvelope.model_validate(raw)
        if not retain and not source.retained_excerpts:
            raise ValueError("retained excerpts are required when source retention is disabled")
        source_id, created = state.add_source(source, project, retain)
        results.append({"source_id": source_id, "created": created, "digest": digest(raw)})
    return results
