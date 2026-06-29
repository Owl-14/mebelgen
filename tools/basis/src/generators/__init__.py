"""Параметрические генераторы: ParamSpec -> project.json (panels[] с placement).

Координаты считает код по RULES.md, а не LLM. Реестр в registry.py.
"""

from .registry import generate_from_paramspec, get_generator

__all__ = ["generate_from_paramspec", "get_generator"]
