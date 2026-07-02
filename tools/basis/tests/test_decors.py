"""Декоры: выбор из производственной базы + палитра по слотам (корпус/фасады)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.decor_colors import build_palette, decor_base, decor_label   # noqa: E402
from src.materials import by_article, list_sheet_decors               # noqa: E402
from src.webviewer import _palette                                    # noqa: E402


def test_decor_label_strips_prefix():
    assert decor_label("ЛДСП белый 16 мм Дуб золотой P 002") == "Дуб золотой P 002"
    assert decor_label("ЛДСП, 3 мм, белый гл.") == "белый гл"
    assert decor_label("Компакт-плита чёрная") == "Компакт-плита чёрная"   # не распарсилось — целиком


def test_list_sheet_decors_search():
    items = list_sheet_decors("дуб 16")
    assert items, "в базе должны находиться дубы 16 мм"
    assert all("article" in i and i["hex"].startswith("#") for i in items)
    labels = [i["label"].lower() for i in items]
    assert all("дуб" in lb for lb in labels)
    # дубли по label схлопнуты
    assert len(labels) == len(set(labels))


def test_by_article_roundtrip():
    it = list_sheet_decors("дуб", limit=1)[0]
    found = by_article(str(it["article"]))
    assert found and found["name"] == it["name"]


def test_palette_facade_slot():
    pal = build_palette("Белый", "Дуб Вотан")
    assert pal["side_left"] != pal["door_front"], "фасады должны отличаться от корпуса"
    assert pal["door_front"].startswith("#")
    # корпусные типы от белой базы (светлые), фасады — от вотана (тёмные)
    assert pal["side_left"] > pal["door_front"]


def test_project_palette_uses_articles():
    it = list_sheet_decors("венге", limit=1)
    dark = it[0] if it else None
    proj = {"materials": {"color": "", "board_article": str(dark["article"])}} if dark else None
    if proj is None:                                   # в базе нет венге — берём любой
        any_it = list_sheet_decors("", limit=1)[0]
        proj = {"materials": {"color": "", "board_article": str(any_it["article"])}}
    pal = _palette(proj)
    assert pal["side_left"].startswith("#")


def test_facade_color_flows_to_project():
    from src.generators import generate_from_paramspec
    spec = json.loads((ROOT / "paramspecs" / "tz_tumba_dokumenty.json").read_text(encoding="utf-8"))
    spec["materials"]["facade_color"] = "Дуб Вотан"
    spec["materials"]["board_article"] = "12345"
    project = generate_from_paramspec(spec)
    assert project["materials"]["facade_color"] == "Дуб Вотан"
    assert project["materials"]["board_article"] == "12345"
    pal = _palette(project)
    assert pal["door_front"] != pal["side_left"]
