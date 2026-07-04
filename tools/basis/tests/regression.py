"""Регресс-харнесс: ParamSpec → генератор → валидаторы (+ сверка с эталоном).

Зелёный = все ParamSpec генерируют валидный JSON (схема+геометрия+согласованность).
exact = совпадение placement с эталоном из projects/ (±0.5 мм), где эталон есть.

Запуск:  python -m tests.regression   (из tools/basis)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.paramspec import validate_paramspec                       # noqa: E402
from src.generators import generate_from_paramspec                 # noqa: E402
from src.validate import validate_furniture                        # noqa: E402
from src.geometry_check import check_placement_geometry            # noqa: E402
from src.consistency_check import check_consistency                # noqa: E402
from src.completeness_check import check_completeness              # noqa: E402

# *.project.json / *.versions.json — артефакты Studio («Сохранить», версии), не ParamSpec
SPECS = sorted(p for p in (ROOT / "paramspecs").glob("*.json")
               if not p.name.endswith((".project.json", ".versions.json")))


def exact_match(gen: dict, golden_path: Path) -> tuple[bool, int, int, int]:
    gold = json.loads(golden_path.read_text(encoding="utf-8"))
    gp = {p["name"]: p["placement"] for p in gen["panels"]}
    gd = {p["name"]: p["placement"] for p in gold["panels"]}
    miss = len(set(gd) - set(gp))
    extra = len(set(gp) - set(gd))
    bad = sum(1 for n in set(gp) & set(gd) for k in ("x1", "x2", "y1", "y2", "z1", "z2")
              if abs(gp[n][k] - gd[n][k]) > 0.5)
    return (miss == 0 and extra == 0 and bad == 0), miss, extra, bad


def main() -> int:
    rows = []
    n_valid = n_exact = n_total = 0
    for spec_path in SPECS:
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        n_total += 1
        ps_err = validate_paramspec(spec)
        if ps_err:
            rows.append((spec_path.stem, "ParamSpec ERR", "", "", ps_err[0][:40]))
            continue
        try:
            gen = generate_from_paramspec(spec)
        except Exception as e:  # noqa: BLE001
            rows.append((spec_path.stem, f"GEN ERR", "", "", str(e)[:40]))
            continue
        sc = validate_furniture(gen)
        g = check_placement_geometry(gen)
        c = check_consistency(gen)
        comp = check_completeness(gen, spec)
        valid = (not sc) and g["ok"] and not c and not comp
        n_valid += valid
        golden = ROOT / "projects" / spec_path.name
        ex = "—"
        if golden.exists():
            ok, miss, extra, bad = exact_match(gen, golden)
            n_exact += ok
            ex = "EXACT" if ok else f"~ (m{miss}/e{extra}/d{bad})"
        note = "" if valid else (f"sc{len(sc)} g{0 if g['ok'] else g['overlap_count']} "
                                 f"c{len(c)} comp{len(comp)}" + (f" [{comp[0][:38]}]" if comp else ""))
        rows.append((spec_path.stem, "VALID" if valid else "INVALID", len(gen["panels"]), ex, note))

    w = max(len(r[0]) for r in rows)
    print(f"{'paramspec':<{w}}  {'status':<8} {'panels':>6}  {'vs golden':<14} note")
    print("-" * (w + 40))
    for name, st, n, ex, note in rows:
        print(f"{name:<{w}}  {st:<8} {str(n):>6}  {ex:<14} {note}")
    n_golden = sum(1 for s in SPECS if (ROOT / "projects" / s.name).exists())
    print("-" * (w + 40))
    print(f"valid: {n_valid}/{n_total} | exact vs golden: {n_exact}/{n_golden}")
    return 0 if n_valid == n_total else 1


if __name__ == "__main__":
    raise SystemExit(main())
