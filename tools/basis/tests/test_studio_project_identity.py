"""The product the operator opened stays the product Studio acts on.

Regression for the post-MEB-107 behaviour where the first catalog file in
alphabetical order silently replaced the spec passed on the command line, and
where stateful routes (versions, export, restore) ignored the file the browser
was showing.
"""

from __future__ import annotations

import http.client
import json
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.studio import _Studio, make_handler  # noqa: E402
from src.studio_tenants import TenantWorkspaceManager  # noqa: E402


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


def _two_specs(tmp_path: Path) -> tuple[Path, Path]:
    source = json.loads(
        (ROOT / "paramspecs" / "cabinet_700x400x500.json").read_text(encoding="utf-8")
    )
    spec_dir = tmp_path / "paramspecs"
    spec_dir.mkdir()
    first = spec_dir / "a-first.json"
    second = spec_dir / "b-second.json"
    first.write_text(json.dumps(source), encoding="utf-8")
    other = json.loads(json.dumps(source))
    other["project_name"] = "Второе изделие"
    other["dimensions"]["depth"] = 450
    second.write_text(json.dumps(other), encoding="utf-8")
    return first, second


def test_cli_spec_wins_over_alphabetical_order(tmp_path: Path) -> None:
    first, second = _two_specs(tmp_path)
    manager = TenantWorkspaceManager(second, tmp_path / "out")
    assert manager.current_spec_path(None) == second.resolve()

    # Explicit selection still works and survives a cleared session:
    manager.select(None, first.name)
    assert manager.current_spec_path(None) == first.resolve()
    manager.clear_session(None)
    assert manager.current_spec_path(None) == second.resolve()


def test_page_and_stateful_routes_follow_the_opened_product(tmp_path: Path) -> None:
    first, second = _two_specs(tmp_path)
    studio = _Studio(second, tmp_path / "out")
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(studio))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, raw = _request(server.server_port, "GET", "/")
        assert status == 200
        page = raw.decode("utf-8")
        assert 'CAT_CURRENT_FILE="b-second.json"' in page
        assert "Второе изделие" in page

        # A route bound to the browser's product switches the server selection.
        status, raw = _request(
            server.server_port, "POST", "/api/versions", {"project_file": "a-first.json"}
        )
        assert status == 200, raw
        assert studio.workspaces.current_spec_path(None).name == "a-first.json"

        # Export without an explicit identity is refused when it is ambiguous,
        # instead of silently writing under another product's name.
        status, raw = _request(
            server.server_port,
            "POST",
            "/api/export-cfrn",
            {"spec": json.loads(second.read_text(encoding="utf-8"))},
        )
        assert status == 409
        assert json.loads(raw)["code"] == "project_identity_required"

        # Unknown identity is a 404, not a fallback to the first file.
        status, raw = _request(
            server.server_port, "POST", "/api/versions", {"project_file": "missing.json"}
        )
        assert status == 404
        assert json.loads(raw)["code"] == "project_not_found"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
