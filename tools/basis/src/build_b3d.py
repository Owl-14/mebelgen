"""Сборка нативного .b3d из ParamSpec/project через облако (device-independent).

Поток без десктопа БАЗИС:
  ParamSpec → generate → панели → .cfrn (мы собираем) → облако CfrnToB3d → .b3d

ВНИМАНИЕ: каждая конвертация — платная операция БАЗИС-Облака (~10 ₽).
Ключ — env BAZIS_API_KEY.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from .b3d_preflight import B3DPreflightError, require_b3d_project_preflight
from .cfrn import project_to_cfrn_bytes
from .cloud_api import CloudTasksClient


def build_b3d(project: dict[str, Any], out_path: str | Path, *,
              client: CloudTasksClient | None = None, timeout: float = 180,
              skip_encoding_check: bool = False) -> dict[str, Any]:
    """project.json (панели) → .cfrn → облако CfrnToB3d → .b3d на диск. Возвращает отчёт."""
    # ``skip_encoding_check`` оставлен только для совместимости вызовов. Он
    # больше не ослабляет safety boundary: любой платный вызов проходит весь
    # offline preflight, включая encoding, holes, drilling и materials.
    _ = skip_encoding_check
    preflight = require_b3d_project_preflight(project)
    project = preflight.project
    client = client or CloudTasksClient()
    cfrn = project_to_cfrn_bytes(project)
    with tempfile.NamedTemporaryFile("wb", suffix=".cfrn", delete=False) as tf:
        tf.write(cfrn)
        cfrn_path = tf.name
    try:
        task_id = int(client.model_convert([cfrn_path], convert_type=1))   # 1 = CfrnToB3d
        task = client.poll(task_id, timeout=timeout)
    finally:
        Path(cfrn_path).unlink(missing_ok=True)
    state = task.get("state")
    if state != 2:
        raise RuntimeError(f"Конвертация не удалась (state={state}): {task.get('errorMessage')}")
    out = Path(out_path)
    client.download_result(task_id, out)
    return {"task_id": task_id, "b3d": str(out), "bytes": out.stat().st_size}


def build_b3d_from_paramspec(spec: dict[str, Any], out_path: str | Path, **kw: Any) -> dict[str, Any]:
    from .production_gate import evaluate_production_gate

    decision = evaluate_production_gate(spec)
    if not decision.report.ok or decision.project is None:
        raise B3DPreflightError(decision.report.to_dict())
    return build_b3d(decision.project, out_path, **kw)
