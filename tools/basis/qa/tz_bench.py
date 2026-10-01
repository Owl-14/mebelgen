"""Сравнение провайдеров на реальных ТЗ: кто чаще доводит ТЗ до изделия.

Один прогон ничего не доказывает — модель каждый раз отвечает по-разному,
поэтому каждый ТЗ прогоняется N раз на каждом провайдере и считается доля
принятых изделий, средняя задержка и расход токенов. Выбор сборщика после
этого делается по таблице, а не по впечатлению.

    python qa/tz_bench.py --tz-dir /opt/bazis/tz-test --providers glm,kimi -n 3

Нужны живые ключи в .env; каждый запуск тратит токены провайдера.
"""

from __future__ import annotations

import argparse
import base64
import json
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PROMPT = ("Собери ParamSpec ТОЛЬКО по этому ТЗ (фото/скан): определи тип изделия, габариты, "
          "секции, материал по изображению. НЕ бери ничего из других изделий. created=true.")


def _cases(tz_dir: Path) -> list[tuple[str, list[dict[str, str]] | None]]:
    cases: list[tuple[str, list[dict[str, str]] | None]] = []
    for image in sorted(tz_dir.glob("*.png")) + sorted(tz_dir.glob("*.jpg")):
        data = base64.b64encode(image.read_bytes()).decode()
        cases.append((image.stem[:38], [{"mime": "image/png", "data": data}]))
    for text_file in sorted(tz_dir.glob("*.txt")):
        cases.append((text_file.stem[:38], None))
    return cases


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tz-dir", required=True, help="каталог с ТЗ (png/jpg/txt)")
    parser.add_argument("--providers", required=True, help="через запятую: glm,kimi,gigachat")
    parser.add_argument("-n", "--repeats", type=int, default=3, help="прогонов на каждый ТЗ")
    parser.add_argument("--json", help="куда сохранить подробный отчёт")
    args = parser.parse_args()

    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    from src.spec_chat import chat_edit

    cases = _cases(Path(args.tz_dir))
    if not cases:
        print("в каталоге нет ни одного ТЗ")
        return 1

    rows: list[dict] = []
    for provider in [item.strip() for item in args.providers.split(",") if item.strip()]:
        for name, images in cases:
            for attempt in range(args.repeats):
                started = time.time()
                journal: dict = {}
                message = PROMPT if images else (
                    "Собери ParamSpec по этому ТЗ. " + Path(args.tz_dir, name + ".txt")
                    .read_text(encoding="utf-8")[:4000])
                try:
                    result = chat_edit({}, message, images=images, provider=provider,
                                       journal=journal)
                    code = result.get("code") or "ok"
                    tokens = ((result.get("usage") or {}).get("total")) or 0
                except Exception as error:  # noqa: BLE001 - падение провайдера тоже результат
                    code, tokens = f"exception:{type(error).__name__}", 0
                rows.append({"provider": provider, "tz": name, "attempt": attempt + 1,
                             "code": code, "seconds": round(time.time() - started, 1),
                             "tokens": tokens,
                             "answered_by": journal.get("provider") or provider})

    print(f"\n{'провайдер':<12} {'принято':<9} {'сек (медиана)':<15} {'токенов':<10} частые отказы")
    print("-" * 78)
    summary = defaultdict(list)
    for row in rows:
        summary[row["provider"]].append(row)
    for provider, items in summary.items():
        ok = [item for item in items if item["code"] == "ok"]
        codes = defaultdict(int)
        for item in items:
            if item["code"] != "ok":
                codes[item["code"]] += 1
        top = ", ".join(f"{code}×{count}" for code, count in
                        sorted(codes.items(), key=lambda pair: -pair[1])[:3]) or "—"
        median = statistics.median([item["seconds"] for item in items]) if items else 0
        tokens = statistics.mean([item["tokens"] for item in items if item["tokens"]]) \
            if any(item["tokens"] for item in items) else 0
        print(f"{provider:<12} {len(ok)}/{len(items):<7} {median:<15} {round(tokens):<10} {top}")

    if args.json:
        Path(args.json).write_text(json.dumps(rows, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
        print(f"\nподробный отчёт: {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
