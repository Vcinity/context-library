from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from context_library_core.canonical import (
    CanonicalParseError,
    discover_packs,
    parse_register,
    parse_register_bytes,
    resolve_pack,
    validate_projection_compatibility,
    weakest_provenance,
)
from context_library_core.contracts import SCHEMA_FAMILIES, ContextPolicy, ContextResolution
from context_library_core.maintainer_contracts import (
    Candidate,
    HarvestBatch,
    Observation,
    SourceEnvelope,
)

ROOT = Path(__file__).parents[2]
FIXTURES = ROOT / "contracts/fixtures"


@pytest.mark.parametrize("name", ["required", "optional", "disabled"])
def test_context_policy_contract_accepts_explicit_states(name):
    payload = json.loads((FIXTURES / f"context-policy-{name}.json").read_text())
    policy = ContextPolicy.model_validate(payload)
    assert policy.context_requirement == name


def test_context_policy_contract_rejects_unknown_version_and_requirement():
    payload = json.loads((FIXTURES / "context-policy-invalid.json").read_text())
    with pytest.raises(ValidationError):
        ContextPolicy.model_validate(payload)


@pytest.mark.parametrize(
    ("name", "availability", "requirement"),
    [
        ("missing", "missing", "required"),
        ("ambiguous", "ambiguous", "required"),
        ("undetermined", "missing", "undetermined"),
    ],
)
def test_context_resolution_fixtures(name, availability, requirement):
    payload = json.loads((FIXTURES / f"context-resolution-{name}.json").read_text())
    resolution = ContextResolution.model_validate(payload)
    assert resolution.availability == availability
    assert resolution.requirement == requirement


def test_authoritative_parser_handles_all_provenance_and_synthesis():
    decisions = parse_register((FIXTURES / "register-positive.md").read_text())
    assert [decision.provenance for decision in decisions] == [
        "explicit",
        "inferred",
        "assumed",
    ]
    synthesized = parse_register((FIXTURES / "register-synthesized.md").read_text())
    assert synthesized[-1].source_ids == ("source-explicit", "source-assumed")
    assert (
        weakest_provenance(
            next(decision.provenance for decision in synthesized if decision.decision_id == source)
            for source in synthesized[-1].source_ids
        )
        == "assumed"
    )


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
def test_authoritative_parser_accepts_supported_line_endings(newline):
    content = (FIXTURES / "register-positive.md").read_text().replace("\n", newline)
    assert len(parse_register(content)) == 3


def test_authoritative_parser_rejects_invalid_utf8_and_anchorless_content():
    with pytest.raises(CanonicalParseError, match="UTF-8"):
        parse_register_bytes(b"# register\n\xff")
    with pytest.raises(CanonicalParseError, match="decision-like content"):
        parse_register((FIXTURES / "register-anchorless.md").read_text())


def test_legacy_and_target_locations_are_one_logical_pack(tmp_path):
    legacy = tmp_path / "decision-artifacts"
    target = tmp_path / "projects/legacy"
    legacy.mkdir(parents=True)
    target.mkdir(parents=True)
    content = (FIXTURES / "register-positive.md").read_bytes()
    (legacy / "decision-register.md").write_bytes(content)
    (target / "decision-register.md").write_bytes(content)
    packs = discover_packs(tmp_path)
    assert len(packs) == 1
    assert packs[0].project == "legacy"
    assert packs[0].location == "projects/legacy"
    assert packs[0].compatibility_locations == ("decision-artifacts",)


@pytest.mark.parametrize(
    ("relative", "location"),
    [
        ("layouts/legacy", "decision-artifacts"),
        ("layouts/project-pack", "projects/legacy"),
    ],
)
def test_shared_layout_fixtures_are_discoverable(relative, location):
    packs = discover_packs(FIXTURES / relative)
    assert [(pack.project, pack.location) for pack in packs] == [("legacy", location)]


def test_sole_legacy_flat_pack_accepts_an_explicit_historical_alias():
    packs = discover_packs(FIXTURES / "layouts/legacy")
    selected = resolve_pack(packs, "previous-project")
    assert selected is not None
    assert selected.location == "decision-artifacts"


def test_legacy_alias_does_not_override_multiple_project_selection(tmp_path):
    legacy = tmp_path / "decision-artifacts"
    current = tmp_path / "projects/demo"
    legacy.mkdir(parents=True)
    current.mkdir(parents=True)
    content = (FIXTURES / "register-positive.md").read_bytes()
    (legacy / "decision-register.md").write_bytes(content)
    (current / "decision-register.md").write_bytes(content)

    assert resolve_pack(discover_packs(tmp_path), "previous-project") is None


def test_parser_sanity_mutation_would_fail_critical_fixture():
    source = (FIXTURES / "register-positive.md").read_text()
    broken = source.replace("- Provenance: explicit", "- Authority: explicit", 1)
    with pytest.raises(CanonicalParseError, match="provenance"):
        parse_register(broken)


