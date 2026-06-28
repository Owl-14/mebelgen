"""Parse a raw spec item (name + characteristics text) into a FurnitureSpec."""
from __future__ import annotations

import re
from typing import Optional

from ..model.spec import Dimensions, FurnitureSpec, Material
from .doc_reader import RawItem

# Cyrillic 'х' and latin 'x' are both used as the dimension separator.
_SEP = "[xх×*]"
_NUM = r"(\d+(?:[.,]\d+)?)"


def _f(s: str) -> Optional[float]:
    if s is None:
        return None
    try:
        return float(s.replace(",", "."))
    except ValueError:
        return None


def _parse_dimensions(text: str) -> Dimensions:
    d = Dimensions()
    low = text.lower()

    # Round: "диаметр 500", "даметр 500", "Ø500"
    m_dia = re.search(r"(?:диаметр|даметр|ø|d)\s*[:=]?\s*" + _NUM, low)
    m_h = None
    if m_dia:
        d.diameter = _f(m_dia.group(1))
        m_h = re.search(r"высот[аы]?\s*[:=]?\s*" + _NUM, low)
        if m_h:
            d.h = _f(m_h.group(1))
        return d

    # Triple WxDxH, tolerating ± tolerances and spaces:
    # 1800х800х750 ; 800±10x400±10x900±10 ; 1000 х 400х1800
    triple = re.search(
        _NUM + r"(?:±\d+)?\s*" + _SEP + r"\s*"
        + _NUM + r"(?:±\d+)?\s*" + _SEP + r"\s*"
        + _NUM + r"(?:±\d+)?",
        low,
    )
    if triple:
        d.w, d.d, d.h = (_f(triple.group(1)), _f(triple.group(2)), _f(triple.group(3)))
    return d


def _parse_material(text: str) -> Material:
    mat = Material(raw=text)
    low = text.lower()
    if "мдф" in low:
        mat.body = "МДФ"
    # Color system + code
    m = re.search(r"ral\s*([0-9]{3,4})", low)
    if m:
        mat.color_system, mat.color_code = "RAL", m.group(1)
    else:
        m = re.search(r"n[cs]s\s*([0-9a-z\-]+)", low)
        if m:
            mat.color_system, mat.color_code = "NCS", m.group(1).upper()
    mat.matte = "глянц" not in low
    return mat


def _count_near(text: str, word_pattern: str) -> Optional[int]:
    """Find an integer count near a keyword, e.g. 'полки ... 4 шт' or '3 ящика'."""
    low = text.lower()
    # number before word: "три выдвижных ящика", "4 полки"
    m = re.search(_NUM + r"\s*(?:шт\.?\s*)?" + word_pattern, low)
    if m:
        return int(float(m.group(1).replace(",", ".")))
    # word ... number шт: "полки ... 4 шт"
    m = re.search(word_pattern + r"[^.]{0,60}?" + _NUM + r"\s*шт", low)
    if m:
        return int(float(m.group(1).replace(",", ".")))
    return None


_RU_NUM = {"один": 1, "одна": 1, "одну": 1, "два": 2, "две": 2, "три": 3,
           "четыре": 4, "пять": 5}


def _ru_count(text: str, word_pattern: str) -> Optional[int]:
    low = text.lower()
    for word, val in _RU_NUM.items():
        if re.search(word + r"\s+(?:\w+\s+){0,2}?" + word_pattern, low):
            return val
    return None


