from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.paramspec_migration import audit_paramspec_v1_catalogs
from src.paramspec_versioning import (
    PARAMSPEC_V1,
    canonical_paramspec_v1_for_activation,
    read_paramspec_v1,
    strict_paramspec_v1_for_write,
)
from src.studio import (
    _migrate_catalog_identity,
    _production_gate_error,
    _snapshot_version,
    build_payload,
)


ROOT = Path(__file__).resolve().parent.parent


def _fixture() -> dict:
    return json.loads(
        (ROOT / "paramspecs" / "wardrobe_demo.json").read_text(encoding="utf-8")
    )


def test_tolerant_read_separates_catalog_and_reports_unknown_extensions() -> None:
    raw = _fixture()
    raw["catalog"] = {
        "creator_user_id": "user-1",
        "responsible_user_id": "user-2",
        "author": "Автор",
        "responsible": "Ответственный",
    }
    raw["future_top_level"] = {"enabled": True}
    raw["dimensions"]["future_dimension"] = 42

    envelope = read_paramspec_v1(raw)

    assert envelope.schema_version == PARAMSPEC_V1
    assert envelope.catalog is not None
    assert envelope.catalog.creator_user_id == "user-1"
    assert "catalog" not in envelope.payload_dict()
    assert envelope.unknown_fields == (
        "dimensions.future_dimension",
        "future_top_level",
    )
    assert envelope.canonical_changed is True
    canonical = envelope.canonical_document()
    assert canonical["catalog"]["responsible_user_id"] == "user-2"
    assert "future_top_level" not in canonical
    assert "future_dimension" not in canonical["dimensions"]


def test_tolerant_read_does_not_tolerate_invalid_known_fields_or_fake_v2() -> None:
    invalid = _fixture()
    invalid["dimensions"]["width"] = 10
    with pytest.raises(ValidationError):
        read_paramspec_v1(invalid)

    fake_v2 = _fixture()
    fake_v2["schemaVersion"] = "paramspec-v2"
    with pytest.raises(ValidationError):
        read_paramspec_v1(fake_v2)


def test_strict_write_rejects_extensions_and_is_idempotent() -> None:
    raw = _fixture()
    first = strict_paramspec_v1_for_write(raw)
    second = strict_paramspec_v1_for_write(first)
    assert second == first

    raw["future_top_level"] = True
    with pytest.raises(ValidationError):
        strict_paramspec_v1_for_write(raw)


def test_activation_normalizes_extensions_but_rejects_invalid_known_fields() -> None:
    raw = _fixture()
    raw["catalog"] = {
        "creator_user_id": "owner-1",
        "responsible_user_id": "designer-1",
    }
    raw["future_top_level"] = True

    active = canonical_paramspec_v1_for_activation(raw)

    assert "future_top_level" not in active
    assert active["catalog"] == raw["catalog"]
    assert strict_paramspec_v1_for_write(active) == active

    raw["dimensions"]["width"] = 10
    with pytest.raises(ValidationError):
        canonical_paramspec_v1_for_activation(raw)


def test_engine_tolerant_read_reports_extension_without_feeding_geometry() -> None:
    raw = _fixture()
    raw["future_top_level"] = True
    raw["dimensions"]["future_dimension"] = 42

    payload = build_payload(raw)

    assert payload["ok"] is True
    assert payload["paramspec_read"]["unknown_fields"] == [
        "dimensions.future_dimension",
        "future_top_level",
    ]
    assert payload["paramspec_read"]["canonical_changed"] is True


def test_catalog_metadata_stays_in_revision_guard_but_out_of_geometry() -> None:
    raw = _fixture()
    raw["catalog"] = {
        "creator_user_id": "user-1",
        "responsible_user_id": "user-2",
    }

    payload = build_payload(raw)

    assert payload["ok"] is True
    assert _production_gate_error(raw, payload["revision"]) is None


