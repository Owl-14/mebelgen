"""BAZIS Studio: локальный редактор-предпросмотр единицы мебели (AKD-94…97).

Идея: «функционал БАЗИСа, который нам нужен» уже реализован в конвейере на Python
(генераторы, материалы, присадки, фурнитура, проверки). Studio отдаёт его в наш
3D-вьювер через локальный HTTP-сервер: правки параметров пересчитываются мгновенно
и БЕСПЛАТНО, а платная сборка .b3d через облако — только по явному подтверждению
и только когда все проверки зелёные.

Один источник правды: ничего не портируется в JS — браузер шлёт ParamSpec,
Python возвращает панели/присадки/фурнитуру/проверки/BOM/чертёж.

Запуск: python main.py studio paramspecs/komi_72_tumba_podkatnaya.json
"""

from __future__ import annotations

import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

# ------------------------------------------------------------------ формы архетипов
#
# Studio универсален: бэкенд собирает ЛЮБОЙ archetype через generate_from_paramspec.
# Здесь — декларация редактируемых параметров каждого архетипа для боковой панели
# (тип num|bool|select|text; пустое значение = удалить ключ → дефолт генератора).

ARCHETYPE_FIELDS: dict[str, list[dict[str, Any]]] = {
    "desk": [
        {"key": "frame", "label": "Каркас", "type": "select", "options": ["", "metal"],
         "hint": "metal = опоры-труба (фурнитура), панели только столешница+экран"},
        {"key": "apron", "label": "Царга", "type": "bool", "default": True},
        {"key": "apron_height", "label": "Царга H", "type": "num", "default": 120},
        {"key": "screen", "label": "Перед. экран", "type": "bool", "default": False,
         "hint": "только при metal-каркасе"},
        {"key": "screen_height", "label": "Экран H", "type": "num", "default": 500},
        {"key": "screen_thickness", "label": "Экран T", "type": "num", "default": 16},
        {"key": "screen_margin", "label": "Экран отступ", "type": "num", "default": 45},
        {"key": "screen_z", "label": "Экран Z", "type": "num", "default": 50},
    ],
    "round_table": [
        {"key": "top_thickness", "label": "Столешница T", "type": "num"},
        {"key": "pedestal_diameter", "label": "Пьедестал Ø", "type": "num"},
        {"key": "base", "label": "База-диск", "type": "bool", "default": False},
        {"key": "base_diameter", "label": "База Ø", "type": "num"},
        {"key": "base_thickness", "label": "База T", "type": "num"},
    ],
    "cabinet": [
        {"key": "facade_reveal", "label": "Свес фасада", "type": "num"},
        {"key": "socle_full", "label": "Цоколь глух.", "type": "bool", "default": False},
        {"key": "carcass_z_front", "label": "Корпус Z-перед", "type": "num"},
        {"key": "interior_z_front", "label": "Нутро Z-перед", "type": "num"},
    ],
    "drawer_unit": [
        {"key": "facade_reveal", "label": "Свес фасада", "type": "num"},
    ],
    "shelving": [], "door_unit": [], "corpus": [],
    "composite": [],   # blocks — через raw JSON
}
ARCHETYPE_FIELDS["table"] = ARCHETYPE_FIELDS["desk"]
ARCHETYPE_FIELDS["wardrobe"] = ARCHETYPE_FIELDS["cabinet"]

# У кого есть секции (панель «Секции» видна даже если их пока нет)
SECTION_ARCHETYPES = ["cabinet", "wardrobe", "shelving", "drawer_unit", "door_unit"]


# ------------------------------------------------------------------ payload

def build_payload(spec: dict[str, Any]) -> dict[str, Any]:
    """ParamSpec → всё для редактора: модель, проверки, BOM. Ошибки не бросают."""
    from .paramspec import validate_paramspec

    issues: dict[str, list[str]] = {"schema": [], "consistency": [], "geometry": [],
                                    "cfrn": [], "holes": [], "drilling": []}
    issues["schema"] = list(validate_paramspec(spec) or [])
    if issues["schema"]:
        return {"ok": False, "issues": issues}

    from .generators import generate_from_paramspec
    try:
        project = generate_from_paramspec(spec)
    except Exception as e:
        issues["schema"] = [f"генерация: {e}"]
        return {"ok": False, "issues": issues}
    try:
        from .materials import resolve_project_materials
        project["material_refs"] = resolve_project_materials(project)
    except Exception:
        pass

    from .consistency_check import check_consistency
    from .geometry_check import check_placement_geometry
    from .cfrn import check_cfrn_encoding, check_cfrn_holes
    issues["consistency"] = [f"{i.panel}: {i.message}" for i in check_consistency(project)
                             if i.severity == "error"]
    g = check_placement_geometry(project)
    if not g.get("ok", True):
        issues["geometry"] = [str(x) for x in g.get("issues", [])][:20] or ["пересечения панелей"]
    try:
        issues["cfrn"] = check_cfrn_encoding(project)[:20]
        issues["holes"] = check_cfrn_holes(project)[:20]
    except Exception as e:
        issues["cfrn"] = [f"кодирование: {e}"]
    try:
        from .drilling_check import check_drilling_geometry
        issues["drilling"] = check_drilling_geometry(project)["errors"][:20]
    except Exception as e:
        issues["drilling"] = [f"валидатор сверловки: {e}"]

    from .webviewer import viewer_payload
    from .delivery import _hardware_bom, spec_summary
    s = spec_summary(project)
    payload = {
        "ok": not any(issues.values()),
        "issues": issues,
        "viewer": viewer_payload(project),      # панели+присадки+фурнитура+открывашки
        "stats": {"n_panels": s["n_panels"], "n_holes": s["n_holes"],
                  "dims": s["dims"], "decor": s["decor"]},
        "bom": _hardware_bom(project),
        "refs": project.get("material_refs") or {},   # слоты фурнитуры для выбора (A4)
    }
    try:
        from .estimate import estimate_project
        payload["estimate"] = estimate_project(project)   # смета live (C1)
    except Exception as e:
        payload["estimate"] = {"rows": [], "total": 0, "currency": "₽",
                               "warnings": [f"смета: {e}"]}
    return payload


def techview_svg(spec: dict[str, Any], panel: str | None = None) -> dict[str, Any]:
    from .generators import generate_from_paramspec
    from .techview import build_panel_detail_svg, build_techview_svg
    try:
        project = generate_from_paramspec(spec)
        if panel:                                     # деталировка одной детали (B4)
            return {"svg": build_panel_detail_svg(project, panel), "issues": []}
        svg, tv_issues = build_techview_svg(project)
        return {"svg": svg, "issues": tv_issues}
    except Exception as e:
        return {"svg": "", "issues": [str(e)]}


# ------------------------------------------------------------------ каталог проектов (D1)

def _slugify(name: str) -> str:
    import re
    s = re.sub(r"[^\w\-]+", "_", name.lower().strip()).strip("_")
    return s[:60] or "model"


def _list_projects(spec_dir: Path) -> list[dict[str, Any]]:
    out = []
    for f in sorted(spec_dir.glob("*.json")):
        if f.name.endswith(".project.json"):
            continue
        try:
            s = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if s.get("schemaVersion") != "paramspec-v1":
            continue
        d = s.get("dimensions", {})
        out.append({"file": f.name,
                    "name": s.get("project_name", f.stem),
                    "archetype": s.get("archetype", "?"),
                    "dims": f'{d.get("width", "?")}×{d.get("depth", "?")}×{d.get("height", "?")}',
                    "decor": (s.get("materials") or {}).get("color", "")})
    return out


def _default_spec(archetype: str, name: str) -> dict[str, Any]:
    """Минимальная валидная спека нового изделия по архетипу."""
    dims = {"desk": (1200, 700, 750), "table": (1200, 700, 750),
            "round_table": (900, 900, 750), "wardrobe": (1200, 600, 2100),
            "cabinet": (800, 400, 2000)}.get(archetype, (800, 400, 720))
    spec: dict[str, Any] = {
        "schemaVersion": "paramspec-v1",
        "project_name": name,
        "furniture_type": archetype,
        "archetype": archetype,
        "dimensions": {"width": dims[0], "depth": dims[1], "height": dims[2],
                       "tolerance": 5},
        "materials": {"board_thickness": 16, "board_material": "ЛДСП",
                      "edge_band_thickness": 2, "color": "Белый", "color_code": ""},
        "legs": {"type": "нет", "height": 0},
        "warnings": [], "estimated_values": [],
    }
    if archetype in ("desk", "table"):
        spec["materials"]["board_thickness"] = 25
        spec["apron"] = True
    elif archetype in ("cabinet", "wardrobe", "shelving", "drawer_unit", "door_unit"):
        spec["sections"] = [{"kind": "shelves", "shelves": 3}]
    return spec


