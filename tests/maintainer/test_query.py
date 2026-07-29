import json

import pytest
from typer.testing import CliRunner

from context_library_maintainer.cli import app
from context_library_maintainer.query import query_library, read_library

REGISTER = """# Decision Register

## User Interface

<a id="ui-react"></a>
### Use React
- Date: 2026-01-01
- Decisionmaker: Product Owner
- Decision: Use React for the product UI.
- Rationale: It matches the established stack.
- Provenance: explicit
- Evidence:
  - https://example.invalid/messages/1 Product Owner confirmed React.

<a id="ui-old"></a>
### Earlier UI direction
- Date: 2025-01-01
- Decisionmaker: Product Owner
- Decision: Use the earlier UI direction.
- Rationale: It was the prior choice.
- Provenance: inferred
- Evidence:
  - ticket://UI-1 Earlier direction.

<a id="ui-new"></a>
### Replace earlier direction
- Date: 2026-02-01
- Decisionmaker: Product Owner
- Decision: Replace the earlier direction.
- Rationale: Requirements changed.
- Provenance: explicit
- Supersedes: `ui-old`
- Evidence:
  - ticket://UI-2 Replacement direction.

<a id="ui-sensitive"></a>
### Never expose token=subject-secret
- Date: 2026-03-01
- Decisionmaker: password=owner-secret
- Decision: Rotate api_key=decision-secret.
- Rationale: Remove token=rationale-secret.
- Provenance: explicit
- Evidence:
  - javascript://example.invalid/?token=uri-secret password=label-secret
"""


def library(tmp_path):
    root = tmp_path / "library"
    project = root / "projects" / "demo"
    project.mkdir(parents=True)
    (project / "decision-register.md").write_text(REGISTER)
    return root


def legacy_library(tmp_path):
    root = tmp_path / "library"
    pack = root / "decision-artifacts"
    pack.mkdir(parents=True)
    (pack / "decision-register.md").write_text(REGISTER)
    return root


def test_sole_legacy_flat_pack_accepts_an_explicit_historical_alias(tmp_path):
    result = read_library(legacy_library(tmp_path), "previous-project")
    assert result["project"] == "previous-project"
    assert result["records"][0]["decision_id"] == "ui-react"


def test_structured_query_is_deterministic_and_marks_supersession(tmp_path):
    root = library(tmp_path)
    first = read_library(root, "demo")
    second = read_library(root, "demo")
    assert first == second
    assert len(first["library_digest"]) == 64
    records = {item["decision_id"]: item for item in first["records"]}
    assert records["ui-react"]["status"] == "authoritative"
    assert records["ui-old"]["status"] == "superseded"
    assert records["ui-old"]["superseded_by"] == ["ui-new"]
    assert records["ui-new"]["supersedes"] == ["ui-old"]
    assert records["ui-react"]["sources"][0]["uri"].startswith("https://")


def test_query_search_filter_detail_and_pagination(tmp_path):
    root = library(tmp_path)
    phrase = query_library(root, "demo", query="established stack")
    assert [item["decision_id"] for item in phrase["items"]] == ["ui-react"]
    inferred = query_library(root, "demo", status="superseded")
    assert [item["decision_id"] for item in inferred["items"]] == ["ui-old"]
    category = query_library(root, "demo", category="User Interface", page_size=2)
    assert category["total"] == 4
    assert category["next_page"] == 2
    detail = query_library(root, "demo", decision_id="ui-new")
    assert detail["decision"]["decisionmaker"] == "Product Owner"
    digest = query_library(root, "demo", digest_only=True)
    assert set(digest) == {"project", "library_digest", "publication_revision"}


@pytest.mark.parametrize(
    "needle",
    [
        "ui-react",
        "Use React",
        "established stack",
        "https://example.invalid/messages/1",
        "Product Owner",
        "2026-01-01",
    ],
)
def test_query_searches_every_normalized_record_field(tmp_path, needle):
    result = query_library(library(tmp_path), "demo", query=needle)
    assert any(item["decision_id"] == "ui-react" for item in result["items"])


def test_query_cli_emits_versioned_json_envelope(tmp_path):
    root = library(tmp_path)
    result = CliRunner().invoke(
        app,
        [
            "query",
            "--library-root",
            str(root),
            "--project",
            "demo",
            "--q",
            "React",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["status"] == "ok"
    assert payload["data"]["items"][0]["decision_id"] == "ui-react"
    assert payload["data"]["page_size"] == 25


def test_query_redacts_secret_like_canonical_metadata(tmp_path):
    detail = query_library(library(tmp_path), "demo", decision_id="ui-sensitive")
    serialized = json.dumps(detail)
    for secret in (
        "subject-secret",
        "owner-secret",
        "decision-secret",
        "rationale-secret",
        "uri-secret",
        "label-secret",
    ):
        assert secret not in serialized
    source = detail["decision"]["sources"][0]
    assert source["redacted"] is True
    assert source["secret_state"] == "redacted"


def test_query_rejects_missing_decision_and_unbounded_page(tmp_path):
    root = library(tmp_path)
    result = CliRunner().invoke(
        app,
        [
            "query",
            "--library-root",
            str(root),
            "--project",
            "demo",
            "--decision-id",
            "missing",
            "--json",
        ],
    )
    assert result.exit_code == 2
    assert json.loads(result.stdout)["errors"][0]["code"] == "decision-not-found"
