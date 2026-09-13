"""Клиент БАЗИС-Облако Tasks API (AKD-16/AKD-17).

Публичный API: https://cloud.bazissoft.ru/api-cloud-tasks-public/tasks
Эндпоинты: список задач, статус, удаление, конвертация модели (.b3d↔.cfrn) и
чертежа (PDF/JPEG/WMF/SVG), скачивание результата.

ВАЖНО (AKD-17): тип задачи 0=ExecuteClientScript существует в CloudTaskTypeEnum,
но публичных POST-эндпоинтов для его СОЗДАНИЯ нет — только model-convert и
drawing-convert. Значит запустить клиентский скрипт через публичное облако нельзя;
скрипт-импорт выполняется в десктопном БАЗИС.

Ключ: env BAZIS_API_KEY. Заголовок — `apiKey` (подтверждено в
/openapi-tasks/swagger/api-key.js); переопределяется BAZIS_API_KEY_HEADER.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import requests

BASE_URL = os.environ.get("BAZIS_CLOUD_URL", "https://cloud.bazissoft.ru")
PREFIX = "/api-cloud-tasks-public/tasks"

TASK_STATE = {0: "Created", 1: "Running", 2: "Success", 3: "Failed"}
TASK_TYPE = {0: "ExecuteClientScript", 1: "DrawingConvertation", 2: "Model3DConvertation"}
MODEL_CONVERT = {"b3d-to-cfrn": 0, "cfrn-to-b3d": 1}            # Model3DConvertTypeEnum
DRAWING_FORMAT = {"pdf": 0, "jpeg": 1, "wmf": 2, "svg": 3}      # DrawingConvertFormatEnum

# Платные конвертации (model-convert / drawing-convert) отключены: .b3d собираем
# сами (local-b3d build). Включить обратно — только осознанно: BAZIS_CLOUD_PAID=1.
PAID_ENV = "BAZIS_CLOUD_PAID"
PAID_DISABLED_MESSAGE = (
    "Платные операции БАЗИС-Облака отключены. Нативный .b3d собирается локально: "
    f"`main.py local-b3d build <paramspec>`. Включить облако: {PAID_ENV}=1."
)


def paid_cloud_enabled() -> bool:
    return os.environ.get(PAID_ENV) == "1"


def require_paid_cloud_enabled() -> None:
    if not paid_cloud_enabled():
        raise RuntimeError(PAID_DISABLED_MESSAGE)


class CloudTasksClient:
    def __init__(self, api_key: str | None = None, base_url: str | None = None,
                 key_header: str | None = None) -> None:
        self.api_key = api_key or os.environ.get("BAZIS_API_KEY")
        self.base = (base_url or BASE_URL).rstrip("/")
        self.key_header = key_header or os.environ.get("BAZIS_API_KEY_HEADER", "apiKey")

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            raise RuntimeError("BAZIS_API_KEY не задан (env). Получите ключ в кабинете БАЗИС-Облако.")
        return {self.key_header: self.api_key}

    def _url(self, suffix: str = "") -> str:
        return f"{self.base}{PREFIX}{suffix}"

    def list_tasks(self, page_size: int = 20, page_index: int = 0,
                   state: int | None = None, type: int | None = None) -> Any:
        params: dict[str, Any] = {"PageSize": page_size, "PageIndex": page_index}
        if state is not None:
            params["state"] = state
        if type is not None:
            params["type"] = type
        r = requests.get(self._url(), headers=self._headers(), params=params, timeout=30)
        r.raise_for_status()
        return r.json()

    def get_task(self, task_id: int) -> Any:
        r = requests.get(self._url(f"/{task_id}"), headers=self._headers(), timeout=30)
        r.raise_for_status()
        return r.json()

    def delete_task(self, task_id: int) -> int:
        r = requests.delete(self._url(f"/{task_id}"), headers=self._headers(), timeout=30)
        r.raise_for_status()
        return r.status_code

    def model_convert(self, files: list[str], convert_type: int) -> Any:
        require_paid_cloud_enabled()
        payload = [("models", (Path(f).name, open(f, "rb"))) for f in files]  # noqa: SIM115
        try:
            r = requests.post(self._url("/model-convert"), headers=self._headers(),
                              params={"convertType": convert_type}, files=payload, timeout=300)
        finally:
            for _, (_, fh) in payload:
                fh.close()
        r.raise_for_status()
        return r.json()

    def drawing_convert(self, files: list[str], fmt: int) -> Any:
        require_paid_cloud_enabled()
        payload = [("drawings", (Path(f).name, open(f, "rb"))) for f in files]  # noqa: SIM115
        try:
            r = requests.post(self._url("/drawing-convert"), headers=self._headers(),
                              params={"format": fmt}, files=payload, timeout=300)
        finally:
            for _, (_, fh) in payload:
                fh.close()
        r.raise_for_status()
        return r.json()

    def download_result(self, task_id: int, out_path: str | Path) -> Path:
        r = requests.get(self._url(f"/{task_id}/download-result"), headers=self._headers(),
                         timeout=300, stream=True)
        r.raise_for_status()
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("wb") as f:
            for chunk in r.iter_content(8192):
                f.write(chunk)
        return out

    def poll(self, task_id: int, *, timeout: float = 300, interval: float = 3) -> Any:
        """Опрашивать задачу до Success/Failed или таймаута. Возвращает финальный объект."""
        deadline = time.monotonic() + timeout
        while True:
            task = self.get_task(task_id)
            state = task.get("state", task.get("State"))
            if state in (2, 3):
                return task
            if time.monotonic() > deadline:
                raise TimeoutError(f"Задача {task_id} не завершилась за {timeout}с (state={state}).")
            time.sleep(interval)


def api_overview() -> str:
    """Текстовая карта API (для CLI/доков)."""
    lines = [
        f"БАЗИС-Облако Tasks API: {BASE_URL}{PREFIX}",
        "  GET    /tasks?PageSize&PageIndex[&state&type]  — список",
        "  GET    /tasks/{id}                              — статус",
        "  DELETE /tasks/{id}                              — удалить",
        "  POST   /tasks/model-convert?convertType=        — .b3d↔.cfrn (multipart models)",
        "  POST   /tasks/drawing-convert?format=           — PDF/JPEG/WMF/SVG (multipart drawings)",
        "  GET    /tasks/{id}/download-result              — результат",
        f"state: {TASK_STATE}",
        f"type:  {TASK_TYPE}",
        f"model convertType: {MODEL_CONVERT}",
        f"drawing format: {DRAWING_FORMAT}",
        "AKD-17: ExecuteClientScript (type 0) НЕ создаётся публичным API — только convert.",
    ]
    return "\n".join(lines)
