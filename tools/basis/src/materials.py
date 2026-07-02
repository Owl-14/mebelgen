"""Справочник материалов/фурнитуры (AKD-11).

Стабильные id + кандидаты точных имён БАЗИС (basisName). Назначение — выбирать
реальные материалы из базы, а не «выдумывать» имена по тексту ТЗ. Точные basisName
сверяются/правятся при доступной лицензии БАЗИС (MatBase, AKD-12).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

CATALOG_PATH = Path(__file__).resolve().parent.parent / "materials" / "catalog.json"


def load_catalog(path: Path | None = None) -> dict[str, Any]:
    return json.loads((path or CATALOG_PATH).read_text(encoding="utf-8"))


def _all_entries(cat: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for key in ("boards", "backs", "edges", "hardware"):
        out += cat.get(key, [])
    return out


def by_id(ref: str, cat: dict[str, Any] | None = None) -> dict[str, Any] | None:
    cat = cat or load_catalog()
    for e in _all_entries(cat):
        if e.get("id") == ref:
            return e
    return None


def resolve_board(*, thickness: float | None = None, color: str | None = None,
                  cat: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Подобрать плиту по толщине (и цвету, если задан)."""
    cat = cat or load_catalog()
    best = None
    for e in cat.get("boards", []):
        if thickness is not None and abs(e.get("thickness", -1) - thickness) > 0.05:
            continue
        if color and e.get("color") and color.lower() not in str(e["color"]).lower():
            continue
        best = e
        if color and e.get("color"):
            break
    return best


def check_project_materials(project: dict[str, Any], cat: dict[str, Any] | None = None) -> list[str]:
    """Замечания: материалы/толщины проекта, которым нет соответствия в каталоге."""
    cat = cat or load_catalog()
    notes: list[str] = []
    m = project.get("materials", {})
    th = m.get("board_thickness")
    if th is not None and resolve_board(thickness=th, cat=cat) is None:
        notes.append(f"Плита толщиной {th} мм не найдена в каталоге — добавить в materials/catalog.json.")
    tb = m.get("back_wall_material")
    if tb and not any(tb.lower() in (e.get("basisName", "").lower()) for e in cat.get("backs", [])):
        notes.append(f"Задняя стенка «{tb}» не сопоставлена с каталогом backs.")
    color = str(m.get("color", "")).lower()
    if color and "согласован" not in color and "уточн" not in color:
        if not any(color in str(e.get("color", "")).lower() for e in cat.get("boards", []) if e.get("color")):
            notes.append(f"Цвет «{m.get('color')}» не встречается в каталоге плит — проверить наличие в базе БАЗИС.")
    return notes


# --- Производственная база материалов (materials/baza_materiala.json, ≈5000 позиций) ---
# Реальные артикулы/имена/цены/размеры из производства. catalog.json — курируемый
# тонкий слой поверх; база — источник реальных позиций для подбора (AKD-11).

BASE_PATH = Path(__file__).resolve().parent.parent / "materials" / "baza_materiala.json"
_BASE_CACHE: dict[str, Any] | None = None


def load_base(path: Path | None = None) -> dict[str, Any]:
    """Загрузить нормализованную базу материалов (кэшируется при пути по умолчанию)."""
    global _BASE_CACHE
    if path is None and _BASE_CACHE is not None:
        return _BASE_CACHE
    p = path or BASE_PATH
    if not p.exists():
        return {"items": [], "categories": {}, "groups": {}, "count": 0}
    data = json.loads(p.read_text(encoding="utf-8"))
    if path is None:
        _BASE_CACHE = data
    return data


