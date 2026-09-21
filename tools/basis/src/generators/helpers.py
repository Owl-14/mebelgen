"""Общие хелперы координат (RULES §1–3) и сборка project.json."""

from __future__ import annotations

from typing import Any

# orientation -> (ось ширины, ось высоты) для dimensions
_DIM_AXES = {"horizont": ("x", "z"), "vertical": ("z", "y"), "front": ("x", "y")}


def _r(v: float) -> float:
    v = round(float(v), 2)
    return int(v) if v == int(v) else v


def panel(
    name: str,
    ptype: str,
    orient: str,
    x: tuple[float, float],
    y: tuple[float, float],
    z: tuple[float, float],
    *,
    thickness: float,
    material: str,
    edges: float = 0.4,
    estimated: bool = False,
    section_id: str | None = None,
    shape: str | None = None,
    radius: float | None = None,
) -> dict[str, Any]:
    x1, x2 = x
    y1, y2 = y
    z1, z2 = z
    wa, ha = _DIM_AXES[orient]
    span = {"x": x2 - x1, "y": y2 - y1, "z": z2 - z1}
    p: dict[str, Any] = {
        "name": name,
        "type": ptype,
        "basis_orientation": orient,
        "material": material,
        "thickness": _r(thickness),
        "dimensions": {"width": _r(span[wa]), "height": _r(span[ha])},
        "placement": {"x1": _r(x1), "x2": _r(x2), "y1": _r(y1), "y2": _r(y2), "z1": _r(z1), "z2": _r(z2)},
        "position": {"x": _r(x1), "y": _r(y1), "z": _r(z1)},
        "rotation": {"x": 0, "y": 0, "z": 0},
        "edge_banding": {"top": edges, "bottom": edges, "left": edges, "right": edges},
        "estimated": estimated,
    }
    if section_id:
        p["section_id"] = section_id
    if shape:
        p["shape"] = shape
    if radius is not None:
        p["radius"] = _r(radius)
    return p


def carcass(W: float, D: float, H: float, T: float, T_back: float, Hleg: float, mat: str, mat_back: str,
            *, leg_as_panel: bool = False, leg_type: str = "", z_front: float = 0,
            top_z: tuple[float, float] | None = None, socle_full: bool = False,
            socle_recess: float = 50, sides_over_top: bool = False,
            t_top: float = 0) -> list[dict[str, Any]]:
    """Короб top_bottom_over_sides: дно, крышка, боковины, задник (+ опц. цоколь-панель).

    z_front — фронтальный инсет дна/боковин/задника (по умолчанию 0, заподлицо).
    top_z — переопределение Z крышки/столешницы (свес), напр. (-80, 270).
    socle_full — цоколь на всю ширину (для tv-тумб).
    socle_recess — утопление цоколя от фронта (AKD-180): фасады выступают
    перед корпусом, цоколь заподлицо читался «ступенькой»; 0 = заподлицо.
    sides_over_top — схема стыка ВЕРХА (AKD-226): боковины идут до самого
    верха (H) и перекрывают крышку, крышка — в проём между ними.
    t_top — толщина крышки, если отличается от плиты (AKD-218): «корпус 16,
    крышка МДФ 25» из реальных ТЗ; 0 → равна T.
    """
    tt = t_top or T
    yb, yt = Hleg + T, H - tt
    tz = top_z if top_z is not None else (z_front, D)
    side_y2 = H if sides_over_top else yt
    top_x = (T, W - T) if sides_over_top else (0, W)
    out = [
        panel("Дно", "bottom", "horizont", (0, W), (Hleg, Hleg + T), (z_front, D), thickness=T, material=mat),
        panel("Крышка", "top", "horizont", top_x, (H - tt, H), tz, thickness=tt, material=mat),
        panel("Боковина левая", "side_left", "vertical", (0, T), (yb, side_y2), (z_front, D), thickness=T, material=mat),
        panel("Боковина правая", "side_right", "vertical", (W - T, W), (yb, side_y2), (z_front, D), thickness=T, material=mat),
        panel("Задняя стенка", "back", "front", (T, W - T), (yb, yt), (D - T_back, D), thickness=T_back, material=mat_back),
    ]
    if Hleg > 0 and leg_as_panel:
        sx = (0, W) if socle_full else (T, W - T)
        r = max(0.0, min(socle_recess, D - T_back - T))
        out.append(panel("Цоколь", "plinth", "front", sx, (0, Hleg), (r, r + T),
                         thickness=T, material=mat, estimated=True))
    return out


