"""Клиент БАЗИС-Облако Cutting Public API (AKD-18).

https://cloud.bazissoft.ru/api-cutting-public  (Swagger: /openapi/index.html)
Производственный контур после .b3d: заказ → загрузка модели → материалы →
раскрой/производственные файлы/ЧПУ → скачивание результатов.

Ключ — тот же env BAZIS_API_KEY, заголовок apiKey (см. cloud_api).
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import requests

from .cloud_api import BASE_URL
from .material_link_contract import serialize_link_payload

PREFIX = "/api-cutting-public"


class CuttingClient:
    def __init__(self, api_key: str | None = None, base_url: str | None = None,
                 key_header: str | None = None) -> None:
        self.api_key = api_key or os.environ.get("BAZIS_API_KEY")
        self.base = (base_url or BASE_URL).rstrip("/")
        self.key_header = key_header or os.environ.get("BAZIS_API_KEY_HEADER", "apiKey")

    def _h(self) -> dict[str, str]:
        if not self.api_key:
            raise RuntimeError("BAZIS_API_KEY не задан (env). Получите ключ в кабинете БАЗИС-Облако.")
        return {self.key_header: self.api_key}

    def _u(self, suffix: str) -> str:
        return f"{self.base}{PREFIX}{suffix}"

    def _get(self, suffix: str, **params: Any) -> Any:
        r = requests.get(self._u(suffix), headers=self._h(), params=params or None, timeout=60)
        r.raise_for_status()
        return r.json() if "application/json" in r.headers.get("content-type", "") else r.text

    def _post(self, suffix: str, *, json: Any = None, params: Any = None, files: Any = None) -> Any:
        r = requests.post(self._u(suffix), headers=self._h(), json=json, params=params, files=files, timeout=300)
        r.raise_for_status()
        return r.json() if "application/json" in r.headers.get("content-type", "") else r.text

    # --- заказы ---
    def list_orders(self, page_index: int = 0, page_size: int = 20, **filters: Any) -> Any:
        return self._get("/orders", pageIndex=page_index, pageSize=page_size, **filters)

    def create_order(self, payload: dict[str, Any]) -> Any:
        return self._post("/orders", json=payload)

    def get_order(self, order_id: int) -> Any:
        return self._get(f"/orders/{order_id}")

    def order_details(self, order_id: int) -> Any:
        return self._get(f"/orders/{order_id}/details")

    def order_specification(self, order_id: int) -> Any:
        return self._get(f"/orders/{order_id}/specification")

    # --- модели в заказе ---
    def list_cad_models(self, order_id: int) -> Any:
        return self._get("/cad-models", orderId=order_id)

    def upload_cad_model(self, order_id: int, file: str, count: int = 1, cut_models: bool = True) -> Any:
        with open(file, "rb") as fh:
            return self._post("/cad-models", params={"orderId": order_id, "count": count, "cutModels": cut_models},
                              files={"file": (Path(file).name, fh)})

    def cad_model_materials(self, model_id: int) -> Any:
        return self._get(f"/cad-models/{model_id}/materials")

    def set_link_materials(self, model_id: int, links: Any) -> Any:
        """Post only the documented ``CuttingMaterialLinkDTO`` array.

        Target MatBase names must already be confirmed.  This method validates
        transport shape; it does not infer names from local articles.
        """
        return self._post(
            f"/cad-models/{model_id}/set-link-materials",
            json=serialize_link_payload(links),
        )

    def cutting_materials(self, order_id: int) -> Any:
        """Read linked MatBase ids, articles and configured sheet items."""
        return self._get("/cutting-materials", orderId=order_id)

    def cutted_materials(self, order_id: int) -> Any:
        """Read post-cut material statistics used as production evidence."""
        return self._get(f"/orders/{order_id}/cutted-materials")

    # --- производство ---
    def run_production_files(self, order_id: int) -> Any:
        return self._post(f"/orders/{order_id}/run-generation-production-files")

    def production_files_url(self, order_id: int) -> Any:
        return self._get(f"/orders/{order_id}/production-files-url")

    def run_control_program(self, order_id: int) -> Any:
        return self._post(f"/orders/{order_id}/run-generation-control-program-files")

    def control_program_url(self, order_id: int) -> Any:
        return self._get(f"/orders/{order_id}/control-program-files-url")

    # --- длинные задачи ---
    def list_long_tasks(self) -> Any:
        return self._get("/long-tasks")

    def long_task(self, task_id: int) -> Any:
        return self._get(f"/long-tasks/{task_id}")

    def poll_long_task(self, task_id: int, *, timeout: float = 600, interval: float = 5) -> Any:
        deadline = time.monotonic() + timeout
        while True:
            t = self.long_task(task_id)
            st = str(t.get("state", t.get("status", ""))).lower()
            if st in ("success", "failed", "completed", "error", "2", "3"):
                return t
            if time.monotonic() > deadline:
                raise TimeoutError(f"long-task {task_id} не завершилась за {timeout}с.")
            time.sleep(interval)


def api_overview() -> str:
    return "\n".join([
        f"БАЗИС-Облако Cutting Public API: {BASE_URL}{PREFIX}",
        "Поток производства после .b3d:",
        "  POST /orders                                   — создать заказ",
        "  POST /cad-models?orderId&count&cutModels       — загрузить модель (.b3d/.cfrn) в заказ",
        "  GET  /cad-models/{id}/materials                — материалы модели",
        "  POST /cad-models/{id}/set-link-materials       — связать материалы с базой",
        "  GET  /cutting-materials?orderId                — MatBase id, артикулы и листы",
        "  GET  /orders/{id}/cutted-materials             — результат и статистика раскроя",
        "  POST /orders/{id}/run-generation-production-files",
        "  GET  /orders/{id}/production-files-url          — ссылка на производственные файлы",
        "  POST /orders/{id}/run-generation-control-program-files (ЧПУ)",
        "  GET  /orders/{id}/specification | /cutting-maps | /details",
        "  GET  /long-tasks/{id}                          — статус длинной задачи",
        "Ключ: env BAZIS_API_KEY (заголовок apiKey).",
    ])
