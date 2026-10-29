"""Бенч правок и команд чата: какая модель правильнее понимает проектировщика.

Сценарии — qa/e2e_ai.py (создание, правки, диагностика, каверзы) плюс реальные
фразы из AI-журнала и прежнего trace_eval. В отличие от e2e_ai.py, здесь нет
HTTP: chat_edit вызывается напрямую, изделие проверяется производственным
гейтом локально, а модель задаётся аргументом — так одни и те же команды
сравниваются на нескольких моделях.

    python qa/edit_bench.py --models glm-4.5-flash,glm-4.7,glm-5.3 -n 1 --json out.json

Нужны живые ключи в .env (LLM_BASE_URL и ключ провайдера); тратит токены.
"""

from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DRESSER = {"schemaVersion": "paramspec-v1", "project_name": "Комод в спальню",
           "archetype": "drawer_unit", "dimensions": {"width": 1600, "depth": 400, "height": 800},
           "materials": {"board_thickness": 16}, "sections": [{"kind": "drawers", "drawers": 6}]}
WARDROBE = {"schemaVersion": "paramspec-v1", "project_name": "Шкаф", "archetype": "cabinet",
            "dimensions": {"width": 1200, "depth": 600, "height": 2200},
            "materials": {"board_thickness": 16},
            "sections": [{"kind": "door", "door": 1, "shelves": 3},
                         {"kind": "drawers", "drawers": 3}]}


def _load_e2e():
    """CASES из qa/e2e_ai.py без его HTTP: generates() заменяем локальным гейтом."""
    # e2e_ai при импорте заворачивает stdout в utf-8 — оставляем его обёртку:
    # возврат старой закрыл бы общий буфер вывода
    spec = importlib.util.spec_from_file_location("e2e_ai", ROOT / "qa" / "e2e_ai.py")
    module = importlib.util.module_from_spec(spec)
    argv, sys.argv = sys.argv, [sys.argv[0]]
    try:
        spec.loader.exec_module(module)
    finally:
        sys.argv = argv

    def generates(candidate: dict) -> tuple[bool, str]:
        from src.production_gate import evaluate_production_gate

        decision = evaluate_production_gate(candidate, unresolved_materials_are_errors=False)
        if decision.report.ok:
            return True, ""
        return False, "; ".join(str(i.detail)[:80] for i in decision.report.errors[:2])

    module.generates = generates
    return module


