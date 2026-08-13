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


def test_open_validates_and_builds_before_switching_current_product(tmp_path: Path) -> None:
    source_spec = json.loads(
        (ROOT / "paramspecs" / "komi_72_tumba_podkatnaya.json").read_text(
            encoding="utf-8"
        )
    )
    spec_dir = tmp_path / "paramspecs"
    spec_dir.mkdir()
    current_path = spec_dir / "current.json"
    valid_path = spec_dir / "valid.json"
    broken_path = spec_dir / "broken.json"
    current_path.write_text(json.dumps(source_spec), encoding="utf-8")

    valid = json.loads(json.dumps(source_spec))
    valid["project_name"] = "Новое изделие"
    valid["catalog"] = {
        "creator_user_id": "user-1",
        "responsible_user_id": "user-1",
        "author": "Автор",
        "responsible": "Автор",
    }
    valid.setdefault("hardware", {}).setdefault("drawer_guides", {})["with_closer"] = True
    valid["future_top_level"] = "tolerant read must not feed this to geometry"
    valid_path.write_text(json.dumps(valid, ensure_ascii=False), encoding="utf-8")
    broken = json.loads(json.dumps(valid))
    broken["dimensions"]["width"] = 10
    broken_path.write_text(json.dumps(broken, ensure_ascii=False), encoding="utf-8")

    studio = _Studio(current_path, tmp_path / "out")
    studio.workspaces.set_current(None, current_path)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(studio))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, raw = _request(
            server.server_port, "POST", "/api/open", {"file": "broken.json"}
        )
        rejected = json.loads(raw)
        assert status == 422
        assert rejected["code"] == "invalid_paramspec"
        assert studio.workspaces.current_spec_path(None).name == "current.json"

        status, raw = _request(
            server.server_port, "POST", "/api/open", {"file": "valid.json"}
        )
        opened = json.loads(raw)
        assert status == 200, raw
        assert opened["ok"] is True
        assert opened["payload"]["viewer"]["panels"]
        assert opened["paramspec_read"]["unknown_fields"] == ["future_top_level"]
        assert "future_top_level" not in opened["spec"]
        assert studio.workspaces.current_spec_path(None).name == "valid.json"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_version_restore_canonicalizes_before_browser_activation(tmp_path: Path) -> None:
    current = json.loads(
        (ROOT / "paramspecs" / "wardrobe_demo.json").read_text(encoding="utf-8")
    )
    spec_path = tmp_path / "wardrobe.json"
    spec_path.write_text(json.dumps(current, ensure_ascii=False), encoding="utf-8")
    historical = json.loads(json.dumps(current))
    historical["catalog"] = {
        "creator_user_id": "owner-1",
        "responsible_user_id": "designer-1",
    }
    historical["future_history_field"] = "must not become active"
    invalid = json.loads(json.dumps(current))
    invalid["dimensions"]["width"] = 10
    spec_path.with_suffix(".versions.json").write_text(
        json.dumps(
            [
                {"ts": "2026-08-13T12:00:00", "spec": historical},
                {"ts": "2026-08-13T12:01:00", "spec": invalid},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    studio = _Studio(spec_path, tmp_path / "out")
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(studio))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, raw = _request(
            server.server_port, "POST", "/api/restore", {"index": 0}
        )
        assert status == 200, raw
        restored = json.loads(raw)
        assert "future_history_field" not in restored["spec"]
        assert restored["spec"]["catalog"] == historical["catalog"]
        assert restored["paramspec_read"]["unknown_fields"] == [
            "future_history_field"
        ]

        status, raw = _request(
            server.server_port, "POST", "/api/restore", {"index": 1}
        )
        assert status == 422, raw
        assert json.loads(raw)["code"] == "invalid_paramspec"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_all_catalog_writes_are_strict_for_unknown_fields(tmp_path: Path) -> None:
    spec = json.loads(
        (ROOT / "paramspecs" / "komi_72_tumba_podkatnaya.json").read_text(
            encoding="utf-8"
        )
    )
    spec_path = tmp_path / "item.json"
    original = json.dumps(spec, ensure_ascii=False)
    spec_path.write_text(original, encoding="utf-8")
    candidate = json.loads(json.dumps(spec))
    candidate["future_top_level"] = True
    duplicate_source = tmp_path / "duplicate_source.json"
    duplicate_source.write_text(
        json.dumps(candidate, ensure_ascii=False), encoding="utf-8"
    )

    studio = _Studio(spec_path, tmp_path / "out")
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(studio))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, raw = _request(
            server.server_port, "POST", "/api/save", {"spec": candidate}
        )
        rejected = json.loads(raw)
        assert status == 422
        assert rejected["code"] == "invalid_paramspec"
        assert any("future_top_level" in item for item in rejected["details"])
        assert spec_path.read_text(encoding="utf-8") == original

        status, raw = _request(
            server.server_port,
            "POST",
            "/api/rename",
            {"file": "duplicate_source.json", "name": "Каноническая запись"},
        )
        assert status == 200, raw
        renamed = json.loads(duplicate_source.read_text(encoding="utf-8"))
        assert renamed["project_name"] == "Каноническая запись"
        assert "future_top_level" not in renamed
        revisions = json.loads(
            (tmp_path / "duplicate_source.versions.json").read_text(encoding="utf-8")
        )
        assert revisions
        assert all("future_top_level" not in item["spec"] for item in revisions)

        candidate["catalog"] = {
            "creator_user_id": "owner-1",
            "responsible_user_id": "owner-1",
        }
        duplicate_source.write_text(
            json.dumps(candidate, ensure_ascii=False), encoding="utf-8"
        )
        status, raw = _request(
            server.server_port,
            "POST",
            "/api/catalog/assign",
            {"file": "duplicate_source.json", "responsible_user_id": ""},
        )
        assert status == 200, raw
        assigned = json.loads(duplicate_source.read_text(encoding="utf-8"))
        assert "future_top_level" not in assigned
        assert assigned["catalog"].get("responsible_user_id") == ""

        duplicate_source.write_text(
            json.dumps(candidate, ensure_ascii=False), encoding="utf-8"
        )
        status, raw = _request(
            server.server_port,
            "POST",
            "/api/duplicate",
            {"file": "duplicate_source.json", "stay_catalog": True},
        )
        assert status == 200, raw
        duplicate = json.loads(
            (tmp_path / "duplicate_source_copy.json").read_text(encoding="utf-8")
        )
        assert "future_top_level" not in duplicate
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

