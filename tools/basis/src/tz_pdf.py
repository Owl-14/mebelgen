"""ТЗ в PDF: текстовый слой — без нейросети, страница картинкой — для раскладки.

ТЗ komi приходят PDF-ками из CAD: в текстовом слое лежат точные «Размер, мм:
1000±10х400±10х1800±10», «2 распашные двери, 4 полки», цвет ЛДСП. Нейросеть
читает те же цифры с картинки с ошибками, поэтому текст идёт сборщику как
приоритетные факты, а габарит из него ставится в изделие детерминированно.
Страница рендерится в PNG и уходит vision-модели — раскладку (колонки, ящики)
из текста не достать.

pypdfium2 (Apache-2.0/BSD) — и текст, и рендер; PyMuPDF не берём из-за AGPL.
"""

from __future__ import annotations

import base64
import io
import re
from typing import Any

MAX_PAGES = 2
TARGET_SIDE_PX = 2000          # длинная сторона рендера: цифры размеров читаемы

_HYPHEN = "￾"             # мягкий перенос в текстовом слое CAD-экспорта


def clean_text(raw: str) -> str:
    text = (raw or "").replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace(_HYPHEN, "")
    # одиночные числа-размерки по строкам — шум для сборщика, оставляем фразы
    lines = [line.strip() for line in text.split("\n")]
    lines = [line for line in lines if line and not re.fullmatch(r"[\d.,±\s]+", line)]
    return "\n".join(lines).strip()


_DIMS = re.compile(
    r"(\d{2,4})\s*(?:±\s*\d+)?\s*[xх×X*]\s*(\d{2,4})\s*(?:±\s*\d+)?\s*[xх×X*]\s*(\d{2,4})")


def dims_from_text(text: str) -> tuple[int, int, int] | None:
    """Ш×Г×В из «Размер, мм: 1000±10х400±10х1800±10» или из заголовка «…1000x400x1800»."""
    if not text:
        return None
    sized = re.search(r"размер[^:\n]*:?\s*(.{0,60})", text, re.IGNORECASE)
    for chunk in ([sized.group(1)] if sized else []) + [text]:
        m = _DIMS.search(chunk)
        if m:
            w, d, h = (int(m.group(i)) for i in (1, 2, 3))
            if all(50 <= v <= 6000 for v in (w, d, h)):
                return w, d, h
    return None


def read_pdf(data: bytes) -> dict[str, Any]:
    """{text, pages, images: [{mime, data(base64)}]} первых MAX_PAGES страниц."""
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(data)
    try:
        texts: list[str] = []
        images: list[dict[str, str]] = []
        for index in range(min(len(pdf), MAX_PAGES)):
            page = pdf[index]
            try:
                textpage = page.get_textpage()
                texts.append(textpage.get_text_range())
                textpage.close()
                width, height = page.get_size()
                scale = TARGET_SIDE_PX / max(width, height, 1)
                bitmap = page.render(scale=scale)
                buffer = io.BytesIO()
                bitmap.to_pil().save(buffer, format="PNG", optimize=True)
                bitmap.close()
                images.append({"mime": "image/png",
                               "data": base64.b64encode(buffer.getvalue()).decode()})
            finally:
                page.close()
        raw = "\n".join(texts)
        from .tz_ocr import numbers_in_text

        return {"text": clean_text(raw), "pages": len(pdf), "images": images,
                "numbers": sorted(set(numbers_in_text(raw)))}
    finally:
        pdf.close()
