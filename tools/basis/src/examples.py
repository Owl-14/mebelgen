"""Подбор похожих примеров (few-shot/RAG) для промпта извлечения ParamSpec.

Простой ретривер без векторной базы: ранжирует принятые ParamSpec по близости
архетипа, габаритного класса и ключевых слов типа. Достаточно на старте (AKD-33);
эмбеддинги можно подключить позже, не меняя интерфейс.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES_DIR = ROOT / "paramspecs"


def _load(dir_: Path) -> list[dict[str, Any]]:
    out = []
    for p in sorted(dir_.glob("*.json")):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            continue
    return out


def _size_class(dim: dict[str, Any]) -> tuple[int, int, int]:
    def b(v: float) -> int:
        return int((v or 0) // 400)
    return (b(dim.get("width", 0)), b(dim.get("depth", 0)), b(dim.get("height", 0)))


def score(query: dict[str, Any], cand: dict[str, Any]) -> float:
    s = 0.0
    if query.get("archetype") and query["archetype"] == cand.get("archetype"):
        s += 3.0
    qt = (query.get("furniture_type") or "").lower()
    ct = (cand.get("furniture_type") or "").lower()
    if qt and qt == ct:
        s += 1.5
    if _size_class(query.get("dimensions", {})) == _size_class(cand.get("dimensions", {})):
        s += 1.0
    qn = len(query.get("sections", []))
    cn = len(cand.get("sections", []))
    if qn and qn == cn:
        s += 0.5
    return s


def find_similar(query: dict[str, Any], k: int = 3, *, examples_dir: Path | None = None) -> list[dict[str, Any]]:
    """Топ-K похожих ParamSpec (по убыванию похожести), исключая точную копию по имени."""
    cands = _load(examples_dir or EXAMPLES_DIR)
    qname = query.get("project_name")
    ranked = sorted(
        ((score(query, c), c) for c in cands if c.get("project_name") != qname),
        key=lambda t: t[0], reverse=True,
    )
    return [c for sc, c in ranked[:k] if sc > 0]


def as_prompt_block(examples: list[dict[str, Any]]) -> str:
    """Подмешиваемый в промпт блок few-shot примеров (компактно)."""
    if not examples:
        return ""
    parts = ["Похожие принятые примеры (формат и метод, не копировать числа):"]
    for ex in examples:
        parts.append(json.dumps(ex, ensure_ascii=False))
    return "\n\n".join(parts)
