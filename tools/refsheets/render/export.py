"""Output helpers: write SVG, optional PNG conversion, HTML gallery."""
from __future__ import annotations

import os
from typing import List, Optional


def save_svg(svg_str: str, path: str) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(svg_str)
    return path


def try_png(svg_path: str, png_path: str, width: int = 1588) -> Optional[str]:
    """Best-effort SVG->PNG. Returns png_path on success else None.

    Prefers ``resvg-py`` (self-contained Rust renderer, no system deps).
    Falls back to ``cairosvg`` if its native cairo library is present.
    Degrades silently to SVG-only when no converter is available.
    """
    # 1) resvg-py
    try:
        import resvg_py  # type: ignore
        data = resvg_py.svg_to_bytes(svg_path=svg_path, background="#ffffff",
                                     width=width)
        if isinstance(data, list):
            data = bytes(data)
        with open(png_path, "wb") as fh:
            fh.write(data)
        return png_path
    except Exception:
        pass
    # 2) cairosvg
    try:
        import cairosvg  # type: ignore
        cairosvg.svg2png(url=svg_path, write_to=png_path, output_width=width,
                         background_color="white")
        return png_path
    except Exception:
        return None


def build_gallery(svg_files: List[str], out_html: str, title="MEBELGEN — референсы"):
    items = []
    base = os.path.dirname(os.path.abspath(out_html))
    for f in svg_files:
        rel = os.path.relpath(os.path.abspath(f), base).replace("\\", "/")
        items.append(
            f'<figure><img src="{rel}" loading="lazy"/>'
            f'<figcaption>{os.path.basename(f)}</figcaption></figure>'
        )
    html = f"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{title}</title>
<style>
 body{{margin:0;background:#3a3a3a;font-family:Arial,sans-serif;color:#eee}}
 header{{padding:18px 24px;background:#222;position:sticky;top:0}}
 h1{{font-size:18px;margin:0;font-weight:600;letter-spacing:1px}}
 .grid{{display:flex;flex-wrap:wrap;gap:24px;padding:24px;justify-content:center}}
 figure{{margin:0;background:#fff;box-shadow:0 6px 24px rgba(0,0,0,.4);border-radius:4px;overflow:hidden}}
 figure img{{display:block;width:420px;height:auto}}
 figcaption{{padding:8px 10px;font-size:12px;color:#333;background:#f3f3f3}}
</style></head>
<body><header><h1>{title}</h1></header>
<div class="grid">{''.join(items)}</div></body></html>"""
    with open(out_html, "w", encoding="utf-8") as fh:
        fh.write(html)
    return out_html
