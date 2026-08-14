from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from context_library_core.retrieval_corpus import CorpusValidationError, validate_corpus

ROOT = Path(__file__).parents[2]
CORPUS = ROOT / "contracts/fixtures/retrieval-benchmark-corpus-v1.json"


def load() -> dict:
    return json.loads(CORPUS.read_text(encoding="utf-8"))


def write_mutation(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / "corpus.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_synthetic_corpus_covers_required_dimensions_offline():
    result = validate_corpus(CORPUS)

    assert result["case_count"] == 8
    assert "supersession" in result["dimensions"]
    assert "insufficient-budget" in result["dimensions"]


def test_validator_is_a_stable_nonzero_cli_boundary(tmp_path):
    result = subprocess.run(
        [sys.executable, "scripts/validate_retrieval_corpus.py", str(CORPUS)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert '"case_count": 8' in result.stdout
    assert result.stderr == ""


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("duplicate_case", "duplicate or invalid case_id"),
        ("dangling_label", "task and gold classifications differ"),
        ("overlapping_classification", "multiple classifications"),
        ("unsupported_version", "unsupported corpus schema or version"),
        ("unknown_field", "unknown fields"),
    ],
)
def test_mutated_corpus_fails_closed(tmp_path, mutation, expected):
    payload = load()
    if mutation == "duplicate_case":
        payload["cases"].append(copy.deepcopy(payload["cases"][0]))
    elif mutation == "dangling_label":
        payload["cases"][0]["gold"]["labels"][0]["decision_id"] = "missing-decision"
    elif mutation == "overlapping_classification":
        payload["cases"][0]["task"]["excluded_decision_ids"].append(
            payload["cases"][0]["task"]["expected_operative_decision_ids"][0]
        )
    elif mutation == "unsupported_version":
        payload["schema_version"] = 2
    elif mutation == "unknown_field":
        payload["cases"][0]["unexpected"] = True

    with pytest.raises(CorpusValidationError, match=expected):
        validate_corpus(write_mutation(tmp_path, payload))


def test_validator_does_not_modify_input(tmp_path):
    path = write_mutation(tmp_path, load())
    before = path.read_bytes()

    validate_corpus(path)

    assert path.read_bytes() == before