def _safe_spec_file(spec_dir: Path, fname: str) -> Path:
    p = (spec_dir / Path(fname).name).resolve()
    if p.parent != spec_dir.resolve() or p.suffix != ".json":
        raise ValueError("файл вне каталога спек")
    return p


# ------------------------------------------------------------------ версии (D2)

def _versions_file(spec_path: Path) -> Path:
    return spec_path.with_suffix(".versions.json")


def _read_versions(spec_path: Path) -> list[dict[str, Any]]:
    try:
        return json.loads(_versions_file(spec_path).read_text(encoding="utf-8"))
    except Exception:
        return []


def _snapshot_version(spec_path: Path, spec: dict[str, Any], keep: int = 30) -> None:
    from datetime import datetime
    vs = _read_versions(spec_path)
    if vs and vs[-1]["spec"] == spec:                 # без дублей подряд
        return
    vs.append({"ts": datetime.now().isoformat(timespec="seconds"), "spec": spec})
    _versions_file(spec_path).write_text(
        json.dumps(vs[-keep:], ensure_ascii=False), encoding="utf-8")


def _list_versions(spec_path: Path) -> list[dict[str, Any]]:
    out = []
    for i, v in enumerate(_read_versions(spec_path)):
        d = (v["spec"].get("dimensions") or {})
        out.append({"index": i, "ts": v["ts"],
                    "dims": f'{d.get("width")}×{d.get("depth")}×{d.get("height")}',
                    "n_overrides": len(v["spec"].get("overrides") or [])})
    return out[::-1]                                  # свежие сверху


# ------------------------------------------------------------------ экспорт-центр (C3)

B3D_COST_RUB = 10          # цена облачной конвертации CfrnToB3d


def _read_builds(out_dir: Path) -> dict[str, Any]:
    f = out_dir / "builds.json"
    try:
        builds = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        builds = []
    return {"builds": builds[-20:][::-1],              # свежие сверху
            "total_spent": sum(b.get("cost_rub", 0) for b in builds)}


def _verify_parity(spec: dict[str, Any], b3d: Path) -> dict[str, Any] | None:
    """Бесплатная сверка собранного .b3d со Studio-моделью (AKD-169)."""
    try:
        from .b3d_verify import verify_b3d_parity
        from .generators import generate_from_paramspec
        from .materials import resolve_project_materials
        project = generate_from_paramspec(spec)
        try:
            project["material_refs"] = resolve_project_materials(project)
        except Exception:
            pass
        return verify_b3d_parity(b3d, project)
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def _log_build(out_dir: Path, spec: dict[str, Any], b3d: Path,
               parity: dict[str, Any] | None = None) -> None:
    from datetime import datetime
    f = out_dir / "builds.json"
    try:
        builds = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        builds = []
    d = spec.get("dimensions", {})
    builds.append({"ts": datetime.now().isoformat(timespec="seconds"),
                   "file": str(b3d), "project": spec.get("project_name", ""),
                   "dims": f'{d.get("width")}×{d.get("depth")}×{d.get("height")}',
                   "cost_rub": B3D_COST_RUB,
                   "parity": bool(parity and parity.get("ok"))})
    f.write_text(json.dumps(builds, ensure_ascii=False, indent=1), encoding="utf-8")


def _open_file(out_dir: Path, path: str) -> dict[str, Any]:
    """Открыть файл из каталога результатов: .b3d — БАЗИС-Просмотр, прочее — ОС."""
    import os
    import subprocess
    p = Path(path).resolve()
    roots = (out_dir.resolve(), Path.cwd().resolve())
    if not any(str(p).startswith(str(r)) for r in roots) or not p.is_file():
        return {"ok": False, "error": "файл вне каталога результатов или не существует"}
    viewer = Path(os.environ.get("BAZIS_VIEWER", r"D:\bazis\viewer.exe"))
    try:
        if p.suffix.lower() == ".b3d" and viewer.is_file():
            subprocess.Popen([str(viewer), str(p)])
        else:
            os.startfile(str(p))                       # noqa: S606 — локальный запуск по клику
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


# ------------------------------------------------------------------ server

class _Studio:
    def __init__(self, spec_path: Path, out_dir: Path):
        self.spec_path = spec_path
        self.out_dir = out_dir
        self.spec = json.loads(spec_path.read_text(encoding="utf-8"))


