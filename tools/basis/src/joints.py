"""Общий детектор стыков панелей (MEB-170, эпик MEB-167).

Сейчас присадка — ветки «пара типов деталей → крепёж», и каждая ветка сама
ищет, где детали касаются. Здесь контакт находится один раз для любой пары
панелей: две грани соприкасаются (зазор ≤ TOUCH_MM) и перекрываются по двум
другим осям. Стык описывает, ЧЕМ каждая деталь касается другой — пластью
(face) или торцом (edge), — этого достаточно, чтобы таблица правил выбрала
крепёж по ролям и толщинам (шаг 4).

Пока модуль работает «в тени»: assign_holes проверяет, что каждое нынешнее
крепёжное отверстие принадлежит найденному стыку, а стыки без отверстий
показываются как незакреплённые контакты (шаг 5 сделает их ошибкой).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

AXES = ("x", "y", "z")
TOUCH_MM = 0.6          # зазор, который ещё считается контактом
MIN_OVERLAP_MM = 20.0   # меньше — касание углом/ребром, не стык

# Назначения отверстий, которые соединяют две панели (остальное — фурнитура:
# ручки, петли, направляющие, опоры, штанги, замки)
JOINERY_PURPOSES = frozenset({
    "шкант 8×30 (торец)", "шкант 8×30 (пласть)",
    "эксцентрик (канал Ø8)", "эксцентрик (чашка Ø15)", "эксцентрик (шток)",
    "евровинт (проход Ø8)", "конфирмат",
    "задник (гвоздь)", "задник (прокол Ø3)", "задник (саморез)",
    "эксцентрик полки (чашка Ø20)", "эксцентрик полки (шток)",
    "короб ящика (саморез)",
    "фасадная стяжка (эксцентрик Ø15)", "фасадная стяжка (шток)",
})


@dataclass
class Joint:
    a: str                      # имя панели (подпись) — пока единственный id
    b: str
    axis: str                   # нормаль плоскости контакта
    plane: float                # координата плоскости по axis
    a_side: str                 # чем касается a: "face" (пласть) | "edge" (торец)
    b_side: str
    rect: dict[str, tuple[float, float]]   # перекрытие по двум другим осям
    roles: tuple[str, str] = ("", "")
    thickness: tuple[float, float] = (0.0, 0.0)
    holes: list[dict[str, Any]] = field(default_factory=list)

    @property
    def kind(self) -> str:
        """edge-face | face-face | edge-edge (торец к пласти и т.п.)."""
        return "-".join(sorted((self.a_side, self.b_side), key=("edge", "face").index))

    @property
    def length(self) -> float:
        return max(hi - lo for lo, hi in self.rect.values())


def _box(panel: dict[str, Any]) -> dict[str, tuple[float, float]] | None:
    pl = panel.get("placement")
    if not isinstance(pl, dict):
        return None
    try:
        return {a: (float(pl[f"{a}1"]), float(pl[f"{a}2"])) for a in AXES}
    except (KeyError, TypeError, ValueError):
        return None


def _thin_axis(box: dict[str, tuple[float, float]]) -> str:
    return min(AXES, key=lambda a: box[a][1] - box[a][0])


def _role(panel: dict[str, Any]) -> str:
    return str(panel.get("role") or panel.get("type") or "")


def _contact(a: dict[str, tuple[float, float]], b: dict[str, tuple[float, float]]
             ) -> tuple[str, float] | None:
    """Ось и плоскость касания двух брусков; None — не касаются."""
    for axis in AXES:
        for a_end, b_end in ((1, 0), (0, 1)):
            if abs(a[axis][a_end] - b[axis][b_end]) > TOUCH_MM:
                continue
            others = [o for o in AXES if o != axis]
            # торец касается пласти полосой шириной в толщину детали (16, у
            # задника 4) — порог по оси не больше 80% меньшего из размеров
            if all(min(a[o][1], b[o][1]) - max(a[o][0], b[o][0])
                   >= min(MIN_OVERLAP_MM, 0.8 * min(a[o][1] - a[o][0], b[o][1] - b[o][0]))
                   for o in others):
                return axis, (a[axis][a_end] + b[axis][b_end]) / 2
    return None


def detect_joints(panels: list[dict[str, Any]]) -> list[Joint]:
    """Все стыки «панель-панель» изделия (без фурнитуры и тел-не-панелей)."""
    boxes = [(p, _box(p)) for p in panels]
    # фигурные детали (диск, цилиндр) — по габаритному бруску: для поиска
    # контакта хватает, точный контур придёт с MEB-174
    boxes = [(p, b) for p, b in boxes if b is not None]
    out: list[Joint] = []
    for i, (pa, ba) in enumerate(boxes):
        for pb, bb in boxes[i + 1:]:
            hit = _contact(ba, bb)
            if hit is None:
                continue
            axis, plane = hit
            others = [o for o in AXES if o != axis]
            rect = {o: (max(ba[o][0], bb[o][0]), min(ba[o][1], bb[o][1])) for o in others}
            out.append(Joint(
                a=str(pa.get("name")), b=str(pb.get("name")), axis=axis, plane=round(plane, 2),
                a_side="face" if _thin_axis(ba) == axis else "edge",
                b_side="face" if _thin_axis(bb) == axis else "edge",
                rect={o: (round(lo, 2), round(hi, 2)) for o, (lo, hi) in rect.items()},
                roles=(_role(pa), _role(pb)),
                thickness=(float(pa.get("thickness") or 0), float(pb.get("thickness") or 0))))
    return out


def _hole_in_joint(hole: dict[str, Any], joint: Joint) -> bool:
    """Отверстие крепит этот стык: сверлится в одну из двух деталей, лежит над
    площадкой контакта и не дальше длины крепежа от плоскости стыка."""
    if str(hole.get("panel")) not in (joint.a, joint.b):
        return False
    reach = float(hole.get("depth") or 0) + 50.0      # чашка эксцентрика — 35 от стыка
    if abs(float(hole.get(joint.axis) or 0) - joint.plane) > reach:
        return False
    return all(lo - 25.0 <= float(hole.get(o) or 0) <= hi + 25.0
               for o, (lo, hi) in joint.rect.items())


def assign_holes(joints: list[Joint], holes: list[dict[str, Any]]
                 ) -> list[dict[str, Any]]:
    """Разложить крепёжные отверстия по стыкам; вернуть те, что ни к одному не легли."""
    orphans: list[dict[str, Any]] = []
    for hole in holes:
        if hole.get("purpose") not in JOINERY_PURPOSES:
            continue
        candidates = [j for j in joints if _hole_in_joint(hole, j)]
        if not candidates:
            orphans.append(hole)
            continue
        best = min(candidates, key=lambda j: abs(float(hole.get(j.axis) or 0) - j.plane))
        best.holes.append(hole)
    return orphans


def joint_report(project: dict[str, Any], holes: list[dict[str, Any]]) -> dict[str, Any]:
    """Сводка для тени: стыки, отверстия вне стыков, стыки без крепежа."""
    joints = detect_joints(project.get("panels") or [])
    orphans = assign_holes(joints, holes)
    return {
        "joints": joints,
        "orphan_holes": orphans,
        "unfastened": [j for j in joints if not j.holes],
    }
