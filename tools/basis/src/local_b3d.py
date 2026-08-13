"""Offline hand-off for creating a B3D in licensed BAZIS desktop.

This module deliberately does not synthesize a ``.b3d`` container.  It builds a
deterministic import package, then verifies the file saved by BAZIS without any
cloud/APIList call.  Offline verification proves structure and selected model
parity; it cannot prove that BAZIS will open a candidate on another installation.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any


WORKFLOW_VERSION = "local-b3d-v1"
MANIFEST_SCHEMA = "akeda.local-b3d-manifest"
MANIFEST_VERSION = 1
REQUIRED_ARTIFACTS = frozenset({
    "project.json",
    "ImportFurnitureFromJSON.js",
    "README.txt",
})
PACKAGE_FILES = (
    "project.json",
    "ImportFurnitureFromJSON.js",
    "README.txt",
    "manifest.json",
)
_FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)


class LocalB3dError(RuntimeError):
    """A local B3D package or candidate failed a safety check."""


def _canonical_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _validate_manifest(manifest: Any) -> dict[str, str]:
    """Validate the complete manifest contract before trusting any path/hash."""
    if not isinstance(manifest, dict):
        raise LocalB3dError("manifest должен быть JSON-объектом")
    if manifest.get("schema") != MANIFEST_SCHEMA or manifest.get("version") != MANIFEST_VERSION:
        raise LocalB3dError("Неподдерживаемая schema/version manifest")
    if manifest.get("workflow") != WORKFLOW_VERSION:
        raise LocalB3dError(f"Неподдерживаемый workflow: {manifest.get('workflow')}")
    if manifest.get("status") != "requires_licensed_basis_desktop_save":
        raise LocalB3dError("Некорректный status manifest")
    source = manifest.get("source")
    if not isinstance(source, dict) or not isinstance(source.get("kind"), str) or not _is_sha256(source.get("sha256")):
        raise LocalB3dError("Manifest source.kind/source.sha256 обязательны")

    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict) or set(artifacts) != REQUIRED_ARTIFACTS:
        raise LocalB3dError("Manifest должен содержать точный обязательный набор artifacts")
    if not all(_is_sha256(value) for value in artifacts.values()):
        raise LocalB3dError("Artifact hash должен быть lowercase SHA-256")

    project = manifest.get("project")
    if not isinstance(project, dict) or project.get("path") != "project.json":
        raise LocalB3dError("Manifest должен содержать project.path=project.json")
    if not _is_sha256(project.get("sha256")) or project["sha256"] != artifacts["project.json"]:
        raise LocalB3dError("Manifest project hash отсутствует или не совпадает с artifact")
    if not isinstance(project.get("panel_count"), int) or project["panel_count"] <= 0:
        raise LocalB3dError("Manifest project.panel_count должен быть положительным")
    if not isinstance(project.get("drilling_count"), int) or project["drilling_count"] < 0:
        raise LocalB3dError("Manifest project.drilling_count должен быть неотрицательным")
    if manifest.get("native_status") != "unverified":
        raise LocalB3dError("Manifest native_status должен оставаться unverified")
    if not isinstance(manifest.get("preflight"), dict) or manifest["preflight"].get("ok") is not True:
        raise LocalB3dError("Manifest preflight.ok должен быть true")
    return artifacts


def _project_preflight(project: dict[str, Any]) -> dict[str, Any]:
    """Run the project-level subset of the production gate."""
    from .cfrn import check_cfrn_encoding, check_cfrn_holes
    from .completeness_check import check_completeness
    from .consistency_check import check_consistency
    from .drilling_check import check_drilling_geometry
    from .geometry_check import check_placement_geometry
    from .materials import check_project_materials

    geometry = check_placement_geometry(project)
    drilling = check_drilling_geometry(project)
    checks = {
        "consistency": [f"[{item.code}] {item.panel}: {item.message}" for item in check_consistency(project)],
        "geometry": [
            f"{item['panel_a']} <-> {item['panel_b']}: {item['detail']}"
            for item in geometry.get("overlaps", [])
        ],
        "cfrn_encoding": check_cfrn_encoding(project),
        "cfrn_holes_parity": check_cfrn_holes(project),
        "drilling_geometry": list(drilling.get("errors", [])),
        "completeness": check_completeness(project),
    }
    warnings = {
        "drilling_geometry": list(drilling.get("warnings", [])),
        "materials": check_project_materials(project),
    }
    return {
        "ok": not any(checks.values()),
        "checks": checks,
        "warnings": warnings,
    }


def _load_input(path: Path) -> tuple[str, dict[str, Any], dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schemaVersion") == "paramspec-v1":
        from .production_gate import evaluate_production_gate

        decision = evaluate_production_gate(data)
        gate = decision.report.to_dict()
        if not gate["ok"] or decision.project is None:
            raise LocalB3dError("ParamSpec не прошёл production gate: " + json.dumps(gate["errors"], ensure_ascii=False))
        project = decision.project
        if decision.material_refs:
            project["material_refs"] = decision.material_refs
        return "paramspec-v1", project, gate

    if not isinstance(data, dict) or not isinstance(data.get("panels"), list):
        raise LocalB3dError("Ожидался ParamSpec v1 или project.json с массивом panels")
    gate = _project_preflight(data)
    if not gate["ok"]:
        failed = {name: issues for name, issues in gate["checks"].items() if issues}
        raise LocalB3dError("project.json не прошёл offline preflight: " + json.dumps(failed, ensure_ascii=False))
    return "project-json", data, gate


def _instructions(expected_name: str) -> str:
    return f"""Локальный путь B3D без Basis APIList

