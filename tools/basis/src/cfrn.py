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
    if orient in ("vertical",):
        return {"x": _r(sz), "y": _r(sy)}
    if orient in ("horizont", "horizontal"):
        return {"x": _r(sz), "y": _r(sx)}
    return {"x": _r(sx), "y": _r(sy)}            # front


def _matrix(orient: str, x1: float, y1: float, z1: float) -> list[float]:
    r = _ROT.get(orient, _ROT["front"])
    return [r[0][0], r[0][1], r[0][2], 0,
            r[1][0], r[1][1], r[1][2], 0,
            r[2][0], r[2][1], r[2][2], 0,
            _r(x1), _r(y1), _r(z1), 1]


def project_to_cfrn_json(project: dict[str, Any]) -> dict[str, Any]:
    name = project.get("project_name", "model")
    materials = [{"name": project.get("materials", {}).get("board_material", "ЛДСП")}]
    objects: list[dict[str, Any]] = [{"objType": 7, "name": name, "isAssemblyUnit": False}]
    children: list[dict[str, Any]] = []

    for p in project.get("panels", []):
        pl = p.get("placement")
        if not pl:
            continue
        orient = str(p.get("basis_orientation") or "front").lower()
        sx, sy, sz = pl["x2"] - pl["x1"], pl["y2"] - pl["y1"], pl["z2"] - pl["z1"]
        cont = _contour(orient, sx, sy, sz)
        idx = len(objects)
        objects.append({
            "objType": 2,
            "name": p.get("name", f"panel_{idx}"),
            "materialIndex": 0,
            "materialWidth": 0,
            "contour": {"size": cont, "pos": {"x": 0, "y": 0}},
            "thickness": _r(p.get("thickness", 16)),
            "textureOrientation": 0,
            "frontFace": 2,
            "sourceContour": {"size": cont, "pos": {"x": 0, "y": 0}},
            "clippedSourceContour": {"size": cont, "pos": {"x": 0, "y": 0}},
            "fullProductContour": {"size": cont, "pos": {"x": 0, "y": 0}},
        })
        children.append({"tableIndex": idx, "matrix": _matrix(orient, pl["x1"], pl["y1"], pl["z1"])})

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
