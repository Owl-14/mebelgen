from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.local_b3d import (  # noqa: E402
    LocalB3dError,
    _write_deterministic_zip,
    inspect_b3d_structure,
    prepare_local_b3d,
    verify_local_b3d,
)
from src.b3d_format import write_b3d  # noqa: E402


FIXTURES = ROOT / "qa" / "fixtures"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_manifest(package: Path, mutate) -> str:
    path = package / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    mutate(manifest)
    path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    _write_deterministic_zip(package, package / "local-b3d-package.zip")
    return _sha(package / "local-b3d-package.zip")


def _empty_b3d(path: Path) -> None:
    path.write_bytes(write_b3d([
        (0, ("Header", "obj", [])),
        (1, ("Document", "obj", [])),
    ]))


def _semantic_b3d(project: dict, path: Path, *, foreign_type: int | None = None) -> None:
    from src.cfrn import project_to_cfrn_json
    from src.hardware import compute_drilling

    cfrn = project_to_cfrn_json(project)
    materials = cfrn["table"]["materials"]
    material_by_panel = {}
    for obj in cfrn["table"]["objects"]:
        index = obj.get("materialIndex")
        if obj.get("objType") == 2 and isinstance(index, int):
            material_by_panel[obj["name"]] = materials[index]["name"]

    panel_nodes = [
        ("Obj", "obj", [
            ("Type", "i32", 4002),
            ("Name", "str", panel["name"]),
            ("Mat", "str", material_by_panel[panel["name"]]),
            ("Thick", "f64", panel["thickness"]),
        ])
        for panel in project["panels"]
    ]
    holes = [
        ("Hole", "obj", [
            ("Radius", "f64", hole["diameter"] / 2),
            ("Depth", "f64", hole["depth"]),
        ])
        for hole in compute_drilling(project)
    ]
    fast_id = 1
    furniture = ("Furn", "obj", [
        ("FastID", "i32", fast_id),
        ("Holes", "obj", holes),
    ])
    instance = ("Obj", "obj", [
        ("Type", "i32", 3001),
        ("FastID", "i32", fast_id),
    ])
    foreign = [] if foreign_type is None else [("Obj", "obj", [
        ("Type", "i32", foreign_type),
        ("Name", "str", "FOREIGN-EXISTING-OBJECT"),
    ])]
    document = ("Document", "obj", [
        ("FurnList", "obj", [furniture]),
        ("Model", "obj", [("Objs", "obj", panel_nodes + [instance] + foreign)]),
    ])
    path.write_bytes(write_b3d([(0, ("Header", "obj", [])), (1, document)]))


def test_known_b3d_profiles_are_structural_but_not_native_proof():
    cloud = inspect_b3d_structure(FIXTURES / "wardrobe_demo_ours_cloud.b3d")
    desktop = inspect_b3d_structure(FIXTURES / "wardrobe_demo_production.b3d")
    assert cloud["ok"] and desktop["ok"]
    assert cloud["trailer"]["profile"] == "opaque-64"
    assert desktop["trailer"]["profile"] == "absent"
    assert cloud["native_openability"] == desktop["native_openability"] == "unverified"


def test_prepare_package_is_reproducible_and_contains_no_b3d(tmp_path: Path):
    source = ROOT / "paramspecs" / "komi_72_tumba_podkatnaya.json"
    first, second = tmp_path / "first", tmp_path / "second"
    one = prepare_local_b3d(source, first)
    two = prepare_local_b3d(source, second)
    assert one["claims"]["basis_apilist_called"] is False
    assert one["claims"]["b3d_created_by_akeda"] is False
    assert one["schema"] == "akeda.local-b3d-manifest"
    assert one["version"] == 1
    assert one["native_status"] == "unverified"
    assert one["project"]["sha256"] == one["artifacts"]["project.json"]
    assert one["project"]["panel_count"] > 0
    assert one["handoff"]["requires_new_empty_document"] is True
    assert one["handoff"]["allowed_model_object_types"]
    project = json.loads((first / "project.json").read_text(encoding="utf-8"))
    assert project["_local_b3d_handoff"]["requires_new_empty_document"] is True
    importer = (first / "ImportFurnitureFromJSON.js").read_text(encoding="utf-8")
    assert "handoff !== undefined" in importer
    assert "handoff.requires_new_empty_document !== true" in importer
    assert "НОВЫЙ ПУСТОЙ документ БАЗИС" in importer
    assert one["package_zip_sha256"] == two["package_zip_sha256"]
    assert _sha(first / "local-b3d-package.zip") == _sha(second / "local-b3d-package.zip")
    assert not list(first.glob("*.b3d"))