def test_dry_run_audits_fixture_tenant_and_revisions_without_writes(
    tmp_path: Path,
) -> None:
    legacy = tmp_path / "legacy"
    tenant = tmp_path / "tenants" / "org-1" / "paramspecs"
    legacy.mkdir()
    tenant.mkdir(parents=True)
    raw = _fixture()
    raw["catalog"] = {"creator_user_id": "user-1"}
    raw["future_top_level"] = "ignored on tolerant read"
    legacy_path = legacy / "wardrobe.json"
    tenant_path = tenant / "wardrobe.json"
    versions_path = tenant / "wardrobe.versions.json"
    encoded = json.dumps(raw, ensure_ascii=False)
    legacy_path.write_text(encoded, encoding="utf-8")
    tenant_path.write_text(encoded, encoding="utf-8")
    versions_path.write_text(
        json.dumps([{"ts": "2026-08-13T12:00:00", "spec": raw}], ensure_ascii=False),
        encoding="utf-8",
    )
    before = {
        path: path.read_bytes() for path in (legacy_path, tenant_path, versions_path)
    }

    report = audit_paramspec_v1_catalogs(legacy, tmp_path / "tenants")

    assert report["dry_run"] is True
    assert report["writes_performed"] == 0
    assert report["production_migration_allowed"] is False
    assert report["documents"] == 3
    assert report["schema_versions"] == {"paramspec-v1": 3}
    assert report["unknown_field_documents"] == 3
    assert report["canonical_changes"] == 3
    assert report["equivalence_checked"] == 3
    assert report["equivalence_failed"] == 0
    assert report["canonical_idempotence_failed"] == 0
    assert report["migration_plan"]["ready"] is True
    assert report["migration_plan"]["source_writes_allowed"] is False
    assert report["backup_manifest"]["source_tree_sha256"] == (
        report["rollback_manifest"]["source_tree_sha256"]
    )
    assert len(report["backup_manifest"]["files"]) == 3
    assert all(
        all(item["equivalence"].values())
        for item in report["items"]
        if item["status"] == "valid"
    )
    assert {
        path: path.read_bytes() for path in (legacy_path, tenant_path, versions_path)
    } == before


def test_dry_run_report_and_exact_byte_manifests_are_deterministic(
    tmp_path: Path,
) -> None:
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    path = legacy / "wardrobe.json"
    path.write_text(json.dumps(_fixture(), ensure_ascii=False), encoding="utf-8")

    first = audit_paramspec_v1_catalogs(legacy, tmp_path / "tenants")
    second = audit_paramspec_v1_catalogs(legacy, tmp_path / "tenants")

    assert first == second
    assert first["report_digest"] == second["report_digest"]
    manifest_file = first["backup_manifest"]["files"][0]
    assert manifest_file == {
        "path": "legacy/wardrobe.json",
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "size": len(path.read_bytes()),
    }
    assert first["rollback_manifest"]["files"] == [manifest_file]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.update(schemaVersion="paramspec-v2"),
        lambda value: value["dimensions"].update(width=10),
    ],
    ids=["unsupported-version", "invalid-known-field"],
)
def test_mixed_catalog_plan_fails_atomically_without_source_writes(
    tmp_path: Path,
    mutate,
) -> None:
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    valid_path = legacy / "valid-v1.json"
    invalid_path = legacy / "invalid.json"
    valid_path.write_text(json.dumps(_fixture()), encoding="utf-8")
    invalid = _fixture()
    mutate(invalid)
    invalid_path.write_text(json.dumps(invalid), encoding="utf-8")
    before = {path: path.read_bytes() for path in (valid_path, invalid_path)}

    report = audit_paramspec_v1_catalogs(legacy, tmp_path / "tenants")

    assert report["documents"] == 2
    assert report["invalid"] == 1
    assert report["migration_plan"]["ready"] is False
    assert report["migration_plan"]["atomicity"] == "all-or-nothing"
    assert report["migration_plan"]["blockers"] == [
        "invalid_or_unsupported_paramspec"
    ]
    assert report["writes_performed"] == 0
    assert {path: path.read_bytes() for path in before} == before


def test_catalog_identity_migration_uses_canonical_strict_write(tmp_path: Path) -> None:
    raw = _fixture()
    raw["future_top_level"] = "tolerated on read, forbidden on write"
    path = tmp_path / "wardrobe.json"
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    _migrate_catalog_identity(
        tmp_path,
        {"user_id": "owner-1", "display_name": "Владелец"},
    )

    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert "future_top_level" not in persisted
    assert persisted["catalog"] == {
        "creator_user_id": "owner-1",
        "responsible_user_id": "owner-1",
        "author": "Владелец",
        "responsible": "Владелец",
    }
    assert strict_paramspec_v1_for_write(persisted) == persisted


def test_snapshot_canonicalizes_new_revision_without_rewriting_old_history(
    tmp_path: Path,
) -> None:
    spec_path = tmp_path / "wardrobe.json"
    legacy = _fixture()
    legacy["future_history_field"] = "preserved only in old history"
    versions_path = tmp_path / "wardrobe.versions.json"
    versions_path.write_text(
        json.dumps([{"ts": "2026-08-13T12:00:00", "spec": legacy}]),
        encoding="utf-8",
    )
    candidate = _fixture()
    candidate["catalog"] = {"creator_user_id": "owner-1"}
    candidate["future_snapshot_field"] = "must not persist in the new revision"

    _snapshot_version(spec_path, candidate)

    revisions = json.loads(versions_path.read_text(encoding="utf-8"))
    assert revisions[0]["spec"]["future_history_field"] == (
        "preserved only in old history"
    )
    assert "future_snapshot_field" not in revisions[1]["spec"]
    assert revisions[1]["spec"]["catalog"] == {"creator_user_id": "owner-1"}
    assert strict_paramspec_v1_for_write(revisions[1]["spec"]) == revisions[1]["spec"]
