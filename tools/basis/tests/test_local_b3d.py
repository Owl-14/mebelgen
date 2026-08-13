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
    inspect_b3d_structure,
    prepare_local_b3d,
    verify_local_b3d,
)


FIXTURES = ROOT / "qa" / "fixtures"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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
    prepare_local_b3d(source, package)
    report = verify_local_b3d(package, FIXTURES / "wardrobe_demo_ours_cloud.b3d")
    assert report["structure"]["ok"]
    assert not report["semantic_parity"]["ok"]
    assert not report["offline_verification_ok"]
    assert report["native_basis"]["confirmed"] is False


def test_verify_detects_package_tampering(tmp_path: Path):
    source = ROOT / "paramspecs" / "komi_72_tumba_podkatnaya.json"
    package = tmp_path / "package"
    prepare_local_b3d(source, package)
    (package / "project.json").write_text("{}", encoding="utf-8")
    with pytest.raises(LocalB3dError, match="целостность"):
        verify_local_b3d(package, FIXTURES / "wardrobe_demo_ours_cloud.b3d")