def test_prepare_refuses_red_project_and_writes_no_package(tmp_path: Path):
    source = ROOT / "paramspecs" / "komi_72_tumba_podkatnaya.json"
    spec = json.loads(source.read_text(encoding="utf-8"))
    from src.generators import generate_from_paramspec

    project = generate_from_paramspec(spec)
    project["panels"][0]["placement"]["x2"] += 50
    broken = tmp_path / "broken.json"
    broken.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    target = tmp_path / "package"
    with pytest.raises(LocalB3dError, match="offline preflight"):
        prepare_local_b3d(broken, target)
    assert not target.exists()


def test_verify_rejects_semantic_mismatch_without_claiming_native(tmp_path: Path):
    source = ROOT / "paramspecs" / "komi_72_tumba_podkatnaya.json"
    package = tmp_path / "package"
    prepared = prepare_local_b3d(source, package)
    report = verify_local_b3d(
        package,
        FIXTURES / "wardrobe_demo_ours_cloud.b3d",
        prepared["package_zip_sha256"],
    )
    assert report["structure"]["ok"]
    assert not report["semantic_parity"]["ok"]
    assert not report["offline_verification_ok"]
    assert report["native_basis"]["confirmed"] is False
    assert report["native_basis"]["status"] == "unverified"


def test_verify_detects_package_tampering(tmp_path: Path):
    source = ROOT / "paramspecs" / "komi_72_tumba_podkatnaya.json"
    package = tmp_path / "package"
    prepared = prepare_local_b3d(source, package)
    (package / "project.json").write_text("{}", encoding="utf-8")
    with pytest.raises(LocalB3dError, match="отличаются от anchored ZIP"):
        verify_local_b3d(
            package,
            FIXTURES / "wardrobe_demo_ours_cloud.b3d",
            prepared["package_zip_sha256"],
        )


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda manifest: manifest.__setitem__("artifacts", {}), "обязательный набор artifacts"),
        (lambda manifest: manifest.pop("schema"), "schema/version"),
        (lambda manifest: manifest.pop("version"), "schema/version"),
        (lambda manifest: manifest.pop("project"), "project.path"),
        (lambda manifest: manifest["project"].pop("sha256"), "project hash"),
        (lambda manifest: manifest.__setitem__("native_status", "confirmed"), "unverified"),
        (lambda manifest: manifest["handoff"].__setitem__("requires_new_empty_document", False), "пустой документ"),
    ],
)
def test_verify_rejects_incomplete_manifest(tmp_path: Path, mutate, message: str):
    package = tmp_path / "package"
    prepare_local_b3d(ROOT / "paramspecs" / "komi_72_tumba_podkatnaya.json", package)
    expected = _write_manifest(package, mutate)
    with pytest.raises(LocalB3dError, match=message):
        verify_local_b3d(package, FIXTURES / "wardrobe_demo_ours_cloud.b3d", expected)


def test_synthetic_empty_bz85_fails_semantic_evidence(tmp_path: Path):
    package = tmp_path / "package"
    candidate = tmp_path / "empty.b3d"
    prepared = prepare_local_b3d(ROOT / "paramspecs" / "komi_72_tumba_podkatnaya.json", package)
    _empty_b3d(candidate)

    report = verify_local_b3d(package, candidate, prepared["package_zip_sha256"])

    assert report["structure"]["ok"]  # exact audit counterexample: container shape alone is insufficient
    assert not report["semantic_parity"]["ok"]
    assert not report["offline_verification_ok"]
    codes = {error["code"] for error in report["semantic_parity"]["errors"]}
    assert "b3d.node.model_missing" in codes
    assert "b3d.panels.count_mismatch" in codes
    assert "b3d.drilling.count_mismatch" in codes
    assert report["native_basis"] == {
        "status": "unverified",
        "confirmed": False,
        "reason": "Нужен фактический open/save/reopen или b3d→cfrn round-trip в лицензированном БАЗИС.",
    }


