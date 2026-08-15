from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from context_library_core.canonical import ID_RE, parse_register
from context_library_maintainer.publish import _indexes

GENERATOR_VERSION = "rb-03-v1"
SCALES = (10, 100, 1000, 10000)
OWNERSHIP_MARKER = ".rb03-owned"
GOLD_DECISIONS = (
    ("rb03-gold-interface", "Use explicit versioned service boundaries.", "global"),
    ("rb03-gold-review", "Record a review before changing a project pack.", "projects/synthetic"),
)


class BenchmarkGenerationError(ValueError):
    pass


@dataclass(frozen=True)
class GeneratedPack:
    output: Path
    manifest: dict[str, object]


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_output(output: Path, canonical_root: Path | None, allow_existing: bool) -> Path:
    target = output.expanduser().absolute()
    if target == Path("/") or target.is_symlink():
        raise BenchmarkGenerationError("output directory is unsafe")
    for ancestor in (target, *target.parents):
        if ancestor.is_symlink():
            raise BenchmarkGenerationError("output path contains a symlink")
    if canonical_root is not None:
        root = canonical_root.expanduser().absolute().resolve(strict=False)
        try:
            target.resolve(strict=False).relative_to(root)
        except ValueError:
            pass
        else:
            raise BenchmarkGenerationError("output directory is inside the canonical root")
    marker = target / OWNERSHIP_MARKER
    if target.exists():
        if not target.is_dir():
            raise BenchmarkGenerationError("output path is not a directory")
        children = {item.name for item in target.iterdir()}
        if children and not allow_existing:
            raise BenchmarkGenerationError("output directory is not empty")
        if children and not marker.is_file():
            raise BenchmarkGenerationError("existing output directory is not generator-owned")
        if children and marker.read_text(encoding="utf-8") != f"{GENERATOR_VERSION}\n":
            raise BenchmarkGenerationError("existing output directory is not generator-owned")
    else:
        target.mkdir(parents=True)
    return target


def _identifier(seed: int, ordinal: int) -> str:
    digest = hashlib.sha256(f"{GENERATOR_VERSION}:{seed}:{ordinal}".encode()).hexdigest()[:16]
    return f"rb03-{digest}-{ordinal}"


def _decision_block(decision_id: str, subject: str, decision: str, scope: str, date: str, **fields: str) -> str:
    lines = [
        f'<a id="{decision_id}"></a>',
        f"### {subject}",
        "- Category: Synthetic benchmark",
        "- Provenance: explicit",
        f"- Date: {date}",
        f"- Decision: {decision}",
        "- Derivation: direct",
        f"- Affected Layers: {scope}",
    ]
    for name, value in fields.items():
        label = re.sub(
            r"(^|_)([a-z])",
            lambda match: f" {match.group(2).upper()}" if match.group(1) else match.group(2).upper(),
            name,
        )
        lines.append(f"- {label}: {value}")
    return "\n".join(lines) + "\n\n"


def _register(scale: int, seed: int) -> str:
    blocks = [
        _decision_block(identifier, f"Gold decision {identifier}", text, scope, "2026-01-01")
        for identifier, text, scope in GOLD_DECISIONS
    ]
    for ordinal in range(scale - len(blocks)):
        identifier = _identifier(seed, ordinal)
        scope = "global" if ordinal == 4 or ordinal % 2 == 0 else "projects/synthetic"
        fields: dict[str, str] = {}
        if ordinal == 1:
            fields["supersedes"] = _identifier(seed, 0)
        if ordinal in {2, 3}:
            fields["conflict_key"] = f"rb03-conflict-{seed}"
            fields["applies_when"] = "synthetic owner review is pending"
        if ordinal == 5:
            fields["applies_when"] = "synthetic deployment tier is confirmed"
        blocks.append(
            _decision_block(
                identifier,
                f"Synthetic generated decision {ordinal:05d}",
                f"Seed {seed} retains benchmark rule {ordinal:05d}.",
                scope,
                f"2026-01-{(ordinal % 28) + 1:02d}",
                **fields,
            )
        )
    return "# Synthetic benchmark register\n\n" + "".join(blocks)


def _config_digest(scale: int, seed: int, project: str) -> str:
    payload = {"generator_version": GENERATOR_VERSION, "project": project, "scale": scale, "seed": seed}
    return _digest(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())


def _validate_manifest(output: Path) -> dict[str, object]:
    try:
        manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        register = (output / "decision-register.md").read_bytes()
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BenchmarkGenerationError(f"generated pack is incomplete: {exc}") from exc
    decisions = parse_register(register.decode("utf-8"))
    if len(decisions) != manifest["actual_count"]:
        raise BenchmarkGenerationError("manifest decision count does not match register")
    if _digest(register) != manifest["register_sha256"]:
        raise BenchmarkGenerationError("manifest register digest does not match register")
    for name, expected in manifest["index_sha256"].items():
        if _digest((output / name).read_bytes()) != expected:
            raise BenchmarkGenerationError(f"manifest index digest does not match {name}")
    return manifest


def generate_pack(
    output: Path,
    *,
    scale: int,
    seed: int,
    project: str = "synthetic",
    canonical_root: Path | None = None,
    allow_existing: bool = False,
) -> GeneratedPack:
    if scale not in SCALES:
        raise BenchmarkGenerationError(f"scale must be one of {SCALES}")
    if not isinstance(seed, int):
        raise BenchmarkGenerationError("seed must be an integer")
    if not ID_RE.fullmatch(project):
        raise BenchmarkGenerationError("project must match the canonical identifier pattern")
    target = _safe_output(output, canonical_root, allow_existing)
    register = _register(scale, seed).encode()
    decisions = parse_register(register.decode("utf-8"))
    if len(decisions) != scale:
        raise BenchmarkGenerationError("generated decision count does not match scale")
    indexes = _indexes(register.decode())
    for name, content in {"decision-register.md": register.decode(), **indexes}.items():
        (target / name).write_text(content, encoding="utf-8", newline="\n")
    marker = target / OWNERSHIP_MARKER
    marker.write_text(f"{GENERATOR_VERSION}\n", encoding="utf-8", newline="\n")
    manifest: dict[str, object] = {
        "generator_version": GENERATOR_VERSION,
        "project": project,
        "seed": seed,
        "scale": scale,
        "actual_count": len(decisions),
        "decision_ids": [decision.decision_id for decision in decisions],
        "configuration_sha256": _config_digest(scale, seed, project),
        "register_sha256": _digest(register),
        "index_sha256": {name: _digest(content.encode()) for name, content in indexes.items()},
    }
    (target / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return GeneratedPack(target, _validate_manifest(target))


def clean_pack(output: Path) -> None:
    if output.exists() and output.is_dir() and (output / OWNERSHIP_MARKER).is_file():
        shutil.rmtree(output)