def _parse_features(text: str) -> dict:
    low = text.lower()
    f: dict = {}

    f["push_open"] = bool(re.search(r"пуш|push", low))
    f["lock"] = bool(re.search(r"замок|замк", low))
    f["lock_right"] = bool(re.search(r"замок на правой", low))
    f["plinth"] = bool(re.search(r"цокол", low))
    f["plinth_black"] = bool(re.search(r"цокол[ья]?[^.]{0,30}(чёрн|черн)", low))
    f["brass"] = bool(re.search(r"латун", low))
    f["adjustable_feet"] = bool(re.search(r"регулируем\w*\s+опор", low))
    f["felt_pads"] = bool(re.search(r"фетров", low))
    f["fluted"] = bool(re.search(r"рифлён|рифлен|флютинг|канелюр", low))
    f["matte_lacquer"] = bool(re.search(r"матов\w*\s+лак|прозрачн\w*\s+лак", low))
    f["rod"] = bool(re.search(r"штанг", low))           # clothes rod
    f["hat_shelf"] = bool(re.search(r"шапк|головн", low))
    f["shoe_shelf"] = bool(re.search(r"обув", low))
    f["handles_profile"] = bool(re.search(r"ручк\w*\s*[-–]?\s*профил|профил\w*\s+ручк", low))
    f["overlay_doors"] = bool(re.search(r"накладн", low))
    f["screen_front"] = bool(re.search(r"передн\w*\s+экран", low))
    f["cable_channel"] = bool(re.search(r"кабель", low))
    f["pc_holder"] = bool(re.search(r"систем\w*\s+блок|подвес", low))
    f["symmetric"] = bool(re.search(r"симметрич", low))
    f["adjustable_shelves"] = bool(re.search(r"полк\w*[^.]{0,40}регулир|регулир[^.]{0,40}высот", low))

    # counts
    drawers = _count_near(text, r"ящик") or _ru_count(text, r"ящик")
    if drawers:
        f["drawers"] = drawers
    shelves = _count_near(text, r"полк") or _ru_count(text, r"полк")
    if shelves:
        f["shelves"] = shelves

    # thicknesses
    th = {}
    for label, pat in [
        ("worktop", r"столешниц\w*[^.0-9]{0,20}" + _NUM + r"\s*мм"),
        ("side", r"боков\w*[^.0-9]{0,20}" + _NUM + r"\s*мм"),
        ("screen", r"экран[^.0-9]{0,20}" + _NUM + r"\s*мм"),
        ("plinth", r"цокол[^.0-9]{0,20}" + _NUM + r"\s*мм"),
    ]:
        m = re.search(pat, low)
        if m:
            th[label] = _f(m.group(1))
    if th:
        f["thickness"] = th
    return f


def _clean_lines(text: str) -> list:
    """Split characteristics into tidy bullet lines for the materials block."""
    lines = []
    for part in re.split(r"[\n;]+", text):
        part = re.sub(r"\s+", " ", part).strip(" .,-—–")
        # drop pure marketing / purchase-link noise
        if not part:
            continue
        low = part.lower()
        if any(k in low for k in ["купить", "яндекс", "маркет", "магазин",
                                   "урал декор", "предлагаем", "x-007", "x007"]):
            continue
        # drop the overall-size line (already shown in the title block)
        if re.match(r"^(габаритн\w*|размер\w*)\b", low) or re.match(r"^[\d\s.,xх×*±ød-]+$", low):
            continue
        # too-short fragments add no value
        if len(part) < 4:
            continue
        if len(part) > 96:
            part = part[:93].rstrip() + "…"
        lines.append(part)
    # de-dup while keeping order
    seen, out = set(), []
    for l in lines:
        if l.lower() not in seen:
            seen.add(l.lower())
            out.append(l)
    return out[:9]


def parse_item(raw: RawItem) -> FurnitureSpec:
    text = raw.characteristics or ""
    spec = FurnitureSpec(
        index=raw.index,
        name=re.sub(r"\s+", " ", raw.name).strip(),
        qty=raw.qty,
        unit=raw.unit or "шт.",
        raw_characteristics=text,
        dims=_parse_dimensions(text),
        material=_parse_material(text),
    )
    spec.features = _parse_features(text)
    spec.material_lines = _clean_lines(text)
    spec.image_path = getattr(raw, "image_path", None)
    spec.image_paths = getattr(raw, "image_paths", None) or []
    return spec


def apply_image_cues(spec: FurnitureSpec, analysis: dict) -> None:
    """Use shape cues from the reference photo to fix ambiguous tables.

    Only affects small/low *tables* (coffee / journal) - never wardrobes,
    desks or units that already carry doors/drawers.
    """
    if not analysis or not analysis.get("ok"):
        return
    name = spec.name.lower()
    is_small_table = ("кофейн" in name or "журнальн" in name) and \
        not spec.features.get("drawers")
    if not is_small_table or spec.dims.is_round:
        return
    if analysis.get("fluted"):
        spec.features["fluted"] = True
        if spec.dims.w:
            spec.dims.diameter = spec.dims.w  # square footprint -> Ø
    elif analysis.get("round"):
        if spec.dims.w and abs((spec.dims.w or 0) - (spec.dims.d or 0)) < 1e-6:
            spec.dims.diameter = spec.dims.w
