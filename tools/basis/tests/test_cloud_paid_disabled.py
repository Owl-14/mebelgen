"""Платные операции БАЗИС-Облака отключены по умолчанию; Studio без облачной сборки."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import main as basis_cli  # noqa: E402
import src.cloud_api as cloud_api  # noqa: E402
from src.studio import PAGE  # noqa: E402


@pytest.fixture(autouse=True)
def _no_paid_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(cloud_api.PAID_ENV, raising=False)


def test_paid_client_calls_refuse_before_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_network(*_a, **_kw):
        raise AssertionError("платный запрос ушёл в сеть")

    monkeypatch.setattr(cloud_api.requests, "post", no_network)
    client = cloud_api.CloudTasksClient(api_key="test")
    with pytest.raises(RuntimeError, match="отключены"):
        client.model_convert(["x.cfrn"], 1)
    with pytest.raises(RuntimeError, match="отключены"):
        client.drawing_convert(["x.cfrn"], 0)


@pytest.mark.parametrize("argv", [
    ["main.py", "build-b3d", "paramspecs/tumba_moderatora.json"],
    ["main.py", "cloud", "drawing-convert", "x.cfrn", "--format", "pdf"],
    ["main.py", "cloud", "model-convert", "x.b3d", "--type", "b3d-to-cfrn"],
])
def test_paid_cli_commands_are_disabled(
    argv: list[str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", argv)
    assert basis_cli.main() == 2
    assert "Платные операции БАЗИС-Облака отключены" in capsys.readouterr().err


def test_studio_has_no_cloud_build() -> None:
    assert 'id="btnB3d"' not in PAGE
    assert "/api/build-b3d'" not in PAGE
    assert "через облако" not in PAGE
    assert "total_spent" not in PAGE
    assert 'id="btnB3dLocal"' in PAGE
