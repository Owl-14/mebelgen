"""Генерация .cfrn из нашего project.json (панели) — для облачной CfrnToB3d.

.cfrn = zip с file.json: сцена БАЗИС {model, table, modelParams}.
Деталь = contour.size + thickness + material; размещение = матрица 4×4.
Три ориентации (vertical/horizont/front) ↔ три поворота (реверс-инжиниринг
реального .cfrn из облака). Это даёт device-independent путь:
ParamSpec → панели → .cfrn → облако CfrnToB3d → .b3d (без десктопа).
"""

from __future__ import annotations

import io
import json
import zipfile
from typing import Any

# row-major 3×3 повороты по ориентации (из реального .cfrn БАЗИС-Облака)
_ROT = {
    "vertical": [[0, 0, 1], [0, 1, 0], [-1, 0, 0]],
    "horizont": [[1, 0, 0], [0, 0, -1], [0, 1, 0]],
    "horizontal": [[1, 0, 0], [0, 0, -1], [0, 1, 0]],
    "front": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
}


def _r(v: float) -> float:
    v = round(float(v), 3)
    return int(v) if v == int(v) else v


def _contour(orient: str, sx: float, sy: float, sz: float) -> dict[str, float]:
    # Контур и трансляция сверены с ЭТАЛОНОМ РОДНОГО шкафа БАЗИС (Мебельщик →
    # экспорт .cfrn, AutoSave18), который облако пересобирает корректно. Это
    # ground-truth, под который должен попадать наш .cfrn для верного импорта:
    #   vertical : {x: глубина(sz), y: высота(sy)},  trans.z = z1
    #   horizont : {x: ширина(sx),  y: глубина(sz)},  trans.z = z2  (см. _z_for)
    #   front    : {x: ширина(sx),  y: высота(sy)},   trans.z = z1
    if orient in ("vertical",):
        return {"x": _r(sz), "y": _r(sy)}
    if orient in ("horizont", "horizontal"):
        return {"x": _r(sx), "y": _r(sz)}
    return {"x": _r(sx), "y": _r(sy)}            # front


def _matrix(orient: str, x1: float, y1: float, z1: float) -> list[float]:
    r = _ROT.get(orient, _ROT["front"])
    return [r[0][0], r[0][1], r[0][2], 0,
            r[1][0], r[1][1], r[1][2], 0,
            r[2][0], r[2][1], r[2][2], 0,
            _r(x1), _r(y1), _r(z1), 1]


_GENERIC_COLOR = ("соглас", "уточн", "не задан", "любой")


def _board_material_name(m: dict[str, Any]) -> str:
    """Имя материала плиты в формате БАЗИС: «Декор (Код)», иначе тип плиты."""
    color = str(m.get("color", "")).strip()
    code = str(m.get("color_code", "")).strip()
    board = (str(m.get("board_material", "")).strip() or "ЛДСП")
    generic = (not color) or any(w in color.lower() for w in _GENERIC_COLOR) or color in ("—", "-")
    if not generic:
        return f"{color} ({code})" if code else color
    return board


def _hardware_entries(project: dict[str, Any]) -> list[dict[str, Any]]:
    """Фурнитура из material_refs → записи материала с артикулом (как в родном .cfrn)."""
    refs = project.get("material_refs") or {}
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for slot in ("handles", "drawer_guides", "hinges", "legs", "locks"):
        r = refs.get(slot)
        if not isinstance(r, dict) or not r.get("resolved"):
            continue
        cand = None
        c = r.get("candidates")
        if isinstance(c, list) and c and isinstance(c[0], dict):
            cand = c[0]
        elif r.get("name"):
            cand = {"name": r.get("name"), "article": r.get("article")}
        if not cand or not cand.get("name") or cand["name"] in seen:
            continue
        seen.add(cand["name"])
        entry = {"name": cand["name"]}
        if cand.get("article"):
            entry["art"] = str(cand["article"])
        out.append(entry)
    return out


def project_to_cfrn_json(project: dict[str, Any]) -> dict[str, Any]:
    name = project.get("project_name", "model")
    m = project.get("materials", {}) or {}

    # 0 = плита (реальный декор), 1 = задник (если материал отличается)
    board_name = _board_material_name(m)
    materials: list[dict[str, Any]] = [{"name": board_name}]
    back_raw = str(m.get("back_wall_material", "")).strip()
    back_idx = 0
    if back_raw and back_raw.lower() not in board_name.lower():
        materials.append({"name": back_raw})
        back_idx = len(materials) - 1
    materials += _hardware_entries(project)   # фурнитура с артикулами (spec)

    objects: list[dict[str, Any]] = [{"objType": 7, "name": name, "isAssemblyUnit": False}]
    children: list[dict[str, Any]] = []

    for p in project.get("panels", []):
        pl = p.get("placement")
        if not pl:
            continue
        orient = str(p.get("basis_orientation") or "front").lower()
        sx, sy, sz = pl["x2"] - pl["x1"], pl["y2"] - pl["y1"], pl["z2"] - pl["z1"]
        cont = _contour(orient, sx, sy, sz)
        pname = str(p.get("name", "")).lower()
        mi = back_idx if ("задн" in pname or p.get("type") == "back") else 0
        idx = len(objects)
        objects.append({
            "objType": 2,
            "name": p.get("name", f"panel_{idx}"),
            "materialIndex": mi,
            "materialWidth": 0,
            "contour": {"size": cont, "pos": {"x": 0, "y": 0}},
            "thickness": _r(p.get("thickness", 16)),
            "textureOrientation": 0,
            "frontFace": 2,
            "sourceContour": {"size": cont, "pos": {"x": 0, "y": 0}},
            "clippedSourceContour": {"size": cont, "pos": {"x": 0, "y": 0}},
            "fullProductContour": {"size": cont, "pos": {"x": 0, "y": 0}},
        })
        # горизонталь: контур растёт в −Z от точки привязки → привязка по задней грани z2
        tz = pl["z2"] if orient in ("horizont", "horizontal") else pl["z1"]
        children.append({"tableIndex": idx, "matrix": _matrix(orient, pl["x1"], pl["y1"], tz)})

    return {
        "model": {"tableIndex": -1, "objs": [{"tableIndex": 0, "objs": children}]},
        "table": {"materials": materials, "objects": objects},
        "modelParams": {"name": name},
    }


def project_to_cfrn_bytes(project: dict[str, Any]) -> bytes:
    data = json.dumps(project_to_cfrn_json(project), ensure_ascii=False).encode("utf-8")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("file.json", data)
    return buf.getvalue()
