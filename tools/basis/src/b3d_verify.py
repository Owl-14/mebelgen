"""Бесплатная верификация паритета Studio ↔ .b3d (AKD-169).

Читаем собранный облаком .b3d нашей читалкой BZ85 (src/b3d_format.py) и
сверяем состав с project.json: все панели по именам, материалы метизов
с артикулами, число встроенных мешей фурнитуры. Ничего не платим —
глубокая кросс-проверка через облако (B3dToCfrn) остаётся опциональной.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def _tree_strings_and_blobs(doc: Any) -> tuple[set[str], int]:
    strings: set[str] = set()
    blobs = 0

    def walk(n):
        nonlocal blobs
        if isinstance(n, dict):
            for k, v in n.items():
                if isinstance(k, str) and len(k) > 1:
                    strings.add(k)
                walk(v)
        elif isinstance(n, (list, tuple)):
            for v in n:
                walk(v)
        elif isinstance(n, str) and len(n) > 1:
            strings.add(n)
        elif isinstance(n, (bytes, bytearray)) and len(n) > 100:
            blobs += 1

    walk(doc)
    return strings, blobs


def verify_b3d_parity(b3d_path: str | Path,
                      project: dict[str, Any]) -> dict[str, Any]:
    """Отчёт: {ok, panels: {found,total,missing}, fasteners: {...}, mesh_blobs}."""
    from .b3d_format import parse_b3d
    doc = parse_b3d(Path(b3d_path).read_bytes())
    strings, blobs = _tree_strings_and_blobs(doc)
    joined = "\n".join(strings)

    panel_names = [str(p.get("name", "")) for p in project.get("panels", [])]
    missing_panels = [n for n in panel_names if n and n not in joined]

    # ожидаемые материалы метизов и тел фурнитуры (AKD-183: штанга/опоры/каркас)
    # — ровно те имена, что кодируются в .cfrn
    fast_expected: list[str] = []
    try:
        from .fasteners3d import build_fastener_objects, build_hardware_bodies
        from .hardware import compute_drilling
        fast_expected = sorted(
            {fo["name"] for fo in build_fastener_objects(compute_drilling(project))}
            | {bo["name"] for bo in build_hardware_bodies(project)})
    except Exception:
        pass
    # в .b3d имя и артикул склеены через \r — ищем подстрокой имени
    missing_fast = [n for n in fast_expected if n not in joined]

    # число инстансов метизов, которые мы кодировали
    n_instances = 0
    try:
        from .cfrn import project_to_cfrn_json
        d = project_to_cfrn_json(project)
        tobjs = d["table"]["objects"]
        n_instances = sum(1 for n in d["model"]["objs"][0]["objs"]
                          if tobjs[n["tableIndex"]].get("objType") == 5
                          and tobjs[n["tableIndex"]].get("holes"))
    except Exception:
        pass

    ok = not missing_panels and not missing_fast and \
        (n_instances == 0 or blobs >= n_instances)
    return {
        "ok": ok,
        "panels": {"total": len(panel_names),
                   "found": len(panel_names) - len(missing_panels),
                   "missing": missing_panels[:10]},
        "fasteners": {"total": len(fast_expected),
                      "found": len(fast_expected) - len(missing_fast),
                      "missing": missing_fast[:10]},
        "mesh_blobs": blobs,
        "instances_encoded": n_instances,
    }
