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
import hmac
import base64
import hashlib
import logging
import secrets
import threading
import unicodedata
import webbrowser
from email.utils import formatdate
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlsplit


AI_LOGGER = logging.getLogger("akeda.studio.ai")

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
    from .telemetry import hash_payload, span
    read_metrics: dict[str, Any] | None = None
    revision_spec = spec
    try:
        from .paramspec_versioning import read_paramspec_v1

        envelope = read_paramspec_v1(spec)
        read_metrics = envelope.metrics()
        # Catalog/ownership metadata is not generator input.  Unknown extension
        # fields are reported by the adapter but cannot reach geometry.
        spec = envelope.payload_dict()
    except (TypeError, ValueError):
        # The production gate below remains the authoritative source of stable
        # validation errors for malformed known fields and unsupported versions.
        pass
    revision = _spec_revision(revision_spec)
    trace_attrs = {"revision.hash": hash_payload(revision_spec)}
    issues: dict[str, list[str]] = {"schema": [], "consistency": [], "geometry": [],
                                    "cfrn": [], "holes": [], "drilling": [],
                                    "completeness": [], "materials": []}
    from .production_gate import evaluate_production_gate

    with span("quality-check.production-gate", {
        **trace_attrs, "check.name": "production_gate",
    }) as gate_span:
        decision = evaluate_production_gate(spec)
        gate_span.set_attributes({
            "check.outcome": "pass" if decision.report.ok else "fail",
            "error.codes": [problem.code for problem in decision.report.errors],
        })
    stage_to_issue = {
        "pydantic": "schema",
        "json_schema": "schema",
        "generate": "schema",
        "consistency": "consistency",
        "geometry": "geometry",
        "bounds": "geometry",
        "cfrn_encoding": "cfrn",
        "cfrn_holes_parity": "holes",
        "drilling_geometry": "drilling",
        "system_32": "drilling",
        "purpose_registry": "drilling",
        "completeness": "completeness",
        "materials": "materials",
    }
    for check in decision.report.checks:
        target = stage_to_issue.get(check.name)
        for problem in check.issues:
            if problem.severity != "error":
                continue
            if target:
                issues[target].append(problem.detail)
    project = decision.project
    exposed_checks = {
        "schema": ("pydantic", "json_schema", "generate"),
        "consistency": ("consistency",),
        "geometry": ("geometry", "bounds"),
        "cfrn": ("cfrn_encoding",),
        "holes": ("cfrn_holes_parity",),
        "drilling": ("drilling_geometry", "system_32", "purpose_registry"),
    }
    checks_by_name = {check.name: check for check in decision.report.checks}
    for public_name, stage_names in exposed_checks.items():
        errors = [problem.code for stage_name in stage_names
                  for problem in (checks_by_name[stage_name].issues
                                  if stage_name in checks_by_name else [])
                  if problem.severity == "error"]
        with span(f"quality-check.{public_name}", {
            **trace_attrs,
            "check.name": public_name,
            "check.outcome": "fail" if errors else "pass",
            "error.codes": errors,
        }):
            pass
    if project is None:
        result = {"ok": False, "issues": issues, "revision": revision,
                  "check_report": decision.report.to_dict()}
        if read_metrics is not None:
            result["paramspec_read"] = read_metrics
        return result

    from .webviewer import viewer_payload
    from .delivery import _hardware_bom, spec_summary
    s = spec_summary(project)
    payload = {
        "ok": decision.report.ok,
        "revision": revision,
        "issues": issues,
        "check_report": decision.report.to_dict(),
        "warnings": [warning.to_dict() for warning in decision.report.warnings],
        "viewer": viewer_payload(project),      # панели+присадки+фурнитура+открывашки
        "stats": {"n_panels": s["n_panels"], "n_holes": s["n_holes"],
                  "dims": s["dims"], "decor": s["decor"]},
        "bom": _hardware_bom(project),
        "refs": project.get("material_refs") or {},   # слоты фурнитуры для выбора (A4)
    }
    if read_metrics is not None:
        payload["paramspec_read"] = read_metrics
    try:
        from .estimate import estimate_project
        payload["estimate"] = estimate_project(project)   # смета live (C1)
    except Exception as e:
        payload["estimate"] = {"rows": [], "total": 0, "currency": "₽",
                               "warnings": [f"смета: {e}"]}
    return payload


def _trace_engineering_result(spec: dict[str, Any] | None) -> None:
    """Run deterministic engineering inside the active AI trace without persisting data."""

    if not isinstance(spec, dict):
        return
    from .telemetry import add_current_attributes

    payload = build_payload(spec)
    stats = payload.get("stats") if isinstance(payload, dict) else {}
    issues = payload.get("issues") if isinstance(payload, dict) else {}
    error_codes = [
        f"{name}_failed" for name, values in dict(issues or {}).items() if values
    ]
    add_current_attributes({
        "panel.count": dict(stats or {}).get("n_panels"),
        "hole.count": dict(stats or {}).get("n_holes"),
        "check.outcome": "pass" if payload.get("ok") else "fail",
        "error.codes": error_codes,
    })


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


_CATALOG_IDENTITY_FIELDS = (
    "creator_user_id",
    "responsible_user_id",
    "author",
    "responsible",
)


def _catalog_section(*, archetype: str, furniture_type: str, draft: bool) -> str:
    """Stable server-owned type facet used by the catalog API."""

    if draft:
        return "Черновики"
    kind = furniture_type.casefold()
    if "тумб" in kind or archetype == "drawer_unit":
        return "Тумбы"
    if "стол" in kind or archetype in {"desk", "table", "round_table"}:
        return "Столы"
    if any(word in kind for word in ("шкаф", "гардероб")) or archetype in {
        "wardrobe",
        "door_unit",
        "cabinet",
    }:
        return "Шкафы"
    if any(word in kind for word in ("стеллаж", "полк")) or archetype == "shelving":
        return "Стеллажи"
    return "Прочее"


def _catalog_actor(auth: dict[str, Any] | None) -> dict[str, str] | None:
    """Return a real organization member, never a support impersonator."""

    context = (auth or {}).get("context") or {}
    user = context.get("user") or {}
    membership = context.get("membership") or {}
    user_id = str(user.get("id") or "")
    if not user_id or str(membership.get("user_id") or "") != user_id:
        return None
    return {
        "user_id": user_id,
        "display_name": str(user.get("display_name") or user.get("email") or "Сотрудник"),
        "role": str(membership.get("role") or ""),
    }


def _catalog_members(studio: Any, auth: dict[str, Any] | None) -> list[dict[str, str]]:
    if auth is None or studio.identity_store is None:
        return []
    context = auth.get("context") or {}
    user = context.get("user") or {}
    organization = context.get("organization") or {}
    session = context.get("session") or {}
    support = context.get("support_session") or {}
    rows = studio.identity_store.list_project_collaborators(
        str(user.get("id") or ""),
        str(organization.get("id") or ""),
        support_session_id=str(support.get("id") or "") or None,
        primary_session_id=str(session.get("id") or "") or None,
    )
    return [
        {
            "user_id": str(row.get("user_id") or ""),
            "display_name": str(row.get("display_name") or "Сотрудник"),
            "role": str(row.get("role") or ""),
        }
        for row in rows
        if row.get("user_id")
    ]