def make_handler(st: _Studio):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):                       # тихий сервер
            pass

        def _send(self, code: int, body: bytes, ctype: str = "application/json; charset=utf-8"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj: Any, code: int = 200):
            self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

        def _body(self) -> dict[str, Any]:
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n).decode("utf-8")) if n else {}

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                from .webviewer import SCENE_JS
                page = (PAGE
                        .replace("__SCENE_JS__", SCENE_JS)
                        .replace("__FIELDS__", json.dumps(ARCHETYPE_FIELDS, ensure_ascii=False))
                        .replace("__SECTION_ARCHS__", json.dumps(SECTION_ARCHETYPES))
                        .replace("__SPEC__", json.dumps(st.spec, ensure_ascii=False)
                                 .replace("</", "<\\/")))
                self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")
            else:
                self._send(404, b"{}")

        def do_POST(self):
            try:
                body = self._body()
                spec = body.get("spec") or {}
                if self.path == "/api/generate":
                    self._json(build_payload(spec))
                elif self.path == "/api/techview":
                    self._json(techview_svg(spec, body.get("panel")))
                elif self.path == "/api/chat":
                    from .spec_chat import chat_edit
                    self._json(chat_edit(spec, str(body.get("message", "")),
                                         body.get("history") or [],
                                         body.get("context") or None))
                elif self.path == "/api/versions":    # версии спеки (D2)
                    self._json({"versions": _list_versions(st.spec_path)})
                elif self.path == "/api/restore":     # восстановить версию (D2)
                    idx = int(body.get("index", -1))
                    vs = _read_versions(st.spec_path)
                    if 0 <= idx < len(vs):
                        st.spec = vs[idx]["spec"]
                        self._json({"ok": True, "spec": st.spec,
                                    "ts": vs[idx]["ts"]})
                    else:
                        self._json({"ok": False, "error": "нет такой версии"}, 404)
                elif self.path == "/api/projects":    # каталог спек (D1)
                    self._json({"projects": _list_projects(st.spec_path.parent),
                                "current": st.spec_path.name})
                elif self.path == "/api/open":        # открыть другую спеку (D1)
                    p = _safe_spec_file(st.spec_path.parent, str(body.get("file", "")))
                    st.spec = json.loads(p.read_text(encoding="utf-8"))
                    st.spec_path = p
                    self._json({"ok": True, "spec": st.spec, "file": p.name})
                elif self.path == "/api/new":         # новое изделие (D1)
                    arch = str(body.get("archetype", "cabinet"))
                    name = str(body.get("name") or f"Новое изделие ({arch})")
                    new_spec = _default_spec(arch, name)
                    p = st.spec_path.parent / f"{_slugify(name)}.json"
                    i = 2
                    while p.exists():
                        p = st.spec_path.parent / f"{_slugify(name)}_{i}.json"
                        i += 1
                    p.write_text(json.dumps(new_spec, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
                    st.spec, st.spec_path = new_spec, p
                    self._json({"ok": True, "spec": new_spec, "file": p.name})
                elif self.path == "/api/duplicate":   # дубликат текущего (D1)
                    dup = json.loads(json.dumps(spec or st.spec))
                    dup["project_name"] = str(dup.get("project_name", "модель")) + " (копия)"
                    p = st.spec_path.parent / f"{st.spec_path.stem}_copy.json"
                    i = 2
                    while p.exists():
                        p = st.spec_path.parent / f"{st.spec_path.stem}_copy{i}.json"
                        i += 1
                    p.write_text(json.dumps(dup, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
                    st.spec, st.spec_path = dup, p
                    self._json({"ok": True, "spec": dup, "file": p.name})
                elif self.path == "/api/nesting":     # раскрой-превью (C2)
                    from .generators import generate_from_paramspec
                    from .nesting import nesting_svg
                    try:
                        self._json({"svg": nesting_svg(generate_from_paramspec(spec))})
                    except Exception as e:
                        self._json({"svg": "", "error": str(e)[:200]})
                elif self.path == "/api/decors":
                    from .materials import list_sheet_decors
                    th = body.get("thickness")
                    self._json({"items": list_sheet_decors(
                        str(body.get("q", "")),
                        thickness=float(th) if th else None,
                        limit=int(body.get("limit", 30)))})
                elif self.path == "/api/save":
                    st.spec = spec
                    st.spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2),
                                            encoding="utf-8")
                    _snapshot_version(st.spec_path, spec)          # версия (D2)
                    from .generators import generate_from_paramspec
                    project = generate_from_paramspec(spec)
                    out = st.out_dir / (st.spec_path.stem + ".project.json")
                    out.write_text(json.dumps(project, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
                    self._json({"ok": True, "spec": str(st.spec_path), "project": str(out)})
                elif self.path == "/api/export-cfrn":
                    from .generators import generate_from_paramspec
                    from .materials import resolve_project_materials
                    from .cfrn import project_to_cfrn_bytes
                    project = generate_from_paramspec(spec)
                    try:
                        project["material_refs"] = resolve_project_materials(project)
                    except Exception:
                        pass
                    out = st.out_dir / (st.spec_path.stem + ".cfrn")
                    out.write_bytes(project_to_cfrn_bytes(project))
                    self._json({"ok": True, "cfrn": str(out)})
                elif self.path == "/api/build-b3d":
                    payload = build_payload(spec)
                    if not payload["ok"]:                # деньги — только на зелёную модель
                        self._json({"ok": False, "error": "проверки не пройдены",
                                    "issues": payload["issues"]}, 409)
                        return
                    from .build_b3d import build_b3d_from_paramspec
                    out = st.out_dir / (st.spec_path.stem + ".b3d")
                    try:
                        rep = build_b3d_from_paramspec(spec, out)
                        parity = _verify_parity(spec, out)         # паритет ✓ (AKD-169)
                        _log_build(st.out_dir, spec, out, parity)  # история сборок (C3)
                        self._json({"ok": True, "parity": parity,
                                    **{k: str(v) for k, v in rep.items()}})
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)[:300]}, 502)
                elif self.path == "/api/builds":         # история сборок .b3d (C3)
                    self._json(_read_builds(st.out_dir))
                elif self.path == "/api/open-file":      # открыть результат (C3)
                    self._json(_open_file(st.out_dir, str(body.get("path", ""))))
                elif self.path == "/api/deliver":        # лист согласования (C3)
                    from datetime import datetime
                    from .generators import generate_from_paramspec
                    from .materials import resolve_project_materials
                    from .delivery import create_delivery
                    project = generate_from_paramspec(spec)
                    try:
                        project["material_refs"] = resolve_project_materials(project)
                    except Exception:
                        pass
                    res = create_delivery(spec, project, out_root=st.out_dir,
                                          created_iso=datetime.now().isoformat(timespec="seconds"),
                                          export=False)
                    import webbrowser
                    webbrowser.open(Path(res["page"]).resolve().as_uri())
                    self._json({"ok": True, "page": res["page"],
                                "version": res["version"]})
                else:
                    self._send(404, b"{}")
            except Exception as e:
                self._json({"ok": False, "error": str(e)[:300]}, 500)

    return Handler


def run_studio(spec_path: str | Path, *, port: int = 8765, out_dir: str | Path | None = None,
               open_browser: bool = True) -> None:
    spec_path = Path(spec_path)
    out = Path(out_dir) if out_dir else spec_path.parent
    st = _Studio(spec_path, out)
    srv = ThreadingHTTPServer(("127.0.0.1", port), make_handler(st))
    url = f"http://127.0.0.1:{port}/"
    print(f"BAZIS Studio: {url}  (спека: {spec_path.name}; Ctrl+C — стоп)")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


# ------------------------------------------------------------------ страница

PAGE = r"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8"><title>BAZIS Studio — предпросмотр и правки</title>
<style>
  :root{--ink:#1a1d21;--mut:#6b7280;--line:#dfe3e8;--bg:#f4f6f8;--card:#fff;
        --ok:#2fa84f;--bad:#e5484d;--accent:#3b82f6}
  *{box-sizing:border-box} html,body{margin:0;height:100%;font-family:Segoe UI,Arial,sans-serif;
    background:var(--bg);color:var(--ink);font-size:13px;overflow:hidden}
  #app{display:grid;grid-template-columns:340px 1fr;height:100%}
  #side{background:var(--card);border-right:1px solid var(--line);overflow-y:auto;padding:12px}
  #side h1{font-size:15px;margin:2px 0 10px}
  fieldset{border:1px solid var(--line);border-radius:8px;margin:0 0 10px;padding:8px 10px}
  legend{font-size:11px;text-transform:uppercase;color:var(--mut);padding:0 4px}
  .row{display:flex;gap:6px;align-items:center;margin:4px 0}
  .row label{flex:0 0 92px;color:var(--mut)}
  input[type=number],input[type=text],select{width:100%;padding:4px 6px;border:1px solid var(--line);
    border-radius:6px;font-size:13px}
  input[type=number]{max-width:86px}
  .sec{border:1px dashed var(--line);border-radius:6px;padding:6px;margin:6px 0}
  .sec .row label{flex-basis:70px}
  .mini{font-size:11px;color:var(--mut)}
  button{cursor:pointer;border:1px solid var(--line);background:#fff;border-radius:6px;
         padding:6px 10px;font-size:12.5px}
  button:hover{background:#eef1f4} button:disabled{opacity:.45;cursor:not-allowed}
  button.primary{background:var(--accent);border-color:var(--accent);color:#fff}
  .badges{display:flex;flex-wrap:wrap;gap:5px}
  .badge{font-size:11px;padding:2px 8px;border-radius:12px;color:#fff;background:var(--ok)}
  .badge.bad{background:var(--bad)}
  #errors{color:var(--bad);font-size:12px;white-space:pre-wrap;max-height:130px;overflow:auto;margin-top:6px}
  #stats{display:flex;gap:14px;margin:8px 0}
  #stats b{font-size:16px} #stats span{display:block;font-size:11px;color:var(--mut)}
  #main{position:relative}
  #stage{position:absolute;inset:0}
  #tabs{position:absolute;top:10px;left:12px;z-index:5;display:flex;gap:6px}
  #tabs button.on{background:var(--accent);color:#fff;border-color:var(--accent)}
  #draw{position:absolute;inset:48px 12px 12px;z-index:4;background:#fff;border:1px solid var(--line);
        border-radius:8px;overflow:auto;display:none;padding:8px}
  #view3d{position:absolute;inset:0}
  #hud{position:absolute;right:12px;top:10px;z-index:5;background:rgba(255,255,255,.9);
       border:1px solid var(--line);border-radius:8px;padding:6px 10px;font-size:12px}
  #toast{position:absolute;left:50%;bottom:14px;transform:translateX(-50%);z-index:9;
         background:#1a1d21;color:#fff;padding:7px 14px;border-radius:8px;font-size:12.5px;
         opacity:0;transition:opacity .25s;pointer-events:none;max-width:80%}
  details{margin-top:8px} textarea{width:100%;height:170px;font:11px/1.4 Consolas,monospace}
  #bom table{width:100%;border-collapse:collapse;font-size:11.5px}
  #bom td{border-bottom:1px solid var(--line);padding:3px 4px}
  #estTable{width:100%;border-collapse:collapse;font-size:11px}
  #estTable td{border-bottom:1px solid var(--line);padding:2px 3px;vertical-align:top}
  #estTable td:last-child{text-align:right;white-space:nowrap}
  #estTable tr.grp td{background:var(--bg);font-weight:600;color:var(--mut);
                      text-transform:uppercase;font-size:10px}
  .swatch{flex:0 0 18px;height:18px;border-radius:4px;border:1px solid var(--line);
          background:#c9a06a}
  #partCard{font-size:12px;line-height:1.6}
  #partCard b{font-size:13px}
  #partCard .kv{display:grid;grid-template-columns:96px 1fr;gap:0 8px}
  #partCard .kv span:nth-child(odd){color:var(--mut)}
  #draw rect.sel{stroke:#2b62c4 !important;stroke-width:2.4 !important;
                 fill:#2b62c433 !important}
  #decorList{max-height:170px;overflow-y:auto;display:none;flex-direction:column;gap:2px;
             border:1px solid var(--line);border-radius:6px;padding:3px;margin-top:4px}
  .ditem{display:flex;align-items:center;gap:6px;padding:3px 5px;border-radius:5px;
         cursor:pointer;font-size:11.5px}
  .ditem:hover{background:var(--bg)}
  .ditem .sw{flex:0 0 16px;height:16px;border-radius:3px;border:1px solid var(--line)}
  .ditem .nm{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .ditem .fb{flex:0 0 auto;font-size:10px;padding:1px 6px}
  #chatlog{max-height:190px;overflow-y:auto;display:flex;flex-direction:column;gap:5px;
           margin-bottom:6px}
  .cmsg{border-radius:8px;padding:5px 8px;font-size:12px;white-space:pre-wrap;max-width:95%}
  .cmsg.user{background:var(--accent);color:#fff;align-self:flex-end}
  .cmsg.ai{background:var(--bg);border:1px solid var(--line);align-self:flex-start}
  .cmsg .diff{display:block;margin-top:4px;font:10.5px/1.5 Consolas,monospace;color:var(--mut)}
</style></head><body>
<div id="app">
<div id="side">
  <h1>BAZIS Studio <span class="mini">— правки до платной сборки</span></h1>

  <fieldset><legend>Проект</legend>
    <div class="row"><label>Изделие</label><select id="projSel"></select></div>
    <div class="row" style="gap:6px">
      <button id="projNew">+ Новое</button>
      <button id="projDup">Дублировать</button>
    </div>
  </fieldset>

  <div class="badges" id="badges"></div>
  <div id="errors"></div>
  <div id="stats"></div>

  <fieldset id="fs_part" style="display:none"><legend>Деталь <span class="mini">(клик в 3D)</span></legend>
    <div id="partCard"></div>
  </fieldset>

  <fieldset id="fs_chat"><legend>Чат с ИИ</legend>
    <div id="chatlog"></div>
    <div class="row" style="gap:6px">
      <input type="text" id="chatMsg" placeholder="напр.: сделай глубину 600, цвет дуб вотан">
      <button id="chatSend" title="отправить">➤</button>
    </div>
    <div class="row" style="gap:6px;margin-top:2px">
      <button id="btnUndo" disabled>⟲ Откатить</button>
      <span class="mini">правки применяются к модели сразу</span>
    </div>
  </fieldset>

  <fieldset><legend>Габариты, мм</legend>
    <div class="row"><label>Ширина</label><input type="number" id="f_w" step="10"></div>
    <div class="row"><label>Глубина</label><input type="number" id="f_d" step="10"></div>
    <div class="row"><label>Высота</label><input type="number" id="f_h" step="10"></div>
  </fieldset>

  <fieldset><legend>Материал</legend>
    <div class="row"><label>Цвет</label><input type="text" id="f_color">
      <span class="swatch" id="swCarcass" title="цвет показа корпуса"></span></div>
    <div class="row"><label>Цвет фасадов</label><input type="text" id="f_facade_color"
      placeholder="как корпус"><span class="swatch" id="swFacade" title="цвет показа фасадов"></span></div>
    <div class="row"><label>Код</label><input type="text" id="f_code"></div>
    <div class="row"><label>Плита, мм</label><input type="number" id="f_t" step="1"></div>
    <div class="row"><label>Из базы</label><input type="text" id="decorQ"
      placeholder="поиск декора: дуб, белый, 16…"></div>
    <div id="decorList"></div>
    <div class="mini" id="decorHint">клик — корпус, кнопка «Ф» — фасады (база: ≈960 листовых)</div>
  </fieldset>

  <fieldset><legend>Опоры / зазор</legend>
    <div class="row"><label>Опоры, мм</label><input type="number" id="f_legs" step="1"></div>
    <div class="row"><label>Зазор, мм</label><input type="number" id="f_gap" step="0.5"></div>
  </fieldset>

  <fieldset><legend>Архетип</legend>
    <div class="row"><label>Тип</label><select id="archSel"></select></div>
  </fieldset>

  <fieldset id="fs_arch" style="display:none"><legend>Параметры архетипа</legend>
    <div id="archFields"></div>
  </fieldset>

  <fieldset id="fs_sections"><legend>Секции</legend>
    <div id="sections"></div>
    <button id="addSec">+ секция</button>
  </fieldset>

  <fieldset><legend>Экспорт</legend>
    <div class="row" style="gap:6px">
      <button id="btnSave">Сохранить</button>
      <button id="btnCfrn">.cfrn</button>
      <button id="btnB3d" class="primary">Собрать .b3d (~10₽)</button>
    </div>
    <div class="row" style="gap:6px;margin-top:4px">
      <button id="btnDeliver">Лист согласования</button>
    </div>
    <div class="mini">Платная сборка доступна только при зелёных проверках.</div>
    <div class="row" style="gap:6px;margin-top:6px">
      <select id="verSel" style="flex:1"><option value="">— версии (при сохранении) —</option></select>
      <button id="verRestore" title="восстановить выбранную версию">⤺</button>
    </div>
    <div id="builds" style="margin-top:6px"></div>
  </fieldset>

  <fieldset id="fs_hw"><legend>Фурнитура <span class="mini" id="hwBadge"></span></legend>
    <div id="hwSlots"></div>
    <div class="row"><label>Ручка: межцентр.</label><input type="number" id="f_hsize" step="32" min="0"></div>
    <div class="row"><label>Отступ сверху</label><input type="number" id="f_hoff" step="5" min="0"></div>
  </fieldset>

  <fieldset id="fs_est"><legend>Смета материалов <span class="mini">(закупка, не продажа)</span></legend>
    <div id="estTotal" style="font-size:16px;font-weight:600;margin:2px 0 6px"></div>
    <table id="estTable"></table>
  </fieldset>

  <fieldset id="bom"><legend>BOM (фурнитура)</legend><table></table></fieldset>

  <details><summary class="mini">ParamSpec (raw JSON)</summary>
    <textarea id="rawspec" spellcheck="false"></textarea>
    <button id="applyRaw">Применить JSON</button>
  </details>
</div>

<div id="main">
  <div id="view3d"></div>
  <div id="tabs">
    <button id="tab3d" class="on">3D</button>
    <button id="tabDraw">Чертёж</button>
    <button id="tabNest">Раскрой</button>
    <button id="btnPrint" title="печать открытого чертежа/раскроя">⎙</button>
  </div>
  <div id="hud">
    <label><input type="checkbox" id="cbHoles" checked> присадки</label>
    <label><input type="checkbox" id="cbHw" checked> фурнитура</label>
    <label><input type="checkbox" id="cbTex" checked> текстура</label>
    <label><input type="checkbox" id="cbDims" checked> размеры</label>
    <label><input type="checkbox" id="cbXray"> прозрачный</label>
    <button id="btnOpenAll">Открыть всё</button>
    <button id="btnCloseAll">Закрыть</button>
    <label title="разнесённый вид">разбор
      <input type="range" id="explode" min="0" max="100" value="0" style="width:90px;vertical-align:middle"></label>
  </div>
  <div id="draw"></div>
  <div id="toast"></div>
</div>
</div>

<script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/three@0.128.0/examples/js/controls/OrbitControls.js"></script>
<script>
__SCENE_JS__
let SPEC = __SPEC__;
const FIELDS = __FIELDS__;                 // archetype -> [{key,label,type,...}]
const SECTION_ARCHS = __SECTION_ARCHS__;   // архетипы с секциями
const $ = id => document.getElementById(id);
const toast = (m,bad)=>{const t=$('toast');t.textContent=m;t.style.background=bad?'#b3261e':'#1a1d21';
  t.style.opacity=1;clearTimeout(t._h);t._h=setTimeout(()=>t.style.opacity=0,2600);};

/* ---------- 3D: общий движок MebelScene (как webviewer, + анимация открытия) ---------- */
const view=$('view3d');
const scene3d=MebelScene(view);
function rebuild(v){scene3d.setPayload(v);
  scene3d.setHoles($('cbHoles').checked);
  scene3d.setHw($('cbHw').checked);
  if($('cbXray').checked) scene3d.setXray(true);
  if(scene3d.select) scene3d.select(null);      // модель пересобрана — выбор сброшен
}
function resize(){scene3d.resize();}
$('cbHoles').onchange=e=>scene3d.setHoles(e.target.checked);
$('cbHw').onchange=e=>scene3d.setHw(e.target.checked);
$('cbTex').onchange=e=>scene3d.setTextures(e.target.checked);
$('cbDims').onchange=e=>scene3d.setDims(e.target.checked);
$('cbXray').onchange=e=>scene3d.setXray(e.target.checked);
$('explode').oninput=e=>scene3d.setExplode(e.target.value/100);
$('btnOpenAll').onclick=()=>scene3d.openAll();
$('btnCloseAll').onclick=()=>scene3d.closeAll();

/* ---------- формы ← spec ---------- */
function fillForm(){
  const d=SPEC.dimensions||{},m=SPEC.materials||{},lg=SPEC.legs||{},gp=SPEC.gaps||{};
  $('f_w').value=d.width??'';$('f_d').value=d.depth??'';$('f_h').value=d.height??'';
  $('f_color').value=m.color??'';$('f_code').value=m.color_code??'';
  $('f_facade_color').value=m.facade_color??'';
  $('f_t').value=m.board_thickness??16;$('f_legs').value=lg.height??0;
  $('f_gap').value=gp.default??2;
  $('rawspec').value=JSON.stringify(SPEC,null,2);
  renderArchetype();
  renderSections();
}
function renderArchetype(){
  const sel=$('archSel');
  sel.innerHTML=Object.keys(FIELDS).sort()
    .map(a=>`<option ${SPEC.archetype===a?'selected':''}>${a}</option>`).join('');
  const defs=FIELDS[SPEC.archetype]||[];
  const box=$('archFields'); box.innerHTML='';
  $('fs_arch').style.display=defs.length?'':'none';
  defs.forEach(f=>{
    const v=SPEC[f.key], row=document.createElement('div'); row.className='row';
    let inp;
    if(f.type==='bool'){
      const on=(v===undefined)?(f.default===true):!!v;
      inp=`<input type="checkbox" data-ak="${f.key}" ${on?'checked':''}>`;
    }else if(f.type==='select'){
      inp=`<select data-ak="${f.key}">${(f.options||[]).map(o=>
        `<option value="${o}" ${String(v??'')===o?'selected':''}>${o||'—'}</option>`).join('')}</select>`;
    }else{
      const ph=f.default!==undefined?` placeholder="${f.default}"`:'';
      inp=`<input type="${f.type==='num'?'number':'text'}" data-ak="${f.key}" value="${v??''}"${ph}>`;
    }
    row.innerHTML=`<label title="${f.hint||''}">${f.label}</label>${inp}`;
    box.appendChild(row);
  });
}
$('archSel').addEventListener('change',()=>{
  pushUndo();                                  // смена архетипа — структурная правка
  SPEC.archetype=$('archSel').value;
  if(SECTION_ARCHS.includes(SPEC.archetype)&&!Array.isArray(SPEC.sections))
    SPEC.sections=[{kind:'shelves',shelves:2}];
  fillForm(); apply();
});
function renderSections(){
  const box=$('sections'); box.innerHTML='';
  const supported=SECTION_ARCHS.includes(SPEC.archetype);
  if(!supported){$('fs_sections').style.display='none';return;}
  $('fs_sections').style.display='';
  const secs=SPEC.sections||[];
  secs.forEach((s,i)=>{
    const div=document.createElement('div'); div.className='sec';
    div.innerHTML=`
      <div class="row"><label>Тип</label>
        <select data-i="${i}" data-k="kind">
          ${['drawers','shelves','door','open'].map(k=>`<option ${s.kind===k?'selected':''}>${k}</option>`).join('')}
        </select>
        <button data-del="${i}" title="удалить">✕</button></div>
      <div class="row"><label>Ящиков</label><input type="number" min="0" data-i="${i}" data-k="drawers" value="${s.drawers??''}"></div>
      <div class="row"><label>Полок</label><input type="number" min="0" data-i="${i}" data-k="shelves" value="${s.shelves??''}"></div>
      <div class="row"><label>Дверей</label><input type="number" min="0" max="2" data-i="${i}" data-k="door" value="${s.door??''}"></div>
      <div class="row"><label>Доля шир.</label><input type="number" step="0.1" data-i="${i}" data-k="width_share" value="${s.width_share??''}"></div>
      <div class="row"><label title="высоты фасадов ящиков сверху вниз, через запятую">Высоты ящ.</label>
        <input type="text" data-i="${i}" data-k="drawer_heights" placeholder="напр. 180,180,240"
          value="${(s.drawer_heights||[]).join(',')}"></div>
      <div class="row"><label title="уровни полок (Y от пола), через запятую">Уровни полок</label>
        <input type="text" data-i="${i}" data-k="shelf_levels" placeholder="напр. 400,800,1200"
          value="${(s.shelf_levels||[]).join(',')}"></div>
      <div class="row"><label title="низ фасадной зоны (Y) — ниша снизу">Ниша снизу до</label>
        <input type="number" data-i="${i}" data-k="front_bottom" value="${s.front_bottom??''}"></div>
      <div class="row"><label title="верх фасадной зоны (Y) — ниша сверху">Ниша сверху от</label>
        <input type="number" data-i="${i}" data-k="front_top" value="${s.front_top??''}"></div>
      <div class="mini" data-sum="${i}"></div>`;
    box.appendChild(div);
  });
  updateSectionSums();
}
function updateSectionSums(){
  (SPEC.sections||[]).forEach((s,i)=>{
    const el=document.querySelector(`[data-sum="${i}"]`);
    if(!el) return;
    const hs=s.drawer_heights||[];
    if(hs.length){
      const sum=hs.reduce((a,b)=>a+Number(b||0),0);
      const H=(SPEC.dimensions||{}).height||0;
      el.textContent=`Σ высот ящиков: ${sum} мм из ~${H}`;
      el.style.color=sum>H?'var(--bad)':'var(--mut)';
    } else el.textContent='';
  });
}
$('addSec').onclick=()=>{(SPEC.sections=SPEC.sections||[]).push({kind:'shelves',shelves:2});
  fillForm(); apply();};

/* spec ← формы */
function harvest(){
  SPEC.dimensions=SPEC.dimensions||{};SPEC.materials=SPEC.materials||{};
  SPEC.legs=SPEC.legs||{};SPEC.gaps=SPEC.gaps||{};
  const num=v=>v===''?undefined:Number(v);
  SPEC.dimensions.width=num($('f_w').value);SPEC.dimensions.depth=num($('f_d').value);
  SPEC.dimensions.height=num($('f_h').value);
  SPEC.materials.color=$('f_color').value;SPEC.materials.color_code=$('f_code').value;
  const fc=$('f_facade_color').value.trim();
  if(fc) SPEC.materials.facade_color=fc;
  else {delete SPEC.materials.facade_color; delete SPEC.materials.facade_article;}
  SPEC.materials.board_thickness=num($('f_t').value);
  SPEC.legs.height=num($('f_legs').value)||0;
  SPEC.gaps.default=num($('f_gap').value);
  const hs=num($('f_hsize').value), ho=num($('f_hoff').value);   // позиции ручек (A4)
  if(hs!==undefined||ho!==undefined){
    SPEC.hardware=SPEC.hardware||{};
    const hh=SPEC.hardware.handles=SPEC.hardware.handles||{};
    if(hs!==undefined) hh.size=hs; else delete hh.size;
    if(ho!==undefined) hh.offset_from_top=ho; else delete hh.offset_from_top;
  }
  $('rawspec').value=JSON.stringify(SPEC,null,2);
}
document.addEventListener('input',e=>{
  const t=e.target;
  if(t.dataset&&t.dataset.ak){                       // параметр архетипа
    const def=(FIELDS[SPEC.archetype]||[]).find(f=>f.key===t.dataset.ak);
    let v;
    if(t.type==='checkbox') v=t.checked;
    else if(t.value==='') v=undefined;
    else v=(def&&def.type==='num')?Number(t.value):t.value;
    if(v===undefined) delete SPEC[t.dataset.ak]; else SPEC[t.dataset.ak]=v;
    $('rawspec').value=JSON.stringify(SPEC,null,2);
    schedule(); return;
  }
  if(t.dataset&&t.dataset.k!==undefined&&t.dataset.i!==undefined){
    const s=SPEC.sections[+t.dataset.i], k=t.dataset.k;
    let v;
    if(t.value==='') v=undefined;
    else if(k==='kind') v=t.value;
    else if(k==='drawer_heights'||k==='shelf_levels')
      v=t.value.split(/[,;\s]+/).map(Number).filter(x=>isFinite(x)&&x>0);
    else v=Number(t.value);
    if(v===undefined||(Array.isArray(v)&&!v.length)) delete s[k]; else s[k]=v;
    updateSectionSums();
    schedule(); return;
  }
  if(t.id&&t.id.startsWith('f_')){harvest();schedule();}
});
document.addEventListener('click',e=>{
  const del=e.target.dataset&&e.target.dataset.del;
  if(del!==undefined&&del!==null&&del!==''){SPEC.sections.splice(+del,1);fillForm();apply();}
});
$('applyRaw').onclick=()=>{try{SPEC=JSON.parse($('rawspec').value);fillForm();apply();}
  catch(err){toast('JSON: '+err.message,true);}};

/* ---------- генерация ---------- */
let timer=null, lastOk=false;
function schedule(){clearTimeout(timer);timer=setTimeout(apply,400);}
async function apply(){
  const r=await fetch('/api/generate',{method:'POST',
    headers:{'Content-Type':'application/json'},body:JSON.stringify({spec:SPEC})});
  const p=await r.json(); paint(p);
  if(drawOn) refreshDraw();
  if(nestOn) refreshNest();
}
function paint(p){
  lastPayload=p;
  const B=$('badges'); B.innerHTML='';
  const names={schema:'схема',consistency:'встык',geometry:'геометрия',cfrn:'.cfrn',
               holes:'присадки',drilling:'сверловка'};
  let errs=[];
  for(const k of Object.keys(names)){
    const bad=(p.issues[k]||[]).length>0;
    B.insertAdjacentHTML('beforeend',`<span class="badge ${bad?'bad':''}">${names[k]}</span>`);
    if(bad) errs.push(...p.issues[k].slice(0,4).map(x=>`[${names[k]}] ${x}`));
  }
  if(p.refs){                                    // подбор позиций базы (C4, информативно)
    const rs=Object.values(p.refs).filter(r=>r&&typeof r==='object');
    const ok=rs.filter(r=>r.resolved).length;
    if(rs.length) B.insertAdjacentHTML('beforeend',
      `<span class="badge" style="background:${ok===rs.length?'var(--ok)':'#c78a2b'}"
        title="позиции производственной базы">база ${ok}/${rs.length}</span>`);
  }
  $('errors').textContent=errs.join('\n');
  lastOk=p.ok; $('btnB3d').disabled=!p.ok;
  if(p.viewer){rebuild(p.viewer);
    const C=p.viewer.colors||{};
    $('swCarcass').style.background=C.side_left||'#c9a06a';
    $('swFacade').style.background=C.door_front||C.side_left||'#c9a06a';
    renderHwSlots(p.refs||{});
    const st=p.stats;
    const tot=(p.estimate&&p.estimate.total>0)
      ?`≈${Math.round(p.estimate.total).toLocaleString('ru-RU')}₽`:'—';
    $('stats').innerHTML=`<div><b>${st.n_panels}</b><span>деталей</span></div>
      <div><b>${st.n_holes}</b><span>присадок</span></div>
      <div><b>${tot}</b><span>материалы</span></div>
      <div><b>${st.dims.w}×${st.dims.d}×${st.dims.h}</b><span>${st.decor}</span></div>`;
    const tb=$('bom').querySelector('table');
    tb.innerHTML=(p.bom||[]).map(b=>`<tr><td>${b.slot}</td><td>${b.name}</td><td>${b.art}</td></tr>`).join('');
    renderEstimate(p.estimate);
  }
}

/* ---------- чертёж / раскрой ---------- */
let drawOn=false, nestOn=false;
function switchTab(mode){
  drawOn=(mode==='draw'); nestOn=(mode==='nest');
  $('draw').style.display=(drawOn||nestOn)?'block':'none';
  $('tab3d').classList.toggle('on',mode==='3d');
  $('tabDraw').classList.toggle('on',drawOn);
  $('tabNest').classList.toggle('on',nestOn);
  if(drawOn) refreshDraw();
  if(nestOn) refreshNest();
}
$('tab3d').onclick=()=>switchTab('3d');
$('tabDraw').onclick=()=>switchTab('draw');
$('tabNest').onclick=()=>switchTab('nest');
$('btnPrint').onclick=()=>{
  const svg=$('draw').querySelector('svg');
  if(!svg){toast('Откройте чертёж или раскрой',true);return;}
  const w=window.open('','print');
  w.document.write('<html><head><title>Печать</title></head><body>'+svg.outerHTML+
    '<scr'+'ipt>onload=()=>{print();close();}</scr'+'ipt></body></html>');
  w.document.close();
};
async function refreshNest(){
  const r=await fetch('/api/nesting',{method:'POST',
    headers:{'Content-Type':'application/json'},body:JSON.stringify({spec:SPEC})});
  const p=await r.json();
  $('draw').innerHTML=p.svg||('<i>'+(p.error||'раскрой недоступен')+'</i>');
}
async function refreshDraw(){
  const r=await fetch('/api/techview',{method:'POST',
    headers:{'Content-Type':'application/json'},body:JSON.stringify({spec:SPEC})});
  const p=await r.json(); $('draw').innerHTML=p.svg||('<i>'+(p.issues||[]).join('; ')+'</i>');
  const pi=scene3d.getSelected&&scene3d.getSelected();     // восстановить подсветку
  if(pi!==null&&pi!==undefined&&lastPayload){
    const nm=(lastPayload.viewer.panels[pi]||{}).name;
    if(nm) document.querySelectorAll(`#draw rect[data-panel="${CSS.escape(nm)}"]`)
      .forEach(el=>el.classList.add('sel'));
  }
}

/* ---------- выбор детали кликом (AKD-120) ---------- */
let lastPayload=null;
scene3d.onSelect=sel=>{
  const fs=$('fs_part'), card=$('partCard');
  document.querySelectorAll('#draw rect.sel').forEach(r=>r.classList.remove('sel'));
  if(!sel){fs.style.display='none';card.innerHTML='';return;}
  const p=sel.panel;
  const dx=p.x2-p.x1, dy=p.y2-p.y1, dz=p.z2-p.z1;
  const nHoles=((lastPayload&&lastPayload.viewer&&lastPayload.viewer.holes)||[])
    .filter(h=>h.panel===p.name).length;
  const hasOv=(SPEC.overrides||[]).some(o=>o.panel===p.name);
  const num=(id,v)=>`<input type="number" data-ov="${id}" value="${v}" step="1"
    style="width:74px;padding:2px 4px;border:1px solid var(--line);border-radius:4px">`;
  card.innerHTML=`<b>${p.name}</b>${hasOv?' <span class="mini" style="color:#c78a2b">✎ правлено</span>':''}
    <div class="kv">
      <span>Тип</span><span>${p.type||'—'}</span>
      <span>Габарит</span><span>${dx}×${dy}×${dz} мм</span>
      <span>Толщина</span><span>${p.thickness??'—'} мм</span>
      <span>Материал</span><span>${p.material||'—'}</span>
      <span>Присадки</span><span>${nHoles}</span>
    </div>
    <div class="kv" style="margin-top:6px">
      <span>X</span><span>${num('x1',p.x1)} … ${num('x2',p.x2)}</span>
      <span>Y</span><span>${num('y1',p.y1)} … ${num('y2',p.y2)}</span>
      <span>Z</span><span>${num('z1',p.z1)} … ${num('z2',p.z2)}</span>
    </div>
    <div class="row" style="gap:4px;margin-top:6px">
      <button id="ovApply" class="primary">Применить</button>
      ${hasOv?'<button id="ovReset" title="убрать правки этой детали">Сбросить</button>':''}
      <button id="ovDelete" title="удалить деталь из изделия">Удалить</button>
      <button id="ovDetail" title="чертёж этой детали с размерами и присадками">Чертёж</button>
    </div>
    <div class="mini" style="margin-top:4px">Shift+перетаскивание в 3D — двигать деталь.
    Правки хранятся в спеке и переживают смену габаритов; чертёж, присадки,
    смета и .b3d пересчитываются.</div>`;
  fs.style.display='';
  $('ovApply').onclick=()=>{
    const pl={};
    card.querySelectorAll('input[data-ov]').forEach(i=>{
      const v=Number(i.value);
      if(isFinite(v)&&v!==p[i.dataset.ov]) pl[i.dataset.ov]=v;
    });
    if(Object.keys(pl).length) setOverride(p.name, pl);
  };
  const rb=$('ovReset'); if(rb) rb.onclick=()=>clearOverride(p.name);
  $('ovDetail').onclick=async()=>{
    switchTab('draw');
    const r=await fetch('/api/techview',{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({spec:SPEC,panel:p.name})});
    const d=await r.json();
    $('draw').innerHTML=(d.svg||'')+'<div style="margin:8px"><button onclick="refreshDraw()">← общий чертёж</button></div>';
  };
  $('ovDelete').onclick=()=>{
    if(!confirm(`Удалить деталь «${p.name}»?`)) return;
    pushUndo();
    SPEC.overrides=(SPEC.overrides||[]).filter(o=>o.panel!==p.name);
    SPEC.overrides.push({panel:p.name, action:'delete'});
    $('rawspec').value=JSON.stringify(SPEC,null,2);
    scene3d.select(null); apply();
  };
  document.querySelectorAll(`#draw rect[data-panel="${CSS.escape(p.name)}"]`)
    .forEach(r=>r.classList.add('sel'));
};
function setOverride(name, placement, move){
  pushUndo();
  SPEC.overrides=SPEC.overrides||[];
  let ov=SPEC.overrides.find(o=>o.panel===name&&(o.action||'transform')==='transform');
  if(!ov){ov={panel:name}; SPEC.overrides.push(ov);}
  if(placement) ov.placement=Object.assign(ov.placement||{}, placement);
  if(move){
    // сдвиг фиксируем абсолютными гранями (складывается с прошлыми правками)
    const p=(lastPayload.viewer.panels||[]).find(q=>q.name===name);
    if(p){ov.placement=Object.assign(ov.placement||{},{
      x1:p.x1+move[0],x2:p.x2+move[0],y1:p.y1+move[1],y2:p.y2+move[1],
      z1:p.z1+move[2],z2:p.z2+move[2]});}
  }
  $('rawspec').value=JSON.stringify(SPEC,null,2);
  apply();
}
function clearOverride(name){
  pushUndo();
  SPEC.overrides=(SPEC.overrides||[]).filter(o=>o.panel!==name);
  if(!SPEC.overrides.length) delete SPEC.overrides;
  $('rawspec').value=JSON.stringify(SPEC,null,2);
  scene3d.select(null); apply();
}
scene3d.onTransform=(name,delta)=>setOverride(name,null,delta);
document.addEventListener('keydown',e=>{
  if(e.key==='Escape') scene3d.select(null);
});
document.addEventListener('click',e=>{           // клик по детали на чертеже
  const r=e.target.closest&&e.target.closest('#draw rect[data-panel]');
  if(!r) return;
  const name=r.getAttribute('data-panel');
  const idx=((lastPayload&&lastPayload.viewer&&lastPayload.viewer.panels)||[])
    .findIndex(p=>p.name===name);
  if(idx>=0) scene3d.select(idx);
});

/* ---------- каталог проектов (AKD-132) ---------- */
async function loadProjects(){
  const r=await fetch('/api/projects',{method:'POST',
    headers:{'Content-Type':'application/json'},body:'{}'});
  const p=await r.json();
  $('projSel').innerHTML=(p.projects||[]).map(x=>
    `<option value="${x.file}" ${x.file===p.current?'selected':''}>`+
    `${x.name.slice(0,38)} · ${x.archetype} ${x.dims}</option>`).join('');
}
function adoptSpec(p){
  SPEC=p.spec; UNDO.length=0; $('btnUndo').disabled=true;
  scene3d.select(null); fillForm(); apply(); loadProjects(); loadBuilds();
  toast('Открыто: '+p.file);
}
$('projSel').onchange=async e=>{
  const r=await fetch('/api/open',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({file:e.target.value})});
  const p=await r.json();
  if(p.ok) adoptSpec(p); else toast('Ошибка: '+(p.error||''),true);
};
$('projNew').onclick=async()=>{
  const arch=prompt('Архетип нового изделия:\n(desk, cabinet, wardrobe, shelving, drawer_unit, door_unit, round_table, corpus)','cabinet');
  if(!arch) return;
  const name=prompt('Название изделия:','Новое изделие');
  if(name===null) return;
  const r=await fetch('/api/new',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({archetype:arch.trim(),name})});
  const p=await r.json();
  if(p.ok) adoptSpec(p); else toast('Ошибка: '+(p.error||''),true);
};
$('projDup').onclick=async()=>{
  const r=await fetch('/api/duplicate',{method:'POST',
    headers:{'Content-Type':'application/json'},body:JSON.stringify({spec:SPEC})});
  const p=await r.json();
  if(p.ok) adoptSpec(p); else toast('Ошибка: '+(p.error||''),true);
};
loadProjects();

