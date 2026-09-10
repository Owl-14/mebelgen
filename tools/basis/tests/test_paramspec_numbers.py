"""Saving an unchanged product must not rewrite its file.

The Pydantic contract validates millimetres as floats, but persisted JSON keeps
the integer spelling people write by hand, and ``/api/save`` keeps the key
order of the file already on disk.  Otherwise every save produced a diff in
``paramspecs/`` (``700`` -> ``700.0``, ``archetype`` moved, newline lost).
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

from src.paramspec import parse_paramspec  # noqa: E402
from src.paramspec_versioning import read_paramspec_v1  # noqa: E402
from src.studio import _Studio, make_handler  # noqa: E402


def _spec_files() -> list[Path]:
    return sorted(
        path for path in (ROOT / "paramspecs").glob("*.json")
        if not path.name.endswith((".project.json", ".versions.json"))
    )


def test_generator_dict_keeps_integer_spelling() -> None:
    raw = json.loads(
        (ROOT / "paramspecs" / "cabinet_700x400x500.json").read_text(encoding="utf-8")
    )
    raw["dimensions"]["depth"] = 412.5
    dumped = parse_paramspec(raw).to_generator_dict()
    assert dumped["dimensions"]["width"] == 700
    assert isinstance(dumped["dimensions"]["width"], int)
    assert dumped["materials"]["board_thickness"] == 16
    assert isinstance(dumped["materials"]["board_thickness"], int)
    assert dumped["dimensions"]["depth"] == 412.5


def test_repository_specs_are_already_canonical() -> None:
    noisy = []
    for path in _spec_files():
        envelope = read_paramspec_v1(json.loads(path.read_text(encoding="utf-8")))
        if envelope.unknown_fields:
            noisy.append((path.name, "unknown", envelope.unknown_fields))
        elif envelope.canonical_changed:
            # A hand-written ``2.0`` is the only accepted spelling difference.
            noisy.append((path.name, "canonical_changed"))
    assert noisy in ([], [("kitchen_1860x630x2400.json", "canonical_changed")]), noisy


def _request(port: int, method: str, path: str, payload: dict | None = None):
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"} if body is not None else {}
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    connection.request(method, path, body=body, headers=headers)
    response = connection.getresponse()
    result = response.status, response.read()
    connection.close()
    return result


def test_save_of_unchanged_product_is_byte_identical(tmp_path: Path) -> None:
    source = json.loads(
        (ROOT / "paramspecs" / "wardrobe_demo.json").read_text(encoding="utf-8")
    )
    spec_dir = tmp_path / "paramspecs"
    spec_dir.mkdir()
    spec_path = spec_dir / "wardrobe_demo.json"
    original = json.dumps(source, ensure_ascii=False, indent=2) + "\n"
    spec_path.write_text(original, encoding="utf-8")

    studio = _Studio(spec_path, tmp_path / "out")
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(studio))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, raw = _request(server.server_port, "GET", "/")
        assert status == 200
        page = raw.decode("utf-8")
        marker = "let SPEC = "
        start = page.index(marker) + len(marker)
        shown = json.loads(page[start:page.index(";\n", start)])

        status, raw = _request(
            server.server_port,
            "POST",
            "/api/save",
            {"spec": shown, "project_file": "wardrobe_demo.json"},
        )
        assert status == 200, raw
        assert spec_path.read_text(encoding="utf-8") == original
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