def _stamp_catalog_identity(
    spec: dict[str, Any],
    actor: dict[str, str] | None,
    *,
    preserved: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Keep ownership fields server-controlled while preserving other metadata."""

    catalog = dict(spec.get("catalog") or {})
    source = dict((preserved or {}).get("catalog") or {})
    for field in _CATALOG_IDENTITY_FIELDS:
        if field in source:
            catalog[field] = source[field]
    if actor:
        catalog.setdefault("creator_user_id", actor["user_id"])
        catalog.setdefault("responsible_user_id", actor["user_id"])
        catalog.setdefault("author", actor["display_name"])
        catalog.setdefault("responsible", actor["display_name"])
    if catalog:
        spec["catalog"] = catalog
    return spec


def _catalog_product_name(value: Any) -> str:
    """Normalize a display name without turning it into a filesystem path."""

    name = unicodedata.normalize("NFKC", str(value or "")).strip()
    name = " ".join(name.split())
    if not name:
        raise ValueError("Укажите название изделия")
    if len(name) > 120:
        raise ValueError("Название не длиннее 120 символов")
    if any(unicodedata.category(char).startswith("C") for char in name):
        raise ValueError("Название содержит недопустимые символы")
    return name


def _catalog_archive_reason(value: Any) -> str:
    reason = unicodedata.normalize("NFKC", str(value or "")).strip()
    reason = " ".join(reason.split())
    if len(reason) < 3:
        raise ValueError("Коротко укажите причину архивирования")
    if len(reason) > 240:
        raise ValueError("Причина не длиннее 240 символов")
    if any(unicodedata.category(char).startswith("C") for char in reason):
        raise ValueError("Причина содержит недопустимые символы")
    return reason


def _catalog_can_manage(
    auth: dict[str, Any] | None,
    product: dict[str, Any],
) -> bool:
    """Object-level catalog authorization after the project.write gate."""

    if auth is None:
        return True
    actor = _catalog_actor(auth)
    if not actor:
        return False
    if actor.get("role") == "owner":
        return True
    actor_id = str(actor.get("user_id") or "")
    return bool(
        actor_id
        and actor_id
        in {
            str(product.get("creator_user_id") or ""),
            str(product.get("responsible_user_id") or ""),
        }
    )


def _catalog_spec_permissions(spec: dict[str, Any]) -> dict[str, str]:
    catalog = spec.get("catalog") if isinstance(spec.get("catalog"), dict) else {}
    return {
        "creator_user_id": str(catalog.get("creator_user_id") or ""),
        "responsible_user_id": str(catalog.get("responsible_user_id") or ""),
    }


def _catalog_name_conflict(
    projects: list[dict[str, Any]],
    name: str,
    *,
    exclude_file: str = "",
) -> bool:
    key = unicodedata.normalize("NFKC", name).casefold()
    return any(
        str(project.get("file") or "") != exclude_file
        and unicodedata.normalize("NFKC", str(project.get("name") or "")).casefold() == key
        for project in projects
    )


def _archived_catalog_projects(
    manifests: list[dict[str, Any]],
    *,
    auth: dict[str, Any] | None,
    member_names: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    names = member_names or {}
    projects: list[dict[str, Any]] = []
    for manifest in manifests:
        dimensions = manifest.get("dimensions") if isinstance(manifest.get("dimensions"), dict) else {}
        materials = manifest.get("materials") if isinstance(manifest.get("materials"), dict) else {}
        creator_user_id = str(manifest.get("creator_user_id") or "")
        responsible_user_id = str(manifest.get("responsible_user_id") or "")
        draft = bool(manifest.get("draft"))
        archetype = str(manifest.get("archetype") or "")
        furniture_type = str(manifest.get("furniture_type") or "")
        archived_at = str(manifest.get("archived_at") or "")
        try:
            from datetime import datetime

            updated_at = int(datetime.fromisoformat(archived_at).timestamp())
        except (TypeError, ValueError):
            updated_at = 0
        item = {
            "archive_id": str(manifest.get("id") or ""),
            "file": str(manifest.get("project_file") or ""),
            "name": str(manifest.get("project_name") or "Без названия"),
            "archetype": archetype or ("черновик" if draft else "?"),
            "dims": (
                "—" if draft else
                f'{dimensions.get("width", "?")}×{dimensions.get("depth", "?")}×{dimensions.get("height", "?")}'
            ),
            "decor": str(materials.get("color") or ""),
            "draft": draft,
            "preview": bool(manifest.get("preview")),
            "preview_current": False,
            "ftype": furniture_type,
            "category": _catalog_section(
                archetype=archetype,
                furniture_type=furniture_type,
                draft=draft,
            ),
            "status": "archived",
            "archived": True,
            "archived_at": archived_at,
            "archived_by": str(manifest.get("archived_by") or ""),
            "archive_reason": str(manifest.get("reason") or ""),
            "updated_at": updated_at,
            "creator_user_id": creator_user_id,
            "responsible_user_id": responsible_user_id,
            "responsible": names.get(responsible_user_id)
            or str(manifest.get("responsible") or ""),
            "author": names.get(creator_user_id) or str(manifest.get("author") or ""),
        }
        item["can_manage"] = _catalog_can_manage(auth, item)
        projects.append(item)
    return projects


_CATALOG_IDENTITY_MIGRATED: set[Path] = set()


def _migrate_catalog_identity(spec_dir: Path, owner: dict[str, str] | None) -> None:
    """Attach legacy tenant products to the organization owner without touching mtime.

    Runs once per catalog for the lifetime of the process: listing the catalog
    must not keep rewriting product files.
    """

    if not owner or spec_dir in _CATALOG_IDENTITY_MIGRATED:
        return
    _CATALOG_IDENTITY_MIGRATED.add(spec_dir)
    for path in sorted(spec_dir.glob("*.json")):
        if path.name.endswith((".project.json", ".versions.json")):
            continue
        try:
            stat = path.stat()
            spec = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        if not isinstance(spec, dict) or spec.get("schemaVersion") != "paramspec-v1":
            continue
        catalog = dict(spec.get("catalog") or {})
        before = dict(catalog)
        catalog.setdefault("creator_user_id", owner["user_id"])
        catalog.setdefault("responsible_user_id", owner["user_id"])
        catalog.setdefault("author", owner["display_name"])
        catalog.setdefault("responsible", owner["display_name"])
        if catalog == before:
            continue
        spec["catalog"] = catalog
        _write_json_atomic(path, spec)
        _os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))


def _list_projects(
    spec_dir: Path,
    *,
    member_names: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    names = member_names or {}
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
        catalog_meta = s.get("catalog") if isinstance(s.get("catalog"), dict) else {}
        creator_user_id = str(catalog_meta.get("creator_user_id") or "")
        responsible_user_id = str(catalog_meta.get("responsible_user_id") or "")
        updated_at = int(f.stat().st_mtime)
        people = {
            "creator_user_id": creator_user_id,
            "responsible_user_id": responsible_user_id,
            "responsible": names.get(responsible_user_id)
            or str(catalog_meta.get("responsible") or ""),
            "author": names.get(creator_user_id) or str(catalog_meta.get("author") or ""),
            "updated_at": updated_at,
        }
        archetype = str(s.get("archetype") or "?")
        furniture_type = str(s.get("furniture_type") or "")
        draft = bool(s.get("draft"))
        catalog_values = {
            "category": _catalog_section(
                archetype=archetype,
                furniture_type=furniture_type,
                draft=draft,
            ),
            "status": "draft" if draft else str(catalog_meta.get("status") or "active"),
        }
        preview_path = spec_dir / ".previews" / (f.stem + ".png")
        has_prev = preview_path.is_file()
        preview_current = bool(
            has_prev and preview_path.stat().st_mtime_ns >= f.stat().st_mtime_ns
        )
        if draft:                                      # черновик (AKD-214)
            out.append({"file": f.name, "name": s.get("project_name", f.stem),
                        "archetype": "черновик", "dims": "—", "decor": "",
                        "draft": True, "preview": False, "preview_current": False,
                        "ftype": furniture_type, **catalog_values, **people})
            continue
        out.append({"file": f.name,
                    "name": s.get("project_name", f.stem),
                    "archetype": archetype,
                    "dims": f'{d.get("width", "?")}×{d.get("depth", "?")}×{d.get("height", "?")}',
                    "decor": (s.get("materials") or {}).get("color", ""),
                    "preview": has_prev, "preview_current": preview_current,
                    "ftype": furniture_type, **catalog_values, **people})
    return out


def _filter_catalog_projects(
    projects: list[dict[str, Any]],
    filters: dict[str, Any],
    *,
    current_user_id: str = "",
) -> list[dict[str, Any]]:
    scope = str(filters.get("scope") or "all")
    responsible = str(filters.get("responsible_user_id") or "all")
    category = str(filters.get("type") or "all")
    status = str(filters.get("status") or "all")
    query = str(filters.get("q") or "").strip().casefold()

    def visible(project: dict[str, Any]) -> bool:
        responsible_user_id = str(project.get("responsible_user_id") or "")
        if scope == "mine" and (
            not current_user_id or responsible_user_id != current_user_id
        ):
            return False
        if scope == "unassigned" and responsible_user_id:
            return False
        if responsible == "unassigned" and responsible_user_id:
            return False
        if responsible not in {"", "all", "unassigned"} and responsible_user_id != responsible:
            return False
        if category not in {"", "all"} and project.get("category") != category:
            return False
        if status not in {"", "all"} and project.get("status") != status:
            return False
        if query and query not in str(project.get("name") or "").casefold():
            return False
        return True

    return [project for project in projects if visible(project)]


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
    raw = str(fname or "")
    if not raw or Path(raw).name != raw or "/" in raw or "\\" in raw:
        raise FileNotFoundError("изделие не найдено")
    p = (spec_dir / raw).resolve()
    if p.parent != spec_dir.resolve() or p.suffix != ".json" or not p.is_file():
        raise FileNotFoundError("изделие не найдено")
    return p


def _reorder_like(value: Any, reference: Any) -> Any:
    """Return ``value`` with dict keys in the order ``reference`` uses.

    The strict writer emits keys in model order, which differs from the order
    people keep in hand-written ParamSpec files.  Re-ordering against the
    document already on disk keeps a save of an unchanged product a no-op in
    version control.
    """
    if isinstance(value, dict) and isinstance(reference, dict):
        ordered: dict[str, Any] = {}
        for key in reference:
            if key in value:
                ordered[key] = _reorder_like(value[key], reference[key])
        for key, item in value.items():
            if key not in ordered:
                ordered[key] = item
        return ordered
    if (
        isinstance(value, list) and isinstance(reference, list)
        and len(value) == len(reference)
    ):
        return [_reorder_like(item, ref) for item, ref in zip(value, reference)]
    return value


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _read_paramspec_document(value: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """Apply the tolerant v1 read boundary used by persisted Studio files."""

    from .paramspec_versioning import read_paramspec_v1

    envelope = read_paramspec_v1(value)
    return envelope.canonical_document(), envelope.metrics()


def _document_warnings(value: Any) -> list[str]:
    """Notes about a stored document the editor shows as-is.

    The page keeps every field the file has (nothing is silently dropped on the
    way to the browser); this tells the operator what the strict contract will
    not carry into generation.
    """

    try:
        _canonical, metrics = _read_paramspec_document(value)
    except (TypeError, ValueError) as error:
        return [f"Изделие не проходит строгую проверку ParamSpec: {str(error)[:200]}"]
    unknown = [str(item) for item in (metrics.get("unknown_fields") or [])]
    if unknown:
        shown = ", ".join(unknown[:6]) + (" …" if len(unknown) > 6 else "")
        return [f"Поля вне схемы ParamSpec, генератор их не учитывает: {shown}"]
    return []


def _spec_revision(spec: dict[str, Any]) -> str:
    """Stable revision used to reject a preview rendered for an old model."""

    payload = json.dumps(
        spec, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


# Checks whose failure makes the exported file itself wrong.  Everything else
# (completeness of hardware, unresolved material slots, drilling standards) is a
# production concern: it blocks paid cloud builds and delivery sheets, but a plain
# .cfrn export only carries it back as a warning, the way Studio always allowed.
_HARD_GATE_ISSUES = ("schema", "consistency", "geometry", "cfrn", "holes")


def _production_gate(
    spec: dict[str, Any], model_revision: str | None, *, strict: bool = True
) -> tuple[dict[str, Any] | None, list[str]]:
    """Return ``(error, warnings)`` for a production action on this browser state.

    The browser sends the revision of the last successfully rendered 3D payload.
    We still recompute all server-side checks: the revision is a freshness guard,
    not a trusted assertion that the model is production-ready.
    """

    if not isinstance(spec, dict):
        return {
            "ok": False,
            "code": "invalid_request",
            "error": "Экспорт остановлен: изделие передано в некорректном формате.",
            "object": "Текущее изделие",
            "reason": "Ожидалось описание изделия в формате ParamSpec.",
            "next_action": "Перезагрузите Studio и повторите действие.",
        }, []
    expected_revision = _spec_revision(spec)
    supplied_revision = str(model_revision or "")
    project_name = str(spec.get("project_name") or "Текущее изделие")
    if not supplied_revision or not hmac.compare_digest(
        supplied_revision, expected_revision
    ):
        return {
            "ok": False,
            "code": "stale_model",
            "error": "Экспорт остановлен: текущая редакция ещё не подтверждена пересчётом.",
            "object": project_name,
            "reason": "В рабочем поле показана другая или предыдущая редакция модели.",
            "next_action": "Дождитесь пересчёта текущей модели и повторите действие.",
        }, []

    payload = build_payload(spec)
    blocking: list[str] = []
    warnings: list[str] = []
    for category, values in (payload.get("issues") or {}).items():
        texts = [str(value) for value in (values or [])]
        if strict or category in _HARD_GATE_ISSUES:
            blocking.extend(texts)
        else:
            warnings.extend(texts)
    unresolved = [
        f"Не выбрана позиция базы: {slot}"
        for slot, value in (payload.get("refs") or {}).items()
        if isinstance(value, dict) and not value.get("resolved")
    ]
    if strict:
        blocking.extend(unresolved)
    else:
        warnings.extend(unresolved)
    if not payload.get("viewer"):
        blocking.append("3D-модель для этой редакции не построена.")
    if blocking:
        return {
            "ok": False,
            "code": "production_blocked",
            "error": "Производство и экспорт заблокированы для текущей редакции.",
            "object": project_name,
            "reason": blocking[:6],
            "next_action": (
                "Исправьте блокирующие проверки и выберите все позиции базы, "
                "затем дождитесь нового пересчёта."
            ),
        }, warnings
    return None, warnings


def _production_gate_error(
    spec: dict[str, Any], model_revision: str | None
) -> dict[str, Any] | None:
    """Strict variant kept for callers that only need the blocking error."""

    return _production_gate(spec, model_revision, strict=True)[0]


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
    def __init__(
        self,
        spec_path: Path,
        out_dir: Path,
        *,
        identity_db: Path | None = None,
        tenant_root: Path | None = None,
        require_auth: bool = False,
    ):
        from .studio_tenants import TenantWorkspaceManager

        self.spec_path = spec_path.resolve()
        self.out_dir = out_dir.resolve()
        self.spec = json.loads(self.spec_path.read_text(encoding="utf-8"))
        self.public = _os.environ.get("STUDIO_PUBLIC") == "1"
        self.require_auth = bool(require_auth)
        self.cookie_secure = (
            _os.environ.get("AKEDA_COOKIE_SECURE", "").strip().lower()
            in {"1", "true", "yes", "on"}
        )
        self.configured_origin = _os.environ.get("AKEDA_STUDIO_ORIGIN", "").rstrip("/")
        self.admin_url = _os.environ.get("AKEDA_ADMIN_URL", "/admin").strip() or "/admin"
        self.identity_store = None
        self.login_throttle = None
        if self.require_auth:
            if identity_db is None:
                raise RuntimeError("Studio auth требует путь к identity DB")
            from .admin import _LoginThrottle
            from .identity import IdentityStore

            self.identity_store = IdentityStore(identity_db)
            self.identity_store.migrate()
            self.login_throttle = _LoginThrottle()
        self.workspaces = TenantWorkspaceManager(
            self.spec_path,
            self.out_dir,
            tenant_root=tenant_root if self.require_auth else None,
        )
        from .studio_reviews import StudioReviewStore

        review_root = (
            self.workspaces.tenant_root / "_review_links"
            if self.workspaces.tenant_root is not None
            else self.out_dir / ".review_links"
        )
        self.reviews = StudioReviewStore(review_root)
        self.guard = _ChatGuard(self.out_dir)
        self._cancelled_chat_operations: dict[str, float] = {}
        self._cancelled_chat_lock = threading.RLock()
        self.started = _time.time()               # /healthz, /version (AKD-264)
        # демо-режим: изделия, существовавшие на старте, защищены от перезаписи
        self.protected: set[str] = (
            {f.name for f in spec_path.parent.glob("*.json")
             if not f.name.endswith((".project.json", ".versions.json"))}
            if self.public else set())

    def review_current_spec(self, record: dict[str, Any]) -> dict[str, Any] | None:
        """Resolve a live review only inside its server-owned tenant product."""

        if str(record.get("link_mode") or "snapshot") != "live":
            return None
        project_file = str(record.get("project_file") or "")
        if not project_file or Path(project_file).name != project_file:
            raise FileNotFoundError("review product missing")
        if self.workspaces.tenant_root is None:
            spec_dir = self.workspaces.legacy_spec_dir
        else:
            organization_id = str(record.get("organization_id") or "")
            if (
                not organization_id
                or Path(organization_id).name != organization_id
                or "/" in organization_id
                or "\\" in organization_id
            ):
                raise FileNotFoundError("review tenant missing")
            tenant_root = self.workspaces.tenant_root.resolve()
            organization_root = (tenant_root / organization_id).resolve()
            if organization_root.parent != tenant_root:
                raise FileNotFoundError("review tenant missing")
            spec_dir = organization_root / "paramspecs"
        path = _safe_spec_file(spec_dir, project_file)
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("draft"):
            raise FileNotFoundError("review product missing")
        return value

    @staticmethod
    def _valid_chat_operation_id(value: Any) -> str:
        operation_id = str(value or "")
        if not operation_id or len(operation_id) > 96:
            return ""
        if any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for ch in operation_id):
            return ""
        return operation_id

    def cancel_chat_operation(self, value: Any) -> bool:
        operation_id = self._valid_chat_operation_id(value)
        if not operation_id:
            return False
        now = _time.monotonic()
        with self._cancelled_chat_lock:
            self._cancelled_chat_operations = {
                key: ts for key, ts in self._cancelled_chat_operations.items()
                if now - ts < 600
            }
            self._cancelled_chat_operations[operation_id] = now
        return True

    def consume_chat_cancellation(self, value: Any) -> bool:
        operation_id = self._valid_chat_operation_id(value)
        if not operation_id:
            return False
        with self._cancelled_chat_lock:
            return self._cancelled_chat_operations.pop(operation_id, None) is not None

    def is_chat_operation_cancelled(self, value: Any) -> bool:
        operation_id = self._valid_chat_operation_id(value)
        if not operation_id:
            return False
        with self._cancelled_chat_lock:
            return operation_id in self._cancelled_chat_operations


def _studio_login_page() -> str:
    """Small same-origin login page for the authenticated Studio process."""

    return r"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Вход · Akeda Studio</title><style>
:root{font-family:Inter,Segoe UI,Arial,sans-serif;color:#171a20;background:#eef1f5}
*{box-sizing:border-box}body{min-height:100vh;margin:0;display:grid;place-items:center;
  background:radial-gradient(circle at 20% 0,#fff 0,#f4f6f9 38%,#e9edf3 100%)}
.card{width:min(430px,calc(100vw - 32px));padding:34px;border:1px solid #dfe4ea;
  border-radius:22px;background:rgba(255,255,255,.94);box-shadow:0 24px 70px rgba(30,39,54,.12)}
.brand{display:flex;align-items:center;justify-content:center;gap:10px;margin:2px auto 31px}
.brand-wordmark{width:70%;max-width:240px;height:auto}.brand-mark{width:24%;max-width:84px;height:auto}
.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;
  clip:rect(0,0,0,0);white-space:nowrap;border:0}.field{display:grid;gap:7px;margin:14px 0}
label{color:#454e5c;font-size:12px;font-weight:650}input{width:100%;height:46px;border:1px solid #ccd3dd;
  border-radius:12px;padding:0 13px;font:inherit;background:#fff;outline:none}input:focus{border-color:#3478ea;
  box-shadow:0 0 0 3px rgba(52,120,234,.12)}.password-field{position:relative}
.password-field input{padding-right:52px}.password-toggle{position:absolute;top:1px;right:1px;
  display:flex;width:44px;height:44px;align-items:center;justify-content:center;padding:0;
  border-radius:11px;background:transparent;color:#687180}.password-toggle:hover{background:#f1f4f8;color:#303844}
.password-toggle:focus-visible{outline:2px solid #3478ea;outline-offset:1px}
.password-toggle svg{width:19px;height:19px;fill:none;stroke:currentColor;stroke-width:1.7;
  stroke-linecap:round;stroke-linejoin:round}.password-toggle .eye-off{display:none}
.password-toggle.is-visible .eye-on{display:none}.password-toggle.is-visible .eye-off{display:block}
button{width:100%;height:46px;border:0;border-radius:12px;
  background:#2466d8;color:white;font:inherit;font-weight:750;cursor:pointer}button:disabled{opacity:.55;cursor:wait}
#error{min-height:20px;margin:12px 0 0;color:#c73d43;font-size:12px;line-height:1.4}
#companies{display:none;margin:14px 0;padding:12px;border-radius:12px;background:#f4f7fb}
#companies.on{display:grid;gap:8px}#companies button{height:auto;min-height:40px;padding:9px 12px;
  background:white;color:#283140;border:1px solid #d8dee7;text-align:left}
.foot{margin:25px auto 0;max-width:310px;color:#9098a4;font-size:10.5px;line-height:15px;text-align:center}
@media(max-width:460px){.card{padding:28px 24px}.brand{margin-bottom:27px}}
</style></head><body><main class="card">
<h1 id="loginTitle" class="sr-only">Вход в Akeda Studio</h1>
<div class="brand" aria-hidden="true">
  <img class="brand-wordmark" src="/assets/studio/akeda-studio-wordmark.png" width="520" height="84" alt="">
  <img class="brand-mark" src="/assets/studio/akeda-studio-mark.png" width="216" height="98" alt="">
</div>
<form id="form" aria-labelledby="loginTitle">
<div class="field"><label for="email">Почта</label><input id="email" name="email" type="email" autocomplete="username" required></div>
<div class="field"><label for="password">Пароль</label><div class="password-field">
  <input id="password" name="password" type="password" autocomplete="current-password" required>
  <button id="passwordToggle" class="password-toggle" type="button" aria-label="Показать пароль"
    aria-pressed="false" title="Показать пароль">
    <svg class="eye-on" viewBox="0 0 24 24" aria-hidden="true"><path d="M2.5 12s3.5-6 9.5-6 9.5 6 9.5 6-3.5 6-9.5 6-9.5-6-9.5-6Z"/><circle cx="12" cy="12" r="2.5"/></svg>
    <svg class="eye-off" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 4 20 20M9.2 6.5A10.5 10.5 0 0 1 12 6c6 0 9.5 6 9.5 6a15.8 15.8 0 0 1-2.3 3M14.6 17.6A10 10 0 0 1 12 18c-6 0-9.5-6-9.5-6a16 16 0 0 1 3-3.7M10.2 10.2a2.5 2.5 0 0 0 3.6 3.6"/></svg>
  </button>
</div></div>
<div id="companies" aria-label="Выбор компании"></div><button id="submit" type="submit">Войти</button>
<p id="error" role="alert"></p></form>
<div class="foot">Используйте логин сотрудника, выданный командой Akeda.</div>
</main><script>
let organizationId=null;const form=document.getElementById('form'),error=document.getElementById('error'),
  password=document.getElementById('password'),passwordToggle=document.getElementById('passwordToggle');
passwordToggle.addEventListener('click',()=>{
  const visible=password.type==='text';password.type=visible?'password':'text';
  passwordToggle.classList.toggle('is-visible',!visible);
  passwordToggle.setAttribute('aria-pressed',String(!visible));
  const label=visible?'Показать пароль':'Скрыть пароль';
  passwordToggle.setAttribute('aria-label',label);passwordToggle.title=label;
  password.focus({preventScroll:true});
});
async function login(){const button=document.getElementById('submit');button.disabled=true;error.textContent='';
  try{const response=await fetch('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({email:document.getElementById('email').value,password:document.getElementById('password').value,
      organization_id:organizationId})});const data=await response.json();
    if(data.code==='organization_selection_required'){
      const organizations=((data.details||{}).organizations||[]),box=document.getElementById('companies');
      box.innerHTML='<b>Выберите компанию</b>'+organizations.map(item=>
        `<button type="button" data-id="${item.id}">${item.name}<small> · ${item.role}</small></button>`).join('');
      box.classList.add('on');box.querySelectorAll('button').forEach(item=>item.onclick=()=>{organizationId=item.dataset.id;login();});
      return;
    }
    if(!response.ok||!data.ok)throw new Error(data.error||'Не удалось войти');
    location.href=data.redirect||'/index.html';
  }catch(reason){error.textContent=reason.message||'Не удалось войти';}
  finally{button.disabled=false;}}
form.addEventListener('submit',event=>{event.preventDefault();organizationId=null;login();});
</script></body></html>"""


def make_handler(st: _Studio):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):                       # тихий сервер
            pass

        def _send(
            self,
            code: int,
            body: bytes,
            ctype: str = "application/json; charset=utf-8",
            *,
            headers: list[tuple[str, str]] | None = None,
        ):
            try:
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("X-Frame-Options", "DENY")
                self.send_header("Referrer-Policy", "same-origin")
                if st.require_auth:
                    self.send_header("Cache-Control", "no-store, max-age=0")
                if st.cookie_secure:
                    self.send_header("Strict-Transport-Security", "max-age=31536000")
                for name, value in headers or []:
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                # Штатная отмена fetch в Studio: клиент больше не ждёт ответ.
                return

        def _json(
            self,
            obj: Any,
            code: int = 200,
            *,
            headers: list[tuple[str, str]] | None = None,
        ):
            from .telemetry import add_current_attributes, current_trace_id

            trace_id = current_trace_id()
            add_current_attributes({
                "http.status_code": code,
                "check.outcome": "pass" if code < 400 else "error",
                "error.codes": [obj.get("code") or "http_error"]
                if code >= 400 and isinstance(obj, dict) else [],
            })
            if trace_id and isinstance(obj, dict) and "trace_id" not in obj:
                obj = {**obj, "trace_id": trace_id}
            self._send(
                code,
                json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                headers=headers,
            )

        def _send_review_attachment(self, metadata: dict[str, Any], path: Path) -> None:
            content_type = str(metadata.get("content_type") or "application/octet-stream")
            disposition = "inline" if content_type.startswith("image/") else "attachment"
            filename = quote(str(metadata.get("name") or "attachment"), safe="")
            self._send(
                200,
                path.read_bytes(),
                content_type,
                headers=[
                    ("Content-Disposition", f"{disposition}; filename*=UTF-8''{filename}"),
                    ("Cache-Control", "private, no-store"),
                    ("Referrer-Policy", "no-referrer"),
                ],
            )

        def _redirect(self, location: str, *, headers: list[tuple[str, str]] | None = None):
            self._send(303, b"", headers=[("Location", location), *(headers or [])])

        def _cookies(self) -> dict[str, str]:
            parsed = SimpleCookie()
            try:
                parsed.load(self.headers.get("Cookie", ""))
            except Exception:
                return {}
            return {name: morsel.value for name, morsel in parsed.items()}

        def _cookie(
            self,
            name: str,
            value: str,
            *,
            http_only: bool,
            clear: bool = False,
            max_age: int | None = None,
        ) -> str:
            cookie = SimpleCookie()
            cookie[name] = value
            morsel = cookie[name]
            morsel["path"] = "/"
            morsel["samesite"] = "Lax"
            if http_only:
                morsel["httponly"] = True
            if st.cookie_secure:
                morsel["secure"] = True
            if clear:
                morsel["max-age"] = 0
                morsel["expires"] = formatdate(0, usegmt=True)
            elif max_age is not None:
                morsel["max-age"] = max(1, int(max_age))
            return morsel.OutputString()

        def _clear_auth_headers(self) -> list[tuple[str, str]]:
            from .admin import CSRF_COOKIE, SESSION_COOKIE, SUPPORT_COOKIE

            return [
                ("Set-Cookie", self._cookie(SESSION_COOKIE, "", http_only=True, clear=True)),
                ("Set-Cookie", self._cookie(CSRF_COOKIE, "", http_only=False, clear=True)),
                ("Set-Cookie", self._cookie(SUPPORT_COOKIE, "", http_only=True, clear=True)),
            ]

        def _login_headers(self, result: dict[str, Any]) -> list[tuple[str, str]]:
            from .admin import CSRF_COOKIE, SESSION_COOKIE, SUPPORT_COOKIE

            session_token = str(result.get("session_token") or "")
            csrf_token = str(result.get("csrf_token") or "")
            if not session_token or not csrf_token:
                raise RuntimeError("IdentityStore не вернул секреты сессии")
            expires_at = result.get("expires_at")
            max_age = max(1, int(expires_at) - int(_time.time())) if expires_at else None
            return [
                ("Set-Cookie", self._cookie(SESSION_COOKIE, session_token, http_only=True, max_age=max_age)),
                ("Set-Cookie", self._cookie(CSRF_COOKIE, csrf_token, http_only=False, max_age=max_age)),
                ("Set-Cookie", self._cookie(SUPPORT_COOKIE, "", http_only=True, clear=True)),
            ]

        def _request_origin(self) -> str:
            if st.configured_origin:
                return st.configured_origin
            host = self.headers.get("Host", "")
            allowed = {
                f"127.0.0.1:{self.server.server_address[1]}",
                f"localhost:{self.server.server_address[1]}",
            }
            if host not in allowed:
                raise PermissionError("Недопустимый адрес запроса")
            return f"{'https' if st.cookie_secure else 'http'}://{host}"

        def _require_same_origin(self) -> bool:
            if not st.require_auth:
                return True
            origin = self.headers.get("Origin", "")
            try:
                expected = self._request_origin()
            except PermissionError:
                self._json({"ok": False, "error": "Недопустимый адрес запроса"}, 400)
                return False
            if not origin or origin == "null" or not hmac.compare_digest(origin, expected):
                self._json({"ok": False, "error": "Запрос отклонён: неверный источник"}, 403)
                return False
            return True

        def _load_auth(self) -> dict[str, Any] | None:
            if not st.require_auth or st.identity_store is None:
                return None
            from .admin import SESSION_COOKIE, SUPPORT_COOKIE

            cookies = self._cookies()
            token = cookies.get(SESSION_COOKIE, "")
            support_id = cookies.get(SUPPORT_COOKIE) or None
            if not token:
                return None
            context = st.identity_store.session(token, support_session_id=support_id)
            if context is None and support_id:
                context = st.identity_store.session(token, support_session_id=None)
                support_id = None
            if context is None:
                return None
            organization = context.get("organization") or {}
            support = context.get("support_session") or {}
            if not organization.get("id") and support.get("organization_id"):
                snapshot = st.identity_store.snapshot(
                    actor_user_id=(context.get("user") or {}).get("id"),
                    organization_id=support.get("organization_id"),
                    support_session_id=support.get("id"),
                    primary_session_id=(context.get("session") or {}).get("id"),
                )
                context = dict(context)
                context.update(
                    {
                        "organization": snapshot.get("organization"),
                        "membership": snapshot.get("membership"),
                        "permissions": snapshot.get("permissions") or [],
                        "support_session": snapshot.get("support_session") or support,
                    }
                )
            return {"token": token, "support_session_id": support_id, "context": context}

        def _require_access(
            self,
            permission: str | None = None,
            *,
            csrf: bool = False,
            page: bool = False,
        ) -> dict[str, Any] | None:
            if not st.require_auth:
                return None
            from .admin import CSRF_COOKIE

            auth = self._load_auth()
            if auth is None:
                if page:
                    self._redirect("/login", headers=self._clear_auth_headers())
                else:
                    self._json(
                        {"ok": False, "error": "Требуется вход", "code": "unauthenticated"},
                        401,
                        headers=self._clear_auth_headers(),
                    )
                return None
            context = auth["context"]
            organization = context.get("organization") or {}
            if not organization.get("id"):
                if page:
                    self._redirect(st.admin_url)
                else:
                    self._json(
                        {"ok": False, "error": "Выберите компанию", "code": "organization_required"},
                        403,
                    )
                return None
            if permission and permission not in set(context.get("permissions") or []):
                self._json(
                    {"ok": False, "error": "Недостаточно прав", "code": "permission_denied"},
                    403,
                )
                return None
            if csrf:
                header = self.headers.get("X-CSRF-Token", "")
                cookie = self._cookies().get(CSRF_COOKIE, "")
                valid = bool(header and cookie and hmac.compare_digest(header, cookie))
                if valid and st.identity_store is not None:
                    valid = st.identity_store.verify_csrf(auth["token"], header)
                if not valid:
                    self._json(
                        {"ok": False, "error": "Проверка CSRF не пройдена", "code": "csrf_failed"},
                        403,
                    )
                    return None
            return auth

        @staticmethod
        def _public_auth(auth: dict[str, Any] | None) -> dict[str, Any] | None:
            if not auth:
                return None
            context = auth.get("context") or {}
            return {
                "user": context.get("user"),
                "organization": context.get("organization"),
                "membership": context.get("membership"),
                "permissions": context.get("permissions") or [],
                "support_session": context.get("support_session"),
                "viewing_as_akeda": bool(context.get("support_session")),
            }

        def _audit_product_action(
            self,
            auth: dict[str, Any] | None,
            action: str,
            spec_path: Path,
            metadata: dict[str, Any] | None = None,
        ) -> None:
            if auth is None or st.identity_store is None:
                return
            context = auth.get("context") or {}
            user = context.get("user") or {}
            organization = context.get("organization") or {}
            support = context.get("support_session") or {}
            st.identity_store.append_audit(
                action,
                actor_user_id=user.get("id"),
                organization_id=organization.get("id"),
                support_session_id=support.get("id"),
                target_type="studio_project",
                target_id=spec_path.name,
                metadata=metadata or {},
            )

        def _body(self) -> dict[str, Any]:
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n).decode("utf-8")) if n else {}

        def do_GET(self):
            path = urlsplit(self.path).path
            parts = path.strip("/").split("/")
            if len(parts) == 2 and parts[0] == "archive-preview":
                auth = self._require_access("project.read")
                if st.require_auth and auth is None:
                    return
                try:
                    preview = st.workspaces.archived_preview_path(auth, parts[1])
                    self._send(
                        200,
                        preview.read_bytes(),
                        "image/png",
                        headers=[("Cache-Control", "private, no-store")],
                    )
                except FileNotFoundError:
                    self._send(404, b"{}")
                return
            if len(parts) == 4 and parts[0] == "review" and parts[2] == "attachments":
                try:
                    metadata, attachment_path = st.reviews.attachment_for_token(
                        parts[1], parts[3]
                    )
                    self._send_review_attachment(metadata, attachment_path)
                except FileNotFoundError:
                    self._send(404, b"{}")
                return
            if (
                len(parts) == 5
                and parts[:3] == ["api", "reviews", "attachments"]
            ):
                auth = self._require_access("project.read")
                if st.require_auth and auth is None:
                    return
                context = (auth or {}).get("context") or {}
                organization = context.get("organization") or {}
                spec_path = st.workspaces.current_spec_path(auth)
                try:
                    metadata, attachment_path = st.reviews.attachment_for_project(
                        review_id=parts[3],
                        attachment_id=parts[4],
                        organization_id=str(organization.get("id") or "") or None,
                        project_file=spec_path.name,
                    )
                    self._send_review_attachment(metadata, attachment_path)
                except FileNotFoundError:
                    self._send(404, b"{}")
                return
            if path.startswith("/review/"):
                token = path[len("/review/"):].strip("/")
                if not token or "/" in token:
                    self._send(404, b"Review link not found")
                    return
                try:
                    record = st.reviews.load(token)
                    record = st.reviews.resolve_record(
                        record,
                        current_spec=st.review_current_spec(record),
                    )
                    spec = record.get("spec") or {}
                    if not isinstance(spec, dict):
                        raise FileNotFoundError("review snapshot missing")
                    from .delivery import spec_summary
                    from .generators import generate_from_paramspec
                    from .studio_review_page import build_review_page
                    from .webviewer import SCENE_JS, viewer_payload

                    project = generate_from_paramspec(spec)
                    summary = spec_summary(project)
                    dimensions = summary["dims"]
                    page = build_review_page(
                        record,
                        viewer=viewer_payload(project),
                        stats={
                            "n_panels": summary["n_panels"],
                            "n_holes": summary["n_holes"],
                            "dims": (
                                f'{dimensions.get("w", "?")}×'
                                f'{dimensions.get("d", "?")}×'
                                f'{dimensions.get("h", "?")} мм'
                            ),
                            "decor": summary["decor"],
                        },
                        scene_js=SCENE_JS,
                    )
                    self._send(
                        200,
                        page.encode("utf-8"),
                        "text/html; charset=utf-8",
                        headers=[
                            ("X-Robots-Tag", "noindex, nofollow, noarchive"),
                            ("Referrer-Policy", "no-referrer"),
                            ("Cache-Control", "private, no-store"),
                            ("X-Frame-Options", "DENY"),
                        ],
                    )
                except Exception:
                    page = ("<!doctype html><html lang='ru'><meta charset='utf-8'>"
                            "<meta name='viewport' content='width=device-width'>"
                            "<title>Ссылка недоступна</title><body style='font:14px system-ui;"
                            "margin:48px;color:#303743'><h1 style='font-size:20px'>"
                            "Ссылка недоступна</h1><p>Проверьте адрес или запросите новую "
                            "ссылку у проектировщика.</p></body></html>")
                    self._send(404, page.encode("utf-8"), "text/html; charset=utf-8")
            elif path == "/login":
                auth = self._load_auth()
                if auth and (auth["context"].get("organization") or {}).get("id"):
                    self._redirect("/index.html")
                    return
                self._send(
                    200,
                    _studio_login_page().encode("utf-8"),
                    "text/html; charset=utf-8",
                )
            elif path == "/api/auth/me":
                if not st.require_auth:
                    self._json({"authenticated": False})
                    return
                auth = self._require_access("organization.read")
                if auth is None:
                    return
                payload = self._public_auth(auth) or {}
                payload["authenticated"] = True
                self._json(payload)
            elif path in ("/", "/index.html"):
                auth = self._require_access("project.read", page=True)
                if st.require_auth and auth is None:
                    return
                spec_path = st.workspaces.current_spec_path(auth)
                spec = json.loads(spec_path.read_text(encoding="utf-8"))
                spec_warnings = _document_warnings(spec)
                from .webviewer import SCENE_JS
                page = (PAGE
                        .replace("__SCENE_JS__", SCENE_JS)
                        .replace("__FIELDS__", json.dumps(ARCHETYPE_FIELDS, ensure_ascii=False))
                        .replace("__SECTION_ARCHS__", json.dumps(SECTION_ARCHETYPES))
                        .replace("__AUTH__", json.dumps(self._public_auth(auth), ensure_ascii=False)
                                 .replace("</", "<\\/"))
                        .replace("__ADMIN_URL__", json.dumps(st.admin_url, ensure_ascii=False)
                                 .replace("</", "<\\/"))
                        .replace("__PROJECT_FILE__", json.dumps(spec_path.name, ensure_ascii=False))
                        .replace("__SPEC_WARNINGS__", json.dumps(spec_warnings, ensure_ascii=False)
                                 .replace("</", "<\\/"))
                        .replace("__SPEC__", json.dumps(spec, ensure_ascii=False)
                                 .replace("</", "<\\/")))
                self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")
            elif path.startswith("/vendor/"):    # three.js локально, без CDN (AKD-261)
                vd = (Path(__file__).resolve().parent.parent / "vendor")
                p = (vd / Path(path[len("/vendor/"):]).name).resolve()
                if p.parent == vd.resolve() and p.suffix == ".js" and p.is_file():
                    self._send(200, p.read_bytes(),
                               "application/javascript; charset=utf-8")
                else:
                    self._send(404, b"{}")
            elif path in {                       # exact user-supplied brand crops
                "/assets/studio/akeda-studio-wordmark.png",
                "/assets/studio/akeda-studio-mark.png",
            }:
                assets = Path(__file__).resolve().parent.parent / "assets" / "studio"
                p = assets / path.rsplit("/", 1)[-1]
                if p.is_file():
                    self._send(200, p.read_bytes(), "image/png")
                else:
                    self._send(404, b"{}")
            elif path == "/healthz":             # мониторинг (AKD-264)
                self._json({"ok": True, "uptime_s": int(_time.time() - st.started)})
            elif path == "/version":             # какой код развёрнут (AKD-264)
                sha = ""
                try:
                    sha = (Path(__file__).resolve().parent.parent / "DEPLOY_SHA") \
                        .read_text(encoding="utf-8").strip()
                except OSError:
                    pass
                self._json({"sha": sha or "dev", "public": st.public,
                            "started": int(st.started)})
            elif path.startswith("/thumb/"):     # аксонометрия карточки (AKD-217)
                auth = self._require_access("project.read")
                if st.require_auth and auth is None:
                    return
                from urllib.parse import unquote
                try:
                    workspace = st.workspaces.workspace(auth)
                    data = _thumb_svg_cached(workspace.spec_dir,
                                             unquote(path[len("/thumb/"):]))
                except Exception:
                    data = None
                if data:
                    self._send(200, data, "image/svg+xml; charset=utf-8")
                else:
                    self._send(404, b"{}")
            elif path.startswith("/preview/"):   # миниатюры каталога (AKD-217)
                auth = self._require_access("project.read")
                if st.require_auth and auth is None:
                    return
                from urllib.parse import unquote
                workspace = st.workspaces.workspace(auth)
                name = unquote(path[len("/preview/"):])
                p = (workspace.spec_dir / ".previews" / name).resolve()
                if (p.parent == (workspace.spec_dir / ".previews").resolve()
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
                path = urlsplit(self.path).path
                if not self._require_same_origin():
                    return
                review_parts = path.strip("/").split("/")
                if (
                    len(review_parts) == 3
                    and review_parts[0] == "review"
                    and review_parts[2] == "attachments"
                ):
                    from .studio_reviews import (
                        MAX_ATTACHMENT_BYTES,
                        ReviewAttachmentError,
                        ReviewRevisionConflict,
                    )

                    try:
                        content_length = int(self.headers.get("Content-Length") or 0)
                    except ValueError:
                        content_length = 0
                    if content_length > MAX_ATTACHMENT_BYTES:
                        self._json({
                            "ok": False,
                            "error": "Один файл должен быть не больше 10 МБ",
                        }, 413)
                        return
                    try:
                        review_record = st.reviews.load(review_parts[1])
                        attachment = st.reviews.stage_attachment(
                            review_parts[1],
                            filename=unquote(
                                str(self.headers.get("X-Akeda-Filename") or "")[:1000]
                            ),
                            content_length=content_length,
                            source=self.rfile,
                            expected_revision=str(
                                self.headers.get("X-Akeda-Revision") or ""
                            ),
                            current_spec=st.review_current_spec(review_record),
                        )
                    except FileNotFoundError:
                        self._json({"ok": False, "error": "Ссылка недоступна"}, 404)
                        return
                    except ReviewRevisionConflict as error:
                        self._json({
                            "ok": False,
                            "error": str(error),
                            "code": "review_revision_changed",
                        }, 409)
                        return
                    except ReviewAttachmentError as error:
                        self._json({"ok": False, "error": str(error)}, 400)
                        return
                    self._json({"ok": True, "attachment": attachment})
                    return
                if len(review_parts) == 3 and review_parts[0] == "review" and review_parts[2] == "decision":
                    try:
                        decision_length = int(self.headers.get("Content-Length") or 0)
                    except ValueError:
                        self._json({"ok": False, "error": "Некорректный размер ответа"}, 400)
                        return
                    if decision_length > 64 * 1024:
                        self._json({"ok": False, "error": "Ответ слишком большой"}, 413)
                        return
                if path == "/api/catalog-preview":
                    try:
                        preview_length = int(self.headers.get("Content-Length") or 0)
                    except ValueError:
                        preview_length = 0
                    if preview_length > 3 * 1024 * 1024:
                        self._json({"ok": False, "error": "Превью слишком большое"}, 413)
                        return
                body = self._body()
                if (
                    len(review_parts) == 3
                    and review_parts[0] == "review"
                    and review_parts[2] == "decision"
                ):
                    from .studio_reviews import ReviewRevisionConflict

                    attachment_ids = body.get("attachment_ids") or []
                    if not isinstance(attachment_ids, list):
                        self._json({"ok": False, "error": "Некорректный список вложений"}, 400)
                        return
                    try:
                        review_record = st.reviews.load(review_parts[1])
                        record = st.reviews.decide(
                            review_parts[1],
                            decision=str(body.get("decision") or ""),
                            reviewer_name=str(body.get("reviewer_name") or ""),
                            comment=str(body.get("comment") or ""),
                            attachment_ids=[str(value or "") for value in attachment_ids],
                            expected_revision=str(body.get("revision") or ""),
                            current_spec=st.review_current_spec(review_record),
                        )
                    except FileNotFoundError:
                        self._json({"ok": False, "error": "Ссылка недоступна"}, 404)
                        return
                    except ReviewRevisionConflict as error:
                        self._json({
                            "ok": False,
                            "error": str(error),
                            "code": "review_revision_changed",
                        }, 409)
                        return
                    except ValueError as error:
                        self._json({"ok": False, "error": str(error)}, 400)
                        return
                    if st.identity_store is not None:
                        st.identity_store.append_audit(
                            "studio.review.decision_received",
                            actor_user_id=None,
                            organization_id=str(record.get("organization_id") or "") or None,
                            target_type="studio_review",
                            target_id=str(record.get("id") or ""),
                            metadata={
                                "revision": str(record.get("revision") or ""),
                                "status": str(record.get("status") or ""),
                                "attachments_count": len(
                                    list((record.get("decision") or {}).get("attachments") or [])
                                ),
                            },
                        )
                    from .telemetry import add_current_attributes
                    add_current_attributes({
                        "approval.outcome": str(record.get("status") or ""),
                        "revision.hash": str(record.get("revision") or ""),
                    })
                    self._json({
                        "ok": True,
                        "status": record["status"],
                        "decision": record["decision"],
                        "revision": record["revision"],
                    })
                    return
                if path == "/api/auth/login":
                    if not st.require_auth or st.identity_store is None or st.login_throttle is None:
                        self._send(404, b"{}")
                        return
                    from .identity import IdentityError

                    email = str(body.get("email") or "").strip().casefold()[:320]
                    password = str(body.get("password") or "")[:4096]
                    ip = self.client_address[0]
                    retry = st.login_throttle.retry_after(ip, email)
                    if retry:
                        self._json(
                            {
                                "ok": False,
                                "error": "Вход временно недоступен. Повторите позже.",
                                "code": "rate_limited",
                            },
                            429,
                            headers=[("Retry-After", str(retry))],
                        )
                        return
                    try:
                        result = st.identity_store.login(
                            email,
                            password,
                            ip=ip,
                            user_agent=self.headers.get("User-Agent"),
                            organization_id=body.get("organization_id"),
                        )
                    except IdentityError as error:
                        if getattr(error, "code", "") == "organization_selection_required":
                            self._json(
                                {
                                    "ok": False,
                                    "error": "Выберите компанию для входа.",
                                    "code": "organization_selection_required",
                                    "details": getattr(error, "details", {}),
                                },
                                409,
                            )
                            return
                        st.login_throttle.failure(ip, email)
                        self._json(
                            {
                                "ok": False,
                                "error": "Неверная почта или пароль.",
                                "code": "invalid_credentials",
                            },
                            401,
                        )
                        return
                    st.login_throttle.success(ip, email)
                    redirect = (
                        "/index.html"
                        if (result.get("organization") or {}).get("id")
                        else st.admin_url
                    )
                    payload = dict(result)
                    payload.pop("session_token", None)
                    payload.pop("csrf_token", None)
                    payload.update({"ok": True, "redirect": redirect})
                    self._json(payload, headers=self._login_headers(result))
                    return

                if path == "/api/auth/logout":
                    auth = self._require_access("organization.read", csrf=True)
                    if auth is None:
                        return
                    if st.identity_store is not None:
                        st.identity_store.logout(auth["token"])
                    st.workspaces.clear_session(auth)
                    self._json({"ok": True}, headers=self._clear_auth_headers())
                    return

                permissions = {
                    "/api/chat": "ai.run",
                    "/api/chat/cancel": "ai.run",
                    "/api/import-tz": "ai.run",
                    "/api/new": "project.create",
                    "/api/duplicate": "project.create",
                    "/api/rename": "project.write",
                    "/api/catalog/assign": "project.write",
                    "/api/catalog/archive": "project.write",
                    "/api/catalog/restore": "project.write",
                    "/api/catalog-preview": "project.write",
                    "/api/catalog-preview-source": "project.read",
                    "/api/reviews/create": "project.write",
                    "/api/reviews/revoke": "project.write",
                    "/api/save": "project.write",
                    "/api/restore": "project.write",
                    "/api/export-cfrn": "production.export",
                    "/api/deliver": "production.export",
                    "/api/open-file": "production.export",
                    "/api/build-b3d": "production.build",
                }
                auth = self._require_access(permissions.get(path, "project.read"), csrf=True)
                if st.require_auth and auth is None:
                    return
                if path == "/api/import-tz" and auth is not None:
                    if "project.create" not in set(auth["context"].get("permissions") or []):
                        self._json(
                            {"ok": False, "error": "Недостаточно прав", "code": "permission_denied"},
                            403,
                        )
                        return
                workspace = st.workspaces.workspace(auth)
                # The browser owns the visible product identity. The previous
                # in-memory-only selection was lost on every service restart,
                # so an already open tab could edit the first catalog item
                # while displaying another one. Bind stateful routes to the
                # explicit, validated file sent by the editor.
                project_bound_routes = {
                    "/api/chat", "/api/chat-history", "/api/save",
                    "/api/versions", "/api/restore", "/api/import-tz",
                    "/api/export-cfrn", "/api/build-b3d", "/api/deliver",
                    "/api/duplicate",
                }
                # Routes where acting on a stale selection corrupts data or
                # writes files under another product's name.
                identity_required_routes = {
                    "/api/chat", "/api/chat-history", "/api/save", "/api/restore",
                    "/api/export-cfrn", "/api/build-b3d", "/api/deliver",
                }
                requested_project = str(body.get("project_file") or "").strip()
                if path in project_bound_routes and requested_project:
                    try:
                        st.workspaces.select(auth, requested_project)
                    except FileNotFoundError:
                        self._json({
                            "ok": False,
                            "error": "Выбранное изделие больше не найдено. Обновите каталог.",
                            "code": "project_not_found",
                        }, 404)
                        return
                elif path in identity_required_routes and len(_list_projects(workspace.spec_dir)) > 1:
                    self._json({
                        "ok": False,
                        "error": "Не удалось определить открытое изделие. Обновите страницу.",
                        "code": "project_identity_required",
                    }, 409)
                    return
                spec_path = st.workspaces.current_spec_path(auth)
                current_spec = json.loads(spec_path.read_text(encoding="utf-8"))
                spec = body.get("spec") or {}
                from .telemetry import add_current_attributes, current_trace_id, hash_payload
                add_current_attributes({
                    "project.hash": hash_payload({
                        "project_file": spec_path.name,
                        "organization": workspace.organization_id,
                    }),
                    "revision.hash": hash_payload(spec),
                    "image.count": len(body.get("images") or []),
                })
                production_warnings: list[str] = []
                if path in ("/api/export-cfrn", "/api/build-b3d", "/api/deliver"):
                    production_error, production_warnings = _production_gate(
                        spec, body.get("model_revision"),
                        strict=path != "/api/export-cfrn",
                    )
                    if production_error is not None:
                        self._json(production_error, 409)
                        return
                if path in ("/api/chat", "/api/import-tz"):
                    gate = self._chat_gate()
                    if gate:
                        self._json({"ok": False, "error": gate,
                                    "reply": "⛔ " + gate}, 429)
                        return
                if path == "/api/generate":
                    self._json(build_payload(spec))
                elif path == "/api/catalog-preview-source":
                    try:
                        preview_spec_path = _safe_spec_file(
                            workspace.spec_dir, str(body.get("file") or "")
                        )
                        preview_spec = json.loads(
                            preview_spec_path.read_text(encoding="utf-8")
                        )
                        if not isinstance(preview_spec, dict) or preview_spec.get("draft"):
                            raise ValueError("Для черновика превью не строится")
                        payload = build_payload(preview_spec)
                        viewer = payload.get("viewer") if isinstance(payload, dict) else None
                        if not isinstance(viewer, dict):
                            raise ValueError("3D-модель для превью не построилась")
                        self._json({
                            "ok": True,
                            "file": preview_spec_path.name,
                            "revision": _spec_revision(preview_spec),
                            "viewer": viewer,
                        })
                    except (FileNotFoundError, ValueError, TypeError, json.JSONDecodeError) as error:
                        self._json({"ok": False, "error": str(error)}, 400)
                elif path == "/api/catalog-preview":
                    try:
                        preview_spec_path = _safe_spec_file(
                            workspace.spec_dir, str(body.get("file") or "")
                        )
                        preview_spec = json.loads(
                            preview_spec_path.read_text(encoding="utf-8")
                        )
                        revision = str(body.get("revision") or "")
                        if not hmac.compare_digest(revision, _spec_revision(preview_spec)):
                            self._json({
                                "ok": False,
                                "error": "Изделие изменилось — превью будет построено заново",
                                "code": "stale_preview",
                            }, 409)
                            return
                        preview = str(body.get("preview") or "")
                        prefix = "data:image/png;base64,"
                        if not preview.startswith(prefix):
                            raise ValueError("Ожидалось PNG-превью")
                        raw = base64.b64decode(preview[len(prefix):], validate=True)
                        if len(raw) > 2 * 1024 * 1024 or not raw.startswith(b"\x89PNG\r\n\x1a\n"):
                            raise ValueError("Некорректное PNG-превью")
                        preview_dir = workspace.spec_dir / ".previews"
                        preview_dir.mkdir(exist_ok=True)
                        target = preview_dir / (preview_spec_path.stem + ".png")
                        temporary = preview_dir / (
                            f".{preview_spec_path.stem}.{secrets.token_hex(6)}.tmp"
                        )
                        temporary.write_bytes(raw)
                        temporary.replace(target)
                        self._json({
                            "ok": True,
                            "file": preview_spec_path.name,
                            "url": f"/preview/{quote(target.name)}?v={target.stat().st_mtime_ns}",
                        })
                    except (FileNotFoundError, ValueError, TypeError, json.JSONDecodeError) as error:
                        self._json({"ok": False, "error": str(error)}, 400)
                elif path == "/api/techview":
                    self._json(techview_svg(spec, body.get("panel")))
                elif path == "/api/chat/cancel":
                    if not st.cancel_chat_operation(body.get("operation_id")):
                        self._json({"ok": False, "error": "Некорректная команда"}, 400)
                        return
                    self._json({"ok": True})
                elif path == "/api/chat-history":
                    operations = st.workspaces.read_ai_history(auth, spec_path)
                    self._json({
                        "operations": operations,
                        "history": st.workspaces.ai_messages(auth, spec_path),
                    })
                elif path == "/api/chat":
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
                    message = str(body.get("message", ""))
                    provider = body.get("provider") or None
                    history = (st.workspaces.ai_messages(auth, spec_path)
                               if auth is not None else body.get("history") or [])
                    from .spec_chat import chat_edit

                    res = chat_edit(spec, message, history, ctx,
                                    body.get("images") or None, provider)
                    _trace_engineering_result(
                        res.get("spec") if isinstance(res, dict) else None
                    )
                    st.guard.add_tokens(int(((res.get("usage") or {}).get("total")) or 0))
                    if st.consume_chat_cancellation(body.get("operation_id")):
                        self._json({
                            "ok": False,
                            "error": "Команда остановлена",
                            "code": "operation_cancelled",
                        }, 409)
                        return
                    if auth is not None:
                        entry = st.workspaces.append_ai_history(
                            auth,
                            spec_path,
                            message=message,
                            reply=str(res.get("reply") or ""),
                            before_spec=spec,
                            after_spec=res.get("spec") if isinstance(res.get("spec"), dict) else None,
                            changes=res.get("changes") if isinstance(res.get("changes"), list) else [],
                            provider=str((res.get("usage") or {}).get("model") or provider or ""),
                            usage=res.get("usage") if isinstance(res.get("usage"), dict) else {},
                            context=ctx if isinstance(ctx, dict) else {},
                            image_count=len(body.get("images") or []),
                            trace_id=current_trace_id(),
                        )
                        res = dict(res)
                        res["history_entry"] = entry
                        self._audit_product_action(
                            auth,
                            "studio.ai.completed",
                            spec_path,
                            {
                                "before_revision": entry["before_revision"],
                                "after_revision": entry["after_revision"],
                                "provider": entry["provider"],
                                "changes_count": len(entry["changes"]),
                                "usage_total": entry["usage"].get("total", 0),
                                "attachments_count": entry["image_count"],
                            },
                        )
                    self._json(res)
                elif path == "/api/providers":       # список нейросетей для селектора
                    from .spec_chat import available_providers
                    self._json(available_providers())
                elif path == "/api/token-balance":   # лимиты/баланс выбранной сети
                    from .spec_chat import token_balance
                    self._json(token_balance(body.get("provider") or None))
                elif path == "/api/import-tz":   # drag&drop ТЗ (D4) → провайдер чата
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
                        _trace_engineering_result(
                            res.get("spec") if isinstance(res, dict) else None
                        )
                        st.guard.add_tokens(int(((res.get("usage") or {}).get("total")) or 0))
                        new_spec = res.get("spec")
                        if not new_spec:
                            error_code = str(res.get("code") or "create_paramspec_failed")
                            trace_id = current_trace_id()
                            add_current_attributes({
                                "check.outcome": "error",
                                "error.codes": [error_code],
                                "ai.workflow": "import_tz",
                            })
                            AI_LOGGER.warning(
                                "import_tz_failed code=%s trace_id=%s provider=%s",
                                error_code,
                                trace_id or "unavailable",
                                str(body.get("provider") or "default"),
                            )
                            status = 502 if error_code == "ai_provider_failed" else 422
                            public_errors = {
                                "ai_provider_failed": (
                                    "Сервис AI временно недоступен. Повторите попытку."
                                ),
                                "invalid_provider_response": (
                                    "Сервис AI вернул некорректный ответ."
                                ),
                            }
                            payload = {
                                "ok": False,
                                "error": public_errors.get(error_code)
                                or res.get("reply")
                                or "Не удалось распознать ТЗ",
                                "code": error_code,
                                "error_code": error_code,
                            }
                            if isinstance(res.get("check_report"), dict):
                                payload["check_report"] = res["check_report"]
                            if isinstance(res.get("usage"), dict):
                                payload["usage"] = res["usage"]
                            self._json(payload, status)
                            return
                        if isinstance(current_spec, dict) and current_spec.get("draft"):
                            out = spec_path        # ТЗ в черновик — тот же файл
                            new_spec = _stamp_catalog_identity(
                                new_spec,
                                _catalog_actor(auth),
                                preserved=current_spec,
                            )
                        else:
                            new_spec = _stamp_catalog_identity(
                                new_spec, _catalog_actor(auth)
                            )
                            title = new_spec.get("project_name", "Из ТЗ")
                            out = workspace.spec_dir / f"{_slugify(title)}.json"
                            i = 2
                            while out.exists():
                                out = workspace.spec_dir / f"{_slugify(title)}_{i}.json"
                                i += 1
                        from .telemetry import span
                        with span("revision.persist", {
                            "project.hash": hash_payload({"project_file": out.name}),
                            "revision.hash": hash_payload(new_spec),
                        }) as persist_span:
                            _write_json_atomic(out, new_spec)
                            persist_span.set_attributes({"revision.persisted": True,
                                                         "check.outcome": "pass"})
                        st.workspaces.set_current(auth, out)
                        self._json({"ok": True, "spec": new_spec, "file": out.name,
                                    "usage": res.get("usage")})
                    except Exception as error:
                        error_code = "import_tz_internal_error"
                        trace_id = current_trace_id()
                        add_current_attributes({
                            "check.outcome": "error",
                            "error.codes": [error_code],
                            "ai.workflow": "import_tz",
                            "exception.type": type(error).__name__,
                        })
                        AI_LOGGER.warning(
                            "import_tz_failed code=%s trace_id=%s exception_type=%s",
                            error_code,
                            trace_id or "unavailable",
                            type(error).__name__,
                        )
                        self._json({
                            "ok": False,
                            "error": "Внутренняя ошибка обработки ТЗ",
                            "code": error_code,
                            "error_code": error_code,
                        }, 500)
                elif path == "/api/versions":    # версии спеки (D2)
                    self._json({"versions": _list_versions(spec_path)})
                elif path == "/api/restore":     # восстановить версию (D2)
                    idx = int(body.get("index", -1))
                    vs = _read_versions(spec_path)
                    if 0 <= idx < len(vs):
                        restored = vs[idx]["spec"]
                        self._json({"ok": True, "spec": restored,
                                    "ts": vs[idx]["ts"]})
                    else:
                        self._json({"ok": False, "error": "нет такой версии"}, 404)
                elif path == "/api/projects":    # каталог спек (D1)
                    members = _catalog_members(st, auth)
                    member_names = {
                        member["user_id"]: member["display_name"] for member in members
                    }
                    owner = next(
                        (member for member in members if member.get("role") == "owner"),
                        members[0] if members else None,
                    )
                    _migrate_catalog_identity(workspace.spec_dir, owner)
                    active_projects = _list_projects(
                        workspace.spec_dir, member_names=member_names
                    )
                    for item in active_projects:
                        item["can_manage"] = bool(
                            workspace.mode != "demo" and _catalog_can_manage(auth, item)
                        )
                    archived_projects = _archived_catalog_projects(
                        st.workspaces.list_archived_products(auth),
                        auth=auth,
                        member_names=member_names,
                    )
                    if workspace.mode == "demo":
                        for item in archived_projects:
                            item["can_manage"] = False
                    actor = _catalog_actor(auth)
                    current_user_id = str((actor or {}).get("user_id") or "")
                    archive_mode = str(body.get("scope") or "all") == "archived"
                    catalog_source = archived_projects if archive_mode else active_projects
                    projects = _filter_catalog_projects(
                        catalog_source, body, current_user_id=current_user_id
                    )
                    self._json({
                        "projects": projects,
                        "current": spec_path.name,
                        "total": len(catalog_source),
                        "counts": {
                            "all": len(active_projects),
                            "mine": sum(
                                item.get("responsible_user_id") == current_user_id
                                for item in active_projects
                            ) if current_user_id else 0,
                            "unassigned": sum(
                                not item.get("responsible_user_id") for item in active_projects
                            ),
                            "archived": len(archived_projects),
                        },
                        "members": members,
                        "types": sorted({str(item["category"]) for item in catalog_source}),
                        "current_user_id": current_user_id,
                        "archive_mode": archive_mode,
                    })
                elif path == "/api/open":        # открыть другую спеку (D1)
                    p = _safe_spec_file(workspace.spec_dir, str(body.get("file", "")))
                    opened = json.loads(p.read_text(encoding="utf-8"))
                    if not isinstance(opened, dict):
                        self._json({
                            "ok": False,
                            "code": "invalid_paramspec",
                            "error": "Изделие не открыто: ParamSpec должен быть объектом",
                        }, 422)
                        return
                    try:
                        opened, paramspec_read = _read_paramspec_document(opened)
                    except (TypeError, ValueError):
                        from .paramspec import validate_paramspec

                        self._json({
                            "ok": False,
                            "code": "invalid_paramspec",
                            "error": "Изделие не открыто: ParamSpec не прошёл проверку схемы",
                            "details": validate_paramspec(opened)[:8],
                        }, 422)
                        return
                    payload = None
                    if not opened.get("draft"):
                        payload = build_payload(opened)
                        if not isinstance(payload.get("viewer"), dict):
                            schema_errors = list(payload.get("issues", {}).get("schema") or [])
                            self._json({
                                "ok": False,
                                "code": "invalid_paramspec",
                                "error": "Изделие не открыто: ParamSpec не прошёл проверку схемы",
                                "issues": payload.get("issues", {}),
                                "details": schema_errors[:8],
                            }, 422)
                            return
                    st.workspaces.set_current(auth, p)
                    self._json({
                        "ok": True,
                        "spec": opened,
                        "file": p.name,
                        "payload": payload,
                        "paramspec_read": paramspec_read,
                    })
                elif path == "/api/rename":      # переименовать из каталога без открытия
                    p = _safe_spec_file(workspace.spec_dir, str(body.get("file", "")))
                    if workspace.mode == "demo" or (
                        st.public and workspace.organization_id is None and p.name in st.protected
                    ):
                        self._json({"ok": False, "error":
                                    "демо-режим: исходное изделие защищено"}, 403)
                        return
                    renamed = json.loads(p.read_text(encoding="utf-8"))
                    if not _catalog_can_manage(auth, _catalog_spec_permissions(renamed)):
                        self._json({
                            "ok": False,
                            "error": "Этим изделием управляет другой сотрудник",
                            "code": "project_forbidden",
                        }, 403)
                        return
                    name = _catalog_product_name(body.get("name"))
                    existing = _list_projects(workspace.spec_dir)
                    if _catalog_name_conflict(existing, name, exclude_file=p.name):
                        self._json({
                            "ok": False,
                            "error": "Изделие с таким названием уже есть в каталоге",
                            "code": "name_conflict",
                        }, 409)
                        return
                    previous_name = str(renamed.get("project_name") or p.stem)
                    _snapshot_version(p, renamed)
                    renamed["project_name"] = name
                    _write_json_atomic(p, renamed)
                    _snapshot_version(p, renamed)
                    self._audit_product_action(
                        auth, "studio.project.renamed", p,
                        {"previous_name": previous_name, "name": name},
                    )
                    member_names = {
                        member["user_id"]: member["display_name"]
                        for member in _catalog_members(st, auth)
                    }
                    project = next(
                        (item for item in _list_projects(
                            workspace.spec_dir, member_names=member_names
                        )
                         if item["file"] == p.name), None
                    )
                    self._json({"ok": True, "file": p.name, "project": project})
                elif path == "/api/catalog/assign":
                    p = _safe_spec_file(workspace.spec_dir, str(body.get("file", "")))
                    if workspace.mode == "demo":
                        self._json({"ok": False, "error": "Демо-каталог неизменяем"}, 403)
                        return
                    assigned = json.loads(p.read_text(encoding="utf-8"))
                    if not _catalog_can_manage(auth, _catalog_spec_permissions(assigned)):
                        self._json({
                            "ok": False,
                            "error": "Этим изделием управляет другой сотрудник",
                            "code": "project_forbidden",
                        }, 403)
                        return
                    responsible_user_id = str(body.get("responsible_user_id") or "")
                    members = _catalog_members(st, auth)
                    responsible = next(
                        (member for member in members
                         if member["user_id"] == responsible_user_id),
                        None,
                    ) if responsible_user_id else None
                    if responsible_user_id and responsible is None:
                        self._json({
                            "ok": False,
                            "error": "Ответственный не найден среди активных сотрудников",
                            "code": "member_unavailable",
                        }, 409)
                        return
                    catalog = dict(assigned.get("catalog") or {})
                    previous_user_id = str(catalog.get("responsible_user_id") or "")
                    previous_name = str(catalog.get("responsible") or "")
                    if previous_user_id == responsible_user_id:
                        self._json({"ok": True, "file": p.name, "unchanged": True})
                        return
                    _snapshot_version(p, assigned)
                    catalog["responsible_user_id"] = responsible_user_id
                    catalog["responsible"] = str((responsible or {}).get("display_name") or "")
                    assigned["catalog"] = catalog
                    _write_json_atomic(p, assigned)
                    _snapshot_version(p, assigned)
                    self._audit_product_action(
                        auth,
                        "studio.project.responsible_changed",
                        p,
                        {
                            "previous_user_id": previous_user_id,
                            "previous_name": previous_name,
                            "responsible_user_id": responsible_user_id,
                            "responsible": catalog["responsible"],
                        },
                    )
                    self._json({
                        "ok": True,
                        "file": p.name,
                        "responsible_user_id": responsible_user_id,
                        "responsible": catalog["responsible"],
                    })
                elif path == "/api/catalog/archive":
                    p = _safe_spec_file(workspace.spec_dir, str(body.get("file", "")))
                    if workspace.mode == "demo":
                        self._json({"ok": False, "error": "Демо-каталог неизменяем"}, 403)
                        return
                    archived_spec = json.loads(p.read_text(encoding="utf-8"))
                    if not _catalog_can_manage(auth, _catalog_spec_permissions(archived_spec)):
                        self._json({
                            "ok": False,
                            "error": "Этим изделием управляет другой сотрудник",
                            "code": "project_forbidden",
                        }, 403)
                        return
                    reason = _catalog_archive_reason(body.get("reason"))
                    actor = _catalog_actor(auth) or {}
                    try:
                        manifest = st.workspaces.archive_product(
                            auth,
                            p,
                            actor_user_id=str(actor.get("user_id") or ""),
                            actor_name=str(actor.get("display_name") or "Локальный пользователь"),
                            reason=reason,
                        )
                    except FileExistsError as error:
                        self._json({"ok": False, "error": str(error), "code": "archive_conflict"}, 409)
                        return
                    self._audit_product_action(
                        auth,
                        "studio.project.archived",
                        p,
                        {
                            "archive_id": manifest["id"],
                            "name": manifest["project_name"],
                            "reason": reason,
                            "revision": manifest["revision"],
                        },
                    )
                    self._json({
                        "ok": True,
                        "archive_id": manifest["id"],
                        "file": manifest["project_file"],
                        "name": manifest["project_name"],
                    })
                elif path == "/api/catalog/restore":
                    if workspace.mode == "demo":
                        self._json({"ok": False, "error": "Демо-каталог неизменяем"}, 403)
                        return
                    archive_id = str(body.get("archive_id") or "")
                    manifests = st.workspaces.list_archived_products(auth)
                    manifest = next(
                        (item for item in manifests if str(item.get("id") or "") == archive_id),
                        None,
                    )
                    if manifest is None:
                        self._json({"ok": False, "error": "Архив не найден", "code": "not_found"}, 404)
                        return
                    if not _catalog_can_manage(auth, manifest):
                        self._json({
                            "ok": False,
                            "error": "Этим изделием управляет другой сотрудник",
                            "code": "project_forbidden",
                        }, 403)
                        return
                    active_projects = _list_projects(workspace.spec_dir)
                    restore_name = _catalog_product_name(manifest.get("project_name"))
                    if _catalog_name_conflict(active_projects, restore_name):
                        self._json({
                            "ok": False,
                            "error": "В каталоге уже есть изделие с таким названием",
                            "code": "name_conflict",
                        }, 409)
                        return
                    actor = _catalog_actor(auth) or {}
                    try:
                        restored = st.workspaces.restore_product(
                            auth,
                            archive_id,
                            actor_user_id=str(actor.get("user_id") or ""),
                            actor_name=str(actor.get("display_name") or "Локальный пользователь"),
                        )
                    except FileExistsError as error:
                        self._json({"ok": False, "error": str(error), "code": "restore_conflict"}, 409)
                        return
                    restored_path = workspace.spec_dir / str(restored["project_file"])
                    self._audit_product_action(
                        auth,
                        "studio.project.restored",
                        restored_path,
                        {
                            "archive_id": archive_id,
                            "name": restored["project_name"],
                            "archive_reason": restored.get("reason") or "",
                        },
                    )
                    self._json({
                        "ok": True,
                        "archive_id": archive_id,
                        "file": restored["project_file"],
                        "name": restored["project_name"],
                    })
                elif path == "/api/new":         # новое изделие: черновик (AKD-214)
                    name = str(body.get("name") or "Новое изделие")
                    new_spec = _stamp_catalog_identity(
                        {"schemaVersion": "paramspec-v1", "draft": True,
                         "project_name": name},
                        _catalog_actor(auth),
                    )
                    p = workspace.spec_dir / f"{_slugify(name)}.json"
                    i = 2
                    while p.exists():
                        p = workspace.spec_dir / f"{_slugify(name)}_{i}.json"
                        i += 1
                    _write_json_atomic(p, new_spec)
                    st.workspaces.set_current(auth, p)
                    self._json({"ok": True, "spec": new_spec, "file": p.name})
                elif path == "/api/duplicate":   # дубликат текущего (D1)
                    source_path = spec_path
                    if body.get("file"):
                        source_path = _safe_spec_file(
                            workspace.spec_dir, str(body.get("file", "")))
                    source_spec = (json.loads(source_path.read_text(encoding="utf-8"))
                                   if body.get("file") else (spec or current_spec))
                    dup = json.loads(json.dumps(source_spec))
                    dup["project_name"] = str(dup.get("project_name", "модель")) + " (копия)"
                    actor = _catalog_actor(auth)
                    if actor:
                        dup["catalog"] = {
                            key: value for key, value in dict(dup.get("catalog") or {}).items()
                            if key not in _CATALOG_IDENTITY_FIELDS
                        }
                        _stamp_catalog_identity(dup, actor)
                    p = workspace.spec_dir / f"{source_path.stem}_copy.json"
                    i = 2
                    while p.exists():
                        p = workspace.spec_dir / f"{source_path.stem}_copy{i}.json"
                        i += 1
                    _write_json_atomic(p, dup)
                    if not body.get("stay_catalog"):
                        st.workspaces.set_current(auth, p)
                    self._audit_product_action(
                        auth, "studio.project.duplicated", p,
                        {"source_file": source_path.name},
                    )
                    self._json({"ok": True, "spec": dup, "file": p.name})
                elif path == "/api/reviews":
                    context = (auth or {}).get("context") or {}
                    organization = context.get("organization") or {}
                    self._json({
                        "ok": True,
                        "project_file": spec_path.name,
                        "reviews": st.reviews.list_for_project(
                            organization_id=str(organization.get("id") or "") or None,
                            project_file=spec_path.name,
                            current_spec=current_spec,
                        ),
                    })
                elif path == "/api/reviews/create":
                    review_path = spec_path
                    if body.get("file"):
                        review_path = _safe_spec_file(
                            workspace.spec_dir, str(body.get("file") or "")
                        )
                    stored_spec = json.loads(review_path.read_text(encoding="utf-8"))
                    incoming_spec = body.get("spec")
                    link_mode = str(body.get("link_mode") or "snapshot")
                    expires_value = body.get("expires_in_days")
                    try:
                        expires_in_days = (
                            int(expires_value)
                            if expires_value not in (None, "", 0, "0")
                            else None
                        )
                    except (TypeError, ValueError):
                        self._json({"ok": False, "error": "Выберите срок действия ссылки"}, 400)
                        return
                    if link_mode not in {"snapshot", "live"} or expires_in_days not in {
                        None, 7, 30, 90,
                    }:
                        self._json({
                            "ok": False,
                            "error": "Выберите тип и срок действия ссылки",
                        }, 400)
                        return
                    if (
                        link_mode == "live"
                        and isinstance(incoming_spec, dict)
                        and not hmac.compare_digest(
                            _spec_revision(incoming_spec), _spec_revision(stored_spec)
                        )
                    ):
                        self._json({
                            "ok": False,
                            "error": (
                                "Обновляемая ссылка показывает последнюю сохранённую версию. "
                                "Сначала сохраните текущие изменения."
                            ),
                            "code": "save_required",
                        }, 409)
                        return
                    review_spec = (
                        json.loads(json.dumps(incoming_spec))
                        if isinstance(incoming_spec, dict) and link_mode != "live"
                        else stored_spec
                    )
                    if link_mode != "live":
                        review_spec = _stamp_catalog_identity(
                            review_spec, _catalog_actor(auth), preserved=stored_spec
                        )
                    if not isinstance(review_spec, dict) or review_spec.get("draft"):
                        self._json({
                            "ok": False,
                            "error": "Сначала соберите модель изделия",
                            "code": "model_required",
                        }, 409)
                        return
                    try:
                        from .generators import generate_from_paramspec
                        generate_from_paramspec(review_spec)
                    except Exception as error:
                        self._json({
                            "ok": False,
                            "error": "Текущую модель нельзя открыть для просмотра: "
                            + str(error)[:220],
                            "code": "model_invalid",
                        }, 409)
                        return
                    context = (auth or {}).get("context") or {}
                    organization = context.get("organization") or {}
                    user = context.get("user") or {}
                    catalog = review_spec.get("catalog") or {}
                    if not isinstance(catalog, dict):
                        catalog = {}
                    token, review = st.reviews.create(
                        review_spec,
                        organization_id=str(organization.get("id") or "") or None,
                        organization_name=str(organization.get("name") or ""),
                        project_file=review_path.name,
                        actor_user_id=str(user.get("id") or "") or None,
                        actor_name=str(
                            user.get("display_name") or user.get("email")
                            or "Локальный проектировщик"
                        ),
                        responsible_user_id=str(
                            catalog.get("responsible_user_id") or user.get("id") or ""
                        ) or None,
                        responsible_name=str(
                            catalog.get("responsible") or user.get("display_name")
                            or user.get("email") or "Локальный проектировщик"
                        ),
                        link_mode=link_mode,
                        expires_in_days=expires_in_days,
                    )
                    self._audit_product_action(
                        auth,
                        "studio.review.created",
                        review_path,
                        {
                            "review_id": review["id"],
                            "revision": review["revision"],
                            "link_mode": review["link_mode"],
                            "expires_at": review["expires_at"],
                        },
                    )
                    self._json({
                        "ok": True,
                        "url": self._request_origin() + "/review/" + token,
                        "review": {
                            "id": review["id"],
                            "revision": review["revision"],
                            "created_at": review["created_at"],
                            "status": review["status"],
                            "project_name": review["project_name"],
                            "link_mode": review["link_mode"],
                            "expires_at": review["expires_at"],
                        },
                    })
                elif path == "/api/reviews/revoke":
                    context = (auth or {}).get("context") or {}
                    organization = context.get("organization") or {}
                    try:
                        review = st.reviews.revoke(
                            review_id=str(body.get("review_id") or ""),
                            organization_id=str(organization.get("id") or "") or None,
                            project_file=spec_path.name,
                        )
                    except FileNotFoundError:
                        self._json({"ok": False, "error": "Ссылка не найдена"}, 404)
                        return
                    self._audit_product_action(
                        auth,
                        "studio.review.revoked",
                        spec_path,
                        {
                            "review_id": str(review.get("id") or ""),
                            "revision": str(review.get("revision") or ""),
                        },
                    )
                    self._json({
                        "ok": True,
                        "review_id": str(review.get("id") or ""),
                        "revoked_at": str(review.get("revoked_at") or ""),
                    })
                elif path == "/api/nesting":     # раскрой-превью (C2)
                    from .generators import generate_from_paramspec
                    from .nesting import nesting_svg
                    try:
                        self._json({"svg": nesting_svg(generate_from_paramspec(spec))})
                    except Exception as e:
                        self._json({"svg": "", "error": str(e)[:200]})
                elif path == "/api/decors":
                    from .materials import list_sheet_decors
                    th = body.get("thickness")
                    self._json({"items": list_sheet_decors(
                        str(body.get("q", "")),
                        thickness=float(th) if th else None,
                        limit=int(body.get("limit", 30)))})
                elif path == "/api/save":
                    # демо-режим (AKD-271): исходные образцы каталога защищены
                    if workspace.mode == "demo" or (
                        st.public and workspace.organization_id is None
                        and spec_path.name in st.protected
                    ):
                        self._json({"ok": False, "error":
                                    "демо-режим: исходное изделие защищено — "
                                    "нажмите «Дублировать» и правьте копию"}, 403)
                        return
                    if not isinstance(spec, dict):
                        self._json({"ok": False, "error": "Некорректное изделие"}, 400)
                        return
                    spec = _stamp_catalog_identity(
                        dict(spec), _catalog_actor(auth), preserved=current_spec
                    )
                    from .paramspec import validate_paramspec
                    from .paramspec_versioning import strict_paramspec_v1_for_write

                    # The operator's work is never refused: a document that does
                    # not pass the strict contract is persisted as-is (the way
                    # Studio always did) and the problems come back as warnings.
                    write_errors = validate_paramspec(spec)
                    if write_errors:
                        save_warnings = [str(item) for item in write_errors[:12]]
                        spec = _reorder_like(spec, current_spec)
                    else:
                        save_warnings = []
                        spec = _reorder_like(strict_paramspec_v1_for_write(spec), current_spec)
                    _write_json_atomic(spec_path, spec)
                    add_current_attributes({
                        "revision.hash": hash_payload(spec),
                        "revision.persisted": True,
                        "check.outcome": "pass",
                    })
                    prev = body.get("preview")         # снапшот 3D для каталога (AKD-217)
                    if isinstance(prev, str) and prev.startswith("data:image/png;base64,"):
                        try:
                            pd = workspace.spec_dir / ".previews"
                            pd.mkdir(exist_ok=True)
                            (pd / (spec_path.stem + ".png")).write_bytes(
                                base64.b64decode(prev.split(",", 1)[1]))
                        except Exception:
                            pass
                    if spec.get("draft") or save_warnings:   # черновик/с замечаниями: только файл
                        self._json({"ok": True, "spec": str(spec_path), "project": None,
                                    "warnings": save_warnings})
                        return
                    _snapshot_version(spec_path, spec)          # версия (D2)
                    from .generators import generate_from_paramspec
                    project = generate_from_paramspec(spec)
                    out = workspace.out_dir / (spec_path.stem + ".project.json")
                    _write_json_atomic(out, project)
                    self._json({"ok": True, "spec": str(spec_path), "project": str(out),
                                "warnings": []})
                elif path == "/api/export-cfrn":
                    from .generators import generate_from_paramspec
                    from .materials import resolve_project_materials
                    from .cfrn import project_to_cfrn_bytes
                    project = generate_from_paramspec(spec)
                    try:
                        project["material_refs"] = resolve_project_materials(project)
                    except Exception:
                        pass
                    out = workspace.out_dir / (spec_path.stem + ".cfrn")
                    out.write_bytes(project_to_cfrn_bytes(project))
                    self._json({"ok": True, "cfrn": str(out), "warnings": production_warnings})
                elif path == "/api/build-b3d":
                    payload = build_payload(spec)
                    if not payload["ok"]:                # деньги — только на зелёную модель
                        self._json({"ok": False, "error": "проверки не пройдены",
                                    "issues": payload["issues"]}, 409)
                        return
                    from .build_b3d import build_b3d_from_paramspec
                    out = workspace.out_dir / (spec_path.stem + ".b3d")
                    try:
                        rep = build_b3d_from_paramspec(spec, out)
                        parity = _verify_parity(spec, out)         # паритет ✓ (AKD-169)
                        _log_build(workspace.out_dir, spec, out, parity)  # история сборок (C3)
                        self._json({"ok": True, "parity": parity,
                                    **{k: str(v) for k, v in rep.items()}})
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)[:300]}, 502)
                elif path == "/api/builds":         # история сборок .b3d (C3)
                    self._json(_read_builds(workspace.out_dir))
                elif path == "/api/open-file":      # открыть результат (C3)
                    self._json(_open_file(workspace.out_dir, str(body.get("path", ""))))
                elif path == "/api/deliver":        # лист согласования (C3)
                    from datetime import datetime
                    from .generators import generate_from_paramspec
                    from .materials import resolve_project_materials
                    from .delivery import create_delivery
                    project = generate_from_paramspec(spec)
                    try:
                        project["material_refs"] = resolve_project_materials(project)
                    except Exception:
                        pass
                    res = create_delivery(spec, project, out_root=workspace.out_dir,
                                          created_iso=datetime.now().isoformat(timespec="seconds"),
                                          export=False)
                    import webbrowser
                    webbrowser.open(Path(res["page"]).resolve().as_uri())
                    self._json({"ok": True, "page": res["page"],
                                "version": res["version"]})
                else:
                    self._send(404, b"{}")
            except FileNotFoundError:
                self._json(
                    {"ok": False, "error": "Изделие не найдено", "code": "not_found"},
                    404,
                )
            except ValueError as e:
                self._json(
                    {"ok": False, "error": str(e)[:300] or "Некорректный запрос",
                     "code": "invalid_request"},
                    400,
                )
            except Exception as e:
                self._json({"ok": False, "error": str(e)[:300]}, 500)

    from .telemetry import traced_http_request
    Handler.do_POST = traced_http_request(Handler.do_POST)
    return Handler


