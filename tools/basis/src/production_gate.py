"""Atomic production gate for untrusted ParamSpec candidates.

The gate owns validation and production-readiness checks, but deliberately does
not own edit-operation reduction.  MEB-143's reducer can pass its result here
without the reducer being duplicated or imported by this module.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Protocol, Sequence


@dataclass(frozen=True)
class RepairOption:
    kind: str
    description: str
    operation: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"kind": self.kind, "description": self.description}
        if self.operation:
            result["operation"] = self.operation
        return result


@dataclass(frozen=True)
class CheckIssue:
    code: str
    detail: str
    purpose: str
    severity: str = "error"
    repair_options: tuple[RepairOption, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "detail": self.detail,
            "purpose": self.purpose,
            "severity": self.severity,
            "repair_options": [item.to_dict() for item in self.repair_options],
        }


@dataclass
class CheckStep:
    name: str
    issues: list[CheckIssue] = field(default_factory=list)
    skipped: bool = False

    @property
    def status(self) -> str:
        if self.skipped:
            return "skipped"
        if any(issue.severity == "error" for issue in self.issues):
            return "error"
        if self.issues:
            return "warning"
        return "ok"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "issues": [issue.to_dict() for issue in self.issues],
        }


@dataclass
class CheckReport:
    checks: list[CheckStep]

    @property
    def ok(self) -> bool:
        return not any(
            issue.severity == "error"
            for check in self.checks
            for issue in check.issues
        )

    @property
    def errors(self) -> list[CheckIssue]:
        return [
            issue
            for check in self.checks
            for issue in check.issues
            if issue.severity == "error"
        ]

    @property
    def warnings(self) -> list[CheckIssue]:
        return [
            issue
            for check in self.checks
            for issue in check.issues
            if issue.severity == "warning"
        ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "checks": [check.to_dict() for check in self.checks],
            "errors": [issue.to_dict() for issue in self.errors],
            "warnings": [issue.to_dict() for issue in self.warnings],
        }


@dataclass
class GateDecision:
    report: CheckReport
    accepted_spec: dict[str, Any] | None
    project: dict[str, Any] | None = None
    material_refs: dict[str, Any] = field(default_factory=dict)


class OperationReducer(Protocol):
    def __call__(self, spec: dict[str, Any], operations: Sequence[Any]) -> Any: ...


_CHECK_ORDER = (
    "pydantic",
    "json_schema",
    "generate",
    "consistency",
    "geometry",
    "bounds",
    "cfrn_encoding",
    "cfrn_holes_parity",
    "drilling_geometry",
    "system_32",
    "purpose_registry",
    "completeness",
    "materials",
)

_PURPOSES = {
    "pydantic": "ParamSpec должен соответствовать типизированному контракту v1.",
    "json_schema": "Публичный Studio/AI JSON-контракт должен принимать ту же правку.",
    "generate": "Детерминированный генератор должен построить производственную модель.",
    "consistency": "Размеры деталей и их размещение должны быть согласованы без пересечений.",
    "geometry": "Детали должны находиться в габарите и не иметь запрещённых нахлёстов.",
    "bounds": "Структурные детали должны оставаться внутри заявленных габаритов изделия.",
    "cfrn_encoding": "Модель должна без потерь кодироваться в производственный CFRN.",
    "cfrn_holes_parity": "Присадки CFRN должны совпадать с расчётом сверловки.",
    "drilling_geometry": "Все отверстия должны быть физически выполнимы на присадочном центре.",
    "system_32": "Производственные присадки должны соблюдать систему 32.",
    "purpose_registry": "Каждый purpose присадки должен быть зарегистрирован во всех потребителях.",
    "completeness": "Все заявленные детали и функции должны быть построены и закреплены.",
    "materials": "Все производственные материальные слоты должны быть определены.",
}

_REPAIRS = {
    "pydantic": ("SetDimension", "SetMaterial", "ChangeArchetype", "UpdateSection"),
    "json_schema": ("SetDimension", "SetMaterial", "ChangeArchetype", "UpdateSection"),
    "generate": ("SetDimension", "ChangeArchetype", "AddSection", "UpdateSection", "DeleteSection"),
    "consistency": ("MovePart", "ResizePart", "DeletePart"),
    "geometry": ("MovePart", "ResizePart", "DeletePart"),
    "bounds": ("MovePart", "ResizePart", "DeletePart"),
    "cfrn_encoding": ("MovePart", "ResizePart", "DeletePart"),
    "cfrn_holes_parity": ("MovePart", "ResizePart", "DeletePart"),
    "drilling_geometry": ("MovePart", "ResizePart", "DeletePart"),
    "system_32": ("MovePart", "ResizePart", "DeletePart"),
    "purpose_registry": ("MovePart", "ResizePart", "DeletePart"),
    "completeness": ("AddShelf", "MovePart", "ResizePart"),
    "materials": ("SetMaterial",),
}


def _repair_options(stage: str) -> tuple[RepairOption, ...]:
    operations = tuple(
        RepairOption(
            kind="operation",
            operation=name,
            description=f"Повторить правку типизированной операцией {name}.",
        )
        for name in _REPAIRS[stage]
    )
    return operations + (
        RepairOption(
            kind="manual_edit",
            description="Открыть замечание и исправить ParamSpec вручную перед повторной проверкой.",
        ),
    )


def _issue(stage: str, code: str, detail: Any, *, severity: str = "error") -> CheckIssue:
    return CheckIssue(
        code=code,
        detail=str(detail),
        purpose=_PURPOSES[stage],
        severity=severity,
        repair_options=_repair_options(stage),
    )


def _candidate_spec(candidate: Any) -> dict[str, Any] | None:
    """Accept a ParamSpec or a reducer result exposing ``spec``.

    A deep copy is mandatory: generators and material policy may enrich their
    input, while a rejected candidate must never mutate the current revision.
    """

    if hasattr(candidate, "model_dump"):
        candidate = candidate.model_dump(mode="python")
    if isinstance(candidate, Mapping) and "schemaVersion" in candidate:
        return copy.deepcopy(dict(candidate))
    if isinstance(candidate, Mapping):
        candidate = candidate.get("spec")
    elif hasattr(candidate, "spec"):
        candidate = candidate.spec
    if hasattr(candidate, "model_dump"):
        candidate = candidate.model_dump(mode="python")
    if isinstance(candidate, Mapping):
        return copy.deepcopy(dict(candidate))
    return None


def _skipped_steps(after: str) -> list[CheckStep]:
    index = _CHECK_ORDER.index(after)
    return [CheckStep(name=name, skipped=True) for name in _CHECK_ORDER[index + 1:]]


def evaluate_production_gate(candidate: Any) -> GateDecision:
    """Run the complete production contour in its canonical order."""

    from jsonschema import Draft202012Validator

    from .paramspec import load_schema, parse_paramspec

    spec = _candidate_spec(candidate)
    checks: list[CheckStep] = []
    pydantic_step = CheckStep("pydantic")
    if spec is None:
        pydantic_step.issues.append(
            _issue(
                "pydantic",
                "paramspec.not_object",
                "Ожидался ParamSpec или результат reducer с полем spec.",
            )
        )
    else:
        try:
            parse_paramspec(spec)
        except Exception as error:  # Pydantic exposes structured errors via ValidationError
            details = getattr(error, "errors", lambda **_: [])(include_url=False)
            if details:
                for detail in details:
                    path = ".".join(str(part) for part in detail.get("loc", ())) or "(root)"
                    pydantic_step.issues.append(
                        _issue("pydantic", "paramspec.pydantic", f"{path}: {detail.get('msg', error)}")
                    )
            else:
                pydantic_step.issues.append(_issue("pydantic", "paramspec.pydantic", error))
    checks.append(pydantic_step)

    schema_step = CheckStep("json_schema")
    if spec is not None:
        validator = Draft202012Validator(load_schema())
        errors = sorted(
            validator.iter_errors(spec),
            key=lambda item: tuple(str(part) for part in item.path),
        )
        for error in errors:
            path = ".".join(str(part) for part in error.path) or "(root)"
            schema_step.issues.append(
                _issue("json_schema", "paramspec.json_schema", f"{path}: {error.message}")
            )
    else:
        schema_step.skipped = True
    checks.append(schema_step)
    if spec is None or pydantic_step.issues or schema_step.issues:
        checks.extend(_skipped_steps("json_schema"))
        return GateDecision(CheckReport(checks), None)

    generate_step = CheckStep("generate")
    project: dict[str, Any] | None = None
    try:
        from .generators import generate_from_paramspec

        project = generate_from_paramspec(copy.deepcopy(spec))
    except Exception as error:
        generate_step.issues.append(_issue("generate", "generation.failed", error))
    checks.append(generate_step)
    if project is None:
        checks.extend(_skipped_steps("generate"))
        return GateDecision(CheckReport(checks), None)

    consistency_step = CheckStep("consistency")
    try:
        from .consistency_check import check_consistency

        for problem in check_consistency(project):
            severity = "error" if problem.code in {
                "bad_orientation",
                "nonpositive_span",
                "thickness_mismatch",
                "dim_width_mismatch",
                "dim_height_mismatch",
                "position_mismatch",
                "panel_overlap",
                "duplicate_name",
            } else "warning"
            consistency_step.issues.append(
                _issue(
                    "consistency",
                    f"model.consistency.{problem.code}",
                    f"{problem.panel}: {problem.message}",
                    severity=severity,
                )
            )
    except Exception as error:
        consistency_step.issues.append(_issue("consistency", "check.consistency_failed", error))
    checks.append(consistency_step)

    geometry_step = CheckStep("geometry")
    try:
        from .geometry_check import check_placement_geometry

        result = check_placement_geometry(project)
        if not result.get("ok", True):
            details = result.get("issues") or ["Обнаружены пересечения панелей."]
            geometry_step.issues.extend(
                _issue("geometry", "model.geometry", detail) for detail in details
            )
    except Exception as error:
        geometry_step.issues.append(_issue("geometry", "check.geometry_failed", error))
    checks.append(geometry_step)

    bounds_step = CheckStep("bounds")
    try:
        from .bounds_check import check_model_bounds

        bounds_step.issues.extend(
            _issue("bounds", "model.out_of_bounds", detail)
            for detail in check_model_bounds(project)
        )
    except Exception as error:
        bounds_step.issues.append(_issue("bounds", "check.bounds_failed", error))
    checks.append(bounds_step)

    cfrn_step = CheckStep("cfrn_encoding")
    try:
        from .cfrn import check_cfrn_encoding

        cfrn_step.issues.extend(
            _issue("cfrn_encoding", "cfrn.encoding", detail)
            for detail in check_cfrn_encoding(project)
        )
    except Exception as error:
        cfrn_step.issues.append(_issue("cfrn_encoding", "check.cfrn_encoding_failed", error))
    checks.append(cfrn_step)

    holes_step = CheckStep("cfrn_holes_parity")
    try:
        from .cfrn import check_cfrn_holes

        holes_step.issues.extend(
            _issue("cfrn_holes_parity", "cfrn.holes_parity", detail)
            for detail in check_cfrn_holes(project)
        )
    except Exception as error:
        holes_step.issues.append(_issue("cfrn_holes_parity", "check.cfrn_holes_failed", error))
    checks.append(holes_step)

    drilling_step = CheckStep("drilling_geometry")
    drilling: dict[str, list[str]] = {"errors": [], "warnings": []}
    try:
        from .drilling_check import check_drilling_geometry

        drilling = check_drilling_geometry(project)
        drilling_step.issues.extend(
            _issue("drilling_geometry", "drilling.geometry", detail)
            for detail in drilling.get("errors", [])
        )
        drilling_step.issues.extend(
            _issue("drilling_geometry", "drilling.warning", detail, severity="warning")
            for detail in drilling.get("warnings", [])
            if "система 32" not in detail
        )
    except Exception as error:
        drilling_step.issues.append(_issue("drilling_geometry", "check.drilling_failed", error))
    checks.append(drilling_step)

    system32_step = CheckStep("system_32")
    system32_step.issues.extend(
        _issue("system_32", "drilling.system_32", detail, severity="warning")
        for detail in drilling.get("warnings", [])
        if "система 32" in detail
    )
    checks.append(system32_step)

    purpose_step = CheckStep("purpose_registry")
    try:
        from .fasteners3d import registered_fastener_purposes as cfrn_purposes
        from .hardware import compute_drilling, registered_fastener_purposes as bom_purposes
        from .webviewer import registered_viewer_fastener_purposes as viewer_purposes

        emitted = {str(hole.get("purpose") or "") for hole in compute_drilling(project)}
        registries = {
            "BOM": bom_purposes(),
            "CFRN 3D": cfrn_purposes(),
            "viewer": viewer_purposes(),
        }
        for registry, purposes in registries.items():
            missing = sorted(emitted - purposes)
            if missing:
                purpose_step.issues.append(
                    _issue(
                        "purpose_registry",
                        "drilling.unregistered_purpose",
                        f"{registry}: нет purpose {', '.join(missing)}.",
                    )
                )
    except Exception as error:
        purpose_step.issues.append(
            _issue("purpose_registry", "check.purpose_registry_failed", error)
        )
    checks.append(purpose_step)

    completeness_step = CheckStep("completeness")
    try:
        from .completeness_check import check_completeness

        completeness_step.issues.extend(
            _issue("completeness", "model.incomplete", detail)
            for detail in check_completeness(project, spec)
        )
    except Exception as error:
        completeness_step.issues.append(
            _issue("completeness", "check.completeness_failed", error)
        )
    checks.append(completeness_step)

    materials_step = CheckStep("materials")
    material_refs: dict[str, Any] = {}
    try:
        from .materials import resolve_project_materials

        material_refs = resolve_project_materials(project)
        project["material_refs"] = material_refs
        for slot, value in material_refs.items():
            if isinstance(value, Mapping) and not value.get("resolved"):
                materials_step.issues.append(
                    _issue(
                        "materials",
                        "materials.unresolved",
                        f"Не выбрана производственная позиция для слота {slot}.",
                    )
                )
    except Exception as error:
        materials_step.issues.append(
            _issue("materials", "materials.resolve_failed", error)
        )
    for warning in project.get("warnings") or []:
        materials_step.issues.append(
            _issue("materials", "model.warning", warning, severity="warning")
        )
    for warning in project.get("estimated_values") or []:
        materials_step.issues.append(
            _issue("materials", "model.estimated_value", warning, severity="warning")
        )
    checks.append(materials_step)

    report = CheckReport(checks)
    return GateDecision(
        report=report,
        accepted_spec=copy.deepcopy(spec) if report.ok else None,
        project=project,
        material_refs=material_refs,
    )


def run_operation_repair_cycle(
    current_spec: Mapping[str, Any],
    operation_batches: Iterable[Sequence[Any]],
    reducer: OperationReducer,
    *,
    max_attempts: int = 3,
) -> GateDecision:
    """Try bounded repairs without publishing an intermediate red revision.

    Every mutation is delegated to the caller-supplied typed-operation reducer.
    The current revision is copied once and never passed to the reducer itself.
    """

    if max_attempts < 1:
        raise ValueError("max_attempts must be positive")
    working = copy.deepcopy(dict(current_spec))
    last = evaluate_production_gate(working)
    for attempt, operations in enumerate(operation_batches, start=1):
        if attempt > max_attempts:
            break
        reduced = reducer(copy.deepcopy(working), operations)
        candidate = _candidate_spec(reduced)
        last = evaluate_production_gate(reduced)
        if last.report.ok:
            return last
        if candidate is None:
            break
        working = candidate
    return GateDecision(
        report=last.report,
        accepted_spec=None,
        project=last.project,
        material_refs=last.material_refs,
    )
