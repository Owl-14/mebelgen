#!/usr/bin/env python3
"""Read-only validation gate for legacy and tenant ParamSpec catalogs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.paramspec import validate_paramspec  # noqa: E402


def _catalog_files(legacy_root: Path, tenant_root: Path) -> Iterable[tuple[str, Path]]:
    if legacy_root.is_dir():
        for path in sorted(legacy_root.glob("*.json")):
            if not path.name.endswith((".project.json", ".versions.json")):
                yield f"legacy/{path.name}", path
    if tenant_root.is_dir():
        for path in sorted(tenant_root.glob("*/paramspecs/*.json")):
            if not path.name.endswith((".project.json", ".versions.json")):
                organization = path.parent.parent.name
                yield f"tenant/{organization}/{path.name}", path


def validate_catalogs(
    legacy_root: Path,
    tenant_root: Path,
    *,
    build: bool = False,
) -> dict[str, Any]:
    failures: list[dict[str, Any]] = []
    checked = drafts = built = 0
    for label, path in _catalog_files(legacy_root, tenant_root):
        checked += 1
        try:
            spec = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            failures.append({"file": label, "kind": "invalid_json", "errors": [str(error)]})
            continue
        problems = validate_paramspec(spec)
        if problems:
            failures.append({"file": label, "kind": "invalid_paramspec", "errors": problems[:12]})
            continue
        if isinstance(spec, dict) and spec.get("draft"):
            drafts += 1
            continue
        if build:
            from src.studio import build_payload

            payload = build_payload(spec)
            if not isinstance(payload.get("viewer"), dict):
                issues = payload.get("issues") if isinstance(payload, dict) else {}
                errors = [
                    f"{group}: {message}"
                    for group, messages in (issues or {}).items()
                    for message in (messages or [])
                ]
                failures.append({
                    "file": label,
                    "kind": "generation_failed",
                    "errors": errors[:12] or ["viewer payload is missing"],
                })
                continue
            built += 1
    return {
        "ok": not failures,
        "checked": checked,
        "drafts": drafts,
        "built": built,
        "failed": len(failures),
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--legacy-root", type=Path, required=True)
    parser.add_argument("--tenant-root", type=Path, required=True)
    parser.add_argument("--build", action="store_true")
    args = parser.parse_args()
    report = validate_catalogs(args.legacy_root, args.tenant_root, build=args.build)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
