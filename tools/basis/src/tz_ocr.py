"""Подписанные числа с чертежа ТЗ — локальный OCR, без нейросети.

Vision-модель читает размеры с ошибками (комод 1600 → «1500»), но выдуманного
числа на чертеже нет. RapidOCR (Apache-2.0, onnxruntime на CPU) достаёт все
подписанные числа; если габарит изделия не совпадает ни с одним, проектировщику
задаётся вопрос. Зависимость необязательная (requirements-server.txt): без неё
drawing_numbers() возвращает None и импорт работает как раньше.
"""

from __future__ import annotations

import base64
import re
import threading
from typing import Any

_ENGINE: Any = None
_LOCK = threading.Lock()
_UNAVAILABLE = False


def _engine() -> Any:
    global _ENGINE, _UNAVAILABLE
    if _ENGINE is not None or _UNAVAILABLE:
        return _ENGINE
    with _LOCK:
        if _ENGINE is None and not _UNAVAILABLE:
            try:
                from rapidocr_onnxruntime import RapidOCR
            except ImportError:
                _UNAVAILABLE = True
                return None
            _ENGINE = RapidOCR()
    return _ENGINE


def numbers_in_text(text: str) -> list[int]:
    """Числа 2–4 знака (мм) из строки; «1000±10х400» → [1000, 10, 400]."""
    return [int(n) for n in re.findall(r"(?<!\d)\d{2,4}(?!\d)", text or "")]


def drawing_numbers(image_b64: str) -> list[int] | None:
    """Отсортированные уникальные числа с картинки; None — OCR недоступен/сбой."""
    engine = _engine()
    if engine is None or not image_b64:
        return None
    try:
        result, _elapsed = engine(base64.b64decode(image_b64))
    except Exception:                                 # битая картинка — без сверки
        return None
    found: set[int] = set()
    for _box, text, _score in result or []:
        found.update(numbers_in_text(str(text)))
    return sorted(found)
