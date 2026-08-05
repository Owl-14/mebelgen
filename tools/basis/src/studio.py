"""Akeda Studio (ранее BAZIS Studio): редактор-предпросмотр единицы мебели (AKD-94…97).

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


# ------------------------------------------------------------------ аксонометрия для карточек каталога

def _shade(hexcol: str, k: float) -> str:
    """Осветлить (k>0) / затемнить (k<0) цвет #rrggbb — грани аксонометрии."""
    h = (hexcol or "#c9a06a").lstrip("#")
    if len(h) != 6:
        h = "c9a06a"
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    if k >= 0:
        r, g, b = (round(c + (255 - c) * k) for c in (r, g, b))
    else:
        r, g, b = (round(c * (1 + k)) for c in (r, g, b))
    return f"#{r:02x}{g:02x}{b:02x}"


def _axon_svg(spec: dict[str, Any]) -> str | None:
    """Изометрическая проекция изделия в SVG — превью карточки каталога (AKD-217).

    Камера как в 3D по умолчанию: фронт + верх + правый бок; painter-сортировка
    панелей по глубине. Ошибки не бросают — None (карточка покажет заглушку).
    """
    try:
        from .generators import generate_from_paramspec
        from .webviewer import _hardware, _palette, _panels
        project = generate_from_paramspec(spec)
        panels = _panels(project)
        colors = _palette(project)
        # тела фурнитуры (металлокаркас, опоры, ручки) — иначе стол «висит в воздухе»
        for hw in _hardware(project):
            panels.append({"type": "_hw", "color": hw.get("color") or "#8f969e",
                           "x1": hw["x1"], "x2": hw["x2"], "y1": hw["y1"],
                           "y2": hw["y2"], "z1": hw["z1"], "z2": hw["z2"]})
    except Exception:
        return None
    if not panels:
        return None
    zmax = max(p["z2"] for p in panels)
    C, S = 0.866, 0.5                     # изометрия: cos30 / sin30
    RX, RY = C * 1.41421356, S * 1.41421356   # круг в плане → эллипс rx=1.22r, ry=0.71r

    def pt(x: float, y: float, z: float) -> tuple[float, float]:
        # z уже в показных координатах (фронт → большие z); Y экрана вниз
        return (x - z) * C, (x + z) * S - y

    boxes = []
    for p in panels:
        x1, x2, y1, y2 = p["x1"], p["x2"], p["y1"], p["y2"]
        z1, z2 = zmax - p["z2"], zmax - p["z1"]     # БАЗИС → фронт модели в +Z
        depth = x1 + x2 + y1 + y2 + z1 + z2         # ~2×центр вдоль луча (1,1,1)
        boxes.append((depth, p, x1, x2, y1, y2, z1, z2))
    boxes.sort(key=lambda b: b[0])                  # дальние — первыми

    base_col = colors.get("_default", "#c9a06a")
    edge = colors.get("_edge", "#5a4326")
    span = max(max(p["x2"] for p in panels), max(p["y2"] for p in panels), zmax, 1)
    sw = round(span * 0.004, 2)                     # толщина контура ∝ габариту
    xs: list[float] = []
    ys: list[float] = []
    polys: list[str] = []

    def emit(tag: str, pts_flat: list[tuple[float, float]]) -> None:
        xs.extend(px for px, _ in pts_flat); ys.extend(py for _, py in pts_flat)
        polys.append(tag)

    # тень-подложка на полу (y=0) — модель «стоит», а не висит на белом
    gx0 = min(b[2] for b in boxes); gx1 = max(b[3] for b in boxes)
    gz0 = min(b[6] for b in boxes); gz1 = max(b[7] for b in boxes)
    gm = span * 0.04
    sh = [pt(x, 0, z) for x, z in ((gx0 - gm, gz0 - gm), (gx1 + gm, gz0 - gm),
                                   (gx1 + gm, gz1 + gm), (gx0 - gm, gz1 + gm))]
    emit('<polygon points="' + " ".join(f"{px:.1f},{py:.1f}" for px, py in sh)
         + '" fill="#000" fill-opacity="0.07"/>', sh)

    for _, p, x1, x2, y1, y2, z1, z2 in boxes:
        col = p.get("color") or colors.get(p["type"], base_col)
        if p.get("shape") in ("circle", "cylinder"):
            # круглые детали (round_table): цилиндр = низ-эллипс + тело + верх-эллипс
            r = float(p.get("radius") or (x2 - x1) / 2)
            cx, cz = (x1 + x2) / 2, (z1 + z2) / 2
            ecx, ety = pt(cx, y2, cz)
            _, eby = pt(cx, y1, cz)
            rx, ry = r * RX, r * RY
            side = _shade(col, -0.12)
            emit(f'<ellipse cx="{ecx:.1f}" cy="{eby:.1f}" rx="{rx:.1f}" ry="{ry:.1f}" '
                 f'fill="{side}" stroke="{edge}" stroke-width="{sw}"/>',
                 [(ecx - rx, eby - ry), (ecx + rx, eby + ry)])
            if eby - ety > 0.5:                     # тело, если есть высота
                emit(f'<rect x="{ecx - rx:.1f}" y="{ety:.1f}" width="{2 * rx:.1f}" '
                     f'height="{eby - ety:.1f}" fill="{side}"/>',
                     [(ecx - rx, ety), (ecx + rx, eby)])
                for lx in (ecx - rx, ecx + rx):     # образующие
                    emit(f'<line x1="{lx:.1f}" y1="{ety:.1f}" x2="{lx:.1f}" y2="{eby:.1f}" '
                         f'stroke="{edge}" stroke-width="{sw}"/>', [(lx, ety)])
            emit(f'<ellipse cx="{ecx:.1f}" cy="{ety:.1f}" rx="{rx:.1f}" ry="{ry:.1f}" '
                 f'fill="{_shade(col, 0.18)}" stroke="{edge}" stroke-width="{sw}"/>',
                 [(ecx - rx, ety - ry), (ecx + rx, ety + ry)])
            continue
        faces = (
            # верх (y2) — светлее, фронт (z2) — базовый, правый бок (x2) — темнее
            (((x1, y2, z1), (x2, y2, z1), (x2, y2, z2), (x1, y2, z2)), _shade(col, 0.18)),
            (((x1, y1, z2), (x2, y1, z2), (x2, y2, z2), (x1, y2, z2)), col),
            (((x2, y1, z2), (x2, y1, z1), (x2, y2, z1), (x2, y2, z2)), _shade(col, -0.22)),
        )
        for corners, fill in faces:
            pp = [pt(*c) for c in corners]
            emit('<polygon points="' + " ".join(f"{px:.1f},{py:.1f}" for px, py in pp)
                 + f'" fill="{fill}" stroke="{edge}" stroke-width="{sw}" '
                 'stroke-linejoin="round"/>', pp)
    m = span * 0.03                                 # поля вокруг изделия
    x0, y0 = min(xs) - m, min(ys) - m
    w, h = max(xs) - x0 + m, max(ys) - y0 + m
    return (f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'viewBox="{x0:.1f} {y0:.1f} {w:.1f} {h:.1f}">{"".join(polys)}</svg>')


_AXON_VERSION = 3      # менять при правке _axon_svg — инвалидирует кэш миниатюр


