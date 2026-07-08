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


def generate_from_paramspec(spec: dict[str, Any]) -> dict[str, Any]:
    # Дозаполнить материалы/фурнитуру детерминированной политикой (AKD-76):
    # из одного ТЗ должна собираться полная модель, без пропусков.
    from ..materials_policy import apply_material_policy
    from ..overrides import apply_back_mount, apply_overrides

    spec = apply_material_policy(spec)
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
