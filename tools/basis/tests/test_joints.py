"""Общий детектор стыков в тени (MEB-170): каждое крепёжное отверстие — у стыка.

Детектор не меняет присадку. Он проверяет, что нынешний крепёж объясняется
найденными контактами панелей, — основа таблицы правил стыков (MEB-171).
Известные дефекты конструкций, которые он нашёл, перечислены явно: шаг 5
(MEB-172) превратит их в ошибки проверки.
"""

from __future__ import annotations

import json

import pytest

from src.generators import generate_from_paramspec
from src.hardware import compute_drilling
from src.joints import JOINERY_PURPOSES, detect_joints, joint_report
from tests.drilling_snapshot import _specs

# Известное: фасадная стяжка шкафа модератора тянется к коробу, который не
# касается фасада (box_z1=20 в спеке) — крепёж через воздух.
KNOWN_ORPHANS = {"moderator_cabinet": {"фасадная стяжка (эксцентрик Ø15)",
                                       "фасадная стяжка (шток)"}}


def _project(path):
    spec = json.loads(path.read_text(encoding="utf-8"))
    if spec.get("draft"):
        pytest.skip("черновик")
    return generate_from_paramspec(spec)


@pytest.mark.parametrize("path", _specs(), ids=lambda p: p.stem)
def test_every_joinery_hole_belongs_to_a_detected_joint(path):
    project = _project(path)
    report = joint_report(project, compute_drilling(project))
    allowed = KNOWN_ORPHANS.get(path.stem, set())
    orphans = [(h["purpose"], h["panel"]) for h in report["orphan_holes"]
               if h["purpose"] not in allowed]
    assert not orphans, f"крепёж вне стыков: {orphans[:6]}"


def test_technologist_wardrobe_joints_and_contact_kinds():
    from tests.drilling_snapshot import ROOT

    spec = json.loads((ROOT / "paramspecs" / "wardrobe_demo_technolog.json").read_text(encoding="utf-8"))
    project = generate_from_paramspec(spec)
    joints = detect_joints(project["panels"])
    by_pair = {frozenset((j.roles[0], j.roles[1])): j for j in joints}
    # боковина стоит на дне — торец боковины к пласти дна
    side_bottom = by_pair[frozenset(("side_left", "bottom"))]
    assert side_bottom.kind == "edge-face" and side_bottom.axis == "y"
    # задник касается корпуса пластью, корпус — торцами
    assert any(j.kind == "edge-face" and "back" in j.roles for j in joints)
    report = joint_report(project, compute_drilling(project))
    assert not report["orphan_holes"]
    fastened = [j for j in report["joints"] if j.holes]
    assert all(h["purpose"] in JOINERY_PURPOSES for j in fastened for h in j.holes)


def test_drawer_box_flush_to_partition_is_visible_as_a_contact():
    """Тумба ТВ: guide_gap=0 в спеке — короб вплотную к перегородке, места под
    направляющую нет. Детектор видит это как контакт пласть-к-пласти."""
    from tests.drilling_snapshot import ROOT

    spec = json.loads((ROOT / "paramspecs" / "tumba_tv_1600x450x600.json").read_text(encoding="utf-8"))
    joints = detect_joints(generate_from_paramspec(spec)["panels"])
    flush = [j for j in joints if j.kind == "face-face"
             and {"vertical_partition"} & set(j.roles) and any("drawer_side" in r for r in j.roles)]
    assert len(flush) == 4