def _thumb_svg_cached(spec_dir: Path, fname: str) -> bytes | None:
    """SVG-превью по имени спеки; кэш в .previews, инвалидация по mtime спеки."""
    src = _safe_spec_file(spec_dir, fname)
    if not src.is_file():
        return None
    pd = spec_dir / ".previews"
    cache = pd / f"{src.stem}.axon{_AXON_VERSION}.svg"
    try:
        if cache.is_file() and cache.stat().st_mtime >= src.stat().st_mtime:
            return cache.read_bytes()
    except OSError:
        pass
    try:
        spec = json.loads(src.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(spec, dict) or spec.get("draft"):
        return None
    svg = _axon_svg(spec)
    if not svg:
        return None
    data = svg.encode("utf-8")
    try:
        pd.mkdir(exist_ok=True)
        cache.write_bytes(data)
    except OSError:
        pass
    return data


# ------------------------------------------------------------------ каталог проектов (D1)

def _slugify(name: str) -> str:
    import re
    s = re.sub(r"[^\w\-]+", "_", name.lower().strip()).strip("_")
    return s[:60] or "model"


def _list_projects(spec_dir: Path) -> list[dict[str, Any]]:
    out = []
    for f in sorted(spec_dir.glob("*.json")):
        if f.name.endswith((".project.json", ".versions.json")):
            continue
        try:
            s = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(s, dict) or s.get("schemaVersion") != "paramspec-v1":
            continue
        d = s.get("dimensions", {})
        has_prev = (spec_dir / ".previews" / (f.stem + ".png")).is_file()
        if s.get("draft"):                            # черновик (AKD-214)
            out.append({"file": f.name, "name": s.get("project_name", f.stem),
                        "archetype": "черновик", "dims": "—", "decor": "",
                        "draft": True, "preview": False,
                        "ftype": s.get("furniture_type", "")})
            continue
        out.append({"file": f.name,
                    "name": s.get("project_name", f.stem),
                    "archetype": s.get("archetype", "?"),
                    "dims": f'{d.get("width", "?")}×{d.get("depth", "?")}×{d.get("height", "?")}',
                    "decor": (s.get("materials") or {}).get("color", ""),
                    "preview": has_prev,
                    "ftype": s.get("furniture_type", "")})
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


# ------------------------------------------------------------------ защита публичного демо (AKD-271)
#
# Публичный инстанс (STUDIO_PUBLIC=1 в .env): ИИ-чат ходит через наши ключи —
# без ограничений бот выжигает лимиты за минуты, а общая папка изделий даёт
# любому посетителю портить демо-образцы. Локальной разработке лимиты не мешают.

import os as _os
import time as _time

_CHAT_RPM = int(_os.environ.get("STUDIO_CHAT_RPM", "8"))          # запросов чата в минуту с IP
_CHAT_RPD = int(_os.environ.get("STUDIO_CHAT_RPD", "200"))        # в сутки с IP
_TOKENS_PER_DAY = int(_os.environ.get("STUDIO_TOKENS_PER_DAY", "400000"))  # на инстанс


class _ChatGuard:
    """Rate-limit по IP + суточный бюджет токенов (журнал в out/chat_tokens.json)."""

    def __init__(self, out_dir: Path):
        self.hits: dict[str, list[float]] = {}
        self.f = out_dir / "chat_tokens.json"

    def check(self, ip: str) -> str | None:
        now = _time.time()
        q = [t for t in self.hits.get(ip, []) if now - t < 86400]
        if sum(1 for t in q if now - t < 60) >= _CHAT_RPM:
            return "слишком часто — подождите минуту"
        if len(q) >= _CHAT_RPD:
            return "дневной лимит запросов чата с этого адреса исчерпан"
        q.append(now)
        self.hits[ip] = q
        if len(self.hits) > 5000:                          # защита памяти от скана IP
            self.hits = {k: v for k, v in list(self.hits.items())[-2500:]}
        return None

    def _day(self) -> str:
        return _time.strftime("%Y-%m-%d")

    def tokens_left(self) -> int:
        try:
            d = json.loads(self.f.read_text(encoding="utf-8"))
        except Exception:
            d = {}
        return _TOKENS_PER_DAY - int(d.get(self._day(), 0))

    def add_tokens(self, n: int) -> None:
        if not n:
            return
        try:
            d = json.loads(self.f.read_text(encoding="utf-8"))
        except Exception:
            d = {}
        day = self._day()
        try:
            self.f.write_text(json.dumps({day: int(d.get(day, 0)) + int(n)}),
                              encoding="utf-8")
        except OSError:
            pass


# ------------------------------------------------------------------ server

class _Studio:
    def __init__(self, spec_path: Path, out_dir: Path):
        self.spec_path = spec_path
        self.out_dir = out_dir
        self.spec = json.loads(spec_path.read_text(encoding="utf-8"))
        self.public = _os.environ.get("STUDIO_PUBLIC") == "1"
        self.guard = _ChatGuard(out_dir)
        self.started = _time.time()               # /healthz, /version (AKD-264)
        # демо-режим: изделия, существовавшие на старте, защищены от перезаписи
        self.protected: set[str] = (
            {f.name for f in spec_path.parent.glob("*.json")
             if not f.name.endswith((".project.json", ".versions.json"))}
            if self.public else set())


def make_handler(st: _Studio):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):                       # тихий сервер
            pass

        def _send(self, code: int, body: bytes, ctype: str = "application/json; charset=utf-8"):
            try:
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                # Штатная отмена fetch в Studio: клиент больше не ждёт ответ.
                return

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
            elif self.path.startswith("/vendor/"):    # three.js локально, без CDN (AKD-261)
                vd = (Path(__file__).resolve().parent.parent / "vendor")
                p = (vd / Path(self.path[len("/vendor/"):]).name).resolve()
                if p.parent == vd.resolve() and p.suffix == ".js" and p.is_file():
                    self._send(200, p.read_bytes(),
                               "application/javascript; charset=utf-8")
                else:
                    self._send(404, b"{}")
            elif self.path in {                       # exact user-supplied brand crops
                "/assets/studio/akeda-studio-wordmark.png",
                "/assets/studio/akeda-studio-mark.png",
            }:
                assets = Path(__file__).resolve().parent.parent / "assets" / "studio"
                p = assets / self.path.rsplit("/", 1)[-1]
                if p.is_file():
                    self._send(200, p.read_bytes(), "image/png")
                else:
                    self._send(404, b"{}")
            elif self.path == "/healthz":             # мониторинг (AKD-264)
                self._json({"ok": True, "uptime_s": int(_time.time() - st.started)})
            elif self.path == "/version":             # какой код развёрнут (AKD-264)
                sha = ""
                try:
                    sha = (Path(__file__).resolve().parent.parent / "DEPLOY_SHA") \
                        .read_text(encoding="utf-8").strip()
                except OSError:
                    pass
                self._json({"sha": sha or "dev", "public": st.public,
                            "started": int(st.started)})
            elif self.path.startswith("/thumb/"):     # аксонометрия карточки (AKD-217)
                from urllib.parse import unquote
                try:
                    data = _thumb_svg_cached(st.spec_path.parent,
                                             unquote(self.path[len("/thumb/"):]))
                except Exception:
                    data = None
                if data:
                    self._send(200, data, "image/svg+xml; charset=utf-8")
                else:
                    self._send(404, b"{}")
            elif self.path.startswith("/preview/"):   # миниатюры каталога (AKD-217)
                from urllib.parse import unquote
                name = unquote(self.path[len("/preview/"):])
                p = (st.spec_path.parent / ".previews" / name).resolve()
                if (p.parent == (st.spec_path.parent / ".previews").resolve()
                        and p.suffix == ".png" and p.is_file()):
                    self._send(200, p.read_bytes(), "image/png")
                else:
                    self._send(404, b"{}")
            else:
                self._send(404, b"{}")

        def _chat_gate(self) -> str | None:
            """Лимиты ИИ-эндпоинтов (AKD-271): None — можно, иначе текст отказа."""
            ip = self.headers.get("X-Real-IP") or self.client_address[0]
            err = st.guard.check(ip)
            if err:
                return err
            if st.guard.tokens_left() <= 0:
                return "дневной бюджет токенов демо исчерпан — приходите завтра"
            return None

        def do_POST(self):
            try:
                body = self._body()
                spec = body.get("spec") or {}
                if self.path in ("/api/chat", "/api/import-tz"):
                    gate = self._chat_gate()
                    if gate:
                        self._json({"ok": False, "error": gate,
                                    "reply": "⛔ " + gate}, 429)
                        return
                if self.path == "/api/generate":
                    self._json(build_payload(spec))
                elif self.path == "/api/techview":
                    self._json(techview_svg(spec, body.get("panel")))
                elif self.path == "/api/chat":
                    from .spec_chat import chat_edit
                    ctx = body.get("context") or None
                    # ИИ не знает содержимого производственной базы: для
                    # нерешённых слотов даём РЕАЛЬНЫХ кандидатов (иначе модель
                    # выдумывает артикулы и «починка базы» не работает)
                    if isinstance(ctx, dict) and isinstance(ctx.get("base_unresolved"), list):
                        try:
                            from .materials import list_sheet_decors, search_base
                            queries = {"handles": "ручка", "hinges": "петля наклад",
                                       "drawer_guides": "направляющ", "guides": "направляющ",
                                       "legs": "опора", "locks": "замок", "edge": "кромка"}
                            cand: dict[str, Any] = {}
                            _th = ((spec or {}).get("materials") or {}).get("board_thickness")
                            for slot in ctx["base_unresolved"][:6]:
                                if slot in ("board", "facade", "back"):
                                    # только листы нужной толщины — иначе ИИ
                                    # выберет 3-мм ХДФ для корпуса 16
                                    items = list_sheet_decors(
                                        "", thickness=float(_th) if _th and slot != "back" else None,
                                        limit=5)
                                    cand[slot] = [{"name": i.get("name"),
                                                   "article": i.get("article")} for i in items]
                                elif slot in queries:
                                    items = search_base(queries[slot], limit=4)
                                    cand[slot] = [{"name": i.get("name"),
                                                   "article": i.get("article")} for i in items]
                            if cand:
                                ctx["base_candidates"] = cand
                        except Exception:
                            pass
                    res = chat_edit(spec, str(body.get("message", "")),
                                    body.get("history") or [],
                                    ctx,
                                    body.get("images") or None,
                                    body.get("provider") or None)
                    st.guard.add_tokens(int(((res.get("usage") or {}).get("total")) or 0))
                    self._json(res)
                elif self.path == "/api/providers":       # список нейросетей для селектора
                    from .spec_chat import available_providers
                    self._json(available_providers())
                elif self.path == "/api/token-balance":   # лимиты/баланс выбранной сети
                    from .spec_chat import token_balance
                    self._json(token_balance(body.get("provider") or None))
                elif self.path == "/api/import-tz":   # drag&drop ТЗ (D4) → провайдер чата
                    try:
                        from .spec_chat import chat_edit
                        name = str(body.get("name", "tz.png"))
                        ext = name.rsplit(".", 1)[-1].lower()
                        mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                                "webp": "image/webp", "gif": "image/gif"}.get(ext, "image/png")
                        # пустой базовый спек — иначе модель якорится на текущее
                        # изделие и копирует его секции вместо чистой сборки по ТЗ
                        res = chat_edit({},
                                        "Собери ParamSpec ТОЛЬКО по этому ТЗ (фото/скан): "
                                        "определи тип изделия, габариты, секции, материал по "
                                        "изображению. НЕ бери ничего из других изделий. created=true.",
                                        images=[{"mime": mime, "data": str(body.get("data", ""))}],
                                        provider=body.get("provider") or None)
                        st.guard.add_tokens(int(((res.get("usage") or {}).get("total")) or 0))
                        new_spec = res.get("spec")
                        if not new_spec:
                            self._json({"ok": False, "error":
                                        res.get("reply") or "не удалось распознать ТЗ"})
                            return
                        if isinstance(st.spec, dict) and st.spec.get("draft"):
                            out = st.spec_path        # ТЗ в черновик — тот же файл
                        else:
                            title = new_spec.get("project_name", "Из ТЗ")
                            out = st.spec_path.parent / f"{_slugify(title)}.json"
                            i = 2
                            while out.exists():
                                out = st.spec_path.parent / f"{_slugify(title)}_{i}.json"
                                i += 1
                        out.write_text(json.dumps(new_spec, ensure_ascii=False, indent=2),
                                       encoding="utf-8")
                        st.spec, st.spec_path = new_spec, out
                        self._json({"ok": True, "spec": new_spec, "file": out.name,
                                    "usage": res.get("usage")})
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)[:300]})
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
                elif self.path == "/api/new":         # новое изделие: черновик (AKD-214)
                    name = str(body.get("name") or "Новое изделие")
                    new_spec = {"schemaVersion": "paramspec-v1", "draft": True,
                                "project_name": name}
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
                    # демо-режим (AKD-271): исходные образцы каталога защищены
                    if st.public and st.spec_path.name in st.protected:
                        self._json({"ok": False, "error":
                                    "демо-режим: исходное изделие защищено — "
                                    "нажмите «Дублировать» и правьте копию"}, 403)
                        return
                    st.spec = spec
                    st.spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2),
                                            encoding="utf-8")
                    prev = body.get("preview")         # снапшот 3D для каталога (AKD-217)
                    if isinstance(prev, str) and prev.startswith("data:image/png;base64,"):
                        try:
                            import base64
                            pd = st.spec_path.parent / ".previews"
                            pd.mkdir(exist_ok=True)
                            (pd / (st.spec_path.stem + ".png")).write_bytes(
                                base64.b64decode(prev.split(",", 1)[1]))
                        except Exception:
                            pass
                    if spec.get("draft"):              # черновик: только файл, без модели
                        self._json({"ok": True, "spec": str(st.spec_path), "project": None})
                        return
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
    print(f"Akeda Studio: {url}  (спека: {spec_path.name}; Ctrl+C — стоп)")
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
<html lang="ru"><head><meta charset="utf-8"><title>Akeda Studio — предпросмотр и правки</title>
<style>
  :root{--ink:#1a1d21;--mut:#6b7280;--line:#dfe3e8;--bg:#f4f6f8;--card:#fff;
        --ok:#2fa84f;--bad:#e5484d;--accent:#3b82f6}
  *{box-sizing:border-box} html,body{margin:0;height:100%;font-family:Segoe UI,Arial,sans-serif;
    background:var(--bg);color:var(--ink);font-size:13px;overflow:hidden}
  /* AKD-207 / MEB-093: рабочая область 3D приоритетна. На обычных ноутбуках
     инспектор справа свёрнут в рейл, на широких экранах обе панели открыты. */
  #app{--side-width:340px;--inspector-width:360px;--rail-width:48px;
    display:grid;grid-template-columns:var(--side-width) minmax(0,1fr) var(--inspector-width);
    grid-template-rows:100%;height:100%}
  #app.right-collapsed{
    grid-template-columns:var(--side-width) minmax(0,1fr) var(--rail-width)}
  /* grid-row:1 всем — иначе #main (col2) после #rightside (col3) в DOM уходит в row2 */
  #side{grid-column:1;grid-row:1;display:flex;flex-direction:column;min-width:0;
    background:var(--card);border-right:1px solid var(--line);overflow:hidden}
  #sideScroll{display:flex;flex:0 1 auto;flex-direction:column;min-height:0;overflow:hidden;
    padding:12px 12px 14px}
  #sidePinned{flex:0 0 auto;min-width:0}
  #side #fs_part{flex:0 1 auto;min-height:0;overflow-y:auto;overscroll-behavior:contain;
    scrollbar-gutter:stable;padding:11px 2px 4px 0;border-bottom:1px solid var(--line)}
  #side #fs_part,#chatlog{scrollbar-width:thin;scrollbar-color:#c8ced7 transparent}
  #side #fs_part::-webkit-scrollbar,#chatlog::-webkit-scrollbar{width:7px}
  #side #fs_part::-webkit-scrollbar-thumb,#chatlog::-webkit-scrollbar-thumb{
    border:2px solid transparent;border-radius:7px;background:#c8ced7;background-clip:padding-box}
  #side .row>*{min-width:0}
  #side .project-actions{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));
    gap:5px!important;margin-top:7px}
  #side .project-actions button{display:flex;align-items:center;justify-content:center;
    gap:5px;min-width:0;padding:6px 5px}
  #side svg.ui-icon,#fs_chat svg.ui-icon{width:16px;height:16px;flex:0 0 16px;fill:none;stroke:currentColor;
    stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}
  #side .icon-button{display:grid;place-items:center;flex:0 0 32px;width:32px;height:32px;padding:0}
  #side h1.studio-brand{display:grid;gap:1px;margin:0 0 13px;font-size:13px;
    line-height:1;letter-spacing:0}
  #side .studio-brand-lockup{display:flex;align-items:center;gap:8px;min-width:0;height:30px}
  #side .studio-brand-wordmark{display:block;width:158px;max-width:calc(100% - 72px);
    height:auto;object-fit:contain}
  #side .studio-brand-mark{display:block;width:64px;height:auto;object-fit:contain}
  #side .studio-brand-tagline{display:block;margin-left:1px;color:#687180;
    font-size:9.5px;line-height:13px;font-weight:400;letter-spacing:.015em}
  #side fieldset{border:0;border-radius:0;margin:0;padding:0}
  #side legend{font-size:12px;line-height:18px;font-weight:600;text-transform:none;
    color:var(--ink);padding:0;margin-bottom:6px}
  #side #fs_project{padding-bottom:12px;border-bottom:1px solid var(--line)}
  #fs_project .project-select{gap:5px;margin:0}
  #fs_project .project-select label{position:absolute;width:1px;height:1px;overflow:hidden;
    clip:rect(0,0,0,0)}
  #fs_project .project-select select{height:32px;font-weight:600}
  #modelState{padding:11px 0 12px;border-bottom:1px solid var(--line)}
  .side-section-head{display:flex;align-items:center;gap:8px;margin-bottom:7px}
  .side-section-head>span{font-size:12px;font-weight:600;flex:1}
  #btnUndo{display:flex;align-items:center;gap:4px;padding:4px 6px;border-color:transparent;
    background:transparent;color:#4f5968;font-size:11.5px}
  #btnUndo:not(:disabled):hover{color:var(--accent);background:#eef4ff}
  #btnFixAll{display:flex;align-items:center;justify-content:center;gap:6px;width:100%;
    margin-top:8px;color:#8a4b08;background:#fff8eb;border-color:#efd6a7}
  #btnFixAll[hidden]{display:none}
  #modelState #errors:empty{display:none}
  #modelState #errors:not(:empty){margin:8px 0 0;padding:7px 8px;background:#fff4f2;
    border-left:2px solid var(--bad);color:#9f2d2f;max-height:120px;overflow-y:auto;
    overscroll-behavior:contain}
  #modelState #errors:not(:empty)::before{content:"Ошибки проверок";display:block;margin-bottom:4px;
    color:#7f2528;font-size:10.5px;font-weight:600}
  #side #stats{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px 12px;
    padding-top:9px;margin:8px 0 0;border-top:1px solid #edf0f3}
  #side #stats>div{min-width:0}
  #side #stats b{display:block;font-size:13px;line-height:17px;font-variant-numeric:tabular-nums;
    white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  #side #stats span{display:block;font-size:10.5px;line-height:14px;color:var(--mut);
    white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  #side .badges{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:2px 12px}
  #side .badge{display:flex;align-items:center;gap:6px;min-width:0;padding:2px 0;
    border-radius:0;color:#46505e;background:transparent!important;font-size:11px}
  #side .badge::before{content:"";width:7px;height:7px;flex:0 0 7px;border-radius:50%;
    background:var(--ok);box-shadow:inset 0 0 0 1px rgba(0,0,0,.08)}
  #side .badge.bad::before{background:var(--bad)}
  #side .badge.warn::before{background:#c78a2b}
  #fs_part legend.part-section-head{display:flex;align-items:center;width:100%;gap:8px;
    margin:0 0 7px;padding:0}
  .part-section-head>span{flex:1;font-size:12px;line-height:18px;font-weight:600}
  #partClearSelection{display:grid;place-items:center;width:26px;height:26px;padding:0;
    border-color:transparent;background:transparent;color:#6c7582}
  #partClearSelection:hover{background:#eef1f4;color:#303947}
  #partClearSelection svg{width:14px!important;height:14px!important}
  #partCard{color:#303641;font-size:12px;line-height:1.45}
  .part-identity{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:2px 8px;min-width:0}
  #partName{min-width:0;font-size:13.5px;line-height:18px;white-space:nowrap;
    overflow:hidden;text-overflow:ellipsis}
  #partKind{grid-column:1;color:var(--mut);font-size:10.5px;line-height:14px}
  #partOrigin{grid-column:2;grid-row:1/3;align-self:center;color:#687180;
    font-size:10.5px;line-height:14px;text-align:right}
  #fs_part.has-override #partOrigin{color:#8a5a13}
  .part-facts{margin:8px 0 0;padding:7px 0;border-top:1px solid #edf0f3;
    border-bottom:1px solid #edf0f3}
  .part-facts>div{display:grid;grid-template-columns:minmax(72px,30%) minmax(0,1fr);
    gap:7px;padding:2px 0}
  .part-facts dt{color:var(--mut);font-size:10.5px;line-height:15px}
  .part-facts dd{margin:0;min-width:0;font-size:11px;line-height:15px;
    overflow-wrap:anywhere;font-variant-numeric:tabular-nums}
  .part-primary-actions{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:5px;margin-top:7px}
  .part-primary-actions button{display:flex;align-items:center;justify-content:center;gap:5px}
  .part-context-note{margin:6px 0 0;color:#687180;font-size:10.5px;line-height:14px}
  .part-exact{margin-top:7px;border-top:1px solid #edf0f3}
  .part-exact>summary{padding:7px 0 5px;color:#46505e;font-size:11.5px;cursor:pointer}
  .part-exact[open]>summary{font-weight:600}
  .part-exact-hint{margin:0 0 6px;color:#727b88;font-size:10.5px;line-height:14px}
  #partEditStatus{min-height:15px;margin-bottom:5px;color:#6d7682;font-size:10.5px;
    line-height:15px}
  #partEditStatus.is-dirty{color:#8a5a13}
  #partEditStatus.is-error{color:#a02f34}
  #partEditStatus.is-success{color:#287d40}
  .part-axis-grid{display:grid;gap:5px}
  .part-axis{display:grid;grid-template-columns:12px minmax(48px,1fr) 10px minmax(48px,1fr) 42px;
    gap:4px;align-items:center}
  .part-axis>span:first-child{font-weight:600;color:#3f4855}
  .part-axis input{width:100%;min-width:0;max-width:none;height:28px;padding:3px 5px;
    font:11px/1 Consolas,monospace;font-variant-numeric:tabular-nums}
  .part-axis .axis-separator{text-align:center;color:#98a0aa}
  .part-axis .axis-delta{color:#6f7885;font:10.5px/1 Consolas,monospace;text-align:right}
  .part-edit-actions{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:5px;margin-top:8px}
  .part-edit-help{margin:7px 0 0;color:#727b88;font-size:10.5px;line-height:14px}
  .part-production-data{margin-top:8px;padding-top:7px;border-top:1px solid #edf0f3}
  .part-production-data b{display:block;margin-bottom:3px;font-size:10.5px}
  .part-production-data p{margin:2px 0;color:#626c79;font-size:10.5px;line-height:14px}
  .part-local-actions{display:flex;flex-direction:column;align-items:flex-start;gap:1px;
    margin-top:8px;padding-top:7px;border-top:1px solid #edf0f3}
  .part-local-actions button{padding:4px 0;border:0;background:transparent;text-align:left;
    color:#4f5968;font-size:11px}
  .part-local-actions button:hover{color:var(--accent);background:transparent}
  .part-local-actions #ovDelete{color:var(--bad)}
  #partChatRow[hidden]{display:none}
  #fs_part[aria-busy="true"] input,#fs_part[aria-busy="true"] button{cursor:wait}
  #main{--chat-stack-height:133px;--viewport-status-height:24px;
    grid-column:2;grid-row:1;position:relative;min-width:0;min-height:0;
    container-type:inline-size}
  #rightside{grid-column:3;grid-row:1;min-width:0;width:100%;background:var(--card);
    border-left:1px solid var(--line);overflow-y:auto;padding:0 12px 12px;
    opacity:1;visibility:visible;transition:opacity .12s ease}
  #rightPanelHead{position:sticky;top:0;z-index:3;display:flex;align-items:center;gap:8px;
    min-height:48px;margin:0 -12px;padding:0 8px 0 14px;background:var(--card)}
  #rightPanelHead b{flex:1;font-size:12.5px}
  #rightPanelClose{display:grid;place-items:center;width:32px;height:32px;padding:0;border-color:transparent}
  #rightPanelTabs{position:sticky;top:48px;z-index:3;display:grid;
    grid-template-columns:repeat(3,minmax(0,1fr));margin:0 -12px 6px;padding:0 8px;
    background:var(--card);border-bottom:1px solid var(--line)}
  #rightPanelTabs button{min-width:0;height:36px;padding:0 5px;border:0;border-bottom:2px solid transparent;
    border-radius:0;background:transparent;color:#66707e;font-size:11px;white-space:nowrap;
    overflow:hidden;text-overflow:ellipsis}
  #rightPanelTabs button:hover{background:#f5f7f9;color:#303947}
  #rightPanelTabs button[aria-selected="true"]{border-bottom-color:var(--accent);color:#245eae;
    background:transparent;font-weight:600}
  #rightPanelTabs button:focus-visible{position:relative;z-index:1;outline:2px solid var(--accent);
    outline-offset:-2px}
  .right-panel-view[hidden]{display:none}
  .right-panel-view{min-width:0;padding-bottom:8px}
  #rightside .right-panel-view>fieldset{border:0;border-bottom:1px solid #edf0f3;
    border-radius:0;margin:0;padding:11px 2px 13px}
  #rightside .right-panel-view>fieldset>legend{margin:0 0 7px;padding:0;color:#303947;
    font-size:12px;line-height:18px;font-weight:600;text-transform:none}
  #rightside .right-panel-view>details{margin:0;padding:10px 2px 13px;border-bottom:1px solid #edf0f3}
  #rightside .right-panel-view>details>summary{padding:2px 0;color:#4f5968;font-size:11.5px;
    font-weight:600;cursor:pointer}
  /* UX-026: the product parameters read as a compact engineering sheet, not a
     stack of generic form rows. These selectors stay scoped to the Parameters
     mode so BOM, export, sections and the left panel keep their contracts. */
  #rightViewProperties .parameter-section{min-width:0}
  #rightViewProperties .dimension-grid{display:grid;
    grid-template-columns:repeat(3,minmax(0,1fr));gap:6px}
  #rightViewProperties .dimension-field,#rightViewProperties .parameter-field{min-width:0}
  #rightViewProperties .dimension-field>label,#rightViewProperties .parameter-field>label,
    #rightViewProperties .material-row>label,#rightViewProperties .parameter-search>label{
    display:flex;align-items:baseline;gap:4px;min-width:0;margin:0 0 4px;color:#687180;
    font-size:10.5px;line-height:14px}
  #rightViewProperties .dimension-axis{color:#35404e;font-size:11px;font-weight:700}
  #rightViewProperties .dimension-name{min-width:0;overflow:hidden;text-overflow:ellipsis;
    white-space:nowrap}
  #rightViewProperties .unit-field{display:grid;grid-template-columns:minmax(0,1fr) auto;
    align-items:center;gap:4px;min-width:0}
  #rightViewProperties .unit-field>span{color:#7a8390;font-size:9.5px;line-height:1;
    white-space:nowrap}
  #rightViewProperties input[type=number],#rightViewProperties input[type=text],
    #rightViewProperties select{min-width:0;max-width:none;height:30px;border-radius:4px;
    font-size:12px;font-variant-numeric:tabular-nums}
  #rightViewProperties input[type=number]{font-family:Consolas,monospace}
  #rightViewProperties .material-stack{display:grid;gap:7px}
  #rightViewProperties .material-row{display:grid;grid-template-columns:72px minmax(0,1fr);
    align-items:center;gap:8px;min-width:0}
  #rightViewProperties .material-row>label{margin:0}
  #rightViewProperties .material-value{display:grid;grid-template-columns:minmax(0,1fr) 24px;
    align-items:center;gap:5px;min-width:0}
  #rightViewProperties .material-value .swatch{width:24px;height:24px;border-radius:3px}
  #rightViewProperties .parameter-grid{display:grid;
    grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;margin-top:8px}
  #rightViewProperties .parameter-search{min-width:0;margin-top:9px;padding-top:8px;
    border-top:1px solid #edf0f3}
  #rightViewProperties #decorList{max-width:100%;border-radius:4px}
  #rightViewProperties #decorHint{margin-top:5px;color:#757e8a;font-size:10px;line-height:14px}
  #rightViewProperties .construction-type{display:grid;grid-template-columns:72px minmax(0,1fr);
    align-items:center;gap:8px;margin:0}
  #rightViewProperties .construction-type>label{color:#687180;font-size:10.5px}
  #rightViewProperties #fs_arch{padding-top:9px}
  #rightViewProperties #archFields{display:grid;gap:6px}
  #rightViewProperties #archFields .parameter-row{display:grid;
    grid-template-columns:minmax(0,1fr) minmax(96px,46%);align-items:center;gap:8px;margin:0}
  #rightViewProperties #archFields .parameter-row>label{min-width:0;color:#687180;
    font-size:10.5px;line-height:14px}
  #rightViewProperties #archFields input[type=checkbox]{justify-self:start;width:18px;height:18px;
    margin:0;accent-color:var(--accent)}
  #rightViewProperties #fs_support{padding-top:9px}
  #rightViewProperties .parameter-section input:focus-visible,
    #rightViewProperties .parameter-section select:focus-visible{
    outline:2px solid var(--accent);outline-offset:1px}
  #rightPanelClose svg,#rightRail svg{width:18px;height:18px;fill:none;stroke:currentColor;
    stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}
  #rightRail{grid-column:3;grid-row:1;z-index:4;display:flex;flex-direction:column;
    align-items:center;gap:6px;padding:8px 5px;background:var(--card);border-left:1px solid var(--line);
    opacity:0;visibility:hidden;pointer-events:none;transition:opacity .12s ease}
  #rightRail button{display:grid;place-items:center;width:36px;height:36px;padding:0;
    color:#596273;border-color:transparent;background:transparent}
  #rightRail button:hover{color:var(--accent);background:#eef4ff}
  #rightRail button.is-active{color:#245eae;background:#eef4ff;
    box-shadow:inset 2px 0 0 var(--accent)}
  #rightRail button:focus-visible,#rightPanelClose:focus-visible{
    outline:2px solid var(--accent);outline-offset:1px}
  #app.right-collapsed #rightside{opacity:0;visibility:hidden;pointer-events:none;overflow:hidden}
  #app.right-collapsed #rightRail{opacity:1;visibility:visible;pointer-events:auto}
  #rightside fieldset{scroll-margin-top:94px}
  .sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;
    clip:rect(0,0,0,0);white-space:nowrap;border:0}
  #chatImgs{display:flex;gap:5px;flex-wrap:wrap;margin:4px 0}
  #chatImgs .chip{position:relative}
  #chatImgs .chip img{width:44px;height:44px;object-fit:cover;border-radius:4px;
    border:1px solid var(--line);display:block}
  #chatImgs .chip-remove{position:absolute;top:-5px;right:-5px;display:grid;place-items:center;
    width:18px;height:18px;padding:0;border-radius:50%;border:1px solid #fff;
    background:#343a44;color:#fff}
  #chatImgs .chip-remove svg{width:10px;height:10px}
  #chatMsg.drop{outline:2px dashed var(--accent);outline-offset:2px}
  #partChatRow{display:flex;gap:5px;margin-top:8px;border-top:1px solid var(--line);padding-top:8px}
  #partChat{flex:1;padding:4px 6px;border:1px solid var(--line);border-radius:6px;font-size:12px}
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
  /* MEB-098: controls are grouped by the decision they change. The wrapper never
     blocks orbit/raycast outside the controls themselves. */
  #viewportTopbar{position:absolute;top:10px;left:12px;right:12px;z-index:7;
    display:flex;align-items:flex-start;justify-content:space-between;gap:12px;
    pointer-events:none}
  #tabs,#views{display:flex;align-items:center;min-height:36px;padding:3px;
    border:1px solid #d9dee5;border-radius:4px;background:#fff;pointer-events:auto}
  #tabs{gap:1px}
  #tabs button,#views .vw{display:flex;align-items:center;justify-content:center;gap:5px;
    height:28px;margin:0;padding:0 9px;border:0;border-radius:2px;background:transparent;
    color:#4e5867;font-size:11.5px;line-height:1;white-space:nowrap}
  #tabs button:hover,#views .vw:hover{background:#f0f3f6;color:#252b34}
  #tabs button.on,#views .vw.on{background:#edf4ff;color:#245fbf;
    box-shadow:inset 0 -2px 0 var(--accent)}
  #tabs button:focus-visible,#views .vw:focus-visible,#hud button:focus-visible,
    #hud input:focus-visible{outline:2px solid var(--accent);outline-offset:1px}
  #tabs button:disabled{color:#a1a8b2;background:transparent}
  #tabs .viewport-icon,#hud .viewport-icon{width:15px;height:15px;flex:0 0 15px;
    fill:none;stroke:currentColor;stroke-width:1.7;stroke-linecap:round;
    stroke-linejoin:round}
  #tabs .toolbar-separator{width:1px;height:20px;margin:0 2px;background:#dfe3e8}
  #btnPrint{padding:0 7px!important}
  #views{justify-content:flex-end;gap:1px}
  #views .viewport-group-label{padding:0 6px 0 4px;color:#7a8390;font-size:10px;
    font-weight:600;letter-spacing:.04em;text-transform:uppercase}
  #views .vw{padding:0 7px;font-size:11px}
  #views[hidden],#hud[hidden]{display:none!important}
  #draw{position:absolute;inset:48px 12px 12px;z-index:4;background:#fff;border:1px solid var(--line);
    border-radius:8px;overflow:auto;display:none;
    padding:8px 8px calc(var(--chat-stack-height) + var(--viewport-status-height) + 14px)}
  /* AKD-217: каталог изделий */
  #catalog{position:absolute;inset:0;z-index:8;display:none;flex-direction:column;
    background:var(--bg);padding:14px 18px;overflow:hidden}
  #catalog.on{display:flex}
  #catHead{display:flex;gap:10px;align-items:center;margin-bottom:10px}
  #catHead input{flex:1;max-width:360px;padding:6px 10px;border:1px solid var(--line);border-radius:8px}
  #catCats{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:12px}
  .catchip{padding:4px 12px;border:1px solid var(--line);border-radius:16px;background:#fff;
    cursor:pointer;font-size:12px}
  .catchip.on{background:var(--accent);border-color:var(--accent);color:#fff}
  #catGrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:12px;
    overflow-y:auto;padding-bottom:20px}
  .catCard{background:var(--card);border:1px solid var(--line);border-radius:10px;
    cursor:pointer;overflow:hidden;transition:box-shadow .15s}
  .catCard:hover{box-shadow:0 4px 14px rgba(0,0,0,.12)}
  .catCard .img{height:130px;background:linear-gradient(180deg,#f8fafc,#e6ebf1);
    display:flex;align-items:center;justify-content:center;color:var(--mut);font-size:30px}
  .catCard .img img{width:100%;height:100%;object-fit:contain;padding:6px;box-sizing:border-box}
  .catCard .nm{padding:7px 10px 2px;font-weight:600;font-size:12.5px;
    white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .catCard .sub{padding:0 10px 8px;font-size:11px;color:var(--mut)}
  .catCard .dr{display:inline-block;background:#c78a2b;color:#fff;border-radius:8px;
    padding:0 6px;font-size:10px;margin-left:4px}
  /* AKD-214: пустое рабочее пространство нового изделия */
  #emptyState{position:absolute;inset:0;z-index:6;display:none;align-items:center;justify-content:center;background:var(--bg)}
  #emptyState.on{display:flex}
  #emptyState .es-box{text-align:center;border:2px dashed var(--line);border-radius:14px;
    padding:36px 48px;background:var(--card)}
  #emptyState .es-title{font-size:20px;color:var(--ink);margin-bottom:8px}
  #emptyState .es-hint{font-size:13px;color:var(--mut);margin-bottom:16px;line-height:1.7}
  #emptyState.drop .es-box{border-color:var(--accent);background:#eef4ff}
  #view3d{position:absolute;inset:0}
  /* AKD-262 / MEB-098: compact opaque CAD HUD. Layers and model state are
     intentionally separate because they have different semantics. */
  #hud{position:absolute;right:12px;top:52px;z-index:6;width:212px;
    border:1px solid #d9dee5;border-radius:4px;background:#fff;color:#303743;
    font-size:11px;pointer-events:auto}
  .hud-section{padding:7px 8px 8px}
  .hud-section+.hud-section{border-top:1px solid #e4e7eb}
  .hud-section-head{display:flex;align-items:center;gap:6px;min-height:18px;margin-bottom:4px}
  .hud-section-head>span{flex:1;color:#5e6876;font-size:10px;font-weight:700;
    letter-spacing:.045em;text-transform:uppercase}
  .hud-section-head output{color:#7a8390;font-size:10px;font-variant-numeric:tabular-nums}
  .hud-layer-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:0 8px}
  #hud .hud-layer{display:flex;align-items:center;gap:5px;min-width:0;height:23px;
    cursor:pointer;user-select:none;white-space:nowrap}
  #hud .hud-layer input{width:14px;height:14px;margin:0;accent-color:var(--accent)}
  #hud .hud-layer span{min-width:0;overflow:hidden;text-overflow:ellipsis}
  #hud .hud-layer.is-wide{grid-column:1/-1}
  .hud-actions{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:4px}
  #hud .hud-actions button{display:flex;align-items:center;justify-content:center;gap:4px;
    min-width:0;height:28px;padding:0 5px;border-radius:3px;font-size:10.5px;white-space:nowrap}
  .explode-control{display:grid;grid-template-columns:auto minmax(0,1fr) auto;
    align-items:center;gap:6px;height:29px;margin-top:4px;color:#4f5967}
  .explode-control>span{font-size:10.5px}
  .explode-control input{width:100%;min-width:0;margin:0;accent-color:var(--accent)}
  .explode-control output{min-width:27px;color:#66707d;font:10px/1 Consolas,monospace;
    text-align:right;font-variant-numeric:tabular-nums}
  @container (max-width:1099px){
    #tabs button{padding:0 7px}
    #tabs .print-label{position:absolute;width:1px;height:1px;margin:-1px;overflow:hidden;
      clip:rect(0,0,0,0);white-space:nowrap}
    #viewportTopbar.is-2d #tabs .print-label{position:static;width:auto;height:auto;margin:0;
      overflow:visible;clip:auto;white-space:nowrap}
    #views .viewport-group-label{position:absolute;width:1px;height:1px;margin:-1px;
      overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap}
    #views .vw{padding:0 6px}
    #hud{width:200px}
  }
  @container (max-width:700px){
    #viewportTopbar{gap:6px}
    #tabs button{padding:0 6px}
    #tabs .viewport-icon{display:none}
    #views .vw{padding:0 5px;font-size:10.5px}
    #hud{width:188px}
    #hud .hud-actions button span{position:absolute;width:1px;height:1px;margin:-1px;
      overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap}
  }
  @container (max-width:440px){
    #viewportTopbar{flex-wrap:wrap}
    #views{margin-left:auto}
    #hud{top:92px;width:176px}
  }
  /* узкие ноутбуки: левую панель уплотняем, инспектор по умолчанию задаёт JS */
  @media (max-width:1440px){
    #app{--side-width:288px;--inspector-width:330px;
      grid-template-columns:var(--side-width) minmax(0,1fr) var(--inspector-width)}
    #app.right-collapsed{
      grid-template-columns:var(--side-width) minmax(0,1fr) var(--rail-width)}
  }
  @media (max-width:1200px){
    #app{--side-width:270px;--inspector-width:300px;
      grid-template-columns:var(--side-width) minmax(0,1fr) var(--inspector-width)}
    #app.right-collapsed{
      grid-template-columns:var(--side-width) minmax(0,1fr) var(--rail-width)}
    #stats{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px 12px}
  }
  @media (min-width:1600px){
    #app{--side-width:340px;--inspector-width:360px;
      grid-template-columns:var(--side-width) minmax(0,1fr) var(--inspector-width)}
    #app.right-collapsed{
      grid-template-columns:var(--side-width) minmax(0,1fr) var(--rail-width)}
  }
  @media (prefers-reduced-motion:reduce){
    #rightside,#rightRail,#chatStatusTray,#chatShortcut,#tabs button,#views .vw{transition:none}
    #fs_chat.is-busy #chatProgress::after{animation:none;transform:none;width:100%}
  }
  #viewportStatus{position:absolute;left:0;right:0;bottom:0;z-index:6;
    display:flex;align-items:center;height:var(--viewport-status-height);min-width:0;
    border-top:1px solid #d9dee5;background:#fff;color:#55606e;font-size:10.5px;
    line-height:1;pointer-events:none}
  .viewport-status-segment{display:flex;align-items:center;min-width:0;height:100%;
    padding:0 9px;white-space:nowrap}
  .viewport-status-segment+.viewport-status-segment{border-left:1px solid #e2e6ea}
  #viewportModelStatus{flex:0 0 auto;gap:6px;color:#3f4956;font-weight:600}
  #viewportModelMark{width:6px;height:6px;flex:0 0 6px;border-radius:50%;background:#7f8996}
  #viewportStatus[data-tone="ready"] #viewportModelMark{background:var(--ok)}
  #viewportStatus[data-tone="pending"] #viewportModelMark,
    #viewportStatus[data-tone="busy"] #viewportModelMark{background:var(--accent)}
  #viewportStatus[data-tone="warning"] #viewportModelMark{background:#c78a2b}
  #viewportStatus[data-tone="error"] #viewportModelMark{background:var(--bad)}
  #viewportModelStateShort{display:none}
  #viewportSelection{flex:0 1 auto;gap:5px;max-width:38%;color:#596373}
  #viewportSelection[hidden]{display:none}
  #viewportSelection>span{color:#7a8390}
  #viewportSelectionName{min-width:0;overflow:hidden;text-overflow:ellipsis;color:#3f4855;
    font-weight:600}
  #viewportHint{flex:1 1 auto;min-width:0;color:#6e7784;overflow:hidden;text-overflow:ellipsis}
  #viewportUnits{flex:0 0 auto;margin-left:auto;color:#4f5967;font-weight:600;
    font-variant-numeric:tabular-nums}
  @container (max-width:999px){
    #viewportModelStateLong{display:none}
    #viewportModelStateShort{display:inline}
    #viewportSelection{max-width:42%}
    .viewport-status-segment{padding-inline:7px}
  }
  @container (max-width:700px){
    #viewportHint{display:none}
    #viewportSelection{flex:1 1 auto;max-width:none}
    #viewportStatus[data-tone="error"] #viewportSelection,
      #viewportStatus[data-tone="busy"] #viewportSelection{display:none}
  }
  #toast{position:absolute;left:50%;bottom:calc(44px + var(--chat-stack-height));
         transform:translateX(-50%);z-index:9;
         background:#1a1d21;color:#fff;padding:7px 14px;border-radius:8px;font-size:12.5px;
         opacity:0;transition:opacity .25s;pointer-events:none;max-width:80%}
  details{margin-top:8px} textarea{width:100%;height:170px;font:11px/1.4 Consolas,monospace}
  #bom table{width:100%;table-layout:fixed;border-collapse:collapse;font-size:11.5px}
  #bom td{border-bottom:1px solid var(--line);padding:3px 4px;overflow-wrap:anywhere}
  #estTable{width:100%;border-collapse:collapse;font-size:11px}
  #estTable td{border-bottom:1px solid var(--line);padding:2px 3px;vertical-align:top}
  #estTable td:last-child{text-align:right;white-space:nowrap}
  #estTable tr.grp td{background:var(--bg);font-weight:600;color:var(--mut);
                      text-transform:uppercase;font-size:10px}
  .swatch{flex:0 0 18px;height:18px;border-radius:4px;border:1px solid var(--line);
          background:#c9a06a}
  #draw rect.sel{stroke:#2b62c4 !important;stroke-width:2.4 !important;
                 fill:#2b62c433 !important}
  #decorList{max-height:170px;overflow-y:auto;display:none;flex-direction:column;gap:2px;
             border:1px solid var(--line);border-radius:6px;padding:3px;margin-top:4px}
  .ditem{display:grid;grid-template-columns:minmax(0,1fr) auto;align-items:center;gap:3px;
         padding:2px;border-radius:3px;font-size:11.5px}
  .ditem:hover{background:var(--bg)}
  .ditem .decor-body{display:grid;grid-template-columns:16px minmax(0,1fr) auto;
         align-items:center;gap:6px;min-width:0;padding:3px;border:0;background:transparent;
         text-align:left}
  .ditem .decor-body:hover{background:#eef1f4}
  .ditem .decor-body:focus-visible,.ditem .fb:focus-visible{outline:2px solid var(--accent);
         outline-offset:-1px}
  .ditem .sw{flex:0 0 16px;height:16px;border-radius:3px;border:1px solid var(--line)}
  .ditem .nm{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .ditem .fb{width:26px;height:24px;padding:0;font-size:10px}
  #operationLog{display:flex;flex:1 1 220px;flex-direction:column;min-height:150px;
    padding:11px 12px 14px;border-top:1px solid var(--line);overflow:hidden}
  #operationLog[hidden]{display:none}
  #operationLog .side-section-head{margin-bottom:3px}
  #operationLogHint{margin-bottom:7px;color:var(--mut);font-size:10.5px;line-height:14px}
  #chatlog{display:block;flex:1;min-height:0;margin:0;padding-right:3px;overflow-y:auto;
    overscroll-behavior:contain;scrollbar-gutter:stable}
  #chatlog:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
  #chatlog:empty{display:none}
  .operation-record{position:relative;padding:9px 0 10px 10px;border-top:1px solid #e3e7ec;
    color:var(--ink)}
  .operation-record:last-child{border-bottom:1px solid #e3e7ec}
  .operation-record::before{content:"";position:absolute;left:0;top:10px;bottom:10px;width:2px;
    background:#9aa5b3}
  .operation-record.is-pending::before,.operation-record.is-answer::before{background:var(--accent)}
  .operation-record.is-applied::before{background:var(--ok)}
  .operation-record.is-warning::before{background:#c78a2b}
  .operation-record.is-error::before{background:var(--bad)}
  .operation-record.is-undone::before{background:#a9b1bc}
  .operation-head{display:flex;align-items:center;gap:6px;min-width:0;margin-bottom:4px}
  .operation-state{display:flex;align-items:center;gap:5px;min-width:0;font-size:10.5px;
    line-height:14px;font-weight:600;color:#4b5563}
  .operation-state::before{content:"";width:6px;height:6px;flex:0 0 6px;border-radius:50%;
    background:#9aa5b3}
  .is-pending .operation-state::before,.is-answer .operation-state::before{background:var(--accent)}
  .is-applied .operation-state::before{background:var(--ok)}
  .is-warning .operation-state::before{background:#c78a2b}
  .is-error .operation-state::before{background:var(--bad)}
  .operation-time{margin-left:auto;color:#8a929e;font-size:10px;line-height:14px;
    font-variant-numeric:tabular-nums}
  .operation-command{display:-webkit-box;margin:0 0 3px;font-size:12px;line-height:16px;
    font-weight:600;overflow:hidden;overflow-wrap:anywhere;-webkit-box-orient:vertical;
    -webkit-line-clamp:3}
  .operation-context{margin-bottom:7px;color:#697382;font-size:10.5px;line-height:14px;
    white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .operation-summary{margin:0 0 6px;color:#414a57;font-size:11.5px;line-height:16px;
    white-space:pre-wrap;overflow-wrap:anywhere}
  .operation-summary[hidden],.operation-changes[hidden],.operation-check[hidden],
    .operation-actions[hidden],.operation-technical[hidden],.operation-more[hidden]{display:none}
  .operation-changes{margin:2px 0 6px;border-top:1px solid #edf0f3}
  .operation-change{padding:5px 0;border-bottom:1px solid #edf0f3}
  .operation-change-label{display:block;margin-bottom:1px;color:#727b88;font-size:10px;line-height:13px}
  .operation-change-values{display:flex;align-items:baseline;gap:5px;min-width:0;
    font-size:11.5px;line-height:15px;font-variant-numeric:tabular-nums}
  .operation-before{min-width:0;color:#747d89;overflow-wrap:anywhere}
  .operation-arrow{flex:0 0 auto;color:#9aa3af}
  .operation-after{min-width:0;font-weight:600;color:#27313d;overflow-wrap:anywhere}
  .operation-change-plain{color:#3f4855;font-size:11.5px;line-height:16px;overflow-wrap:anywhere}
  .operation-more{margin:-1px 0 6px;color:#6f7885;font-size:10.5px;line-height:14px}
  .operation-check{display:flex;align-items:flex-start;gap:6px;margin:5px 0 7px;color:#4f5967;
    font-size:10.5px;line-height:14px}
  .operation-check::before{content:"";width:6px;height:6px;flex:0 0 6px;margin-top:4px;
    border-radius:50%;background:var(--ok)}
  .operation-check.warning::before{background:#c78a2b}
  .operation-check.error::before{background:var(--bad)}
  .operation-actions{display:flex;align-items:center;gap:2px;margin:4px -3px -3px}
  .operation-actions button{padding:4px 5px;border-color:transparent;background:transparent;
    color:#4f5968;font-size:10.5px;line-height:14px}
  .operation-actions button:hover{background:#eef4ff;color:var(--accent)}
  .operation-actions button:focus-visible{outline:2px solid var(--accent);outline-offset:0}
  .operation-actions .operation-undo{margin-left:auto}
  .operation-actions button[hidden]{display:none}
  .operation-technical{margin:7px 0 1px;padding:7px 8px;background:#f5f7f9;
    border-left:2px solid #cbd2db;color:#4b5563;font-size:10.5px;line-height:15px}
  .operation-tech-label{display:block;margin:5px 0 2px;color:#737c89;font-size:9.5px;
    line-height:12px;font-weight:600;text-transform:uppercase}
  .operation-tech-label:first-child{margin-top:0}
  .operation-raw{margin:0;max-height:120px;overflow:auto;white-space:pre-wrap;
    overflow-wrap:anywhere;font:10px/1.45 Consolas,monospace;color:#5e6875}
  .operation-record.is-undone .operation-command,.operation-record.is-undone .operation-changes{
    opacity:.62}
  @media (max-height:720px){
    #operationLog{flex-basis:180px;min-height:140px;max-height:50%}
    #operationLogHint{display:none}
  }
  @media (max-height:560px){
    #sidePinned{flex:0 1 auto;min-height:180px;overflow-y:auto;scrollbar-gutter:stable}
    #operationLog{min-height:120px}
  }
  #fs_chat{position:absolute;left:50%;bottom:calc(10px + var(--viewport-status-height));z-index:7;
    width:min(760px,calc(100% - 32px));
    min-inline-size:0;margin:0;padding:0;border:0;transform:translateX(-50%);pointer-events:none}
  #chatStatusTray{display:flex;align-items:center;gap:9px;width:max-content;max-width:100%;
    min-height:34px;margin:0 auto 7px;padding:6px 7px 6px 10px;border:1px solid var(--line);
    border-radius:9px;background:rgba(255,255,255,.97);box-shadow:0 5px 16px rgba(29,39,52,.12);
    opacity:0;transform:translateY(6px);visibility:hidden;transition:opacity .14s ease,transform .14s ease;
    pointer-events:none}
  #fs_chat.has-state #chatStatusTray{opacity:1;transform:translateY(0);visibility:visible;pointer-events:auto}
  #chatStateMark{width:7px;height:7px;flex:0 0 7px;border-radius:50%;background:#788493}
  #fs_chat.is-busy #chatStateMark{background:var(--accent)}
  #fs_chat.state-success #chatStateMark{background:var(--ok)}
  #fs_chat.state-error #chatStateMark{background:var(--bad)}
  #chatState{min-width:0;font-size:11.5px;line-height:16px;color:#3d4653;white-space:nowrap;
    overflow:hidden;text-overflow:ellipsis}
  #chatResultActions{display:flex;align-items:center;gap:3px;margin-left:2px;padding-left:5px;
    border-left:1px solid var(--line)}
  #chatResultActions[hidden],#chatUndoQuick[hidden]{display:none}
  #chatResultActions button{padding:4px 6px;border-color:transparent;background:transparent;
    color:#4f5968;font-size:11px}
  #chatResultActions button:hover{background:#eef4ff;color:var(--accent)}
  #chatSurface{position:relative;pointer-events:auto;border:1px solid #cbd2dc;border-radius:13px;
    background:rgba(255,255,255,.97);box-shadow:0 8px 24px rgba(29,39,52,.14);overflow:visible;
    transition:border-color .12s ease,box-shadow .12s ease}
  #fs_chat:focus-within #chatSurface{border-color:#93b5ec;box-shadow:0 8px 24px rgba(29,39,52,.14),
    0 0 0 2px rgba(59,130,246,.10)}
  #chatProgress{position:absolute;left:12px;right:12px;top:-1px;height:2px;overflow:hidden;
    border-radius:2px;opacity:0;background:#dfe7f2}
  #chatProgress::after{content:"";display:block;width:34%;height:100%;background:var(--accent);
    transform:translateX(-130%)}
  #fs_chat.is-busy #chatProgress{opacity:1}
  #fs_chat.is-busy #chatProgress::after{animation:chat-progress 1.05s linear infinite}
  @keyframes chat-progress{to{transform:translateX(390%)}}
  #chatMeta{display:flex;align-items:center;gap:7px;padding:7px 7px 0 9px}
  #chatContext{display:flex;align-items:center;gap:5px;min-width:0;max-width:58%;height:25px;
    padding:0 7px;border:1px solid #d9dee5;border-radius:6px;background:#f4f6f8;color:#4c5665}
  #chatContext.selected{border-color:#b8cdf3;background:#eef4ff;color:#245eae}
  #chatContext .context-copy{min-width:0}
  #chatContext .context-copy span{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0,0,0,0)}
  #chatContextLabel{display:block;max-width:100%;font-size:10.5px;line-height:14px;font-weight:600;
    white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  #chatMetaTitle{margin-left:auto;color:var(--mut);font-size:10.5px;line-height:14px;white-space:nowrap}
  #aiSettings{position:relative;margin:0 0 0 1px}
  #aiSettings>summary{display:grid;place-items:center;width:26px;height:26px;border-radius:6px;
    color:#596273;cursor:pointer;list-style:none}
  #aiSettings>summary::-webkit-details-marker{display:none}
  #aiSettings>summary:hover{background:#eef1f4;color:var(--accent)}
  #aiSettings[open]>summary{background:#eef4ff;color:var(--accent)}
  #aiSettings .ai-settings-body{position:absolute;right:0;bottom:34px;width:250px;padding:9px;
    border:1px solid var(--line);border-radius:8px;background:var(--card);
    box-shadow:0 8px 22px rgba(29,39,52,.14)}
  #aiSettings label{display:block;margin-bottom:4px;font-size:10.5px;color:var(--mut)}
  #aiProvider{height:30px}
  #chatImgs{display:flex;gap:5px;flex-wrap:nowrap;margin:6px 9px 0;overflow-x:auto}
  #chatComposer{display:flex;align-items:flex-end;gap:5px;padding:4px 6px 7px 9px}
  #chatMsg{display:block;flex:1;min-width:0;width:auto;height:40px;min-height:40px;max-height:96px;
    resize:none;overflow-y:auto;padding:10px 5px 8px 0;border:0;border-radius:0;background:transparent;
    font:12.5px/1.45 Segoe UI,Arial,sans-serif}
  #chatMsg:focus{outline:0}
  .chat-actions{display:flex;align-items:center;gap:4px;flex:0 0 auto;padding-bottom:1px}
  #chatAttach{display:flex;align-items:center;gap:5px;height:34px;padding:0 7px;border-color:transparent;
    background:transparent;color:#596273}
  #chatAttach:hover{color:var(--accent);background:#eef4ff}
  #chatSend{display:flex;align-items:center;justify-content:center;gap:5px;min-width:104px;height:34px}
  #chatSend .chat-stop-icon{display:none}
  #chatSend.is-cancel{border-color:#d8a2a4;background:#fff5f4;color:#9f2d2f}
  #chatSend.is-cancel:hover{border-color:#c88386;background:#ffedeb;color:#8d2428}
  #chatSend.is-cancel .chat-send-icon{display:none}
  #chatSend.is-cancel .chat-stop-icon{display:block}
  #chatShortcut{position:absolute;right:12px;bottom:-24px;font-size:9.5px;line-height:12px;
    color:#757f8d;opacity:0;transition:opacity .12s ease;pointer-events:none}
  #fs_chat:focus-within #chatShortcut{opacity:1}
  #fs_chat:focus-within + #viewportStatus #viewportHint{visibility:hidden}
  #tokenCount{margin-top:6px;font-size:10.5px;line-height:15px}
  @container (max-width:620px){
    #chatMetaTitle{display:none}
    #chatContext{max-width:72%}
    #chatAttach span{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0,0,0,0)}
    #chatAttach{width:34px;padding:0;justify-content:center}
    #chatSend{min-width:42px;width:42px;padding:0}
    #chatSendLabel{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0,0,0,0)}
  }
</style></head><body>
<div id="app" class="right-collapsed">
<div id="side">
<div id="sideScroll">
<div id="sidePinned">
  <h1 id="studioBrand" class="studio-brand">
    <span class="studio-brand-lockup">
      <img id="studioBrandWordmark" class="studio-brand-wordmark"
        src="/assets/studio/akeda-studio-wordmark.png" width="520" height="84"
        decoding="async" alt="Akeda Studio">
      <img id="studioBrandMark" class="studio-brand-mark"
        src="/assets/studio/akeda-studio-mark.png" width="216" height="98"
        decoding="async" alt="" aria-hidden="true">
    </span>
    <span class="studio-brand-tagline">от ТЗ до производства</span>
  </h1>

  <fieldset id="fs_project"><legend>Проект</legend>
    <div class="row project-select"><label for="projSel">Изделие</label><select id="projSel"></select>
      <button id="projRen" class="icon-button" type="button" aria-label="Переименовать изделие"
        title="Переименовать изделие">
        <svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true">
          <path d="M4 20h4l10.5-10.5a2.1 2.1 0 0 0-4-4L4 16v4Z"/><path d="m13 7 4 4"/>
        </svg>
      </button>
    </div>
    <div class="row project-actions" style="gap:6px">
      <button id="projNew" type="button">
        <svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>
        Новое
      </button>
      <button id="projDup" type="button" title="Создать копию текущего изделия">
        <svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true">
          <rect x="8" y="8" width="11" height="11" rx="1"/><path d="M16 8V5H5v11h3"/>
        </svg>
        Копия
      </button>
      <button id="projCat" type="button" title="Открыть каталог изделий">
        <svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true">
          <path d="M4 6.5h6l2 2h8v10H4v-12Z"/>
        </svg>
        Каталог
      </button>
    </div>
  </fieldset>

  <section id="modelState" aria-labelledby="modelStateTitle">
    <div class="side-section-head">
      <span id="modelStateTitle">Состояние модели</span>
      <button id="btnUndo" type="button" disabled title="Отменить последнюю обратимую правку">
        <svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true">
          <path d="M9 7 5 11l4 4"/><path d="M5 11h8a6 6 0 0 1 6 6"/>
        </svg>
        Отменить
      </button>
    </div>
    <div class="badges" id="badges"></div>
    <div id="errors" role="alert"></div>
    <div id="stats"></div>
    <button id="btnFixAll" type="button" hidden
      title="До трёх проходов AI. Изменения применяются сразу; может потребоваться ручная проверка">
      <svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true">
        <circle cx="12" cy="12" r="8"/><path d="m8.5 12 2.2 2.2 4.8-5"/>
      </svg>
      Запустить автоисправление
    </button>
  </section>
</div>

  <fieldset id="fs_part" style="display:none" aria-busy="false">
    <legend class="part-section-head">
      <span>Выбранная деталь</span>
      <button id="partClearSelection" type="button" aria-label="Снять выбор"
        title="Снять выбор (Esc)">
        <svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="m7 7 10 10M17 7 7 17"/></svg>
      </button>
    </legend>
    <div id="partCard">
      <header class="part-identity">
        <b id="partName"></b>
        <span id="partKind"></span>
        <span id="partOrigin">От генератора</span>
      </header>
      <dl class="part-facts">
        <div><dt>Габарит XYZ</dt><dd id="partDimensions"></dd></div>
        <div><dt>Материал</dt><dd id="partMaterial"></dd></div>
        <div><dt>Кромка</dt><dd id="partEdges"></dd></div>
        <div><dt>Присадки</dt><dd id="partHoles"></dd></div>
      </dl>
      <div class="part-primary-actions">
        <button id="partCommandFocus" type="button" class="primary">
          <svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true">
            <path d="M5 6h14v9H9l-4 4V6Z"/><path d="M9 10h6"/>
          </svg>
          Изменить словами
        </button>
        <button id="ovDetail" type="button" title="Чертёж детали с размерами и присадками">Чертёж</button>
      </div>
      <p class="part-context-note">Нижняя команда уже адресована выбранной детали.</p>
      <details id="partExact" class="part-exact">
        <summary>Точные параметры</summary>
        <p class="part-exact-hint">Грани в координатах модели, мм</p>
        <div id="partEditStatus" role="status" aria-live="polite"></div>
        <div class="part-axis-grid">
          <label class="part-axis"><span>X</span>
            <input type="number" data-ov="x1" aria-label="X от" step="1"><span class="axis-separator">—</span>
            <input type="number" data-ov="x2" aria-label="X до" step="1"><span class="axis-delta" data-delta="x"></span>
          </label>
          <label class="part-axis"><span>Y</span>
            <input type="number" data-ov="y1" aria-label="Y от" step="1"><span class="axis-separator">—</span>
            <input type="number" data-ov="y2" aria-label="Y до" step="1"><span class="axis-delta" data-delta="y"></span>
          </label>
          <label class="part-axis"><span>Z</span>
            <input type="number" data-ov="z1" aria-label="Z от" step="1"><span class="axis-separator">—</span>
            <input type="number" data-ov="z2" aria-label="Z до" step="1"><span class="axis-delta" data-delta="z"></span>
          </label>
        </div>
        <div class="part-edit-actions">
          <button id="ovApply" type="button" class="primary" disabled>Применить</button>
          <button id="partEditCancel" type="button" disabled>Отменить ввод</button>
        </div>
        <p class="part-edit-help">Shift + перетаскивание перемещает деталь в 3D. После применения пересчитаются присадки и проверки.</p>
        <div class="part-production-data">
          <b>Производственные данные</b>
          <p id="partEdgesRaw"></p>
          <p id="partHolesRaw"></p>
        </div>
        <div class="part-local-actions">
          <button id="ovReset" type="button" hidden>Сбросить к результату генератора</button>
          <button id="ovDelete" type="button">Удалить деталь…</button>
        </div>
        <div id="partChatRow" hidden aria-hidden="true">
          <input id="partChat" type="hidden" tabindex="-1">
          <button id="partChatSend" type="button" hidden tabindex="-1">К команде</button>
        </div>
      </details>
    </div>
  </fieldset>
</div>
  <section id="operationLog" aria-labelledby="operationLogTitle" hidden>
    <div class="side-section-head">
      <span id="operationLogTitle">Изменения</span>
    </div>
    <div id="operationLogHint">Команды, результат и технические изменения этой сессии</div>
    <div id="chatlog" role="log" aria-live="polite" aria-relevant="additions text"
      aria-label="Изменения этой сессии" tabindex="0"></div>
  </section>
</div>

<div id="rightside" data-mode="properties">
  <div id="rightPanelHead">
    <b id="rightPanelTitle">Параметры изделия</b>
    <button id="rightPanelClose" type="button" aria-label="Свернуть правую панель"
      aria-controls="rightside" aria-expanded="true" title="Свернуть панель">
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m15 6-6 6 6 6"/></svg>
    </button>
  </div>

  <nav id="rightPanelTabs" role="tablist" aria-label="Разделы правой панели">
    <button id="rightTabProperties" type="button" role="tab" aria-selected="true"
      aria-controls="rightViewProperties" data-mode="properties">Параметры</button>
    <button id="rightTabComponents" type="button" role="tab" aria-selected="false"
      aria-controls="rightViewComponents" data-mode="components" tabindex="-1">Комплектация</button>
    <button id="rightTabProduction" type="button" role="tab" aria-selected="false"
      aria-controls="rightViewProduction" data-mode="production" tabindex="-1">Производство</button>
  </nav>

  <div id="rightViewProperties" class="right-panel-view" role="tabpanel"
    aria-labelledby="rightTabProperties">
    <fieldset id="fs_dims" class="parameter-section"><legend>Габариты</legend>
      <div class="dimension-grid" aria-label="Габариты изделия в миллиметрах">
        <div class="dimension-field">
          <label for="f_w"><span class="dimension-axis">W</span><span class="dimension-name">Ширина</span></label>
          <div class="unit-field"><input type="number" id="f_w" step="10"
            aria-describedby="f_w_unit"><span id="f_w_unit">мм</span></div>
        </div>
        <div class="dimension-field">
          <label for="f_d"><span class="dimension-axis">D</span><span class="dimension-name">Глубина</span></label>
          <div class="unit-field"><input type="number" id="f_d" step="10"
            aria-describedby="f_d_unit"><span id="f_d_unit">мм</span></div>
        </div>
        <div class="dimension-field">
          <label for="f_h"><span class="dimension-axis">H</span><span class="dimension-name">Высота</span></label>
          <div class="unit-field"><input type="number" id="f_h" step="10"
            aria-describedby="f_h_unit"><span id="f_h_unit">мм</span></div>
        </div>
      </div>
    </fieldset>

    <fieldset id="fs_material" class="parameter-section"><legend>Материал</legend>
      <div class="material-stack">
        <div class="material-row"><label for="f_color">Корпус</label>
          <div class="material-value"><input type="text" id="f_color">
            <span class="swatch" id="swCarcass" title="Цвет показа корпуса" aria-hidden="true"></span></div>
        </div>
        <div class="material-row"><label for="f_facade_color">Фасады</label>
          <div class="material-value"><input type="text" id="f_facade_color" placeholder="Как корпус">
            <span class="swatch" id="swFacade" title="Цвет показа фасадов" aria-hidden="true"></span></div>
        </div>
      </div>
      <div class="parameter-grid">
        <div class="parameter-field"><label for="f_code">Код декора</label>
          <input type="text" id="f_code"></div>
        <div class="parameter-field"><label for="f_t">Толщина плиты</label>
          <div class="unit-field"><input type="number" id="f_t" step="1"
            aria-describedby="f_t_unit"><span id="f_t_unit">мм</span></div></div>
      </div>
      <div class="parameter-search"><label for="decorQ">Найти в базе материалов</label>
        <input type="text" id="decorQ" placeholder="Дуб, белый, артикул…" autocomplete="off"
          aria-controls="decorList" aria-describedby="decorHint"></div>
      <div id="decorList" role="list" aria-live="polite"></div>
      <div class="mini" id="decorHint">Строка — корпус · «Ф» — фасады · ≈960 листовых материалов</div>
    </fieldset>

    <fieldset id="fs_archetype" class="parameter-section"><legend>Конструкция</legend>
      <div class="construction-type"><label for="archSel">Тип изделия</label><select id="archSel"></select></div>
    </fieldset>

    <fieldset id="fs_arch" class="parameter-section" style="display:none"><legend>Параметры конструкции</legend>
      <div id="archFields"></div>
    </fieldset>

    <fieldset id="fs_support" class="parameter-section"><legend>Установка</legend>
      <div class="parameter-grid">
        <div class="parameter-field"><label for="f_legs">Высота опор</label>
          <div class="unit-field"><input type="number" id="f_legs" step="1"
            aria-describedby="f_legs_unit"><span id="f_legs_unit">мм</span></div></div>
        <div class="parameter-field"><label for="f_gap">Фасадный зазор</label>
          <div class="unit-field"><input type="number" id="f_gap" step="0.5"
            aria-describedby="f_gap_unit"><span id="f_gap_unit">мм</span></div></div>
      </div>
    </fieldset>

    <fieldset id="fs_sections"><legend>Секции</legend>
      <div id="sections"></div>
      <button id="addSec">+ секция</button>
    </fieldset>

    <details id="fs_raw"><summary class="mini">ParamSpec (raw JSON)</summary>
      <textarea id="rawspec" spellcheck="false"></textarea>
      <button id="applyRaw">Применить JSON</button>
    </details>
  </div>

  <div id="rightViewComponents" class="right-panel-view" role="tabpanel"
    aria-labelledby="rightTabComponents" hidden inert>
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
  </div>

  <div id="rightViewProduction" class="right-panel-view" role="tabpanel"
    aria-labelledby="rightTabProduction" hidden inert>
    <fieldset id="fs_export"><legend>Экспорт</legend>
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
  </div>
</div>

<nav id="rightRail" aria-label="Разделы параметров изделия">
  <button id="rightRailProperties" type="button" aria-label="Открыть параметры изделия"
    aria-controls="rightside" aria-expanded="false" title="Параметры изделия">
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M4 7h10M18 7h2M4 17h2M10 17h10M14 4v6M6 14v6"/>
    </svg>
    <span class="sr-only">Параметры изделия</span>
  </button>
  <button id="rightRailComponents" type="button" aria-label="Открыть комплектацию"
    aria-controls="rightside" aria-expanded="false" title="Комплектация">
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="m12 3 8 4-8 4-8-4 8-4Z"/><path d="m4 12 8 4 8-4M4 17l8 4 8-4"/>
    </svg>
    <span class="sr-only">Комплектация</span>
  </button>
  <button id="rightRailProduction" type="button" aria-label="Открыть производство"
    aria-controls="rightside" aria-expanded="false" title="Производство">
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M3 21V10l6 3V9l6 4V5h6v16H3Z"/><path d="M17 9h2M7 17h2M12 17h2M17 17h2"/>
    </svg>
    <span class="sr-only">Производство</span>
  </button>
</nav>

<div id="main">
  <div id="view3d"></div>
  <div id="viewportTopbar">
    <div id="tabs" role="tablist" aria-label="Режим рабочего поля">
      <button id="tab3d" class="on" type="button" role="tab" aria-selected="true"
        aria-controls="view3d" title="Интерактивная 3D-модель">
        <svg class="viewport-icon" viewBox="0 0 24 24" aria-hidden="true">
          <path d="m12 3 8 4.5v9L12 21l-8-4.5v-9L12 3Z"/><path d="m4 7.5 8 4.5 8-4.5M12 12v9"/>
        </svg>
        3D
      </button>
      <button id="tabDraw" type="button" role="tab" aria-selected="false"
        aria-controls="draw" title="Технический чертёж изделия">
        <svg class="viewport-icon" viewBox="0 0 24 24" aria-hidden="true">
          <path d="M5 3h10l4 4v14H5V3Z"/><path d="M15 3v5h5M8 13h8M8 17h5"/>
        </svg>
        Чертёж
      </button>
      <button id="tabNest" type="button" role="tab" aria-selected="false"
        aria-controls="draw" title="Раскрой листовых материалов">
        <svg class="viewport-icon" viewBox="0 0 24 24" aria-hidden="true">
          <rect x="3" y="5" width="18" height="14" rx="1"/><path d="M9 5v7h12M3 14h10v5"/>
        </svg>
        Раскрой
      </button>
      <span class="toolbar-separator" aria-hidden="true"></span>
      <button id="btnPrint" type="button" disabled aria-label="Печать чертежа или раскроя"
        title="Доступно в чертеже и раскрое">
        <svg class="viewport-icon" viewBox="0 0 24 24" aria-hidden="true">
          <path d="M7 8V3h10v5M7 17H4v-7h16v7h-3"/><path d="M7 14h10v7H7v-7Z"/>
        </svg>
        <span class="print-label">Печать</span>
      </button>
    </div>
    <div id="views" role="toolbar" aria-label="Ракурс 3D-модели">
      <span class="viewport-group-label">Вид</span>
      <button class="vw" type="button" data-view="axon" aria-pressed="false"
        title="Аксонометрия без перспективы">Аксон</button>
      <button class="vw on" type="button" data-view="persp" aria-pressed="true"
        title="Перспектива три четверти">Персп.</button>
      <button class="vw" type="button" data-view="top" aria-pressed="false"
        title="Вид сверху">Сверху</button>
      <button class="vw" type="button" data-view="front" aria-pressed="false"
        title="Вид спереди">Спереди</button>
      <button class="vw" type="button" data-view="left" aria-pressed="false"
        title="Вид слева">Слева</button>
    </div>
  </div>
  <div id="hud" aria-label="Инструменты отображения 3D-модели">
    <section class="hud-section" aria-labelledby="hudLayersTitle">
      <div class="hud-section-head">
        <span id="hudLayersTitle">Отображение</span>
        <output id="layerCount" aria-live="polite">4 / 5</output>
      </div>
      <div class="hud-layer-grid">
        <label class="hud-layer"><input type="checkbox" id="cbHoles" checked><span>Присадки</span></label>
        <label class="hud-layer"><input type="checkbox" id="cbHw" checked><span>Фурнитура</span></label>
        <label class="hud-layer"><input type="checkbox" id="cbTex" checked><span>Текстура</span></label>
        <label class="hud-layer"><input type="checkbox" id="cbDims" checked><span>Размеры</span></label>
        <label class="hud-layer is-wide"><input type="checkbox" id="cbXray"><span>Прозрачность</span></label>
      </div>
    </section>
    <section class="hud-section" aria-labelledby="hudModelTitle">
      <div class="hud-section-head"><span id="hudModelTitle">Модель</span></div>
      <div class="hud-actions" role="group" aria-label="Открытие фасадов и ящиков">
        <button id="btnOpenAll" type="button" title="Открыть все фасады и ящики">
          <svg class="viewport-icon" viewBox="0 0 24 24" aria-hidden="true">
            <path d="M4 4h11v16H4V4Z"/><path d="m15 4 5 3v13l-5-2V4ZM17 11h.01"/>
          </svg>
          <span>Открыть всё</span>
        </button>
        <button id="btnCloseAll" type="button" title="Закрыть все фасады и ящики">
          <svg class="viewport-icon" viewBox="0 0 24 24" aria-hidden="true">
            <path d="M5 4h14v16H5V4ZM15 11h.01"/>
          </svg>
          <span>Закрыть всё</span>
        </button>
      </div>
      <label class="explode-control" title="Разнесённый вид модели">
        <span>Разбор</span>
        <input type="range" id="explode" min="0" max="100" value="0"
          aria-label="Степень разборки модели">
        <output id="explodeValue" for="explode">0%</output>
      </label>
    </section>
  </div>
  <div id="draw"></div>
  <div id="catalog">
    <div id="catHead">
      <b style="font-size:16px">Каталог изделий</b>
      <input type="text" id="catQ" placeholder="поиск по названию…">
      <button id="catClose">✕ Закрыть</button>
    </div>
    <div id="catCats"></div>
    <div id="catGrid"></div>
  </div>
  <div id="emptyState">
    <div class="es-box">
      <div class="es-title">Новое изделие</div>
      <div class="es-hint">Перетащите сюда фото ТЗ &mdash;<br>или опишите изделие в командной строке ниже</div>
      <button id="esUpload" class="primary">Загрузить фото ТЗ</button>
      <input type="file" id="esFile" accept="image/*" style="display:none">
    </div>
  </div>

  <fieldset id="fs_chat" aria-busy="false">
    <legend class="sr-only">Изменить модель словами</legend>
    <div id="chatStatusTray">
      <span id="chatStateMark" aria-hidden="true"></span>
      <div id="chatState" role="status" aria-live="polite"></div>
      <div id="chatResultActions" hidden>
        <button id="chatShowLog" type="button">Изменения</button>
        <button id="chatUndoQuick" type="button" hidden>Отменить</button>
      </div>
    </div>
    <div id="chatSurface">
      <div id="chatProgress" aria-hidden="true"></div>
      <div id="chatMeta">
        <div id="chatContext" title="Контекст следующей команды">
          <svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true">
            <path d="m12 3 8 4.5v9L12 21l-8-4.5v-9L12 3Z"/><path d="m4 7.5 8 4.5 8-4.5M12 12v9"/>
          </svg>
          <div class="context-copy">
            <span>Контекст команды</span>
            <b id="chatContextLabel">Всё изделие</b>
          </div>
        </div>
        <span id="chatMetaTitle">Изменить модель словами</span>
        <details id="aiSettings">
          <summary aria-label="Настройки AI" title="Настройки AI">
            <svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true">
              <circle cx="12" cy="12" r="3"/><path d="M19 12a7 7 0 0 0-.1-1l2-1.5-2-3.4-2.4 1A7 7 0 0 0 14.8 6L14.5 3h-5L9.2 6a7 7 0 0 0-1.7 1.1l-2.4-1-2 3.4L5.1 11a7 7 0 0 0 0 2l-2 1.5 2 3.4 2.4-1A7 7 0 0 0 9.2 18l.3 3h5l.3-3a7 7 0 0 0 1.7-1.1l2.4 1 2-3.4-2-1.5a7 7 0 0 0 .1-1Z"/>
            </svg>
          </summary>
          <div class="ai-settings-body">
            <label for="aiProvider">Модель AI</label>
            <select id="aiProvider"></select>
            <div id="tokenCount" class="mini"></div>
          </div>
        </details>
      </div>
      <div id="chatImgs"></div>
      <div id="chatComposer">
        <textarea id="chatMsg" rows="1" aria-describedby="chatContextLabel chatShortcut"
          placeholder="Опишите, что нужно изменить в изделии"></textarea>
        <div class="chat-actions">
          <button id="chatAttach" type="button" title="Добавить фото или скан технического задания">
            <svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true">
              <path d="m8 12.5 6.3-6.3a3 3 0 0 1 4.2 4.2l-8.2 8.2a5 5 0 0 1-7.1-7.1l8-8"/>
            </svg>
            <span>Добавить ТЗ</span>
          </button>
          <button id="chatSend" class="primary" type="button" title="Выполнить команду">
            <svg class="ui-icon chat-send-icon" viewBox="0 0 24 24" aria-hidden="true">
              <path d="m5 12 14-7-4 14-3-5-7-2Z"/><path d="m12 14 7-9"/>
            </svg>
            <svg class="ui-icon chat-stop-icon" viewBox="0 0 24 24" aria-hidden="true">
              <rect x="7" y="7" width="10" height="10" rx="1"/>
            </svg>
            <span id="chatSendLabel">Выполнить</span>
          </button>
        </div>
        <input type="file" id="chatFile" accept="image/*" multiple style="display:none">
      </div>
    </div>
    <div id="chatShortcut">Ctrl/Command + Enter — выполнить</div>
  </fieldset>
  <div id="viewportStatus" data-tone="pending" aria-label="Состояние рабочего поля">
    <div id="viewportModelStatus" class="viewport-status-segment" role="status"
      aria-live="polite" aria-atomic="true">
      <span id="viewportModelMark" aria-hidden="true"></span>
      <span id="viewportModelStateLong">Загружаю модель…</span>
      <span id="viewportModelStateShort">Загрузка…</span>
    </div>
    <div id="viewportSelection" class="viewport-status-segment" hidden>
      <span>Выбрано</span><b id="viewportSelectionName"></b>
    </div>
    <div id="viewportHint" class="viewport-status-segment">
      Клик — выбрать · перетаскивание — вращать · колесо — масштаб
    </div>
    <div id="viewportUnits" class="viewport-status-segment">мм</div>
  </div>
  <div id="toast"></div>
</div>
</div>

<script src="/vendor/three.min.js"></script>
<script src="/vendor/OrbitControls.js"></script>
<script>
__SCENE_JS__
let SPEC = __SPEC__;
const FIELDS = __FIELDS__;                 // archetype -> [{key,label,type,...}]
const SECTION_ARCHS = __SECTION_ARCHS__;   // архетипы с секциями
const $ = id => document.getElementById(id);
const toast = (m,bad,sticky)=>{const t=$('toast');t.textContent=m;t.style.background=bad?'#b3261e':'#1a1d21';
  t.style.opacity=1;clearTimeout(t._h);
  if(!sticky) t._h=setTimeout(()=>t.style.opacity=0,bad?5000:2600);};

/* ---------- 3D: общий движок MebelScene (как webviewer, + анимация открытия) ---------- */
const view=$('view3d');
const scene3d=MebelScene(view);

/* ---------- компоновка панелей (MEB-093) ---------- */
let rightPanelUserChoice=null;
let rightPanelMode='properties';
const rightPanelModes={
  properties:{title:'Параметры изделия',tab:$('rightTabProperties'),
    rail:$('rightRailProperties'),panel:$('rightViewProperties'),scrollTop:0},
  components:{title:'Комплектация',tab:$('rightTabComponents'),
    rail:$('rightRailComponents'),panel:$('rightViewComponents'),scrollTop:0},
  production:{title:'Производство',tab:$('rightTabProduction'),
    rail:$('rightRailProduction'),panel:$('rightViewProduction'),scrollTop:0}
};
let lastRightRailControl=rightPanelModes.properties.rail;
function setRightPanelMode(mode){
  if(!rightPanelModes[mode])return;
  const side=$('rightside'),changed=mode!==rightPanelMode;
  if(changed&&rightPanelModes[rightPanelMode])
    rightPanelModes[rightPanelMode].scrollTop=side.scrollTop;
  rightPanelMode=mode;side.dataset.mode=mode;
  Object.entries(rightPanelModes).forEach(([key,item])=>{
    const active=key===mode;
    item.panel.hidden=!active;item.panel.inert=!active||$('sideScroll').inert;
    item.tab.setAttribute('aria-selected',String(active));item.tab.tabIndex=active?0:-1;
    item.rail.classList.toggle('is-active',active);
  });
  const current=rightPanelModes[mode];
  $('rightPanelTitle').textContent=current.title;
  $('rightPanelClose').setAttribute('aria-label',`Свернуть панель «${current.title}»`);
  lastRightRailControl=current.rail;
  if(changed)requestAnimationFrame(()=>{side.scrollTop=current.scrollTop||0;});
}
function setRightPanel(open,mode=rightPanelMode,fromUser=false){
  const focusWasRail=!!(fromUser&&open&&document.activeElement&&
    document.activeElement.closest('#rightRail')),wasOpen=!$('app').classList.contains('right-collapsed');
  if(fromUser) rightPanelUserChoice=!!open;
  setRightPanelMode(mode);
  $('app').classList.toggle('right-collapsed',!open);
  $('rightPanelClose').setAttribute('aria-expanded',String(!!open));
  Object.entries(rightPanelModes).forEach(([key,item])=>
    item.rail.setAttribute('aria-expanded',String(!!open&&key===rightPanelMode)));
  if(wasOpen!==!!open)requestAnimationFrame(()=>scene3d.resize());
  if(fromUser) requestAnimationFrame(()=>{
    if(!open)lastRightRailControl.focus({preventScroll:true});
    else if(focusWasRail)rightPanelModes[rightPanelMode].tab.focus({preventScroll:true});
  });
}
const wideStudioLayout=window.matchMedia('(min-width: 1600px)');
function syncRightPanelToViewport(){
  const open=rightPanelUserChoice===null
    ? wideStudioLayout.matches
    : rightPanelUserChoice;
  setRightPanel(open);
}
$('rightPanelClose').onclick=()=>setRightPanel(false,null,true);
Object.entries(rightPanelModes).forEach(([mode,item],index,entries)=>{
  item.rail.onclick=()=>setRightPanel(true,mode,true);
  item.rail.onkeydown=event=>{
    if(!['ArrowUp','ArrowDown','Home','End'].includes(event.key))return;
    event.preventDefault();
    let next=index;
    if(event.key==='ArrowUp')next=(index+entries.length-1)%entries.length;
    if(event.key==='ArrowDown')next=(index+1)%entries.length;
    if(event.key==='Home')next=0;
    if(event.key==='End')next=entries.length-1;
    entries[next][1].rail.focus({preventScroll:true});
  };
  item.tab.onclick=()=>setRightPanel(true,mode,true);
  item.tab.onkeydown=event=>{
    if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;
    event.preventDefault();
    let next=index;
    if(event.key==='ArrowLeft')next=(index+entries.length-1)%entries.length;
    if(event.key==='ArrowRight')next=(index+1)%entries.length;
    if(event.key==='Home')next=0;
    if(event.key==='End')next=entries.length-1;
    const [nextMode,nextItem]=entries[next];
    setRightPanel(true,nextMode,true);nextItem.tab.focus({preventScroll:true});
  };
});
wideStudioLayout.addEventListener('change',syncRightPanelToViewport);
if(window.ResizeObserver) new ResizeObserver(()=>scene3d.resize()).observe(view);
syncRightPanelToViewport();

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
const viewportLayerInputs=[$('cbHoles'),$('cbHw'),$('cbTex'),$('cbDims'),$('cbXray')];
function syncViewportLayerCount(){
  const enabled=viewportLayerInputs.filter(input=>input.checked).length;
  $('layerCount').textContent=`${enabled} / ${viewportLayerInputs.length}`;
}
viewportLayerInputs.forEach(input=>input.addEventListener('change',syncViewportLayerCount));
syncViewportLayerCount();
$('explode').oninput=e=>{
  scene3d.setExplode(e.target.value/100);
  $('explodeValue').textContent=`${e.target.value}%`;
};
$('btnOpenAll').onclick=()=>scene3d.openAll();
$('btnCloseAll').onclick=()=>scene3d.closeAll();
// ракурсы: аксонометрия/перспектива/сверху/спереди/слева
document.querySelectorAll('#views .vw').forEach(b=>b.onclick=()=>{
  scene3d.setView(b.dataset.view);
  document.querySelectorAll('#views .vw').forEach(x=>{
    const active=x===b;
    x.classList.toggle('on',active);
    x.setAttribute('aria-pressed',String(active));
  });
});
// Простой клик выбирает деталь/открывает фасад и не меняет камеру. Снимаем preset
// только после реального orbit/pan-жеста либо zoom, не вмешиваясь в MebelScene.
let cameraGestureStart=null;
function clearCameraPreset(){document.querySelectorAll('#views .vw').forEach(x=>{
  x.classList.remove('on');x.setAttribute('aria-pressed','false');
});}
view.addEventListener('pointerdown',event=>{
  cameraGestureStart=event.shiftKey?null:{id:event.pointerId,x:event.clientX,y:event.clientY};
});
view.addEventListener('pointermove',event=>{
  if(!cameraGestureStart||cameraGestureStart.id!==event.pointerId)return;
  if(Math.hypot(event.clientX-cameraGestureStart.x,event.clientY-cameraGestureStart.y)<4)return;
  clearCameraPreset();cameraGestureStart=null;
});
['pointerup','pointercancel'].forEach(type=>view.addEventListener(type,()=>cameraGestureStart=null));
view.addEventListener('wheel',clearCameraPreset,{passive:true});

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
  defs.forEach((f,index)=>{
    const v=SPEC[f.key], row=document.createElement('div'); row.className='parameter-row';
    const fieldId=`archField_${index}`;
    const label=document.createElement('label');label.htmlFor=fieldId;
    label.textContent=f.label;if(f.hint)label.title=f.hint;
    let input;
    if(f.type==='bool'){
      const on=(v===undefined)?(f.default===true):!!v;
      input=document.createElement('input');input.type='checkbox';input.checked=on;
    }else if(f.type==='select'){
      input=document.createElement('select');
      (f.options||[]).forEach(o=>{
        const option=document.createElement('option');option.value=o;option.textContent=o||'—';
        option.selected=String(v??'')===String(o);input.appendChild(option);
      });
    }else{
      input=document.createElement('input');input.type=f.type==='num'?'number':'text';
      input.value=v??'';if(f.default!==undefined)input.placeholder=f.default;
    }
    input.id=fieldId;input.dataset.ak=f.key;
    if(f.hint){
      const hint=document.createElement('span');hint.id=`archHint_${index}`;hint.className='sr-only';
      hint.textContent=f.hint;input.setAttribute('aria-describedby',hint.id);
      row.append(label,input,hint);
    }else row.append(label,input);
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
let timer=null,lastOk=false,generateRequestSeq=0,viewportModelPhase='loading',
    viewportModelDiagnostics={checkErrors:0,unresolved:0,total:0};
function checkErrorWord(count){
  const n=Math.abs(Number(count)||0)%100,d=n%10;
  return n>=11&&n<=14?'ошибок':d===1?'ошибка':d>=2&&d<=4?'ошибки':'ошибок';
}
function findingWord(count){
  const n=Math.abs(Number(count)||0)%100,d=n%10;
  return n>=11&&n<=14?'замечаний':d===1?'замечание':d>=2&&d<=4?'замечания':'замечаний';
}
function unresolvedPositionCaption(count){
  const value=Math.abs(Number(count)||0),n=value%100,d=value%10;
  if(n>=11&&n<=14)return `${value} позиций базы не сопоставлено`;
  if(d===1)return `${value} позиция базы не сопоставлена`;
  if(d>=2&&d<=4)return `${value} позиции базы не сопоставлены`;
  return `${value} позиций базы не сопоставлено`;
}
function modelDiagnosticCounts(payload=lastPayload){
  let checkErrors=0;
  if(payload&&payload.issues)
    Object.values(payload.issues).forEach(values=>{checkErrors+=(values||[]).length;});
  const unresolved=payload&&payload.refs
    ?Object.values(payload.refs).filter(item=>item&&typeof item==='object'&&!item.resolved).length:0;
  return {checkErrors,unresolved,total:checkErrors+unresolved};
}
function diagnosticLongCaption(diagnostics){
  const parts=[];
  if(diagnostics.checkErrors)parts.push(`${diagnostics.checkErrors} ${checkErrorWord(diagnostics.checkErrors)} проверки`);
  if(diagnostics.unresolved)parts.push(unresolvedPositionCaption(diagnostics.unresolved));
  return parts.join(' · ');
}
function setViewportModelPhase(phase,diagnostics=null){
  viewportModelPhase=phase;
  viewportModelDiagnostics=diagnostics&&typeof diagnostics==='object'
    ?Object.assign({checkErrors:0,unresolved:0,total:0},diagnostics)
    :{checkErrors:Number(diagnostics)||0,unresolved:0,total:Number(diagnostics)||0};
  syncViewportStatus();
}
function syncViewportStatus(){
  const status=$('viewportStatus');if(!status)return;
  let tone='pending',longText='Загружаю модель…',shortText='Загрузка…';
  if(viewportModelPhase==='pending'){
    longText='Изменения ожидают пересчёта…';shortText='Ожидает пересчёта';
  }else if(viewportModelPhase==='busy'){
    tone='busy';longText='Пересчитываю модель и проверки…';shortText='Пересчёт…';
  }else if(viewportModelPhase==='ready'){
    tone='ready';longText='Модель актуальна';shortText='Актуальна';
  }else if(viewportModelPhase==='warning'){
    tone='warning';
    const diagnosticText=diagnosticLongCaption(viewportModelDiagnostics);
    longText='3D обновлена'+(diagnosticText?` · ${diagnosticText}`:' · есть замечания');
    shortText=viewportModelDiagnostics.checkErrors
      ?`${viewportModelDiagnostics.checkErrors} ${checkErrorWord(viewportModelDiagnostics.checkErrors)}`:
      viewportModelDiagnostics.unresolved?`База: ${viewportModelDiagnostics.unresolved}`:'Есть замечания';
  }else if(viewportModelPhase==='no-viewer'){
    tone='error';
    const diagnosticText=diagnosticLongCaption(viewportModelDiagnostics);
    longText='Модель не обновлена'+(diagnosticText?` · ${diagnosticText}`:'');shortText='Не обновлена';
  }else if(viewportModelPhase==='network'){
    tone='error';longText='Не удалось пересчитать · показана предыдущая модель';
    shortText='Ошибка пересчёта';
  }else if(viewportModelPhase==='draft'){
    tone='neutral';longText='Черновик · модель ещё не построена';shortText='Черновик';
  }
  status.dataset.tone=tone;
  $('viewportModelStateLong').textContent=longText;
  $('viewportModelStateShort').textContent=shortText;
  const panel=currentSelectedPart(),selection=$('viewportSelection');
  selection.hidden=!panel;
  $('viewportSelectionName').textContent=panel&&panel.name||'';
  $('viewportSelectionName').title=panel&&panel.name||'';
  const mode=currentWorkspaceMode();
  $('viewportHint').textContent=chatBusy?'Команда выполняется · модель доступна для осмотра':
    partEditBusy?'Пересчёт детали · модель доступна для осмотра':
    mode==='draw'?'Клик по детали — выбрать':
    mode==='nest'?'Раскрой текущей модели':panel?
      'Shift + перетаскивание — переместить · Esc — снять выбор':
      'Клик — выбрать · перетаскивание — вращать · колесо — масштаб';
}
function schedule(){
  clearTimeout(timer);refreshUndoState();
  if(typeof renderedWorkspaceMode!=='undefined'){
    renderedWorkspaceMode=null;renderedWorkspaceSpecJson=null;syncWorkspacePrintState();
  }
  setViewportModelPhase('pending');timer=setTimeout(()=>apply().catch(()=>{}),400);
}
async function apply(){
  const requestId=++generateRequestSeq;
  if(SPEC&&SPEC.draft){showEmpty(true);setViewportModelPhase('draft');return;} // черновик не генерируем
  refreshUndoState();
  const requestSpecJson=JSON.stringify(SPEC);
  setViewportModelPhase('busy');
  try{
    const r=await fetch('/api/generate',{method:'POST',
      headers:{'Content-Type':'application/json'},body:JSON.stringify({spec:SPEC})});
    const p=await r.json();
    if(requestId!==generateRequestSeq||JSON.stringify(SPEC)!==requestSpecJson)
      return Object.assign({},p,{ok:false,viewer:null,stale:true});
    const restorePartName=(typeof SELECTED_PART!=='undefined'&&SELECTED_PART)
      ?SELECTED_PART.name:null;
    paint(p);
    const diagnostics=modelDiagnosticCounts(p);
    setViewportModelPhase(p.viewer?(p.ok&&!diagnostics.unresolved?'ready':'warning'):'no-viewer',diagnostics);
    if(restorePartName&&p.viewer&&Array.isArray(p.viewer.panels)){
      const restoreIndex=p.viewer.panels.findIndex(panel=>panel.name===restorePartName);
      if(restoreIndex>=0)scene3d.select(restoreIndex);
    }
    refreshOperationTargets();
    if(drawOn) refreshDraw();
    if(nestOn) refreshNest();
    return p;
  }catch(error){
    if(requestId===generateRequestSeq)setViewportModelPhase('network');
    throw error;
  }
}
function paint(p){
  lastPayload=p;
  const B=$('badges'); B.innerHTML='';
  const names={schema:'схема',consistency:'встык',geometry:'геометрия',cfrn:'.cfrn',
               holes:'присадки',drilling:'сверловка'};
  let errs=[], hasFixableProblems=false;
  for(const k of Object.keys(names)){
    const bad=(p.issues[k]||[]).length>0;
    if(bad) hasFixableProblems=true;
    B.insertAdjacentHTML('beforeend',`<span class="badge ${bad?'bad':''}">${names[k]}</span>`);
    if(bad) errs.push(...p.issues[k].slice(0,4).map(x=>`[${names[k]}] ${x}`));
  }
  if(p.refs){                                    // подбор позиций базы (C4, информативно)
    const rs=Object.values(p.refs).filter(r=>r&&typeof r==='object');
    const ok=rs.filter(r=>r.resolved).length;
    if(ok<rs.length) hasFixableProblems=true;
    if(rs.length) B.insertAdjacentHTML('beforeend',
      `<span class="badge ${ok===rs.length?'':'warn'}"
        title="позиции производственной базы">база ${ok}/${rs.length}</span>`);
  }
  $('errors').textContent=errs.join('\n');
  $('btnFixAll').hidden=!hasFixableProblems;
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
let drawOn=false,nestOn=false,workspaceViewRequestSeq=0,detailDrawingRequestSeq=0,
    renderedWorkspaceMode=null,renderedWorkspaceSpecJson=null;
function currentWorkspaceMode(){return drawOn?'draw':nestOn?'nest':'3d';}
function syncWorkspacePrintState(){
  const mode=currentWorkspaceMode();
  const ready=(mode==='draw'||mode==='nest')&&renderedWorkspaceMode===mode&&
    renderedWorkspaceSpecJson===JSON.stringify(SPEC)&&!!$('draw').querySelector('svg');
  $('btnPrint').disabled=!ready;
  $('btnPrint').title=mode==='3d'?'Доступно в чертеже и раскрое':
    ready?'Распечатать текущий вид':'Печать станет доступна после построения';
  syncViewportStatus();
}
function switchTab(mode,load=true){
  workspaceViewRequestSeq++;                    // поздний ответ прошлого режима больше не рисует
  drawOn=(mode==='draw'); nestOn=(mode==='nest');
  const is3d=mode==='3d';
  renderedWorkspaceMode=null;renderedWorkspaceSpecJson=null;
  $('draw').style.display=(drawOn||nestOn)?'block':'none';
  if(!is3d)$('draw').textContent=drawOn?'Строю чертёж изделия…':'Строю раскрой…';
  $('tab3d').classList.toggle('on',is3d);
  $('tabDraw').classList.toggle('on',drawOn);
  $('tabNest').classList.toggle('on',nestOn);
  [['tab3d',is3d],['tabDraw',drawOn],['tabNest',nestOn]].forEach(([id,active])=>
    $(id).setAttribute('aria-selected',String(active)));
  $('views').hidden=!is3d;
  $('hud').hidden=!is3d;
  syncWorkspacePrintState();
  $('viewportTopbar').classList.toggle('is-2d',!is3d);
  if(load&&drawOn) refreshDraw();
  if(load&&nestOn) refreshNest();
}
$('tab3d').onclick=()=>switchTab('3d');
$('tabDraw').onclick=()=>switchTab('draw');
$('tabNest').onclick=()=>switchTab('nest');
$('btnPrint').onclick=()=>{
  if($('btnPrint').disabled||renderedWorkspaceMode!==currentWorkspaceMode()){
    toast('Сначала дождитесь текущего чертежа или раскроя',true);return;
  }
  const svg=$('draw').querySelector('svg');
  if(!svg){toast('Откройте чертёж или раскрой',true);return;}
  const w=window.open('','print');
  w.document.write('<html><head><title>Печать</title></head><body>'+svg.outerHTML+
    '<scr'+'ipt>onload=()=>{print();close();}</scr'+'ipt></body></html>');
  w.document.close();
};
async function refreshNest(){
  const requestId=++workspaceViewRequestSeq;
  const requestSpecJson=JSON.stringify(SPEC);
  renderedWorkspaceMode=null;renderedWorkspaceSpecJson=null;syncWorkspacePrintState();
  $('draw').textContent='Строю раскрой…';
  const r=await fetch('/api/nesting',{method:'POST',
    headers:{'Content-Type':'application/json'},body:JSON.stringify({spec:SPEC})});
  const p=await r.json();
  if(requestId!==workspaceViewRequestSeq||!nestOn||JSON.stringify(SPEC)!==requestSpecJson)return;
  $('draw').innerHTML=p.svg||('<i>'+(p.error||'раскрой недоступен')+'</i>');
  renderedWorkspaceMode=p.svg?'nest':null;renderedWorkspaceSpecJson=p.svg?requestSpecJson:null;
  syncWorkspacePrintState();
}
async function refreshDraw(){
  const requestId=++workspaceViewRequestSeq;
  const requestSpecJson=JSON.stringify(SPEC);
  renderedWorkspaceMode=null;renderedWorkspaceSpecJson=null;syncWorkspacePrintState();
  $('draw').textContent='Строю чертёж изделия…';
  const r=await fetch('/api/techview',{method:'POST',
    headers:{'Content-Type':'application/json'},body:JSON.stringify({spec:SPEC})});
  const p=await r.json();
  if(requestId!==workspaceViewRequestSeq||!drawOn||JSON.stringify(SPEC)!==requestSpecJson)return;
  $('draw').innerHTML=p.svg||('<i>'+(p.issues||[]).join('; ')+'</i>');
  renderedWorkspaceMode=p.svg?'draw':null;renderedWorkspaceSpecJson=p.svg?requestSpecJson:null;
  syncWorkspacePrintState();
  const pi=scene3d.getSelected&&scene3d.getSelected();     // восстановить подсветку
  if(pi!==null&&pi!==undefined&&lastPayload){
    const nm=(lastPayload.viewer.panels[pi]||{}).name;
    if(nm) document.querySelectorAll(`#draw rect[data-panel="${CSS.escape(nm)}"]`)
      .forEach(el=>el.classList.add('sel'));
  }
}

/* ---------- выбор детали кликом (AKD-120) ---------- */
let lastPayload=null, SELECTED_PART=null, partEditBusy=false, partEditMessage='',
    partEditMessageTone='',partEditMessageFor=null;
const PART_TYPE_LABELS={back:'Задняя стенка',bottom:'Дно',door_front:'Дверной фасад',
  drawer_back:'Задняя стенка ящика',drawer_bottom:'Дно ящика',drawer_front:'Фасад ящика',
  drawer_side_left:'Левая боковина ящика',drawer_side_right:'Правая боковина ящика',
  plinth:'Цоколь',screen:'Экран',shelf:'Полка',side_left:'Левая боковина',
  side_right:'Правая боковина',top:'Крышка',vertical_partition:'Перегородка'};
const PART_EDGE_LABELS={top:'верх',bottom:'низ',left:'слева',right:'справа'};
const PART_COORD_KEYS=['x1','x2','y1','y2','z1','z2'];
function formatPartNumber(value){
  const n=Number(value); if(!Number.isFinite(n))return '—';
  return Number.isInteger(n)?String(n):String(Math.round(n*10)/10).replace('.',',');
}
function currentSelectedPart(){
  if(!SELECTED_PART||!lastPayload||!lastPayload.viewer)return null;
  return (lastPayload.viewer.panels||[]).find(panel=>panel.name===SELECTED_PART.name)||null;
}
function selectedPartOverrides(name){
  const list=(SPEC.overrides||[]).filter(item=>item.panel===name);
  return {list,added:list.some(item=>item.action==='add'),
    transform:list.find(item=>(item.action||'transform')==='transform')||null};
}
function selectedPartHoles(name){
  return ((lastPayload&&lastPayload.viewer&&lastPayload.viewer.holes)||[])
    .filter(hole=>hole.panel===name);
}
function parsePanelEdges(value){
  const raw=String(value||'').trim(); if(!raw||raw==='—')return [];
  return raw.split(',').map(item=>{
    const [side,thickness]=item.trim().split(':');
    return {side:side||'',thickness:Number(thickness)};
  }).filter(item=>item.side&&Number.isFinite(item.thickness));
}
function edgeWord(count){
  const n=count%100,d=count%10;
  return n>=11&&n<=14?'торцов':d===1?'торец':d>=2&&d<=4?'торца':'торцов';
}
function panelEdgeSummary(edges){
  if(!edges.length)return 'Нет';
  const values=[...new Set(edges.map(item=>formatPartNumber(item.thickness)))];
  return `${values.join(' / ')} мм · ${edges.length} ${edgeWord(edges.length)}`;
}
function holeTypeWord(count){
  const n=count%100,d=count%10;
  return n>=11&&n<=14?'типов':d===1?'тип':d>=2&&d<=4?'типа':'типов';
}
function partHoleData(name){
  const holes=selectedPartHoles(name),counts=new Map();
  holes.forEach(hole=>counts.set(hole.purpose||'без назначения',
    (counts.get(hole.purpose||'без назначения')||0)+1));
  const groups=[...counts.entries()].sort((a,b)=>b[1]-a[1]);
  return {holes,groups,summary:holes.length?`${holes.length} · ${groups.length} ${holeTypeWord(groups.length)}`:'Нет'};
}
function readPartDraft(){
  const values={};
  PART_COORD_KEYS.forEach(key=>{
    const input=$('partCard').querySelector(`input[data-ov="${key}"]`);
    values[key]=input&&input.value.trim()!==''?Number(input.value):NaN;
  });
  const panel=currentSelectedPart();
  const valid=PART_COORD_KEYS.every(key=>Number.isFinite(values[key]))&&
    values.x1<values.x2&&values.y1<values.y2&&values.z1<values.z2;
  const dirty=!!panel&&PART_COORD_KEYS.some(key=>values[key]!==Number(panel[key]));
  return {values,valid,dirty,panel};
}
function syncPartEditState(){
  const draft=readPartDraft(),locked=partEditBusy||chatBusy;
  for(const axis of ['x','y','z']){
    const from=draft.values[axis+'1'],to=draft.values[axis+'2'];
    const delta=$('partCard').querySelector(`[data-delta="${axis}"]`);
    delta.textContent=Number.isFinite(from)&&Number.isFinite(to)?`Δ${formatPartNumber(to-from)}`:'Δ—';
  }
  $('partCard').querySelectorAll('input[data-ov]').forEach(input=>input.disabled=locked);
  $('ovApply').disabled=locked||!draft.dirty||!draft.valid;
  $('partEditCancel').disabled=locked||!draft.dirty;
  [$('ovReset'),$('ovDelete'),$('ovDetail'),$('partCommandFocus')]
    .forEach(button=>{if(button)button.disabled=locked;});
  $('fs_part').setAttribute('aria-busy',String(partEditBusy));
  const status=$('partEditStatus'); status.className='';
  if(partEditBusy){status.textContent='Пересчитываю деталь и проверки…';}
  else if(partEditMessage&&draft.panel&&partEditMessageFor===draft.panel.name){status.textContent=partEditMessage;
    if(partEditMessageTone)status.classList.add('is-'+partEditMessageTone);}
  else if(draft.dirty&&!draft.valid){status.textContent='Проверьте границы: начало оси должно быть меньше конца.';
    status.classList.add('is-error');}
  else if(draft.dirty){status.textContent='Есть неприменённые значения.';status.classList.add('is-dirty');}
  else status.textContent='';
  return draft;
}
function fillPartCoordinateInputs(panel){
  PART_COORD_KEYS.forEach(key=>{
    const input=$('partCard').querySelector(`input[data-ov="${key}"]`);
    input.value=panel[key]??'';
  });
}
function renderSelectedPart(panel){
  const fs=$('fs_part'),override=selectedPartOverrides(panel.name),
    edges=parsePanelEdges(panel.edges),holes=partHoleData(panel.name);
  const dx=Number(panel.x2)-Number(panel.x1),dy=Number(panel.y2)-Number(panel.y1),
    dz=Number(panel.z2)-Number(panel.z1);
  $('partName').textContent=panel.name||'Без названия';$('partName').title=panel.name||'';
  $('partKind').textContent=PART_TYPE_LABELS[panel.type]||panel.type||'Деталь';
  $('partKind').title=panel.type||'';
  $('partOrigin').textContent=override.added?'Добавлена вручную':
    override.transform?'Локальная правка':'От генератора';
  fs.classList.toggle('has-override',!!(override.added||override.transform));
  $('partDimensions').textContent=`${formatPartNumber(dx)} × ${formatPartNumber(dy)} × ${formatPartNumber(dz)} мм`;
  $('partMaterial').textContent=`${panel.material||'Не указан'}${panel.thickness!=null?` · ${formatPartNumber(panel.thickness)} мм`:''}`;
  $('partEdges').textContent=panelEdgeSummary(edges);
  $('partHoles').textContent=holes.summary;
  $('partEdgesRaw').textContent=edges.length?'Кромка: '+edges.map(item=>
    `${PART_EDGE_LABELS[item.side]||item.side} ${formatPartNumber(item.thickness)} мм`).join(' · '):'Кромка: нет';
  $('partHolesRaw').textContent=holes.groups.length?'Присадки: '+holes.groups.map(([purpose,count])=>
    `${purpose} ×${count}`).join(' · '):'Присадки: нет';
  $('ovReset').hidden=!override.transform;
  $('ovDetail').textContent='Чертёж';
  fillPartCoordinateInputs(panel); partEditMessage='';partEditMessageTone='';partEditMessageFor=null;
  fs.style.display=''; syncPartEditState();
}
function focusPartCommand(){
  $('chatMsg').focus();$('chatMsg').scrollIntoView({block:'nearest'});
}
async function openSelectedPartDrawing(){
  const panel=currentSelectedPart(); if(!panel||partEditBusy||chatBusy)return;
  const name=panel.name,button=$('ovDetail');
  switchTab('draw',false);
  const requestId=++workspaceViewRequestSeq,detailRequestId=++detailDrawingRequestSeq,
    requestSpecJson=JSON.stringify(SPEC);
  button.disabled=true;button.textContent='Открываю…';
  $('draw').textContent=`Строю чертёж детали «${name}»…`;
  try{
    const r=await fetch('/api/techview',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({spec:SPEC,panel:name})});
    const data=await r.json();
    const current=currentSelectedPart();
    if(requestId!==workspaceViewRequestSeq||detailRequestId!==detailDrawingRequestSeq||
       !drawOn||!current||current.name!==name||JSON.stringify(SPEC)!==requestSpecJson)return;
    if(!r.ok||!data.svg)throw new Error((data.issues||[]).join('; ')||data.error||'чертёж недоступен');
    $('draw').innerHTML=data.svg;
    renderedWorkspaceMode='draw';renderedWorkspaceSpecJson=requestSpecJson;syncWorkspacePrintState();
    const back=document.createElement('div');back.style.margin='8px';
    const backButton=document.createElement('button');backButton.type='button';
    backButton.textContent='← Общий чертёж';backButton.onclick=refreshDraw;back.appendChild(backButton);
    $('draw').appendChild(back);
  }catch(error){
    if(requestId===workspaceViewRequestSeq&&detailRequestId===detailDrawingRequestSeq){
      toast('Чертёж детали: '+error.message,true);refreshDraw();}
  }finally{
    button.textContent='Чертёж';
    if(currentSelectedPart())syncPartEditState();
  }
}
async function commitPartMutation(name,mutate,successText){
  if(modelMutationLocked())return false;
  clearTimeout(timer);timer=null;
  const beforeSpec=JSON.stringify(SPEC),undoBefore=UNDO.slice();
  partEditBusy=true;partEditMessage='';partEditMessageTone='';partEditMessageFor=name;
  syncModelEditLock();syncPartEditState();
  pushUndo();
  try{
    mutate();$('rawspec').value=JSON.stringify(SPEC,null,2);
    const generated=await apply();
    if(!generated||generated.stale||!generated.viewer)throw new Error('модель не пересчитана');
    partEditMessage=successText||'Изменение применено.';partEditMessageTone='success';
    partEditMessageFor=name;
    if(successText)toast(successText);
    return true;
  }catch(error){
    SPEC=JSON.parse(beforeSpec);UNDO.splice(0,UNDO.length,...undoBefore);refreshUndoState();fillForm();
    try{await apply();}catch(_restoreError){}
    partEditMessage='Не удалось применить. Исходная модель сохранена.';
    partEditMessageTone='error';partEditMessageFor=name;
    toast('Правка детали: '+error.message,true);return false;
  }finally{
    partEditBusy=false;syncModelEditLock();if(currentSelectedPart())syncPartEditState();
  }
}
function setOverride(name,placement,move){
  return commitPartMutation(name,()=>{
    SPEC.overrides=SPEC.overrides||[];
    let override=SPEC.overrides.find(item=>item.panel===name&&
      (item.action||'transform')==='transform');
    if(!override){override={panel:name};SPEC.overrides.push(override);}
    if(placement)override.placement=Object.assign(override.placement||{},placement);
    if(move){
      const panel=((lastPayload&&lastPayload.viewer&&lastPayload.viewer.panels)||[])
        .find(item=>item.name===name);
      if(panel)override.placement=Object.assign(override.placement||{},
        {x1:panel.x1+move[0],x2:panel.x2+move[0],y1:panel.y1+move[1],
         y2:panel.y2+move[1],z1:panel.z1+move[2],z2:panel.z2+move[2]});
    }
  },move?'Положение детали изменено.':'Точные параметры применены.');
}
function clearOverride(name){
  return commitPartMutation(name,()=>{
    SPEC.overrides=(SPEC.overrides||[]).filter(item=>!(item.panel===name&&
      (item.action||'transform')==='transform'));
    if(!SPEC.overrides.length)delete SPEC.overrides;
  },'Локальная правка сброшена.');
}
function deleteSelectedPart(name){
  const wasAdded=selectedPartOverrides(name).added;
  return commitPartMutation(name,()=>{
    SPEC.overrides=(SPEC.overrides||[]).filter(item=>item.panel!==name);
    if(!wasAdded)SPEC.overrides.push({panel:name,action:'delete'});
    if(!SPEC.overrides.length)delete SPEC.overrides;
  },'Деталь удалена.');
}
$('partClearSelection').onclick=()=>scene3d.select(null);
$('partCommandFocus').onclick=focusPartCommand;
$('partChatSend').onclick=focusPartCommand;
$('partEditCancel').onclick=()=>{
  const panel=currentSelectedPart();if(!panel)return;
  fillPartCoordinateInputs(panel);partEditMessage='';partEditMessageTone='';partEditMessageFor=null;
  syncPartEditState();
};
$('partCard').querySelectorAll('input[data-ov]').forEach(input=>input.addEventListener('input',()=>{
  partEditMessage='';partEditMessageTone='';partEditMessageFor=null;syncPartEditState();
}));
$('ovApply').onclick=()=>{
  const draft=syncPartEditState();if(!draft.panel||!draft.valid||!draft.dirty)return;
  setOverride(draft.panel.name,draft.values);
};
$('ovReset').onclick=()=>{const panel=currentSelectedPart();if(panel)clearOverride(panel.name);};
$('ovDetail').onclick=openSelectedPartDrawing;
$('ovDelete').onclick=()=>{
  const panel=currentSelectedPart();if(!panel)return;
  if(!confirm(`Деталь «${panel.name}» будет исключена из модели. Присадки, смета и чертежи пересчитаются. Удалить?`))return;
  deleteSelectedPart(panel.name);
};
scene3d.onSelect=sel=>{
  const fs=$('fs_part');
  document.querySelectorAll('#draw rect.sel').forEach(rect=>rect.classList.remove('sel'));
  const previousName=SELECTED_PART&&SELECTED_PART.name,nextName=sel&&sel.panel&&sel.panel.name;
  const discarded=!!(previousName&&previousName!==nextName&&!modelMutationLocked()&&readPartDraft().dirty);
  if(previousName!==nextName)detailDrawingRequestSeq++;
  SELECTED_PART=sel?sel.panel:null;
  const cm=$('chatMsg'),cc=$('chatContext'),cl=$('chatContextLabel');
  if(cm)cm.placeholder=sel?`Опишите, что изменить в детали «${sel.panel.name}»`:
    'Опишите, что нужно изменить в изделии';
  if(cc)cc.classList.toggle('selected',!!sel);
  if(cl)cl.textContent=sel?`Выбранная деталь · ${sel.panel.name}`:'Всё изделие';
  if(discarded)toast('Неприменённые точные значения отменены');
  syncViewportStatus();
  if(!sel){fs.style.display='none';partEditMessage='';partEditMessageTone='';partEditMessageFor=null;return;}
  renderSelectedPart(sel.panel);
  document.querySelectorAll(`#draw rect[data-panel="${CSS.escape(sel.panel.name)}"]`)
    .forEach(rect=>rect.classList.add('sel'));
};
scene3d.onTransform=(name,delta)=>{
  if(modelMutationLocked()){
    // Жест мог начаться до блокировки. Движок уже сдвинул mesh, поэтому
    // возвращаем последнюю подтверждённую геометрию, не меняя ParamSpec.
    if(lastPayload&&lastPayload.viewer)rebuild(lastPayload.viewer);
    toast('Пока выполняется команда, деталь нельзя перемещать. Осмотр модели доступен.',true);
    return;
  }
  setOverride(name,null,delta);
};
document.addEventListener('keydown',e=>{
  if(e.key!=='Escape')return;
  const exact=$('partExact'),draft=currentSelectedPart()?readPartDraft():null;
  if(exact&&exact.open&&draft&&draft.dirty){
    fillPartCoordinateInputs(draft.panel);partEditMessage='';partEditMessageTone='';partEditMessageFor=null;
    exact.open=false;syncPartEditState();return;
  }
  scene3d.select(null);
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
  // другой объект — другой разговор: история чата, лог и вложения не должны
  // утекать между изделиями (иначе ИИ «помнит» чужие правки)
  CHAT_HISTORY.length=0; chatWorkspaceGeneration++; resetOperationLog();
  PENDING_IMGS.length=0; renderImgs(); SELECTED_PART=null;
  $('chatContext').classList.remove('selected');
  $('chatContextLabel').textContent='Всё изделие';
  $('chatMsg').placeholder='Опишите, что нужно изменить в изделии';
  setChatState('',false,'');
  const dr=!!(SPEC&&SPEC.draft);                   // черновик — пустой экран без модели
  showEmpty(dr);
  scene3d.select(null); fillForm();
  if(!dr) apply();
  loadProjects(); loadBuilds();
  toast('Открыто: '+p.file);
}
// пустое рабочее пространство (AKD-214): «Новое» → чистый экран с приглашением загрузить ТЗ
let EMPTY=false;
function showEmpty(on){
  EMPTY=on; $('emptyState').classList.toggle('on',on);
  setViewportModelPhase(on?'draft':'loading');
  if(on){                                          // чистый экран: 3D, бейджи, статистика
    if(scene3d.setPayload) scene3d.setPayload({panels:[]});
    ['badges','stats','errors'].forEach(id=>{const e=$(id); if(e) e.innerHTML='';});
    const fp=$('fs_part'); if(fp) fp.style.display='none';
  }
}
// распознавание фото ТЗ через FileReader — надёжно на больших файлах (AKD-214)
let TZ_BUSY=false;
function importTzFile(f){
  if(!f||TZ_BUSY||modelMutationLocked()) return;
  if(!/\.(png|jpe?g|webp|gif)$/i.test(f.name)){toast('Нужно изображение ТЗ (png/jpg/webp)',true);return;}
  TZ_BUSY=true;
  const btn=$('esUpload'), btnTxt=btn.textContent;
  btn.disabled=true; btn.textContent='⏳ Распознаю…';
  const hint=$('emptyState').querySelector('.es-hint'), hintHtml=hint.innerHTML;
  hint.innerHTML='Нейросеть читает ТЗ и собирает модель.<br>Обычно 15–40 секунд…';
  toast('⏳ Распознаю ТЗ — нейросеть читает изображение (15–40 сек)…',false,true);
  const done=()=>{TZ_BUSY=false; btn.disabled=false; btn.textContent=btnTxt; hint.innerHTML=hintHtml;};
  const rd=new FileReader();
  rd.onerror=()=>{done(); toast('Не удалось прочитать файл',true);};
  rd.onload=async()=>{
    const s=String(rd.result), b64=s.slice(s.indexOf(',')+1);
    try{
      const r=await fetch('/api/import-tz',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({name:f.name,data:b64,provider:CHAT_PROVIDER})});
      const p=await r.json();
      done();
      if(p.ok){adoptSpec(p); toast('✅ ТЗ распознано → '+(p.spec&&p.spec.project_name||p.file));}
      else toast('❌ Не удалось распознать ТЗ: '+(p.error||'нет ответа нейросети'),true);
    }catch(e){done(); toast('❌ Ошибка распознавания: '+e.message,true);}
  };
  rd.readAsDataURL(f);
}
$('esUpload').onclick=()=>$('esFile').click();
$('esFile').onchange=e=>{importTzFile(e.target.files[0]); e.target.value='';};
$('projSel').onchange=async e=>{
  const r=await fetch('/api/open',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({file:e.target.value})});
  const p=await r.json();
  if(p.ok) adoptSpec(p); else toast('Ошибка: '+(p.error||''),true);
};
$('projNew').onclick=async()=>{   // черновик: запись в каталоге + пустой воркспейс (AKD-214)
  const name=prompt('Название нового изделия:','Новое изделие');
  if(name===null) return;
  const r=await fetch('/api/new',{method:'POST',
    headers:{'Content-Type':'application/json'},body:JSON.stringify({name})});
  const p=await r.json();
  if(p.ok){adoptSpec(p); toast('Создано «'+(name||'Новое изделие')+'» — загрузите ТЗ или опишите в командной строке');}
  else toast('Ошибка: '+(p.error||''),true);
};
$('projRen').onclick=async()=>{   // переименовать текущее изделие
  const name=prompt('Название изделия:',SPEC&&SPEC.project_name||'');
  if(!name) return;
  const previousName=SPEC.project_name;SPEC.project_name=name;
  const r=await fetch('/api/save',{method:'POST',
    headers:{'Content-Type':'application/json'},body:JSON.stringify({spec:SPEC})});
  const p=await r.json();
  if(p.ok){fillForm();loadProjects();schedule();toast('Переименовано: '+name);}
  else{SPEC.project_name=previousName;toast('Ошибка: '+(p.error||''),true);}
};
/* ---------- каталог изделий (AKD-217) ---------- */
// аксонометрия не собралась (битая спека) → PNG-снапшот, если был, иначе заглушка
function thumbErr(img){
  const png=img.dataset.png;
  if(png){img.removeAttribute('data-png'); img.onerror=()=>{img.parentNode.textContent='🪑';}; img.src=png;}
  else img.parentNode.textContent='🪑';
}
const CAT_RULES=[  // раздел ← archetype/furniture_type
  ['Тумбы',   p=>/тумб/i.test(p.ftype)||['drawer_unit'].includes(p.archetype)],
  ['Столы',   p=>/стол/i.test(p.ftype)||['desk','table','round_table'].includes(p.archetype)],
  ['Шкафы',   p=>/шкаф|гардероб/i.test(p.ftype)||['wardrobe','door_unit','cabinet'].includes(p.archetype)],
  ['Стеллажи',p=>/стеллаж|полк/i.test(p.ftype)||['shelving'].includes(p.archetype)],
  ['Черновики',p=>p.draft],
];
let CAT_ITEMS=[], CAT_SEL='Все';
function catSection(p){
  if(p.draft) return 'Черновики';
  for(const [nm,fn] of CAT_RULES) if(fn(p)) return nm;
  return 'Прочее';
}
function renderCatalog(){
  const q=($('catQ').value||'').toLowerCase().trim();
  const secs=['Все',...new Set(CAT_ITEMS.map(catSection))];
  $('catCats').innerHTML=secs.map(s=>
    `<span class="catchip ${s===CAT_SEL?'on':''}" data-s="${s}">${s}</span>`).join('');
  $('catCats').querySelectorAll('.catchip').forEach(ch=>
    ch.onclick=()=>{CAT_SEL=ch.dataset.s; renderCatalog();});
  const items=CAT_ITEMS.filter(p=>
    (CAT_SEL==='Все'||catSection(p)===CAT_SEL)&&
    (!q||String(p.name).toLowerCase().includes(q)));
  $('catGrid').innerHTML=items.map(p=>`
    <div class="catCard" data-f="${p.file}">
      <div class="img">${p.draft?'✏️'
        :`<img src="/thumb/${encodeURIComponent(p.file)}" loading="lazy"
            data-png="${p.preview?`/preview/${encodeURIComponent(p.file.replace(/\.json$/,'.png'))}`:''}"
            onerror="thumbErr(this)">`}</div>
      <div class="nm" title="${p.name}">${p.name}${p.draft?'<span class="dr">черновик</span>':''}</div>
      <div class="sub">${p.dims}${p.decor?' · '+p.decor:''}</div>
    </div>`).join('')||'<div class="mini">ничего не найдено</div>';
  $('catGrid').querySelectorAll('.catCard').forEach(c=>
    c.onclick=async()=>{
      const r=await fetch('/api/open',{method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({file:c.dataset.f})});
      const p=await r.json();
      if(p.ok){$('catalog').classList.remove('on'); adoptSpec(p);}
      else toast('Ошибка: '+(p.error||''),true);
    });
}
$('projCat').onclick=async()=>{
  const r=await fetch('/api/projects',{method:'POST',
    headers:{'Content-Type':'application/json'},body:'{}'});
  const p=await r.json();
  CAT_ITEMS=p.projects||[]; CAT_SEL='Все'; $('catQ').value='';
  $('catalog').classList.add('on'); renderCatalog();
};
$('catClose').onclick=()=>$('catalog').classList.remove('on');
$('catQ').addEventListener('input',renderCatalog);

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
    const d=document.createElement('div'); d.className='ditem';d.setAttribute('role','listitem');
    d.innerHTML=`<button type="button" class="decor-body"
        aria-label="Применить ${it.label} к корпусу">
      <span class="sw" style="background:${it.hex}" aria-hidden="true"></span>
      <span class="nm" title="${it.name} (арт. ${it.article})">${it.label}</span>
      <span class="mini">${it.thickness??''}</span></button>
      <button type="button" class="fb" title="Применить к фасадам"
        aria-label="Применить ${it.label} к фасадам">Ф</button>`;
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
const UNDO=[], CHAT_HISTORY=[], OPERATIONS=new Map();
const OPERATION_LIMIT=30;
const CHAT_REQUEST_TIMEOUT_MS=130000;
let chatBusy=false, operationSeq=0, chatWorkspaceGeneration=0;
let activeChatController=null,chatAbortReason='',chatRequestInFlight=false;
let chatQuickUndoDepth=null, chatQuickUndoOperationId=null;
let chatQuickUndoBeforeSpecJson=null, chatQuickUndoAfterSpecJson=null;
function presentChatReply(text){
  const raw=String(text||'');
  const m=raw.match(/^Применил:\s*([a-z_]+)\s*=\s*(.+)$/i);
  if(!m)return raw;
  const labels={width:'Ширина изделия',depth:'Глубина изделия',height:'Высота изделия',
    color:'Материал корпуса',facade_color:'Материал фасадов',
    board_thickness:'Толщина плиты',legs:'Высота опор',gap:'Зазор'};
  const label=labels[m[1]];
  if(!label)return raw;
  const mm=['width','depth','height','board_thickness','legs','gap'].includes(m[1])?' мм':'';
  return `${label}: ${m[2]}${mm}`;
}
function operationReplyPreview(text){
  const value=presentChatReply(text).replace(/\s+/g,' ').trim();
  return value.length>220?value.slice(0,217).trimEnd()+'…':value;
}
function presentChatChange(change){
  let value=String(change||'');
  const paths={
    'dimensions.width':'Ширина','dimensions.depth':'Глубина','dimensions.height':'Высота',
    'materials.color':'Материал корпуса','materials.facade_color':'Материал фасадов',
    'materials.color_code':'Код материала','materials.board_thickness':'Толщина плиты',
    'legs.height':'Высота опор','gaps.default':'Зазор','apron_height':'Высота царги',
    'frame':'Каркас','archetype':'Тип изделия'
  };
  for(const [path,label] of Object.entries(paths))
    if(value.startsWith(path+':')) value=label+value.slice(path.length);
  const sectionFields={kind:'Тип',width:'Ширина',ratio:'Доля',drawers:'Ящики',
    shelves:'Полки',door:'Дверь'};
  value=value.replace(/^sections\.(\d+)\.([^:]+):/,(_,i,key)=>
    `Секция ${Number(i)+1} · ${sectionFields[key]||key}:`);
  if(/^(Ширина|Глубина|Высота|Толщина плиты|Высота опор|Зазор):/.test(value)
     && !value.endsWith(' мм')) value+=' мм';
  return value;
}
function operationChangeUnit(rawPath){
  return /^(dimensions\.(width|depth|height)|materials\.board_thickness|legs\.height|gaps\.default|apron_height|sections\.\d+\.(width|drawer_heights|shelf_levels)|overrides\.\d+\.placement\.(x1|x2|y1|y2|z1|z2))$/.test(rawPath)
    ?' мм':'';
}
function changeCountCaption(count){
  const mod100=count%100, mod10=count%10;
  const word=(mod100>=11&&mod100<=14)?'изменений'
    :mod10===1?'изменение':(mod10>=2&&mod10<=4)?'изменения':'изменений';
  return `${count} ${word}`;
}
function passWord(count){
  const mod100=count%100,mod10=count%10;
  return (mod100>=11&&mod100<=14)?'проходов'
    :mod10===1?'проход':(mod10>=2&&mod10<=4)?'прохода':'проходов';
}
function operationNode(tag,className,text){
  const node=document.createElement(tag);
  if(className) node.className=className;
  if(text!==undefined&&text!==null) node.textContent=String(text);
  return node;
}
function operationContextSnapshot(forceModel=false){
  if(!forceModel&&SELECTED_PART) return {scope:'part',label:`Деталь · ${SELECTED_PART.name}`,
    partName:SELECTED_PART.name,partType:SELECTED_PART.type||'',
    placement:{x1:SELECTED_PART.x1,x2:SELECTED_PART.x2,y1:SELECTED_PART.y1,
      y2:SELECTED_PART.y2,z1:SELECTED_PART.z1,z2:SELECTED_PART.z2}};
  return {scope:'model',label:'Всё изделие',partName:null};
}
function trimOperationCommand(text,attachmentCount){
  const value=String(text||'').trim();
  if(value)return value;
  return attachmentCount===1?'Техническое задание из вложения':
    `Техническое задание · ${attachmentCount} вложения`;
}
function setOperationStatus(operation,state,label){
  operation.state=state;
  operation.el.classList.remove('is-pending','is-applied','is-warning','is-answer','is-error','is-undone');
  operation.el.classList.add('is-'+state);
  operation.status.textContent=label;
}
function createOperation(command,{images=[],forceModel=false,kind='command'}={}){
  const id=`operation-${++operationSeq}`, context=operationContextSnapshot(forceModel);
  const attachmentMeta=(images||[]).map(im=>({name:im.name||'',mime:im.mime||''}));
  const el=operationNode('article','operation-record is-pending'); el.id=id;
  el.setAttribute('aria-labelledby',id+'-title');
  const head=operationNode('div','operation-head');
  const status=operationNode('span','operation-state','В работе');
  const time=operationNode('time','operation-time',new Date().toLocaleTimeString('ru-RU',
    {hour:'2-digit',minute:'2-digit'})); time.dateTime=new Date().toISOString();
  head.append(status,time);
  const title=operationNode('p','operation-command',trimOperationCommand(command,attachmentMeta.length));
  title.id=id+'-title';
  const contextLine=operationNode('div','operation-context',`Контекст: ${context.label}`+
    (attachmentMeta.length?` · ТЗ: ${attachmentMeta.length}`:''));
  const summary=operationNode('div','operation-summary','Разбираю команду и контекст…');
  const changes=operationNode('div','operation-changes'); changes.hidden=true;
  const more=operationNode('div','operation-more'); more.hidden=true;
  const check=operationNode('div','operation-check'); check.hidden=true;
  const actions=operationNode('div','operation-actions'); actions.hidden=true;
  const show=operationNode('button','operation-show','Показать'); show.type='button';
  show.hidden=true; show.title='Показать контекст команды в модели';
  const details=operationNode('button','operation-details','Подробнее'); details.type='button';
  details.hidden=true; details.setAttribute('aria-expanded','false');
  const undo=operationNode('button','operation-undo','Отменить'); undo.type='button'; undo.hidden=true;
  undo.title='Отменить эту операцию';
  actions.append(show,details,undo);
  const technical=operationNode('div','operation-technical'); technical.hidden=true;
  el.append(head,title,contextLine,summary,changes,more,check,actions,technical);
  const operation={id,kind,el,status,summary,changes,more,check,actions,show,details,undo,technical,
    context,attachments:attachmentMeta,provider:$('aiProvider').selectedOptions[0]?.textContent||CHAT_PROVIDER||'—',
    startedAt:time.dateTime,command:title.textContent,beforeSpecJson:JSON.stringify(SPEC),
    beforeIssueCount:issueCount(),undoDepthBefore:UNDO.length,state:'pending',rawChanges:[]};
  show.onclick=()=>showOperationTarget(operation.id);
  details.onclick=()=>{
    technical.hidden=!technical.hidden;
    details.setAttribute('aria-expanded',String(!technical.hidden));
    details.textContent=technical.hidden?'Подробнее':'Скрыть';
  };
  undo.onclick=()=>undoOperation(operation.id);
  OPERATIONS.set(id,operation); $('operationLog').hidden=false; $('chatlog').appendChild(el);
  while($('chatlog').children.length>OPERATION_LIMIT){
    const old=$('chatlog').firstElementChild; OPERATIONS.delete(old.id); old.remove();
  }
  requestAnimationFrame(()=>{const log=$('chatlog');log.scrollTop=log.scrollHeight;});
  return operation;
}
function updateOperationProgress(operation,text){
  if(!operation)return;
  setOperationStatus(operation,'pending','В работе');
  operation.summary.hidden=false; operation.summary.textContent=text;
}
function cleanOperationValue(value){
  let text=String(value||'').trim();
  if((text.startsWith("'")&&text.endsWith("'"))||(text.startsWith('"')&&text.endsWith('"')))
    text=text.slice(1,-1);
  return text.replace(/^\(нет\)$/,'—').replace(/^\(удалено\)$/,'Удалено')
    .replace(/^True$/,'Да').replace(/^False$/,'Нет').replace(/^None$/,'—');
}
function parseOperationChange(change){
  const raw=String(change||''),rawPath=(raw.match(/^([^:]+):/)||[])[1]||'';
  const human=presentChatChange(raw), match=human.match(/^(.+?):\s*(.*?)\s*→\s*(.*)$/);
  if(!match)return {plain:human};
  let before=cleanOperationValue(match[2]),after=cleanOperationValue(match[3]);
  const unit=operationChangeUnit(rawPath);
  if(unit){
    if(/^-?\d+(?:[.,]\d+)?$/.test(before))before+=unit;
    if(/^-?\d+(?:[.,]\d+)?$/.test(after))after+=unit;
  }else if(/^-?\d+(?:[.,]\d+)?$/.test(before)&&/\sмм$/.test(after))before+=' мм';
  return {label:match[1],before,after};
}
function renderOperationChanges(operation,rawChanges){
  operation.rawChanges=Array.isArray(rawChanges)?rawChanges.map(String):[];
  operation.changes.innerHTML='';
  const visible=operation.rawChanges.slice(0,3);
  visible.forEach(raw=>{
    const parsed=parseOperationChange(raw), row=operationNode('div','operation-change');
    if(parsed.plain){row.appendChild(operationNode('div','operation-change-plain',parsed.plain));}
    else{
      row.appendChild(operationNode('span','operation-change-label',parsed.label));
      const values=operationNode('div','operation-change-values');
      values.append(operationNode('span','operation-before',parsed.before),
        operationNode('span','operation-arrow','→'),operationNode('span','operation-after',parsed.after));
      row.appendChild(values);
    }
    operation.changes.appendChild(row);
  });
  operation.changes.hidden=!visible.length;
  const hiddenCount=operation.rawChanges.length-visible.length;
  operation.more.hidden=!hiddenCount;
  operation.more.textContent=hiddenCount?`Ещё ${hiddenCount} — в подробностях`:'';
}
function currentCheckSnapshot(){
  if(!lastPayload)return null;
  const details=[];let checkIssues=0;
  for(const [group,values] of Object.entries(lastPayload.issues||{})){
    checkIssues+=(values||[]).length;
    (values||[]).slice(0,4).forEach(value=>details.push(`[${group}] ${value}`));
  }
  const refEntries=Object.entries(lastPayload.refs||{})
    .filter(([,r])=>r&&typeof r==='object');
  const unresolvedEntries=refEntries.filter(([,r])=>!r.resolved),unresolved=unresolvedEntries.length;
  unresolvedEntries.forEach(([slot])=>details.push(`[база] не подобрано: ${slot}`));
  const baseText=refEntries.length?` · база ${refEntries.length-unresolved}/${refEntries.length}`:'';
  if(checkIssues)return {tone:'error',
    text:`Проверки модели: ${checkIssues} ${checkErrorWord(checkIssues)}${baseText}`,
    checkIssues,unresolved,ok:false,details};
  if(unresolved)return {tone:'warning',
    text:`Проверки модели пройдены${baseText} · ${unresolvedPositionCaption(unresolved)}`,
    checkIssues,unresolved,ok:true,details};
  return {tone:'ok',text:`Проверки модели пройдены${baseText}`,
    checkIssues:0,unresolved:0,ok:true,details};
}
function renderOperationTechnical(operation,reply,usage){
  operation.technical.innerHTML='';
  const humanReply=presentChatReply(reply||'');
  if(humanReply){
    operation.technical.append(operationNode('span','operation-tech-label','Ответ системы'),
      operationNode('div','operation-tech-reply',humanReply));
  }
  if(operation.rawChanges.length){
    operation.technical.append(operationNode('span','operation-tech-label','Технический diff'),
      operationNode('pre','operation-raw',operation.rawChanges.join('\n')));
  }
  if(operation.checkSnapshot){
    operation.technical.append(operationNode('span','operation-tech-label','Снимок проверок'),
      operationNode('div','operation-tech-meta',operation.checkSnapshot.text));
    if(operation.checkSnapshot.details&&operation.checkSnapshot.details.length)
      operation.technical.append(operationNode('pre','operation-raw',
        operation.checkSnapshot.details.join('\n')));
  }
  const meta=[`Модель: ${operation.provider}`];
  if(usage&&usage.total)meta.push(`Токены: ${Number(usage.total).toLocaleString('ru-RU')}`);
  if(operation.attachments.length)meta.push(`Вложения: ${operation.attachments.length}`);
  operation.technical.append(operationNode('span','operation-tech-label','Выполнение'),
    operationNode('div','operation-tech-meta',meta.join(' · ')));
  operation.details.hidden=false; operation.actions.hidden=false;
}
function finishOperation(operation,{state='applied',reply='',changes=[],usage=null,canUndo=false,
  summary='',checkSnapshot=null,statusLabel=''}={}){
  if(!operation)return;
  operation.reply=String(reply||''); operation.afterSpecJson=JSON.stringify(SPEC);
  operation.undoDepthAfter=UNDO.length; operation.undoBeforeSpecJson=canUndo?UNDO[UNDO.length-1]:null;
  operation.checkSnapshot=checkSnapshot;
  const count=Array.isArray(changes)?changes.length:0;
  const labels={applied:count?`Применено · ${changeCountCaption(count)}`:'Применено',
    warning:count?`Применено · ${changeCountCaption(count)}`:'Применено · есть замечания',
    answer:'Ответ',error:'Не выполнено',undone:'Отменено'};
  setOperationStatus(operation,state,statusLabel||labels[state]||labels.applied);
  const humanReply=operationReplyPreview(reply||'');
  const generic=/^(Готово\.?|Применено\.?|\(пусто\))$/i.test(humanReply)||
    (state!=='answer'&&/^Применил:\s*/i.test(operation.reply));
  const visibleSummary=summary||(state==='answer'?humanReply:(generic?'':humanReply));
  operation.summary.hidden=!visibleSummary;
  operation.summary.textContent=visibleSummary;
  renderOperationChanges(operation,changes);
  operation.check.classList.remove('warning','error');
  if(checkSnapshot){operation.check.hidden=false;operation.check.textContent=checkSnapshot.text;
    if(checkSnapshot.tone!=='ok')operation.check.classList.add(checkSnapshot.tone);}
  else operation.check.hidden=true;
  operation.show.hidden=!operation.context.partName;
  renderOperationTechnical(operation,reply,usage);
  operation.canUndo=!!canUndo;
  refreshOperationTargets(); refreshUndoState();
}
function failOperation(operation,message){
  finishOperation(operation,{state:'error',reply:String(message||''),summary:String(message||''),canUndo:false});
}
function markOperationUndone(operationId){
  const operation=OPERATIONS.get(operationId); if(!operation)return;
  operation.canUndo=false; setOperationStatus(operation,'undone','Отменено');
  operation.summary.hidden=false; operation.summary.textContent='Правка отменена, модель пересчитана.';
  if(operation.checkSnapshot){operation.check.hidden=false;
    operation.check.textContent='Снимок после применения · '+operation.checkSnapshot.text.replace(/^Проверки:\s*/, '');}
  operation.undo.hidden=true; refreshOperationTargets();
}
function operationRevisionMatches(operation){
  return !!(operation&&operation.canUndo&&operation.undoDepthAfter===UNDO.length&&
    UNDO[UNDO.length-1]===operation.undoBeforeSpecJson&&JSON.stringify(SPEC)===operation.afterSpecJson);
}
function quickUndoRevisionMatches(){
  return chatQuickUndoDepth!==null&&chatQuickUndoDepth===UNDO.length&&
    UNDO[UNDO.length-1]===chatQuickUndoBeforeSpecJson&&JSON.stringify(SPEC)===chatQuickUndoAfterSpecJson;
}
function refreshOperationUndoActions(){
  OPERATIONS.forEach(operation=>{
    const valid=operationRevisionMatches(operation);
    operation.undo.hidden=!valid; operation.undo.disabled=modelMutationLocked()||!valid;
    operation.undo.title=valid?'Отменить эту операцию':'Отмена доступна только для последней неизменённой ревизии';
    if(!operation.details.hidden||!operation.show.hidden||valid)operation.actions.hidden=false;
  });
}
function refreshUndoState(){
  $('btnUndo').disabled=modelMutationLocked()||!UNDO.length;
  const quick=$('chatUndoQuick'),valid=quickUndoRevisionMatches();
  if(quick){quick.hidden=!valid;quick.disabled=modelMutationLocked()||!valid;}
  refreshOperationUndoActions();
}
function clearQuickUndo(){
  chatQuickUndoDepth=null;chatQuickUndoOperationId=null;
  chatQuickUndoBeforeSpecJson=null;chatQuickUndoAfterSpecJson=null;
}
function pushUndo(){UNDO.push(JSON.stringify(SPEC));
  if(UNDO.length>30)UNDO.shift(); refreshUndoState();}
async function undoLastChange(){
  if(modelMutationLocked()||!UNDO.length)return;
  const matchingOperation=[...OPERATIONS.values()].reverse().find(operationRevisionMatches);
  const operationId=matchingOperation?matchingOperation.id:
    (quickUndoRevisionMatches()?chatQuickUndoOperationId:null);
  const current=JSON.stringify(SPEC),previous=UNDO.pop();
  setChatBusy(true,'Отменяю последнюю правку…');
  try{
    SPEC=JSON.parse(previous); showEmpty(!!SPEC.draft); fillForm();
    const generated=await apply();
    if(!SPEC.draft&&(!generated||!generated.viewer))throw new Error('модель не пересчитана');
    if(operationId)markOperationUndone(operationId);
    clearQuickUndo(); setChatState('Последняя правка отменена',false,'success',false);
  }catch(e){
    SPEC=JSON.parse(current); UNDO.push(previous); showEmpty(!!SPEC.draft); fillForm();
    try{await apply();}catch(_restoreError){}
    const operation=OPERATIONS.get(operationId);
    if(operation){operation.summary.hidden=false;
      operation.summary.textContent='Не удалось отменить правку: '+e.message;}
    setChatState('Не удалось отменить последнюю правку',true,'error');
  }finally{setChatBusy(false);refreshUndoState();}
}
$('btnUndo').onclick=undoLastChange;
function undoOperation(operationId){
  const operation=OPERATIONS.get(operationId);
  if(!operationRevisionMatches(operation)){refreshUndoState();toast('Эту операцию уже нельзя отменить отдельно',true);return;}
  chatQuickUndoDepth=operation.undoDepthAfter;chatQuickUndoOperationId=operation.id;
  chatQuickUndoBeforeSpecJson=operation.undoBeforeSpecJson;
  chatQuickUndoAfterSpecJson=operation.afterSpecJson;undoLastChange();
}
function showOperationTarget(operationId){
  const operation=OPERATIONS.get(operationId),name=operation&&operation.context.partName;
  if(!name)return;
  const panels=((lastPayload&&lastPayload.viewer&&lastPayload.viewer.panels)||[]);
  const index=panels.findIndex(panel=>panel.name===name);
  if(index<0){operation.show.hidden=true;toast('Деталь из контекста больше не найдена',true);return;}
  switchTab('3d');
  scene3d.select(index);
}
function refreshOperationTargets(){
  const panels=((lastPayload&&lastPayload.viewer&&lastPayload.viewer.panels)||[]);
  OPERATIONS.forEach(operation=>{
    operation.show.hidden=!operation.context.partName||!panels.some(panel=>panel.name===operation.context.partName);
    if(!operation.details.hidden||!operation.show.hidden||!operation.undo.hidden)operation.actions.hidden=false;
  });
}
function resetOperationLog(){
  OPERATIONS.clear();operationSeq=0;clearQuickUndo();$('chatlog').innerHTML='';
  $('operationLog').hidden=true;refreshUndoState();
}
function setChatState(text,error=false,mode='',canUndo=false,operationId=null){
  const state=$('chatState'); state.textContent=text||'';
  state.classList.toggle('error',!!error);
  const dock=$('fs_chat');
  dock.classList.toggle('has-state',!!text);
  dock.classList.toggle('state-error',!!error);
  dock.classList.toggle('state-success',mode==='success'&&!error);
  const actions=$('chatResultActions');
  actions.hidden=mode!=='success'||!text;
  if(canUndo){chatQuickUndoDepth=UNDO.length;chatQuickUndoOperationId=operationId;
    chatQuickUndoBeforeSpecJson=UNDO[UNDO.length-1]||null;
    chatQuickUndoAfterSpecJson=JSON.stringify(SPEC);}
  else clearQuickUndo();
  refreshUndoState();
}
function modelMutationLocked(){return chatBusy||partEditBusy;}
function syncChatPrimaryAction(){
  const button=$('chatSend'),canCancel=!!(chatBusy&&activeChatController&&
    chatRequestInFlight&&!activeChatController.signal.aborted),
    stopping=!!(chatBusy&&chatAbortReason==='user'&&activeChatController&&
      activeChatController.signal.aborted);
  button.classList.toggle('is-cancel',canCancel);
  button.disabled=chatBusy?!canCancel:partEditBusy;
  $('chatSendLabel').textContent=canCancel?'Остановить':stopping?'Останавливаю…':
    chatBusy?'Выполняю…':'Выполнить';
  button.title=canCancel?'Остановить выполнение команды':'Выполнить команду';
  button.setAttribute('aria-label',button.title);
}
function syncModelEditLock(){
  const locked=modelMutationLocked();
  $('sideScroll').inert=locked;
  Object.entries(rightPanelModes).forEach(([key,item])=>
    item.panel.inert=locked||key!==rightPanelMode);
  [$('chatMsg'),$('chatAttach'),$('aiProvider'),$('partChat'),$('partChatSend')]
    .filter(Boolean).forEach(el=>el.disabled=locked);
  $('btnFixAll').disabled=locked;
  syncChatPrimaryAction();
  syncViewportStatus();
}
function setChatBusy(busy,stateText){
  chatBusy=!!busy;
  $('fs_chat').setAttribute('aria-busy',String(chatBusy));
  $('fs_chat').classList.toggle('is-busy',chatBusy);
  [$('chatMsg'),$('chatAttach'),$('aiProvider'),$('partChat'),$('partChatSend')]
    .filter(Boolean).forEach(el=>el.disabled=chatBusy);
  syncModelEditLock();
  if(currentSelectedPart())syncPartEditState();
  refreshUndoState();
  if(stateText!==undefined) setChatState(stateText,false,chatBusy?'busy':'');
}
// Пока AI считает, 3D остаётся доступной для вращения, выбора и открытия.
// Единственная запрещённая операция — Shift+drag, потому что она меняет ParamSpec.
view.addEventListener('pointerdown',event=>{
  if(!modelMutationLocked()||!event.shiftKey)return;
  event.preventDefault();event.stopPropagation();
  toast('Пока выполняется команда, деталь нельзя перемещать. Осмотр модели доступен.',true);
},true);
function beginChatRequest(){
  activeChatController=new AbortController();chatAbortReason='';
  return activeChatController;
}
async function requestChat(payload,controller){
  chatRequestInFlight=true;syncChatPrimaryAction();
  const timeout=setTimeout(()=>{
    if(!controller.signal.aborted){chatAbortReason='timeout';controller.abort();}
  },CHAT_REQUEST_TIMEOUT_MS);
  try{return await fetch('/api/chat',{method:'POST',
    headers:{'Content-Type':'application/json'},body:JSON.stringify(payload),signal:controller.signal});}
  finally{clearTimeout(timeout);chatRequestInFlight=false;syncChatPrimaryAction();}
}
function cancelActiveChatRequest(){
  if(!chatBusy||!chatRequestInFlight||!activeChatController||
     activeChatController.signal.aborted)return;
  chatAbortReason='user';activeChatController.abort();syncChatPrimaryAction();
  setChatState('Останавливаю команду…',false,'busy');
}
function finishChatRequest(controller){
  if(activeChatController===controller){activeChatController=null;chatAbortReason='';
    chatRequestInFlight=false;}
}
const PENDING_IMGS=[];                       // фото ТЗ: [{mime,data(base64)}]
function renderImgs(){
  $('chatImgs').innerHTML=PENDING_IMGS.map((im,i)=>
    `<span class="chip"><img alt="Прикреплённое изображение ТЗ" src="data:${im.mime};base64,${im.data}">`+
    `<button class="chip-remove" type="button" data-rm="${i}" aria-label="Убрать изображение" title="Убрать изображение">`+
    `<svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="m7 7 10 10M17 7 7 17"/></svg>`+
    `</button></span>`).join('');
  $('chatImgs').querySelectorAll('[data-rm]').forEach(b=>
    b.onclick=()=>{PENDING_IMGS.splice(+b.dataset.rm,1);renderImgs();});
  syncCommandStackHeight();
}
function addImgFile(file){
  const r=new FileReader();
  r.onload=()=>{const s=String(r.result),c=s.indexOf(',');
    PENDING_IMGS.push({mime:(file.type||'image/png'),data:s.slice(c+1),name:file.name||''});renderImgs();};
  r.readAsDataURL(file);
}
// реальная диагностика для ИИ (AKD-219): тексты ошибок чеков + слоты базы
function diagCtx(){
  const ctx=lastPayload?{n_panels:lastPayload.stats&&lastPayload.stats.n_panels,
    n_holes:lastPayload.stats&&lastPayload.stats.n_holes,
    dims:lastPayload.stats&&lastPayload.stats.dims,
    estimate_total:lastPayload.estimate&&lastPayload.estimate.total}:{};
  // реальная геометрия деталей — чтобы ИИ добавлял/двигал панели по фактическим
  // координатам соседей, а не вслепую (перегородки, полки, примыкание встык)
  if(lastPayload&&lastPayload.viewer&&lastPayload.viewer.panels)
    ctx.panels=lastPayload.viewer.panels.slice(0,80).map(p=>({n:p.name,t:p.type,
      x:[Math.round(p.x1),Math.round(p.x2)],y:[Math.round(p.y1),Math.round(p.y2)],
      z:[Math.round(p.z1),Math.round(p.z2)]}));
  if(lastPayload&&lastPayload.issues){
    const bad={};
    for(const k of Object.keys(lastPayload.issues)){
      const v=lastPayload.issues[k]||[];
      if(v.length) bad[k]=v.slice(0,3);
    }
    ctx.check_errors=Object.keys(bad).length?bad:'нет — все проверки зелёные';
  }
  if(lastPayload&&lastPayload.refs){
    const un=Object.entries(lastPayload.refs)
      .filter(([k,r])=>r&&typeof r==='object'&&!r.resolved).map(([k])=>k);
    ctx.base_unresolved=un.length?un:'все позиции подобраны';
  }
  return ctx;
}
// сколько проблем осталось (для автоцикла починки)
function issueCount(){
  return modelDiagnosticCounts().total;
}
function requireCleanPartDraft(){
  const draft=currentSelectedPart()?readPartDraft():null;
  if(!draft||!draft.dirty)return true;
  $('partExact').open=true;syncPartEditState();
  toast('Сначала примените или отмените точные значения выбранной детали.',true);
  return false;
}
async function runChat(text){
  const m=(text||'').trim();
  if((!m&&!PENDING_IMGS.length)||modelMutationLocked())return false;
  if(!requireCleanPartDraft())return false;
  const imgs=PENDING_IMGS.splice(0); renderImgs();
  const operation=createOperation(m,{images:imgs});
  const requestSpecJson=JSON.stringify(SPEC),requestGeneration=chatWorkspaceGeneration;
  let rollbackSpec=null, rollbackUndoDepth=null;
  const controller=beginChatRequest();
  setChatBusy(true,'Разбираю команду и контекст…');
  try{
    const ctx=diagCtx();
    if(operation.context.partName)ctx.selected_part={name:operation.context.partName,
      type:operation.context.partType,placement:operation.context.placement};
    const r=await requestChat({spec:JSON.parse(requestSpecJson),message:m,history:CHAT_HISTORY,
      context:ctx,images:imgs.length?imgs:null,provider:CHAT_PROVIDER},controller);
    let p={};
    try{p=await r.json();}catch(e){throw new Error(`Сервер вернул ответ ${r.status} без данных`);}
    if(!r.ok) throw new Error(p.reply||p.error||`Ошибка запроса (${r.status})`);
    if(p.error&&!p.spec) throw new Error(String(p.error));
    if(controller.signal.aborted)throw new DOMException('Команда остановлена','AbortError');
    if(p.usage&&p.usage.total){SESSION_TOKENS+=p.usage.total; renderTokens();}
    refreshBalance();                              // остаток бесплатных токенов
    if(chatWorkspaceGeneration!==requestGeneration)
      throw new Error('За время выполнения открыто другое изделие. Команда отменена.');
    if(JSON.stringify(SPEC)!==requestSpecJson)
      throw new Error('Модель изменилась во время выполнения. Повторите команду для актуальной версии.');
    if(p.spec){
      updateOperationProgress(operation,'Пересчитываю модель и инженерные проверки…');
      setChatState('Пересчитываю модель и инженерные проверки…',false,'busy');
      const wasDraft=!!(SPEC&&SPEC.draft);
      rollbackSpec=JSON.stringify(SPEC); rollbackUndoDepth=UNDO.length;
      pushUndo(); SPEC=p.spec; showEmpty(false); fillForm();
      const generated=await apply();
      if(!generated||!generated.viewer)throw new Error('Движок не вернул пересчитанную 3D-модель.');
      if(controller.signal.aborted)throw new DOMException('Команда остановлена','AbortError');
      if(wasDraft||p.created){                     // создано из черновика — в базу сразу
        await saveSpec();                          // с превью для каталога
        loadProjects();
      }
    }
    CHAT_HISTORY.push({role:'user',text:m},{role:'assistant',text:p.reply||''});
    if(CHAT_HISTORY.length>16)CHAT_HISTORY.splice(0,CHAT_HISTORY.length-16);
    const changeCount=Array.isArray(p.changes)?p.changes.length:0;
    const checkSnapshot=p.spec?currentCheckSnapshot():null;
    const operationState=p.spec&&checkSnapshot&&checkSnapshot.tone!=='ok'?'warning':
      (p.spec?'applied':'answer');
    const answerSummary=p.spec?'':
      ((operationReplyPreview(p.reply||'')?operationReplyPreview(p.reply||'')+'\n':'')+
       'Модель не изменялась.');
    finishOperation(operation,{state:operationState,reply:p.reply||'',changes:p.changes||[],
      usage:p.usage,canUndo:!!p.spec,summary:answerSummary,checkSnapshot});
    const resultText=p.spec
      ?(changeCount?`Готово · ${changeCountCaption(changeCount)}`
        :'Готово · модель пересчитана')
      :'Ответ готов · подробности слева';
    setChatState(resultText,false,'success',!!p.spec,operation.id);
    return true;
  }catch(e){
    const abortReason=e&&e.name==='AbortError'?chatAbortReason:'';
    const workspaceChanged=chatWorkspaceGeneration!==requestGeneration;
    if(rollbackSpec!==null){
      SPEC=JSON.parse(rollbackSpec); UNDO.splice(rollbackUndoDepth); refreshUndoState();
      showEmpty(!!SPEC.draft); scene3d.select(null); fillForm();
      try{await apply();}catch(_restoreError){}
    }
    if(!workspaceChanged){
      if(imgs.length) PENDING_IMGS.unshift(...imgs);
      if(m&&!$('chatMsg').value.trim()) $('chatMsg').value=m;
      resizeChatInput(); renderImgs();
      if(abortReason==='user'){
        finishOperation(operation,{state:'undone',summary:'Остановлено пользователем. Модель не изменена.',
          statusLabel:'Остановлено'});
        setChatState('Команда остановлена · текст и вложения сохранены',false,'');
      }else if(abortReason==='timeout'){
        failOperation(operation,'AI не ответил за отведённое время. Модель не изменена.');
        setChatState('AI не ответил · команду можно повторить',true,'error');
      }else{
        failOperation(operation,'Команда не применена: '+e.message);
        setChatState('Команда не выполнена · текст и вложения сохранены',true,'error');
      }
    }else toast('Команда отменена: открыто другое изделие',true);
    return false;
  }finally{finishChatRequest(controller);setChatBusy(false);}
}
function sendChat(){
  if(chatBusy){cancelActiveChatRequest();return;}
  if(modelMutationLocked())return;
  if(!requireCleanPartDraft())return;
  const v=$('chatMsg').value;
  if(!v.trim()&&!PENDING_IMGS.length)return;
  $('chatMsg').value='';
  resizeChatInput();
  runChat(v);
}
// автоцикл «Починить всё» (AKD-222): ИИ правит → регенерация → перепроверка,
// до зелёных бейджей / отсутствия прогресса / 3 итераций
async function fixAll(){
  if(modelMutationLocked()) return;
  if(!requireCleanPartDraft())return;
  const startIssues=issueCount();
  if(!startIssues){toast('Все проверки зелёные, база подобрана — чинить нечего');return;}
  const operation=createOperation('Автоматическое исправление проверок',{forceModel:true,kind:'fix-all'});
  const requestGeneration=chatWorkspaceGeneration;
  let before=startIssues,appliedPasses=0,stopReason='',operationTokens=0;
  const allChanges=[],replies=[];
  const controller=beginChatRequest();
  setChatBusy(true,'Проверяю, что можно исправить автоматически…');
  try{
    for(let it=1; it<=3; it++){
      updateOperationProgress(operation,`Проход ${it} из 3 · осталось ${before} ${findingWord(before)}`);
      setChatState(`Автоисправление · проход ${it} из 3`,false,'busy');
      const passSpecJson=JSON.stringify(SPEC),passUndoDepth=UNDO.length;
      const r=await requestChat({spec:JSON.parse(passSpecJson),
          message:'Почини все перечисленные проблемы: ошибки проверок и неподобранные '
                 +'позиции базы. Меняй только то, что нужно для починки. '
                 +'Детали, добавленные пользователем (overrides с action:"add"), '
                 +'УДАЛЯТЬ ЗАПРЕЩЕНО — вместо удаления подгони их placement до '
                 +'примыкания встык по координатам соседей из context.panels.',
          history:[],context:diagCtx(),provider:CHAT_PROVIDER},controller);
      const p=await r.json();
      if(!r.ok) throw new Error(p.reply||p.error||`Ошибка запроса (${r.status})`);
      if(p.error&&!p.spec)throw new Error(String(p.error));
      if(controller.signal.aborted)throw new DOMException('Автоисправление остановлено','AbortError');
      if(p.usage&&p.usage.total){operationTokens+=p.usage.total;
        SESSION_TOKENS+=p.usage.total; renderTokens();}
      if(chatWorkspaceGeneration!==requestGeneration)
        throw new Error('За время выполнения открыто другое изделие.');
      if(JSON.stringify(SPEC)!==passSpecJson)
        throw new Error('Модель изменилась во время выполнения. Автоисправление остановлено.');
      if(!p.spec){stopReason=p.reply||'Автоматическая правка не предложена — нужна ручная проверка.';
        replies.push(stopReason);break;}
      pushUndo(); SPEC=p.spec; fillForm();
      let generated;
      try{
        generated=await apply();                   // регенерация + свежие бейджи
        if(!generated||!generated.viewer)throw new Error('Движок не вернул пересчитанную 3D-модель.');
      }catch(error){
        SPEC=JSON.parse(passSpecJson);UNDO.splice(passUndoDepth);refreshUndoState();fillForm();
        try{await apply();}catch(_restoreError){}
        throw error;
      }
      if(controller.signal.aborted)throw new DOMException('Автоисправление остановлено','AbortError');
      const after=issueCount();
      appliedPasses++;
      if(p.reply)replies.push(`Проход ${it}: ${p.reply}`);
      if(Array.isArray(p.changes))allChanges.push(...p.changes);
      allChanges.push(`Проблемы: ${before} → ${after}`);
      if(!after){toast('Всё исправлено — проверки зелёные'); break;}
      if(after>=before){stopReason='Количество замечаний не уменьшилось — проверьте их вручную.';break;}
      before=after;
    }
    const remaining=issueCount(),stillHasIssues=!!remaining;
    const state=stillHasIssues?'warning':'applied';
    const summary=stillHasIssues
      ?`${startIssues} → ${remaining} ${findingWord(remaining)} · ${appliedPasses} ${passWord(appliedPasses)}. ${stopReason}`.trim()
      :`Все ${startIssues} ${findingWord(startIssues)} исправлены за ${appliedPasses} ${passWord(appliedPasses)}.`;
    finishOperation(operation,{state,reply:replies.join('\n'),changes:allChanges,
      usage:operationTokens?{total:operationTokens}:null,canUndo:false,summary,
      checkSnapshot:currentCheckSnapshot(),
      statusLabel:stillHasIssues?(appliedPasses?'Исправлено частично':'Требуется вручную'):'Исправлено'});
    setChatState(stillHasIssues?`Автоисправление завершено · осталось ${remaining} ${findingWord(remaining)}`:
      'Все замечания исправлены',
      false,'success',false,operation.id);
  }catch(e){
    const abortReason=e&&e.name==='AbortError'?chatAbortReason:'';
    const workspaceChanged=chatWorkspaceGeneration!==requestGeneration;
    if(!workspaceChanged&&appliedPasses){
      const remaining=issueCount();
      finishOperation(operation,{state:'warning',reply:replies.join('\n'),changes:allChanges,
        usage:operationTokens?{total:operationTokens}:null,canUndo:false,
        summary:abortReason==='user'
          ?`Остановлено пользователем после ${appliedPasses} ${passWord(appliedPasses)}. Осталось ${remaining} ${findingWord(remaining)}.`
          :`Остановлено после ${appliedPasses} ${passWord(appliedPasses)}: ${e.message}`,
        checkSnapshot:currentCheckSnapshot(),statusLabel:'Исправлено частично'});
      setChatState('Автоисправление остановлено · часть правок применена',abortReason!=='user',
        abortReason==='user'?'':'error');
    }else if(!workspaceChanged&&abortReason==='user'){
      finishOperation(operation,{state:'undone',summary:'Остановлено пользователем. Модель не изменена.',
        statusLabel:'Остановлено'});
      setChatState('Автоисправление остановлено · модель не изменена',false,'');
    }else if(!workspaceChanged&&abortReason==='timeout'){
      failOperation(operation,'AI не ответил за отведённое время. Модель не изменена.');
      setChatState('AI не ответил · автоисправление остановлено',true,'error');
    }else if(!workspaceChanged){
      failOperation(operation,'Автоисправление не выполнено: '+e.message);
      setChatState('Автоисправление не выполнено',true,'error');
    }else toast('Автоисправление отменено: открыто другое изделие',true);
  }finally{finishChatRequest(controller);setChatBusy(false); refreshBalance();}
}
$('btnFixAll').onclick=fixAll;
$('chatShowLog').onclick=()=>{
  const log=$('operationLog');
  if(log.hidden)return;
  const history=$('chatlog');history.scrollTop=history.scrollHeight;
  history.focus({preventScroll:true});
};
$('chatUndoQuick').onclick=()=>$('btnUndo').click();
$('chatSend').onclick=sendChat;
function resizeChatInput(){
  const input=$('chatMsg');
  input.style.height='40px';
  input.style.height=Math.max(40,Math.min(input.scrollHeight,96))+'px';
  syncCommandStackHeight();
}
function syncCommandStackHeight(){
  $('main').style.setProperty('--chat-stack-height',Math.ceil($('fs_chat').getBoundingClientRect().height)+'px');
}
$('chatMsg').addEventListener('input',resizeChatInput);
$('chatMsg').addEventListener('keydown',e=>{
  if(e.key==='Enter'&&(e.ctrlKey||e.metaKey)){e.preventDefault();sendChat();}
});
resizeChatInput();
if(window.ResizeObserver) new ResizeObserver(syncCommandStackHeight).observe($('fs_chat'));
syncCommandStackHeight();
// фото ТЗ: кнопка-скрепка, выбор файла, вставка из буфера, drag&drop
$('chatAttach').onclick=()=>$('chatFile').click();
$('chatFile').onchange=e=>{[...e.target.files].forEach(addImgFile); e.target.value='';};
$('chatMsg').addEventListener('paste',e=>{
  for(const it of e.clipboardData.items) if(it.type.startsWith('image/')) addImgFile(it.getAsFile());});
$('chatMsg').addEventListener('dragover',e=>{e.preventDefault();$('chatMsg').classList.add('drop');});
$('chatMsg').addEventListener('dragleave',()=>$('chatMsg').classList.remove('drop'));
$('chatMsg').addEventListener('drop',e=>{e.preventDefault();$('chatMsg').classList.remove('drop');
  [...e.dataTransfer.files].forEach(f=>{if(f.type.startsWith('image/'))addImgFile(f);});});
// выбор нейросети (AKD-210) + счётчик токенов/лимитов выбранного провайдера
let CHAT_PROVIDER=null, SESSION_TOKENS=0, BAL_ITEMS=null, BAL_ERR=null;
function renderTokens(){
  const el=$('tokenCount'); if(!el) return;
  const parts=[]; let low=false;
  if(SESSION_TOKENS) parts.push('за сессию: '+SESSION_TOKENS.toLocaleString('ru-RU')+' ток.');
  (BAL_ITEMS||[]).forEach(it=>{
    const v=Number(it.value);
    parts.push(it.label+': '+v.toLocaleString('ru-RU')+' '+(it.unit||''));
    if((it.unit==='ток.'&&v<10000)||(it.unit&&it.unit!=='ток.'&&v<=0)) low=true;  // мало/нет
  });
  if(BAL_ERR){parts.push('лимит: '+BAL_ERR); low=true;}
  el.textContent=parts.join('  ·  ')+(low?' · требуется пополнение или доступ':'');
  el.style.color=low?'var(--bad)':'var(--mut)';
}
async function refreshBalance(){
  try{
    const r=await fetch('/api/token-balance',{method:'POST',
      headers:{'Content-Type':'application/json'},body:JSON.stringify({provider:CHAT_PROVIDER})});
    const d=await r.json();
    BAL_ITEMS=d.items||null; BAL_ERR=d.error?String(d.error).slice(0,60):null; renderTokens();
  }catch(e){}
}
async function loadProviders(){
  try{
    const r=await fetch('/api/providers',{method:'POST',
      headers:{'Content-Type':'application/json'},body:'{}'});
    const d=await r.json();
    const sel=$('aiProvider');
    sel.innerHTML=(d.providers||[]).map(p=>`<option value="${p.id}">${p.name}</option>`).join('');
    CHAT_PROVIDER=d.active; sel.value=d.active;
    sel.onchange=()=>{CHAT_PROVIDER=sel.value; SESSION_TOKENS=0; refreshBalance();};
  }catch(e){}
  refreshBalance();
}
loadProviders();

/* ---------- экспорт ---------- */
async function post(url){const r=await fetch(url,{method:'POST',
  headers:{'Content-Type':'application/json'},body:JSON.stringify({spec:SPEC})});
  return await r.json();}
// сохранение со снапшотом-превью для каталога (AKD-217)
async function saveSpec(){
  const prev=(scene3d.snapshot&&!EMPTY)?scene3d.snapshot(320):null;
  const r=await fetch('/api/save',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({spec:SPEC,preview:prev})});
  return await r.json();
}
$('btnSave').onclick=async()=>{const p=await saveSpec();
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

/* ---------- drag&drop ТЗ в область 3D / пустой экран (AKD-135/214) ---------- */
const stage=$('main');
stage.addEventListener('dragover',e=>{e.preventDefault();
  stage.style.outline='3px dashed var(--accent)'; $('emptyState').classList.add('drop');});
stage.addEventListener('dragleave',()=>{stage.style.outline=''; $('emptyState').classList.remove('drop');});
stage.addEventListener('drop',e=>{
  e.preventDefault(); stage.style.outline=''; $('emptyState').classList.remove('drop');
  importTzFile(e.dataTransfer.files && e.dataTransfer.files[0]);   // FileReader — без краха на больших фото
});

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
fillForm(); resize(); apply().catch(()=>{});
</script></body></html>"""
