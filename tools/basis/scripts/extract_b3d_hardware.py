"""Экстрактор крепежа/панелей из .b3d (облачного и десктопного).

Печатает: библиотеку крепежа (FurnList: шаблоны отверстий, ParamType/Datum),
инстансы крепежа (Type 3001: мировая позиция + ось), панели (Type 4002:
материал, толщина, кромка по сторонам). Основной инструмент реверса правок
технолога: см. rules/b3d_production_reference.md.

Запуск: python scripts/extract_b3d_hardware.py <файл.b3d> [--json out.json]
"""

from __future__ import annotations

import argparse
import collections
import io
import json
import sys
from pathlib import Path

# консоль Windows по умолчанию cp1251 — вывод содержит «×» и юникод-имена
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.b3d_format import parse_b3d  # noqa: E402


def _get(node, name):
    if node and node[1] == "obj":
        for c in node[2]:
            if c[0] == name:
                return c
    return None


def _val(node, name, default=None):
    c = _get(node, name)
    return c[2] if c else default


def _qmul(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw,
            aw * bw - ax * bx - ay * by - az * bz)


def _qrot(q, v):
    r = _qmul(_qmul(q, (v[0], v[1], v[2], 0.0)), (-q[0], -q[1], -q[2], q[3]))
    return (round(r[0], 3), round(r[1], 3), round(r[2], 3))


def _trans(o):
    t = _get(o, "Trans")
    if not t:
        return (0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0)
    return ((_val(t, "X", 0.0), _val(t, "Y", 0.0), _val(t, "Z", 0.0)),
            (_val(t, "Rx", 0.0), _val(t, "Ry", 0.0), _val(t, "Rz", 0.0), _val(t, "Rw", 1.0)))


def extract(path: str) -> dict:
    parsed = parse_b3d(Path(path).read_bytes())
    doc = None
    for _flag, nodes in parsed["sections"]:
        for n in (nodes if isinstance(nodes, list) else [nodes]):
            if n[0] == "Document":
                doc = n
    if doc is None:
        raise SystemExit("Document не найден")

    lib: dict[int, dict] = {}
    fl = _get(doc, "FurnList")
    for f in (fl[2] if fl else []):
        holes = []
        for h in (_get(f, "Holes")[2] if _get(f, "Holes") else []):
            holes.append({"pos": [_val(h, "X", 0), _val(h, "Y", 0), _val(h, "Z", 0)],
                          "dir": [_val(h, "DirX", 0), _val(h, "DirY", 0), _val(h, "DirZ", 0)],
                          "d": 2 * _val(h, "Radius", 0), "depth": _val(h, "Depth", 0),
                          "drillMode": _val(h, "DrillMode")})
        lib[_val(f, "FastID")] = {
            "name": str(_val(f, "Name", "?")),
            "paramType": _val(f, "ParamType"), "datumMode": _val(f, "DatumMode"),
            "paramOffset": _val(f, "ParamOffset"), "color": _val(f, "Color"),
            "hasMesh": _get(f, "TriData") is not None, "holes": holes,
        }

    instances, panels = [], []

    def walk(o, ppos, pq, path):
        pos, q = _trans(o)
        rp = _qrot(pq, pos)
        wpos = tuple(round(ppos[i] + rp[i], 2) for i in range(3))
        wq = _qmul(pq, q)
        t = _val(o, "Type")
        nm = str(_val(o, "Name", "")).split("\r")[0]
        if t == 3001:
            e = lib.get(_val(o, "FastID"), {})
            instances.append({"name": e.get("name", nm).split("\r")[0],
                              "fastId": _val(o, "FastID"),
                              "pos": list(wpos), "axisX": list(_qrot(wq, (1, 0, 0))),
                              "assembly": path})
        elif t == 4002:
            butts = [{"elem": _val(b, "Elem"), "thick": _val(b, "Thick"),
                      "mat": str(_val(b, "Mat", ""))}
                     for b in (_get(o, "Butts")[2] if _get(o, "Butts") else [])]
            panels.append({"name": nm, "thick": _val(o, "Thick"),
                           "mat": str(_val(o, "Mat", "")), "pos": list(wpos),
                           "quat": [round(x, 3) for x in wq], "butts": butts,
                           "assembly": path})
        objs = _get(o, "Objs")
        if objs:
            for c in objs[2]:
                if c[0] == "Obj":
                    walk(c, wpos, wq, path + "/" + nm)

    model = _get(doc, "Model")
    for o in (model[2] if model else []):
        if o[0] == "Obj":
            walk(o, (0, 0, 0), (0, 0, 0, 1), "")

    return {"library": {str(k): v for k, v in lib.items()},
            "instances": instances, "panels": panels}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("b3d")
    ap.add_argument("--json", help="сохранить полный экстракт в JSON")
    a = ap.parse_args()
    data = extract(a.b3d)
    print(f"библиотека крепежа: {len(data['library'])} записей; "
          f"инстансов: {len(data['instances'])}; панелей: {len(data['panels'])}")
    by = collections.Counter(i["name"] for i in data["instances"])
    for nm, n in by.most_common():
        print(f"  {n:4d} × {nm}")
    if a.json:
        Path(a.json).write_text(json.dumps(data, ensure_ascii=False, indent=1),
                                encoding="utf-8")
        print("JSON:", a.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