def test_machine_contracts_emit_named_schema_families():
    models = (
        (
            SourceEnvelope,
            {
                "external_id": "S-1",
                "source_type": "ticket",
                "uri": "ticket://S-1",
                "title": "Source",
                "retrieved_at": "2026-07-28T00:00:00Z",
                "content_format": "text",
                "content": "Evidence",
            },
            "context-library/source-envelope",
        ),
        (
            Observation,
            {
                "source_id": "src-1",
                "kind": "directive",
                "excerpt": "Evidence",
                "location": "body",
                "agent_interpretation": "Directive",
            },
            "context-library/observation",
        ),
    )
    for model, payload, family in models:
        serialized = model.model_validate(payload).model_dump(mode="json", by_alias=True)
        assert serialized["schema"] == family
        assert serialized["schema_version"] in SCHEMA_FAMILIES[family]
    assert "context-library/candidate" in SCHEMA_FAMILIES
    assert Candidate.model_json_schema()["properties"]["schema"]["const"] == "context-library/candidate"


def test_harvest_batch_is_redacted_proposal_only_and_references_local_sources():
    source = SourceEnvelope.model_validate(
        {
            "external_id": "source-1",
            "source_type": "chat",
            "uri": "https://example.invalid/source-1",
            "title": "Synthetic discussion",
            "retrieved_at": "2026-08-03T00:00:00Z",
            "content_format": "text",
            "content": "Use the narrow interface.",
        }
    )
    observation = Observation.model_validate(
        {
            "source_id": "source-1",
            "kind": "directive",
            "excerpt": "Use the narrow interface.",
            "location": "message-1",
            "agent_interpretation": "Synthetic directive.",
        }
    )
    batch = HarvestBatch.model_validate(
        {
            "batch_id": "batch-1",
            "idempotency_key": "harvest-1",
            "project": "synthetic-project",
            "produced_at": "2026-08-03T00:00:00Z",
            "sources": [source.model_dump(by_alias=True)],
            "observations": [observation.model_dump(by_alias=True)],
        }
    )
    assert batch.redacted is True
    assert batch.canonical_write is False
    assert batch.schema_id == "context-library/harvest-batch"


def test_harvest_batch_rejects_unredacted_or_cross_batch_references():
    base = {
        "batch_id": "batch-1",
        "idempotency_key": "harvest-1",
        "project": "synthetic-project",
        "produced_at": "2026-08-03T00:00:00Z",
        "redacted": False,
    }
    with pytest.raises(ValidationError, match="redacted"):
        HarvestBatch.model_validate(base)

    source_with_secret_span = {
        "external_id": "source-secret",
        "source_type": "chat",
        "uri": "https://example.invalid/source-secret",
        "title": "Synthetic secret source",
        "retrieved_at": "2026-08-03T00:00:00Z",
        "content_format": "text",
        "content": "Evidence",
        "secret_spans": [{"start": 0, "end": 1}],
    }
    with pytest.raises(ValidationError, match="already be redacted"):
        HarvestBatch.model_validate({**base, "redacted": True, "sources": [source_with_secret_span]})

    source = {
        "external_id": "source-1",
        "source_type": "chat",
        "uri": "https://example.invalid/source-1",
        "title": "Synthetic discussion",
        "retrieved_at": "2026-08-03T00:00:00Z",
        "content_format": "text",
        "content": "Evidence",
    }
    observation = {
        "source_id": "source-not-in-batch",
        "kind": "context",
        "excerpt": "Evidence",
        "location": "message-1",
        "agent_interpretation": "Synthetic context.",
    }
    with pytest.raises(ValidationError, match="source in the batch"):
        HarvestBatch.model_validate({**base, "redacted": True, "sources": [source], "observations": [observation]})


def test_synthesis_rejects_provenance_laundering_and_cycles():
    source = (FIXTURES / "register-synthesized.md").read_text()
    with pytest.raises(CanonicalParseError, match="weakest source provenance assumed"):
        parse_register(
            source.replace(
                "- Decision: Preserve evidence while confirming scope.\n- Provenance: assumed",
                "- Decision: Preserve evidence while confirming scope.\n- Provenance: explicit",
            )
        )
    cycle = source.replace(
        "- Sources: `source-explicit`, `source-assumed`",
        "- Sources: `synthesized-guidance`, `source-assumed`",
    )
    with pytest.raises(CanonicalParseError, match="synthesis cycle"):
        parse_register(cycle)


def test_authoritative_parser_uses_record_category_and_rejects_supersession_cycle():
    categorized = parse_register(
        '# Register\n\n<a id="category"></a>\n### Category\n'
        "- Category: authority\n- Decision: Preserve category.\n- Provenance: explicit\n"
    )
    assert categorized[0].category == "authority"
    with pytest.raises(CanonicalParseError, match="supersession cycle"):
        validate_projection_compatibility((FIXTURES / "register-supersession-cycle.md").read_text())


def test_pack_discovery_rejects_symlinked_project_and_legacy_parents(tmp_path):
    outside = tmp_path / "outside"
    pack = outside / "demo"
    pack.mkdir(parents=True)
    (pack / "decision-register.md").write_text((FIXTURES / "register-positive.md").read_text())
    library = tmp_path / "library"
    library.mkdir()
    (library / "projects").symlink_to(outside, target_is_directory=True)
    assert discover_packs(library) == ()
    (library / "projects").unlink()
    (library / "decision-artifacts").symlink_to(pack, target_is_directory=True)
    assert discover_packs(library) == ()
