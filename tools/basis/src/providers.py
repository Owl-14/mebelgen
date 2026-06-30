"""Провайдеры извлечения ParamSpec (AKD-10).

«AI»-шаг ТЗ→ParamSpec вынесен за интерфейс — координаты считает код, модель только
извлекает параметры. Провайдер выбирается env-переменной PARAMSPEC_PROVIDER;
по умолчанию mock (без ключей и сети, для тестов и для «ассистент сам выдал ParamSpec»).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ParamSpecProvider(Protocol):
    def extract(self, source: Any) -> dict[str, Any]:
        """source → ParamSpec (dict)."""


class MockProvider:
    """Готовый ParamSpec: dict как есть, либо путь к JSON-файлу.

    Покрывает случай «ParamSpec выдала любая модель/ассистент» — провайдер просто
    отдаёт его в пайплайн без обращения к внешнему API.
    """

    def extract(self, source: Any) -> dict[str, Any]:
        if isinstance(source, dict):
            return source
        return json.loads(Path(source).read_text(encoding="utf-8"))


class OpenAIProvider:
    """Извлечение ParamSpec из изображения через OpenAI Vision (нужен ключ)."""

    def __init__(self, **kwargs: Any) -> None:
        from .converter import FurnitureConverter
        self._conv = FurnitureConverter(**kwargs)

    def extract(self, source: Any) -> dict[str, Any]:
        return self._conv.convert_paramspec(Path(source))


def get_provider(name: str | None = None, **kwargs: Any) -> ParamSpecProvider:
    name = (name or os.environ.get("PARAMSPEC_PROVIDER", "mock")).lower()
    if name == "openai":
        return OpenAIProvider(**kwargs)
    if name == "mock":
        return MockProvider()
    raise ValueError(f"Неизвестный PARAMSPEC_PROVIDER={name!r} (mock|openai)")