/* ---------- смета live (AKD-128) ---------- */
function renderEstimate(est){
  if(!est){$('fs_est').style.display='none';return;}
  $('fs_est').style.display='';
  const t=$('estTotal');
  t.textContent=est.total>0?`≈ ${est.total.toLocaleString('ru-RU')} ${est.currency}`:'—';
  if(est.warnings&&est.warnings.length)
    t.textContent+=`  (${est.warnings.length} без цены)`;
  const rows=[]; let grp='';
  for(const r of (est.rows||[])){
    if(r.group!==grp){grp=r.group;
      rows.push(`<tr class="grp"><td colspan="2">${grp}</td></tr>`);}
    const c=r.cost!=null?`${r.cost.toLocaleString('ru-RU')} ₽`:'— ₽';
    rows.push(`<tr><td title="${r.note||''}">${r.name}<br>
      <span class="mini">${r.qty} ${r.unit}${r.unit_cost?` × ${r.unit_cost}₽`:''}</span></td>
      <td>${c}</td></tr>`);
  }
  $('estTable').innerHTML=rows.join('');
}

/* ---------- фурнитура из базы (AKD-123) ---------- */
const HW_LABELS={handles:'Ручки',hinges:'Петли',drawer_guides:'Направляющие',
                 legs:'Опоры',locks:'Замки'};
