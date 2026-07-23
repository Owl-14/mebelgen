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

from .hardware import compute_drilling

# row-major 3×3 повороты по ориентации (из реального .cfrn БАЗИС-Облака)
_ROT = {
    "vertical": [[0, 0, 1], [0, 1, 0], [-1, 0, 0]],
    "horizont": [[1, 0, 0], [0, 0, -1], [0, 1, 0]],
    "horizontal": [[1, 0, 0], [0, 0, -1], [0, 1, 0]],
    "front": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
}

# (axis, dir) → единичный вектор сверления (в мировых осях)
_DRILL_VEC = {
    ("x", 1): {"x": 1, "y": 0, "z": 0}, ("x", -1): {"x": -1, "y": 0, "z": 0},
    ("y", 1): {"x": 0, "y": 1, "z": 0}, ("y", -1): {"x": 0, "y": -1, "z": 0},
    ("z", 1): {"x": 0, "y": 0, "z": 1}, ("z", -1): {"x": 0, "y": 0, "z": -1},
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


def _facade_material_name(m: dict[str, Any]) -> str | None:
    """Отдельный декор фасадов (AKD-260); None — фасады как корпус."""
    color = str(m.get("facade_color", "")).strip()
    code = str(m.get("facade_color_code", "")).strip()
    generic = (not color) or any(w in color.lower() for w in _GENERIC_COLOR) or color in ("—", "-")
    if generic:
        return None
    return f"{color} ({code})" if code else color


def _slot_article(project: dict[str, Any], slot: str, spec_key: str) -> str | None:
    """Артикул позиции базы для материала: явный из спеки или из резолвера."""
    art = str((project.get("materials") or {}).get(spec_key, "")).strip()
    if art:
        return art
    r = (project.get("material_refs") or {}).get(slot)
    if isinstance(r, dict) and r.get("resolved") and r.get("article"):
        return str(r["article"])
    return None


# фасадные детали — получают материал фасадов в .cfrn/.b3d (AKD-260)
_CFRN_FACADE_TYPES = {"door_front", "drawer_front", "facade", "screen"}

# Кромка (реверс round-trip b3d→cfrn файла технолога, task 13307): панель несёт
# butts[{elemIndex, materialIndex, thickness, width, clip, overhang, allowance,
# cutIndex}] по сторонам контура. Нумерация сторон прямоугольного контура:
# 0 = y=0, 1 = x=max, 2 = y=max, 3 = x=0 (против часовой от нижней).
# Маппинг наших ключей edge_banding (см. helpers.apply_edge_policy) на стороны
# НАШИХ контуров (_contour/_ROT): vertical — x конт. от ПЕРЕДА (z1);
# horizont — y конт. от ЗАДА (привязка z2, растёт к переду); front — прямая.
#   vertical: x конт. вдоль +Z от z1 → x=0 = ПЕРЕДНИЙ торец: left→3, right(зад)→1;
#   horizont: y конт. вдоль −Z от z2 → y=max = ПЕРЕД: bottom(перед)→2, top(зад)→0;
#   front: прямое соответствие.
_BUTT_SIDES = {
    "vertical": {"left": 3, "right": 1, "top": 2, "bottom": 0},
    "horizont": {"bottom": 2, "top": 0, "left": 3, "right": 1},
    "front": {"bottom": 0, "right": 1, "top": 2, "left": 3},
}


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

    # 0 = плита (реальный декор), далее задник и фасады (если отличаются)
    board_name = _board_material_name(m)
    board_entry: dict[str, Any] = {"name": board_name}
    board_art = _slot_article(project, "board", "board_article")
    if board_art:
        board_entry["art"] = board_art
    materials: list[dict[str, Any]] = [board_entry]
    back_raw = str(m.get("back_wall_material", "")).strip()
    back_idx = 0
    # задник из корпусной плиты («ЛДСП»/«ЛДСП 16») наследует декор корпуса
    # (AKD-287); отдельная запись — только для другого материала (ХДФ/ДВП)
    board_type = (str(m.get("board_material", "")).strip() or "ЛДСП")
    if back_raw and back_raw.lower() not in board_name.lower() \
            and not back_raw.lower().startswith(board_type.lower()):
        materials.append({"name": back_raw})
        back_idx = len(materials) - 1
    # AKD-260: отдельный декор фасадов доезжает до производства
    facade_idx = 0
    facade_name = _facade_material_name(m)
    if facade_name and facade_name != board_name:
        f_entry: dict[str, Any] = {"name": facade_name}
        f_art = _slot_article(project, "facade", "facade_article")
        if f_art:
            f_entry["art"] = f_art
        materials.append(f_entry)
        facade_idx = len(materials) - 1
    materials += _hardware_entries(project)   # фурнитура с артикулами (spec)

    # направление текстуры (AKD-218): across → 1, along/нет → 0
    tex_orient = 1 if str(m.get("texture_direction", "")).lower() == "across" else 0
    objects: list[dict[str, Any]] = [{"objType": 7, "name": name, "isAssemblyUnit": False}]
    children: list[dict[str, Any]] = []

    # материалы кромки (AKD-287 ф.2, реверс round-trip task 13307): видимая
    # 2 мм — резолвленная позиция базы, тонкая 0.5 — «в цвет» (как у технолога)
    edge_mat_cache: dict[float, int] = {}

    def _edge_mat(th: float) -> int:
        key = 2.0 if th >= 1 else 0.5
        if key not in edge_mat_cache:
            if key == 2.0:
                r = (project.get("material_refs") or {}).get("edge")
                if isinstance(r, dict) and r.get("resolved") and r.get("name"):
                    entry: dict[str, Any] = {"name": str(r["name"]),
                                             "sign": "2х19(в цвет)"}
                    if r.get("article"):
                        entry["art"] = str(r["article"])
                else:
                    entry = {"name": "Кромка ПВХ, 19/2 мм (в цвет)",
                             "art": "1(2х19)", "sign": "2х19(в цвет)"}
            else:
                entry = {"name": "Кромка ПВХ, 19/0,4 мм (в цвет)",
                         "art": "1(0,4х19)", "sign": "0,5х19(в цвет)"}
            materials.append(entry)
            edge_mat_cache[key] = len(materials) - 1
        return edge_mat_cache[key]

    for p in project.get("panels", []):
        pl = p.get("placement")
        if not pl:
            continue
        orient = str(p.get("basis_orientation") or "front").lower()
        sx, sy, sz = pl["x2"] - pl["x1"], pl["y2"] - pl["y1"], pl["z2"] - pl["z1"]
        cont = _contour(orient, sx, sy, sz)
        pname = str(p.get("name", "")).lower()
        if "задн" in pname or p.get("type") == "back":
            mi = back_idx
        elif facade_idx and p.get("type") in _CFRN_FACADE_TYPES:
            mi = facade_idx                       # декор фасадов (AKD-260)
        else:
            mi = 0
        idx = len(objects)
        pobj: dict[str, Any] = {
            "objType": 2,
            "name": p.get("name", f"panel_{idx}"),
            "materialIndex": mi,
            "materialWidth": 0,
            "contour": {"size": cont, "pos": {"x": 0, "y": 0}},
            "thickness": _r(p.get("thickness", 16)),
            "textureOrientation": tex_orient,
            "frontFace": 2,
            "sourceContour": {"size": cont, "pos": {"x": 0, "y": 0}},
            "clippedSourceContour": {"size": cont, "pos": {"x": 0, "y": 0}},
            "fullProductContour": {"size": cont, "pos": {"x": 0, "y": 0}},
        }
        # кромка по сторонам контура (AKD-287 ф.2) — из edge_banding панели
        eb = p.get("edge_banding") or {}
        smap = _BUTT_SIDES["vertical" if orient == "vertical" else
                           ("horizont" if orient in ("horizont", "horizontal")
                            else "front")]
        butts = []
        for k in ("bottom", "right", "top", "left"):
            th = float(eb.get(k) or 0)
            if th <= 0:
                continue
            butts.append({"elemIndex": smap[k], "materialIndex": _edge_mat(th),
                          "thickness": 2.0 if th >= 1 else 0.5, "width": 19,
                          "clip": True, "overhang": 30, "allowance": 0.5,
                          "cutIndex": -1})
        if butts:
            pobj["butts"] = butts
        objects.append(pobj)
        # компенсация направления выдавливания толщины от точки привязки:
        #   горизонталь — контур растёт в −Z → привязка по задней грани z2;
        #   вертикаль   — толщина растёт в −X → привязка по правой грани x2
        # (иначе панель уезжает на толщину: боковина x[0,16] кодируется как x[-16,0]).
        tz = pl["z2"] if orient in ("horizont", "horizontal") else pl["z1"]
        tx = pl["x2"] if orient == "vertical" else pl["x1"]
        children.append({"tableIndex": idx, "matrix": _matrix(orient, tx, pl["y1"], tz)})

    table: dict[str, Any] = {"materials": materials, "objects": objects}
    models: dict[str, str] = {}
    _encode_drilling(project, objects, children, table, models)
    _encode_hardware_bodies(project, objects, children, table, models)   # AKD-183
    _encode_catalog_hardware(project, materials, objects, children, table)
    children = _group_assemblies(project, objects, children)   # узлы ящик/дверь (AKD-137)
    # AKD-188: камера просмотрщика БАЗИС («вид спереди» и дефолт конвертации)
    # смотрит на +Z-сторону сцены — инвертируем Z (фасады к зрителю), X не
    # трогаем (секции «слева направо» как в Studio). Чекеры (cfrn_world_boxes,
    # cfrn_holes) применяют обратную инволюцию при реконструкции.
    _W0, D0 = _model_wd(project)
    for nd in _leaf_nodes(children):
        g = objects[nd["tableIndex"]]
        sy = float(g["contour"]["size"]["y"]) if "contour" in g else 0.0
        nd["matrix"] = _flip_front_matrix(nd["matrix"], sy, D0)
    return {
        "model": {"tableIndex": -1, "objs": [{"tableIndex": 0, "objs": children}]},
        "table": table,
        "modelParams": {"name": name},
        "_models": models,          # OBJ/MTL метизов — уходит в zip, не в file.json
    }


_IDENTITY = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]


