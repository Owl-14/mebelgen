"""Оркестратор пайплайна (AKD-10): ParamSpec → генерация → валидаторы → само-ревью.

Единая точка входа. «AI»-шаги (ТЗ→ParamSpec, vision-review рендера) — плагины:
их выполняет любая модель (OpenAI, Claude, ассистент в чате). Детерминированный
backbone здесь: генерация координат, проверки, авторемонт, структурный отчёт.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .consistency_check import check_consistency
from .finish_project import finish_project
from .generators import generate_from_paramspec
from .paramspec import validate_paramspec


@dataclass
class OrchestratorResult:
    project: dict[str, Any]
    schema_ok: bool
    geometry_ok: bool
    consistency_count: int
    review: list[str] = field(default_factory=list)
    fixes: list[str] = field(default_factory=list)
    saved_to: Path | None = None

    @property
    def ok(self) -> bool:
        return self.schema_ok and self.geometry_ok and self.consistency_count == 0

    def report(self) -> str:
        lines = [f"Изделие: {self.project.get('project_name')}",
                 f"Панелей: {len(self.project.get('panels', []))}",
                 f"Проверки: схема={'OK' if self.schema_ok else 'ОШИБКА'}, "
                 f"геометрия={'OK' if self.geometry_ok else 'ОШИБКА'}, "
                 f"согласованность={self.consistency_count}"]
        if self.fixes:
            lines.append("Автоисправления: " + "; ".join(self.fixes))
        if self.review:
            lines.append("На проверку человеку:")
            lines += [f"  • {r}" for r in self.review]
        if self.saved_to:
            lines.append(f"Сохранено: {self.saved_to}")
        return "\n".join(lines)


def self_review(project: dict[str, Any]) -> list[str]:
    """Правило-ориентированное ревью: что должен досмотреть человек/AI."""
    out: list[str] = []
    mats = project.get("materials", {})
    color = str(mats.get("color", "")).lower()
    if not color or "согласован" in color or "уточн" in color:
        out.append("Цвет/материал не зафиксирован — уточнить перед производством.")

    panels = project.get("panels", [])
    est = sum(1 for p in panels if p.get("estimated"))
    if est:
        out.append(f"{est} панелей с оценочными размерами (estimated) — сверить с чертежом.")

    hw = project.get("hardware", {})
    has_fronts = any(p.get("type") in ("door_front", "drawer_front") for p in panels)
    if has_fronts and (hw.get("handles", {}) or {}).get("count", 0) == 0:
        out.append("Фасады есть, ручки count=0 (push/уточнить) — подтвердить тип открывания.")

    has_drawers = any(p.get("type") == "drawer_front" for p in panels)
    if has_drawers and not hw.get("drawer_guides"):
        out.append("Есть ящики, но drawer_guides не заданы — выбрать направляющие в БАЗИС.")

    if any(p.get("shape") in ("circle", "cylinder") for p in panels):
        out.append("Круглые/цилиндрические элементы: импортёр строит по габариту — "
                   "для контура нужна доработка под БАЗИС (AKD-42).")

    ev = project.get("estimated_values") or []
    if ev:
        out.append("Оценочные значения: " + ", ".join(map(str, ev)) + ".")

    try:
        from .materials import check_project_materials
        out += check_project_materials(project)
    except (OSError, ValueError):
        pass
    return out


def orchestrate(spec: dict[str, Any], *, out_path: Path, min_volume: float = 1.0) -> OrchestratorResult:
    """ParamSpec → project.json → finish (схема+геометрия+автофикс) → ревью."""
    errors = validate_paramspec(spec)
    if errors:
        raise ValueError("ParamSpec не прошёл валидацию:\n" + "\n".join(errors))

    project = generate_from_paramspec(spec)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(project, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    fin = finish_project(out_path, min_volume=min_volume)
    project = json.loads(out_path.read_text(encoding="utf-8"))  # перечитать (мог быть автофикс)

    return OrchestratorResult(
        project=project,
        schema_ok=fin.schema_ok,
        geometry_ok=fin.geometry_ok,
        consistency_count=len(check_consistency(project)),
        review=self_review(project),
        fixes=fin.fixes_applied,
        saved_to=out_path,
    )


def orchestrate_file(paramspec_path: str | Path, out_path: str | Path | None = None,
                     *, min_volume: float = 1.0) -> OrchestratorResult:
    p = Path(paramspec_path)
    spec = json.loads(p.read_text(encoding="utf-8"))
    out = Path(out_path) if out_path else p.parent.parent / "projects" / p.name
    return orchestrate(spec, out_path=out, min_volume=min_volume)


def orchestrate_source(source: Any, out_path: Path, *, provider: Any = None,
                       provider_name: str | None = None, min_volume: float = 1.0) -> OrchestratorResult:
    """Полный путь: source (ТЗ/изображение/ParamSpec) → провайдер → ParamSpec → orchestrate."""
    from .providers import get_provider
    prov = provider or get_provider(provider_name)
    spec = prov.extract(source)
    return orchestrate(spec, out_path=Path(out_path), min_volume=min_volume)