function renderHwSlots(refs){
  const box=$('hwSlots'); box.innerHTML='';
  const sel=(SPEC.hardware&&SPEC.hardware.selection)||{};
  let total=0, chosen=0;
  for(const slot of Object.keys(HW_LABELS)){
    const r=refs[slot];
    if(!r||!Array.isArray(r.candidates)||!r.candidates.length) continue;
    total++;
    const cur=sel[slot]||'';
    if(cur) chosen++;
    const row=document.createElement('div'); row.className='row';
    const opts=['<option value="">— из шорт-листа —</option>']
      .concat(r.candidates.map(c=>{
        const price=(c.cost&&c.cost>0)?` · ${c.cost}₽`:'';
        return `<option value="${c.article}" ${String(cur)===String(c.article)?'selected':''}>`+
               `${(c.name||'').slice(0,46)}${price}</option>`;}));
    row.innerHTML=`<label>${HW_LABELS[slot]}</label>
      <select data-hw="${slot}">${opts.join('')}</select>`;
    box.appendChild(row);
  }
  $('hwBadge').textContent=total?`выбрано ${chosen}/${total}`:'— нет слотов';
  $('fs_hw').style.display=total?'':'none';
  const hh=(SPEC.hardware&&SPEC.hardware.handles)||{};
  $('f_hsize').value=hh.size??'';
  $('f_hoff').value=hh.offset_from_top??'';
}
document.addEventListener('change',e=>{
  const slot=e.target.dataset&&e.target.dataset.hw;
  if(!slot) return;
  SPEC.hardware=SPEC.hardware||{};
  const sel=SPEC.hardware.selection=SPEC.hardware.selection||{};
  if(e.target.value) sel[slot]=e.target.value; else delete sel[slot];
  if(!Object.keys(sel).length) delete SPEC.hardware.selection;
  $('rawspec').value=JSON.stringify(SPEC,null,2);
  apply();
});