def _extra_cases(e2e) -> list[tuple]:
    expect = e2e._expect_spec

    def _columns(spec: dict) -> list[int]:
        return [int(s.get("drawers") or 0) for s in spec.get("sections") or []
                if s.get("kind") == "drawers"]

    def _box_two_thirds(spec: dict) -> str | None:
        from src.production_gate import evaluate_production_gate

        project = evaluate_production_gate(spec, unresolved_materials_are_errors=False).project
        fronts = [p["dimensions"]["height"] for p in project["panels"] if p.get("type") == "drawer_front"]
        sides = [p["dimensions"]["height"] for p in project["panels"]
                 if p.get("type") == "drawer_side_left"]
        if not fronts or not sides:
            return "нет ящиков"
        ratio = min(sides) / min(fronts)
        return None if 0.55 <= ratio <= 0.75 else f"боковина/фасад = {ratio:.2f}"

    return [
        # реальные фразы из AI-журнала studio.akeda.ru
        ("журнал: 3 слева и 3 справа", DRESSER,
         "ящиков должно быть 3 в левой части и 3 в правой, ты их неправильно расположил",
         expect(lambda s: None if _columns(s) == [3, 3] else f"колонки={_columns(s)}")),
        ("журнал: стенки ящиков 2/3 фасада", {**DRESSER, "dimensions": {"width": 600, "depth": 450,
                                                                         "height": 750},
                                               "sections": [{"kind": "drawers", "drawers": 3}]},
         "сделай стенки ящиков выше, сейчас они на половине от фасада, должно быть примерно 2/3",
         expect(_box_two_thirds)),
        ("журнал: разбить на две колонки", DRESSER, "разбей ящики на две колонки рядом, не стопкой",
         expect(lambda s: None if _columns(s) == [3, 3] else f"колонки={_columns(s)}")),
        # команды прежнего trace_eval
        ("trace: полка в левой секции на 1700", WARDROBE, "Добавь в левую секцию полку на высоте 1700 мм",
         expect(lambda s: None if any(
             1650 <= float(((o.get("placement") or {}).get("y1")) or 0) <= 1750
             for o in s.get("overrides") or []) or any(
             any(1650 <= float(y) <= 1750 for y in (sec.get("shelf_levels") or []))
             for sec in s.get("sections") or []) else "полки на ~1700 нет")),
        ("trace: правую секцию первой", WARDROBE, "Поставь правую секцию первой",
         expect(lambda s: None if [sec.get("kind") for sec in s.get("sections") or []]
                == ["drawers", "door"] else f"порядок={[sec.get('kind') for sec in s.get('sections') or []]}")),
        ("trace: ширина 20 мм — отказ", WARDROBE, "Сделай ширину 20 мм",
         lambda r: None if (not r.get("spec")) or r["spec"]["dimensions"]["width"] >= 300
         else f"приняла ширину {r['spec']['dimensions']['width']}"),
        ("trace: ширина 1600", WARDROBE, "Сделай ширину 1600 мм",
         expect(lambda s: None if s["dimensions"]["width"] == 1600 else f"width={s['dimensions']['width']}")),
        ("проектировщик: две двери вместо одной", WARDROBE, "в левой секции сделай две двери вместо одной",
         expect(lambda s: None if (s.get("sections") or [{}])[0].get("door") == 2
                else f"секции={s.get('sections')}")),
        ("проектировщик: убери нижний ящик", WARDROBE, "убери один ящик, пусть их будет два",
         expect(lambda s: None if _columns(s) == [2] else f"колонки={_columns(s)}")),
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", required=True, help="через запятую, напр. glm-4.7,glm-5.3")
    parser.add_argument("--provider", default="glm")
    parser.add_argument("-n", "--repeats", type=int, default=1)
    parser.add_argument("--only", help="подстрока имени сценария")
    parser.add_argument("--json", help="куда сохранить подробный отчёт")
    args = parser.parse_args()

    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    e2e = _load_e2e()
    cases = [*e2e.CASES, *_extra_cases(e2e)]
    if args.only:
        cases = [case for case in cases if args.only in case[0]]
    from src.spec_chat import chat_edit

    rows: list[dict] = []
    for model in [m.strip() for m in args.models.split(",") if m.strip()]:
        os.environ["LLM_MODEL"] = os.environ["LLM_EDIT_MODEL"] = model   # одна ступень — честное сравнение
        for name, spec, message, check in cases:
            base = e2e.BASE_SPEC if spec is None else spec
            for attempt in range(args.repeats):
                started = time.time()
                journal: dict = {}
                try:
                    result = chat_edit(copy.deepcopy(base), message, context=dict(e2e.GREEN_CTX),
                                       provider=args.provider, journal=journal)
                    error = check(result)
                except Exception as exc:  # noqa: BLE001 - сбой провайдера — тоже результат
                    result, error = {}, f"exc: {type(exc).__name__}: {str(exc)[:120]}"
                rows.append({"model": model, "case": name, "attempt": attempt + 1,
                             "ok": error is None, "error": error,
                             "seconds": round(time.time() - started, 1),
                             "tokens": ((result.get("usage") or {}).get("total")) or 0,
                             "reply": str(result.get("reply") or "")[:200]})
                mark = "pass" if error is None else "FAIL"
                print(f"{model:<14} {mark} {name} — {error or ''}", flush=True)

    print(f"\n{'модель':<16} {'верно':<9} {'сек (сред.)':<12} токенов")
    for model in dict.fromkeys(row["model"] for row in rows):
        items = [row for row in rows if row["model"] == model]
        ok = sum(row["ok"] for row in items)
        seconds = sum(row["seconds"] for row in items) / max(len(items), 1)
        tokens = sum(row["tokens"] for row in items)
        print(f"{model:<16} {ok}/{len(items):<7} {seconds:<12.1f} {tokens}")
    if args.json:
        Path(args.json).write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
