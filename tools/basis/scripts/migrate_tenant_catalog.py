#!/usr/bin/env python3
"""Safely copy a legacy Studio catalog into one organization workspace.

The source catalog is never changed.  Valid products are copied to the tenant;
drafts, invalid files and obvious duplicates are copied to a dated archive with
a machine-readable manifest.  The command is a dry run unless ``--apply`` is
explicitly supplied.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.paramspec import validate_paramspec  # noqa: E402


@dataclass(frozen=True)
class CatalogItem:
    file: str
    target_file: str
    project_name: str
    decision: str
    reason: str
    duplicate_of: str | None = None


_PRODUCTION_CLEANUPS: dict[str, tuple[str, str]] = {
    "новое_изделие10.json": (
        "Высокий составной модуль 1860×630×2400",
        "sostavnoy_modul_1860x630x2400.json",
    ),
    "новое_изделие_2.json": ("Тумба для ванной", "tumba_dlya_vannoy.json"),
    "пппп.json": ("Тумба модератора — вариант 2", "tumba_moderatora_variant_2.json"),
}


def _digest(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _semantic_copy_signature(spec: dict[str, Any]) -> str:
    value = json.loads(json.dumps(spec))
    value.pop("project_name", None)
    return _digest(value)


def classify_catalog(source: Path) -> list[CatalogItem]:
    result: list[CatalogItem] = []
    exact: dict[str, str] = {}
    semantic: dict[str, str] = {}
    for path in sorted(source.glob("*.json")):
        if path.name.endswith((".project.json", ".versions.json")):
            continue
        try:
            spec = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            result.append(CatalogItem(path.name, path.name, path.stem, "archive", "invalid_json"))
            continue
        name = str(spec.get("project_name") or path.stem) if isinstance(spec, dict) else path.stem
        if not isinstance(spec, dict) or spec.get("schemaVersion") != "paramspec-v1":
            result.append(CatalogItem(path.name, path.name, name, "archive", "not_paramspec"))
            continue
        if spec.get("draft"):
            result.append(CatalogItem(path.name, path.name, name, "archive", "draft"))
            continue
        problems = validate_paramspec(spec)
        if problems:
            result.append(
                CatalogItem(
                    path.name,
                    path.name,
                    name,
                    "archive",
                    f"validation_errors:{len(problems)}",
                )
            )
            continue

        full_signature = _digest(spec)
        if full_signature in exact:
            result.append(
                CatalogItem(
                    path.name,
                    path.name,
                    name,
                    "archive",
                    "exact_duplicate",
                    exact[full_signature],
                )
            )
            continue
        copy_signature = _semantic_copy_signature(spec)
        obvious_copy = "copy" in path.stem.casefold() or "(копия)" in name.casefold()
        if obvious_copy and copy_signature in semantic:
            result.append(
                CatalogItem(
                    path.name,
                    path.name,
                    name,
                    "archive",
                    "named_copy",
                    semantic[copy_signature],
                )
            )
            continue
        exact[full_signature] = path.name
        semantic.setdefault(copy_signature, path.name)
        cleaned_name, target_file = _PRODUCTION_CLEANUPS.get(path.name, (name, path.name))
        result.append(CatalogItem(path.name, target_file, cleaned_name, "keep", "valid"))
    return result


def migrate_catalog(
    source: Path,
    tenant_root: Path,
    organization_id: str,
    *,
    apply: bool,
) -> dict[str, Any]:
    source = source.resolve()
    tenant_root = tenant_root.resolve()
    if not source.is_dir():
        raise ValueError(f"Каталог-источник не найден: {source}")
    if not organization_id or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_." for ch in organization_id):
        raise ValueError("Некорректный organization-id")

    items = classify_catalog(source)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    organization_root = tenant_root / organization_id
    destination = organization_root / "paramspecs"
    archive = organization_root / "archive" / f"legacy-import-{timestamp}"
    report: dict[str, Any] = {
        "dry_run": not apply,
        "source": str(source),
        "organization_id": organization_id,
        "destination": str(destination),
        "archive": str(archive),
        "kept": sum(item.decision == "keep" for item in items),
        "archived": sum(item.decision == "archive" for item in items),
        "items": [asdict(item) for item in items],
    }
    if not apply:
        return report

    destination.mkdir(parents=True, exist_ok=True)
    archive.mkdir(parents=True, exist_ok=False)
    for item in items:
        source_path = source / item.file
        target_dir = destination if item.decision == "keep" else archive
        target = target_dir / item.target_file
        expected = source_path.read_bytes()
        if item.decision == "keep" and item.project_name:
            value = json.loads(source_path.read_text(encoding="utf-8"))
            value["project_name"] = item.project_name
            expected = json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")
        if target.exists():
            if target.read_bytes() != expected:
                raise FileExistsError(f"Файл уже есть и отличается: {target}")
        else:
            if item.decision == "keep":
                target.write_bytes(expected)
            else:
                shutil.copy2(source_path, target)
        if item.decision != "keep":
            continue
        stem = source_path.stem
        target_stem = Path(item.target_file).stem
        companions = [
            source / f"{stem}.versions.json",
            source / ".previews" / f"{stem}.png",
        ]
        for companion in companions:
            if not companion.is_file():
                continue
            relative = companion.relative_to(source)
            companion_target = destination / relative
            if companion.parent == source:
                companion_target = destination / f"{target_stem}.versions.json"
            elif companion.parent.name == ".previews":
                companion_target = destination / ".previews" / f"{target_stem}.png"
            companion_target.parent.mkdir(parents=True, exist_ok=True)
            if companion_target.exists() and companion_target.read_bytes() != companion.read_bytes():
                raise FileExistsError(f"Файл уже есть и отличается: {companion_target}")
            if not companion_target.exists():
                shutil.copy2(companion, companion_target)

    (archive / "manifest.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--tenant-root", required=True, type=Path)
    parser.add_argument("--organization-id", required=True)
    parser.add_argument("--apply", action="store_true", help="Выполнить копирование")
    args = parser.parse_args()
    report = migrate_catalog(
        args.source, args.tenant_root, args.organization_id, apply=args.apply
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
