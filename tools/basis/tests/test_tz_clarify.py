"""Уточняющие вопросы при импорте ТЗ (MEB-164).

Реальные случаи со studio.akeda.ru 29.09 (факты — ответ vision-узла glm):
- комод в спальню: два модуля по 3 ящика, картинка 20 КБ — модель прочитала
  1500×400×1100 и собрала одну стопку из 6 ящиков;
- развёртка кухонной стены 4510 — модель собрала один глухой корпус 4510×150×2050.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ["SPEC_CHAT_PROVIDER"] = "mock"   # тесты всегда офлайн

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.spec_chat import _accept_created, create_from_clarification  # noqa: E402
from src.tz_clarify import apply_answers, questions_for                # noqa: E402

KOMOD_FACTS = """Тип изделия: Камода в спальню
Внешние габариты Ш×Г×В: 1500×400×1100 мм
Секции слева направо: 1. Выдвижной ящик 2. Выдвижной ящик 3. Выдвижной ящик 4. Выдвижной ящик 5. Выдвижной ящик 6. Выдвижной ящик
Материалы: Не указаны"""

KOMOD_DRAFT = {
    "schemaVersion": "paramspec-v1", "project_name": "Комода в спальню",
    "archetype": "drawer_unit",
    "dimensions": {"width": 1500, "depth": 400, "height": 1100},
    "materials": {"board_thickness": 16},
    "sections": [{"kind": "drawers", "drawers": 6}],
}
KOMOD_IMAGE = {"bytes": 20625, "width": 400, "height": 500}

KITCHEN_FACTS = """Тип изделия: Корпусная мебель
Внешние габариты Ш×Г×В: 4510×150×2050 мм
Секции: Слева направо: Холодильник, ПЛМ, ПЛМ, Розетка для духовки, Розетка для индукционной плиты и ПЛМ
Материалы: Холодная вода, Штукатурная розетка"""

KITCHEN_DRAFT = {
    "schemaVersion": "paramspec-v1", "project_name": "Корпусная мебель из ТЗ",
    "archetype": "corpus",
    "dimensions": {"width": 4510, "depth": 150, "height": 2050},
    "materials": {"board_thickness": 18},
}

NEW_FORMAT_FACTS = """Тип изделия: Комод
Изделий на листе: 1
Модулей (корпусов): 2 по 780
Внешние габариты Ш×Г×В: 1600×300×800
Колонки слева направо: Колонка 1 (780): 3 ящика; Колонка 2 (780): 3 ящика
Неясно: нет"""


def _ids(questions):
    return [q["id"] for q in questions]


def test_komod_asks_layout_and_dimensions():
    qs = questions_for(KOMOD_DRAFT, KOMOD_FACTS, KOMOD_IMAGE)
    assert _ids(qs) == ["layout", "dims"]
    layout = qs[0]
    assert layout["suggested"] == "3x3"
    assert {o["value"] for o in layout["options"]} >= {"6", "3x3"}


def test_kitchen_wall_asks_which_product_and_flags_sizes():
    qs = questions_for(KITCHEN_DRAFT, KITCHEN_FACTS, {"bytes": 160300, "width": 1200, "height": 850})
    assert _ids(qs)[0] == "product"
    assert {"depth", "width"} <= set(_ids(qs))


def test_columns_from_structured_facts_mismatch_is_asked():
    draft = {**KOMOD_DRAFT, "dimensions": {"width": 1600, "depth": 300, "height": 800}}
    qs = questions_for(draft, NEW_FORMAT_FACTS, {"bytes": 300_000, "width": 2000, "height": 1400})
    assert _ids(qs) == ["layout"]
    assert qs[0]["suggested"] == "3x3"


def test_clear_tz_has_no_questions():
    spec = {**KOMOD_DRAFT, "archetype": "cabinet",
            "dimensions": {"width": 1600, "depth": 300, "height": 800},
            "sections": [{"kind": "drawers", "drawers": 3}, {"kind": "drawers", "drawers": 3}]}
    assert questions_for(spec, NEW_FORMAT_FACTS, {"bytes": 300_000, "width": 2000, "height": 1400}) == []


def test_answers_patch_the_draft_without_llm():
    qs = questions_for(KOMOD_DRAFT, KOMOD_FACTS, KOMOD_IMAGE)
    spec, free = apply_answers(KOMOD_DRAFT, qs, {"layout": "3x3", "dims": "1600×300×800"})
    assert free == []
    assert spec["archetype"] == "cabinet"
    assert spec["sections"] == [{"kind": "drawers", "drawers": 3}, {"kind": "drawers", "drawers": 3}]
    assert spec["dimensions"] == {"width": 1600, "depth": 300, "height": 800}


def test_free_text_answer_goes_to_llm():
    qs = questions_for(KITCHEN_DRAFT, KITCHEN_FACTS, None)
    _spec, free = apply_answers(KITCHEN_DRAFT, qs, {"product": "пенал под холодильник 600×600×2050"})
    assert free and "пенал под холодильник" in free[0]


def test_import_returns_questions_instead_of_a_wrong_product():
    res = _accept_created(dict(KOMOD_DRAFT), reply="Создан.", usage=None, trace={},
                          build=None, history=None,
                          context={"tz_import": True, "tz_image": KOMOD_IMAGE},
                          capture={"vision_facts": KOMOD_FACTS})
    assert res["code"] == "clarification_needed"
    assert res["spec"] is None and res["draft"]["archetype"] == "drawer_unit"
    assert _ids(res["questions"]) == ["layout", "dims"]


def test_answered_clarification_builds_a_product_that_passes_the_gate():
    qs = questions_for(KOMOD_DRAFT, KOMOD_FACTS, KOMOD_IMAGE)
    res = create_from_clarification({"facts": KOMOD_FACTS, "draft": KOMOD_DRAFT, "questions": qs,
                                     "answers": {"layout": "3x3", "dims": "1600x300x800"}})
    assert res.get("created") is True, res.get("reply")
    spec = res["spec"]
    assert spec["archetype"] == "cabinet"
    assert spec["dimensions"]["width"] == 1600
    assert [s["drawers"] for s in spec["sections"]] == [3, 3]


# GigaChat присылает факты в JSON-обёртке с экранированными переносами строк
GIGACHAT_KOMOD = ('```json\n{"reply": "Тип изделия: Камода в спальню\nИзделий на листе: 1\n'
                  'Модулей (корпусов): 1 (1500)\nВнешние габариты Ш×Г×В: 1500×400×1100\n'
                  'Колонки слева направо: Колонка 1 (1500): 3 ящика"}\n```')


def test_wrapped_facts_are_unwrapped():
    from src.tz_clarify import clean_facts

    text = clean_facts(GIGACHAT_KOMOD)
    assert text.startswith("Тип изделия: Камода") and "\nКолонки слева направо" in text


def test_small_image_asks_layout_even_for_one_short_stack():
    draft = {**KOMOD_DRAFT, "sections": [{"kind": "drawers", "drawers": 3}]}
    qs = questions_for(draft, GIGACHAT_KOMOD, KOMOD_IMAGE)
    layout = next(q for q in qs if q["id"] == "layout")
    assert layout["suggested"] == "3"                     # не навязываем, но предлагаем 3+3
    assert "3x3" in {o["value"] for o in layout["options"]}


def test_own_layout_answer_is_parsed_without_llm():
    draft = {**KOMOD_DRAFT, "sections": [{"kind": "drawers", "drawers": 3}]}
    qs = questions_for(draft, GIGACHAT_KOMOD, KOMOD_IMAGE)
    spec, free = apply_answers(draft, qs, {"layout": "2 по 3"})
    assert free == [] and spec["archetype"] == "cabinet"
    assert [s["drawers"] for s in spec["sections"]] == [3, 3]


def test_echoed_capability_schema_is_not_a_fact():
    from src.tz_clarify import clean_facts

    raw = ('Тип изделия: Шкаф\nНеясно: нет\n\n'
           '{"type":"object","additionalProperties":false,"required":["reply"]}')
    assert clean_facts(raw) == "Тип изделия: Шкаф\nНеясно: нет"


def test_dimension_not_on_the_drawing_is_asked():
    """Модель прочитала 1500, а на чертеже подписаны 1600/780/300/800 (OCR)."""
    draft = {**KOMOD_DRAFT, "archetype": "cabinet",
             "dimensions": {"width": 1500, "depth": 300, "height": 800},
             "sections": [{"kind": "drawers", "drawers": 3}, {"kind": "drawers", "drawers": 3}]}
    big = {"bytes": 300_000, "width": 2000, "height": 1400}
    qs = questions_for(draft, NEW_FORMAT_FACTS, big, numbers=[30, 40, 250, 300, 780, 800, 1600])
    dims = next(q for q in qs if q["id"] == "dims")
    assert any("ширина 1500" in r for r in dims["reasons"])
    # всё подписано — вопроса нет; OCR ничего не нашёл — не сверяем
    ok = {**draft, "dimensions": {"width": 1600, "depth": 300, "height": 800}}
    assert questions_for(ok, NEW_FORMAT_FACTS, big, numbers=[300, 780, 800, 1600]) == []
    assert questions_for(ok, NEW_FORMAT_FACTS, big, numbers=[]) == []


def test_two_doors_in_one_section_are_asked_when_tz_says_two_sections():
    """komi 46: «Шкаф содержит две секции, разделён по вертикали» — а собрана одна."""
    draft = {"schemaVersion": "paramspec-v1", "project_name": "Шкаф 46", "archetype": "door_unit",
             "dimensions": {"width": 1000, "depth": 400, "height": 1800},
             "materials": {"board_thickness": 16},
             "sections": [{"kind": "door", "door": 2, "shelves": 4}]}
    text = "Текст ТЗ из PDF:\n2 распашные двери, 4 полки. Шкаф содержит две секции, разделен по вертикали"
    qs = questions_for(draft, text, {"bytes": 400_000, "width": 2000, "height": 1414})
    q = next(q for q in qs if q["id"] == "door_sections")
    spec, free = apply_answers(draft, qs, {"door_sections": q["suggested"]})
    assert free == [] and spec["archetype"] == "cabinet"
    assert spec["sections"] == [{"kind": "door", "shelves": 4, "door": 1},
                                {"kind": "door", "shelves": 4, "door": 1}]
    # без упоминания секций — не спрашиваем
    assert not [q for q in questions_for(draft, "2 распашные двери, 4 полки",
                                         {"bytes": 400_000, "width": 2000, "height": 1414})
                if q["id"] == "door_sections"]
