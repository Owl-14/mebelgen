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
    if not isinstance(secs, list) or not any(
            isinstance(s, dict) and ("door_inset" in s or "open_top" in s
                                     or "open_top_height" in s) for s in secs):
        return spec
    spec = _json.loads(_json.dumps(spec, ensure_ascii=False))   # не мутируем вход
    warns = spec.setdefault("warnings", [])
    for i, s in enumerate(spec["sections"], start=1):
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
    return spec


def generate_from_paramspec(spec: dict[str, Any]) -> dict[str, Any]:
    # Дозаполнить материалы/фурнитуру детерминированной политикой (AKD-76):
    # из одного ТЗ должна собираться полная модель, без пропусков.
    from ..materials_policy import apply_material_policy
    from ..overrides import apply_back_mount, apply_overrides

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
    return apply_overrides(project, spec.get("overrides"))
