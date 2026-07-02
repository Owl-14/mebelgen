"""Декор → цвет отображения (общее для вьювера/Studio/доставки).

Цвета в производственной базе (`baza_materiala.json`) — заглушки (498× #7fb6f6,
295× −1 и т.п.), поэтому цвет выводим из ИМЕНИ декора: справочник популярных
декоров ЛДСП + стабильный древесный тон из хэша для неизвестных. Это условные
цвета показа, не текстуры (текстуры — MatBase БАЗИС, AKD-87).
"""

from __future__ import annotations

import colorsys
import hashlib
import re

# Базовые цвета популярных декоров ЛДСП (подстрока, специфичные — раньше)
DECOR_COLORS: list[tuple[str, str]] = [
    ("дуб вотан", "#9a7b52"), ("дуб сонома", "#cfa671"), ("дуб крафт", "#b98d5d"),
    ("венге", "#4b3626"), ("орех", "#7b5a3a"), ("ольха", "#c98850"),
    ("бук", "#d9a869"), ("берёза", "#e2c290"), ("береза", "#e2c290"),
    ("ясень шимо тём", "#8c7b66"), ("ясень шимо", "#cbbfa4"), ("ясень", "#d3c6ae"),
    ("махагон", "#6e3b2a"), ("вишня", "#9e4f35"), ("клён", "#e8d3ac"), ("клен", "#e8d3ac"),
    ("сосна", "#d9b57c"), ("лиственница", "#c9a06a"), ("гикори", "#a57f56"),
    ("дуб золот", "#c69a5a"), ("дуб молочный", "#e6d4b0"), ("дуб", "#c69c6d"),
    ("слоновая кость", "#ece2c8"), ("крем", "#e8dcc0"), ("беж", "#ddc9a3"),
    ("белый", "#f0efec"), ("бел", "#f0efec"),
    ("графит", "#4f5357"), ("антрацит", "#3a3d40"),
    ("чёрный", "#2e2e2e"), ("черный", "#2e2e2e"),
    ("бетон", "#a3a49e"), ("металлик", "#aeb2b8"), ("алюмин", "#b9bdc1"),
    ("хром", "#c4c8cc"), ("сер", "#9a9da1"),
    ("синий", "#4a6f9c"), ("голуб", "#7fa3c4"),
    ("зелён", "#5e7d54"), ("зелен", "#5e7d54"),
    ("красн", "#a34434"), ("бордо", "#7a2f33"), ("гранат", "#8f3a3a"),
    ("жёлт", "#d9b13b"), ("желт", "#d9b13b"), ("оранж", "#cf7b3a"),
]

# Смещение светлоты по типу детали (объём читается даже в однотонном декоре)
TYPE_SHADE = {
    "door_front": +0.04, "drawer_front": +0.04, "facade": +0.04, "screen": +0.04,
    "shelf": +0.07, "drawer_bottom": +0.07,
    "top": -0.03, "bottom": -0.03,
    "plinth": -0.10, "back": -0.12, "drawer_back": -0.12,
    "side_left": 0.0, "side_right": 0.0, "vertical_partition": 0.0,
    "drawer_side_left": 0.0, "drawer_side_right": 0.0,
    "_default": 0.0,
}
# Типы-фасады: красятся из facade-слота, если он задан
FACADE_TYPES = ("door_front", "drawer_front", "facade", "screen")

_GENERIC = ("", "по согласованию", "—", "-", "лдсп", "мдф", "двп")


def hex_to_hls(h: str) -> tuple[float, float, float]:
    h = h.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    return colorsys.rgb_to_hls(r, g, b)


def hls_to_hex(h: float, l: float, s: float) -> str:
    r, g, b = colorsys.hls_to_rgb(h, min(max(l, 0.04), 0.97), s)
    return "#{:02x}{:02x}{:02x}".format(round(r * 255), round(g * 255), round(b * 255))


def decor_base(decor: str | None) -> str | None:
    """Имя декора → базовый hex. Неизвестный (но заданный) — стабильный тон из хэша."""
    d = (decor or "").strip().lower()
    if not d or d in _GENERIC:
        return None
    for key, hexcol in DECOR_COLORS:
        if key in d:
            return hexcol
    x = int(hashlib.md5(d.encode("utf-8")).hexdigest()[:6], 16)   # стабильно по имени
    hue = 0.055 + (x % 97) / 97 * 0.055          # 20°..40° — древесная гамма
    light = 0.50 + (x // 97 % 89) / 89 * 0.22    # 0.50..0.72
    sat = 0.30 + (x // 8633 % 83) / 83 * 0.15    # 0.30..0.45
    return hls_to_hex(hue, light, sat)


def shade(base_hex: str, dl: float) -> str:
    h, l, s = hex_to_hls(base_hex)
    return hls_to_hex(h, l + dl, s)


def decor_label(item_name: str) -> str:
    """Имя позиции базы → короткая метка декора.

    «ЛДСП белый 16 мм Дуб золотой P 002» → «Дуб золотой P 002»;
    «ЛДСП, 3 мм, белый гл.» → «белый гл.». Не распарсилось — имя целиком.
    """
    s = re.sub(r"^\s*(лдсп|мдф|дсп|двп|хдф|тдсп|дсп\.?)\b[^0-9]*\d+(?:[.,]\d+)?\s*мм[\s,.—-]*",
               "", item_name, flags=re.I)
    s = s.strip(" ,.—-")
    return s or item_name


def build_palette(carcass_decor: str | None, facade_decor: str | None = None,
                  default: dict[str, str] | None = None) -> dict[str, str] | None:
    """Палитра типов деталей из декоров корпуса и (опц.) фасадов.

    Нет ни одного декора → None (звонящий подставит дефолт).
    """
    base = decor_base(carcass_decor)
    fac = decor_base(facade_decor)
    if base is None and fac is None:
        return None
    if base is None:                                  # задан только фасад
        base = (default or {}).get("side_left", "#caa06a")
    bh, bl, bs = hex_to_hls(base)
    pal = {t: hls_to_hex(bh, bl + dl, bs) for t, dl in TYPE_SHADE.items()}
    if fac is not None:
        fh, fl, fs = hex_to_hls(fac)
        for t in FACADE_TYPES:
            pal[t] = hls_to_hex(fh, fl + TYPE_SHADE.get(t, 0.0), fs)
    pal["_edge"] = hls_to_hex(bh, max(bl - 0.30, 0.06), min(bs * 1.1, 1.0))
    return pal