/* ---------- выбор декора из производственной базы ---------- */
let decorTimer=null;
$('decorQ').addEventListener('input',()=>{clearTimeout(decorTimer);
  decorTimer=setTimeout(loadDecors,300);});
async function loadDecors(){
  const q=$('decorQ').value.trim();
  const box=$('decorList');
  if(!q){box.style.display='none';box.innerHTML='';return;}
  const r=await fetch('/api/decors',{method:'POST',
    headers:{'Content-Type':'application/json'},body:JSON.stringify({q})});
  const p=await r.json();
  box.innerHTML='';
  (p.items||[]).forEach(it=>{
    const d=document.createElement('div'); d.className='ditem';
    d.innerHTML=`<span class="sw" style="background:${it.hex}"></span>
      <span class="nm" title="${it.name} (арт. ${it.article})">${it.label}</span>
      <span class="mini">${it.thickness??''}</span>
      <button class="fb" title="применить к фасадам">Ф</button>`;
    d.onclick=e=>{
      pushUndo();
      if(e.target.classList.contains('fb')){          // фасады
        SPEC.materials.facade_color=it.label;
        SPEC.materials.facade_article=String(it.article);
      }else{                                          // корпус: выбор ДЕКОРА
        SPEC.materials.color=it.label;
        // артикул и толщину переносим только если позиция совпадает по толщине —
        // иначе это лишь цвет, конкретную плиту подберёт резолвер по декору
        if(it.thickness && it.thickness===SPEC.materials.board_thickness)
          SPEC.materials.board_article=String(it.article);
        else delete SPEC.materials.board_article;
      }
      fillForm(); apply();
    };
    box.appendChild(d);
  });
  if(!(p.items||[]).length)
    box.innerHTML='<div class="mini" style="padding:4px">ничего не найдено</div>';
  box.style.display='flex';
}

