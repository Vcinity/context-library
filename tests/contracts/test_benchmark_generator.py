from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from context_library_core.benchmark_generator import (
    SCALES,
    BenchmarkGenerationError,
    generate_pack,
)
from context_library_core.canonical import parse_register


def files(path: Path) -> dict[str, bytes]:
    return {item.name: item.read_bytes() for item in path.iterdir() if item.is_file()}


@pytest.mark.parametrize("scale", SCALES)
def test_all_required_scales_parse_and_preserve_gold(tmp_path, scale):
    pack = generate_pack(tmp_path / f"pack-{scale}", scale=scale, seed=17)
    decisions = parse_register((pack.output / "decision-register.md").read_text(encoding="utf-8"))

    assert len(decisions) == scale
    assert [decision.decision_id for decision in decisions] == pack.manifest["decision_ids"]
    assert {"rb03-gold-interface", "rb03-gold-review"} <= set(pack.manifest["decision_ids"])
    assert (pack.output / "manifest.json").is_file()
    assert (pack.output / "supersession-index.md").is_file()


def test_same_seed_is_byte_identical_and_new_seed_changes_digest(tmp_path):
    first = generate_pack(tmp_path / "first", scale=100, seed=17, project="synthetic")
    second = generate_pack(tmp_path / "second", scale=100, seed=17, project="synthetic")
    changed = generate_pack(tmp_path / "changed", scale=100, seed=18, project="synthetic")

    assert files(first.output) == files(second.output)
    assert first.manifest["register_sha256"] != changed.manifest["register_sha256"]
    assert changed.manifest["actual_count"] == 100


def test_generator_rejects_unsafe_or_invalid_outputs(tmp_path):
    with pytest.raises(BenchmarkGenerationError, match="scale"):
        generate_pack(tmp_path / "bad-scale", scale=11, seed=1)

    occupied = tmp_path / "occupied"
    occupied.mkdir()
    (occupied / "unrelated.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(BenchmarkGenerationError, match="not empty"):
        generate_pack(occupied, scale=10, seed=1)

    canonical = tmp_path / "canonical"
    with pytest.raises(BenchmarkGenerationError, match="inside the canonical root"):
        generate_pack(canonical / "generated", scale=10, seed=1, canonical_root=canonical)


def test_cli_emits_manifest_and_fails_for_traversal(tmp_path):
    output = tmp_path / "cli-pack"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/generate_retrieval_scale.py",
            "--scale",
            "10",
            "--seed",
            "23",
            "--output",
            str(output),
        ],
        cwd=Path(__file__).parents[2],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert '"actual_count": 10' in result.stdout
    assert result.stderr == ""
