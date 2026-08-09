"""Canonical catalog previews are rendered from a requested tenant product."""

from __future__ import annotations

import base64
import http.client
import json
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.studio import _Studio, make_handler  # noqa: E402


def _request(
    port: int, method: str, path: str, payload: dict | None = None
) -> tuple[int, bytes]:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"} if body is not None else {}
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    connection.request(method, path, body=body, headers=headers)
    response = connection.getresponse()
    result = response.status, response.read()
    connection.close()
    return result


def test_preview_source_and_revision_checked_cache(tmp_path: Path) -> None:
    source_spec = json.loads(
        (ROOT / "paramspecs" / "komi_72_tumba_podkatnaya.json").read_text(
            encoding="utf-8"
        )
    )
    spec_dir = tmp_path / "paramspecs"
    spec_dir.mkdir()
    current_path = spec_dir / "current.json"
    other_path = spec_dir / "other.json"
    current_path.write_text(json.dumps(source_spec), encoding="utf-8")
    other_spec = json.loads(json.dumps(source_spec))
    other_spec["project_name"] = "Другое изделие"
    other_path.write_text(json.dumps(other_spec), encoding="utf-8")

    studio = _Studio(current_path, tmp_path / "out")
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(studio))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, raw = _request(
            server.server_port,
            "POST",
            "/api/catalog-preview-source",
            {"file": "other.json"},
        )
        assert status == 200, raw
        source = json.loads(raw)
        assert source["ok"] is True
        assert source["file"] == "other.json"
        assert source["viewer"]["panels"]
        assert studio.workspaces.current_spec_path(None).name == "current.json"

        png = b"\x89PNG\r\n\x1a\ncanonical-preview"
        preview = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
        status, raw = _request(
            server.server_port,
            "POST",
            "/api/catalog-preview",
            {"file": "other.json", "revision": "wrong", "preview": preview},
        )
        assert status == 409
        assert json.loads(raw)["code"] == "stale_preview"

        status, raw = _request(
            server.server_port,
            "POST",
            "/api/catalog-preview",
            {
                "file": "other.json",
                "revision": source["revision"],
                "preview": preview,
            },
        )
        assert status == 200, raw
        saved = json.loads(raw)
        assert saved["ok"] is True
        status, raw = _request(server.server_port, "GET", saved["url"])
        assert status == 200
        assert raw == png
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_chat_cancellation_ids_are_validated_and_consumed(tmp_path: Path) -> None:
    spec_path = tmp_path / "item.json"
    spec_path.write_text(
        json.dumps({"schemaVersion": "paramspec-v1", "draft": True, "project_name": "Черновик"}),
        encoding="utf-8",
    )
    studio = _Studio(spec_path, tmp_path / "out")
    assert studio.cancel_chat_operation("operation-123") is True
    assert studio.consume_chat_cancellation("operation-123") is True
    assert studio.consume_chat_cancellation("operation-123") is False
    assert studio.cancel_chat_operation("../bad") is False

