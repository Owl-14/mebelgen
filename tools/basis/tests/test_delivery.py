"""Веб-доставка (AKD-15): лист согласования, снапшот версии, воспроизводимость.

Экспорт PDF/PNG (headless Chrome) здесь не гоняем — проверяем страницу, спецификацию,
снапшот и повторяемость сборки из spec.json.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.delivery import build_approval_html, create_delivery, spec_summary   # noqa: E402
from src.generators import generate_from_paramspec                            # noqa: E402
from src.materials import resolve_project_materials                           # noqa: E402

SPEC = ROOT / "paramspecs" / "komi_46_shkaf_dokumenty.json"


def _project():
    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    pr = generate_from_paramspec(spec)
    try:
        pr["material_refs"] = resolve_project_materials(pr)
    except Exception:
        pass
    return spec, pr


def test_spec_summary_has_bom_and_drilling():
    _, pr = _project()
    s = spec_summary(pr)
    assert s["n_panels"] == len(pr["panels"])
    assert s["n_holes"] > 0 and s["drilling"]
    assert s["dims"]["w"] and s["dims"]["h"]
    assert any(b.get("art") for b in s["bom"])           # BOM с артикулами


def test_approval_html_contains_spec_and_viewer():
    _, pr = _project()
    html = build_approval_html(pr, version=1, status="review", created_iso="2026-01-01T00:00:00")
    assert html.startswith("<!DOCTYPE html>")
    assert "На согласовании" in html                     # статус-бейдж
    assert "Фурнитура (BOM)" in html and "Присадки" in html
    assert 'id="stage"' in html                          # встроенный 3D-фрейм


def test_create_delivery_snapshot_and_reproducible(tmp_path: Path):
    spec, pr = _project()
    r1 = create_delivery(spec, pr, out_root=tmp_path, created_iso="2026-01-01T00:00:00",
                         version=1, status="draft", export=False)
    vdir = Path(r1["dir"])
    for f in ("spec.json", "project.json", "page.html", "metadata.json"):
        assert (vdir / f).exists(), f
    meta = json.loads((vdir / "metadata.json").read_text(encoding="utf-8"))
    assert meta["version"] == 1 and meta["status"] == "draft"
    assert meta["counts"]["panels"] == len(pr["panels"]) and meta["counts"]["drilling"] > 0

    # воспроизводимость: та же spec+версия+дата → тот же лист
    page1 = (vdir / "page.html").read_text(encoding="utf-8")
    page2 = build_approval_html(pr, version=1, status="draft", created_iso="2026-01-01T00:00:00")
    assert page1 == page2

    # авто-инкремент версии
    r2 = create_delivery(spec, pr, out_root=tmp_path, created_iso="2026-01-01T00:00:00",
                         status="draft", export=False)
    assert r2["version"] == 2
