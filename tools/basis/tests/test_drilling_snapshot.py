"""Присадки всех изделий совпадают со снимком (MEB-168).

Переделка движка (таблица правил стыков, дерево зон) идёт шагами; на каждом
шаге присадки не должны меняться, пока правило не меняется осознанно. Тогда
снимок пересобирается: python -m tests.drilling_snapshot --update.
"""

from __future__ import annotations

import json

from tests.drilling_snapshot import SNAPSHOT, build_snapshot, diff_snapshots


def test_drilling_of_every_product_matches_the_snapshot():
    expected = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    diff = diff_snapshots(expected, build_snapshot())
    assert not diff, "присадки изменились:\n" + "\n".join(diff)


def test_box_back_mode_contract_values_switch_the_drawer_back():
    """Схема разрешает beyond|inset — генератор раньше ждал outside и молча игнорировал."""
    from src.generators import generate_from_paramspec

    spec = {"schemaVersion": "paramspec-v1", "project_name": "Тумба", "archetype": "drawer_unit",
            "dimensions": {"width": 600, "depth": 450, "height": 720},
            "materials": {"board_thickness": 16},
            "sections": [{"kind": "drawers", "drawers": 2}]}

    def backs(mode):
        sec = {**spec["sections"][0], **({"box_back_mode": mode} if mode else {})}
        project = generate_from_paramspec({**spec, "sections": [sec]})
        return [p["placement"] for p in project["panels"] if p.get("type") == "drawer_back"]

    inset, beyond = backs("inset"), backs("beyond")
    assert inset == backs(None)                 # по умолчанию — между боковинами
    assert beyond != inset                      # накладная — за боковинами
    assert beyond == backs("outside")           # прежнее написание тоже работает