def _model_wd(project: dict[str, Any]) -> tuple[float, float]:
    """Габарит модели для разворота (AKD-188): X/Z центр инволюции = bbox панелей."""
    panels = [p for p in project.get("panels", []) if isinstance(p.get("placement"), dict)]
    if not panels:
        return 0.0, 0.0
    return (max(p["placement"]["x2"] for p in panels),
            max(p["placement"]["z2"] for p in panels))


def _mat_mul(A: list[float], B: list[float]) -> list[float]:
    """C = A·B для row-major 4×4 (row-vector: сначала A, затем B)."""
    C = [0.0] * 16
    for i in range(4):
        for j in range(4):
            C[4 * i + j] = sum(A[4 * i + k] * B[4 * k + j] for k in range(4))
    return C


def _flip_front_matrix(M: list[float], sy: float, D: float) -> list[float]:
    """Разворот модели фасадами к камере просмотрщика (AKD-188): мировая
    инверсия Z (x, y, z) → (x, y, D−z) БЕЗ инверсии X — секции остаются
    «слева направо» как в ParamSpec и Studio.

    Инверсия Z сама по себе — зеркало (лево-матрицы БАЗИС не примет), поэтому
    компонуем её с СОБСТВЕННОЙ симметрией детали (локальное зеркало по Y:
    панель — прямоугольная коробка, метиз — тело вращения/центрованный бокс).
    Произведение двух отражений = поворот, матрица остаётся правой:
        M' = S_y(sy) · M · F_z(D)
    где S_y — локальное y→sy−y (sy=0 для центрованных метизов), F_z — мировое
    z→D−z. Результат: applied дважды с теми же параметрами → identity."""
    S = [1, 0, 0, 0, 0, -1, 0, 0, 0, 0, 1, 0, 0, sy, 0, 1]
    F = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, -1, 0, 0, 0, D, 1]
    C = _mat_mul(_mat_mul(S, M), F)
    return [_r(v) for v in C]


