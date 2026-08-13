"""Read-only ParamSpec v1 audit and migration evidence.

There is intentionally no v2 migrator here: no incompatible v2 contract has
been approved.  The audit proves whether canonical v1 adaptation changes
geometry, drilling or CFRN bytes and always reports zero writes.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from .paramspec_versioning import read_paramspec_v1


def _equivalence(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, bool]:
    from .cfrn import project_to_cfrn_json
    from .generators import generate_from_paramspec
    from .hardware import compute_drilling

    before_project = generate_from_paramspec(copy.deepcopy(dict(before)))
    after_project = generate_from_paramspec(copy.deepcopy(dict(after)))
    return {
        # Python structural equality deliberately treats JSON-equivalent
        # numeric spellings (for example 1200 and 1200.0) as equal.  Pydantic
        # canonicalization may normalize that spelling without changing the
        # geometry or the CFRN model consumed by the encoder.
        "geometry": (before_project.get("panels") or [])
        == (after_project.get("panels") or []),
        "drilling": compute_drilling(before_project)
        == compute_drilling(after_project),
        "cfrn": project_to_cfrn_json(before_project)
        == project_to_cfrn_json(after_project),
    }


def _audit_document(label: str, value: Any) -> dict[str, Any]:
    item: dict[str, Any] = {"document": label, "status": "invalid"}
    try:
        envelope = read_paramspec_v1(value)
    except Exception as error:
        item["error_type"] = type(error).__name__
        return item

    canonical = envelope.canonical_document()
    item.update(envelope.metrics())
    item["status"] = "draft" if canonical.get("draft") else "valid"
    if item["status"] == "valid":
        try:
            item["equivalence"] = _equivalence(value, canonical)
        except Exception as error:
            item["equivalence"] = {
                "geometry": False, "drilling": False, "cfrn": False
            }
            item["equivalence_error_type"] = type(error).__name__
    return item


def _catalog_documents(
    legacy_root: Path,
    tenant_root: Path,
) -> Iterable[tuple[str, Any]]:
    roots: list[tuple[str, Path]] = []
    if legacy_root.is_dir():
        roots.append(("legacy", legacy_root))
    if tenant_root.is_dir():
        roots.extend(
            (f"tenant/{organization.name}", organization / "paramspecs")
            for organization in sorted(tenant_root.iterdir())
            if (organization / "paramspecs").is_dir()
        )
    for prefix, root in roots:
        for path in sorted(root.glob("*.json")):
            if path.name.endswith((".project.json", ".versions.json")):
                continue
            label = f"{prefix}/{path.name}"
            try:
                yield label, json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                yield label, None
        for path in sorted(root.glob("*.versions.json")):
            try:
                revisions = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                yield f"{prefix}/{path.name}", None
                continue
            if not isinstance(revisions, list):
                yield f"{prefix}/{path.name}", None
                continue
            for index, revision in enumerate(revisions):
                spec = revision.get("spec") if isinstance(revision, Mapping) else None
                yield f"{prefix}/{path.name}#spec[{index}]", spec


def audit_paramspec_v1_catalogs(
    legacy_root: Path,
    tenant_root: Path,
) -> dict[str, Any]:
    """Audit catalog copies without writing, renaming or deleting anything."""

    items = [
        _audit_document(label, value)
        for label, value in _catalog_documents(legacy_root, tenant_root)
    ]
    valid = [item for item in items if item["status"] == "valid"]
    equivalence_failed = sum(
        not all(item.get("equivalence", {}).values()) for item in valid
    )
    versions: dict[str, int] = {}
    for item in items:
        version = str(item.get("schema_version") or "invalid_or_unknown")
        versions[version] = versions.get(version, 0) + 1
    return {
        "dry_run": True,
        "writes_performed": 0,
        "production_migration_allowed": False,
        "documents": len(items),
        "valid": len(valid),
        "drafts": sum(item["status"] == "draft" for item in items),
        "invalid": sum(item["status"] == "invalid" for item in items),
        "schema_versions": versions,
        "unknown_field_documents": sum(bool(item.get("unknown_fields")) for item in items),
        "unknown_field_count": sum(int(item.get("unknown_field_count") or 0) for item in items),
        "canonical_changes": sum(bool(item.get("canonical_changed")) for item in items),
        "equivalence_checked": len(valid),
        "equivalence_failed": equivalence_failed,
        "items": items,
    }