/* ---------- чат с ИИ + undo (AKD-107/108/110) ---------- */
const UNDO=[], CHAT_HISTORY=[];
function pushUndo(){UNDO.push(JSON.stringify(SPEC));
  if(UNDO.length>30)UNDO.shift(); $('btnUndo').disabled=false;}
$('btnUndo').onclick=()=>{
  if(!UNDO.length)return;
  SPEC=JSON.parse(UNDO.pop()); $('btnUndo').disabled=!UNDO.length;
  fillForm(); apply(); addMsg('ai','Откатил последнюю правку.');};
function addMsg(who,text,changes){
  const d=document.createElement('div'); d.className='cmsg '+who;
  d.textContent=text;
  if(changes&&changes.length){
    const df=document.createElement('span'); df.className='diff';
    df.textContent=changes.join('\n'); d.appendChild(df);
  }
  $('chatlog').appendChild(d); $('chatlog').scrollTop=1e9; return d;}
let chatBusy=false;
async function sendChat(){
  const m=$('chatMsg').value.trim();
  if(!m||chatBusy)return;
  chatBusy=true; $('chatMsg').value=''; addMsg('user',m);
  const wait=addMsg('ai','думаю…');
  try{
    const ctx=lastPayload?{n_panels:lastPayload.stats&&lastPayload.stats.n_panels,
      n_holes:lastPayload.stats&&lastPayload.stats.n_holes,
      dims:lastPayload.stats&&lastPayload.stats.dims,
      estimate_total:lastPayload.estimate&&lastPayload.estimate.total}:null;
    const r=await fetch('/api/chat',{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({spec:SPEC,message:m,history:CHAT_HISTORY,context:ctx})});
    const p=await r.json();
    wait.remove();
    addMsg('ai',p.reply||'(пусто)',p.changes);
    CHAT_HISTORY.push({role:'user',text:m},{role:'assistant',text:p.reply||''});
    if(CHAT_HISTORY.length>16)CHAT_HISTORY.splice(0,CHAT_HISTORY.length-16);
    if(p.spec){pushUndo(); SPEC=p.spec; fillForm(); apply();}
  }catch(e){wait.remove(); addMsg('ai','Ошибка: '+e.message);}
  finally{chatBusy=false;}
}
$('chatSend').onclick=sendChat;
$('chatMsg').addEventListener('keydown',e=>{if(e.key==='Enter')sendChat();});