1. Нужна лицензированная/триальная Windows-установка БАЗИС-Мебельщик.
2. Запустите ImportFurnitureFromJSON.js внутри БАЗИС и выберите project.json.
3. Проверьте сообщения импортёра и модель вручную.
4. Сохраните модель штатной командой БАЗИС как {expected_name}.
5. Вернитесь в Akeda и выполните:
   python main.py local-b3d verify <каталог-пакета> <путь-к-{expected_name}>

Важно:
- пакет не вызывает Basis Cloud/APIList, Cutting или LLM API;
- сам пакет не содержит .b3d и не выдаётся за нативный файл;
- текущий импортёр создаёт панели и ограниченный набор фурнитуры, но не доказывает
  полноту кромки, присадок и производственных связей;
- offline verify проверяет BZ85-структуру и базовый семантический паритет, но
  открываемость и round-trip подтверждаются только реальным БАЗИС.
"""


def _write_deterministic_zip(package_dir: Path, zip_path: Path) -> None:
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in PACKAGE_FILES:
            info = zipfile.ZipInfo(name, _FIXED_ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, (package_dir / name).read_bytes())


def prepare_local_b3d(input_path: str | Path, package_dir: str | Path) -> dict[str, Any]:
    """Create a deterministic desktop-import package; never create a fake B3D."""
    source = Path(input_path)
    target = Path(package_dir)
    occupied = [name for name in (*PACKAGE_FILES, "local-b3d-package.zip") if (target / name).exists()]
    if occupied:
        raise LocalB3dError("Каталог уже содержит файлы пакета: " + ", ".join(occupied))

    source_kind, project, preflight = _load_input(source)
    target.mkdir(parents=True, exist_ok=True)
    root = Path(__file__).resolve().parent.parent
    importer = (root / "scripts" / "ImportFurnitureFromJSON.js").read_bytes()
    project_bytes = _canonical_json(project)
    expected_name = source.stem.removesuffix(".project") + ".b3d"
    readme = _instructions(expected_name).encode("utf-8")

    manifest = {
        "schema": MANIFEST_SCHEMA,
        "version": MANIFEST_VERSION,
        "workflow": WORKFLOW_VERSION,
        "status": "requires_licensed_basis_desktop_save",
        "source": {"kind": source_kind, "sha256": _sha256(source.read_bytes())},
        "artifacts": {
            "project.json": _sha256(project_bytes),
            "ImportFurnitureFromJSON.js": _sha256(importer),
            "README.txt": _sha256(readme),
        },
        "project": {
            "path": "project.json",
            "sha256": _sha256(project_bytes),
            "panel_count": len(project.get("panels", [])),
            "drilling_count": _project_drilling_count(project),
        },
        "expected_b3d_name": expected_name,
        "native_status": "unverified",
        "preflight": preflight,
        "claims": {
            "offline_package_reproducible": True,
            "basis_apilist_called": False,
            "b3d_created_by_akeda": False,
            "native_b3d_confirmed": False,
        },
        "limitations": [
            "Нужна реальная лицензированная/триальная среда БАЗИС для импорта и сохранения .b3d.",
            "Импортёр не доказывает полноту кромки, присадок и производственных связей.",
            "Структурный parse/serialize round-trip не доказывает открываемость файла БАЗИС.",
        ],
    }
    manifest_bytes = _canonical_json(manifest)
    for name, payload in (
        ("project.json", project_bytes),
        ("ImportFurnitureFromJSON.js", importer),
        ("README.txt", readme),
        ("manifest.json", manifest_bytes),
    ):
        (target / name).write_bytes(payload)
    zip_path = target / "local-b3d-package.zip"
    _write_deterministic_zip(target, zip_path)
    return {**manifest, "package_zip_sha256": _sha256(zip_path.read_bytes())}


def inspect_b3d_structure(path: str | Path) -> dict[str, Any]:
    """Inspect a BZ85 candidate without claiming BAZIS openability."""
    from .b3d_format import parse_b3d, write_b3d

    raw = Path(path).read_bytes()
    doc = parse_b3d(raw)
    rebuilt = write_b3d(doc["sections"], doc["trailer"])
    reparsed = parse_b3d(rebuilt)
    flags = [flag for flag, _ in doc["sections"]]
    roots = [root[0] for _, root in doc["sections"]]
    trailer_len = len(doc["trailer"])
    return {
        "ok": flags == [0, 1] and roots == ["Header", "Document"]
        and reparsed["sections"] == doc["sections"] and reparsed["trailer"] == doc["trailer"],
        "sha256": _sha256(raw),
        "bytes": len(raw),
        "section_flags": flags,
        "section_roots": roots,
        "tree_roundtrip": reparsed["sections"] == doc["sections"],
        "trailer_roundtrip": reparsed["trailer"] == doc["trailer"],
        "byte_identical_rebuild": rebuilt == raw,
        "trailer": {
            "bytes": trailer_len,
            "profile": "absent" if trailer_len == 0 else ("opaque-64" if trailer_len == 64 else "unknown"),
            "sha256": _sha256(doc["trailer"]),
        },
        "native_openability": "unverified",
    }


def _project_drilling_count(project: dict[str, Any]) -> int:
    from .hardware import compute_drilling

    return len(compute_drilling(project))


def verify_local_b3d(
    package_dir: str | Path,
    b3d_path: str | Path,
    report_path: str | Path | None = None,
) -> dict[str, Any]:
    """Verify package integrity plus structural/basic semantic B3D parity."""
    package = Path(package_dir)
    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
    artifacts = _validate_manifest(manifest)
    integrity: dict[str, bool] = {}
    for name, expected in artifacts.items():
        integrity[name] = _sha256((package / name).read_bytes()) == expected
    if set(integrity) != REQUIRED_ARTIFACTS or not all(integrity.values()):
        raise LocalB3dError("Нарушена целостность import-пакета")

    project = json.loads((package / "project.json").read_text(encoding="utf-8"))
    if len(project.get("panels", [])) != manifest["project"]["panel_count"]:
        raise LocalB3dError("Фактический panel_count project.json не совпадает с manifest")
    if _project_drilling_count(project) != manifest["project"]["drilling_count"]:
        raise LocalB3dError("Фактический drilling_count project.json не совпадает с manifest")
    structural = inspect_b3d_structure(b3d_path)
    from .b3d_verify import verify_b3d_parity

    semantic = verify_b3d_parity(b3d_path, project)
    report = {
        "workflow": WORKFLOW_VERSION,
        "offline_verification_ok": bool(structural["ok"] and semantic["ok"]),
        "package_integrity": integrity,
        "structure": structural,
        "semantic_parity": semantic,
        "native_basis": {
            "status": "unverified",
            "confirmed": False,
            "reason": "Нужен фактический open/save/reopen или b3d→cfrn round-trip в лицензированном БАЗИС.",
        },
    }
    if report_path is not None:
        Path(report_path).write_bytes(_canonical_json(report))
    return report
