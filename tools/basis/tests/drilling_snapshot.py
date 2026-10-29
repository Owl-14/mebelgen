"""Снимок присадок всех ParamSpec — эталон для переделки движка (MEB-167/168).

Для каждого изделия из paramspecs/: число отверстий, состав по назначению
(purpose → шт) и отпечаток полного списка (место, ось, направление, Ø,
глубина). Любое изменение присадок видно поимённо: какое изделие и какое
назначение поменялось. Меняется осознанно — пересоберите снимок:

    python -m tests.drilling_snapshot --update   (из tools/basis)
"""

from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SNAPSHOT = ROOT / "qa" / "fixtures" / "drilling_snapshot.json"


def _specs() -> list[Path]:
    return sorted(p for p in (ROOT / "paramspecs").glob("*.json")
                  if not p.name.endswith((".project.json", ".versions.json")))


def _hole_key(hole: dict[str, Any]) -> tuple:
    return (str(hole.get("purpose")), str(hole.get("panel")),
            *(round(float(hole.get(k) or 0), 1) for k in ("x", "y", "z")),
            str(hole.get("axis")), int(hole.get("dir") or 0),
            round(float(hole.get("diameter") or 0), 1), round(float(hole.get("depth") or 0), 1))


def spec_fingerprint(spec: dict[str, Any]) -> dict[str, Any]:
    from src.generators import generate_from_paramspec
    from src.hardware import compute_drilling

    holes = compute_drilling(generate_from_paramspec(spec))
    keys = sorted(_hole_key(h) for h in holes)
    digest = hashlib.sha256(json.dumps(keys, ensure_ascii=False).encode("utf-8")).hexdigest()
    return {"count": len(holes), "sha256": digest,
            "purposes": dict(sorted(Counter(str(h.get("purpose")) for h in holes).items()))}


def build_snapshot() -> dict[str, Any]:
    out: dict[str, Any] = {}
    for path in _specs():
        spec = json.loads(path.read_text(encoding="utf-8"))
        if spec.get("draft"):
            continue
        out[path.stem] = spec_fingerprint(spec)
    return out


def diff_snapshots(expected: dict[str, Any], actual: dict[str, Any]) -> list[str]:
    """Человекочитаемые отличия: новые/пропавшие изделия и сдвиги по назначению."""
    lines: list[str] = []
    for name in sorted(set(expected) | set(actual)):
        old, new = expected.get(name), actual.get(name)
        if old is None or new is None:
            lines.append(f"{name}: {'новое изделие' if old is None else 'изделие пропало'}")
            continue
        if old["sha256"] == new["sha256"]:
            continue
        changes = []
        for purpose in sorted(set(old["purposes"]) | set(new["purposes"])):
            a, b = old["purposes"].get(purpose, 0), new["purposes"].get(purpose, 0)
            if a != b:
                changes.append(f"{purpose} {a}→{b}")
        lines.append(f"{name}: отверстий {old['count']}→{new['count']}; "
                     + ("; ".join(changes) if changes else "те же количества, сдвинуты места"))
    return lines


def main() -> int:
    actual = build_snapshot()
    if "--update" in sys.argv:
        SNAPSHOT.write_text(json.dumps(actual, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"снимок обновлён: {len(actual)} изделий → {SNAPSHOT.relative_to(ROOT)}")
        return 0
    expected = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    diff = diff_snapshots(expected, actual)
    for line in diff:
        print(line)
    print(f"изделий: {len(actual)}, изменилось: {len(diff)}")
    return 1 if diff else 0


if __name__ == "__main__":
    raise SystemExit(main())
