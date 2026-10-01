"""Уточняющие вопросы проектировщику при импорте ТЗ.

Нейросеть читает ТЗ с ошибками: мелкая картинка — путает размеры, шесть ящиков в
двух модулях — собирает одну стопку, развёртку кухни — один глухой короб. Гейт
такое пропускает: изделие формально корректно. Здесь детерминированные правила
сверяют кандидата ParamSpec с распознанными фактами и, если что-то неясно,
возвращают до MAX_QUESTIONS вопросов с готовыми вариантами. Ответы применяются
к черновику без нейросети; свободный текст уходит в повторную сборку.

Вопрос: {id, text, kind: choice|dims|number|text, options: [{value, label,
patch?}], suggested, field?, reasons}. patch — частичный ParamSpec, который
накладывается на черновик (sections заменяются целиком).
"""

from __future__ import annotations

import copy
import re
from typing import Any

MAX_QUESTIONS = 5

# Картинка мельче — цифры на чертеже читаются ненадёжно.
SMALL_IMAGE_BYTES = 60_000
SMALL_IMAGE_SIDE = 900

# Признаки листа с несколькими изделиями (развёртка кухни / стены).
_MULTI_PRODUCT = re.compile(
    r"кухн|холодильн|вытяжк|ПММ|посудомо|духов|варочн|индукц|развёртк|развертк|"
    r"гарнитур|стен[аы]\b|мойк",
    re.IGNORECASE,
)

# Типичные глубины корпусов, мм — для подсказки и проверки правдоподобия.
_TYPICAL_DEPTH = {"wardrobe": 600, "cabinet": 450, "drawer_unit": 450,
                  "door_unit": 450, "corpus": 450, "shelving": 350,
                  "desk": 700, "table": 700}
_MIN_DEPTH = {"shelving": 200, "desk": 400, "table": 400}
_DEFAULT_MIN_DEPTH = 250
_MAX_WIDTH = 3000
_MAX_HEIGHT = 2800


def clean_facts(text: str) -> str:
    """Факты vision-узла → простой текст строками.

    Модели (GigaChat) иногда присылают факты как ```json {"reply": "...\\n..."}```:
    без разбора строки «Колонки…», «Габариты…» не видны ни правилам, ни сборщику.
    """
    import json

    raw = str(text or "").strip()
    body = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.I).strip()
    if body.startswith("{"):
        try:
            data = json.loads(body)
        except ValueError:                       # оборванный JSON — берём текст reply как есть
            m = re.search(r'"reply"\s*:\s*"(.*?)"?\s*}?\s*$', body, re.S)
            data = {"reply": m.group(1) if m else ""}
        if isinstance(data, dict) and isinstance(data.get("reply"), str) and data["reply"].strip():
            body = data["reply"]
    if "\\n" in body and "\n" not in body:
        body = body.replace("\\n", "\n")
    return body.strip()


def _fact_line(facts: str, label: str) -> str:
    m = re.search(rf"^\s*{label}[^:\n]*:\s*(.+)$", facts or "", re.IGNORECASE | re.MULTILINE)
    return m.group(1).strip() if m else ""


def _not_given(value: str) -> bool:
    return not value or bool(re.match(r"не\s+(указан|видн|читаетс)|нет\b|—|-$", value, re.I))


def _count_in(text: str) -> int | None:
    m = re.search(r"\d+", text or "")
    return int(m.group()) if m else None


def _fact_columns(facts: str) -> list[int]:
    """Число ящиков по колонкам из строки «Колонки слева направо».

    «Колонка 1 (780): 3 ящика; Колонка 2 (780): 3 ящика» → [3, 3].
    Колонки без ящиков дают 0, нераспознанная строка — [].
    """
    line = _fact_line(facts, "Колонки")
    if _not_given(line):
        return []
    parts = [p for p in re.split(r"колонка\s*\d+", line, flags=re.I) if p.strip(" ;,.")]
    out = []
    for part in parts:
        m = re.search(r"(\d+)\s*ящик", part, re.I)
        out.append(int(m.group(1)) if m else 0)
    return out


