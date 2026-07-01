"""Проверка КОДИРОВАНИЯ .cfrn (а не только placement).

placement-чек (consistency_check) видит только наши координаты. Но панель
кодируется в .cfrn матрицей 4×4, и толщина выдавливается в сторону, зависящую
от ориентации. Если трансляцию поставить не по той грани — деталь уезжает на
толщину, и .b3d собирается с нахлёстами, хотя placement был чист.

Этот класс багов ловится реконструкцией мировых AABB из матриц .cfrn и сверкой
с placement (совпадает с обратной выгрузкой b3d→cfrn из облака до миллиметра).

Запуск:  python -m pytest tests/test_cfrn_encoding.py   (из tools/basis)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.cfrn import check_cfrn_encoding                       # noqa: E402
from src.generators import generate_from_paramspec             # noqa: E402
from src.oldspec import old_to_paramspec                       # noqa: E402

PARAMSPECS = sorted((ROOT / "paramspecs").glob("*.json"))
OLDSPECS = sorted((ROOT / "fixtures" / "oldspecs").glob("*.json"))


def _paramspec_projects() -> list[tuple[str, dict]]:
    out = []
    for f in PARAMSPECS:
        s = json.loads(f.read_text(encoding="utf-8"))
        if s.get("schemaVersion") != "paramspec-v1":
            continue
        out.append((f.name, generate_from_paramspec(s)))
    return out


def test_paramspec_cfrn_encoding_matches_placement():
    bad = {}
    for name, pr in _paramspec_projects():
        issues = check_cfrn_encoding(pr)
        if issues:
            bad[name] = issues[:3]
    assert not bad, f"кодирование .cfrn ≠ placement:\n" + json.dumps(bad, ensure_ascii=False, indent=2)


def test_oldspec_cfrn_encoding_matches_placement():
    bad = {}
    for f in OLDSPECS:
        spec = old_to_paramspec(json.loads(f.read_text(encoding="utf-8")))
        pr = generate_from_paramspec(spec)
        issues = check_cfrn_encoding(pr)
        if issues:
            bad[f.name] = issues[:3]
    assert not bad, f"кодирование .cfrn ≠ placement:\n" + json.dumps(bad, ensure_ascii=False, indent=2)