def base_items(category: str | None = None, group_substr: str | None = None,
               base: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    base = base or load_base()
    items = base.get("items", [])
    if category:
        items = [x for x in items if x.get("category") == category]
    if group_substr:
        gs = group_substr.lower()
        items = [x for x in items if gs in str(x.get("group", "")).lower()]
    return items


def by_article(article: str, base: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Позиция базы по точному артикулу/id."""
    art = str(article).strip()
    for x in base_items(base=base):
        if str(x.get("article", "")).strip() == art or str(x.get("id", "")).strip() == art:
            return x
    return None


def list_sheet_decors(query: str = "", thickness: float | None = None, limit: int = 30,
                      base: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Выбор декора в Studio: листовой материал базы со свотчем цвета.

    Возвращает [{article, name, label, thickness, hex}] — label короткая метка
    декора, hex условный цвет показа (см. decor_colors). Дубли по label схлопнуты.
    """
    from .decor_colors import decor_base, decor_label
    tokens = [t for t in query.lower().split() if t]
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for x in base_items(category="Листовой материал", base=base):
        t = x.get("thickness")
        if thickness is not None and (t is None or abs(float(t) - thickness) > 0.05):
            continue
        name = str(x.get("name", ""))
        hay = f"{name} {x.get('article', '')} {x.get('group', '')}".lower()
        name_l = name.lower()

        def _tok_ok(tk: str) -> bool:
            # числовой токен = толщина (иначе «16» ловит артикулы вроде U2167)
            if tk.replace(".", "").replace(",", "").isdigit():
                try:
                    return t is not None and abs(float(t) - float(tk.replace(",", "."))) < 0.05
                except ValueError:
                    return tk in name_l
            return tk in hay

        if tokens and not all(_tok_ok(tk) for tk in tokens):
            continue
        label = decor_label(name)
        key = label.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append({"article": x.get("article"), "name": name, "label": label,
                    "thickness": t, "hex": decor_base(label) or "#c9a06a"})
        if len(out) >= limit:
            break
    return out


def search_base(query: str, category: str | None = None, limit: int = 25,
                base: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Поиск по имени/артикулу/обозначению/группе: все слова запроса должны
    встречаться (AND, без регистра, порядок не важен)."""
    tokens = [t for t in query.lower().split() if t]
    out: list[dict[str, Any]] = []
    for x in base_items(category=category, base=base):
        hay = f"{x.get('name','')} {x.get('article','')} {x.get('designation','')} {x.get('group','')}".lower()
        if all(t in hay for t in tokens):
            out.append(x)
            if len(out) >= limit:
                break
    return out


def find_board(thickness: float | None = None, query: str | None = None,
               base: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Реальные плиты из базы по толщине и/или подстроке имени; дешевле — выше."""
    res: list[dict[str, Any]] = []
    for x in base_items(category="Листовой материал", base=base):
        t = x.get("thickness")
        if thickness is not None and (t is None or abs(float(t) - thickness) > 0.05):
            continue
        if query and query.lower() not in str(x.get("name", "")).lower():
            continue
        res.append(x)
    # дешевле — выше; неизвестная/нулевая цена уходит в конец
    res.sort(key=lambda x: (x["cost"] if isinstance(x.get("cost"), (int, float)) and x["cost"] > 0 else 1e9))
    return res


# --- Резолв материалов проекта в реальные позиции базы (material_refs, AKD-11) ---
# Плита/задник/кромка детерминированы (толщина+цвет) → одна позиция. Фурнитура
# имеет сотни вариантов на тип → отдаём шорт-лист реальных кандидатов из нужной
# группы, не выбирая артикул принудительно (это делает технолог/каталог БАЗИС).

_GENERIC_COLOR = ("соглас", "уточн", "не задан", "любой", "по цвету")


def _is_generic_color(color: str | None) -> bool:
    c = (color or "").strip().lower()
    return (not c) or c in ("—", "-", "n/a", "нет") or any(w in c for w in _GENERIC_COLOR)


def _ref(item: dict[str, Any], match: str, confidence: str) -> dict[str, Any]:
    return {"resolved": True, "id": item.get("id"), "article": item.get("article"),
            "name": item.get("name"), "cost": item.get("cost"), "unit": item.get("unit"),
            "group": item.get("group"), "match": match, "confidence": confidence}


def _unresolved(query: str, reason: str, candidates: Any = ()) -> dict[str, Any]:
    return {"resolved": False, "query": query, "reason": reason,
            "candidates": [c.get("name") for c in list(candidates)[:3]]}


_BOARD_KINDS = ("ЛДСП", "МДФ", "ХДФ", "ДСП", "ДВП")


def _norm_code(s: Any) -> str:
    return "".join(str(s).split()).upper()


def resolve_board_ref(thickness: float | None, color: str | None = None,
                      color_code: str | None = None, base: dict[str, Any] | None = None) -> dict[str, Any]:
    if thickness is None:
        return _unresolved("плита", "не задана толщина плиты")
    boards = [b for b in find_board(thickness=thickness, base=base)
              if any(k in str(b.get("name", "")) for k in _BOARD_KINDS)]
    if not boards:
        return _unresolved(f"плита {thickness} мм", f"нет плиты {thickness} мм в базе")
    # 1) точный код декора (артикул/обозначение/имя), пробелонезависимо
    if color_code and str(color_code).strip():
        code = _norm_code(color_code)
        for b in boards:
            hay = _norm_code(f"{b.get('article','')} {b.get('designation','')} {b.get('name','')}")
            if code in hay:
                return _ref(b, f"код декора {color_code}", "high")
    # 2) цвет не задан → дефолтная (дешёвая) плита нужной толщины
    if _is_generic_color(color):
        return _ref(boards[0], "по толщине (цвет не задан → дефолт: дешевле)", "low")
    # 3) по названию цвета/декора
    cl = color.strip().lower()
    matched = [b for b in boards if cl in str(b.get("name", "")).lower()]
    if matched:
        return _ref(matched[0], "толщина + цвет", "high")
    return _unresolved(f"плита {thickness} мм «{color}»" + (f" (код {color_code})" if color_code else ""),
                       "не найдено по цвету/коду — подтвердить вручную", boards)


def resolve_back_ref(back_material: str | None, base: dict[str, Any] | None = None) -> dict[str, Any]:
    m = (back_material or "").lower()
    if "двп" in m or "хдф" in m:
        c = [x for x in base_items(category="Листовой материал", base=base)
             if any(k in str(x.get("name", "")) for k in ("ДВП", "ХДФ"))]
        c.sort(key=lambda x: (x.get("thickness") or 99, x.get("cost") or 1e9))
        return _ref(c[0], "задник ДВП/ХДФ", "medium") if c else _unresolved(back_material or "задник", "ДВП/ХДФ не найдены")
    if "лдсп" in m or "дсп" in m:
        return resolve_board_ref(16, None, base=base)
    return _unresolved(back_material or "задник", "тип задней стенки не распознан")


def resolve_edge_ref(thickness: float | None, color: str | None = None,
                     base: dict[str, Any] | None = None) -> dict[str, Any]:
    if not thickness:
        return _unresolved("кромка", "не задана толщина кромки")
    cand = [x for x in base_items(category="Кромочные материалы", base=base)
            if x.get("thickness") and abs(float(x["thickness"]) - float(thickness)) < 0.3]
    cand.sort(key=lambda x: (x.get("cost") or 1e9))
    if not cand:
        return _unresolved(f"кромка {thickness} мм", "кромка такой толщины не найдена")
    if not _is_generic_color(color):
        cl = color.strip().lower()
        col = [x for x in cand if cl in str(x.get("name", "")).lower()]
        if col:
            return _ref(col[0], "толщина + цвет", "high")
    return _ref(cand[0], "по толщине (в цвет плиты)", "low")


def shortlist(group_substr: str, tokens: list[Any], *, limit: int = 5, label: str = "",
              base: dict[str, Any] | None = None) -> dict[str, Any]:
    """Шорт-лист реальных позиций фурнитуры из группы, ранжированных по совпадению
    токенов (тип/размер/длина), затем по цене. Артикул не выбирается принудительно."""
    items = base_items(group_substr=group_substr, base=base)
    toks = [str(t).lower() for t in tokens if t not in (None, "", 0)]

    def score(x: dict[str, Any]) -> int:
        name = str(x.get("name", "")).lower()
        return sum(1 for t in toks if t in name)

    ranked = sorted(items, key=lambda x: (-score(x), x.get("cost") if isinstance(x.get("cost"), (int, float)) and x["cost"] > 0 else 1e9))
    hit = [x for x in ranked if score(x) > 0]
    top = (hit or ranked)[:limit]
    return {
        "resolved": bool(top),
        "group": group_substr,
        "matched_tokens": toks,
        "note": "шорт-лист реальных позиций базы; конкретный артикул выбирает технолог/каталог БАЗИС",
        "candidates": [{"article": x.get("article"), "name": x.get("name"), "cost": x.get("cost")} for x in top],
    } if top else _unresolved(label or group_substr, f"в группе «{group_substr}» нет позиций")


def _handle_shortlist(h: dict[str, Any], base: dict[str, Any] | None) -> dict[str, Any]:
    t = str(h.get("type", "")).lower()
    kw = "скоб" if "скоб" in t else "профил" if "профил" in t else "кнопк" if "кнопк" in t \
        else "рейлинг" if "рейлинг" in t else ""
    return shortlist("Ручк", [kw, h.get("size")], label="ручка", base=base)


def _guides_shortlist(g: dict[str, Any], base: dict[str, Any] | None) -> dict[str, Any]:
    t = str(g.get("type", "")).lower()
    length = g.get("length_mm")
    if "шарик" in t:
        return shortlist("Шариковые направляющие", [length], label="направляющая шариковая", base=base)
    kw = "метабокс" if "метабокс" in t else "тандем" if "тандем" in t else "роликов" if "ролик" in t else ""
    return shortlist("выдвижения", [kw, length, "довод" if g.get("soft_close") else ""],
                     label="направляющие", base=base)


def _legs_shortlist(legs: dict[str, Any], base: dict[str, Any] | None) -> dict[str, Any]:
    t = str(legs.get("type", "")).lower()
    if "колёс" in t or "колес" in t or "ролик" in t:
        return shortlist("Опоры колесные", [], label="опора колёсная", base=base)
    if legs.get("adjustable") or "регулир" in t:
        return shortlist("Опоры регулируемые", [], label="опора регулируемая", base=base)
    return shortlist("Опоры", [], label="опора", base=base)


def resolve_project_materials(project: dict[str, Any], base: dict[str, Any] | None = None) -> dict[str, Any]:
    """Сопоставить материалы/фурнитуру проекта с реальными позициями базы.
    Плита/задник/кромка — одна позиция (по толщине+цвету), фурнитура — шорт-лист."""
    base = base or load_base()
    m = project.get("materials", {}) or {}
    hw = project.get("hardware", {}) or {}
    refs: dict[str, Any] = {
        "board": resolve_board_ref(m.get("board_thickness"), m.get("color"), m.get("color_code"), base=base),
        "back": resolve_back_ref(m.get("back_wall_material"), base=base),
    }
    if m.get("edge_band_thickness"):
        refs["edge"] = resolve_edge_ref(m.get("edge_band_thickness"), m.get("color"), base=base)
    h = hw.get("handles") or {}
    if (h.get("count") or 0) > 0 and str(h.get("type", "")).lower() not in ("нет", "—", ""):
        refs["handles"] = _handle_shortlist(h, base)
    if hw.get("drawer_guides"):
        refs["drawer_guides"] = _guides_shortlist(hw["drawer_guides"], base)
    if project.get("doors") or hw.get("hinges"):
        refs["hinges"] = shortlist("Петли", ["петля"], label="петля", base=base)
    legs = hw.get("legs") or {}
    if (legs.get("count") or 0) > 0 and str(legs.get("type", "")).lower() not in ("нет", "—", ""):
        refs["legs"] = _legs_shortlist(legs, base)
    if hw.get("locks"):
        refs["locks"] = shortlist("Замки", ["замок"], label="замок", base=base)
    return refs
