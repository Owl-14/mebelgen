"""AKD-15: веб-доставка — лист согласования проекта + версии + экспорт PDF/PNG.

Инструмент живёт не в чате, а как рабочий интерфейс: страница проекта — источник
правды, с 3D-просмотром (правосторонним, наш webviewer), спецификацией (материалы,
BOM фурнитуры с артикулами, детали, присадки, габариты), статусом согласования и
экспортом PDF/PNG.

Роли: оператор производства · менеджер · клиентский дизайнер (см. rules/delivery.md).
Жизненный цикл: draft → review → approved → production.

Версия = снапшот spec + project + метаданные рендера. Лист воспроизводится из
снапшота после изменений рендерера (bump RENDERER_VERSION).
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .hardware import compute_drilling, drilling_summary
from .techview import build_techview_svg
from .webviewer import project_to_viewer_html

RENDERER_VERSION = "2"      # ↑ при изменении вёрстки/рендерера листа (2: чертёж AKD-7/8)

STATUS_ORDER = ["draft", "review", "approved", "production"]
STATUS_RU = {
    "draft": "Черновик", "review": "На согласовании",
    "approved": "Согласовано", "production": "В производство",
}
STATUS_COLOR = {
    "draft": "#8a8f98", "review": "#d9822b",
    "approved": "#2fa84f", "production": "#3b82f6",
}


def _slug(name: str) -> str:
    keep = []
    for ch in str(name).lower():
        if ch.isalnum():
            keep.append(ch)
        elif ch in " -_./\\":
            keep.append("_")
    s = "".join(keep).strip("_")
    while "__" in s:
        s = s.replace("__", "_")
    return s or "model"


def _e(s: Any) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


# ------------------------------------------------------------------ спецификация

def _hardware_bom(project: dict[str, Any]) -> list[dict[str, str]]:
    """Фурнитура с артикулами из material_refs (BOM для листа)."""
    refs = project.get("material_refs") or {}
    label = {"handles": "Ручки", "hinges": "Петли", "drawer_guides": "Направляющие",
             "guides": "Направляющие", "legs": "Опоры/ножки", "locks": "Замки",
             "edge": "Кромка", "board": "Плита", "back": "Задняя стенка",
             "facade": "Плита фасадов"}
    # задник в BOM — только если задняя стенка реально есть среди деталей
    # (у столов слот «back» из материалов есть, а детали-задника нет)
    has_back_wall = any(
        p.get("type") == "back" and "стенк" in str(p.get("name", "")).lower()
        for p in project.get("panels", []))
    out: list[dict[str, str]] = []
    for slot, r in refs.items():
        if slot == "back" and not has_back_wall:
            continue
        if not isinstance(r, dict) or not r.get("resolved"):
            continue
        cand = None
        c = r.get("candidates")
        if isinstance(c, list) and c and isinstance(c[0], dict):
            cand = c[0]
        elif r.get("name"):
            cand = {"name": r.get("name"), "article": r.get("article")}
        if not cand or not cand.get("name"):
            continue
        out.append({"slot": label.get(slot, slot), "name": str(cand.get("name")),
                    "art": str(cand.get("article") or "—")})
    # опоры/каркас: если слот не разрешился по базе (напр. металлокаркас, count=0),
    # берём описание из hardware.legs — покупное изделие должно быть в BOM
    if not any(b["slot"] == "Опоры/ножки" for b in out):
        lg = (project.get("hardware") or {}).get("legs") or {}
        lt = str(lg.get("type") or "").strip()
        if lt and lt.lower() not in ("нет", "-", "—"):
            h = lg.get("height")
            out.append({"slot": "Опоры/ножки",
                        "name": f"{lt}" + (f", H={h} мм" if h else ""), "art": "—"})
    # штанги-вешала (AKD-177): покупная фурнитура из hardware.rods
    for rod in (project.get("hardware") or {}).get("rods") or []:
        kind = "Штанга выдвижная" if rod.get("axis") == "z" else "Штанга d25"
        name = f"{kind}, L={round(float(rod.get('length', 0)))} мм"
        art = "—"
        try:
            from .materials import search_base
            hit = search_base("штанга выдвижная" if rod.get("axis") == "z" else "штанга",
                              limit=1)
            if hit:
                art = str(hit[0].get("article") or "—")
        except Exception:
            pass
        out.append({"slot": "Штанга", "name": name, "art": art})
    # крепёж по присадкам (конфирматы/гвозди/саморезы/заглушки — реверс готовых
    # изделий БАЗИС, docs/BASIS_FASTENERS_REVERSE.md)
    try:
        from .hardware import compute_drilling, fastener_bom
        for f in fastener_bom(compute_drilling(project)):
            out.append({"slot": "Крепёж", "name": f["name"], "art": f"{f['qty']} шт"})
    except Exception:
        pass
    return out


def _panel_rows(project: dict[str, Any]) -> list[dict[str, Any]]:
    """Детали: группировка одинаковых (имя без индекса + размер + толщина) с количеством."""
    groups: dict[tuple, dict[str, Any]] = {}
    for p in project.get("panels", []):
        pl = p.get("placement")
        dm = p.get("dimensions") or {}
        w = dm.get("width")
        h = dm.get("height")
        if (w is None or h is None) and isinstance(pl, dict):
            w = round(pl["x2"] - pl["x1"], 1)
            h = round(pl["y2"] - pl["y1"], 1)
        t = p.get("thickness")
        base = "".join(ch for ch in str(p.get("name", "")) if not ch.isdigit()).strip()
        key = (base, w, h, t)
        g = groups.get(key)
        if g:
            g["count"] += 1
        else:
            groups[key] = {"name": base or p.get("name"), "w": w, "h": h,
                           "t": t, "material": p.get("material", ""), "count": 1}
    return list(groups.values())


def spec_summary(project: dict[str, Any]) -> dict[str, Any]:
    od = project.get("overall_dimensions") or {}
    m = project.get("materials") or {}
    color = (m.get("color") or "").strip()
    code = (m.get("color_code") or "").strip()
    decor = f"{color} ({code})" if code else (color or m.get("board_material", "ЛДСП"))
    holes = compute_drilling(project)
    return {
        "name": project.get("project_name") or project.get("furniture_type") or "Модель",
        "type": project.get("furniture_type", ""),
        "dims": {"w": od.get("width"), "d": od.get("depth"), "h": od.get("height")},
        "decor": decor, "thickness": m.get("board_thickness"),
        "back": m.get("back_wall_material", ""), "edge": m.get("edge_band_thickness"),
        "bom": _hardware_bom(project),
        "panels": _panel_rows(project),
        "n_panels": len(project.get("panels", [])),
        "drilling": sorted(drilling_summary(holes).items(), key=lambda kv: -kv[1]),
        "n_holes": len(holes),
    }


# ------------------------------------------------------------------ HTML листа

def build_approval_html(project: dict[str, Any], *, version: int, status: str,
                        created_iso: str) -> str:
    s = spec_summary(project)
    viewer = project_to_viewer_html(project)
    # вытащим тело просмотра (всё внутри <body>…</body>) для встраивания во фрейм srcdoc
    viewer_doc = viewer.replace('"', "&quot;")
    st = status if status in STATUS_RU else "draft"
    badge = (f'<span class="status" style="background:{STATUS_COLOR[st]}">'
             f'{STATUS_RU[st]}</span>')
    dims = s["dims"]
    dim_str = f'{dims["w"]}×{dims["d"]}×{dims["h"]} мм'

    bom_rows = "".join(
        f'<tr><td>{_e(b["slot"])}</td><td>{_e(b["name"])}</td><td class="art">{_e(b["art"])}</td></tr>'
        for b in s["bom"]) or '<tr><td colspan="3" class="muted">—</td></tr>'
    panel_rows = "".join(
        f'<tr><td>{_e(p["name"])}</td><td>{_e(p["w"])}×{_e(p["h"])}</td>'
        f'<td>{_e(p["t"])}</td><td>{_e(p["material"])}</td><td class="num">{p["count"]}</td></tr>'
        for p in s["panels"])
    drill_rows = "".join(
        f'<tr><td>{_e(k)}</td><td class="num">{v}</td></tr>' for k, v in s["drilling"]) \
        or '<tr><td colspan="2" class="muted">—</td></tr>'

    # технический чертёж (AKD-7/8); при сбое лист остаётся без карточки чертежа
    try:
        tv_svg, tv_issues = build_techview_svg(project)
        techview = tv_svg if not tv_issues else ""
    except Exception:
        techview = ""
    techview_card = (
        '<div class="card" style="margin-top:16px"><h2>Чертёж (фронт · бок)</h2>'
        f'<div class="body" style="overflow-x:auto">{techview}</div></div>') if techview else ""

    # смета материалов (C1) — карточка в листе согласования (C3)
    estimate_card = ""
    try:
        from .estimate import estimate_project
        est = estimate_project(project)
        if est["rows"]:
            er = "".join(
                f'<tr><td>{_e(r["group"])}</td><td>{_e(r["name"])}</td>'
                f'<td class="num">{r["qty"]} {_e(r["unit"])}</td>'
                f'<td class="num">{(str(r["cost"]) + " ₽") if r["cost"] is not None else "—"}</td></tr>'
                for r in est["rows"])
            estimate_card = (
                '<div class="card" style="margin-top:16px"><h2>Смета материалов '
                f'(закупка) — ≈{est["total"]:.0f} {est["currency"]}</h2>'
                '<div class="body" style="padding:0"><table><thead><tr><th>Группа</th>'
                '<th>Позиция</th><th class="num">Кол-во</th><th class="num">Стоимость</th>'
                f'</tr></thead><tbody>{er}</tbody></table></div></div>')
    except Exception:
        pass

    return _SHEET_TEMPLATE.format(
        name=_e(s["name"]), badge=badge, version=version, created=_e(created_iso),
        dim=_e(dim_str), decor=_e(s["decor"]), thickness=_e(s["thickness"]),
        back=_e(s["back"]), edge=_e(s["edge"]),
        n_panels=s["n_panels"], n_holes=s["n_holes"],
        bom_rows=bom_rows, panel_rows=panel_rows, drill_rows=drill_rows,
        viewer_srcdoc=viewer_doc, renderer=RENDERER_VERSION,
        techview_card=techview_card, estimate_card=estimate_card)


# ------------------------------------------------------------------ версии/снапшот

def create_delivery(spec: dict[str, Any], project: dict[str, Any], *, out_root: str | Path,
                    created_iso: str, version: int | None = None, status: str = "draft",
                    export: bool = True) -> dict[str, Any]:
    """Снапшот версии листа: spec.json + project.json + page.html + metadata.json (+ pdf/png).

    Возвращает {dir, page, pdf, png, version, status}."""
    name = project.get("project_name") or project.get("furniture_type") or "model"
    root = Path(out_root) / "deliveries" / _slug(name)
    root.mkdir(parents=True, exist_ok=True)
    if version is None:
        existing = [int(p.name[1:]) for p in root.glob("v*") if p.name[1:].isdigit()]
        version = (max(existing) + 1) if existing else 1
    vdir = root / f"v{version}"
    vdir.mkdir(parents=True, exist_ok=True)

    (vdir / "spec.json").write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
    (vdir / "project.json").write_text(json.dumps(project, ensure_ascii=False, indent=2), encoding="utf-8")
    page = vdir / "page.html"
    page.write_text(build_approval_html(project, version=version, status=status,
                                        created_iso=created_iso), encoding="utf-8")

    spec_hash = hashlib.sha256(json.dumps(spec, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    meta = {"project": name, "slug": _slug(name), "version": version, "status": status,
            "created": created_iso, "renderer_version": RENDERER_VERSION,
            "overall_dimensions": project.get("overall_dimensions"),
            "counts": {"panels": len(project.get("panels", [])),
                       "drilling": len(compute_drilling(project))},
            "spec_hash": spec_hash}
    (vdir / "metadata.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    pdf = png = None
    if export:
        pdf, png = export_pdf_png(page, vdir)
    return {"dir": str(vdir), "page": str(page), "pdf": str(pdf) if pdf else None,
            "png": str(png) if png else None, "version": version, "status": status,
            "metadata": meta}


# ------------------------------------------------------------------ headless экспорт

def _find_chrome() -> str | None:
    for c in ("chrome", "google-chrome", "chromium"):
        p = shutil.which(c)
        if p:
            return p
    for p in (r"C:\Program Files\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"):
        if Path(p).exists():
            return p
    return None


def export_pdf_png(page: Path, out_dir: Path) -> tuple[Path | None, Path | None]:
    """PDF + PNG листа. Сначала Playwright (система Chrome), иначе Chrome CLI headless.
    Best-effort: если экспорт недоступен, возвращает (None, None) — страница всё равно
    работает и печатается кнопкой в браузере."""
    page = Path(page)
    pdf, png = out_dir / "sheet.pdf", out_dir / "sheet.png"
    url = page.resolve().as_uri()
    # 1) Playwright с системным Chrome
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel="chrome", headless=True)
            pg = browser.new_page(viewport={"width": 1400, "height": 1000})
            pg.goto(url, wait_until="networkidle", timeout=30000)
            pg.wait_for_timeout(1800)     # дать three.js отрисоваться
            pg.pdf(path=str(pdf), format="A4", print_background=True,
                   margin={"top": "10mm", "bottom": "10mm", "left": "8mm", "right": "8mm"})
            pg.screenshot(path=str(png), full_page=True)
            browser.close()
        if pdf.exists() and png.exists():
            return pdf, png
    except Exception:
        pass
    # 2) Chrome CLI
    chrome = _find_chrome()
    if not chrome:
        return (pdf if pdf.exists() else None, png if png.exists() else None)
    try:
        subprocess.run([chrome, "--headless=new", "--no-pdf-header-footer",
                        "--virtual-time-budget=6000", f"--print-to-pdf={pdf}", url],
                       capture_output=True, timeout=60)
        subprocess.run([chrome, "--headless=new", "--hide-scrollbars",
                        "--window-size=1400,1600", "--virtual-time-budget=6000",
                        f"--screenshot={png}", url], capture_output=True, timeout=60)
    except Exception:
        pass
    return (pdf if pdf.exists() else None, png if png.exists() else None)


# ------------------------------------------------------------------ шаблон листа

_SHEET_TEMPLATE = r"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{name} — лист согласования v{version}</title>
<style>
  :root{{--ink:#1a1d21;--mut:#6b7280;--line:#e3e6ea;--bg:#f4f6f8;--card:#fff}}
  *{{box-sizing:border-box}}
  body{{margin:0;background:var(--bg);color:var(--ink);font-family:Segoe UI,Arial,sans-serif;font-size:14px}}
  .wrap{{max-width:1080px;margin:0 auto;padding:20px}}
  header{{display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin-bottom:6px}}
  header h1{{font-size:22px;margin:0}}
  .status{{color:#fff;font-size:12px;font-weight:600;padding:3px 10px;border-radius:20px}}
  .sub{{color:var(--mut);font-size:13px;margin-bottom:16px}}
  .bar{{display:flex;gap:8px;margin:0 0 16px}}
  .btn{{cursor:pointer;border:1px solid var(--line);background:var(--card);color:var(--ink);
        padding:7px 14px;border-radius:7px;font-size:13px;text-decoration:none}}
  .btn:hover{{background:#eef1f4}}
  .grid{{display:grid;grid-template-columns:1.15fr .85fr;gap:16px;align-items:start}}
  .card{{background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden}}
  .card h2{{font-size:13px;text-transform:uppercase;letter-spacing:.04em;color:var(--mut);
           margin:0;padding:11px 14px;border-bottom:1px solid var(--line)}}
  .card .body{{padding:12px 14px}}
  iframe#stage{{width:100%;height:460px;border:0;display:block;background:#eceff3}}
  table{{width:100%;border-collapse:collapse;font-size:13px}}
  th,td{{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line)}}
  th{{color:var(--mut);font-weight:600;font-size:12px}}
  td.num,td.art{{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}}
  .muted{{color:var(--mut)}}
  .kv{{display:grid;grid-template-columns:auto 1fr;gap:4px 14px}}
  .kv div:nth-child(odd){{color:var(--mut)}}
  .chips{{display:flex;gap:18px;flex-wrap:wrap;margin-top:4px}}
  .chip b{{font-size:18px}} .chip span{{color:var(--mut);font-size:12px;display:block}}
  footer{{color:var(--mut);font-size:12px;margin-top:18px;border-top:1px solid var(--line);padding-top:10px}}
  @media print{{
    body{{background:#fff}} .bar,.no-print{{display:none!important}}
    iframe#stage{{height:360px}} .grid{{grid-template-columns:1fr 1fr}}
    .card{{break-inside:avoid}}
  }}
</style></head><body>
<div class="wrap">
  <header><h1>{name}</h1> {badge}</header>
  <div class="sub">Лист согласования · версия v{version} · {created} · рендерер r{renderer}</div>
  <div class="bar no-print">
    <a class="btn" href="javascript:window.print()">🖨 Печать / PDF</a>
    <a class="btn" href="sheet.pdf" download>⬇ PDF</a>
    <a class="btn" href="sheet.png" download>⬇ PNG</a>
  </div>

  <div class="grid">
    <div class="card">
      <h2>3D-модель</h2>
      <iframe id="stage" srcdoc="{viewer_srcdoc}"></iframe>
    </div>
    <div class="card">
      <h2>Параметры</h2>
      <div class="body">
        <div class="kv">
          <div>Габарит</div><div><b>{dim}</b></div>
          <div>Декор плиты</div><div>{decor}</div>
          <div>Толщина плиты</div><div>{thickness} мм</div>
          <div>Задняя стенка</div><div>{back}</div>
          <div>Кромка</div><div>{edge} мм</div>
        </div>
        <div class="chips">
          <div class="chip"><b>{n_panels}</b><span>деталей</span></div>
          <div class="chip"><b>{n_holes}</b><span>присадок</span></div>
        </div>
      </div>
    </div>
  </div>

  {techview_card}
  {estimate_card}

  <div class="grid" style="margin-top:16px">
    <div class="card">
      <h2>Детали (раскрой)</h2>
      <div class="body" style="padding:0">
        <table><thead><tr><th>Деталь</th><th>Размер, мм</th><th>Толщ.</th><th>Материал</th><th class="num">Кол-во</th></tr></thead>
        <tbody>{panel_rows}</tbody></table>
      </div>
    </div>
    <div>
      <div class="card">
        <h2>Фурнитура (BOM)</h2>
        <div class="body" style="padding:0">
          <table><thead><tr><th>Тип</th><th>Наименование</th><th class="art">Артикул</th></tr></thead>
          <tbody>{bom_rows}</tbody></table>
        </div>
      </div>
      <div class="card" style="margin-top:16px">
        <h2>Присадки (ЧПУ)</h2>
        <div class="body" style="padding:0">
          <table><thead><tr><th>Назначение</th><th class="num">Отв.</th></tr></thead>
          <tbody>{drill_rows}</tbody></table>
        </div>
      </div>
    </div>
  </div>

  <footer>Источник правды — эта страница. Файл для БАЗИС/станка кодируется отдельно
  в родной конвенции. Статусы: черновик → на согласовании → согласовано → в производство.</footer>
</div>
</body></html>"""