/* ---------- экспорт ---------- */
async function post(url){const r=await fetch(url,{method:'POST',
  headers:{'Content-Type':'application/json'},body:JSON.stringify({spec:SPEC})});
  return await r.json();}
$('btnSave').onclick=async()=>{const p=await post('/api/save');
  toast(p.ok?('Сохранено: '+p.spec):('Ошибка: '+p.error),!p.ok);
  loadVersions();};
$('btnCfrn').onclick=async()=>{const p=await post('/api/export-cfrn');
  toast(p.ok?('.cfrn: '+p.cfrn):('Ошибка: '+p.error),!p.ok);};
$('btnB3d').onclick=async()=>{
  if(!lastOk){toast('Проверки не пройдены',true);return;}
  if(!confirm('Собрать .b3d через облако БАЗИС? Операция платная (~10₽).'))return;
  toast('Сборка в облаке…');
  const p=await post('/api/build-b3d');
  toast(p.ok?('Готов .b3d: '+p.b3d):('Ошибка: '+(p.error||'')),!p.ok);
  if(p.ok){loadBuilds();
    if(confirm('Открыть результат в БАЗИС-Просмотре?'))
      await fetch('/api/open-file',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({path:p.b3d})});}};

/* ---------- версии (AKD-133) ---------- */
async function loadVersions(){
  const r=await fetch('/api/versions',{method:'POST',
    headers:{'Content-Type':'application/json'},body:'{}'});
  const p=await r.json();
  $('verSel').innerHTML='<option value="">— версии (при сохранении) —</option>'+
    (p.versions||[]).map(v=>`<option value="${v.index}">${v.ts.replace('T',' ')} · `+
      `${v.dims}${v.n_overrides?` · правок ${v.n_overrides}`:''}</option>`).join('');
}
$('verRestore').onclick=async()=>{
  const idx=$('verSel').value;
  if(idx==='')return;
  const r=await fetch('/api/restore',{method:'POST',
    headers:{'Content-Type':'application/json'},body:JSON.stringify({index:+idx})});
  const p=await r.json();
  if(p.ok){pushUndo(); SPEC=p.spec; scene3d.select(null); fillForm(); apply();
    toast('Восстановлена версия '+p.ts);}
  else toast('Ошибка: '+(p.error||''),true);
};
loadVersions();

/* ---------- экспорт-центр (AKD-130) ---------- */
$('btnDeliver').onclick=async()=>{
  toast('Собираю лист согласования…');
  const p=await post('/api/deliver');
  toast(p.ok?`Лист v${p.version} открыт в браузере`:('Ошибка: '+(p.error||'')),!p.ok);};
async function loadBuilds(){
  const r=await fetch('/api/builds',{method:'POST',
    headers:{'Content-Type':'application/json'},body:'{}'});
  const p=await r.json();
  const b=$('builds');
  if(!(p.builds||[]).length){b.innerHTML='';return;}
  b.innerHTML=`<div class="mini">Сборки .b3d (расход ~${p.total_spent}₽):</div>`+
    p.builds.slice(0,5).map(x=>{
      const f=(x.file||'').split(/[\\/]/).pop();
      const par=x.parity===true?' <span title="паритет Studio↔b3d подтверждён" style="color:var(--ok)">✓</span>'
               :(x.parity===false?' <span title="паритет не подтверждён" style="color:#c78a2b">?</span>':'');
      return `<div class="row" style="margin:2px 0"><span class="mini" style="flex:1"
        title="${x.file}">${x.ts.replace('T',' ')} · ${f}${par}</span>
        <button class="fb" data-open="${x.file}">▶</button></div>`;}).join('');
}
document.addEventListener('click',async e=>{
  const f=e.target.dataset&&e.target.dataset.open;
  if(!f) return;
  const r=await fetch('/api/open-file',{method:'POST',
    headers:{'Content-Type':'application/json'},body:JSON.stringify({path:f})});
  const p=await r.json();
  if(!p.ok) toast('Открыть не удалось: '+(p.error||''),true);
});
loadBuilds();

/* старт */
fillForm(); resize(); apply();
</script></body></html>"""
