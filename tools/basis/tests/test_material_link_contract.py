from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.cloud_cutting import CuttingClient  # noqa: E402
from src.cfrn import project_to_cfrn_json  # noqa: E402
from src.generators import generate_from_paramspec  # noqa: E402
from src.materials import list_sheet_decors, resolve_project_materials  # noqa: E402
from src.material_link_contract import (  # noqa: E402
    MaterialLinkContractError,
    audit_sheet_link_result,
    plan_sheet_links,
    serialize_link_payload,
    sheet_manifest,
)


FIXTURE = json.loads(
    (ROOT / "tests" / "fixtures" / "material_link_contract.json").read_text(encoding="utf-8")
)


def test_fixture_is_explicitly_synthetic_and_never_claims_real_basis_evidence():
    assert FIXTURE["scope"] == "synthetic-offline-contract-only"
    assert FIXTURE["realBasisVerified"] is False
    assert all("__FIXTURE_" in item["name"] for item in FIXTURE["cfrn"]["table"]["materials"])


def test_sheet_manifest_keeps_name_article_and_geometry_together():
    assert sheet_manifest(FIXTURE["cfrn"]) == [{
        "material_index": 0,
        "source_name": "__FIXTURE_CAD_SHEET__",
        "article": "__FIXTURE_LOCAL_ARTICLE__",
        "panel_count": 1,
        "sheets": [{"width": 720.0, "height": 560.0, "thickness": 16.0}],
    }]


def test_sheet_manifest_uses_a_real_local_base_article_without_hardcoding_one():
    local = list_sheet_decors("", thickness=16, limit=1)[0]
    spec = json.loads((ROOT / "paramspecs" / "tz_tumba_dokumenty.json").read_text(encoding="utf-8"))
    spec["materials"]["board_article"] = str(local["article"])
    project = generate_from_paramspec(spec)
    project["material_refs"] = resolve_project_materials(project)
    manifest = sheet_manifest(project_to_cfrn_json(project))
    assert any(item["article"] == str(local["article"]) for item in manifest)
    assert all(item["panel_count"] > 0 for item in manifest)


def test_confirmed_name_article_sheet_mapping_builds_exact_openapi_payload():
    plan = plan_sheet_links(
        FIXTURE["cadModelMaterials"], FIXTURE["cfrn"], FIXTURE["confirmations"]
    )
    assert plan["ready"] is True
    assert plan["payload"] == [{
        "originalMaterialFullName": "__FIXTURE_CAD_SHEET__",
        "materialType": 0,
        "linkedMaterialFullName": "__FIXTURE_CONFIRMED_MATBASE_TARGET__",
    }]
    assert plan["decisions"][0]["article"] == "__FIXTURE_LOCAL_ARTICLE__"
    assert plan["decisions"][0]["expected_sheets"] == FIXTURE["confirmations"][0]["sheets"]


def test_unique_normalized_source_name_is_the_only_fallback():
    plan = plan_sheet_links(
        ["  __fixture_cad_sheet__  "], FIXTURE["cfrn"], FIXTURE["confirmations"]
    )
    assert plan["ready"] is True
    assert plan["decisions"][0]["match"] == "unique_normalized_name"
    assert plan["payload"][0]["originalMaterialFullName"] == "__fixture_cad_sheet__"


def test_missing_matbase_confirmation_blocks_instead_of_inventing_target_name():
    plan = plan_sheet_links(FIXTURE["cadModelMaterials"], FIXTURE["cfrn"], [])
    assert plan["ready"] is False
    assert plan["payload"] == []
    assert plan["decisions"][0]["status"] == "blocked"
    assert "not externally confirmed" in plan["decisions"][0]["reason"]


def test_article_mismatch_blocks_mapping():
    confirmation = dict(FIXTURE["confirmations"][0], article="__OTHER_FIXTURE_ARTICLE__")
    plan = plan_sheet_links(FIXTURE["cadModelMaterials"], FIXTURE["cfrn"], [confirmation])
    assert plan["payload"] == []
    assert "article differs" in plan["decisions"][0]["reason"]