def run_studio(
    spec_path: str | Path,
    *,
    port: int = 8765,
    out_dir: str | Path | None = None,
    open_browser: bool = True,
    identity_db: str | Path | None = None,
    tenant_root: str | Path | None = None,
    require_auth: bool = False,
) -> None:
    spec_path = Path(spec_path)
    out = Path(out_dir) if out_dir else spec_path.parent
    st = _Studio(
        spec_path,
        out,
        identity_db=Path(identity_db) if identity_db else None,
        tenant_root=Path(tenant_root) if tenant_root else None,
        require_auth=require_auth,
    )
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
        --ok:#2fa84f;--warn:#c78a2b;--bad:#e5484d;--accent:#3b82f6}
  *{box-sizing:border-box} html,body{margin:0;height:100%;font-family:Segoe UI,Arial,sans-serif;
    background:var(--bg);color:var(--ink);font-size:13px;overflow:hidden}
  button:focus-visible,input:focus-visible,select:focus-visible,textarea:focus-visible,
    a:focus-visible{outline:2px solid var(--accent);outline-offset:1px}
  @media (prefers-reduced-motion:reduce){*,*::before,*::after{scroll-behavior:auto!important;
    animation-duration:.01ms!important;animation-iteration-count:1!important;transition-duration:.01ms!important}}
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
  #side .project-share{display:flex;align-items:center;justify-content:center;gap:6px;width:100%;
    margin-top:6px;padding:7px 8px;border-color:#b9c9e7;background:#f4f7fd;color:#285ba9;
    font-weight:600}
  #side .project-share:hover{background:#eaf1ff;border-color:#91addb}
  #side svg.ui-icon,#fs_chat svg.ui-icon{width:16px;height:16px;flex:0 0 16px;fill:none;stroke:currentColor;
    stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}
  #rightside svg.ui-icon{width:16px;height:16px;flex:0 0 16px;fill:none;stroke:currentColor;
    stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}
  #rightside #verRestore{display:grid;place-items:center;flex:0 0 32px;width:32px;height:32px;padding:0}
  #side .icon-button{display:grid;place-items:center;flex:0 0 32px;width:32px;height:32px;padding:0}
  #side h1.studio-brand{margin:0 0 13px;font-size:13px;line-height:1;letter-spacing:0}
  #side .studio-brand-home{display:flex;align-items:center;gap:8px;width:max-content;
    max-width:100%;height:34px;
    min-width:0;margin:0;padding:2px 1px;border:0;border-radius:5px;background:transparent;
    color:inherit;text-align:left}
  #side .studio-brand-home:hover{background:transparent}
  #side .studio-brand-home:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
  #side .studio-brand-wordmark{display:block;width:158px;max-width:calc(100% - 72px);
    height:auto;object-fit:contain}
  #side .studio-brand-mark{display:block;width:64px;height:auto;object-fit:contain}
  .profile-shell{position:relative;margin:0 0 12px}
  #profileChip{display:grid;grid-template-columns:32px minmax(0,1fr) 14px;align-items:center;
    gap:9px;width:100%;min-height:48px;padding:7px 9px;border:1px solid #e0e5eb;border-radius:11px;
    background:#f8fafc;color:#202631;text-align:left;box-shadow:none}
  #profileChip:hover,#profileChip[aria-expanded="true"]{border-color:#c8d5e7;background:#f2f6fb}
  #profileChip[hidden]{display:none}
  .profile-avatar{display:grid;place-items:center;width:32px;height:32px;border-radius:9px;
    background:#1f66d3;color:#fff;font-size:11px;font-weight:750;letter-spacing:.03em}
  .profile-copy{display:grid;min-width:0;gap:1px}.profile-copy strong,.profile-copy span{
    overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .profile-copy strong{font-size:11.5px;line-height:15px}.profile-copy span{color:#687180;
    font-size:10px;line-height:14px}.profile-chevron{color:#7b8491;font-size:13px;text-align:center}
  #profileMenu{position:absolute;z-index:40;top:54px;left:0;right:0;padding:10px;border:1px solid #dbe1e8;
    border-radius:12px;background:#fff;box-shadow:0 14px 34px rgba(27,37,52,.16)}
  #profileMenu[hidden]{display:none}.profile-menu-name{font-size:12px;font-weight:700}
  .profile-menu-email{margin-top:2px;color:#687180;font-size:10.5px;overflow-wrap:anywhere}
  .profile-menu-role{display:inline-flex;margin-top:8px;padding:3px 6px;border-radius:999px;
    background:#eef4ff;color:#275fae;font-size:9.5px;font-weight:650}
  #profileCompanyAdmin{display:flex;align-items:center;justify-content:center;width:100%;min-height:30px;
    margin-top:10px;padding:6px 9px;border:1px solid #c8d5e7;border-radius:5px;
    background:#f5f8fd;color:#285ba9;font-size:10.5px;font-weight:650;text-decoration:none}
  #profileCompanyAdmin:hover{border-color:#91addb;background:#eaf1ff}
  #profileLogout{display:flex;align-items:center;justify-content:center;width:100%;margin-top:7px;
    border-color:#e2e6eb;background:#fff;color:#3e4754}
  #profileLogout:hover{border-color:#d5a7aa;background:#fff6f6;color:#a12e33}
  #catalogContext{display:none;padding:11px 0 13px;border-top:1px solid var(--line);
    border-bottom:1px solid var(--line)}
  #catalogContext .catalog-context-kicker{display:block;margin-bottom:3px;color:#687180;
    font-size:10.5px;line-height:14px}
  #catalogContext strong{display:block;font-size:13.5px;line-height:18px}
  #catalogContext p{margin:5px 0 9px;color:#606a77;font-size:11px;line-height:15px}
  #catalogContextCount{display:flex;align-items:center;gap:6px;color:#46505e;
    font-size:10.5px;font-variant-numeric:tabular-nums}
  #catalogContextCount::before{content:"";width:6px;height:6px;border-radius:50%;
    background:var(--accent)}
  #catalogInspectorSlot{display:none}
  #app.catalog-mode #sideScroll{flex:1 1 auto;overflow-y:auto;overscroll-behavior:contain;
    scrollbar-gutter:stable}
  #app.catalog-mode #catalogInspectorSlot{display:block;margin-top:12px}
  #app.catalog-mode #catInspector{margin:0;border:0;border-top:1px solid var(--line)}
  #app.catalog-mode #catInspectorEmpty{min-height:150px}
  #app.catalog-mode #catInspectPreview{height:150px;flex-basis:150px}
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
  .part-utility-actions{display:flex;justify-content:flex-start;margin-top:7px}
  .part-utility-actions button{display:flex;align-items:center;justify-content:center;gap:5px}
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
  #fs_part[aria-busy="true"] input,#fs_part[aria-busy="true"] button{cursor:wait}
  #main{--chat-stack-height:133px;--viewport-status-height:24px;
    grid-column:2;grid-row:1;position:relative;min-width:0;min-height:0;
    container-type:inline-size}
  #supportBanner{position:absolute;z-index:9;top:0;left:0;right:0;min-height:36px;
    display:flex;align-items:center;justify-content:center;gap:10px;padding:5px 12px;
    background:#172b4d;color:#fff;box-shadow:0 1px 4px rgba(19,33,55,.22)}
  #supportBanner[hidden]{display:none}#supportBanner span{font-size:11.5px}
  #supportBanner strong{font-weight:750}#supportBack{height:26px;padding:0 9px;border-color:#71809a;
    background:rgba(255,255,255,.08);color:#fff;font-size:10.5px}
  #supportBack:hover{background:rgba(255,255,255,.16)}
  #app.support-active #viewportTopbar{top:48px}
  #app.support-active #hud{top:90px}
  #rightside{grid-column:3;grid-row:1;min-width:0;width:100%;background:var(--card);
    border-left:1px solid var(--line);overflow-y:auto;padding:0 12px 12px;
    opacity:1;visibility:visible;transition:opacity .12s ease}
  #rightPanelClose{position:absolute;top:20px;right:7px;z-index:4;display:grid;place-items:center;
    width:28px;height:28px;padding:0;border-color:transparent;background:var(--card)}
  #rightPanelTabs{position:sticky;top:0;z-index:3;display:grid;
    grid-template-columns:repeat(2,minmax(0,1fr));margin:0 -12px 8px;padding:8px 42px 0 8px;
    background:var(--card);border-bottom:1px solid var(--line)}
  #rightPanelTabs button{min-width:0;height:34px;padding:0 6px;border:0;border-bottom:2px solid transparent;
    border-radius:0;background:transparent;color:#66707e;font-size:11px;white-space:nowrap;
    overflow:visible;text-overflow:clip}
  #rightPanelTabs button:hover{background:#f5f7f9;color:#303947}
  #rightPanelTabs button[aria-selected="true"]{border-bottom-color:var(--accent);color:#245eae;
    background:transparent;font-weight:600}
  #rightPanelTabs button:focus-visible{position:relative;z-index:1;outline:2px solid var(--accent);
    outline-offset:-2px}
  .right-panel-view[hidden]{display:none}
  .right-panel-view{min-width:0;padding-bottom:8px}
  .review-inbox-head{display:flex;align-items:center;gap:8px;padding:10px 2px 9px;
    border-bottom:1px solid #e3e7ec}.review-inbox-head p{flex:1;margin:0;color:#697381;
    font-size:10.5px;line-height:14px}.review-inbox-head button{height:28px;padding:0 8px;font-size:10.5px}
  #reviewInbox{min-height:86px}.review-inbox-empty{padding:18px 2px;color:#737d89;font-size:11px;
    line-height:16px}.review-inbox-item{padding:12px 2px;border-bottom:1px solid #e6e9ed}
  .review-inbox-status{display:flex;align-items:center;gap:6px;margin-bottom:5px;font-size:11px;font-weight:700}
  .review-inbox-status::before{content:"";width:7px;height:7px;border-radius:50%;background:#bd7b25}
  .review-inbox-item.approved .review-inbox-status{color:#167142}.review-inbox-item.approved .review-inbox-status::before{background:#16834a}
  .review-inbox-item.changes_requested .review-inbox-status{color:#975614}.review-inbox-item.changes_requested .review-inbox-status::before{background:#c37a22}
  .review-inbox-meta{display:flex;flex-wrap:wrap;gap:3px 8px;color:#77818e;font-size:9.5px;line-height:14px}
  .review-inbox-decision{margin-top:8px;color:#3f4956;font-size:10.5px;line-height:15px}
  .review-inbox-comment{margin:5px 0 0;padding-left:8px;border-left:2px solid #dce2e9;color:#505b68;
    white-space:pre-wrap;overflow-wrap:anywhere}.review-inbox-files{display:grid;gap:4px;margin-top:7px}
  .review-inbox-files a{color:#245eae;font-size:10px;text-decoration:none;overflow-wrap:anywhere}
  .review-inbox-files a:hover{text-decoration:underline}
  .review-inbox-item.revoked,.review-inbox-item.expired{background:#fafbfc}
  .review-inbox-item.revoked .review-inbox-status,.review-inbox-item.expired .review-inbox-status{color:#6f7884}
  .review-inbox-item.revoked .review-inbox-status::before,.review-inbox-item.expired .review-inbox-status::before{background:#8e97a3}
  .review-inbox-actions{display:flex;align-items:center;gap:7px;margin-top:9px}
  .review-inbox-actions button{height:27px;padding:0 8px;border:1px solid #ccd2da;border-radius:4px;
    background:#fff;color:#4e5967;font-size:10px;cursor:pointer}
  .review-inbox-actions button:hover{background:#f0f3f6}.review-inbox-actions button.danger{color:#a03338}
  .review-inbox-history{margin-top:8px;color:#596473;font-size:10px}.review-inbox-history summary{cursor:pointer}
  .review-inbox-history-event{padding:7px 0;border-top:1px solid #e5e8ec}.review-inbox-history-event:first-of-type{margin-top:5px}
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
  /* MEB-096: section controls are contextual, not one repeated web-form. */
  #rightViewProperties #fs_sections{padding-bottom:10px}
  #rightViewProperties #sections{display:grid;margin:0 -2px 8px}
  #rightViewProperties .section-inspector{margin:0;border:0;border-top:1px solid #e6e9ed}
  #rightViewProperties .section-inspector:last-child{border-bottom:1px solid #e6e9ed}
  #rightViewProperties .section-inspector>summary{display:flex;align-items:center;gap:6px;
    min-height:34px;padding:0 2px;color:#303947;font-size:11.5px;font-weight:600;cursor:pointer;
    list-style:none}
  #rightViewProperties .section-inspector>summary::-webkit-details-marker{display:none}
  #rightViewProperties .section-inspector>summary::before{content:"›";width:10px;color:#6e7885;
    font-size:16px;line-height:1;transition:transform .12s ease}
  #rightViewProperties .section-inspector[open]>summary::before{transform:rotate(90deg)}
  #rightViewProperties .section-summary-kind{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  #rightViewProperties .section-summary-share{margin-left:auto;color:#737d89;font-size:10.5px;
    font-weight:500;font-variant-numeric:tabular-nums;white-space:nowrap}
  #rightViewProperties .section-inspector-body{display:grid;gap:7px;padding:1px 2px 11px 18px}
  #rightViewProperties .section-inspector-row{display:grid;grid-template-columns:92px minmax(0,1fr);
    align-items:center;gap:8px;min-width:0}
  #rightViewProperties .section-inspector-row>label{min-width:0;color:#687180;font-size:10.5px;line-height:14px}
  #rightViewProperties .section-inspector-row input,#rightViewProperties .section-inspector-row select{
    min-width:0;max-width:none}
  #rightViewProperties .section-inspector-actions{display:flex;justify-content:flex-end;margin-top:1px}
  #rightViewProperties .section-inspector-actions button{height:27px;padding:0 8px;color:#8d3035;
    border-color:#e2c8ca;background:#fff;font-size:10.5px}
  #rightViewProperties .section-inspector-actions button:hover{background:#fff4f4;border-color:#d69b9e}
  #rightViewProperties .section-inspector-note{margin:0;color:#697381;font-size:10px;line-height:14px}
  #rightViewProperties .section-inspector-state{margin:0;padding:8px 0 2px;color:#697381;
    font-size:10.5px;line-height:15px}
  #rightViewProperties #fs_sections.is-unsupported #sections{display:none}
  #rightViewProperties #fs_sections.is-unsupported #addSec{display:none}
  #rightViewProperties #fs_sections.is-busy .section-inspector-state{color:#8a5a13}
  #rightViewProperties #addSec{height:29px;padding:0 9px;font-size:10.5px}
  @media (prefers-reduced-motion:reduce){
    #rightViewProperties .section-inspector>summary::before{transition:none}
  }
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
  /* MEB-116: каталог — самостоятельный режим рабочего пространства. Модель
     остаётся в памяти для возврата, но её контекст и инструменты не выдаются
     за контекст каталога. */
  #app.catalog-mode,#app.catalog-mode.right-collapsed{
    grid-template-columns:var(--side-width) minmax(0,1fr)}
  #app.catalog-mode #main{grid-column:2}
  #app.catalog-mode #fs_project,#app.catalog-mode #modelState,
  #app.catalog-mode #fs_part{display:none!important}
  #app.catalog-mode #catalogContext{display:block}
  #app.catalog-mode #rightside,#app.catalog-mode #rightRail{display:none!important}
  #rightside fieldset{scroll-margin-top:82px}
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
  fieldset{border:1px solid var(--line);border-radius:8px;margin:0 0 10px;padding:8px 10px}
  legend{font-size:11px;text-transform:uppercase;color:var(--mut);padding:0 4px}
  .row{display:flex;gap:6px;align-items:center;margin:4px 0}
  .row label{flex:0 0 92px;color:var(--mut)}
  input[type=number],input[type=text],select{width:100%;padding:4px 6px;border:1px solid var(--line);
    border-radius:6px;font-size:13px}
  input[type=number]{max-width:86px}
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
    display:grid;grid-template-columns:max-content max-content minmax(0,1fr);grid-template-areas:
      "modes layers views"
      ". . model";align-items:start;justify-content:start;gap:6px;
    pointer-events:none}
  #viewportTools,#hud{display:contents}
  #tabs,#views,#hud .hud-section{display:flex;align-items:center;min-height:36px;padding:3px;
    border:1px solid #d9dee5;border-radius:4px;background:#fff;pointer-events:auto}
  #tabs{grid-area:modes}#views{grid-area:views}
  #hud .hud-layers{grid-area:layers}#hud .hud-model{grid-area:model}
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
  #views{justify-content:stretch;gap:1px}
  #views .viewport-group-label{padding:0 6px 0 4px;color:#7a8390;font-size:10px;
    font-weight:600;letter-spacing:.04em;text-transform:uppercase}
  #views .vw{flex:1 1 0;min-width:0;padding:0 7px;font-size:11px}
  #views[hidden],#hud[hidden]{display:none!important}
  #draw{position:absolute;inset:90px 12px 12px;z-index:4;background:#fff;border:1px solid var(--line);
    border-radius:8px;overflow:auto;display:none;
    padding:8px 8px calc(var(--chat-stack-height) + var(--viewport-status-height) + 14px)}
  /* AKD-217: каталог изделий */
  #catalog{position:absolute;inset:0;z-index:8;display:none;flex-direction:column;
    background:var(--bg);padding:14px 18px;overflow:hidden}
  #catalog.on{display:flex}
  #catHead{display:flex;gap:10px;align-items:center;margin-bottom:10px}
  #catHead input{flex:1;max-width:420px;padding:6px 10px;border:1px solid var(--line);border-radius:5px}
  #catClose{display:flex;align-items:center;gap:5px}
  #catClose svg{width:15px;height:15px;fill:none;stroke:currentColor;stroke-width:1.8;
    stroke-linecap:round}
  /* Каталог держит постоянную рабочую сетку: выбор не должен сдвигать карточки
     и ломать зрительную память человека. Инспектор живёт в левой панели,
     а до выбора показывает короткое направление. */
  #catWorkspace{display:grid;grid-template-columns:minmax(0,1fr);flex:1;min-height:0;
    border-top:1px solid var(--line);overflow:hidden}
  #catBrowser{display:flex;flex-direction:column;min-width:0;min-height:0;padding-top:10px}
  #catFilterbar{display:flex;align-items:center;gap:10px;min-height:38px;margin-bottom:8px;
    border-bottom:1px solid #dfe3e8}
  #catScopes{display:flex;align-self:stretch;gap:2px;flex:0 0 auto}
  .cat-scope{position:relative;display:flex;align-items:center;gap:6px;padding:0 9px;
    border:0;border-radius:0;background:transparent;color:#596473;font-size:11.5px;
    font-weight:600;cursor:pointer}
  .cat-scope::after{content:"";position:absolute;left:9px;right:9px;bottom:-1px;height:2px;
    background:transparent}
  .cat-scope.on{color:#1f5fc9}.cat-scope.on::after{background:var(--accent)}
  .cat-scope output{min-width:18px;padding:1px 5px;border-radius:8px;background:#edf0f4;
    color:#65707e;font-size:9.5px;font-variant-numeric:tabular-nums;text-align:center}
  .cat-scope.on output{background:#e6efff;color:#1f5fc9}
  #catFilterFields{display:flex;align-items:center;gap:6px;margin-left:auto;padding-bottom:6px}
  .cat-filter-select{height:27px;max-width:172px;padding:0 25px 0 8px;border:1px solid #d6dbe2;
    border-radius:4px;background:#fff;color:#3f4855;font-size:10.5px}
  #catCats{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px}
  .catchip{padding:4px 9px;border:1px solid var(--line);border-radius:16px;background:#fff;
    cursor:pointer;font-size:12px}
  .catchip.on{background:var(--accent);border-color:var(--accent);color:#fff}
  .catchip:focus-visible,.cat-scope:focus-visible,.cat-filter-select:focus-visible,
    #catClose:focus-visible,#catInspectorClose:focus-visible,
    #catInspectorActions button:focus-visible{outline:2px solid var(--accent);outline-offset:1px}
  /* Каталог всегда использует всю свободную ширину, но не превращается в
     бесконечную ленту мелких карточек: пять колонок на широком desktop,
     четыре — на обычном рабочем экране. */
  #catGrid{display:grid;flex:1;min-height:0;align-content:start;
    grid-template-columns:repeat(5,minmax(0,1fr));
    grid-auto-rows:max-content;gap:12px;
    overflow-y:auto;padding:1px 14px 20px 1px;scrollbar-gutter:stable}
  #catalogPreviewRenderer{position:fixed;left:-10000px;top:0;width:420px;height:315px;
    overflow:hidden;opacity:0;pointer-events:none;z-index:-1}
  .catCard{display:block;width:100%;padding:0;text-align:left;color:var(--ink);
    background:var(--card);border:1px solid var(--line);border-radius:6px;
    cursor:pointer;overflow:hidden;transition:border-color .12s ease,box-shadow .12s ease}
  .catCard:hover{border-color:#b8c1cd;box-shadow:0 2px 7px rgba(29,39,52,.09)}
  .catCard[aria-selected="true"]{border-color:var(--accent);box-shadow:0 0 0 1px var(--accent)}
  .catCard:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
  .catCard .img{height:130px;background:#eef1f5;
    display:flex;align-items:center;justify-content:center;color:var(--mut);font-size:30px}
  .catCard .img img{width:100%;height:100%;object-fit:contain;padding:6px;box-sizing:border-box}
  .catCard .draft-placeholder{display:flex;flex-direction:column;align-items:center;gap:7px;
    color:#687180;font-size:11px;font-weight:600}
  .catCard .draft-placeholder svg{width:28px;height:28px;fill:none;stroke:#7e8896;
    stroke-width:1.5;stroke-linecap:round;stroke-linejoin:round}
  .catCard .nm{display:block;padding:7px 10px 2px;font-weight:600;font-size:12.5px;
    white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .catCard .sub{display:block;padding:0 10px 6px;font-size:11px;color:var(--mut);
    white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .cat-card-owner{display:flex;align-items:center;gap:5px;min-width:0;margin:0 10px 8px;
    padding-top:6px;border-top:1px solid #e8ebef;color:#5f6977;font-size:10.5px}
  .cat-card-owner svg{width:13px;height:13px;flex:0 0 13px;fill:none;stroke:#6f7986;
    stroke-width:1.6;stroke-linecap:round;stroke-linejoin:round}
  .cat-card-owner span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .cat-card-owner.is-empty{color:#a06816}.cat-card-owner.is-empty svg{stroke:#a06816}
  .catCard .dr{display:inline-block;background:#c78a2b;color:#fff;border-radius:8px;
    padding:0 6px;font-size:10px;margin-left:4px}
  .catEmpty{padding:24px 4px;color:var(--mut);font-size:12px}
  #catInspector{display:flex;flex-direction:column;min-width:0;min-height:0;margin-left:14px;
    border-left:1px solid var(--line);background:#fff}
  #catInspectorEmpty{display:none;flex:1;align-items:center;justify-content:center;padding:28px;
    text-align:center;color:#687180}
  #catInspectorEmptyInner{max-width:210px}
  #catInspectorEmptyKicker{display:block;margin-bottom:8px;color:#778190;font-size:10px;
    font-weight:600;letter-spacing:.05em;text-transform:uppercase}
  #catInspectorEmpty h3{margin:0;color:#35404d;font-size:14px;line-height:20px}
  #catInspectorEmpty p{margin:7px 0 0;font-size:11.5px;line-height:17px}
  #catInspector.is-empty #catInspectorEmpty{display:flex}
  #catInspector.is-empty>#catInspectorHead,#catInspector.is-empty>#catInspectPreview,
  #catInspector.is-empty>#catInspectorBody,#catInspector.is-empty>#catInspectorActions{display:none}
  #catInspectorHead{display:flex;align-items:flex-start;gap:10px;padding:13px 14px 10px;
    border-bottom:1px solid var(--line)}
  #catInspectorHeadCopy{min-width:0;flex:1}
  #catInspectorKicker{display:block;margin-bottom:3px;color:#6e7784;font-size:10px;
    line-height:13px;font-weight:600;letter-spacing:.04em;text-transform:uppercase}
  #catInspectName{display:-webkit-box;margin:0;font-size:14px;line-height:19px;
    overflow:hidden;overflow-wrap:anywhere;-webkit-box-orient:vertical;-webkit-line-clamp:3}
  #catInspectorClose{display:flex;width:28px;height:28px;flex:0 0 28px;align-items:center;
    justify-content:center;padding:0;border-color:transparent;background:transparent}
  #catInspectorClose:hover{background:#eef1f4}
  #catInspectorClose svg{width:15px;height:15px;fill:none;stroke:currentColor;
    stroke-width:1.8;stroke-linecap:round}
  #catInspectPreview{height:180px;flex:0 0 180px;margin:12px 14px 0;border:1px solid #dfe3e8;
    border-radius:4px;background:#eef1f5;display:flex;align-items:center;justify-content:center;
    color:#687180;font-size:11px;font-weight:600;overflow:hidden}
  #catInspectPreview img{width:100%;height:100%;object-fit:contain;padding:8px;box-sizing:border-box}
  #catInspectPreview svg{width:34px;height:34px;fill:none;stroke:#7e8896;stroke-width:1.4}
  #catInspectorBody{flex:1;min-height:0;overflow:auto;padding:12px 14px}
  .cat-inspector-status{display:inline-flex;align-items:center;gap:6px;margin-bottom:12px;
    color:#485260;font-size:11px;line-height:15px;font-weight:600}
  .cat-inspector-status::before{content:"";width:6px;height:6px;border-radius:50%;background:var(--ok)}
  .cat-inspector-status.is-draft::before{background:#c78a2b}
  .cat-inspector-status.is-archived{color:#75511c}
  .cat-inspector-status.is-archived::before{background:#b8781d}
  .cat-inspector-group{margin:0;padding:0}
  .cat-inspector-group+.cat-inspector-group{margin-top:13px;padding-top:11px;
    border-top:1px solid #e7eaee}
  .cat-inspector-group h4{margin:0 0 7px;color:#687180;font-size:10px;line-height:13px;
    font-weight:600;text-transform:uppercase;letter-spacing:.04em}
  .cat-inspector-group dl{margin:0}
  .cat-inspector-row{display:grid;grid-template-columns:92px minmax(0,1fr);gap:8px;
    padding:3px 0;font-size:11.5px;line-height:16px}
  .cat-inspector-row dt{color:#737c89}
  .cat-inspector-row dd{margin:0;color:#303844;font-weight:500;overflow-wrap:anywhere;
    font-variant-numeric:tabular-nums}
  #catInspectResponsibleSelect{height:28px;min-width:0;padding:0 24px 0 7px;
    border:1px solid #ccd3dc;border-radius:4px;background:#fff;color:#303844;font-size:10.5px}
  #catInspectResponsibleSelect:focus-visible{outline:2px solid var(--accent);outline-offset:1px}
  .cat-inspector-note{margin:8px 0 0;color:#747d89;font-size:10.5px;line-height:15px}
  #catInspectorActions{display:grid;grid-template-columns:1fr 1fr;gap:6px;padding:10px 14px 14px;
    border-top:1px solid var(--line);background:#fff}
  #catOpen,#catRestore{grid-column:1/-1}
  #catArchive{grid-column:1/-1;border-color:#e1b7b9;background:#fff7f7;color:#9d3034}
  #catArchive:hover{border-color:#d49497;background:#fff0f0;color:#88252a}
  #catRestore{border-color:#9db9e6;background:#eef4ff;color:#245eae;font-weight:600}
  @media (max-width:1200px){
    #catGrid{grid-template-columns:repeat(4,minmax(0,1fr))}
    #catInspectPreview{height:150px;flex-basis:150px}
    #catFilterbar{align-items:flex-start;flex-direction:column;gap:4px;padding-bottom:7px}
    #catScopes{height:34px}
    #catFilterFields{margin-left:0;padding-bottom:0}
  }
  /* AKD-214: пустое рабочее пространство нового изделия */
  #emptyState{position:absolute;inset:0;z-index:6;display:none;align-items:center;justify-content:center;background:var(--bg)}
  #emptyState.on{display:flex}
  #emptyState .es-box{text-align:center;border:2px dashed var(--line);border-radius:14px;
    padding:36px 48px;background:var(--card)}
  #emptyState .es-title{font-size:20px;color:var(--ink);margin-bottom:8px}
  #emptyState .es-hint{font-size:13px;color:var(--mut);margin-bottom:16px;line-height:1.7}
  #emptyState.drop .es-box{border-color:var(--accent);background:#eef4ff}
  #view3d{position:absolute;inset:0}
  /* MEB-098: единый пульт модели. Режим, ракурс и отображение находятся в
     одной верхней рабочей зоне, а не отдельной плавающей карточке. */
  #hud{color:#303743;font-size:11px}
  .hud-section{min-width:0;padding:0 5px}
  .hud-section-head{position:absolute;width:1px;height:1px;margin:-1px;overflow:hidden;
    clip:rect(0,0,0,0);white-space:nowrap}
  .hud-layer-grid{display:flex;align-items:center;gap:0 7px}
  #hud .hud-layer{display:flex;align-items:center;gap:4px;min-width:0;height:28px;
    cursor:pointer;user-select:none;white-space:nowrap}
  #hud .hud-layer input{width:14px;height:14px;margin:0;accent-color:var(--accent)}
  #hud .hud-layer span{min-width:0;overflow:hidden;text-overflow:ellipsis}
  #hud .hud-model{gap:5px}
  .hud-actions{display:flex;align-items:center;gap:5px}
  #hud .hud-actions button{display:flex;align-items:center;justify-content:center;gap:4px;
    min-width:0;height:28px;padding:0 7px;border-radius:3px;font-size:10.5px;white-space:nowrap}
  .explode-control{display:grid;grid-template-columns:auto minmax(0,1fr) auto;
    flex:1 1 auto;align-items:center;gap:6px;height:29px;margin:0;color:#4f5967}
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
    #hud .hud-layer span{position:absolute;width:1px;height:1px;margin:-1px;overflow:hidden;
      clip:rect(0,0,0,0);white-space:nowrap}
    #viewportTopbar{gap:5px}
  }
  @container (max-width:700px){
    #viewportTopbar{gap:6px}
    #tabs button{padding:0 6px}
    #tabs .viewport-icon{display:none}
    #views .vw{padding:0 5px;font-size:10.5px}
    #hud .hud-actions button span{position:absolute;width:1px;height:1px;margin:-1px;
      overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap}
  }
  @container (max-width:440px){
    #views .vw{padding:0 4px;font-size:10px}
    #hud .hud-section{padding:0 3px}
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
  #viewportStatus[data-tone="changed"] #viewportModelMark,
    #viewportStatus[data-tone="recalculating"] #viewportModelMark{background:var(--accent)}
  #viewportStatus[data-tone="decision"] #viewportModelMark{background:#c78a2b}
  #viewportStatus[data-tone="blocked"] #viewportModelMark,
    #viewportStatus[data-tone="stale"] #viewportModelMark{background:var(--bad)}
  #viewportModelStateShort{display:none}
  #viewportSaveStatus{flex:0 0 auto;gap:5px;color:#697381;font-weight:500}
  #viewportSaveMark{width:5px;height:5px;flex:0 0 5px;border-radius:50%;background:var(--ok)}
  #viewportStatus[data-save="changed"] #viewportSaveStatus{color:#365f9b}
  #viewportStatus[data-save="changed"] #viewportSaveMark{background:var(--accent)}
  #viewportSaveStateShort{display:none}
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
    #viewportSaveStateLong{display:none}
    #viewportSaveStateShort{display:inline}
    #viewportSelection{max-width:42%}
    .viewport-status-segment{padding-inline:7px}
  }
  @container (max-width:700px){
    #viewportHint{display:none}
    #viewportSelection{flex:1 1 auto;max-width:none}
    #viewportStatus[data-tone="blocked"] #viewportSelection,
      #viewportStatus[data-tone="stale"] #viewportSelection,
      #viewportStatus[data-tone="recalculating"] #viewportSelection{display:none}
  }
  .dependent-data-state{display:none;margin:0 0 9px;padding:7px 8px;border-left:2px solid #c78a2b;
    background:#fff8ec;color:#62563f;font-size:10.5px;line-height:15px}
  .dependent-data-state.is-visible{display:block}
  .dependent-data-state[data-tone="blocked"],.dependent-data-state[data-tone="stale"]{
    border-left-color:var(--bad);background:#fff3f2;color:#743d3d}
  #toast{position:absolute;left:50%;bottom:calc(44px + var(--chat-stack-height));
         transform:translateX(-50%);z-index:9;width:min(440px,calc(100% - 32px));
         color:#fff;font-size:12.5px;opacity:0;transition:opacity .2s;pointer-events:none}
  #toast.is-visible{opacity:1;pointer-events:auto}
  #toast.has-history:not(.is-visible){width:auto;opacity:1;pointer-events:auto}
  #toast.has-history:not(.is-visible) #toastCurrent{min-height:0;padding:0;background:transparent;box-shadow:none}
  #toast.has-history:not(.is-visible) #toastMessage{display:none}
  #toast.has-history.is-history-open{width:min(440px,calc(100% - 32px))}
  #toastCurrent{display:flex;align-items:center;gap:8px;min-height:32px;padding:7px 8px 7px 12px;
         border-radius:8px;background:#1a1d21;box-shadow:0 8px 22px rgba(22,29,38,.18)}
  #toast[data-tone="error"] #toastCurrent{background:#9f2924}
  #toastMessage{min-width:0;flex:1 1 auto;line-height:17px}
  #toastHistoryToggle{flex:0 0 auto;min-height:24px;padding:0 7px;border:1px solid rgba(255,255,255,.32);
         border-radius:4px;background:rgba(255,255,255,.08);color:inherit;font-size:10.5px;font-weight:650}
  #toastHistoryToggle:hover{background:rgba(255,255,255,.16)}
  #toast.has-history:not(.is-visible) #toastHistoryToggle{border-color:#d0d6df;background:#fff;color:#586574;
         box-shadow:0 4px 14px rgba(22,29,38,.12)}
  #toast.has-history:not(.is-visible) #toastHistoryToggle:hover{background:#f5f7fa}
  #toastHistory{position:absolute;left:0;right:0;bottom:calc(100% + 6px);max-height:154px;margin:0;
         padding:5px;overflow:auto;border:1px solid #ccd2da;border-radius:7px;background:#fff;color:#35404d;
         box-shadow:0 10px 26px rgba(22,29,38,.18);list-style:none}
  #toastHistory li{padding:6px 7px;border-radius:4px;font-size:11px;line-height:15px}
  #toastHistory li+li{border-top:1px solid #e7eaf0}
  #toastHistory li[data-tone="error"]{color:#8b302c;background:#fff7f6}
  @media (prefers-reduced-motion:reduce){#toast{transition:none}}
  #shareDialog,#catArchiveDialog{width:min(470px,calc(100vw - 32px));padding:0;border:1px solid #cfd5dd;
    border-radius:8px;background:#fff;color:var(--ink);box-shadow:0 24px 70px rgba(20,31,44,.24)}
  #shareDialog::backdrop,#catArchiveDialog::backdrop{background:rgba(30,39,50,.42)}
  #catArchiveForm{margin:0}
  .share-dialog-head{padding:17px 18px 12px;border-bottom:1px solid #e2e6ea}
  .share-dialog-head span{display:block;margin-bottom:3px;color:#647080;font-size:9.5px;
    font-weight:700;letter-spacing:.06em;text-transform:uppercase}
  .share-dialog-head h2{margin:0;font-size:16px;line-height:21px}
  .share-dialog-body{padding:14px 18px 18px}.share-dialog-body p{margin:0 0 11px;color:#65707e;
    font-size:11.5px;line-height:17px}.share-link-row{display:grid;grid-template-columns:minmax(0,1fr) auto;
    gap:6px}.share-link-row input{min-width:0;height:34px;padding:0 9px;border:1px solid #ccd2da;
    border-radius:4px;background:#f7f8fa;color:#38424f;font:10.5px/1 ui-monospace,SFMono-Regular,
    Consolas,monospace}.share-link-row button{height:34px}.share-dialog-meta{margin-top:8px;color:#77818e;
    font-size:10px}.share-dialog-actions{display:flex;justify-content:flex-end;gap:6px;margin-top:15px}
  .share-dialog-actions a{display:flex;align-items:center;justify-content:center;min-height:32px;
    padding:0 12px;border:1px solid var(--accent);border-radius:5px;background:var(--accent);
    color:#fff;text-decoration:none;font-size:11.5px;font-weight:600}
  .share-config-group{margin:0 0 14px;padding:0;border:0}.share-config-group legend{margin-bottom:7px;
    color:#586473;font-size:10.5px;font-weight:700}.share-mode-grid{display:grid;grid-template-columns:1fr 1fr;gap:7px}
  .share-mode-option{display:grid;grid-template-columns:16px minmax(0,1fr);gap:6px;padding:10px;
    border:1px solid #d4d9e0;border-radius:5px;background:#fff;cursor:pointer}
  .share-mode-option:has(input:checked){border-color:#7ca2df;background:#f4f8ff}
  .share-mode-option input{margin:2px 0 0;accent-color:var(--accent)}.share-mode-option strong{display:block;
    margin-bottom:2px;color:#313b48;font-size:11.5px}.share-mode-option span{display:block;color:#6e7885;font-size:10px;line-height:14px}
  .share-expiry{display:grid;grid-template-columns:92px minmax(0,1fr);align-items:center;gap:10px;
    color:#586473;font-size:10.5px;font-weight:700}.share-expiry select{height:32px;padding:0 8px;border:1px solid #ccd2da;
    border-radius:4px;background:#fff;color:#35404c;font-size:11px}
  .share-config-note{margin:10px 0 0!important;padding:8px 10px;border-left:2px solid #7fa3db;
    background:#f5f8fc;color:#536171!important}.share-dialog-actions .primary{border-color:var(--accent);
    background:var(--accent);color:#fff;font-weight:600}.share-dialog-actions .primary:hover{background:#245ebf}
  [hidden]{display:none!important}
  .archive-dialog-target{padding:8px 10px;border-left:2px solid #c9862b;background:#fff9ef;
    color:#3f4855;font-size:11.5px;font-weight:600;overflow-wrap:anywhere}
  .archive-dialog-field{display:grid;gap:5px;margin-top:12px;color:#5d6876;font-size:10.5px}
  .archive-dialog-field textarea{height:76px;resize:vertical;padding:8px 9px;border:1px solid #ccd3dc;
    border-radius:4px;background:#fff;color:#303844;font:11.5px/1.45 "Segoe UI",Arial,sans-serif}
  .archive-dialog-field textarea:focus-visible{outline:2px solid var(--accent);outline-offset:1px}
  #catArchiveError{min-height:16px;margin:7px 0 0;color:#a12e33;font-size:10.5px;line-height:15px}
  #catArchiveConfirm{border-color:#a6383d;background:#a6383d;color:#fff;font-weight:600}
  #catArchiveConfirm:hover{border-color:#8f292e;background:#8f292e}
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
    <button id="studioBrandHome" class="studio-brand-home" type="button"
      aria-label="Обновить Akeda Studio" title="Обновить страницу">
      <img id="studioBrandWordmark" class="studio-brand-wordmark"
        src="/assets/studio/akeda-studio-wordmark.png" width="520" height="84"
        decoding="async" alt="Akeda Studio">
      <img id="studioBrandMark" class="studio-brand-mark"
        src="/assets/studio/akeda-studio-mark.png" width="216" height="98"
        decoding="async" alt="" aria-hidden="true">
    </button>
  </h1>
  <div id="profileShell" class="profile-shell" hidden>
    <button id="profileChip" type="button" aria-expanded="false" aria-controls="profileMenu">
      <span id="profileInitials" class="profile-avatar" aria-hidden="true"></span>
      <span class="profile-copy"><strong id="profileName"></strong><span id="profileCompany"></span></span>
      <span class="profile-chevron" aria-hidden="true">⌄</span>
    </button>
    <div id="profileMenu" hidden>
      <div id="profileMenuName" class="profile-menu-name"></div>
      <div id="profileEmail" class="profile-menu-email"></div>
      <span id="profileRole" class="profile-menu-role"></span>
      <a id="profileCompanyAdmin" href="/admin" hidden>Управление компанией</a>
      <button id="profileLogout" type="button">Выйти из аккаунта</button>
    </div>
  </div>

  <section id="catalogContext" aria-labelledby="catalogContextTitle" hidden>
    <span class="catalog-context-kicker">Рабочий раздел</span>
    <strong id="catalogContextTitle">Каталог компании</strong>
    <p id="catalogContextHint">Выберите изделие, чтобы открыть его в Studio.</p>
    <span id="catalogContextCount" aria-live="polite">Загрузка изделий…</span>
  </section>
  <div id="catalogInspectorSlot"></div>

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
    <button id="projShare" class="project-share" type="button"
      title="Зафиксировать текущую версию и создать ссылку только для просмотра">
      <svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true">
        <path d="M10 13a5 5 0 0 0 7.1.1l2-2a5 5 0 0 0-7.1-7.1l-1.1 1.1"/>
        <path d="M14 11a5 5 0 0 0-7.1-.1l-2 2A5 5 0 0 0 12 20l1.1-1.1"/>
      </svg>
      Создать ссылку на просмотр
    </button>
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
      <div class="part-utility-actions">
        <button id="ovDetail" type="button" title="Чертёж детали с размерами и присадками">Чертёж</button>
      </div>
      <p class="part-context-note">Нижняя команда уже адресована выбранной детали — опишите правку там.</p>
      <details id="partExact" class="part-exact" open>
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
  <nav id="rightPanelTabs" role="tablist" aria-label="Разделы правой панели">
    <button id="rightTabProperties" type="button" role="tab" aria-selected="true"
      aria-controls="rightViewProperties" data-mode="properties">Параметры</button>
    <button id="rightTabComponents" type="button" role="tab" aria-selected="false"
      aria-controls="rightViewComponents" data-mode="components" tabindex="-1">Комплектация</button>
    <button id="rightTabProduction" type="button" role="tab" aria-selected="false"
      aria-controls="rightViewProduction" data-mode="production" tabindex="-1">Производство</button>
    <button id="rightTabReviews" type="button" role="tab" aria-selected="false"
      aria-controls="rightViewReviews" data-mode="reviews" tabindex="-1"
      aria-label="Ссылки и согласования">Ссылки</button>
  </nav>
  <button id="rightPanelClose" type="button" aria-label="Свернуть правую панель"
    aria-controls="rightside" aria-expanded="true" title="Свернуть панель">
    <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m15 6-6 6 6 6"/></svg>
  </button>

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
      <p id="sectionsState" class="section-inspector-state" aria-live="polite" hidden></p>
      <button id="addSec">+ секция</button>
    </fieldset>

    <details id="fs_raw"><summary class="mini">ParamSpec (raw JSON)</summary>
      <textarea id="rawspec" spellcheck="false"></textarea>
      <button id="applyRaw">Применить JSON</button>
    </details>
  </div>

  <div id="rightViewComponents" class="right-panel-view" role="tabpanel"
    aria-labelledby="rightTabComponents" hidden inert>
    <div id="componentsStateNotice" class="dependent-data-state"></div>
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
    <div id="productionStateNotice" class="dependent-data-state"></div>
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
        <button id="verRestore" type="button" aria-label="Восстановить выбранную версию"
          title="Восстановить выбранную версию">
          <svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true">
            <path d="M8 7 4.5 10.5 8 14"/><path d="M5 10.5h8a6.5 6.5 0 1 1-5.1 10.5"/>
          </svg>
        </button>
      </div>
      <div id="builds" style="margin-top:6px"></div>
    </fieldset>
  </div>

  <div id="rightViewReviews" class="right-panel-view" role="tabpanel"
    aria-labelledby="rightTabReviews" hidden inert>
    <div class="review-inbox-head"><p>Ссылки на просмотр, их срок и решения клиентов по каждой версии изделия.</p>
      <button id="reviewInboxRefresh" type="button">Обновить</button></div>
    <div id="reviewInbox" aria-live="polite"><div class="review-inbox-empty">Загружаем согласования…</div></div>
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
  <button id="rightRailReviews" type="button" aria-label="Открыть согласования"
    aria-controls="rightside" aria-expanded="false" title="Согласования">
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M4 5h16v11H9l-5 4V5Z"/><path d="m8 10 2.5 2.5L16 8"/>
    </svg>
    <span class="sr-only">Согласования</span>
  </button>
</nav>

<div id="main">
  <div id="supportBanner" role="status" hidden>
    <span>Вы просматриваете компанию <strong id="supportCompanyName"></strong> от имени Akeda</span>
    <button id="supportBack" type="button">Вернуться в админку</button>
  </div>
  <div id="view3d"></div>
  <div id="catalogPreviewRenderer" aria-hidden="true"></div>
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
    <div id="viewportTools">
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
      <div id="hud" role="toolbar" aria-label="Отображение и состояние 3D-модели">
        <section class="hud-section hud-layers" aria-labelledby="hudLayersTitle">
      <div class="hud-section-head">
        <span id="hudLayersTitle">Отображение</span>
      </div>
      <div class="hud-layer-grid">
        <label class="hud-layer"><input type="checkbox" id="cbHoles" checked><span>Присадки</span></label>
        <label class="hud-layer"><input type="checkbox" id="cbHw" checked><span>Фурнитура</span></label>
        <label class="hud-layer"><input type="checkbox" id="cbTex" checked><span>Текстура</span></label>
        <label class="hud-layer"><input type="checkbox" id="cbDims" checked><span>Размеры</span></label>
        <label class="hud-layer is-wide"><input type="checkbox" id="cbXray"><span>Прозрачность</span></label>
      </div>
    </section>
        </section>
        <section class="hud-section hud-model" aria-labelledby="hudModelTitle">
          <div class="hud-section-head"><span id="hudModelTitle">Модель</span></div>
          <div class="hud-actions" role="group" aria-label="Открытие фасадов и ящиков">
            <button id="btnToggleOpenAll" type="button" aria-pressed="false"
              title="Открыть все фасады и ящики">
          <svg class="viewport-icon" viewBox="0 0 24 24" aria-hidden="true">
            <path d="M4 4h11v16H4V4Z"/><path d="m15 4 5 3v13l-5-2V4ZM17 11h.01"/>
          </svg>
              <span id="btnToggleOpenAllText">Открыть всё</span>
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
    </div>
  </div>
  <div id="draw"></div>
  <div id="catalog" role="region" aria-labelledby="catalogTitle" aria-hidden="true">
    <div id="catHead">
      <b id="catalogTitle" style="font-size:16px">Каталог изделий</b>
      <label class="sr-only" for="catQ">Поиск изделий по названию</label>
      <input type="text" id="catQ" placeholder="Поиск по названию…">
      <button id="catClose" type="button" aria-label="Закрыть каталог">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m7 7 10 10M17 7 7 17"/></svg>
        Закрыть
      </button>
    </div>
    <div id="catWorkspace">
      <div id="catBrowser">
        <div id="catFilterbar">
          <div id="catScopes" role="group" aria-label="Область каталога"></div>
          <div id="catFilterFields">
            <label class="sr-only" for="catResponsible">Ответственный</label>
            <select id="catResponsible" class="cat-filter-select" aria-label="Ответственный">
              <option value="all">Все ответственные</option>
            </select>
            <label class="sr-only" for="catStatus">Статус изделия</label>
            <select id="catStatus" class="cat-filter-select" aria-label="Статус изделия">
              <option value="all">Все статусы</option>
              <option value="active">Рабочие изделия</option>
              <option value="draft">Черновики</option>
            </select>
          </div>
        </div>
        <div id="catCats" role="group" aria-label="Категории изделий"></div>
        <div id="catGrid" role="listbox" aria-label="Изделия каталога"></div>
      </div>
      <aside id="catInspector" class="is-empty" aria-label="Настройки изделия">
        <div id="catInspectorEmpty">
          <div id="catInspectorEmptyInner">
            <span id="catInspectorEmptyKicker">Каталог компании</span>
            <h3>Выберите изделие</h3>
            <p>Нажмите на карточку — здесь появятся параметры, ответственный и действия с изделием.</p>
          </div>
        </div>
        <div id="catInspectorHead">
          <div id="catInspectorHeadCopy">
            <span id="catInspectorKicker">Выбрано в каталоге</span>
            <h3 id="catInspectName"></h3>
          </div>
          <button id="catInspectorClose" type="button" aria-label="Снять выбор">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m7 7 10 10M17 7 7 17"/></svg>
          </button>
        </div>
        <div id="catInspectPreview" aria-hidden="true"></div>
        <div id="catInspectorBody">
          <div id="catInspectStatus" class="cat-inspector-status"></div>
          <section class="cat-inspector-group">
            <h4>Изделие</h4>
            <dl>
              <div class="cat-inspector-row"><dt>Тип</dt><dd id="catInspectType"></dd></div>
              <div class="cat-inspector-row"><dt>Габариты</dt><dd id="catInspectDims"></dd></div>
              <div class="cat-inspector-row"><dt>Материал</dt><dd id="catInspectDecor"></dd></div>
              <div class="cat-inspector-row"><dt>Изменено</dt><dd id="catInspectUpdated"></dd></div>
            </dl>
          </section>
          <section class="cat-inspector-group">
            <h4>Работа</h4>
            <dl>
              <div class="cat-inspector-row"><dt>Ответственный</dt><dd>
                <span id="catInspectResponsible"></span>
                <select id="catInspectResponsibleSelect" aria-label="Изменить ответственного" hidden></select>
              </dd></div>
              <div class="cat-inspector-row"><dt>Автор</dt><dd id="catInspectAuthor"></dd></div>
            </dl>
          </section>
          <section id="catArchiveMeta" class="cat-inspector-group" hidden>
            <h4>Архив</h4>
            <dl>
              <div class="cat-inspector-row"><dt>Перемещено</dt><dd id="catInspectArchivedAt"></dd></div>
              <div class="cat-inspector-row"><dt>Кем</dt><dd id="catInspectArchivedBy"></dd></div>
              <div class="cat-inspector-row"><dt>Причина</dt><dd id="catInspectArchiveReason"></dd></div>
            </dl>
          </section>
          <p id="catInspectorPeopleNote" class="cat-inspector-note" hidden>
            Ответственный и автор появятся после подключения сотрудников к изделиям.
          </p>
        </div>
        <div id="catInspectorActions">
          <button id="catOpen" class="primary" type="button">Открыть в Studio</button>
          <button id="catRename" type="button">Переименовать</button>
          <button id="catDuplicate" type="button">Дублировать</button>
          <button id="catArchive" type="button">Переместить в архив…</button>
          <button id="catRestore" type="button" hidden>Восстановить в каталог</button>
        </div>
      </aside>
    </div>
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
    <div id="viewportSaveStatus" class="viewport-status-segment">
      <span id="viewportSaveMark" aria-hidden="true"></span>
      <span id="viewportSaveStateLong">Сохранено</span>
      <span id="viewportSaveStateShort">Сохранено</span>
    </div>
    <div id="viewportSelection" class="viewport-status-segment" hidden>
      <span>Выбрано</span><b id="viewportSelectionName"></b>
    </div>
    <div id="viewportHint" class="viewport-status-segment">
      Клик — выбрать · перетаскивание — вращать · колесо — масштаб
    </div>
    <div id="viewportUnits" class="viewport-status-segment">мм</div>
  </div>
  <dialog id="shareDialog" aria-labelledby="shareDialogTitle">
    <div class="share-dialog-head"><span id="shareEyebrow">Просмотр для клиента</span>
      <h2 id="shareDialogTitle">Создать ссылку</h2></div>
    <div class="share-dialog-body">
      <div id="shareSetup">
        <fieldset class="share-config-group"><legend>Как ссылка будет обновляться</legend>
          <div class="share-mode-grid">
            <label class="share-mode-option"><input type="radio" name="shareMode" value="snapshot" checked>
              <span><strong>Фиксированная</strong>Покажет состояние изделия в момент создания.</span></label>
            <label class="share-mode-option"><input type="radio" name="shareMode" value="live">
              <span><strong>Обновляемая</strong>Всегда откроет последнюю сохранённую версию.</span></label>
          </div>
        </fieldset>
        <label class="share-expiry">Срок доступа
          <select id="shareExpiry"><option value="30" selected>30 дней</option><option value="7">7 дней</option>
            <option value="90">90 дней</option><option value="">Бессрочно</option></select>
        </label>
        <p id="shareModeNote" class="share-config-note">Будущие правки не изменят то, что увидит клиент.</p>
        <div class="share-dialog-actions"><button id="shareCancel" type="button">Отмена</button>
          <button id="shareCreate" class="primary" type="button">Создать ссылку</button></div>
      </div>
      <div id="shareResult" hidden>
        <p id="shareResultCopy"></p>
        <div class="share-link-row"><input id="shareUrl" readonly aria-label="Ссылка на просмотр">
          <button id="shareCopy" type="button">Копировать</button></div>
        <div id="shareMeta" class="share-dialog-meta"></div>
        <div class="share-dialog-actions"><button id="shareClose" type="button">Закрыть</button>
          <a id="shareOpen" href="#" target="_blank" rel="noopener">Открыть просмотр</a></div>
      </div>
    </div>
  </dialog>
  <dialog id="catArchiveDialog" aria-labelledby="catArchiveDialogTitle">
    <form id="catArchiveForm" method="dialog">
      <div class="share-dialog-head"><span>Обратимое действие</span>
        <h2 id="catArchiveDialogTitle">Переместить изделие в архив?</h2></div>
      <div class="share-dialog-body">
        <p>Изделие исчезнет из рабочего каталога, но модель, версии, AI-история и производственные файлы сохранятся.</p>
        <div id="catArchiveTarget" class="archive-dialog-target"></div>
        <label class="archive-dialog-field" for="catArchiveReason">Причина
          <textarea id="catArchiveReason" maxlength="240" required
            placeholder="Например: дубль или заказ отменён"></textarea>
        </label>
        <p id="catArchiveError" role="alert"></p>
        <div class="share-dialog-actions">
          <button id="catArchiveCancel" type="button">Отмена</button>
          <button id="catArchiveConfirm" type="submit">Переместить в архив</button>
        </div>
      </div>
    </form>
  </dialog>
  <div id="toast">
    <div id="toastCurrent" role="status" aria-live="polite" aria-atomic="true">
      <span id="toastMessage"></span>
      <button id="toastHistoryToggle" type="button" aria-expanded="false" aria-controls="toastHistory" hidden>Уведомления</button>
    </div>
    <ol id="toastHistory" hidden aria-label="Последние уведомления"></ol>
  </div>
</div>
</div>

<script src="/vendor/three.min.js"></script>
<script src="/vendor/OrbitControls.js"></script>
<script>
__SCENE_JS__
const AUTH_CONTEXT = __AUTH__;
const ADMIN_URL = __ADMIN_URL__;
const nativeFetch = window.fetch.bind(window);
function studioCookie(name){
  const prefix=name+'=';
  return document.cookie.split(';').map(item=>item.trim()).find(item=>item.startsWith(prefix))
    ?.slice(prefix.length)||'';
}
window.fetch = (input, init={}) => {
  const requestMethod=(init.method||(input instanceof Request?input.method:'GET')).toUpperCase();
  const headers=new Headers(init.headers||(input instanceof Request?input.headers:undefined));
  if(!['GET','HEAD','OPTIONS'].includes(requestMethod)){
    const csrf=studioCookie('akeda_csrf');
    if(csrf&&!headers.has('X-CSRF-Token'))headers.set('X-CSRF-Token',csrf);
  }
  return nativeFetch(input,{...init,headers,credentials:init.credentials||'same-origin'}).then(response=>{
    const url=typeof input==='string'?input:(input&&input.url)||'';
    if(response.status===401&&!url.includes('/api/auth/login'))location.href='/login';
    return response;
  });
};
let SPEC = __SPEC__;
const SPEC_WARNINGS = __SPEC_WARNINGS__;   // что в файле не по контракту (показываем при старте)
const FIELDS = __FIELDS__;                 // archetype -> [{key,label,type,...}]
const SECTION_ARCHS = __SECTION_ARCHS__;   // архетипы с секциями
const $ = id => document.getElementById(id);
$('studioBrandHome').onclick=()=>location.reload();
/* В режиме каталога инспектор принадлежит общей навигационной панели слева:
   она уже закреплена и не оставляет отдельную пустую правую колонку. */
$('catalogInspectorSlot').append($('catInspector'));
/* Значимые системные сообщения не должны перетирать друг друга. Текущее
   показываем по одному, а последние остаются доступными в компактной истории. */
const NOTICE_HISTORY_LIMIT=8,NOTICE_QUEUE_LIMIT=6;
let activeNotice=null,noticeTimer=null;
const noticeQueue=[],noticeHistory=[];
function renderNoticeHistory(){
  const list=$('toastHistory'),toggle=$('toastHistoryToggle');
  list.replaceChildren();
  noticeHistory.slice().reverse().forEach(notice=>{
    const item=document.createElement('li');item.dataset.tone=notice.bad?'error':'info';
    item.textContent=notice.message;list.append(item);
  });
  const hasHistory=noticeHistory.length>0;
  const toastBox=$('toast');toastBox.classList.toggle('has-history',hasHistory);
  toggle.hidden=!hasHistory;
  toggle.textContent=`Уведомления · ${noticeHistory.length}`;
  if(!hasHistory){list.hidden=true;toggle.setAttribute('aria-expanded','false');}
}
function hideCurrentNotice({showNext=true}={}){
  clearTimeout(noticeTimer);noticeTimer=null;activeNotice=null;
  const toastBox=$('toast');toastBox.classList.remove('is-visible');
  if(showNext)showNextNotice();
}
function showNextNotice(){
  if(activeNotice||!noticeQueue.length)return;
  activeNotice=noticeQueue.shift();
  const toastBox=$('toast');toastBox.dataset.tone=activeNotice.bad?'error':'info';
  toastBox.classList.remove('is-history-open');
  $('toastMessage').textContent=activeNotice.message;
  toastBox.classList.add('is-visible');
  if(!activeNotice.sticky){
    noticeTimer=setTimeout(()=>hideCurrentNotice(),activeNotice.bad?5000:2800);
  }
}
function toast(message,bad=false,sticky=false){
  const text=String(message||'').trim();if(!text)return;
  const notice={message:text,bad:!!bad,sticky:!!sticky};
  noticeHistory.push(notice);if(noticeHistory.length>NOTICE_HISTORY_LIMIT)noticeHistory.shift();
  renderNoticeHistory();
  // Промежуточный статус (например, распознавание ТЗ) заменяется его итогом,
  // а не задерживает его в очереди.
  if(activeNotice&&activeNotice.sticky)hideCurrentNotice({showNext:false});
  if(noticeQueue.length>=NOTICE_QUEUE_LIMIT)noticeQueue.shift();
  noticeQueue.push(notice);showNextNotice();
}
$('toastHistoryToggle').onclick=()=>{
  const list=$('toastHistory'),opened=list.hidden,toastBox=$('toast');
  list.hidden=!opened;toastBox.classList.toggle('is-history-open',opened);
  $('toastHistoryToggle').setAttribute('aria-expanded',String(opened));
};

/* ---------- текущий сотрудник и компания ---------- */
function setupProfile(){
  if(!AUTH_CONTEXT||!AUTH_CONTEXT.user||!AUTH_CONTEXT.organization)return;
  const user=AUTH_CONTEXT.user,organization=AUTH_CONTEXT.organization,
    membership=AUTH_CONTEXT.membership||{};
  const displayName=user.display_name||user.email||'Сотрудник';
  const initials=displayName.split(/\s+/).filter(Boolean).slice(0,2).map(part=>part[0]).join('').toUpperCase();
  const roles={owner:'Владелец',admin:'Администратор',designer:'Конструктор',
    technologist:'Технолог',reviewer:'Наблюдатель'};
  $('profileInitials').textContent=initials||'А';$('profileName').textContent=displayName;
  $('profileCompany').textContent=organization.name||'Компания';
  $('profileMenuName').textContent=displayName;$('profileEmail').textContent=user.email||'';
  $('profileRole').textContent=roles[membership.role]||membership.role||'Сотрудник';
  if(membership.role==='owner'){
    $('profileCompanyAdmin').href=ADMIN_URL;
    $('profileCompanyAdmin').hidden=false;
    $('profileCompanyAdmin').onclick=async event=>{
      event.preventDefault();
      if(!await prepareWorkspaceChange('открыть управление компанией'))return;
      location.href=ADMIN_URL;
    };
  }
  if(AUTH_CONTEXT.viewing_as_akeda){
    $('profileRole').textContent='Просмотр от Akeda';
    $('supportCompanyName').textContent=organization.name||'компанию';
    $('supportBanner').hidden=false;$('app').classList.add('support-active');
    $('supportBack').onclick=async()=>{
      const button=$('supportBack');button.disabled=true;
      try{await fetch('/api/admin/company-view/end',{method:'POST',
        headers:{'Content-Type':'application/json'},body:'{}'});}finally{location.href='/admin';}
    };
  }
  $('profileShell').hidden=false;
  $('profileChip').onclick=()=>{const open=$('profileMenu').hidden;
    $('profileMenu').hidden=!open;$('profileChip').setAttribute('aria-expanded',String(open));};
  document.addEventListener('click',event=>{if(!$('profileShell').contains(event.target)){
    $('profileMenu').hidden=true;$('profileChip').setAttribute('aria-expanded','false');}});
  $('profileLogout').onclick=async()=>{if(!await prepareWorkspaceChange('выйти из аккаунта'))return;
    const button=$('profileLogout');button.disabled=true;
    try{await fetch('/api/auth/logout',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});}
    finally{location.href='/login';}};
}
setupProfile();

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
    rail:$('rightRailProduction'),panel:$('rightViewProduction'),scrollTop:0},
  reviews:{title:'Согласования',tab:$('rightTabReviews'),
    rail:$('rightRailReviews'),panel:$('rightViewReviews'),scrollTop:0}
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
    item.panel.hidden=!active;item.panel.inert=!active;
    item.tab.setAttribute('aria-selected',String(active));item.tab.tabIndex=active?0:-1;
    item.rail.classList.toggle('is-active',active);
  });
  const current=rightPanelModes[mode];
  $('rightPanelClose').setAttribute('aria-label',`Свернуть панель «${current.title}»`);
  lastRightRailControl=current.rail;
  if(mode==='reviews')loadReviews();
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
const wideStudioLayout=window.matchMedia('(min-width: 1180px)');
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

function syncCameraPreset(viewName){document.querySelectorAll('#views .vw').forEach(button=>{
  const active=button.dataset.view===viewName;
  button.classList.toggle('on',active);button.setAttribute('aria-pressed',String(active));
});}
function rebuild(v,options){const opts=options||{};scene3d.setPayload(v,opts);
  if(opts.resetView)syncCameraPreset(opts.view||'persp');
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
function syncOpenAllButton(hasOpen){
  const button=$('btnToggleOpenAll'),label=$('btnToggleOpenAllText');
  button.dataset.open=String(!!hasOpen);button.setAttribute('aria-pressed',String(!!hasOpen));
  button.title=hasOpen?'Закрыть все открытые фасады и ящики':'Открыть все фасады и ящики';
  label.textContent=hasOpen?'Закрыть всё':'Открыть всё';
}
scene3d.onOpenablesChange=state=>syncOpenAllButton(!!(state&&state.hasOpen));
$('explode').oninput=e=>{
  scene3d.setExplode(e.target.value/100);
  $('explodeValue').textContent=`${e.target.value}%`;
};
$('btnToggleOpenAll').onclick=()=>{
  if($('btnToggleOpenAll').dataset.open==='true')scene3d.closeAll();
  else scene3d.openAll();
};
// ракурсы: аксонометрия/перспектива/сверху/спереди/слева
document.querySelectorAll('#views .vw').forEach(b=>b.onclick=()=>{
  scene3d.setView(b.dataset.view);
  syncCameraPreset(b.dataset.view);
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
  if(typeof syncModelEditLock==='function')syncModelEditLock();
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
const SECTION_KIND_LABELS={drawers:'Ящики',shelves:'Полки',door:'Дверь',open:'Открытая'};
let sectionInspectorStateKnown=false;
const openSectionInspectors=new Set();
function sectionShareLabel(section){
  const share=Number(section.width_share);
  return Number.isFinite(share)&&share>0?`${Math.round(share*100)}% ширины`:'Ширина по расчёту';
}
function sectionInputRow(index,key,label,{type='number',min='',max='',step='',placeholder='',title='',value=''}={}){
  const attrs=[`type="${type}"`,`data-i="${index}"`,`data-k="${key}"`,`value="${value}"`];
  if(min!=='')attrs.push(`min="${min}"`);if(max!=='')attrs.push(`max="${max}"`);
  if(step!=='')attrs.push(`step="${step}"`);if(placeholder)attrs.push(`placeholder="${placeholder}"`);
  return `<div class="section-inspector-row"><label title="${title}">${label}</label><input ${attrs.join(' ')}></div>`;
}
function sectionContextFields(section,index){
  const kind=section.kind||'open',value=key=>section[key]??'';
  if(kind==='drawers')return [
    sectionInputRow(index,'drawers','Количество ящиков',{min:'0',value:value('drawers')}),
    sectionInputRow(index,'drawer_heights','Высоты ящиков',{type:'text',placeholder:'например: 180, 180, 240',
      title:'Высоты фасадов ящиков сверху вниз, через запятую',value:(section.drawer_heights||[]).join(', ')}),
    sectionInputRow(index,'front_bottom','Низ фасадной зоны',{title:'Y: ниша снизу до фасадной зоны',value:value('front_bottom')}),
    sectionInputRow(index,'front_top','Верх фасадной зоны',{title:'Y: ниша сверху от фасадной зоны',value:value('front_top')})
  ].join('');
  if(kind==='shelves')return [
    sectionInputRow(index,'shelves','Количество полок',{min:'0',value:value('shelves')}),
    sectionInputRow(index,'shelf_levels','Уровни полок',{type:'text',placeholder:'например: 400, 800, 1200',
      title:'Уровни полок по Y от пола, через запятую',value:(section.shelf_levels||[]).join(', ')})
  ].join('');
  if(kind==='door')return [
    sectionInputRow(index,'door','Количество дверей',{min:'0',max:'2',value:value('door')}),
    sectionInputRow(index,'front_bottom','Низ фасадной зоны',{title:'Y: ниша снизу до фасадной зоны',value:value('front_bottom')}),
    sectionInputRow(index,'front_top','Верх фасадной зоны',{title:'Y: ниша сверху от фасадной зоны',value:value('front_top')})
  ].join('');
  return '<p class="section-inspector-note">Открытая секция не требует дополнительных параметров.</p>';
}
function syncSectionInspectorState(){
  const fieldset=$('fs_sections'),state=$('sectionsState'),supported=SECTION_ARCHS.includes(SPEC.archetype),
    hasSections=Array.isArray(SPEC.sections)&&SPEC.sections.length>0,locked=modelMutationLocked();
  fieldset.classList.toggle('is-unsupported',!supported);
  fieldset.classList.toggle('is-empty',supported&&!hasSections);
  fieldset.classList.toggle('is-busy',supported&&locked);
  if(!supported){
    state.hidden=false;
    state.textContent='Для этого типа изделия секции не используются.';
    return;
  }
  if(!hasSections){
    state.hidden=false;
    state.textContent='В изделии пока нет секций. Добавьте первую секцию.';
    return;
  }
  if(locked){
    state.hidden=false;
    state.textContent='Пересчёт идёт — значения пока нельзя менять.';
    return;
  }
  state.hidden=true;state.textContent='';
}
function renderSections(){
  const box=$('sections');box.innerHTML='';
  const supported=SECTION_ARCHS.includes(SPEC.archetype);
  if(!supported){syncSectionInspectorState();return;}
  $('fs_sections').style.display='';
  const secs=SPEC.sections||[];
  secs.forEach((section,index)=>{
    const kind=section.kind||'open',details=document.createElement('details');
    details.className='section-inspector';details.dataset.sectionIndex=String(index);
    details.open=sectionInspectorStateKnown?openSectionInspectors.has(index):index===0;
    details.innerHTML=`<summary><span class="section-summary-kind">Секция ${index+1} · ${SECTION_KIND_LABELS[kind]||'Секция'}</span>
      <span class="section-summary-share" data-section-share="${index}">${sectionShareLabel(section)}</span></summary>
      <div class="section-inspector-body">
        <div class="section-inspector-row"><label>Тип секции</label><select data-i="${index}" data-k="kind">
          ${Object.entries(SECTION_KIND_LABELS).map(([key,label])=>`<option value="${key}" ${kind===key?'selected':''}>${label}</option>`).join('')}
        </select></div>
        ${sectionInputRow(index,'width_share','Доля ширины',{step:'0.1',title:'Доля ширины изделия для этой секции',value:section.width_share??''})}
        <div data-section-context="${index}">${sectionContextFields(section,index)}</div>
        <div class="section-inspector-note" data-sum="${index}"></div>
        <div class="section-inspector-actions"><button type="button" data-del="${index}">Удалить секцию</button></div>
      </div>`;
    details.addEventListener('toggle',()=>{
      sectionInspectorStateKnown=true;
      if(details.open)openSectionInspectors.add(index);else openSectionInspectors.delete(index);
    });
    details.querySelector('select[data-k="kind"]').addEventListener('change',()=>requestAnimationFrame(renderSections));
    box.appendChild(details);
  });
  updateSectionSums();
  syncSectionInspectorState();
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
    const share=document.querySelector(`[data-section-share="${i}"]`);
    if(share)share.textContent=sectionShareLabel(s);
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
let timer=null,lastOk=false,generateRequestSeq=0,viewportModelState='recalculating',
    generatedSpecJson=null,generatedRevision='',savedSpecJson=JSON.stringify(SPEC),
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
function setViewportModelState(state,diagnostics=null){
  viewportModelState=state;
  viewportModelDiagnostics=diagnostics&&typeof diagnostics==='object'
    ?Object.assign({checkErrors:0,unresolved:0,total:0},diagnostics)
    :{checkErrors:Number(diagnostics)||0,unresolved:0,total:Number(diagnostics)||0};
  syncViewportStatus();
}
function modelStateNotice(){
  if(viewportModelState==='changed')return 'Показаны данные предыдущей модели. Текущие изменения ещё не пересчитаны.';
  if(viewportModelState==='recalculating')return 'Показаны данные предыдущей модели. Новая редакция пересчитывается.';
  if(viewportModelState==='stale')return 'Показана предыдущая модель. Пересчёт текущей редакции не завершён — повторите действие.';
  if(viewportModelState==='blocked')return 'Данные текущей модели обновлены, но производство и экспорт заблокированы проверками.';
  if(viewportModelState==='decision')return '3D актуальна, но перед производством нужно выбрать позиции базы.';
  return '';
}
function productionBlockReason(){
  if(viewportModelState==='changed')return 'Сначала дождитесь пересчёта изменений';
  if(viewportModelState==='recalculating')return 'Модель пересчитывается';
  if(viewportModelState==='stale')return 'Повторите пересчёт текущей модели';
  if(viewportModelState==='blocked')return 'Исправьте блокирующие ошибки модели';
  if(viewportModelState==='decision')return 'Выберите все позиции производственной базы';
  if(viewportModelState==='draft')return 'Сначала создайте модель изделия';
  return 'Экспорт станет доступен после проверки модели';
}
function syncProductionAvailability(){
  const current=generatedSpecJson===JSON.stringify(SPEC),
    ready=viewportModelState==='ready'&&current&&!!generatedRevision&&lastOk&&!modelMutationLocked(),
    reason=ready?'Текущая редакция проверена и готова к производству':productionBlockReason();
  ['btnCfrn','btnB3d','btnDeliver'].forEach(id=>{const button=$(id);if(!button)return;
    button.disabled=!ready;button.title=reason;});
}
function syncDependentDataNotices(){
  const text=modelStateNotice();
  ['componentsStateNotice','productionStateNotice'].forEach(id=>{const notice=$(id);if(!notice)return;
    notice.textContent=text;notice.dataset.tone=viewportModelState;
    notice.classList.toggle('is-visible',!!text);});
}
function syncViewportStatus(){
  const status=$('viewportStatus');if(!status)return;
  let tone='recalculating',longText='Пересчитываю модель…',shortText='Пересчёт…';
  if(viewportModelState==='changed'){
    tone='changed';longText='Изменения ещё не пересчитаны · показана предыдущая модель';shortText='Есть изменения';
  }else if(viewportModelState==='recalculating'){
    tone='recalculating';longText='Пересчитываю изменения · показана предыдущая модель';shortText='Пересчёт…';
  }else if(viewportModelState==='ready'){
    tone='ready';longText='Модель актуальна · производство доступно';shortText='Готово';
  }else if(viewportModelState==='decision'){
    tone='decision';
    const diagnosticText=diagnosticLongCaption(viewportModelDiagnostics);
    longText='3D актуальна · требуется решение'+(diagnosticText?` · ${diagnosticText}`:'');
    shortText=viewportModelDiagnostics.unresolved?`Выбрать базу: ${viewportModelDiagnostics.unresolved}`:'Требуется решение';
  }else if(viewportModelState==='blocked'){
    tone='blocked';
    const diagnosticText=diagnosticLongCaption(viewportModelDiagnostics);
    longText='3D актуальна · производство заблокировано'+(diagnosticText?` · ${diagnosticText}`:'');
    shortText=viewportModelDiagnostics.checkErrors
      ?`Заблокировано: ${viewportModelDiagnostics.checkErrors}`:'Производство заблокировано';
  }else if(viewportModelState==='stale'){
    tone='stale';
    const diagnosticText=diagnosticLongCaption(viewportModelDiagnostics);
    longText='Текущая редакция не построена · показана предыдущая модель'+
      (diagnosticText?` · ${diagnosticText}`:'');
    shortText=viewportModelDiagnostics.checkErrors
      ?`Не построена: ${viewportModelDiagnostics.checkErrors}`:'Модель устарела';
  }else if(viewportModelState==='draft'){
    tone='neutral';longText='Черновик · модель ещё не построена';shortText='Черновик';
  }
  status.dataset.tone=tone;
  const saved=JSON.stringify(SPEC)===savedSpecJson;
  status.dataset.save=saved?'saved':'changed';
  $('viewportModelStateLong').textContent=longText;
  $('viewportModelStateShort').textContent=shortText;
  $('viewportSaveStateLong').textContent=saved?'Сохранено':'Есть несохранённые изменения';
  $('viewportSaveStateShort').textContent=saved?'Сохранено':'Не сохранено';
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
  syncDependentDataNotices();syncProductionAvailability();
}
function schedule(){
  clearTimeout(timer);refreshUndoState();
  if(typeof renderedWorkspaceMode!=='undefined'){
    renderedWorkspaceMode=null;renderedWorkspaceSpecJson=null;syncWorkspacePrintState();
  }
  setViewportModelState('changed');timer=setTimeout(()=>apply().catch(()=>{}),400);
}
async function apply(options){
  const opts=options||{};
  const requestId=++generateRequestSeq;
  if(SPEC&&SPEC.draft){showEmpty(true);setViewportModelState('draft');return;} // черновик не генерируем
  refreshUndoState();
  const requestSpecJson=JSON.stringify(SPEC);
  setViewportModelState('recalculating');
  try{
    const r=await fetch('/api/generate',{method:'POST',
      headers:{'Content-Type':'application/json'},body:JSON.stringify({spec:SPEC})});
    const p=await r.json();
    if(requestId!==generateRequestSeq||JSON.stringify(SPEC)!==requestSpecJson)
      return Object.assign({},p,{ok:false,viewer:null,stale:true});
    return commitGeneratedPayload(p,requestSpecJson,opts);
  }catch(error){
    if(requestId===generateRequestSeq){generatedSpecJson=null;generatedRevision='';setViewportModelState('stale');}
    throw error;
  }
}
function commitGeneratedPayload(p,requestSpecJson,options){
  const opts=options||{};
  const restorePartName=(typeof SELECTED_PART!=='undefined'&&SELECTED_PART)
    ?SELECTED_PART.name:null;
  paint(p,opts);
  const diagnostics=modelDiagnosticCounts(p);
  generatedSpecJson=p.viewer?requestSpecJson:null;
  generatedRevision=p.viewer?String(p.revision||''):'';
  setViewportModelState(!p.viewer?'stale':!p.ok?'blocked':diagnostics.unresolved?'decision':'ready',diagnostics);
  if(restorePartName&&p.viewer&&Array.isArray(p.viewer.panels)){
    const restoreIndex=p.viewer.panels.findIndex(panel=>panel.name===restorePartName);
    if(restoreIndex>=0)scene3d.select(restoreIndex);
  }
  refreshOperationTargets();
  if(drawOn) refreshDraw();
  if(nestOn) refreshNest();
  return p;
}
function paint(p,options){
  const opts=options||{};
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
  lastOk=p.ok;
  if(p.viewer){rebuild(p.viewer,opts);
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
  syncModelEditLock();
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
  [$('ovReset'),$('ovDelete')]
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
  if(CATALOG_MODE){
    if(CAT_SELECTED_FILE){clearCatalogSelection({restoreFocus:true});return;}
    closeCatalog();return;
  }
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
let CATALOG_MODE=false,CATALOG_PREVIOUS_HASH='';
let catalogReturnFocus=null;
const reviewStatusLabels={pending:'Ожидает решения',approved:'Согласовано',changes_requested:'Нужны изменения'};
const reviewAccessLabels={revoked:'Ссылка отозвана',expired:'Срок ссылки истёк'};
function reviewDate(value){const date=new Date(value);return Number.isNaN(date.getTime())?'':
  date.toLocaleString('ru-RU',{day:'numeric',month:'short',year:'numeric',hour:'2-digit',minute:'2-digit'}).replace(',',' ·');}
function reviewFileSize(bytes){const value=Number(bytes)||0;return value<1048576?
  `${Math.max(1,Math.round(value/1024))} КБ`:`${(value/1048576).toFixed(1).replace('.',',')} МБ`;}
function reviewText(tag,className,text){const element=document.createElement(tag);if(className)element.className=className;
  element.textContent=text;return element;}
function appendReviewDecision(parent,decision,review,{compact=false}={}){const result=document.createElement('div');
  result.className=compact?'review-inbox-history-event':'review-inbox-decision';
  const label=reviewStatusLabels[decision.status]||'Решение клиента';
  result.append(reviewText('strong','',`${label} · ${decision.reviewer_name||'Клиент'}`));
  if(decision.created_at)result.append(document.createTextNode(' · '+reviewDate(decision.created_at)));
  if(decision.revision)result.append(document.createTextNode(` · версия ${decision.revision.slice(0,10)}`));
  if(decision.comment)result.append(reviewText('p','review-inbox-comment',decision.comment));
  const files=Array.isArray(decision.attachments)?decision.attachments:[];
  if(files.length){const links=document.createElement('div');links.className='review-inbox-files';files.forEach(file=>{
      const link=document.createElement('a');link.href=`/api/reviews/attachments/${encodeURIComponent(review.id)}/${encodeURIComponent(file.id)}`;
      link.target='_blank';link.rel='noopener';link.textContent=`↗ ${file.name} · ${reviewFileSize(file.bytes)}`;links.append(link);});result.append(links);}
  parent.append(result);}
function renderReviews(reviews){const inbox=$('reviewInbox');inbox.replaceChildren();
  $('rightTabReviews').setAttribute('aria-label',`Согласования: ${reviews.length}`);
  if(!reviews.length){inbox.append(reviewText('div','review-inbox-empty',
    'Для этого изделия ещё нет ссылок на просмотр. Создайте ссылку слева — ответ клиента появится здесь.'));return;}
  reviews.forEach(review=>{const access=review.access_status||'active',item=document.createElement('article');
    item.className='review-inbox-item '+(access==='active'?review.status:access);
    item.append(reviewText('div','review-inbox-status',reviewAccessLabels[access]||reviewStatusLabels[review.status]||'Версия отправлена'));
    const meta=document.createElement('div');meta.className='review-inbox-meta';
    meta.append(reviewText('span','',review.link_mode==='live'?'Обновляемая ссылка':'Фиксированная версия'));
    meta.append(reviewText('span','',`Версия ${(review.revision||'').slice(0,10)}`));
    if(review.created_at)meta.append(reviewText('span','',reviewDate(review.created_at)));
    meta.append(reviewText('span','',review.expires_at?`Доступ до ${reviewDate(review.expires_at)}`:'Бессрочный доступ'));
    meta.append(reviewText('span','',`Ответственный: ${review.responsible_name||'не назначен'}`));
    if(review.created_by_name)meta.append(reviewText('span','',`Ссылку создал: ${review.created_by_name}`));item.append(meta);
    const decision=review.decision;if(decision)appendReviewDecision(item,decision,review);
    const history=Array.isArray(review.decision_history)?review.decision_history:[],past=history.filter(event=>!decision||event.id!==decision.id).reverse();
    if(past.length){const details=document.createElement('details');details.className='review-inbox-history';
      const summary=document.createElement('summary');summary.textContent=`Предыдущие решения · ${past.length}`;details.append(summary);
      past.forEach(event=>appendReviewDecision(details,event,review,{compact:true}));item.append(details);}
    if(access==='active'){const actions=document.createElement('div');actions.className='review-inbox-actions';
      const revoke=document.createElement('button');revoke.type='button';revoke.className='danger';revoke.textContent='Отозвать ссылку';
      revoke.onclick=()=>revokeReview(review);actions.append(revoke);item.append(actions);}
    inbox.append(item);});}
async function revokeReview(review){if(!confirm('Отозвать эту ссылку? Клиент сразу потеряет доступ, но история решений сохранится.'))return;
  try{const response=await fetch('/api/reviews/revoke',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({review_id:review.id})});const result=await response.json();
    if(!response.ok||!result.ok)throw new Error(result.error||'Ссылка не отозвана');toast('Ссылка отозвана');loadReviews();
  }catch(error){toast('Не удалось отозвать ссылку: '+error.message,true);}}
let reviewLoadSequence=0;
async function loadReviews(){const sequence=++reviewLoadSequence,expectedFile=$('projSel').value,button=$('reviewInboxRefresh');
  if(button)button.disabled=true;try{const response=await fetch('/api/reviews',{method:'POST',
      headers:{'Content-Type':'application/json'},body:'{}'});const result=await response.json();
    if(sequence!==reviewLoadSequence||result.project_file!==$('projSel').value)return;
    if(!response.ok||!result.ok)throw new Error(result.error||'Не удалось загрузить согласования');renderReviews(result.reviews||[]);
  }catch(error){if(sequence===reviewLoadSequence)$('reviewInbox').replaceChildren(
      reviewText('div','review-inbox-empty','Согласования не загрузились. Нажмите «Обновить».'));
  }finally{if(button&&sequence===reviewLoadSequence)button.disabled=false;}}
$('reviewInboxRefresh').onclick=loadReviews;
async function loadProjects(){
  const r=await fetch('/api/projects',{method:'POST',
    headers:{'Content-Type':'application/json'},body:'{}'});
  const p=await r.json();
  CAT_CURRENT_FILE=p.current||CAT_CURRENT_FILE;
  $('projSel').innerHTML=(p.projects||[]).map(x=>
    `<option value="${x.file}" ${x.file===p.current?'selected':''}>`+
    `${x.name.slice(0,38)} · ${x.archetype} ${x.dims}</option>`).join('');
  loadReviews();
}
function activeProjectFile(){
  return CAT_CURRENT_FILE||$('projSel').value||'';
}
function adoptSpec(p){
  SPEC=p.spec;CAT_CURRENT_FILE=p.file||CAT_CURRENT_FILE;UNDO.length=0;$('btnUndo').disabled=true;
  savedSpecJson=JSON.stringify(SPEC);generatedSpecJson=null;generatedRevision='';
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
  if(!dr){
    const cameraReset={resetView:true,view:'persp'};
    if(p.payload&&p.payload.viewer)commitGeneratedPayload(p.payload,JSON.stringify(SPEC),cameraReset);
    else apply(cameraReset);
  }
  loadProjects(); loadBuilds(); loadChatHistory();
  toast('Открыто: '+p.file);
}
// пустое рабочее пространство (AKD-214): «Новое» → чистый экран с приглашением загрузить ТЗ
let EMPTY=false;
function showEmpty(on){
  EMPTY=on; $('emptyState').classList.toggle('on',on);
  setViewportModelState(on?'draft':'recalculating');
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
        body:JSON.stringify({name:f.name,data:b64,provider:CHAT_PROVIDER,
          project_file:activeProjectFile()})});
      const p=await r.json();
      done();
      if(p.ok){adoptSpec(p); toast('✅ ТЗ распознано → '+(p.spec&&p.spec.project_name||p.file));}
      else{
        const ref=p.trace_id?' · trace '+p.trace_id:'';
        const code=p.error_code||p.code;
        toast('❌ Не удалось распознать ТЗ: '+(p.error||'нет ответа нейросети')+
          (code?' ['+code+']':'')+ref,true);
      }
    }catch(e){done(); toast('❌ Ошибка распознавания: '+e.message,true);}
  };
  rd.readAsDataURL(f);
}
$('esUpload').onclick=()=>$('esFile').click();
$('esFile').onchange=e=>{importTzFile(e.target.files[0]); e.target.value='';};
$('projSel').onchange=async e=>{
  const nextFile=e.target.value,previousFile=CAT_CURRENT_FILE;
  if(!await prepareWorkspaceChange('открыть другое изделие')){
    e.target.value=previousFile;return;
  }
  try{
    const r=await fetch('/api/open',{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({file:nextFile})});
    const p=await r.json();
    if(r.ok&&p.ok) adoptSpec(p);
    else{e.target.value=previousFile;toast('Ошибка: '+(p.error||'изделие не открылось'),true);}
  }catch(error){e.target.value=previousFile;toast('Изделие не открылось: '+error.message,true);}
};
$('projNew').onclick=async()=>{   // черновик: запись в каталоге + пустой воркспейс (AKD-214)
  if(!await prepareWorkspaceChange('создать новое изделие'))return;
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
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({spec:SPEC,project_file:activeProjectFile()})});
  const p=await r.json();
  if(p.ok){savedSpecJson=JSON.stringify(SPEC);fillForm();loadProjects();schedule();toast('Переименовано: '+name);}
  else{SPEC.project_name=previousName;toast('Ошибка: '+(p.error||''),true);}
};
$('projShare').onclick=()=>{
  if(modelMutationLocked()){toast('Дождитесь завершения текущего изменения модели',true);return;}
  $('shareSetup').hidden=false;$('shareResult').hidden=true;$('shareEyebrow').textContent='Просмотр для клиента';
  $('shareDialogTitle').textContent='Создать ссылку';$('shareUrl').value='';$('shareOpen').href='#';
  const dialog=$('shareDialog');if(dialog.showModal)dialog.showModal();else dialog.setAttribute('open','');
};
function selectedShareMode(){return document.querySelector('input[name="shareMode"]:checked')?.value||'snapshot';}
function syncShareModeNote(){$('shareModeNote').textContent=selectedShareMode()==='live'?
  'Клиент по тому же адресу увидит последнюю сохранённую версию. Несохранённые правки в ссылку не попадут.':
  'Будущие правки не изменят то, что увидит клиент.';}
document.querySelectorAll('input[name="shareMode"]').forEach(input=>input.onchange=syncShareModeNote);syncShareModeNote();
$('shareCreate').onclick=async()=>{
  const button=$('shareCreate'),mode=selectedShareMode(),expiry=$('shareExpiry').value;
  button.disabled=true;
  try{
    const response=await fetch('/api/reviews/create',{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({file:$('projSel').value,spec:SPEC,link_mode:mode,
        expires_in_days:expiry?Number(expiry):null})});
    const result=await response.json();
    if(!response.ok||!result.ok){toast('Ссылка не создана: '+(result.error||'ошибка'),true);return;}
    $('shareUrl').value=result.url;$('shareOpen').href=result.url;
    const created=new Date(result.review.created_at),createdText=Number.isNaN(created.getTime())?'':
      created.toLocaleString('ru-RU',{day:'numeric',month:'short',year:'numeric',hour:'2-digit',minute:'2-digit'}).replace(',',' ·');
    const expires=result.review.expires_at?` · до ${reviewDate(result.review.expires_at)}`:' · бессрочно';
    $('shareMeta').textContent=`${result.review.project_name} · версия ${result.review.revision.slice(0,10)}${createdText?' · '+createdText:''}${expires}`;
    $('shareResultCopy').textContent=mode==='live'?
      'Эта ссылка будет открывать последнюю сохранённую версию изделия. Полный адрес показывается только сейчас.':
      'Клиент увидит именно эту зафиксированную версию. Полный адрес показывается только сейчас.';
    $('shareSetup').hidden=true;$('shareResult').hidden=false;$('shareEyebrow').textContent=mode==='live'?'Обновляемая ссылка':'Версия зафиксирована';
    $('shareDialogTitle').textContent='Ссылка на просмотр готова';loadReviews();
  }catch(error){toast('Ссылка не создана: '+error.message,true);}
  finally{button.disabled=false;}
};
$('shareCopy').onclick=async()=>{
  const field=$('shareUrl');
  try{await navigator.clipboard.writeText(field.value);toast('Ссылка скопирована');}
  catch(error){field.focus();field.select();document.execCommand('copy');toast('Ссылка скопирована');}
};
function closeShareDialog(){const dialog=$('shareDialog');dialog.close?dialog.close():dialog.removeAttribute('open');}
$('shareCancel').onclick=closeShareDialog;$('shareClose').onclick=closeShareDialog;
/* ---------- каталог изделий (AKD-217) ---------- */
// Точное PNG строится тем же MebelScene, что и рабочая 3D. Старый SVG нужен
// только как временная заглушка, пока карточка переснимается в фоне.
function thumbErr(img){
  const fallback=img.dataset.fallback;
  if(fallback){img.removeAttribute('data-fallback');
    img.onerror=()=>{img.parentNode.textContent='Нет превью';};img.src=fallback;}
  else img.parentNode.textContent='Нет превью';
}
const CAT_RULES=[  // раздел ← archetype/furniture_type
  ['Тумбы',   p=>/тумб/i.test(p.ftype)||['drawer_unit'].includes(p.archetype)],
  ['Столы',   p=>/стол/i.test(p.ftype)||['desk','table','round_table'].includes(p.archetype)],
  ['Шкафы',   p=>/шкаф|гардероб/i.test(p.ftype)||['wardrobe','door_unit','cabinet'].includes(p.archetype)],
  ['Стеллажи',p=>/стеллаж|полк/i.test(p.ftype)||['shelving'].includes(p.archetype)],
  ['Черновики',p=>p.draft],
];
let CAT_ITEMS=[],CAT_VISIBLE_ITEMS=[],CAT_SELECTED_FILE='',CAT_CURRENT_FILE=__PROJECT_FILE__||'',
  CAT_LAST_CLICK_FILE='',CAT_LAST_CLICK_AT=0,CAT_SCOPE='all',CAT_TYPE='all',
  CAT_STATUS='all',CAT_RESPONSIBLE='all',CAT_TOTAL=0,CAT_CURRENT_USER_ID='',
  CAT_COUNTS={all:0,mine:0,unassigned:0,archived:0},CAT_MEMBERS=[],CAT_TYPES=[],CAT_SEARCH_TIMER=null;
let CATALOG_PREVIEW_SCENE=null,CATALOG_PREVIEW_RUNNING=false;
const CATALOG_PREVIEW_QUEUE=[],CATALOG_PREVIEW_SEEN=new Set();
const CATALOG_INACTIVE_IDS=['fs_project','modelState','fs_part','rightside','rightRail',
  'viewportTopbar','hud','draw','emptyState','fs_chat','viewportStatus'];
const CAT_ARCHETYPE_LABELS={desk:'Стол',table:'Стол',round_table:'Круглый стол',
  wardrobe:'Шкаф',door_unit:'Изделие с фасадом',cabinet:'Корпусное изделие',
  drawer_unit:'Тумба с ящиками',shelving:'Стеллаж',corpus:'Корпус',composite:'Составное изделие'};
function catalogEscape(value){
  return String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;',
    '"':'&quot;',"'":'&#39;'}[char]));
}
function catalogDraftMarkup(){
  return `<div class="draft-placeholder">
    <svg viewBox="0 0 32 32" aria-hidden="true"><path d="M7 4h13l5 5v19H7V4Z"/>
      <path d="M20 4v6h5M11 16h10M11 21h7"/></svg><span>Черновик</span></div>`;
}
function catalogThumbMarkup(item){
  if(item.archived){
    if(item.preview)return `<img src="/archive-preview/${encodeURIComponent(item.archive_id)}?v=${encodeURIComponent(item.archived_at||'')}"
      loading="lazy" alt="" onerror="thumbErr(this)">`;
    return `<div class="draft-placeholder"><svg viewBox="0 0 32 32" aria-hidden="true">
      <path d="M5 9h22v18H5V9ZM3 5h26v4H3V5ZM12 14h8"/></svg><span>Без превью</span></div>`;
  }
  if(item.draft)return catalogDraftMarkup();
  const exact=`/preview/${encodeURIComponent(item.file.replace(/\.json$/,'.png'))}?v=${Number(item.updated_at||0)}`;
  const fallback=`/thumb/${encodeURIComponent(item.file)}`;
  return `<img src="${item.preview_current?exact:fallback}" loading="lazy"
    data-preview-file="${catalogEscape(item.file)}"
    data-fallback="${item.preview_current?fallback:''}" alt="" onerror="thumbErr(this)">`;
}
function canBuildCatalogPreviews(){
  return !AUTH_CONTEXT||new Set(AUTH_CONTEXT.permissions||[]).has('project.write');
}
function waitCatalogPreviewFrame(){
  return new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
}
function updateCatalogPreviewImage(file,url){
  const item=CAT_ITEMS.find(value=>value.file===file);
  if(item){item.preview=true;item.preview_current=true;}
  document.querySelectorAll('img[data-preview-file]').forEach(img=>{
    if(img.dataset.previewFile!==file)return;
    img.dataset.fallback=`/thumb/${encodeURIComponent(file)}`;img.src=url;
  });
}
async function buildNextCatalogPreview(){
  if(CATALOG_PREVIEW_RUNNING)return;
  const file=CATALOG_PREVIEW_QUEUE.shift();if(!file)return;
  CATALOG_PREVIEW_RUNNING=true;
  try{
    const sourceResponse=await fetch('/api/catalog-preview-source',{method:'POST',
      headers:{'Content-Type':'application/json'},body:JSON.stringify({file})});
    const source=await sourceResponse.json();
    if(!sourceResponse.ok||!source.ok||!source.viewer)
      throw new Error(source.error||'3D-модель не построилась');
    if(!CATALOG_PREVIEW_SCENE)CATALOG_PREVIEW_SCENE=MebelScene($('catalogPreviewRenderer'));
    CATALOG_PREVIEW_SCENE.setPayload(source.viewer);
    if(CATALOG_PREVIEW_SCENE.setView)CATALOG_PREVIEW_SCENE.setView('axon');
    if(CATALOG_PREVIEW_SCENE.closeAll)CATALOG_PREVIEW_SCENE.closeAll();
    CATALOG_PREVIEW_SCENE.resize();await waitCatalogPreviewFrame();
    await new Promise(resolve=>setTimeout(resolve,90));
    const preview=CATALOG_PREVIEW_SCENE.snapshot(360);
    if(!preview)throw new Error('Снимок 3D не получен');
    const saveResponse=await fetch('/api/catalog-preview',{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({file,revision:source.revision,preview})});
    const saved=await saveResponse.json();
    if(!saveResponse.ok||!saved.ok)throw new Error(saved.error||'Превью не сохранилось');
    updateCatalogPreviewImage(file,saved.url);
  }catch(_error){/* SVG остаётся временным фолбэком; повтор — после перезагрузки Studio. */}
  finally{CATALOG_PREVIEW_RUNNING=false;queueMicrotask(buildNextCatalogPreview);}
}
function queueCatalogPreviews(items){
  if(!canBuildCatalogPreviews())return;
  (items||[]).forEach(item=>{
    if(item.archived||item.draft||item.preview_current||CATALOG_PREVIEW_SEEN.has(item.file))return;
    CATALOG_PREVIEW_SEEN.add(item.file);CATALOG_PREVIEW_QUEUE.push(item.file);
  });
  buildNextCatalogPreview();
}
function catalogTypeLabel(item){
  const ftype=String(item.ftype||'').trim();
  return ftype||CAT_ARCHETYPE_LABELS[item.archetype]||catSection(item);
}
function catalogUpdatedLabel(seconds){
  const date=new Date(Number(seconds||0)*1000);
  if(!Number(seconds)||Number.isNaN(date.getTime()))return 'Не записано';
  return date.toLocaleString('ru-RU',{day:'numeric',month:'short',year:'numeric',
    hour:'2-digit',minute:'2-digit'}).replace(',', ' ·');
}
function catalogCountText(count){
  const mod100=count%100,mod10=count%10;
  const noun=mod100>=11&&mod100<=14?'изделий':mod10===1?'изделие':
    mod10>=2&&mod10<=4?'изделия':'изделий';
  return `${count} ${noun}`;
}
function updateCatalogContext(){
  const visible=CAT_ITEMS.length;
  const archived=CAT_SCOPE==='archived';
  $('catalogTitle').textContent=archived?'Архив изделий':'Каталог изделий';
  $('catalogContextTitle').textContent=archived?'Архив компании':'Каталог компании';
  $('catalogContextHint').textContent=archived?'Здесь хранятся изделия, которые можно вернуть в работу.':
    'Выберите изделие, чтобы открыть его в Studio.';
  $('catalogContextCount').textContent=visible===CAT_TOTAL?catalogCountText(CAT_TOTAL):
    `Показано ${visible} из ${CAT_TOTAL}`;
}
function catalogOwnerMarkup(item){
  const name=item.responsible||'Без ответственного',empty=!item.responsible_user_id;
  return `<span class="cat-card-owner${empty?' is-empty':''}">
    <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8Z"/>
      <path d="M4.5 21a7.5 7.5 0 0 1 15 0"/></svg>
    <span>${catalogEscape(name)}</span></span>`;
}
function renderCatalogControls(){
  const scopes=[
    ['all','Все изделия',CAT_COUNTS.all],
    ['mine','Мои',CAT_COUNTS.mine],
    ['unassigned','Без ответственного',CAT_COUNTS.unassigned],
    ['archived','Архив',CAT_COUNTS.archived],
  ];
  $('catScopes').innerHTML=scopes.map(([value,label,count])=>
    `<button type="button" class="cat-scope ${value===CAT_SCOPE?'on':''}" data-scope="${value}"
      aria-pressed="${value===CAT_SCOPE}" ${value==='mine'&&!CAT_CURRENT_USER_ID?'disabled':''}>
      <span>${label}</span><output>${count}</output></button>`).join('');
  $('catScopes').querySelectorAll('.cat-scope').forEach(button=>button.onclick=()=>{
    /* Верхняя область меняет набор изделий, но не намерение человека по типу:
       выбрал «Стеллажи» — значит, при переходе в «Мои» или «Без ответственного»
       он всё ещё смотрит именно стеллажи. */
    CAT_SCOPE=button.dataset.scope;CAT_RESPONSIBLE='all';CAT_STATUS='all';
    refreshCatalog({clearSelection:true});
  });
  $('catResponsible').innerHTML=`<option value="all">Все ответственные</option>
    <option value="unassigned">Без ответственного</option>`+CAT_MEMBERS.map(member=>
      `<option value="${catalogEscape(member.user_id)}">${catalogEscape(member.display_name)}</option>`
    ).join('');
  $('catResponsible').value=CAT_RESPONSIBLE;
  $('catResponsible').disabled=CAT_SCOPE!=='all'||CAT_SCOPE==='archived';
  $('catStatus').value=CAT_STATUS;
  $('catStatus').disabled=CAT_SCOPE==='archived';
  const categories=['all',...CAT_TYPES];
  $('catCats').innerHTML=categories.map(value=>{
    const label=value==='all'?'Все типы':value;
    return `<button type="button" class="catchip ${value===CAT_TYPE?'on':''}"
      data-type="${catalogEscape(value)}" aria-pressed="${value===CAT_TYPE}">${catalogEscape(label)}</button>`;
  }).join('');
  $('catCats').querySelectorAll('.catchip').forEach(button=>button.onclick=()=>{
    CAT_TYPE=button.dataset.type;refreshCatalog({clearSelection:true});
  });
}
function selectedCatalogItem(){
  return CAT_ITEMS.find(item=>item.file===CAT_SELECTED_FILE)||null;
}
function renderCatalogInspector(){
  const item=selectedCatalogItem(),inspector=$('catInspector');
  inspector.classList.toggle('is-empty',!item);
  $('catInspectorEmpty').hidden=!!item;
  if(!item)return;
  $('catInspectName').textContent=item.name||'Без названия';
  $('catInspectPreview').innerHTML=catalogThumbMarkup(item);
  const status=$('catInspectStatus');
  status.textContent=item.archived?'В архиве':item.draft?'Черновик':'Рабочее изделие';
  status.classList.toggle('is-draft',!!item.draft);
  status.classList.toggle('is-archived',!!item.archived);
  $('catInspectType').textContent=catalogTypeLabel(item);
  $('catInspectDims').textContent=item.dims||'—';
  $('catInspectDecor').textContent=item.decor||'Не указан';
  $('catInspectUpdated').textContent=catalogUpdatedLabel(item.updated_at);
  const responsibleText=$('catInspectResponsible'),responsibleSelect=$('catInspectResponsibleSelect');
  responsibleText.textContent=item.responsible||'Не назначен';
  const mayAssign=!item.archived&&!!item.can_manage;
  responsibleText.hidden=mayAssign;responsibleSelect.hidden=!mayAssign;
  if(mayAssign){
    responsibleSelect.innerHTML='<option value="">Не назначен</option>'+CAT_MEMBERS.map(member=>
      `<option value="${catalogEscape(member.user_id)}">${catalogEscape(member.display_name)}</option>`).join('');
    responsibleSelect.value=item.responsible_user_id||'';
  }
  $('catInspectAuthor').textContent=item.author||'Не указан';
  $('catInspectorPeopleNote').hidden=!!(item.responsible&&item.author);
  $('catArchiveMeta').hidden=!item.archived;
  $('catInspectArchivedAt').textContent=catalogUpdatedLabel(item.updated_at);
  $('catInspectArchivedBy').textContent=item.archived_by||'Не указано';
  $('catInspectArchiveReason').textContent=item.archive_reason||'Без комментария';
  const canCreate=!AUTH_CONTEXT||new Set(AUTH_CONTEXT.permissions||[]).has('project.create');
  $('catOpen').hidden=!!item.archived;
  $('catRename').hidden=!!item.archived||!item.can_manage;
  $('catDuplicate').hidden=!!item.archived||!canCreate;
  $('catArchive').hidden=!!item.archived||!item.can_manage;
  $('catRestore').hidden=!item.archived||!item.can_manage;
}
function syncCatalogSelection({focus=false}={}){
  $('catGrid').querySelectorAll('.catCard').forEach((card,index)=>{
    const selected=card.dataset.f===CAT_SELECTED_FILE;
    card.setAttribute('aria-selected',String(selected));
    card.tabIndex=selected||(!CAT_SELECTED_FILE&&index===0)?0:-1;
  });
  renderCatalogInspector();
  if(focus){
    const target=[...$('catGrid').querySelectorAll('.catCard')]
      .find(card=>card.dataset.f===CAT_SELECTED_FILE);
    if(target)requestAnimationFrame(()=>target.focus({preventScroll:true}));
  }
}
function selectCatalogItem(file,{focus=false}={}){
  if(!CAT_ITEMS.some(item=>item.file===file))return;
  CAT_SELECTED_FILE=file;syncCatalogSelection({focus});
}
function catalogCardClick(file){
  const now=performance.now(),doubleClick=file===CAT_LAST_CLICK_FILE&&now-CAT_LAST_CLICK_AT<420;
  CAT_LAST_CLICK_FILE=file;CAT_LAST_CLICK_AT=now;selectCatalogItem(file);
  if(doubleClick){CAT_LAST_CLICK_FILE='';CAT_LAST_CLICK_AT=0;openCatalogItem(file);}
}
function clearCatalogSelection({restoreFocus=false}={}){
  const previous=CAT_SELECTED_FILE;CAT_SELECTED_FILE='';syncCatalogSelection();
  if(restoreFocus&&previous){
    const card=[...$('catGrid').querySelectorAll('.catCard')]
      .find(item=>item.dataset.f===previous);
    if(card)requestAnimationFrame(()=>card.focus({preventScroll:true}));
  }
}
function setCatalogMode(on,{restoreFocus=true}={}){
  on=!!on;
  if(on&&!CATALOG_MODE)catalogReturnFocus=document.activeElement;
  CATALOG_MODE=on;
  $('app').classList.toggle('catalog-mode',on);
  $('catalog').classList.toggle('on',on);
  $('catalog').setAttribute('aria-hidden',String(!on));
  $('catalogContext').hidden=!on;
  CATALOG_INACTIVE_IDS.forEach(id=>{const element=$(id);if(element)element.inert=on;});
  document.title=on?'Каталог изделий — Akeda Studio':'Akeda Studio — предпросмотр и правки';
  if(on){
    updateCatalogContext();
    requestAnimationFrame(()=>{$('catQ').focus({preventScroll:true});scene3d.resize();});
  }else{
    requestAnimationFrame(()=>{
      scene3d.resize();
      if(restoreFocus&&catalogReturnFocus&&catalogReturnFocus.isConnected)
        catalogReturnFocus.focus({preventScroll:true});
    });
  }
}
function catalogRequestPayload(){
  return {scope:CAT_SCOPE,type:CAT_TYPE,status:CAT_SCOPE==='archived'?'all':CAT_STATUS,
    responsible_user_id:CAT_SCOPE==='archived'?'all':CAT_RESPONSIBLE,q:($('catQ').value||'').trim()};
}
async function refreshCatalog({clearSelection=false}={}){
  try{
    const r=await fetch('/api/projects',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify(catalogRequestPayload())});
    const p=await r.json();
    if(!r.ok){toast('Каталог не обновился: '+(p.error||'ошибка загрузки'),true);return false;}
    CAT_ITEMS=p.projects||[];CAT_CURRENT_FILE=p.current||CAT_CURRENT_FILE;
    CAT_TOTAL=Number(p.total??CAT_ITEMS.length);
    CAT_COUNTS=p.counts||{all:CAT_TOTAL,mine:0,unassigned:0,archived:0};
    CAT_MEMBERS=p.members||[];CAT_TYPES=p.types||[];CAT_CURRENT_USER_ID=p.current_user_id||'';
    if(clearSelection)CAT_SELECTED_FILE='';
    renderCatalog();return true;
  }catch(error){toast('Каталог не обновился: '+error.message,true);return false;}
}
async function openCatalog({pushHistory=true}={}){
  if(partEditBusy){toast('Дождитесь короткого пересчёта детали',true);return;}
  CAT_SCOPE='all';CAT_TYPE='all';CAT_STATUS='all';CAT_RESPONSIBLE='all';
  CAT_SELECTED_FILE='';$('catQ').value='';
  if(!await refreshCatalog({clearSelection:true}))return;
  if(pushHistory&&location.hash!=='#catalog'){
    CATALOG_PREVIOUS_HASH=location.hash||'';
    history.pushState({akedaView:'catalog'},'',location.pathname+location.search+'#catalog');
  }
  setCatalogMode(true);
}
function closeCatalog({fromHistory=false}={}){
  if(!CATALOG_MODE)return;
  CAT_SELECTED_FILE='';renderCatalogInspector();
  setCatalogMode(false);
  if(fromHistory||location.hash!=='#catalog')return;
  if(history.state&&history.state.akedaView==='catalog')history.back();
  else history.replaceState({akedaView:'editor'},'',
    location.pathname+location.search+(CATALOG_PREVIOUS_HASH||''));
}
function leaveCatalogForEditor(){
  CAT_SELECTED_FILE='';renderCatalogInspector();
  setCatalogMode(false,{restoreFocus:false});
  if(location.hash==='#catalog')history.replaceState({akedaView:'editor'},'',
    location.pathname+location.search+(CATALOG_PREVIOUS_HASH||''));
}
function catSection(p){
  if(p.category)return p.category;
  if(p.draft) return 'Черновики';
  for(const [nm,fn] of CAT_RULES) if(fn(p)) return nm;
  return 'Прочее';
}
function renderCatalog(){
  updateCatalogContext();renderCatalogControls();
  const items=CAT_ITEMS;
  CAT_VISIBLE_ITEMS=items;
  if(CAT_SELECTED_FILE&&!items.some(item=>item.file===CAT_SELECTED_FILE))CAT_SELECTED_FILE='';
  $('catGrid').innerHTML=items.map(p=>`
    <button type="button" class="catCard" role="option" data-f="${catalogEscape(p.file)}"
      aria-selected="${p.file===CAT_SELECTED_FILE}" aria-label="${catalogEscape(p.name)}">
      <span class="img">${catalogThumbMarkup(p)}</span>
      <span class="nm" title="${catalogEscape(p.name)}">${catalogEscape(p.name)}${p.archived?'<span class="dr">архив</span>':p.draft?'<span class="dr">черновик</span>':''}</span>
      <span class="sub">${catalogEscape(p.dims||'—')}${p.decor?' · '+catalogEscape(p.decor):''}</span>
      ${catalogOwnerMarkup(p)}
    </button>`).join('')||'<div class="catEmpty">По этому запросу изделий нет.</div>';
  $('catGrid').querySelectorAll('.catCard').forEach(c=>{
    c.onclick=()=>catalogCardClick(c.dataset.f);
    c.onkeydown=event=>catalogCardKeydown(event,c.dataset.f);
  });
  syncCatalogSelection();queueCatalogPreviews(items);
}
async function openCatalogItem(file=CAT_SELECTED_FILE){
  if(!file)return;
  const selected=selectedCatalogItem();
  if(selected&&selected.archived){toast('Сначала восстановите изделие из архива',true);return;}
  if(!await prepareWorkspaceChange('открыть выбранное изделие'))return;
  const button=$('catOpen');button.disabled=true;
  try{
    const r=await fetch('/api/open',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({file})});
    const p=await r.json();
    if(r.ok&&p.ok){leaveCatalogForEditor();adoptSpec(p);}
    else toast('Ошибка: '+(p.error||''),true);
  }catch(error){toast('Изделие не открылось: '+error.message,true);}
  finally{button.disabled=false;}
}
function catalogCardKeydown(event,file){
  if(event.key==='Enter'){event.preventDefault();openCatalogItem(file);return;}
  if(event.key==='Escape'){
    if(!CAT_SELECTED_FILE){event.preventDefault();event.stopPropagation();closeCatalog();return;}
    event.preventDefault();event.stopPropagation();clearCatalogSelection({restoreFocus:true});return;
  }
  if(!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(event.key))return;
  event.preventDefault();
  const cards=[...$('catGrid').querySelectorAll('.catCard')],index=cards.findIndex(c=>c.dataset.f===file);
  if(index<0)return;
  const columns=Math.max(1,getComputedStyle($('catGrid')).gridTemplateColumns.split(' ').length);
  const step={ArrowLeft:-1,ArrowRight:1,ArrowUp:-columns,ArrowDown:columns}[event.key];
  const next=Math.max(0,Math.min(cards.length-1,index+step));
  selectCatalogItem(cards[next].dataset.f,{focus:true});
}
async function renameCatalogItem(){
  const item=selectedCatalogItem();if(!item)return;
  const name=prompt('Название изделия:',item.name||'');
  if(!name||name.trim()===item.name)return;
  const button=$('catRename');button.disabled=true;
  try{
    const r=await fetch('/api/rename',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({file:item.file,name:name.trim()})});
    const p=await r.json();
    if(!r.ok||!p.ok){toast('Не удалось переименовать: '+(p.error||'ошибка'),true);return;}
    if(item.file===CAT_CURRENT_FILE&&SPEC)SPEC.project_name=name.trim();
    await refreshCatalog();
    if(CAT_ITEMS.some(entry=>entry.file===item.file))CAT_SELECTED_FILE=item.file;
    syncCatalogSelection();loadProjects();toast('Переименовано: '+name.trim());
  }catch(error){toast('Не удалось переименовать: '+error.message,true);}
  finally{button.disabled=false;}
}
async function duplicateCatalogItem(){
  const item=selectedCatalogItem();if(!item)return;
  const button=$('catDuplicate');button.disabled=true;
  try{
    const r=await fetch('/api/duplicate',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({file:item.file,stay_catalog:true})});
    const p=await r.json();
    if(!r.ok||!p.ok){toast('Не удалось создать копию: '+(p.error||'ошибка'),true);return;}
    await refreshCatalog();CAT_SELECTED_FILE=p.file;renderCatalog();
    selectCatalogItem(p.file,{focus:true});loadProjects();toast('Копия добавлена в каталог');
  }catch(error){toast('Не удалось создать копию: '+error.message,true);}
  finally{button.disabled=false;}
}
async function assignCatalogResponsible(){
  const item=selectedCatalogItem();if(!item||item.archived||!item.can_manage)return;
  const select=$('catInspectResponsibleSelect'),responsibleUserId=select.value;
  select.disabled=true;
  try{
    const r=await fetch('/api/catalog/assign',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({file:item.file,responsible_user_id:responsibleUserId})});
    const p=await r.json();
    if(!r.ok||!p.ok){toast('Ответственный не изменён: '+(p.error||'ошибка'),true);renderCatalogInspector();return;}
    await refreshCatalog();CAT_SELECTED_FILE=item.file;syncCatalogSelection();
    toast(responsibleUserId?'Ответственный изменён':'Ответственный снят');
  }catch(error){toast('Ответственный не изменён: '+error.message,true);renderCatalogInspector();}
  finally{select.disabled=false;}
}
function openCatalogArchiveDialog(){
  const item=selectedCatalogItem();if(!item||item.archived||!item.can_manage)return;
  const dialog=$('catArchiveDialog');dialog.dataset.file=item.file;
  $('catArchiveTarget').textContent=item.name||'Без названия';
  $('catArchiveReason').value='';$('catArchiveError').textContent='';
  if(dialog.showModal)dialog.showModal();else dialog.setAttribute('open','');
  requestAnimationFrame(()=>$('catArchiveReason').focus({preventScroll:true}));
}
function closeCatalogArchiveDialog(){
  const dialog=$('catArchiveDialog');
  dialog.close?dialog.close():dialog.removeAttribute('open');delete dialog.dataset.file;
}
async function submitCatalogArchive(event){
  event.preventDefault();
  const dialog=$('catArchiveDialog'),file=dialog.dataset.file||'',reason=$('catArchiveReason').value.trim();
  const item=CAT_ITEMS.find(value=>value.file===file&&!value.archived);
  if(!item){$('catArchiveError').textContent='Изделие уже изменилось. Закройте окно и повторите.';return;}
  if(reason.length<3){$('catArchiveError').textContent='Укажите короткую причину.';$('catArchiveReason').focus();return;}
  const button=$('catArchiveConfirm');button.disabled=true;$('catArchiveError').textContent='';
  try{
    const r=await fetch('/api/catalog/archive',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({file,reason})});const p=await r.json();
    if(!r.ok||!p.ok){$('catArchiveError').textContent=p.error||'Не удалось переместить изделие.';return;}
    closeCatalogArchiveDialog();CAT_SELECTED_FILE='';await refreshCatalog({clearSelection:true});
    loadProjects();toast(`«${item.name}» перемещено в архив`);
  }catch(error){$('catArchiveError').textContent=error.message||'Не удалось переместить изделие.';}
  finally{button.disabled=false;}
}
async function restoreCatalogItem(){
  const item=selectedCatalogItem();if(!item||!item.archived||!item.can_manage)return;
  if(!confirm(`Вернуть «${item.name}» в рабочий каталог?`))return;
  const button=$('catRestore');button.disabled=true;
  try{
    const r=await fetch('/api/catalog/restore',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({archive_id:item.archive_id})});const p=await r.json();
    if(!r.ok||!p.ok){toast('Изделие не восстановлено: '+(p.error||'ошибка'),true);return;}
    CAT_SELECTED_FILE='';await refreshCatalog({clearSelection:true});loadProjects();
    toast(`«${item.name}» вернуто в каталог`);
  }catch(error){toast('Изделие не восстановлено: '+error.message,true);}
  finally{button.disabled=false;}
}
$('projCat').onclick=()=>openCatalog();
$('catClose').onclick=()=>closeCatalog();
$('catInspectorClose').onclick=()=>clearCatalogSelection({restoreFocus:true});
$('catOpen').onclick=()=>openCatalogItem();
$('catRename').onclick=renameCatalogItem;
$('catDuplicate').onclick=duplicateCatalogItem;
$('catInspectResponsibleSelect').onchange=assignCatalogResponsible;
$('catArchive').onclick=openCatalogArchiveDialog;
$('catArchiveCancel').onclick=closeCatalogArchiveDialog;
$('catArchiveForm').onsubmit=submitCatalogArchive;
$('catRestore').onclick=restoreCatalogItem;
$('catResponsible').onchange=event=>{
  CAT_RESPONSIBLE=event.target.value;refreshCatalog({clearSelection:true});
};
$('catStatus').onchange=event=>{
  CAT_STATUS=event.target.value;refreshCatalog({clearSelection:true});
};
$('catQ').addEventListener('input',()=>{
  clearTimeout(CAT_SEARCH_TIMER);
  CAT_SEARCH_TIMER=setTimeout(()=>refreshCatalog({clearSelection:true}),180);
});
window.addEventListener('popstate',()=>{
  if(location.hash==='#catalog')openCatalog({pushHistory:false});
  else if(CATALOG_MODE)closeCatalog({fromHistory:true});
});
if(location.hash==='#catalog')queueMicrotask(()=>openCatalog({pushHistory:false}));

$('projDup').onclick=async()=>{
  const r=await fetch('/api/duplicate',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({spec:SPEC,project_file:activeProjectFile()})});
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
let activeChatController=null,activeChatOperationId='',chatAbortReason='',chatRequestInFlight=false;
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
function createOperation(command,{images=[],forceModel=false,kind='command',recordedAt='',
  contextOverride=null,provider='',traceId=''}={}){
  const id=`operation-${++operationSeq}`,
    context=contextOverride||operationContextSnapshot(forceModel);
  const attachmentMeta=(images||[]).map(im=>({name:im.name||'',mime:im.mime||''}));
  const el=operationNode('article','operation-record is-pending'); el.id=id;
  el.setAttribute('aria-labelledby',id+'-title');
  const head=operationNode('div','operation-head');
  const status=operationNode('span','operation-state','В работе');
  const recordedDate=recordedAt?new Date(recordedAt):new Date(), validDate=!Number.isNaN(recordedDate.getTime());
  const shownDate=validDate?recordedDate:new Date();
  const time=operationNode('time','operation-time',shownDate.toLocaleString('ru-RU',
    {day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}));
  time.dateTime=shownDate.toISOString();
  head.append(status,time);
  const title=operationNode('p','operation-command',trimOperationCommand(command,attachmentMeta.length));
  title.id=id+'-title';
  const contextLine=operationNode('div','operation-context',`Контекст: ${context.label}`+
    (attachmentMeta.length?` · ТЗ: ${attachmentMeta.length}`:''));
  if(traceId)contextLine.append(document.createTextNode(` · trace_id: ${traceId}`));
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
  const operation={id,kind,el,status,summary,changes,more,check,actions,show,details,undo,technical,contextLine,
    context,attachments:attachmentMeta,provider:provider||$('aiProvider').selectedOptions[0]?.textContent||CHAT_PROVIDER||'—',
    traceId:traceId||'',
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
async function loadChatHistory(){
  const generation=chatWorkspaceGeneration;
  try{
    const response=await fetch('/api/chat-history',{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({project_file:activeProjectFile()})});
    if(!response.ok)return;
    const payload=await response.json();
    if(generation!==chatWorkspaceGeneration||OPERATIONS.size)return;
    CHAT_HISTORY.splice(0,CHAT_HISTORY.length,...(payload.history||[]).slice(-16));
    (payload.operations||[]).slice(-OPERATION_LIMIT).forEach(item=>{
      const stored=item.context||{},partName=stored.scope==='part'?stored.part_name:null;
      const context=partName?{scope:'part',label:`Деталь · ${partName}`,partName,
        partType:stored.part_type||'',placement:null}:{scope:'model',label:'Всё изделие',partName:null};
      const operation=createOperation(item.message||'Команда без текста',{
        forceModel:true,kind:'history',recordedAt:item.created_at||'',contextOverride:context,
        provider:item.provider||'—',traceId:item.trace_id||''});
      finishOperation(operation,{state:item.changed?'applied':'answer',reply:item.reply||'',
        changes:item.changes||[],usage:item.usage||null,canUndo:false,
        summary:item.changed?'':'Модель не изменялась.'});
    });
  }catch(_error){}
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
const MODEL_LOCK_DISABLED_STATE=new Map();
function modelMutationControls(){
  return [...document.querySelectorAll([
    '#projRen','#projDup','#projShare','#btnUndo','#btnFixAll',
    '#rightViewProperties input','#rightViewProperties select','#rightViewProperties textarea',
    '#rightViewProperties button','#rightViewComponents input','#rightViewComponents select',
    '#rightViewComponents textarea','#rightViewComponents button',
    '#rightViewProduction input','#rightViewProduction select','#rightViewProduction textarea',
    '#rightViewProduction button','#fs_part input[data-ov]','#ovApply','#partEditCancel',
    '#ovReset','#ovDelete'
  ].join(','))];
}
function setModelMutationControlsLocked(locked){
  if(locked){
    modelMutationControls().forEach(control=>{
      if(!MODEL_LOCK_DISABLED_STATE.has(control))MODEL_LOCK_DISABLED_STATE.set(control,control.disabled);
      control.disabled=true;
    });
    return;
  }
  MODEL_LOCK_DISABLED_STATE.forEach((wasDisabled,control)=>{control.disabled=wasDisabled;});
  MODEL_LOCK_DISABLED_STATE.clear();
  syncProductionAvailability();
  syncWorkspacePrintState();refreshUndoState();
}
function syncChatPrimaryAction(){
  const button=$('chatSend'),canCancel=!!(chatBusy&&activeChatController&&
    !activeChatController.signal.aborted),
    stopping=!!(chatBusy&&['user','navigation'].includes(chatAbortReason)&&activeChatController&&
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
  setModelMutationControlsLocked(locked);
  Object.entries(rightPanelModes).forEach(([key,item])=>item.panel.inert=key!==rightPanelMode);
  [$('chatMsg'),$('chatAttach'),$('aiProvider')]
    .filter(Boolean).forEach(el=>el.disabled=locked);
  $('btnFixAll').disabled=locked;
  syncChatPrimaryAction();
  syncSectionInspectorState();
  syncViewportStatus();
}
function setChatBusy(busy,stateText){
  chatBusy=!!busy;
  $('fs_chat').setAttribute('aria-busy',String(chatBusy));
  $('fs_chat').classList.toggle('is-busy',chatBusy);
  [$('chatMsg'),$('chatAttach'),$('aiProvider')]
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
  activeChatOperationId=(typeof crypto!=='undefined'&&crypto.randomUUID)?crypto.randomUUID():
    `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  return activeChatController;
}
async function requestChat(payload,controller){
  chatRequestInFlight=true;syncChatPrimaryAction();
  const timeout=setTimeout(()=>{
    if(!controller.signal.aborted){chatAbortReason='timeout';controller.abort();}
  },CHAT_REQUEST_TIMEOUT_MS);
  try{return await fetch('/api/chat',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({...payload,project_file:activeProjectFile(),
      operation_id:activeChatOperationId}),signal:controller.signal});}
  finally{clearTimeout(timeout);chatRequestInFlight=false;syncChatPrimaryAction();}
}
async function cancelActiveChatRequest(reason='user'){
  if(!chatBusy||!activeChatController||activeChatController.signal.aborted)return false;
  chatAbortReason=reason;
  const operationId=activeChatOperationId;
  if(operationId){
    const notice=fetch('/api/chat/cancel',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({operation_id:operationId})}).catch(()=>null);
    await Promise.race([notice,new Promise(resolve=>setTimeout(resolve,250))]);
  }
  activeChatController.abort();syncChatPrimaryAction();
  setChatState(reason==='navigation'?'Останавливаю команду перед переходом…':
    'Останавливаю команду…',false,'busy');
  return true;
}
function finishChatRequest(controller){
  if(activeChatController===controller){activeChatController=null;chatAbortReason='';
    activeChatOperationId='';chatRequestInFlight=false;}
}
async function prepareWorkspaceChange(action){
  if(partEditBusy){toast('Дождитесь короткого пересчёта детали',true);return false;}
  if(!chatBusy)return true;
  if(!activeChatController){toast('Дождитесь короткого пересчёта модели',true);return false;}
  const confirmed=confirm(`Сейчас ИИ изменяет изделие. Остановить команду и ${action}?\n\n`+
    'Незавершённый результат не будет применён.');
  if(!confirmed)return false;
  await cancelActiveChatRequest('navigation');
  const deadline=Date.now()+2500;
  while(chatBusy&&Date.now()<deadline)await new Promise(resolve=>setTimeout(resolve,25));
  if(chatBusy){toast('Команда ещё останавливается. Повторите переход через секунду.',true);return false;}
  return true;
}
window.addEventListener('beforeunload',event=>{
  if(!modelMutationLocked())return;
  event.preventDefault();event.returnValue='';
});
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
  // ИИ получает только идентификаторы и семантику. Геометрию и стыки считает
  // серверный EditEngine; мировые координаты в LLM-контекст не передаём.
  if(lastPayload&&lastPayload.viewer&&lastPayload.viewer.panels)
    ctx.panels=lastPayload.viewer.panels.slice(0,80).map(p=>({panel_id:p.name,
      name:p.name,type:p.type,section_id:p.section_id||null}));
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
    if(operation.context.partName)ctx.selected_part={panel_id:operation.context.partName,
      name:operation.context.partName,type:operation.context.partType,
      section_id:(currentSelectedPart()||{}).section_id||null};
    const r=await requestChat({spec:JSON.parse(requestSpecJson),message:m,history:CHAT_HISTORY,
      context:ctx,images:imgs.length?imgs:null,provider:CHAT_PROVIDER},controller);
    let p={};
    try{p=await r.json();}catch(e){throw new Error(`Сервер вернул ответ ${r.status} без данных`);}
    if(p.trace_id&&!operation.traceId){operation.traceId=String(p.trace_id);
      operation.contextLine.append(document.createTextNode(` · trace_id: ${operation.traceId}`));}
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
      }else if(abortReason==='navigation'){
        finishOperation(operation,{state:'undone',summary:'Остановлено перед переходом. Модель не изменена.',
          statusLabel:'Остановлено'});
        setChatState('Команда остановлена перед переходом',false,'');
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
  const initialSpecJson=JSON.stringify(SPEC),initialUndoDepth=UNDO.length;
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
                 +'УДАЛЯТЬ ЗАПРЕЩЕНО. Для полок и перегородок верни move_panel '
                 +'с семантическими привязками; placement и координаты не пиши.',
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
    const requestedStop=['user','navigation'].includes(abortReason);
    const workspaceChanged=chatWorkspaceGeneration!==requestGeneration;
    if(!workspaceChanged&&abortReason==='navigation'&&appliedPasses){
      SPEC=JSON.parse(initialSpecJson);UNDO.splice(initialUndoDepth);appliedPasses=0;
      refreshUndoState();fillForm();try{await apply();}catch(_restoreError){}
    }
    if(!workspaceChanged&&appliedPasses){
      const remaining=issueCount();
      finishOperation(operation,{state:'warning',reply:replies.join('\n'),changes:allChanges,
        usage:operationTokens?{total:operationTokens}:null,canUndo:false,
        summary:requestedStop
          ?`Остановлено после ${appliedPasses} ${passWord(appliedPasses)}. Осталось ${remaining} ${findingWord(remaining)}.`
          :`Остановлено после ${appliedPasses} ${passWord(appliedPasses)}: ${e.message}`,
        checkSnapshot:currentCheckSnapshot(),statusLabel:'Исправлено частично'});
      setChatState('Автоисправление остановлено · часть правок применена',!requestedStop,
        requestedStop?'':'error');
    }else if(!workspaceChanged&&requestedStop){
      finishOperation(operation,{state:'undone',summary:'Остановлено. Модель не изменена.',
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
function syncProviderLabel(){
  const sel=$('aiProvider'),opt=sel&&sel.options[sel.selectedIndex];
  $('chatMetaTitle').textContent='Изменить модель словами'+(opt?' · '+opt.textContent:'');
}
async function loadProviders(){
  try{
    const r=await fetch('/api/providers',{method:'POST',
      headers:{'Content-Type':'application/json'},body:'{}'});
    const d=await r.json();
    const sel=$('aiProvider');
    sel.innerHTML=(d.providers||[]).map(p=>`<option value="${p.id}">${p.name}</option>`).join('');
    CHAT_PROVIDER=d.active; sel.value=d.active; syncProviderLabel();
    sel.onchange=()=>{CHAT_PROVIDER=sel.value; SESSION_TOKENS=0; syncProviderLabel(); refreshBalance();};
  }catch(e){}
  refreshBalance();
}
loadProviders();

/* ---------- экспорт ---------- */
async function post(url){const r=await fetch(url,{method:'POST',
  headers:{'Content-Type':'application/json'},
  body:JSON.stringify({spec:SPEC,model_revision:generatedRevision,
    project_file:activeProjectFile()})});
  return await r.json();}
function productionErrorText(payload){
  const reason=Array.isArray(payload&&payload.reason)?payload.reason[0]:payload&&payload.reason;
  return [payload&&payload.error,reason,payload&&payload.next_action].filter(Boolean).join(' ');
}
// После сохранения каноническое превью пересобирается скрытой MebelScene.
// Снимок текущей пользовательской камеры сюда не подходит: он может оказаться
// повёрнутым или разобранным и снова исказить карточку каталога.
async function saveSpec(){
  const r=await fetch('/api/save',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({spec:SPEC,project_file:activeProjectFile()})});
  const result=await r.json();
  if(result.ok){savedSpecJson=JSON.stringify(SPEC);syncViewportStatus();}
  if(result.ok&&!SPEC.draft){
    const file=$('projSel').value,item=CAT_ITEMS.find(value=>value.file===file);
    if(item)item.preview_current=false;
    CATALOG_PREVIEW_SEEN.delete(file);
    queueCatalogPreviews([item||{file,draft:false,preview_current:false}]);
  }
  return result;
}
function warnList(p){return (p&&Array.isArray(p.warnings)?p.warnings:[]).slice(0,3);}
$('btnSave').onclick=async()=>{const p=await saveSpec();
  toast(p.ok?('Сохранено: '+p.spec):('Ошибка: '+p.error),!p.ok);
  warnList(p).forEach(w=>toast('Сохранено с замечанием: '+w,true));
  loadVersions();};
$('btnCfrn').onclick=async()=>{const p=await post('/api/export-cfrn');
  toast(p.ok?('.cfrn: '+p.cfrn):productionErrorText(p),!p.ok);
  warnList(p).forEach(w=>toast('Экспорт с замечанием: '+w,true));};
$('btnB3d').onclick=async()=>{
  if(!lastOk){toast('Проверки не пройдены',true);return;}
  if(!confirm('Собрать .b3d через облако БАЗИС? Операция платная (~10₽).'))return;
  toast('Сборка в облаке…');
  const p=await post('/api/build-b3d');
  toast(p.ok?('Готов .b3d: '+p.b3d):productionErrorText(p),!p.ok);
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
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({project_file:activeProjectFile()})});
  const p=await r.json();
  $('verSel').innerHTML='<option value="">— версии (при сохранении) —</option>'+
    (p.versions||[]).map(v=>`<option value="${v.index}">${v.ts.replace('T',' ')} · `+
      `${v.dims}${v.n_overrides?` · правок ${v.n_overrides}`:''}</option>`).join('');
}
$('verRestore').onclick=async()=>{
  const idx=$('verSel').value;
  if(idx==='')return;
  const r=await fetch('/api/restore',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({index:+idx,project_file:activeProjectFile()})});
  const p=await r.json();
  if(p.ok){pushUndo(); SPEC=p.spec;savedSpecJson=JSON.stringify(SPEC);
    generatedSpecJson=null;generatedRevision='';scene3d.select(null); fillForm(); apply();
    toast('Восстановлена версия '+p.ts);}
  else toast('Ошибка: '+(p.error||''),true);
};
loadVersions();
loadChatHistory();

/* ---------- экспорт-центр (AKD-130) ---------- */
$('btnDeliver').onclick=async()=>{
  toast('Собираю лист согласования…');
  const p=await post('/api/deliver');
  toast(p.ok?`Лист v${p.version} открыт в браузере`:productionErrorText(p),!p.ok);};
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
SPEC_WARNINGS.forEach(w=>toast(w,true));
</script></body></html>"""
