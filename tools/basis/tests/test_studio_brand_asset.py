"""Контракт точной пользовательской марки в верхней левой части Studio."""

from __future__ import annotations

import json
import re
import struct
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.studio import PAGE, _Studio, make_handler  # noqa: E402


ASSETS = {
    "studioBrandWordmark": ("akeda-studio-wordmark.png", (520, 84), "Akeda Studio"),
    "studioBrandMark": ("akeda-studio-mark.png", (216, 98), ""),
}


def _png_size(data: bytes) -> tuple[int, int]:
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    assert data[12:16] == b"IHDR"
    return struct.unpack(">II", data[16:24])


def test_brand_crops_are_small_exact_png_assets() -> None:
    for _element_id, (name, dimensions, _alt) in ASSETS.items():
        path = ROOT / "assets" / "studio" / name
        data = path.read_bytes()
        assert len(data) < 64 * 1024
        assert _png_size(data) == dimensions


def test_brand_dom_uses_local_assets_with_intrinsic_dimensions() -> None:
    assert '<h1 id="studioBrand" class="studio-brand">' in PAGE
    assert '<span class="studio-brand-tagline">от ТЗ до производства</span>' in PAGE
    for element_id, (name, dimensions, alt) in ASSETS.items():
        match = re.search(rf'<img\s+id="{element_id}".*?>', PAGE, flags=re.DOTALL)
        assert match, f"Не найден #{element_id}"
        tag = match.group(0)
        assert f'src="/assets/studio/{name}"' in tag
        assert f'width="{dimensions[0]}"' in tag
        assert f'height="{dimensions[1]}"' in tag
        assert f'alt="{alt}"' in tag
        assert "data:" not in tag
    assert 'id="studioBrandMark"' in PAGE and 'aria-hidden="true"' in PAGE


def test_brand_routes_are_exact_and_return_tracked_bytes(tmp_path: Path) -> None:
    spec_path = tmp_path / "studio.json"
    source_spec = ROOT / "paramspecs" / "tumba_moderatora.json"
    spec_path.write_text(json.dumps(json.loads(source_spec.read_text(encoding="utf-8"))),
                         encoding="utf-8")
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(_Studio(spec_path, tmp_path)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        for _element_id, (name, _dimensions, _alt) in ASSETS.items():
            with urlopen(f"{base}/assets/studio/{name}") as response:
                assert response.status == 200
                assert response.headers.get_content_type() == "image/png"
                assert response.read() == (ROOT / "assets" / "studio" / name).read_bytes()
        for url in (
            "/assets/studio/unknown.png",
            "/assets/studio/%2e%2e/%2e%2e/src/studio.py",
        ):
            try:
                urlopen(base + url)
            except HTTPError as error:
                assert error.code == 404
            else:
                raise AssertionError(f"Неизвестный brand asset стал публичным: {url}")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_deploy_bundle_includes_tracked_brand_assets() -> None:
    script = (ROOT / "ops" / "deploy-update.sh").read_text(encoding="utf-8")
    assert re.search(r"\bvendor\s+assets\s+ops\b", script), (
        "production deploy должен отправлять каталог assets вместе с Studio"
    )
