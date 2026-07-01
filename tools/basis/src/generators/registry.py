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

    spec = apply_material_policy(spec)
    return get_generator(spec["archetype"])(spec)
