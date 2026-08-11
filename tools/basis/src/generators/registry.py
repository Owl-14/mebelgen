"""Реестр генераторов: archetype -> функция generate(spec) -> project.json."""

from __future__ import annotations

from typing import Any, Callable

from . import cabinet, composite, corpus, desk, door_unit, drawer_unit, round_table, shelving

_REGISTRY: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "corpus": corpus.generate,
    "shelving": shelving.generate,
    "door_unit": door_unit.generate,
    "drawer_unit": drawer_unit.generate,
    "cabinet": cabinet.generate,
    "wardrobe": cabinet.generate,
    "desk": desk.generate,
    "table": desk.generate,
    "round_table": round_table.generate,
    "composite": composite.generate,
}


def get_generator(archetype: str) -> Callable[[dict[str, Any]], dict[str, Any]]:
    gen = _REGISTRY.get(archetype)
    if gen is None:
        raise ValueError(
            f"Нет генератора для archetype={archetype!r}. "
            f"Доступны: {', '.join(sorted(_REGISTRY))}"
        )
    return gen


def _normalize_spec(spec: dict[str, Any]) -> dict[str, Any]:
    """Алиасы и устаревшие поля секций → канонический контракт (AKD-259).

    - door_inset: true → door_z: "inset" (врезная дверь в проём);
    - open_top / open_top_height — устаревшие: генераторы понимают только
      front_top (Y верха фасадной зоны) — игнорируем с warning, не молча.
    """
    import json as _json
    secs = spec.get("sections")
    has_legacy_section = isinstance(secs, list) and any(
        isinstance(s, dict) and (
            "door_inset" in s or "open_top" in s or "open_top_height" in s
        )
        for s in secs
    )
    hardware = spec.get("hardware")
    guides = hardware.get("drawer_guides") if isinstance(hardware, dict) else None
    has_legacy_guides = isinstance(guides, dict) and "with_closer" in guides
    if not has_legacy_section and not has_legacy_guides:
        return spec
    spec = _json.loads(_json.dumps(spec, ensure_ascii=False))   # не мутируем вход
    warns = spec.setdefault("warnings", [])
    for i, s in enumerate(spec.get("sections") or [], start=1):
        if not isinstance(s, dict):
            continue
        if s.pop("door_inset", None):
            s.setdefault("door_z", "inset")
        for dead in ("open_top", "open_top_height"):
            if dead in s:
                s.pop(dead)
                w = (f"секция {i}: поле {dead} устарело и проигнорировано — "
                     f"верх ниши задаётся front_top (Y от пола)")
                if w not in warns:
                    warns.append(w)
    normalized_hardware = spec.get("hardware")
    normalized_guides = (
        normalized_hardware.get("drawer_guides")
        if isinstance(normalized_hardware, dict) else None
    )
    if isinstance(normalized_guides, dict) and "with_closer" in normalized_guides:
        legacy_value = normalized_guides.pop("with_closer")
        normalized_guides.setdefault("soft_close", legacy_value)
        warning = "hardware.drawer_guides.with_closer устарело; использовано soft_close"
        if warning not in warns:
            warns.append(warning)
    return spec


def generate_from_paramspec(spec: dict[str, Any]) -> dict[str, Any]:
    # Дозаполнить материалы/фурнитуру детерминированной политикой (AKD-76):
    # из одного ТЗ должна собираться полная модель, без пропусков.
    from ..materials_policy import apply_material_policy
    from ..overrides import apply_back_mount, apply_overrides

    from ..telemetry import hash_payload, span

    with span("geometry.generate", {
        "project.hash": hash_payload({
            "project_name": spec.get("project_name"),
            "archetype": spec.get("archetype"),
        }),
        "revision.hash": hash_payload(spec),
    }) as trace_span:
        spec = apply_material_policy(_normalize_spec(spec))
        project = get_generator(spec["archetype"])(spec)
        # конструкция задника (AKD-137): overlay = накладной поверх торцов.
        # Для composite задник накладывается поблочно (внутри generate каждого
        # блока); повторный вызов на собранном проекте раздул бы один задник на
        # габарит всей композиции (перекрытие с соседними модулями) — пропускаем.
        if spec["archetype"] != "composite":
            project = apply_back_mount(project, spec.get("back_mount"))
        # точечные правки деталей поверх генератора (AKD-121) — до валидаторов,
        # чтобы чертёж/присадки/смета/cfrn считались по итоговой геометрии
        project = apply_overrides(project, spec.get("overrides"))
        trace_span.set_attributes({
            "panel.count": len(project.get("panels") or []),
            "check.outcome": "pass",
        })
        return project
