"""Собственная сборка .b3d (формат версии 15) сверяется с облачным эталоном.

Эталон `qa/fixtures/wardrobe_demo_ours_cloud.b3d` — то, что облако CfrnToB3d
построило из нашего же .cfrn для wardrobe_demo. Панели должны совпасть по
имени, положению (Trans + кватернион), контуру и толщине. Крепёж сверяется
через verify_b3d_parity (сигнатуры присадок), потому что эталон собран до
изменений системы 32 и набор метизов у него другой.
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.b3d_builder import (  # noqa: E402
    FORMAT_DOCUMENT_VERSION,
    FORMAT_HEADER_VERSION,
    delphi_datetime,
    project_to_b3d_bytes,
    quaternion_from_cfrn_matrix,
    rectangle_contour,
)
from src.b3d_format import child, parse_b3d  # noqa: E402
from src.b3d_verify import verify_b3d_parity  # noqa: E402
from src.generators import generate_from_paramspec  # noqa: E402

CLOUD_FIXTURE = ROOT / "qa" / "fixtures" / "wardrobe_demo_ours_cloud.b3d"


def _project():
    spec = json.loads((ROOT / "paramspecs" / "wardrobe_demo.json").read_text(encoding="utf-8"))
    return generate_from_paramspec(spec)


def _panels(document):
    """name → (Trans-кортеж, Contour, Thick) по всему дереву Model, включая сборки."""
    result = {}

    def walk(node):
        for obj in child(node, "Objs")[2]:
            kind = child(obj, "Type")[2]
            if kind == 4002:
                trans = child(obj, "Trans")
                result[child(obj, "Name")[2]] = (
                    tuple(round(child(trans, key)[2], 4) for key in ("X", "Y", "Z", "Rx", "Ry", "Rz", "Rw")),
                    child(obj, "Contour")[2],
                    child(obj, "Thick")[2],
                )
            elif kind == 1005:
                walk(obj)

    walk(child(document, "Model")[2][0])
    return result


def _contour_size(blob: bytes) -> tuple[float, float]:
    """(w, h) прямоугольного контура из блоба: u32 count + отрезки 0x10 с 4 double."""
    import struct

    count = struct.unpack_from("<I", blob, 0)[0]
    xs, ys, p = [], [], 4
    for _ in range(count):
        assert blob[p] == 0x10
        x1, y1, x2, y2 = struct.unpack_from("<4d", blob, p + 1)
        xs += [x1, x2]; ys += [y1, y2]; p += 33
    return round(max(xs) - min(xs), 2), round(max(ys) - min(ys), 2)


def test_quaternion_matches_cloud_convention():
    # горизонтальная панель «Дно» из .cfrn-представления wardrobe_demo:
    # облако записало для неё Rx,Ry,Rz,Rw = (-0.7071, 0, 0, 0.7071)
    from src.cfrn import project_to_cfrn_json

    doc = project_to_cfrn_json(_project())
    objects = doc["table"]["objects"]
    bottom = next(
        node for node in doc["model"]["objs"][0]["objs"]
        if objects[node["tableIndex"]].get("name") == "Дно"
    )
    qx, qy, qz, qw = quaternion_from_cfrn_matrix(bottom["matrix"])
    assert (round(qx, 4), round(qy, 4), round(qz, 4), round(qw, 4)) == (-0.7071, 0.0, 0.0, 0.7071)
    identity = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 5, 6, 7, 1]
    assert quaternion_from_cfrn_matrix(identity) == (0.0, 0.0, 0.0, 1.0)


def test_rectangle_contour_is_four_closed_lines():
    blob = rectangle_contour(576, 600)
    assert len(blob) == 4 + 4 * 33
    assert blob[:5] == b"\x04\x00\x00\x00\x10"


def test_delphi_datetime_epoch():
    assert delphi_datetime(dt.datetime(1899, 12, 30)) == 0.0
    assert abs(delphi_datetime(dt.datetime(2026, 7, 16, 12, 0)) - 46219.5) < 1e-9


def test_builder_output_is_version_15_without_trailer_and_deterministic():
    project = _project()
    moment = dt.datetime(2026, 9, 11, 12, 0, 0)
    first = project_to_b3d_bytes(project, saved_at=moment)
    second = project_to_b3d_bytes(project, saved_at=moment)
    assert first == second
    doc = parse_b3d(first)
    assert doc["trailer"] == b""
    header, document = (root for _flag, root in doc["sections"])
    assert child(header, "Version")[2] == FORMAT_HEADER_VERSION == 15
    assert (child(document, "VersionMajor")[2], child(document, "VersionMinor")[2]) == FORMAT_DOCUMENT_VERSION
    assert child(document, "HardwareKey") is None


def test_panels_match_cloud_oracle_and_parity_passes(tmp_path: Path):
    project = _project()
    data = project_to_b3d_bytes(project)
    ours = _panels(parse_b3d(data)["sections"][1][1])
    cloud = _panels(parse_b3d(CLOUD_FIXTURE.read_bytes())["sections"][1][1])
    assert set(ours) == set(cloud)
    # Эталон облака собран, когда перегородка и полки при накладном заднике
    # заканчивались на толщину задника раньше боковин. Теперь они идут до
    # задней плоскости корпуса (как у технолога), поэтому у этих деталей
    # контур глубже ровно на толщину задника; положение и толщина те же.
    back_thickness = 3.2
    deepened = {"Перегородка 1"} | {f"Полка левая (left) {i}" for i in range(2, 5)}
    # Сознательно ушли от эталона облака (по файлу технолога): задник на 1 мм
    # внутрь от габарита, полка над стеком ящиков сидит в зоне фасадов и
    # верхний фасад заканчивается на зазор ниже её верха, соседняя полка
    # выравнивается по ней (AKD-190).
    moved = {"Задняя стенка", "Полка под нишей", "Фасад ящик 3", "Полка левая (left) 1"}
    mismatched = sorted(name for name in cloud
                        if name not in deepened | moved and cloud[name] != ours[name])
    assert mismatched == []
    for name in deepened:
        (trans_c, contour_c, thick_c), (trans_o, contour_o, thick_o) = cloud[name], ours[name]
        assert trans_c == trans_o and thick_c == thick_o, name
        wc, hc = _contour_size(contour_c)
        wo, ho = _contour_size(contour_o)
        # глубина полки лежит по y контура, глубина перегородки — по x
        grown = sorted((round(wo - wc, 2), round(ho - hc, 2)))
        assert grown == [0.0, back_thickness], (name, wc, hc, wo, ho)

    path = tmp_path / "wardrobe_demo.b3d"
    path.write_bytes(data)
    report = verify_b3d_parity(path, project)
    assert report["ok"], report["errors"]
    assert report["panels"]["actual"] == len(project["panels"]) == 27
    assert report["drilling"]["actual"] == report["drilling"]["expected"] > 0