def test_duplicate_exact_cfrn_names_with_different_articles_are_ambiguous():
    cfrn = json.loads(json.dumps(FIXTURE["cfrn"]))
    cfrn["table"]["materials"].append({
        "name": "__FIXTURE_CAD_SHEET__",
        "art": "__OTHER_FIXTURE_ARTICLE__",
    })
    cfrn["table"]["objects"].append({
        "objType": 2,
        "materialIndex": 1,
        "thickness": 16,
        "contour": {"size": {"x": 400, "y": 300}},
    })
    plan = plan_sheet_links(FIXTURE["cadModelMaterials"], cfrn, FIXTURE["confirmations"])
    assert plan["ready"] is False
    assert plan["payload"] == []
    assert plan["decisions"] == [{
        "source_name": "__FIXTURE_CAD_SHEET__",
        "status": "unresolved",
        "reason": "no unique exact/normalized sheet material in exported CFRN",
    }]


@pytest.mark.parametrize("field,value", [
    ("article", None),
    ("sheets", []),
    ("sheets", [{"height": "2800", "width": 2070, "count": 1,
                  "production": True, "materialType": 1}]),
])
def test_confirmation_requires_strict_article_and_expected_sheet_schema(field, value):
    confirmation = dict(FIXTURE["confirmations"][0], **{field: value})
    with pytest.raises(MaterialLinkContractError):
        plan_sheet_links(FIXTURE["cadModelMaterials"], FIXTURE["cfrn"], [confirmation])


def test_transport_rejects_articles_extra_keys_and_invalid_types():
    valid = [{
        "originalMaterialFullName": "source",
        "materialType": 0,
        "linkedMaterialFullName": "target",
    }]
    assert serialize_link_payload(valid) == valid
    with pytest.raises(MaterialLinkContractError, match="extra=.*article"):
        serialize_link_payload([{**valid[0], "article": "not-in-openapi"}])
    with pytest.raises(MaterialLinkContractError, match="0..5"):
        serialize_link_payload([{**valid[0], "materialType": 6}])


def test_cutting_client_posts_only_validated_payload(monkeypatch):
    calls = []
    client = CuttingClient(api_key="offline-fixture", base_url="https://fixture.invalid")
    monkeypatch.setattr(client, "_post", lambda suffix, **kwargs: calls.append((suffix, kwargs)) or None)
    client.set_link_materials(17, [{
        "originalMaterialFullName": "source",
        "materialType": 0,
        "linkedMaterialFullName": "target",
    }])
    assert calls == [("/cad-models/17/set-link-materials", {"json": [{
        "originalMaterialFullName": "source",
        "materialType": 0,
        "linkedMaterialFullName": "target",
    }]})]


def test_cutting_client_does_not_retry_remote_post_without_live_idempotency_evidence(monkeypatch):
    calls = []
    client = CuttingClient(api_key="offline-fixture", base_url="https://fixture.invalid")

    def fail_once(suffix, **kwargs):
        calls.append((suffix, kwargs))
        raise TimeoutError("synthetic timeout with unknown remote outcome")

    monkeypatch.setattr(client, "_post", fail_once)
    with pytest.raises(TimeoutError, match="unknown remote outcome"):
        client.set_link_materials(17, [{
            "originalMaterialFullName": "source",
            "materialType": 0,
            "linkedMaterialFullName": "target",
        }])
    assert len(calls) == 1


def test_cutting_client_exposes_result_evidence_endpoints(monkeypatch):
    calls = []
    client = CuttingClient(api_key="offline-fixture", base_url="https://fixture.invalid")
    monkeypatch.setattr(client, "_get", lambda suffix, **kwargs: calls.append((suffix, kwargs)) or [])
    assert client.cutting_materials(23) == []
    assert client.cutted_materials(23) == []
    assert calls == [
        ("/cutting-materials", {"orderId": 23}),
        ("/orders/23/cutted-materials", {}),
    ]


