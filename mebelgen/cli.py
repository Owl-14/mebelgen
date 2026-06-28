"""Command-line interface:

    python -m mebelgen <spec.doc|.docx> [--out output] [--date ..] [--manager ..]

Produces, for every item in the spec table:
    output/<nn>_<slug>.svg   - the reference sheet
    output/<nn>_<slug>.png   - raster version (if a converter is available)
    output/specs.json        - parsed structured data
    output/index.html        - gallery of all sheets
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import re
import sys

from .draw.sheet import build_sheet
from .draw.technical_sheet import build_technical_sheet
from .model.classifier import classify
from .parsing import parse_item, read_spec_rows
from .parsing.image_analysis import analyze_image
from .parsing.spec_parser import apply_image_cues
from .render import build_gallery, save_svg, try_png


def _slug(text: str) -> str:
    translit = {
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
        "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
        "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
        "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "",
        "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    }
    out = []
    for ch in text.lower():
        out.append(translit.get(ch, ch))
    s = "".join(out)
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return s[:40] or "item"


def _spec_to_dict(spec):
    return {
        "index": spec.index,
        "name": spec.name,
        "qty": spec.qty,
        "unit": spec.unit,
        "archetype": spec.archetype,
        "dimensions": dataclasses.asdict(spec.dims),
        "material": dataclasses.asdict(spec.material),
        "features": spec.features,
        "material_lines": spec.material_lines,
        "image_path": spec.image_path,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(prog="mebelgen",
                                 description="ТЗ (Word) -> визуальные референсы мебели")
    ap.add_argument("spec", help="путь к .doc/.docx со спецификацией")
    ap.add_argument("--out", default="output", help="папка для результатов")
    ap.add_argument("--date", default=None, help="дата для штампа")
    ap.add_argument("--manager", default=None, help="руководитель для штампа")
    ap.add_argument("--png", action="store_true", help="пытаться сохранять PNG")
    ap.add_argument("--no-3d", action="store_true",
                    help="не запускать Blender, рисовать векторную изометрию")
    ap.add_argument("--blender", default=None, help="путь к blender.exe")
    ap.add_argument("--style", choices=("classic", "technical"), default="classic",
                    help="стиль листа: classic или experimental technical")
    args = ap.parse_args(argv)

    if not os.path.exists(args.spec):
        print(f"Файл не найден: {args.spec}", file=sys.stderr)
        return 2

    os.makedirs(args.out, exist_ok=True)
    rows = read_spec_rows(args.spec)
    print(f"Найдено изделий: {len(rows)}")

    # 1) parse + classify all items, compute output base names
    entries = []  # (spec, base)
    specs = []
    for i, raw in enumerate(rows, 1):
        spec = parse_item(raw)
        if spec.image_path:
            try:
                apply_image_cues(spec, analyze_image(spec.image_path))
            except Exception:
                pass
        spec.archetype = classify(spec)
        specs.append(spec)
        num = re.sub(r"\D", "", spec.index or "") or str(i)
        base = os.path.join(args.out, f"{int(num):02d}_{_slug(spec.name)}")
        entries.append((spec, base))

    # 2) photoreal 3D heroes via Blender (single headless run for all items)
    hero_map = {}
    if not args.no_3d:
        try:
            from .render.blender_render import find_blender, render_all
            bl = args.blender or find_blender()
            if bl:
                print(f"3D рендер (Blender): {bl}")
                render_specs = []
                for spec, base in entries:
                    d = _spec_to_dict(spec)
                    d["_slug"] = os.path.basename(base)
                    render_specs.append(d)
                hero_map = render_all(render_specs,
                                      os.path.join(args.out, "render3d"),
                                      blender=bl)
                print(f"  отрендерено: {len(hero_map)}/{len(entries)}")
            else:
                print("Blender не найден -> векторная изометрия "
                      "(задайте --blender или MEBELGEN_BLENDER)")
        except Exception as e:
            print(f"3D рендер пропущен: {e}")

    # 3) compose sheets (with hero render when available)
    svg_files = []
    for spec, base in entries:
        spec.hero_png = hero_map.get(os.path.basename(base))
        builder = build_technical_sheet if args.style == "technical" else build_sheet
        svg = builder(spec, date=args.date, manager=args.manager)
        svg_path = base + ".svg"
        save_svg(svg.tostring(), svg_path)
        svg_files.append(svg_path)
        png_note = ""
        if args.png:
            if try_png(svg_path, base + ".png"):
                png_note = " (+png)"
        tag = "3D" if spec.hero_png else "2D"
        print(f"  [{spec.archetype:<13}|{tag}] {spec.name}  -> {os.path.basename(svg_path)}{png_note}")

    with open(os.path.join(args.out, "specs.json"), "w", encoding="utf-8") as fh:
        json.dump([_spec_to_dict(s) for s in specs], fh, ensure_ascii=False, indent=2)

    gallery = build_gallery(svg_files, os.path.join(args.out, "index.html"))
    print(f"\nГотово. Галерея: {gallery}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
