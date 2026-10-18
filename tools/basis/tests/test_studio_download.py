"""Скачивание собранного .b3d/.cfrn из Studio с другого компьютера."""

from __future__ import annotations

import json
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.studio import PAGE, _downloadable_result, _Studio, make_handler  # noqa: E402


def _studio(tmp_path: Path):
    spec_dir = tmp_path / "specs"
    spec_dir.mkdir()
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    spec_path = spec_dir / "tumba.json"
    spec_path.write_text(
        (ROOT / "paramspecs" / "tumba_moderatora.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(_Studio(spec_path, out_dir)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, f"http://127.0.0.1:{server.server_address[1]}", out_dir


def _status(url: str, **kw) -> int:
    try:
        with urlopen(Request(url, **kw)) as response:
            return response.status
    except HTTPError as error:
        return error.code


def test_downloadable_result_rejects_traversal_and_foreign_suffixes(tmp_path: Path) -> None:
    (tmp_path / "a.b3d").write_bytes(b"x")
    (tmp_path / "a.json").write_text("{}", encoding="utf-8")
    assert _downloadable_result(tmp_path, "a.b3d") == (tmp_path / "a.b3d").resolve()
    for bad in ("", "a.json", "../a.b3d", "sub/a.b3d", "..\\a.b3d", ".hidden.b3d", "missing.b3d"):
        assert _downloadable_result(tmp_path, bad) is None, bad


def test_download_route_serves_built_file_as_attachment(tmp_path: Path) -> None:
    server, thread, base, out_dir = _studio(tmp_path)
    try:
        payload = b"\x0f\x00b3d-bytes"
        (out_dir / "шкаф 1.b3d").write_bytes(payload)
        (tmp_path / "secret.b3d").write_bytes(b"outside")
        with urlopen(base + "/download/%D1%88%D0%BA%D0%B0%D1%84%201.b3d") as response:
            assert response.status == 200
            assert response.read() == payload
            disposition = response.headers["Content-Disposition"]
            assert disposition.startswith("attachment;")
            assert "filename*=UTF-8''%D1%88%D0%BA%D0%B0%D1%84%201.b3d" in disposition
        for url in ("/download/missing.b3d", "/download/..%2Fsecret.b3d",
                    "/download/%2e%2e/secret.b3d", "/download/tumba.json"):
            assert _status(base + url) == 404, url
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_open_file_is_refused_behind_proxy(tmp_path: Path) -> None:
    server, thread, base, out_dir = _studio(tmp_path)
    try:
        (out_dir / "x.b3d").write_bytes(b"x")
        body = json.dumps({"path": str(out_dir / "x.b3d")}).encode("utf-8")
        code = _status(base + "/api/open-file", data=body, method="POST", headers={
            "Content-Type": "application/json", "X-Real-IP": "203.0.113.7"})
        assert code == 403
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_page_downloads_results_for_remote_visitors() -> None:
    assert "const LOCAL_STUDIO=" in PAGE
    assert "if(!LOCAL_STUDIO){downloadResult(p.download);return;}" in PAGE
    assert "data-download=" in PAGE
