"""Регресс-набор из старых FurnitureSpec (AKD-19).

Конвертирует `fixtures/oldspecs/*.json` (выгрузка из three-spike) в ParamSpec и
прогоняет через генератор + валидаторы. Покрывает 11 типов изделий (шкафы, тумбы,
столы, кухни/гардеробные системы, кофейные столы) — стресс-тест генераторов.

Запуск:  python -m tests.test_oldspec_coverage   (из tools/basis)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.consistency_check import check_consistency            # noqa: E402
from src.generators import generate_from_paramspec             # noqa: E402
from src.geometry_check import check_placement_geometry        # noqa: E402
from src.oldspec import old_to_paramspec                       # noqa: E402
from src.paramspec import validate_paramspec                   # noqa: E402
from src.validate import validate_furniture                    # noqa: E402

FIXTURES = sorted((ROOT / "fixtures" / "oldspecs").glob("*.json"))


def _valid(old: dict) -> bool:
    spec = old_to_paramspec(old)
    assert not validate_paramspec(spec), spec.get("project_name")
    pr = generate_from_paramspec(spec)
    return (not validate_furniture(pr)) and check_placement_geometry(pr)["ok"] and not check_consistency(pr)


def test_all_oldspecs_generate_valid():
    assert FIXTURES, "нет фикстур fixtures/oldspecs/"
    bad = [f.name for f in FIXTURES if not _valid(json.loads(f.read_text(encoding="utf-8")))]
    assert not bad, f"невалидные: {bad}"


def main() -> int:
    by: dict[str, list[int]] = {}
    valid = 0
    for f in FIXTURES:
        old = json.loads(f.read_text(encoding="utf-8"))
        t = old.get("type", "?")
        ok = False
        try:
            ok = _valid(old)
        except Exception:  # noqa: BLE001
            ok = False
        by.setdefault(t, [0, 0])
        by[t][1] += 1
        by[t][0] += int(ok)
        valid += int(ok)
    print(f"old FurnitureSpec → ParamSpec → генератор: ВАЛИДНО {valid}/{len(FIXTURES)}")
    for t in sorted(by):
        print(f"  {t:14s} {by[t][0]}/{by[t][1]}")
    return 0 if valid == len(FIXTURES) else 1


if __name__ == "__main__":
    raise SystemExit(main())
