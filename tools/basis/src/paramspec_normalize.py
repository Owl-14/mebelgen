"""Нормализация кандидата ParamSpec от нейросети до производственного гейта.

Модели возвращают почти правильный ParamSpec: синоним типа секции («doors»
вместо «door»), габарит строкой («1000 мм»), лишнее поле («facade_thickness» в
materials). Гейт отклоняет такой кандидат целиком, и по импорту ТЗ клиент
получает ошибку вместо изделия — при том, что сам чертёж распознан верно.

Здесь кандидат приводится к контракту детерминированно, и каждая правка
записывается в примечания: подмены видно в ответе Studio и в AI-журнале, молча
не теряется ничего. Нормализация не придумывает данные — она переименовывает
известные синонимы, приводит числа и удаляет поля, которых нет в контракте.
Геометрию, материалы и полноту по-прежнему проверяет гейт.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Any

from .paramspec import _ARCHETYPE_TAGS, _SECTION_TAGS, parse_paramspec

MAX_PASSES = 12
_REQUIRED_NUMBERS = {"width", "depth", "height", "board_thickness"}

# Синонимы типа изделия: модели пишут по-русски или обиходным словом.
ARCHETYPE_ALIASES = {
    "шкаф": "cabinet", "шкаф_для_документов": "cabinet", "cupboard": "cabinet",
    "гардероб": "wardrobe", "гардеробный_шкаф": "wardrobe", "wardrobe_cabinet": "wardrobe",
    "тумба": "drawer_unit", "тумбочка": "drawer_unit", "pedestal": "drawer_unit",
    "drawer_cabinet": "drawer_unit", "nightstand": "drawer_unit",
    "стол": "desk", "table": "desk", "письменный_стол": "desk", "рабочий_стол": "desk",
    "стеллаж": "shelving", "rack": "shelving", "bookcase": "shelving",
    "круглый_стол": "round_table", "корпус": "corpus",
}

# Синонимы типа секции. Контракт знает только shelves/drawers/door/open.
SECTION_KIND_ALIASES = {
    "doors": "door", "дверь": "door", "двери": "door", "door_section": "door",
    "распашная": "door", "створка": "door",
    "drawer": "drawers", "ящик": "drawers", "ящики": "drawers", "выдвижная": "drawers",
    "выдвижные_ящики": "drawers",
    "shelf": "shelves", "полка": "shelves", "полки": "shelves", "shelving": "shelves",
    "niche": "open", "ниша": "open", "открытая": "open", "opened": "open",
    "open_section": "open",
}

# Поля, у которых значение обязано быть числом: модели любят «1000 мм» и «16.0».
_NUMERIC_FIELDS = {
    "width", "depth", "height", "depth_carcass", "tolerance",
    "board_thickness", "edge_band_thickness", "back_thickness", "top_thickness",
    "facade_thickness", "drawers", "shelves", "door", "count",
    "apron_height", "screen_height", "screen_thickness",
    "shelf_levels", "drawer_heights", "width_share", "box_height", "box_depth",
}
_NUMBER_RE = re.compile(r"-?\d+(?:[.,]\d+)?")


@dataclass
class NormalizationResult:
    spec: dict[str, Any]
    notes: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.notes)


def _slug(value: Any) -> str:
    return str(value or "").strip().lower().replace(" ", "_").replace("-", "_")


def _as_number(value: Any) -> float | int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return value
    match = _NUMBER_RE.search(str(value))
    if match is None:
        return None
    number = float(match.group(0).replace(",", "."))
    return int(number) if number == int(number) else number


def _fix_numbers(node: Any, notes: list[str], path: str = "") -> Any:
    """Габарит строкой — самая частая мелочь; приводим её к числу."""
    if isinstance(node, dict):
        return {key: _fix_numbers(value, notes, f"{path}{key}.")
                for key, value in node.items()}
    if isinstance(node, list):
        return [_fix_numbers(item, notes, f"{path}{index}.")
                for index, item in enumerate(node)]
    parts = path[:-1].split(".")
    # для элементов массива (shelf_levels.0) числовым считаем имя самого массива
    key = parts[-2] if len(parts) > 1 and parts[-1].isdigit() else parts[-1]
    if key in _NUMERIC_FIELDS and isinstance(node, str):
        number = _as_number(node)
        if number is not None:
            notes.append(f"{path[:-1]}: «{node}» → {number}")
            return number
    return node


def _drop_nulls(node: Any, dropped: list[str], path: str = "") -> Any:
    """Убрать «поле»: null и пустые элементы списков.

    Неизвестное нейросеть пишет как null («legs»: null, «facade_reveal»: null),
    а движок ждёт контрактный дефолт и падает на None. Пустое значение — это
    отсутствие значения, поэтому поле удаляем и дефолт применяется сам.
    """
    if isinstance(node, dict):
        result = {}
        for key, value in node.items():
            if value is None:
                dropped.append(f"{path}{key}")
                continue
            result[key] = _drop_nulls(value, dropped, f"{path}{key}.")
        return result
    if isinstance(node, list):
        return [_drop_nulls(item, dropped, f"{path}{index}.")
                for index, item in enumerate(node) if item is not None]
    return node


def _fix_archetype(spec: dict[str, Any], notes: list[str]) -> None:
    archetype = spec.get("archetype")
    if not isinstance(archetype, str) or archetype in _ARCHETYPE_TAGS:
        return
    mapped = ARCHETYPE_ALIASES.get(_slug(archetype))
    if mapped:
        notes.append(f"archetype: «{archetype}» → {mapped}")
        spec["archetype"] = mapped
        if spec.get("furniture_type") == archetype:
            spec["furniture_type"] = mapped


def _fix_sections(spec: dict[str, Any], notes: list[str]) -> None:
    sections = spec.get("sections")
    if not isinstance(sections, list):
        return
    for index, section in enumerate(sections):
        if not isinstance(section, dict):
            continue
        kind = section.get("kind")
        if not isinstance(kind, str) or kind in _SECTION_TAGS:
            continue
        mapped = SECTION_KIND_ALIASES.get(_slug(kind))
        if mapped:
            notes.append(f"sections.{index}.kind: «{kind}» → {mapped}")
            section["kind"] = mapped


def _fix_shelves_in_drawer_column(spec: dict[str, Any], notes: list[str]) -> None:
    """Колонка ящиков — это стек ящиков; полок движок там не строит.

    Модель регулярно пишет в такую секцию shelves, приняв за количество фразу
    ТЗ вроде «внутренние полки 25 мм» (там речь о толщине). Проверка полноты
    потом честно ругается «заявлено 2, построено 0» и ТЗ отклоняется целиком.
    """
    for index, section in enumerate(spec.get("sections") or []):
        if not isinstance(section, dict) or section.get("kind") != "drawers":
            continue
        if not section.get("drawers") or section.get("shelf_levels"):
            continue
        declared = section.get("shelves")
        if isinstance(declared, (int, float)) and declared > 0:
            section.pop("shelves")
            notes.append(f"sections.{index}: полки ({int(declared)}) в колонке ящиков "
                         "не строятся — заявление убрано")


SINGLE_COLUMN_ARCHETYPES = {"drawer_unit", "door_unit"}
# Уже не бывает колонки корпусной мебели (ящик с направляющими не встанет)
MIN_COLUMN_WIDTH = 300


def _fix_single_column_sections(spec: dict[str, Any], notes: list[str]) -> None:
    """Тумба и однодверная секция строятся одной колонкой.

    Несколько секций у drawer_unit/door_unit — это колонки слева направо
    («3 ящика слева и 3 справа»): собираем как cabinet, а не отрезаем лишнее —
    иначе комод 2×3 молча превращается в одну стопку (так было до 01.10.2026).
    """
    sections = spec.get("sections")
    if (spec.get("archetype") not in SINGLE_COLUMN_ARCHETYPES
            or not isinstance(sections, list) or len(sections) < 2):
        return
    width = (spec.get("dimensions") or {}).get("width")
    if isinstance(width, (int, float)) and width / len(sections) >= MIN_COLUMN_WIDTH:
        notes.append(f"archetype: {spec['archetype']} с {len(sections)} секциями → cabinet "
                     "(секции — колонки слева направо)")
        spec["archetype"] = "cabinet"
        return
    # узкая тумба (400 на две «секции» = 200 на колонку) — модель описала ярусы
    # одной колонки как секции; колонку такой ширины не собрать, берём первую
    extra = sections[1:]
    spec["sections"] = sections[:1]
    kinds = ", ".join(str(item.get("kind")) for item in extra if isinstance(item, dict))
    notes.append(f"секции сверх первой ({kinds}) убраны: {spec['archetype']} "
                 "собирается одной колонкой")
    warnings = spec.get("warnings")
    if not isinstance(warnings, list):
        warnings = []
        spec["warnings"] = warnings
    warnings.append(f"В ТЗ были дополнительные секции ({kinds}) — изделие собрано "
                    "одной колонкой, достроить в Studio.")


def _infer_archetype(spec: dict[str, Any], notes: list[str]) -> None:
    """Модель иногда не пишет archetype вовсе — схема отвергает весь кандидат.

    Выводим из furniture_type/project_name (синонимы) или из секций: одна
    колонка ящиков — drawer_unit, одна дверная — door_unit, несколько — cabinet.
    """
    if isinstance(spec.get("archetype"), str) and spec["archetype"] in _ARCHETYPE_TAGS:
        return
    for field_name in ("furniture_type", "project_name"):
        words = re.findall(r"[a-zа-яё_]+", str(spec.get(field_name) or "").lower())
        for word in words:
            mapped = ARCHETYPE_ALIASES.get(word)
            if mapped and mapped not in ("corpus",):
                spec["archetype"] = mapped
                notes.append(f"archetype не указан → {mapped} (по «{word}»)")
                return
    sections = [item for item in spec.get("sections") or [] if isinstance(item, dict)]
    if len(sections) >= 2:
        mapped = "cabinet"
    elif sections and sections[0].get("kind") == "drawers":
        mapped = "drawer_unit"
    elif sections and sections[0].get("kind") == "door":
        mapped = "door_unit"
    elif sections:
        mapped = "shelving"
    else:
        mapped = "corpus"
    spec["archetype"] = mapped
    notes.append(f"archetype не указан → {mapped} (по секциям)")


def _delete_path(spec: dict[str, Any], path: list[str]) -> bool:
    node: Any = spec
    for part in path[:-1]:
        if isinstance(node, list):
            if not part.isdigit() or int(part) >= len(node):
                return False
            node = node[int(part)]
        elif isinstance(node, dict):
            if part not in node:
                return False
            node = node[part]
        else:
            return False
    last = path[-1]
    if isinstance(node, dict) and last in node:
        del node[last]
        return True
    return False


# Необязательные числа, которые модель ставит нулём («длина не указана» → 0):
# контракт требует > 0, удаляем поле — действует дефолт движка.
_DROPPABLE_ERROR_TYPES = {"extra_forbidden", "greater_than", "greater_than_equal"}


def _extra_paths(spec: dict[str, Any]) -> list[list[str]]:
    """Пути полей, которых нет в контракте (extra_forbidden) или с нулём вместо > 0."""
    try:
        parse_paramspec(spec)
    except Exception as error:  # noqa: BLE001 - ValidationError и всё, что не распарсилось
        details = getattr(error, "errors", None)
        if details is None:
            return []
        paths: list[list[str]] = []
        for detail in details(include_url=False):
            if detail.get("type") not in _DROPPABLE_ERROR_TYPES:
                continue
            if detail.get("type") != "extra_forbidden" and (
                    detail["loc"][-1:] and detail["loc"][-1] in _REQUIRED_NUMBERS):
                continue                         # габарит/толщина обязательны — пусть решает гейт
            loc = [str(part) for part in detail["loc"] if part != "root"]
            if loc and loc[0] in _ARCHETYPE_TAGS:
                loc.pop(0)
            loc = [part for index, part in enumerate(loc)
                   if not (part in _SECTION_TAGS and index > 0 and loc[index - 1].isdigit())]
            if loc:
                paths.append(loc)
        return paths
    return []


def normalize_candidate(candidate: Any) -> NormalizationResult:
    """Привести кандидат нейросети к контракту ParamSpec, не выдумывая значений."""
    if not isinstance(candidate, dict):
        return NormalizationResult(spec=candidate, notes=[])

    notes: list[str] = []
    dropped: list[str] = []
    spec = _drop_nulls(copy.deepcopy(candidate), dropped)
    if dropped:
        notes.append("пустые поля убраны (действуют дефолты): " + ", ".join(dropped[:8])
                     + (f" и ещё {len(dropped) - 8}" if len(dropped) > 8 else ""))
    _fix_archetype(spec, notes)
    _infer_archetype(spec, notes)
    _fix_sections(spec, notes)
    _fix_shelves_in_drawer_column(spec, notes)
    _fix_single_column_sections(spec, notes)
    spec = _fix_numbers(spec, notes)

    for _ in range(MAX_PASSES):
        paths = _extra_paths(spec)
        if not paths:
            break
        removed = False
        for path in paths:
            if _delete_path(spec, path):
                notes.append("удалено поле не из контракта или с нулём: " + ".".join(path))
                removed = True
        if not removed:
            break

    return NormalizationResult(spec=spec, notes=notes)


__all__ = ["ARCHETYPE_ALIASES", "SECTION_KIND_ALIASES", "NormalizationResult",
           "normalize_candidate"]
