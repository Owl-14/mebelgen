"""Сравнение провайдеров на реальных ТЗ: кто чаще доводит ТЗ до ВЕРНОГО изделия.

Один прогон ничего не доказывает — модель каждый раз отвечает по-разному,
поэтому каждый ТЗ прогоняется N раз на каждом провайдере и считается доля
принятых изделий, средняя задержка и расход токенов. Выбор сборщика после
этого делается по таблице, а не по впечатлению.

    python qa/tz_bench.py --tz-dir /opt/bazis/tz-test --providers glm,kimi -n 3

«Принято гейтом» не значит «верно»: комод 1500×400×1100 вместо 1600×300×800
гейт пропускает. Если рядом с ТЗ лежит <имя>.expect.json, кандидат (изделие
или черновик под уточнения) сверяется с эталоном:

    {"archetype": "cabinet", "dimensions": [1600, 300, 800],
     "columns": [3, 3], "tolerance_mm": 20, "clarify": true}

columns — ящиков по колонкам слева направо; clarify — на этом ТЗ обязаны
прозвучать уточнения (кухня, мелкая картинка). Считаются две точности: как
распознала модель и после ответа «Как предлагаешь» на уточнения.

Нужны живые ключи в .env; каждый запуск тратит токены провайдера.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PROMPT = ("Собери ParamSpec ТОЛЬКО по этому ТЗ (фото/скан): определи тип изделия, габариты, "
          "секции, материал по изображению. НЕ бери ничего из других изделий. created=true.")

_ARCH_GROUP = {"wardrobe": "cabinet", "table": "desk"}
_ACCEPTED = ("ok", "clarification_needed")


def _expect(source: Path) -> dict | None:
    path = source.with_name(source.stem + ".expect.json")
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _cases(tz_dir: Path) -> list[tuple[str, list[dict[str, str]] | None, dict | None, Path]]:
    cases: list[tuple[str, list[dict[str, str]] | None, dict | None, Path]] = []
    images = sorted(tz_dir.glob("*.png")) + sorted(tz_dir.glob("*.jpg")) + sorted(tz_dir.glob("*.jpeg"))
    for image in images:
        data = base64.b64encode(image.read_bytes()).decode()
        mime = "image/png" if image.suffix == ".png" else "image/jpeg"
        cases.append((image.stem[:38], [{"mime": mime, "data": data}], _expect(image), image))
    for pdf in sorted(tz_dir.glob("*.pdf")):
        cases.append((pdf.stem[:38] + " [pdf]", None, _expect(pdf), pdf))
    for text_file in sorted(tz_dir.glob("*.txt")):
        cases.append((text_file.stem[:38], None, _expect(text_file), text_file))
    return cases


def _columns(spec: dict) -> list[int]:
    return [int(s.get("drawers") or 0) for s in spec.get("sections") or []
            if isinstance(s, dict) and s.get("kind") == "drawers"]


def score(spec: dict | None, expect: dict) -> list[str]:
    """Расхождения кандидата с эталоном; пустой список — изделие верное."""
    if not isinstance(spec, dict):
        return ["нет изделия"]
    misses = []
    if "archetype" in expect:
        got = _ARCH_GROUP.get(spec.get("archetype"), spec.get("archetype"))
        want = _ARCH_GROUP.get(expect["archetype"], expect["archetype"])
        if got != want:
            misses.append(f"тип {spec.get('archetype')}≠{expect['archetype']}")
    if "dimensions" in expect:
        tol = float(expect.get("tolerance_mm", 20))
        dims = spec.get("dimensions") or {}
        got = [float(dims.get(k) or 0) for k in ("width", "depth", "height")]
        if any(abs(a - b) > tol for a, b in zip(got, expect["dimensions"])):
            misses.append("габариты " + "×".join(f"{v:g}" for v in got) + "≠"
                          + "×".join(f"{v:g}" for v in expect["dimensions"]))
    if "columns" in expect and _columns(spec) != list(expect["columns"]):
        misses.append(f"колонки {_columns(spec)}≠{expect['columns']}")
    return misses


def _with_defaults(result: dict) -> dict | None:
    """Черновик после ответа «Как предлагаешь» на все уточнения."""
    from src.tz_clarify import apply_answers

    questions = result.get("questions") or []
    answers = {q["id"]: q.get("suggested", "") for q in questions}
    spec, _free = apply_answers(result.get("draft") or {}, questions, answers)
    return spec


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tz-dir", required=True, help="каталог с ТЗ (png/jpg/txt)")
    parser.add_argument("--providers", required=True, help="через запятую: glm,kimi,deepseek")
    parser.add_argument("-n", "--repeats", type=int, default=3, help="прогонов на каждый ТЗ")
    parser.add_argument("--vision", help="кто читает картинку (VISION_EXTRACT_PROVIDER); "
                                         "по умолчанию — из .env")
    parser.add_argument("--json", help="куда сохранить подробный отчёт")
    args = parser.parse_args()

    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    if args.vision:
        os.environ["VISION_EXTRACT_PROVIDER"] = args.vision
    from src.spec_chat import chat_edit
    from src.studio import _image_info

    cases = _cases(Path(args.tz_dir))
    if not cases:
        print("в каталоге нет ни одного ТЗ")
        return 1

    rows: list[dict] = []
    for provider in [item.strip() for item in args.providers.split(",") if item.strip()]:
        for name, images, expect, source in cases:
            for attempt in range(args.repeats):
                started = time.time()
                journal: dict = {}
                context: dict = {"tz_import": True}
                if source.suffix == ".pdf":            # как Studio: текст PDF + страницы картинкой
                    from src.tz_pdf import read_pdf

                    pdf = read_pdf(source.read_bytes())
                    images, context["tz_text"] = pdf["images"], pdf["text"]
                    context["tz_numbers"] = pdf["numbers"]
                elif images:
                    from src.tz_ocr import drawing_numbers

                    context["tz_numbers"] = drawing_numbers(images[0]["data"])
                message = PROMPT if images else (
                    "Собери ParamSpec по этому ТЗ. " + source.read_text(encoding="utf-8")[:4000])
                context["tz_image"] = _image_info(images[0]["data"]) if images else {}
                row: dict = {"provider": provider, "tz": name, "attempt": attempt + 1}
                try:
                    result = chat_edit({}, message, images=images, provider=provider,
                                       context=context, journal=journal)
                    code = result.get("code") or "ok"
                    tokens = ((result.get("usage") or {}).get("total")) or 0
                    row["questions"] = [q["id"] for q in result.get("questions") or []]
                    if expect:
                        row["misses"] = score(result.get("spec") or result.get("draft"), expect)
                        row["misses_after_defaults"] = (
                            score(_with_defaults(result), expect)
                            if code == "clarification_needed" else list(row["misses"]))
                        if expect.get("clarify") and code != "clarification_needed":
                            row["misses_after_defaults"].append("не спросил уточнений")
                except Exception as error:  # noqa: BLE001 - падение провайдера тоже результат
                    code, tokens = f"exception:{type(error).__name__}", 0
                    if expect:
                        row["misses"] = ["нет ответа"]
                        row["misses_after_defaults"] = ["нет ответа"]
                row.update({"code": code, "seconds": round(time.time() - started, 1),
                            "tokens": tokens, "facts": journal.get("vision_facts"),
                            "answered_by": journal.get("provider") or provider})
                rows.append(row)

    print(f"\n{'провайдер':<12} {'принято':<9} {'верно':<7} {'верно+уточн.':<13} "
          f"{'сек (медиана)':<15} {'токенов':<10} частые отказы")
    print("-" * 100)
    summary = defaultdict(list)
    for row in rows:
        summary[row["provider"]].append(row)
    for provider, items in summary.items():
        accepted = [item for item in items if item["code"] in _ACCEPTED]
        scored = [item for item in items if "misses" in item]
        right = sum(1 for item in scored if not item["misses"])
        right_after = sum(1 for item in scored if not item["misses_after_defaults"])
        codes = defaultdict(int)
        for item in items:
            if item["code"] not in _ACCEPTED:
                codes[item["code"]] += 1
        top = ", ".join(f"{code}×{count}" for code, count in
                        sorted(codes.items(), key=lambda pair: -pair[1])[:3]) or "—"
        median = statistics.median([item["seconds"] for item in items]) if items else 0
        tokens = statistics.mean([item["tokens"] for item in items if item["tokens"]]) \
            if any(item["tokens"] for item in items) else 0
        right_col = f"{right}/{len(scored)}" if scored else "—"
        after_col = f"{right_after}/{len(scored)}" if scored else "—"
        print(f"{provider:<12} {len(accepted)}/{len(items):<7} {right_col:<7} {after_col:<13} "
              f"{median:<15} {round(tokens):<10} {top}")

    misses = [item for item in rows if item.get("misses_after_defaults")]
    if misses:
        print("\nрасхождения с эталоном (после «Как предлагаешь»):")
        for item in misses:
            print(f"  {item['provider']:<10} {item['tz']:<38} #{item['attempt']}: "
                  + "; ".join(item["misses_after_defaults"]))

    if args.json:
        Path(args.json).write_text(json.dumps(rows, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
        print(f"\nподробный отчёт: {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