def test_offline_result_audit_checks_matbase_sheet_and_production_but_keeps_blocker():
    plan = plan_sheet_links(
        FIXTURE["cadModelMaterials"], FIXTURE["cfrn"], FIXTURE["confirmations"]
    )
    audit = audit_sheet_link_result(
        plan, FIXTURE["cuttingMaterials"], FIXTURE["cuttedMaterials"]
    )
    assert audit["offline_contract_passed"] is True
    assert audit["real_environment_verified"] is False
    assert "licensed Basis/Cutting" in audit["blocker"]
    assert audit["results"] == [{
        "source_name": "__FIXTURE_CAD_SHEET__",
        "linked_name": "__FIXTURE_CONFIRMED_MATBASE_TARGET__",
        "unique_result": True,
        "matbase_reference_present": True,
        "article_matches": True,
        "sheet_mapping_matches": True,
        "cutting_result_present": True,
    }]


def test_result_audit_cannot_pass_when_plan_is_not_fully_ready():
    ready_plan = plan_sheet_links(
        FIXTURE["cadModelMaterials"], FIXTURE["cfrn"], FIXTURE["confirmations"]
    )
    for broken_plan in (
        {**ready_plan, "ready": False},
        {**ready_plan, "decisions": ready_plan["decisions"] + [{
            "source_name": "unlinked", "status": "blocked", "reason": "fixture",
        }]},
        {**ready_plan, "decisions": []},
    ):
        audit = audit_sheet_link_result(
            broken_plan, FIXTURE["cuttingMaterials"], FIXTURE["cuttedMaterials"]
        )
        assert audit["offline_contract_passed"] is False


@pytest.mark.parametrize("field,value", [
    ("inMaterialBaseId", 0),
    ("items", []),
])
def test_result_audit_fails_closed_when_matbase_or_sheet_evidence_is_missing(field, value):
    plan = plan_sheet_links(
        FIXTURE["cadModelMaterials"], FIXTURE["cfrn"], FIXTURE["confirmations"]
    )
    material = dict(FIXTURE["cuttingMaterials"][0], **{field: value})
    audit = audit_sheet_link_result(plan, [material], FIXTURE["cuttedMaterials"])
    assert audit["offline_contract_passed"] is False


def test_result_audit_fails_closed_on_malformed_numeric_evidence():
    plan = plan_sheet_links(
        FIXTURE["cadModelMaterials"], FIXTURE["cfrn"], FIXTURE["confirmations"]
    )
    material = json.loads(json.dumps(FIXTURE["cuttingMaterials"][0]))
    material["items"][0]["count"] = "not-a-number"
    cutted = json.loads(json.dumps(FIXTURE["cuttedMaterials"]))
    cutted[0]["statistic"]["boardsCount"] = None
    cutted[0]["statistic"]["countPlate"] = "not-a-number"
    audit = audit_sheet_link_result(plan, [material], cutted)
    assert audit["offline_contract_passed"] is False


@pytest.mark.parametrize("mutation", [
    lambda material, cutted: material.update(article="__OTHER_FIXTURE_ARTICLE__"),
    lambda material, cutted: material.update(inMaterialBaseId="1"),
    lambda material, cutted: material["items"][0].update(height="2800"),
    lambda material, cutted: material["items"][0].update(height=2440),
    lambda material, cutted: material["items"][0].update(production=1),
    lambda material, cutted: material["items"][0].update(materialType="1"),
    lambda material, cutted: cutted[0].update(article="__OTHER_FIXTURE_ARTICLE__"),
    lambda material, cutted: cutted[0].update(inMaterialBaseId="1"),
    lambda material, cutted: cutted[0].update(height=2440),
    lambda material, cutted: cutted[0]["statistic"].update(boardsCount="1", countPlate=0),
])
def test_result_audit_requires_exact_article_sheet_mapping_and_strict_dto_types(mutation):
    plan = plan_sheet_links(
        FIXTURE["cadModelMaterials"], FIXTURE["cfrn"], FIXTURE["confirmations"]
    )
    material = json.loads(json.dumps(FIXTURE["cuttingMaterials"][0]))
    cutted = json.loads(json.dumps(FIXTURE["cuttedMaterials"]))
    mutation(material, cutted)
    audit = audit_sheet_link_result(plan, [material], cutted)
    assert audit["offline_contract_passed"] is False