def shelf_levels(y_bottom: float, y_top: float, n: int, T: float) -> list[float]:
    """Низы N полок, равномерно в проёме [y_bottom, y_top]. gap округляется до мм."""
    if n <= 0:
        return []
    span = y_top - y_bottom
    gap = round((span - n * T) / (n + 1))
    return [y_bottom + (k + 1) * gap + k * T for k in range(n)]


def shelves(levels: list[float], W: float, D: float, T: float, T_back: float, mat: str, section_id: str,
            *, x1: float = None, x2: float = None) -> list[dict[str, Any]]:
    """Горизонтальные полки на заданных уровнях, z от T до D-T_back."""
    xa = T if x1 is None else x1
    xb = (W - T) if x2 is None else x2
    out = []
    for i, y in enumerate(levels, start=1):
        out.append(panel(f"Полка {i}", "shelf", "horizont", (xa, xb), (y, y + T), (T, D - T_back),
                         thickness=T, material=mat, section_id=section_id))
    return out


def facade_band(Hleg: float, H: float, T: float, *, has_overhang: bool = False,
                reveal: float = 2.0) -> tuple[float, float]:
    """Вертикальная полоса накладных фасадов: перекрывают дно и крышку.

    Низ — чуть выше ножек/цоколя (Hleg+reveal, закрывает торец дна).
    Верх — под крышкой (H−reveal, закрывает её торец), либо под столешницей
    (H−T−reveal), если сверху свес (столешница не закрывается фасадом)."""
    bottom = round(Hleg + reveal, 2)
    top = round((H - T - reveal) if has_overhang else (H - reveal), 2)
    return bottom, top


def overlay_door(W: float, H: float, T: float, Hleg: float, gap: float, mat: str, section_id: str,
                 name: str = "Фасад двери", *, x1: float = None, x2: float = None,
                 y1: float = None, y2: float = None) -> dict[str, Any]:
    """Накладной фасад: z −T..0 (ПЕРЕД корпусом, не в его плоскости — иначе
    фасад врезается в боковину). По периметру зазор gap."""
    ax1 = gap if x1 is None else x1
    ax2 = (W - gap) if x2 is None else x2
    ay1 = (Hleg + T + gap) if y1 is None else y1
    ay2 = (H - T - gap) if y2 is None else y2
    return panel(name, "door_front", "front", (ax1, ax2), (ay1, ay2), (-T, 0),
                 thickness=T, material=mat, section_id=section_id)


# ------------------------------------------------------------------ кромка (AKD-260)
#
# Кромка по назначению торца (rules/materials.md: видимые — edge_band_thickness,
# скрытые — 0.4, стыковые с ДВП/дном ящика — без кромки), а не 0.4 на всё.
# Ключи edge_banding — в осях пласти детали: left/right — торцы по оси ширины
# контура (min/max), bottom/top — по оси высоты (min/max):
#   horizont: ширина=X, высота=Z → bottom = ПЕРЕДНИЙ торец (z1);
#   vertical: ширина=Z, высота=Y → left  = ПЕРЕДНИЙ торец (z1);
#   front:    ширина=X, высота=Y.
_FACADE_EDGE_TYPES = {"door_front", "drawer_front", "facade", "screen"}
_NO_EDGE_TYPES = {"back", "drawer_bottom"}


def apply_edge_policy(panels: list[dict[str, Any]], spec: dict[str, Any]) -> None:
    # реверс эталона технолога (AKD-287, rules/b3d_production_reference.md):
    # фасады — edge_band_thickness (2) по кругу; ВИДИМЫЕ не-фасадные торцы —
    # 0.5; СКРЫТЫЕ (в стыках, к заднику) — совсем без кромки
    m = spec.get("materials") or {}
    v = float(m.get("edge_band_thickness") or 2.0)
    # ТЗ часто несёт «0.4»/«0.5» — это класс тонкой кромки видимых торцов, а не
    # фасадной: фасады у технолога всегда 2 мм по кругу. Значение < 1 не
    # переносим на фасады.
    if v < 1:
        v = 2.0
    vis = int(v) if v.is_integer() else v
    thin = 0.5
    for p in panels:
        t = str(p.get("type") or "")
        orient = str(p.get("basis_orientation") or "front")
        if t in _FACADE_EDGE_TYPES:
            eb = {"top": vis, "bottom": vis, "left": vis, "right": vis}
        elif t in _NO_EDGE_TYPES:
            eb = {"top": 0, "bottom": 0, "left": 0, "right": 0}
        elif t in ("drawer_side_left", "drawer_side_right", "drawer_back"):
            # короб ящика: видны верхний, передний и задний торцы (эталон)
            eb = {"top": thin, "bottom": 0, "left": thin, "right": thin}
        elif orient in ("horizont", "horizontal"):
            # дно/крышка перекрывают боковины: перед + X-торцы видны, зад скрыт;
            # полки/цоколь прячут торцы в стыках — только перед
            side = thin if t in ("top", "bottom") else 0
            eb = {"bottom": thin, "top": 0, "left": side, "right": side}
        elif orient == "vertical":
            # боковины/перегородки: виден только передний торец
            eb = {"left": thin, "right": 0, "top": 0, "bottom": 0}
        else:
            eb = {"top": vis, "bottom": vis, "left": vis, "right": vis}
        p["edge_banding"] = eb


