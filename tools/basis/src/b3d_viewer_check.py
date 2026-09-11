"""Проверка «БАЗИС открывает файл» через бесплатный БАЗИС-Просмотр 3D (Windows).

Просмотр не даёт кода возврата, зато ведёт себя предсказуемо (эксперименты
2026-09-11): у принятого файла в течение ~10 с появляется главное окно
(класс ``TFrmViewer``), у отклонённого — модальный диалог ``#32770``
«Ошибка чтения файла …» и главного окна нет. Здесь это читается через
``EnumWindows`` без UI Automation и без скриншотов; процесс Просмотра
завершается принудительно после проверки.

Это проверка открываемости, а не корректности геометрии: её даёт
``b3d_verify.verify_b3d_parity``.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

DEFAULT_VIEWER = Path(os.environ.get("BAZIS_VIEWER", r"D:\bazis\viewer.exe"))
_MAIN_WINDOW_CLASS = "TFrmViewer"
_DIALOG_CLASS = "#32770"


def viewer_available(viewer: Path | None = None) -> bool:
    return sys.platform == "win32" and (viewer or DEFAULT_VIEWER).is_file()


def _windows_of(pid: int) -> list[tuple[str, str]]:
    """(class, title) видимых окон верхнего уровня процесса."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    result: list[tuple[str, str]] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _lparam):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            cls = ctypes.create_unicode_buffer(256)
            title = ctypes.create_unicode_buffer(512)
            user32.GetClassNameW(hwnd, cls, 256)
            user32.GetWindowTextW(hwnd, title, 512)
            result.append((cls.value, title.value))
        return True

    user32.EnumWindows(visit, 0)
    return result


def check_with_viewer(
    b3d_path: str | Path, *, viewer: Path | None = None, timeout_s: float = 30.0
) -> dict[str, Any]:
    """Открыть файл в Просмотре и сказать, принял ли он его.

    Возвращает ``{"status": "accepted" | "rejected" | "timeout" | "unavailable",
    "seconds": …, "windows": […], "detail": …}``.
    """
    exe = viewer or DEFAULT_VIEWER
    if not viewer_available(exe):
        return {"status": "unavailable", "seconds": 0.0, "windows": [],
                "detail": f"БАЗИС-Просмотр не найден: {exe} (env BAZIS_VIEWER)"}
    path = Path(b3d_path).resolve()
    started = time.monotonic()
    process = subprocess.Popen([str(exe), str(path)])
    status, windows, detail = "timeout", [], "главное окно не появилось"
    try:
        while time.monotonic() - started < timeout_s:
            time.sleep(0.5)
            if process.poll() is not None:
                status, detail = "rejected", f"Просмотр завершился с кодом {process.returncode}"
                break
            windows = _windows_of(process.pid)
            if any(cls == _MAIN_WINDOW_CLASS for cls, _title in windows):
                status, detail = "accepted", "открыто главное окно Просмотра"
                break
            dialog = next((title for cls, title in windows if cls == _DIALOG_CLASS), None)
            if dialog is not None:
                status, detail = "rejected", f"диалог «{dialog}»"
                break
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
    return {
        "status": status,
        "seconds": round(time.monotonic() - started, 1),
        "windows": [f"{cls} [{title}]" for cls, title in windows],
        "detail": detail,
    }


__all__ = ["DEFAULT_VIEWER", "check_with_viewer", "viewer_available"]
