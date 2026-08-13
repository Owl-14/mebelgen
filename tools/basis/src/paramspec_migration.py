"""Deterministic, read-only ParamSpec v1 migration planning evidence.

There is intentionally no apply mode or v2 converter: no incompatible v2
contract has been approved. The planner inventories exact input bytes, proves
canonical-v1 equivalence and idempotence, and fails closed as one catalog batch.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from .paramspec_versioning import read_paramspec_v1, strict_paramspec_v1_for_write


REPORT_VERSION = "paramspec-v1-dry-run-v2"
BACKUP_MANIFEST_VERSION = "paramspec-v1-backup-v1"
ROLLBACK_MANIFEST_VERSION = "paramspec-v1-rollback-v1"


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _stable_digest(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return _sha256_bytes(encoded)


def _equivalence(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, bool]:
    from .cfrn import project_to_cfrn_json
    from .generators import generate_from_paramspec
    from .hardware import compute_drilling

    before_project = generate_from_paramspec(copy.deepcopy(dict(before)))
    after_project = generate_from_paramspec(copy.deepcopy(dict(after)))
    return {
        "geometry": (before_project.get("panels") or [])
        == (after_project.get("panels") or []),
        "drilling": compute_drilling(before_project)
        == compute_drilling(after_project),
        "cfrn": project_to_cfrn_json(before_project)
        == project_to_cfrn_json(after_project),
    }


@dataclass(frozen=True)
class _CatalogDocument:
    label: str
    source_file: str
    source_file_sha256: str
    source_file_size: int
    value: Any
    revision_index: int | None = None


def _audit_document(document: _CatalogDocument) -> dict[str, Any]:
    observed_version = (
        document.value.get("schemaVersion")
        if isinstance(document.value, Mapping)
        else "unreadable"
    )
    item: dict[str, Any] = {
        "document": document.label,
        "source_file": document.source_file,
        "source_file_sha256": document.source_file_sha256,
        "source_file_size": document.source_file_size,
        "observed_schema_version": observed_version or "missing",
        "status": "invalid",
    }
    if document.revision_index is not None:
        item["revision_index"] = document.revision_index
    try:
        envelope = read_paramspec_v1(document.value)
    except Exception as error:
        item["error_type"] = type(error).__name__
        return item

    canonical = envelope.canonical_document()
    strict_once = strict_paramspec_v1_for_write(canonical)
    strict_twice = strict_paramspec_v1_for_write(strict_once)
    item.update(envelope.metrics())
    item["canonical_sha256"] = _stable_digest(strict_once)
    item["canonical_idempotent"] = strict_once == strict_twice
    item["status"] = "draft" if canonical.get("draft") else "valid"
    if item["status"] == "valid":
        try:
            item["equivalence"] = _equivalence(document.value, canonical)
        except Exception as error:
            item["equivalence"] = {
                "geometry": False,
                "drilling": False,
                "cfrn": False,
            }
            item["equivalence_error_type"] = type(error).__name__
    return item


def _catalog_documents(
    legacy_root: Path,
    tenant_root: Path,
) -> Iterable[_CatalogDocument]:
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
            raw = path.read_bytes()
            source_file = f"{prefix}/{path.name}"
            try:
                value = json.loads(raw.decode("utf-8"))
            except Exception:
                value = None
            yield _CatalogDocument(
                source_file,
                source_file,
                _sha256_bytes(raw),
                len(raw),
                value,
            )
        for path in sorted(root.glob("*.versions.json")):
            raw = path.read_bytes()
            source_file = f"{prefix}/{path.name}"
            try:
                revisions = json.loads(raw.decode("utf-8"))
            except Exception:
                revisions = None
            if not isinstance(revisions, list):
                yield _CatalogDocument(
                    source_file,
                    source_file,
                    _sha256_bytes(raw),
                    len(raw),
                    None,
                )
                continue
            for index, revision in enumerate(revisions):
                spec = revision.get("spec") if isinstance(revision, Mapping) else None
                yield _CatalogDocument(
                    f"{source_file}#spec[{index}]",
                    source_file,
                    _sha256_bytes(raw),
                    len(raw),
                    spec,
                    index,
                )


def audit_paramspec_v1_catalogs(
    legacy_root: Path,
    tenant_root: Path,
) -> dict[str, Any]:
    """Build a deterministic, all-or-nothing plan without changing sources."""

    items = [
        _audit_document(document)
        for document in _catalog_documents(legacy_root, tenant_root)
    ]
    valid = [item for item in items if item["status"] == "valid"]
    equivalence_failed = sum(
        not all(item.get("equivalence", {}).values()) for item in valid
    )
    idempotence_failed = sum(
        not item.get("canonical_idempotent", False)
        for item in items
        if item["status"] != "invalid"
    )
    versions: dict[str, int] = {}
    for item in items:
        version = str(item.get("observed_schema_version") or "missing")
        versions[version] = versions.get(version, 0) + 1

    backup_by_path = {
        item["source_file"]: {
            "path": item["source_file"],
            "sha256": item["source_file_sha256"],
            "size": item["source_file_size"],
        }
        for item in items
    }
    backup_files = sorted(backup_by_path.values(), key=lambda value: value["path"])
    source_tree_sha256 = _stable_digest(backup_files)
    invalid_count = sum(item["status"] == "invalid" for item in items)
    blockers = []
    if invalid_count:
        blockers.append("invalid_or_unsupported_paramspec")
    if equivalence_failed:
        blockers.append("production_equivalence_failed")
    if idempotence_failed:
        blockers.append("canonicalization_not_idempotent")
    operations = [
        {
            "document": item["document"],
            "source_file": item["source_file"],
            "source_sha256": item["source_file_sha256"],
            "canonical_sha256": item["canonical_sha256"],
        }
        for item in items
        if item["status"] != "invalid" and item.get("canonical_changed")
    ]

    report: dict[str, Any] = {
        "report_version": REPORT_VERSION,
        "dry_run": True,
        "writes_performed": 0,
        "production_migration_allowed": False,
        "documents": len(items),
        "valid": len(valid),
        "drafts": sum(item["status"] == "draft" for item in items),
        "invalid": invalid_count,
        "schema_versions": dict(sorted(versions.items())),
        "unknown_field_documents": sum(
            bool(item.get("unknown_fields")) for item in items
        ),
        "unknown_field_count": sum(
            int(item.get("unknown_field_count") or 0) for item in items
        ),
        "canonical_changes": sum(
            bool(item.get("canonical_changed")) for item in items
        ),
        "canonical_idempotence_failed": idempotence_failed,
        "equivalence_checked": len(valid),
        "equivalence_failed": equivalence_failed,
        "backup_manifest": {
            "version": BACKUP_MANIFEST_VERSION,
            "strategy": "exact-bytes",
            "source_tree_sha256": source_tree_sha256,
            "files": backup_files,
        },
        "rollback_manifest": {
            "version": ROLLBACK_MANIFEST_VERSION,
            "strategy": "restore-exact-bytes-and-verify-sha256",
            "source_tree_sha256": source_tree_sha256,
            "includes_revisions": any(
                file["path"].endswith(".versions.json") for file in backup_files
            ),
            "files": backup_files,
        },
        "migration_plan": {
            "mode": "canonical-v1-copy-only",
            "atomicity": "all-or-nothing",
            "source_writes_allowed": False,
            "ready": not blockers,
            "blockers": blockers,
            "operations": operations,
        },
        "items": items,
    }
    report["report_digest"] = _stable_digest(report)
    return report
