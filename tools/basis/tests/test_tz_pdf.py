"""ТЗ в PDF: текстовый слой даёт точный габарит без нейросети, страница — картинкой."""

from __future__ import annotations

import base64
import os
import sys
from pathlib import Path

os.environ["SPEC_CHAT_PROVIDER"] = "mock"   # тесты всегда офлайн

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest                                              # noqa: E402

from src.spec_chat import _accept_created                  # noqa: E402
from src.tz_pdf import clean_text, dims_from_text, read_pdf  # noqa: E402

# Текстовый слой ТЗ komi 46 как его отдаёт pypdfium2 (CAD-экспорт)
KOMI_46 = ("1000\r\n18\r\n400\r\n1800\r\nРазмер, мм: 1000±10х400±10х1800±10 Окончательные\r\n"
           "размеры определяются после проведения замеров. 2\r\nраспашные двери, 4 полки. "
           "Двери нак￾ладные.\r\n46. Шкаф для документов - замдиректора 1000х400х1800\r\n16\r\n339")


def _minimal_pdf(text: str) -> bytes:
    """Одностраничный PDF с ASCII-текстом — без reportlab и без файлов клиентов."""
    stream = f"BT /F1 24 Tf 72 500 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 842 595] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(out)


def test_dimensions_come_from_the_size_line():
    assert dims_from_text(KOMI_46) == (1000, 400, 1800)
    assert dims_from_text("72.Тумба подкатная 400x450x580 Размер, мм: 400±10x450±10x580±10") == (400, 450, 580)
    assert dims_from_text("Материал ЛДСП 16 мм") is None


def test_text_layer_is_cleaned():
    text = clean_text(KOMI_46)
    assert "накладные" in text                       # мягкий перенос CAD убран
    assert "\n339" not in text and not text.startswith("1000")   # одиночные размерки — шум


def test_pdf_page_is_read_and_rendered():
    pytest.importorskip("pypdfium2")
    pytest.importorskip("PIL")
    result = read_pdf(_minimal_pdf("Size, mm: 1000x400x1800"))
    assert result["pages"] == 1
    assert dims_from_text(result["text"]) == (1000, 400, 1800)
    png = base64.b64decode(result["images"][0]["data"])
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_text_dimensions_override_the_model_and_skip_the_dims_question():
    draft = {"schemaVersion": "paramspec-v1", "project_name": "Шкаф 46", "archetype": "cabinet",
             "dimensions": {"width": 1000, "depth": 450, "height": 1700},
             "materials": {"board_thickness": 16},
             "sections": [{"kind": "door", "door": 1, "shelves": 4},
                          {"kind": "door", "door": 1, "shelves": 4}]}
    capture: dict = {"vision_facts": "Тип изделия: шкаф\nНеясно: глубина 450 или 400"}
    res = _accept_created(draft, reply="Создан.", usage=None, trace={}, build=None, history=None,
                          context={"tz_import": True, "tz_text": clean_text(KOMI_46),
                                   "tz_image": {"bytes": 300_000, "width": 2000, "height": 1414}},
                          capture=capture)
    assert res.get("created") is True, res.get("reply")
    assert res["spec"]["dimensions"] == {"width": 1000, "depth": 400, "height": 1800}
    assert any("Габарит взят из текста ТЗ" in w for w in res["spec"]["warnings"])