def test_complete_offline_evidence_still_leaves_native_unverified(tmp_path: Path):
    package = tmp_path / "package"
    candidate = tmp_path / "semantic-candidate.b3d"
    prepared = prepare_local_b3d(ROOT / "paramspecs" / "komi_72_tumba_podkatnaya.json", package)
    project = json.loads((package / "project.json").read_text(encoding="utf-8"))
    _semantic_b3d(project, candidate)

    report = verify_local_b3d(package, candidate, prepared["package_zip_sha256"])

    assert report["offline_verification_ok"], report
    assert report["package_anchor"] == {
        "expected_sha256": prepared["package_zip_sha256"],
        "actual_sha256": prepared["package_zip_sha256"],
        "verified": True,
    }
    assert report["repeated_preflight"]["ok"] is True
    assert report["semantic_parity"]["panels"]["actual"] > 0
    assert report["semantic_parity"]["materials"]["actual"] > 0
    assert report["semantic_parity"]["drilling"]["actual"] > 0
    assert report["native_basis"]["status"] == "unverified"
    assert report["native_basis"]["confirmed"] is False


def test_coordinated_package_substitution_fails_external_anchor(tmp_path: Path):
    trusted = tmp_path / "trusted"
    substituted = tmp_path / "substituted"
    original = prepare_local_b3d(ROOT / "paramspecs" / "komi_72_tumba_podkatnaya.json", trusted)
    prepare_local_b3d(ROOT / "paramspecs" / "wardrobe_demo.json", substituted)

    with pytest.raises(LocalB3dError, match="trust anchor mismatch"):
        verify_local_b3d(
            substituted,
            FIXTURES / "wardrobe_demo_ours_cloud.b3d",
            original["package_zip_sha256"],
        )


def test_repeated_preflight_rejects_reanchored_red_project(tmp_path: Path):
    from src.hardware import compute_drilling

    package = tmp_path / "package"
    prepare_local_b3d(ROOT / "paramspecs" / "komi_72_tumba_podkatnaya.json", package)
    project_path = package / "project.json"
    project = json.loads(project_path.read_text(encoding="utf-8"))
    project["panels"][0]["placement"]["x1"] += 100_000
    project["panels"][0]["placement"]["x2"] += 100_000
    project_bytes = (json.dumps(project, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    project_path.write_bytes(project_bytes)
    manifest_path = package / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifacts"]["project.json"] = hashlib.sha256(project_bytes).hexdigest()
    manifest["project"]["sha256"] = manifest["artifacts"]["project.json"]
    manifest["project"]["drilling_count"] = len(compute_drilling(project))
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    _write_deterministic_zip(package, package / "local-b3d-package.zip")
    attacker_anchor = _sha(package / "local-b3d-package.zip")

    with pytest.raises(LocalB3dError, match="повторный preflight"):
        verify_local_b3d(package, FIXTURES / "wardrobe_demo_ours_cloud.b3d", attacker_anchor)


def test_foreign_model_object_type_makes_verification_red(tmp_path: Path):
    package = tmp_path / "package"
    candidate = tmp_path / "foreign.b3d"
    prepared = prepare_local_b3d(ROOT / "paramspecs" / "komi_72_tumba_podkatnaya.json", package)
    project = json.loads((package / "project.json").read_text(encoding="utf-8"))
    _semantic_b3d(project, candidate, foreign_type=9999)

    report = verify_local_b3d(package, candidate, prepared["package_zip_sha256"])

    assert not report["offline_verification_ok"]
    assert report["semantic_parity"]["model_object_types"]["unknown"] == [9999]
    assert "b3d.model_object_type.unknown" in {
        error["code"] for error in report["semantic_parity"]["errors"]
    }
    assert report["native_basis"]["status"] == "unverified"
