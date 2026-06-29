from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema
from jsonschema import Draft202012Validator


def load_schema(schema_path: Path | None = None) -> dict[str, Any]:
    if schema_path is None:
        schema_path = Path(__file__).resolve().parent.parent / "schema" / "furniture.schema.json"
    with schema_path.open(encoding="utf-8") as f:
        return json.load(f)


def validate_furniture(data: dict[str, Any], schema_path: Path | None = None) -> list[str]:
    """Возвращает список ошибок валидации (пустой — если всё ок)."""
    schema = load_schema(schema_path)
    validator = Draft202012Validator(schema)
    errors: list[str] = []
    for err in sorted(validator.iter_errors(data), key=lambda e: e.path):
        path = ".".join(str(p) for p in err.path) or "(root)"
        errors.append(f"{path}: {err.message}")
    return errors


def validate_file(json_path: Path, schema_path: Path | None = None) -> list[str]:
    with json_path.open(encoding="utf-8") as f:
        data = json.load(f)
    return validate_furniture(data, schema_path)