def _group_assemblies(project: dict[str, Any], objects: list[dict[str, Any]],
                      children: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Сборочные узлы как в изделиях БАЗИС (реверс GLB, AKD-137): ящики и
    двери — подузлы objType 7 isAssemblyUnit с единичной матрицей (координаты
    детей не меняются — только группировка для спецификации/структуры)."""
    panels = [p for p in project.get("panels", [])
              if isinstance(p.get("placement"), dict)]
    # child-индекс панели = её порядковый номер (панели добавляются первыми)
    name_to_child = {str(p.get("name")): i for i, p in enumerate(panels)}
    box_types = ("drawer_bottom", "drawer_side_left", "drawer_side_right", "drawer_back")
    groups: list[tuple[str, list[int]]] = []
    used: set[int] = set()

    for d in project.get("drawers", []):
        pos, dim = d.get("position") or {}, d.get("dimensions") or {}
        if not pos or not dim:
            continue
        idxs: list[int] = []
        for i, p in enumerate(panels):
            pl = p["placement"]
            cx, cy = (pl["x1"] + pl["x2"]) / 2, (pl["y1"] + pl["y2"]) / 2
            in_box = (pos["x"] - 20 <= cx <= pos["x"] + dim["width"] + 20
                      and pos["y"] - 5 <= cy <= pos["y"] + dim["height"] + 5)
            if p.get("type") in box_types and in_box:
                idxs.append(i)
            elif p.get("type") == "drawer_front" and i not in used \
                    and pl["y1"] - 2 <= pos["y"] <= pl["y2"] + 2 \
                    and pl["x1"] - 20 <= pos["x"] <= pl["x2"] + 20:
                idxs.append(i)
        idxs = [i for i in idxs if i not in used]
        if idxs:
            groups.append((f"Ящик {d.get('id', len(groups) + 1)}", idxs))
            used.update(idxs)

    for i, p in enumerate(panels):
        if p.get("type") == "door_front" and i not in used:
            groups.append((f"Дверь: {p.get('name')}", [i]))
            used.add(i)

    if not groups:
        return children
    out = [c for i, c in enumerate(children) if i >= len(panels) or i not in used]
    for gname, idxs in groups:
        gi = len(objects)
        objects.append({"objType": 7, "name": gname, "isAssemblyUnit": True})
        out.append({"tableIndex": gi, "matrix": list(_IDENTITY),
                    "objs": [children[i] for i in idxs]})
    return out


def _encode_drilling(project: dict[str, Any], objects: list[dict[str, Any]],
                     children: list[dict[str, Any]], table: dict[str, Any],
                     models: dict[str, str] | None = None) -> None:
    """Присадки + 3D-тела метизов (AKD-168) — по механике эталона
    native_cabinet.cfrn: объект objType 5 на ТИПОРАЗМЕР метиза с материалом
    (имя+артикул для спецификации), triangleData → OBJ в models/,
    локальные holes {pos, dir, infoIndex}, инстансы узлами с матрицами.

    Присадки считает hardware.compute_drilling. Ошибка не ломает сборку .cfrn —
    модель просто идёт без присадок."""
    try:
        drill = compute_drilling(project)
    except Exception:
        return
    if not drill:
        return
    # артикулы позиций крепежа из BOM (для материалов-строк спецификации)
    arts: dict[str, str] = {}
    try:
        from .hardware import fastener_bom
        for b in fastener_bom(drill, resolve=True):
            if b.get("article"):
                arts[b["name"]] = str(b["article"])
    except Exception:
        pass

    from .fasteners3d import build_fastener_objects
    catalog: list[dict[str, Any]] = table.setdefault("holes", [])
    cat_index: dict[tuple[float, float, int], int] = {}
    triangles: list[str] = table.setdefault("triangles", [])
    materials: list[dict[str, Any]] = table["materials"]
    mat_index = {m.get("name"): i for i, m in enumerate(materials)}

    def _info(depth: float, dia: float) -> int:
        key = (_r(depth), _r(dia), 1)                 # drillMode 1 — как в эталоне
        if key not in cat_index:
            cat_index[key] = len(catalog)
            catalog.append({"depth": key[0], "diameter": key[1], "drillMode": key[2]})
        return cat_index[key]

    for fo in build_fastener_objects(drill, arts):
        mname = fo["name"]
        if mname not in mat_index:
            mat_index[mname] = len(materials)
            mat = {"name": mname}
            if fo.get("art"):
                mat["art"] = fo["art"]
            materials.append(mat)
        tri_idx = len(triangles)
        triangles.append(fo["obj_name"])
        if models is not None:
            models[f"models/{fo['obj_name']}"] = fo["obj_text"]
            models[f"models/{fo['obj_name'][:-4]}.mtl"] = fo["mtl_text"]
        obj_holes = [{"pos": h["pos"], "dir": h["dir"],
                      "infoIndex": _info(h["depth"], h["diameter"])}
                     for h in fo["holes"]]
        idx = len(objects)
        objects.append({"objType": 5, "materialIndex": mat_index[mname],
                        "triangleData": tri_idx, "holes": obj_holes})
        for mtx in fo["instances"]:
            children.append({"tableIndex": idx, "matrix": mtx})


def _encode_hardware_bodies(project: dict[str, Any], objects: list[dict[str, Any]],
                            children: list[dict[str, Any]], table: dict[str, Any],
                            models: dict[str, str] | None = None) -> None:
    """Тела фурнитуры (AKD-183): штанга, держатели, опоры, металлокаркас —
    та же механика, что у метизов (_encode_drilling): objType 5 + OBJ в
    models/ + инстансы матрицами. Без них .b3d терял штангу и опоры,
    которые Studio уже показывает."""
    try:
        from .fasteners3d import _BODY_KINDS, build_hardware_bodies
        bodies = build_hardware_bodies(project)
    except Exception:
        return
    if not bodies:
        return
    # артикулы из производственной базы (опционально, офлайн-безопасно)
    arts: dict[str, str] = {}
    try:
        from .materials import search_base
        for label, query, _c, _s in _BODY_KINDS.values():
            hit = search_base(query, limit=1)
            if hit and hit[0].get("article"):
                arts[label] = str(hit[0]["article"])
    except Exception:
        pass
    triangles: list[str] = table.setdefault("triangles", [])
    materials: list[dict[str, Any]] = table["materials"]
    mat_index = {m.get("name"): i for i, m in enumerate(materials)}
    for bo in bodies:
        mname = bo["name"]
        if mname not in mat_index:
            mat_index[mname] = len(materials)
            mat = {"name": mname}
            art = arts.get(bo.get("label", "")) or bo.get("art")
            if art:
                mat["art"] = str(art)
            materials.append(mat)
        tri_idx = len(triangles)
        triangles.append(bo["obj_name"])
        if models is not None:
            models[f"models/{bo['obj_name']}"] = bo["obj_text"]
            models[f"models/{bo['obj_name'][:-4]}.mtl"] = bo["mtl_text"]
        idx = len(objects)
        objects.append({"objType": 5, "materialIndex": mat_index[mname],
                        "triangleData": tri_idx, "holes": []})
        for mtx in bo["instances"]:
            children.append({"tableIndex": idx, "matrix": mtx})


# поворот экземпляра фурнитуры на фронте (из эталона: нормаль наружу, −Z)
_HW_ROT = [-1, 0, 0, 0, 0, 1, 0, 0, 0, 0, -1, 0]


def _encode_catalog_hardware(project: dict[str, Any], materials: list[dict[str, Any]],
                             objects: list[dict[str, Any]], children: list[dict[str, Any]],
                             table: dict[str, Any]) -> None:
    """КАТАЛОЖНАЯ фурнитура в .cfrn (эксперимент, включается project['catalog_hardware']).

    Схема эталона native_cabinet.cfrn: .cfrn НЕ хранит меш — только ссылку:
      objType:5 {materialIndex, triangleData: <индекс в table.triangles>, holes} +
      table.triangles ["<имя из каталога БАЗИС>.obj"] + матрица экземпляра.
    БАЗИС резолвит меш по ИМЕНИ из своего каталога при импорте. Требование: имя
    должно существовать в каталоге БАЗИС (иначе объект без геометрии)."""
    cfg = project.get("catalog_hardware")
    if not cfg:
        return
    # имя каталожной ручки: явное из конфига или из material_refs.handles
    name = None
    if isinstance(cfg, dict):
        name = cfg.get("handle_name")
    if not name:
        r = (project.get("material_refs") or {}).get("handles") or {}
        c = r.get("candidates")
        if isinstance(c, list) and c and isinstance(c[0], dict):
            name = c[0].get("name")
    if not name:
        return
    # материал ручки (name+art) — найти или добавить
    mi = next((i for i, m in enumerate(materials) if m.get("name") == name), None)
    if mi is None:
        mi = len(materials)
        entry = {"name": name}
        if isinstance(cfg, dict) and cfg.get("art"):
            entry["art"] = str(cfg["art"])
        materials.append(entry)
    tri = table.setdefault("triangles", [])
    obj_name = f"{name}.obj"
    ti = tri.index(obj_name) if obj_name in tri else (tri.append(obj_name) or len(tri) - 1)
    # один объект-ручка, экземпляры матрицами (как 4 ручки в эталоне)
    oi = len(objects)
    objects.append({"objType": 5, "materialIndex": mi, "triangleData": ti, "holes": []})
    # точки ручек: ящик — центр X у верхней кромки; дверь — у кромки открывания
    handles = (project.get("hardware") or {}).get("handles") or {}
    off = float(handles.get("offset_from_top", 40))
    for p in project.get("panels", []):
        pl = p.get("placement")
        t = p.get("type")
        if not pl or t not in ("drawer_front", "door_front"):
            continue
        zf = min(pl["z1"], pl["z2"])                       # внешняя плоскость фасада
        if t == "drawer_front":
            hx, hy = (pl["x1"] + pl["x2"]) / 2, pl["y2"] - off
        else:
            nm = str(p.get("name", "")).lower()
            hx = pl["x1"] + 40 if "прав" in nm else pl["x2"] - 40
            hy = (pl["y1"] + pl["y2"]) / 2
        children.append({"tableIndex": oi,
                         "matrix": _HW_ROT + [_r(hx), _r(hy), _r(zf), 1]})


def project_to_cfrn_bytes(project: dict[str, Any]) -> bytes:
    doc = project_to_cfrn_json(project)
    models = doc.pop("_models", {})
    data = json.dumps(doc, ensure_ascii=False).encode("utf-8")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("file.json", data)
        for path, text in models.items():
            z.writestr(path, text.encode("utf-8"))
    return buf.getvalue()


def _leaf_nodes(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Листовые узлы модели (панели/метизы), сборочные узлы — насквозь.

    Матрицы сборочных узлов у нас единичные (группировка без трансформации),
    поэтому листовые матрицы остаются мировыми."""
    out: list[dict[str, Any]] = []
    for n in nodes:
        if n.get("objs"):
            out.extend(_leaf_nodes(n["objs"]))
        else:
            out.append(n)
    return out


def cfrn_world_boxes(project: dict[str, Any]) -> list[dict[str, Any]]:
    """Мировые AABB панелей, реконструированные ИЗ матриц .cfrn — как их строит
    БАЗИС при сборке .b3d. Позволяет проверить кодирование, а не только placement
    (совпадает с обратной выгрузкой b3d→cfrn из облака до миллиметра)."""
    d = project_to_cfrn_json(project)
    tobjs = d["table"]["objects"]
    nodes = _leaf_nodes(d["model"]["objs"][0]["objs"])
    W0, D0 = _model_wd(project)                 # обратная инволюция AKD-188

    def xf(M: list[float], p: tuple[float, float, float]) -> tuple[float, float, float]:
        px, py, pz = p
        return (px * M[0] + py * M[4] + pz * M[8] + M[12],
                px * M[1] + py * M[5] + pz * M[9] + M[13],
                px * M[2] + py * M[6] + pz * M[10] + M[14])

    out: list[dict[str, Any]] = []
    for nd in nodes:
        g = tobjs[nd["tableIndex"]]
        if "contour" not in g:      # objType 5 (присадки) — не панель, пропускаем
            continue
        sz = g["contour"]["size"]
        th = g.get("thickness", 16)
        M = nd["matrix"]
        corners = [(x, y, z) for x in (0, sz["x"]) for y in (0, sz["y"]) for z in (0, th)]
        ws = [xf(M, c) for c in corners]
        xs = [w[0] for w in ws]                 # обратная инволюция: только Z
        ys = [w[1] for w in ws]
        zs = [D0 - w[2] for w in ws]
        out.append({"name": g.get("name"), "x1": min(xs), "x2": max(xs),
                    "y1": min(ys), "y2": max(ys), "z1": min(zs), "z2": max(zs)})
    return out


def cfrn_holes(project: dict[str, Any]) -> list[dict[str, Any]]:
    """Мировые присадки, реконструированные ИЗ .cfrn: все объекты-метизы
    (objType 5) × их инстансы (узлы с матрицами) × локальные отверстия.
    Для сверки с compute_drilling."""
    d = project_to_cfrn_json(project)
    tobjs = d["table"]["objects"]
    nodes = _leaf_nodes(d["model"]["objs"][0]["objs"])
    catalog = d["table"].get("holes", [])
    W0, D0 = _model_wd(project)                 # обратная инволюция AKD-188

    out: list[dict[str, Any]] = []
    for node in nodes:
        obj = tobjs[node["tableIndex"]]
        if obj.get("objType") != 5 or not obj.get("holes"):
            continue
        M = node["matrix"]

        def xf(p: dict[str, float], translate: bool = True):
            px, py, pz = p["x"], p["y"], p["z"]
            return (px * M[0] + py * M[4] + pz * M[8] + (M[12] if translate else 0),
                    px * M[1] + py * M[5] + pz * M[9] + (M[13] if translate else 0),
                    px * M[2] + py * M[6] + pz * M[10] + (M[14] if translate else 0))

        for h in obj.get("holes", []):
            w0 = xf(h["pos"])
            d0 = xf(h["dir"], translate=False)        # направление — только поворот
            w = (w0[0], w0[1], D0 - w0[2])            # обратная инволюция: только Z
            dv = (d0[0], d0[1], -d0[2])
            cat = catalog[h["infoIndex"]] if h["infoIndex"] < len(catalog) else {}
            out.append({"x": w[0], "y": w[1], "z": w[2],
                        "dir": {"x": round(dv[0], 3), "y": round(dv[1], 3), "z": round(dv[2], 3)},
                        "diameter": cat.get("diameter"), "depth": cat.get("depth")})
    return out


def check_cfrn_holes(project: dict[str, Any], *, tol: float = 0.5) -> list[str]:
    """Сверяет присадки, закодированные в .cfrn, с compute_drilling (позиция,
    диаметр, глубина). Пусто = кодирование присадок корректно. Наличие присадок в
    самом .b3d подтверждается обратной выгрузкой b3d→cfrn из облака (round-trip)."""
    src = compute_drilling(project)
    enc = cfrn_holes(project)
    issues: list[str] = []
    if len(src) != len(enc):
        issues.append(f"число присадок: compute={len(src)} ≠ .cfrn={len(enc)}")
        return issues

    def _svec(s: dict[str, Any]) -> tuple[float, float, float]:
        v = _DRILL_VEC[(s["axis"], int(s["dir"]))]
        return (v["x"], v["y"], v["z"])

    # метизы группируются по типам → порядок другой: сверка как мультимножество
    def _key(x, y, z, d, dep, vx, vy, vz):
        return (round(x, 1), round(y, 1), round(z, 1), round(d, 1), round(dep, 1),
                round(vx, 2), round(vy, 2), round(vz, 2))

    s_keys = sorted(_key(s["x"], s["y"], s["z"], s["diameter"], s["depth"], *_svec(s))
                    for s in src)
    e_keys = sorted(_key(e["x"], e["y"], e["z"], e["diameter"], e["depth"],
                         e["dir"]["x"], e["dir"]["y"], e["dir"]["z"]) for e in enc)
    for sk, ek in zip(s_keys, e_keys):
        if any(abs(a - b) > tol for a, b in zip(sk[:3], ek[:3])) or sk[3:] != ek[3:]:
            issues.append(f".cfrn {ek} ≠ compute {sk}")
            if len(issues) >= 10:
                break
    return issues


def check_cfrn_encoding(project: dict[str, Any], *, tol: float = 0.5) -> list[str]:
    """Сверяет мировые AABB из .cfrn с placement и ловит пересечения на уровне
    кодирования (толщина панели должна выдавливаться в нужную сторону). Пусто = ок.

    Именно этот класс багов placement-чек НЕ видит: placement может быть корректен,
    а матрица .cfrn — уводить деталь на толщину, создавая нахлёсты в самом .b3d."""
    boxes = cfrn_world_boxes(project)
    panels = [p for p in project.get("panels", []) if p.get("placement")]
    issues: list[str] = []
    # сопоставление по имени: сборочные узлы (AKD-137) меняют порядок узлов
    by_name = {b["name"]: b for b in boxes}
    for p in panels:
        b = by_name.get(p.get("name"))
        if b is None:
            issues.append(f"{p.get('name')}: нет узла в .cfrn")
            continue
        pl = p["placement"]
        for ax in ("x", "y", "z"):
            if abs(b[ax + "1"] - pl[ax + "1"]) > tol or abs(b[ax + "2"] - pl[ax + "2"]) > tol:
                issues.append(
                    f"{p.get('name')}: .cfrn {ax}[{b[ax+'1']:.1f},{b[ax+'2']:.1f}] "
                    f"≠ placement {ax}[{pl[ax+'1']},{pl[ax+'2']}]")
    n = len(boxes)
    for i in range(n):
        a = boxes[i]
        for j in range(i + 1, n):
            c = boxes[j]
            ox = min(a["x2"], c["x2"]) - max(a["x1"], c["x1"])
            oy = min(a["y2"], c["y2"]) - max(a["y1"], c["y1"])
            oz = min(a["z2"], c["z2"]) - max(a["z1"], c["z1"])
            if ox > tol and oy > tol and oz > tol:
                issues.append(
                    f"пересечение в .cfrn: {a['name']} × {c['name']} "
                    f"на {ox:.1f}×{oy:.1f}×{oz:.1f} мм")
    return issues
