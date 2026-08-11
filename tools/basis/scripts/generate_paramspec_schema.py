"""Regenerate schema/paramspec.schema.json from the Pydantic contract."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.paramspec import SCHEMA_PATH, paramspec_json_schema  # noqa: E402


def main() -> None:
    SCHEMA_PATH.write_text(
        json.dumps(paramspec_json_schema(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(SCHEMA_PATH)


if __name__ == "__main__":
    main()
