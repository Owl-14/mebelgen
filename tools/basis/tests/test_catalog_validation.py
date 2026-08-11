from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.validate_catalogs import validate_catalogs  # noqa: E402


def _valid_spec() -> dict:
    return json.loads(
        (ROOT / "paramspecs" / "komi_72_tumba_podkatnaya.json").read_text(
            encoding="utf-8"
        )
    )


def test_tenant_catalog_gate_accepts_typed_metadata_and_legacy_guide(tmp_path: Path) -> None:
    legacy = tmp_path / "legacy"
    tenant = tmp_path / "tenants" / "org-1" / "paramspecs"
    legacy.mkdir()
    tenant.mkdir(parents=True)
    spec = _valid_spec()
    spec["catalog"] = {
        "creator_user_id": "user-1",
        "responsible_user_id": "user-2",
        "author": "Автор",
        "responsible": "Ответственный",
    }
    spec.setdefault("hardware", {}).setdefault("drawer_guides", {})["with_closer"] = True
    (tenant / "product.json").write_text(
        json.dumps(spec, ensure_ascii=False), encoding="utf-8"
    )

    report = validate_catalogs(legacy, tmp_path / "tenants", build=True)

    assert report == {
        "ok": True,
        "checked": 1,
        "drafts": 0,
        "built": 1,
        "failed": 0,
        "failures": [],
    }


def test_tenant_catalog_gate_reports_unknown_fields_without_writing(tmp_path: Path) -> None:
    legacy = tmp_path / "legacy"
    tenant = tmp_path / "tenants" / "org-1" / "paramspecs"
    legacy.mkdir()
    tenant.mkdir(parents=True)
    spec = _valid_spec()
    spec["catalog"] = {"unexpected": "must remain forbidden"}
    path = tenant / "broken.json"
    original = json.dumps(spec, ensure_ascii=False)
    path.write_text(original, encoding="utf-8")

    report = validate_catalogs(legacy, tmp_path / "tenants")

    assert report["ok"] is False and report["failed"] == 1
    assert report["failures"][0]["kind"] == "invalid_paramspec"
    assert "catalog.unexpected" in "\n".join(report["failures"][0]["errors"])
    assert path.read_text(encoding="utf-8") == original
