"""Валидация ParamSpec (высокоуровневый вход генераторов)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema
from jsonschema import Draft202012Validator

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema" / "paramspec.schema.json"


def load_schema(path: Path | None = None) -> dict[str, Any]:
    with (path or SCHEMA_PATH).open(encoding="utf-8") as f:
        return json.load(f)


def validate_paramspec(data: dict[str, Any], schema_path: Path | None = None) -> list[str]:
    """Список ошибок (пустой — если ParamSpec валиден)."""
    validator = Draft202012Validator(load_schema(schema_path))
    errors: list[str] = []
    for err in sorted(validator.iter_errors(data), key=lambda e: list(e.path)):
        path = ".".join(str(p) for p in err.path) or "(root)"
        errors.append(f"{path}: {err.message}")
    return errors


def validate_paramspec_file(path: str | Path, schema_path: Path | None = None) -> list[str]:
    with Path(path).open(encoding="utf-8") as f:
        return validate_paramspec(json.load(f), schema_path)