def build_project(spec: dict[str, Any], panels: list[dict[str, Any]], *,
                  sections: list[dict[str, Any]] | None = None,
                  drawers: list[dict[str, Any]] | None = None,
                  doors: list[dict[str, Any]] | None = None,
                  carcass_calc: dict[str, Any] | None = None,
                  rods: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Собрать project.json по furniture.schema.json из панелей и ParamSpec."""
    apply_edge_policy(panels, spec)               # кромка по назначению (AKD-260)
    dim = spec["dimensions"]
    m = spec["materials"]
    T_back = m.get("back_thickness", m["board_thickness"])
    hw = spec.get("hardware", {}) or {}
    legs = spec.get("legs", {}) or {}
    gaps = spec.get("gaps", {}) or {}
    handles = hw.get("handles") or {"type": "нет", "material": "—", "color": "—", "size": 0,
                                     "count": 0, "offset_from_top": 0, "furniture_encoded": ""}
    # count по умолчанию из габарита (AKD-178): 4, шире 1200 — 6
    _lt = str(legs.get("type", "нет")).lower()
    _n_legs = legs.get("count", 0)
    if not _n_legs and legs.get("height", 0) and not legs.get("as_panel") \
            and _lt not in ("нет", "", "-", "—"):
        _n_legs = 6 if dim["width"] > 1200 else 4
    legs_block = {"type": legs.get("type", "нет"), "adjustable": legs.get("adjustable", False),
                  "color": legs.get("color", "—"), "height": legs.get("height", 0),
                  "count": _n_legs}
    project: dict[str, Any] = {
        "project_name": spec["project_name"],
        "furniture_type": spec.get("furniture_type", spec["archetype"]),
        "overall_dimensions": {"width": dim["width"], "depth": dim["depth"], "height": dim["height"],
                               "tolerance": dim.get("tolerance", 5)},
        "materials": {
            "board_material": m.get("board_material", "ЛДСП"),
            "board_thickness": m["board_thickness"],
            "back_wall_material": m.get("back_material", m.get("board_material", "ЛДСП")),
            "edge_band_thickness": m.get("edge_band_thickness", 0.4),
            "color": m.get("color", "по согласованию"),
            "color_code": m.get("color_code", ""),
            # Привязка к производственной базе + отдельный декор фасадов (опц.)
            **{k: m[k] for k in ("board_article", "facade_color", "facade_color_code",
                                 "facade_article", "top_thickness", "texture_direction")
               if m.get(k)},
        },
        "sections": sections or [],
        "panels": panels,
        "drawers": drawers or [],
        "doors": doors or [],
        "hardware": {"handles": handles, "legs": legs_block,
                     # выбранные позиции базы по слотам {slot: article} (Studio A4)
                     **({"selection": hw["selection"]} if hw.get("selection") else {})},
        "constraints": {
            "default_gap": gaps.get("default", 2),
            "drawer_gap": gaps.get("default", 2),
            "door_gap": gaps.get("facade", 2),
            "back_wall_offset": T_back,
            "construction": "top_bottom_over_sides",
        },
        "basis_mapping": {"coordinate_system": "basis_mebelshik", "units": "mm",
                          "ready_for_import": True, "placement_required": True},
        "warnings": spec.get("warnings", []),
        "estimated_values": spec.get("estimated_values", []),
    }
    if rods:
        project["hardware"]["rods"] = rods
    if hw.get("drawer_guides"):
        project["hardware"]["drawer_guides"] = hw["drawer_guides"]
    if hw.get("hinges"):
        project["hardware"]["hinges"] = hw["hinges"]
    if hw.get("locks"):
        project["hardware"]["locks"] = hw["locks"]
    if carcass_calc:
        project["carcass_calculation"] = carcass_calc
    return project
