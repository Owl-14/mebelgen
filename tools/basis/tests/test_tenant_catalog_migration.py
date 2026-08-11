from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.migrate_tenant_catalog import classify_catalog, migrate_catalog  # noqa: E402


def _valid(name: str) -> dict:
    value = json.loads((ROOT / "paramspecs" / "wardrobe_demo.json").read_text(encoding="utf-8"))
    value["project_name"] = name
    return value


def _write(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def test_migration_is_dry_by_default_and_archives_without_deleting(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    good = _valid("Рабочая тумба")
    _write(source / "good.json", good)
    copied = json.loads(json.dumps(good))
    copied["project_name"] = "Рабочая тумба (копия)"
    _write(source / "good_copy.json", copied)
    _write(
        source / "draft.json",
        {"schemaVersion": "paramspec-v1", "draft": True, "project_name": "Черновик"},
    )

    dry = migrate_catalog(source, tmp_path / "tenants", "org-1", apply=False)
    assert dry["kept"] == 1 and dry["archived"] == 2
    assert not (tmp_path / "tenants").exists()

    applied = migrate_catalog(source, tmp_path / "tenants", "org-1", apply=True)
    destination = Path(applied["destination"])
    archive = Path(applied["archive"])
    assert (destination / "good.json").is_file()
    assert (archive / "good_copy.json").is_file()
    assert (archive / "draft.json").is_file()
    assert (archive / "manifest.json").is_file()
    assert sorted(path.name for path in source.glob("*.json")) == [
        "draft.json",
        "good.json",
        "good_copy.json",
    ]


def test_known_production_junk_filename_gets_clean_tenant_copy(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _write(source / "пппп.json", _valid("Тумба модератора"))
    item = classify_catalog(source)[0]
    assert item.target_file == "tumba_moderatora_variant_2.json"
    assert item.project_name == "Тумба модератора — вариант 2"