def _dims(spec: dict[str, Any]) -> tuple[float, float, float]:
    d = spec.get("dimensions") or {}
    return (float(d.get("width") or 0), float(d.get("depth") or 0), float(d.get("height") or 0))


def _fmt(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else str(v)


def _drawer_sections(spec: dict[str, Any]) -> list[dict[str, Any]]:
    return [s for s in spec.get("sections") or [] if isinstance(s, dict) and s.get("kind") == "drawers"]


def _columns_patch(spec: dict[str, Any], counts: list[int]) -> dict[str, Any]:
    if len(counts) == 1:
        return {"archetype": "drawer_unit", "sections": [{"kind": "drawers", "drawers": counts[0]}]}
    return {"archetype": "cabinet",
            "sections": [{"kind": "drawers", "drawers": n} for n in counts]}


def _small_image(image: dict[str, Any] | None) -> bool:
    img = image or {}
    side = max(int(img.get("width") or 0), int(img.get("height") or 0))
    return bool((img.get("bytes") and int(img["bytes"]) < SMALL_IMAGE_BYTES)
                or (side and side < SMALL_IMAGE_SIDE))


def _layout_question(spec: dict[str, Any], facts: str,
                     image: dict[str, Any] | None = None) -> dict[str, Any] | None:
    drawers = _drawer_sections(spec)
    total = sum(int(s.get("drawers") or 0) for s in drawers)
    if not total:
        return None
    width = _dims(spec)[0]
    current = [int(s.get("drawers") or 0) for s in drawers]
    from_facts = [n for n in _fact_columns(facts) if n]
    modules = _count_in(_fact_line(facts, "Модул"))
    reasons = []
    if from_facts and from_facts != current and sum(from_facts) == total:
        reasons.append(f"в ТЗ колонки {'+'.join(map(str, from_facts))}, а собрано {'+'.join(map(str, current))}")
    if modules and modules > 1 and len(current) < modules:
        reasons.append(f"в ТЗ модулей: {modules}, а колонок с ящиками: {len(current)}")
    if len(current) == 1 and total >= 5 and width >= 1000:
        reasons.append(f"{total} ящиков одной стопкой при ширине {_fmt(width)} мм — необычно")
    keep_current = not reasons
    if not reasons and _small_image(image):
        # мелкая картинка: модель могла не разглядеть второй модуль (комод 3+3 → «3 ящика»)
        reasons.append("картинка ТЗ мелкая — раскладку ящиков могли прочитать неверно")
    if not reasons:
        return None
    variants: list[list[int]] = [current]
    if len(current) == 1 and width >= 1000:
        variants.append([total, total])            # два одинаковых модуля рядом
    if from_facts and sum(from_facts) == total:
        variants.append(from_facts)
    if total % 2 == 0:
        variants.append([total // 2, total // 2])
    if total % 3 == 0 and width >= 1800:
        variants.append([total // 3] * 3)
    variants.append([total])
    seen: list[list[int]] = []
    for v in variants:
        if v not in seen:
            seen.append(v)
    options = []
    for v in seen:
        label = (f"Одна стопка из {v[0]} ящиков" if len(v) == 1 else
                 f"{len(v)} колонки: " + " + ".join(f"{n} ящ." for n in v))
        options.append({"value": "x".join(map(str, v)), "label": label,
                        "patch": _columns_patch(spec, v)})
    if keep_current:
        suggested = "x".join(map(str, current))
    elif from_facts and sum(from_facts) == total:
        suggested = "x".join(map(str, from_facts))
    else:
        suggested = "x".join(map(str, next((v for v in seen[1:] if sum(v) == total), seen[0])))
    return {"id": "layout", "kind": "choice",
            "text": "Как расположены ящики? Колонки — слева направо, в колонке ящики сверху вниз.",
            "options": options, "suggested": suggested, "reasons": reasons}


def _dims_question(spec: dict[str, Any], facts: str,
                   image: dict[str, Any] | None) -> dict[str, Any] | None:
    w, d, h = _dims(spec)
    arch = str(spec.get("archetype") or "")
    reasons = []
    if _not_given(_fact_line(facts, "Внешние габариты")) and facts:
        reasons.append("габариты на чертеже не подписаны")
    img = image or {}
    side = max(int(img.get("width") or 0), int(img.get("height") or 0))
    if (img.get("bytes") and int(img["bytes"]) < SMALL_IMAGE_BYTES) or (side and side < SMALL_IMAGE_SIDE):
        reasons.append("картинка ТЗ мелкая — цифры могли прочитаться неверно")
    unclear = _fact_line(facts, "Неясно")
    if unclear and not _not_given(unclear) and re.search(r"размер|габарит|\d", unclear, re.I):
        reasons.append(f"при распознавании: {unclear}")
    if not reasons:
        return None
    return {"id": "dims", "kind": "dims",
            "text": f"Проверь габариты Ш×Г×В, мм (распознано {_fmt(w)}×{_fmt(d)}×{_fmt(h)}).",
            "options": [{"value": f"{_fmt(w)}x{_fmt(d)}x{_fmt(h)}", "label": "Верно"}],
            "suggested": f"{_fmt(w)}x{_fmt(d)}x{_fmt(h)}", "reasons": reasons,
            "hint": arch}


def _plausibility_questions(spec: dict[str, Any]) -> list[dict[str, Any]]:
    w, d, h = _dims(spec)
    arch = str(spec.get("archetype") or "")
    out = []
    min_depth = _MIN_DEPTH.get(arch, _DEFAULT_MIN_DEPTH)
    if d and d < min_depth:
        typical = _TYPICAL_DEPTH.get(arch, 450)
        out.append({"id": "depth", "kind": "number", "field": "dimensions.depth",
                    "text": f"Глубина {_fmt(d)} мм нетипична для такого изделия. Какая глубина корпуса?",
                    "options": [{"value": typical, "label": f"{typical} мм (типично)"},
                                {"value": _number(d), "label": f"{_fmt(d)} мм — так и есть"}],
                    "suggested": typical, "reasons": [f"глубина меньше {min_depth} мм"]})
    if w > _MAX_WIDTH and arch != "composite":
        out.append({"id": "width", "kind": "number", "field": "dimensions.width",
                    "text": f"Ширина {_fmt(w)} мм — это одно изделие? Корпус шире {_MAX_WIDTH} мм "
                            f"обычно делят на модули. Укажи ширину изделия.",
                    "options": [{"value": _number(w), "label": f"{_fmt(w)} мм — одно изделие"}],
                    "suggested": _number(w), "reasons": [f"ширина больше {_MAX_WIDTH} мм"]})
    if h > _MAX_HEIGHT:
        out.append({"id": "height", "kind": "number", "field": "dimensions.height",
                    "text": f"Высота {_fmt(h)} мм больше типичной для корпуса. Какая высота?",
                    "options": [{"value": _number(h), "label": f"{_fmt(h)} мм — так и есть"}],
                    "suggested": _number(h), "reasons": [f"высота больше {_MAX_HEIGHT} мм"]})
    return out


def _multi_product_question(spec: dict[str, Any], facts: str) -> dict[str, Any] | None:
    count_line = _fact_line(facts, "Изделий на листе")
    many = (_count_in(count_line) or 1) > 1 or bool(_MULTI_PRODUCT.search(facts or ""))
    if not many:
        return None
    w, d, h = _dims(spec)
    return {"id": "product", "kind": "text",
            "text": "Похоже, на листе несколько изделий (кухня / развёртка стены). "
                    "Какое изделие собрать? Опиши его: тип, размеры, что внутри.",
            "options": [{"value": "", "label": f"Как распознано: один корпус {_fmt(w)}×{_fmt(d)}×{_fmt(h)}"}],
            "suggested": "",
            "reasons": [count_line or "в фактах есть приборы/кухонные элементы"]}


def questions_for(spec: dict[str, Any], facts: str = "",
                  image: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Вопросы по кандидату из ТЗ; пустой список — собирать без уточнений."""
    facts = clean_facts(facts)
    found: list[dict[str, Any]] = []
    multi = _multi_product_question(spec, facts)
    if multi:
        found.append(multi)
    layout = _layout_question(spec, facts, image)
    if layout:
        found.append(layout)
    plaus = _plausibility_questions(spec)
    dims = _dims_question(spec, facts, image)
    if dims:
        # общий вопрос о габаритах поглощает точечные о глубине/ширине/высоте
        dims["reasons"] += [r for q in plaus for r in q["reasons"]]
        found.append(dims)
    else:
        found += plaus
    return found[:MAX_QUESTIONS]


def _set_path(spec: dict[str, Any], path: str, value: Any) -> None:
    node = spec
    keys = path.split(".")
    for key in keys[:-1]:
        node = node.setdefault(key, {})
    node[keys[-1]] = value


def _number(value: Any) -> float | int | None:
    try:
        num = float(str(value).replace(",", ".").strip())
    except (TypeError, ValueError):
        return None
    if num <= 0:
        return None
    return int(num) if num.is_integer() else num


def _parse_dims(value: Any) -> tuple[Any, Any, Any] | None:
    nums = [_number(n) for n in re.findall(r"\d+(?:[.,]\d+)?", str(value or ""))]
    nums = [n for n in nums if n]
    return tuple(nums[:3]) if len(nums) >= 3 else None  # type: ignore[return-value]


def _parse_columns(value: Any) -> list[int] | None:
    """«3+3», «3х3», «2 по 3», «3, 3» → ящиков по колонкам; иначе None."""
    text = str(value or "").lower()
    m = re.fullmatch(r"\s*(\d+)\s*(?:колонк\w*|модул\w*)?\s*по\s*(\d+)\s*(?:ящ\w*)?\s*", text)
    if m:
        return [int(m.group(2))] * int(m.group(1))
    if re.fullmatch(r"[\d\s+x×х,;]+", text):
        nums = [int(n) for n in re.findall(r"\d+", text) if int(n) > 0]
        return nums or None
    return None


def apply_answers(draft: dict[str, Any], questions: list[dict[str, Any]],
                  answers: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Накладывает ответы на черновик. Возвращает (spec, свободные ответы текстом).

    Свободные ответы (текст, вариант без patch) не трактуются здесь — они
    уходят нейросети блоком «Уточнения проектировщика».
    """
    spec = copy.deepcopy(draft)
    free: list[str] = []
    for q in questions:
        qid = q.get("id")
        if qid not in answers:
            continue
        value = answers[qid]
        kind = q.get("kind")
        option = next((o for o in q.get("options") or [] if str(o.get("value")) == str(value)), None)
        if option and isinstance(option.get("patch"), dict):
            for key, val in option["patch"].items():
                spec[key] = copy.deepcopy(val)
        elif qid == "layout" and _parse_columns(value):
            for key, val in _columns_patch(spec, _parse_columns(value) or []).items():
                spec[key] = val
        elif kind == "dims":
            parsed = _parse_dims(value)
            if parsed:
                spec.setdefault("dimensions", {}).update(
                    {"width": parsed[0], "depth": parsed[1], "height": parsed[2]})
            elif str(value).strip():
                free.append(f"{q.get('text')} — {value}")
        elif kind == "number" and q.get("field"):
            num = _number(value)
            if num is not None:
                _set_path(spec, str(q["field"]), num)
            elif str(value).strip():
                free.append(f"{q.get('text')} — {value}")
        elif str(value).strip():
            free.append(f"{q.get('text')} — {value}")
    # ответ переразложил колонки: drawer_unit с несколькими секциями не бывает
    secs = spec.get("sections") or []
    if spec.get("archetype") in ("drawer_unit", "door_unit") and len(secs) > 1:
        spec["archetype"] = "cabinet"
    return spec, free
