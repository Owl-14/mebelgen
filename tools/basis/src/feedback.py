"""Сбор обучающих пар из обратной связи (AKD-35).

Каждая принятая/исправленная модель → legacy-пара (ParamSpec → принятый
project.json) в `dataset/`. Такая запись сама по себе не является чистой парой
ТЗ→ParamSpec для fine-tune: gate MEB-141 дополнительно требует split, review,
provenance/usage rights и leakage group.

Экспорт исправленной модели из БАЗИС (через импортёр-отчёт или Cutting API,
см. AKD-18/AKD-14) пока выполняется вручную — здесь только приём готового
project.json. Когда появится автоэкспорт, подключить его в `ingest_from_basis`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DATASET_DIR = ROOT / "dataset"


def record_pair(paramspec: dict[str, Any], accepted_project: dict[str, Any],
                *, source_tz: str = "", dataset_dir: Path | None = None) -> Path:
    """Сохранить обучающую пару (вход ParamSpec → принятый результат)."""
    d = dataset_dir or DATASET_DIR
    d.mkdir(parents=True, exist_ok=True)
    name = paramspec.get("project_name", "item").strip().replace(" ", "_")[:60]
    n = len(list(d.glob("*.json")))
    path = d / f"{n:04d}_{name}.json"
    path.write_text(json.dumps({
        "paramspec": paramspec,
        "accepted_project": accepted_project,
        "source_tz": source_tz,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def ingest_from_basis(*args, **kwargs):  # noqa: ANN002, ANN003
    """TODO(AKD-35): автоэкспорт исправленной модели из БАЗИС → record_pair.

    Нужен установленный БАЗИС и путь экспорта (.b3d → project.json) или Cutting API.
    """
    raise NotImplementedError("Автоэкспорт из БАЗИС не подключён (нужна среда БАЗИС, см. AKD-12/AKD-18).")
