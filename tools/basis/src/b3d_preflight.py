"""Offline production checks that must pass before a paid B3D conversion.

This module owns the project.json side of the boundary.  ParamSpec candidates
first pass :mod:`src.production_gate`; an already generated project still has
to prove its schema, geometry, drilling, material and CFRN contracts here.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class B3DProjectPreflight:
    project: dict[str, Any]
    checks: tuple[dict[str, Any], ...]

    @property
    def ok(self) -> bool:
        return all(check["status"] != "fail" for check in self.checks)

    def to_dict(self) -> dict[str, Any]:
        errors = [issue for check in self.checks for issue in check["issues"]
                  if issue["severity"] == "error"]
        warnings = [issue for check in self.checks for issue in check["issues"]
                    if issue["severity"] == "warning"]
        return {
            "ok": self.ok,
            "scope": "offline",
            "checks": list(self.checks),
            "errors": errors,
            "warnings": warnings,
        }


class B3DPreflightError(RuntimeError):
    """A paid conversion was stopped by an offline production gate."""

    def __init__(self, report: dict[str, Any]):
        self.report = report
        errors = report.get("errors") or []
        summary = "; ".join(
            f"{item.get('code', 'preflight.failed')}: {item.get('detail', '')}"
            for item in errors[:10]
        ) or "неизвестная ошибка preflight"
        super().__init__(
            "B3D preflight не пройден; платная конвертация не запущена: " + summary
        )


def _issue(code: str, detail: Any, *, severity: str = "error") -> dict[str, str]:
    return {"code": code, "detail": str(detail), "severity": severity}


def _run_check(name: str, action: Callable[[], list[dict[str, str]]]) -> dict[str, Any]:
    try:
        issues = action()
    except Exception as error:  # every adapter failure must stop spending
        issues = [_issue(f"preflight.{name}_failed", error)]
    return {
        "name": name,
        "status": "fail" if any(x["severity"] == "error" for x in issues) else "pass",
        "issues": issues,
    }


def evaluate_b3d_project_preflight(project: dict[str, Any]) -> B3DProjectPreflight:
    """Return a complete offline report for an already generated project."""
    prepared = copy.deepcopy(project)
    checks: list[dict[str, Any]] = []

    def schema_check() -> list[dict[str, str]]:
        from .validate import validate_furniture

        return [_issue("project.schema", detail) for detail in validate_furniture(prepared)]

    checks.append(_run_check("project_schema", schema_check))

    def mapping_check() -> list[dict[str, str]]:
        mapping = prepared.get("basis_mapping") or {}
        issues = []
        if mapping.get("coordinate_system") != "basis_mebelshik":
            issues.append(_issue("project.coordinate_system", "ожидался basis_mebelshik"))
        if mapping.get("units") != "mm":
            issues.append(_issue("project.units", "ожидались миллиметры"))
        if mapping.get("ready_for_import") is not True:
            issues.append(_issue("project.not_ready_for_import", "ready_for_import должен быть true"))
        if any(not isinstance(p, dict) or not p.get("placement")
               for p in prepared.get("panels") or []):
            issues.append(_issue("project.placement_missing", "каждая панель должна иметь placement"))
        return issues

    checks.append(_run_check("basis_mapping", mapping_check))

    def consistency_check() -> list[dict[str, str]]:
        from .consistency_check import check_consistency

        return [_issue("model.consistency", f"{p.panel}: {p.message}")
                for p in check_consistency(prepared) if p.severity == "error"]

    checks.append(_run_check("consistency", consistency_check))

    def geometry_check() -> list[dict[str, str]]:
        from .geometry_check import check_placement_geometry

        result = check_placement_geometry(prepared)
        return [] if result.get("ok", True) else [
            _issue("model.geometry", detail)
            for detail in result.get("issues") or ["обнаружены пересечения панелей"]
        ]

    checks.append(_run_check("geometry", geometry_check))

    def encoding_check() -> list[dict[str, str]]:
        from .cfrn import check_cfrn_encoding

        return [_issue("cfrn.encoding", detail) for detail in check_cfrn_encoding(prepared)]

    checks.append(_run_check("cfrn_encoding", encoding_check))

    def holes_check() -> list[dict[str, str]]:
        from .cfrn import check_cfrn_holes

        return [_issue("cfrn.holes_parity", detail) for detail in check_cfrn_holes(prepared)]

    checks.append(_run_check("cfrn_holes_parity", holes_check))

    def drilling_check() -> list[dict[str, str]]:
        from .drilling_check import check_drilling_geometry

        result = check_drilling_geometry(prepared)
        return ([_issue("drilling.geometry", detail) for detail in result.get("errors", [])]
                + [_issue("drilling.warning", detail, severity="warning")
                   for detail in result.get("warnings", [])])

    checks.append(_run_check("drilling_geometry", drilling_check))

    def materials_check() -> list[dict[str, str]]:
        from .materials import resolve_project_materials

        refs = resolve_project_materials(prepared)
        prepared["material_refs"] = refs
        return [
            _issue("materials.unresolved", f"не выбрана производственная позиция для слота {slot}")
            for slot, value in refs.items()
            if isinstance(value, dict) and not value.get("resolved")
        ]

    checks.append(_run_check("materials", materials_check))

    def archive_check() -> list[dict[str, str]]:
        from .cfrn import project_to_cfrn_bytes

        payload = project_to_cfrn_bytes(prepared)
        return [] if payload else [_issue("cfrn.empty", "получен пустой CFRN")]

    checks.append(_run_check("cfrn_archive", archive_check))
    return B3DProjectPreflight(prepared, tuple(checks))


def require_b3d_project_preflight(project: dict[str, Any]) -> B3DProjectPreflight:
    result = evaluate_b3d_project_preflight(project)
    if not result.ok:
        raise B3DPreflightError(result.to_dict())
    return result
