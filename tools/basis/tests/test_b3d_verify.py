"""Паритет Studio ↔ .b3d (AKD-168/169): метизы в .cfrn и сверка собранного .b3d."""

from __future__ import annotations

import io
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.generators import generate_from_paramspec       # noqa: E402
from src.cfrn import project_to_cfrn_bytes, cfrn_holes   # noqa: E402
from src.hardware import compute_drilling                # noqa: E402


def _project(name: str):
    spec = json.loads((ROOT / "paramspecs" / f"{name}.json").read_text(encoding="utf-8"))
    return generate_from_paramspec(spec)


def test_cfrn_has_fastener_bodies():
    """.cfrn несёт OBJ-меши метизов + материалы с именами (AKD-168)."""
    p = _project("stol_ofisny_foto")
    z = zipfile.ZipFile(io.BytesIO(project_to_cfrn_bytes(p)))
    objs = [n for n in z.namelist() if n.endswith(".obj")]
    assert len(objs) >= 4                                # шкант/чашка/шток/конфирмат
    d = json.loads(z.read("file.json"))
    mats = [m["name"] for m in d["table"]["materials"]]
    assert any("Конфирмат" in m for m in mats)
    assert any("Шкант" in m for m in mats)
    # triangleData — int-индексы в table.triangles (формат эталона)
    f5 = [o for o in d["table"]["objects"] if o.get("objType") == 5 and o.get("holes")]
    assert f5 and all(isinstance(o["triangleData"], int) for o in f5)
    # OBJ валиден: вершины и грани
    obj_text = z.read(objs[0]).decode("utf-8")
    assert "v " in obj_text and "f " in obj_text


def test_cfrn_holes_roundtrip_multiset():
    """Реконструкция отверстий из .cfrn (все инстансы × матрицы) = compute_drilling."""
    for name in ("stol_ofisny_foto", "komi_72_tumba_podkatnaya"):
        p = _project(name)
        src = compute_drilling(p)
        enc = cfrn_holes(p)
        assert len(src) == len(enc), name
        s = sorted((round(h["x"], 1), round(h["y"], 1), round(h["z"], 1)) for h in src)
        e = sorted((round(h["x"], 1), round(h["y"], 1), round(h["z"], 1)) for h in enc)
        assert s == e, name


def test_verify_parity_on_built_b3d():
    """Собранный облаком .b3d (реальный артефакт) проходит сверку паритета."""
    b3d = Path(r"D:\claude\bazis\out\stol_metizy.b3d")
    if not b3d.is_file():
        import pytest
        pytest.skip("нет собранного .b3d (платный артефакт)")
    from src.materials import resolve_project_materials
    from src.b3d_verify import verify_b3d_parity
    p = _project("stol_ofisny_foto")
    p["material_refs"] = resolve_project_materials(p)
    r = verify_b3d_parity(b3d, p)
    assert r["ok"], r
    assert r["mesh_blobs"] >= r["instances_encoded"]
