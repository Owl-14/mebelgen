"""Read a Word specification file into raw table rows.

Supports modern ``.docx`` directly (python-docx). Legacy ``.doc`` is converted
to ``.docx`` on the fly using MS Word automation (win32com) when available.

The expected table layout (columns may shift; we detect by header keywords):
    № | Наименование | Изображение | Технические характеристики | Ед.изм | Кол-во
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class RawItem:
    index: str
    name: str
    characteristics: str
    unit: str
    qty: str
    image_path: Optional[str] = None      # main product reference image
    image_paths: List[str] = None         # all images found in the row


def _convert_doc_to_docx(path: str) -> str:
    """Convert legacy .doc to .docx via MS Word; return new path."""
    out = os.path.splitext(path)[0] + "._auto.docx"
    try:
        import win32com.client as win32
    except ImportError as e:  # pragma: no cover
        raise RuntimeError(
            "Для чтения .doc нужен MS Word (pywin32). Сконвертируйте файл в .docx."
        ) from e
    word = win32.gencache.EnsureDispatch("Word.Application")
    word.Visible = False
    doc = word.Documents.Open(os.path.abspath(path), ReadOnly=True)
    try:
        doc.SaveAs(os.path.abspath(out), FileFormat=12)  # wdFormatXMLDocument
    finally:
        doc.Close(False)
        word.Quit()
    return out


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip()).lower()


def _detect_columns(header_cells: List[str]) -> dict:
    """Map logical fields to column indices using header keywords."""
    mapping = {"index": 0, "name": 1, "image": 2, "char": 3, "unit": 4, "qty": 5}
    for i, cell in enumerate(header_cells):
        h = _norm(cell)
        if "п/п" in h or h in ("№", "n"):
            mapping["index"] = i
        elif "наимен" in h:
            mapping["name"] = i
        elif "изображ" in h:
            mapping["image"] = i
        elif "характ" in h:
            mapping["char"] = i
        elif "измер" in h or "ед." in h:
            mapping["unit"] = i
        elif "кол" in h:
            mapping["qty"] = i
    return mapping


_NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "v": "urn:schemas-microsoft-com:vml",
}


def _iter_rids(tc):
    """Yield relationship ids of images in a cell (DrawingML a:blip + VML imagedata)."""
    for blip in tc.iter("{%s}blip" % _NS["a"]):
        rid = blip.get("{%s}embed" % _NS["r"]) or blip.get("{%s}link" % _NS["r"])
        if rid:
            yield rid
    for img in tc.iter("{%s}imagedata" % _NS["v"]):
        rid = img.get("{%s}id" % _NS["r"])
        if rid:
            yield rid


def _row_images(document, row, ri, image_col, media_dir) -> List[str]:
    """Extract embedded images from a table row (preferring the image column)."""
    saved: List[str] = []
    rels = document.part.rels
    cells = row.cells
    order = list(range(len(cells)))
    if image_col is not None and image_col < len(cells):
        order = [image_col] + [i for i in order if i != image_col]
    seen_rids = set()
    for ci in order:
        tc = cells[ci]._tc
        for rid in _iter_rids(tc):
            if rid in seen_rids:
                continue
            seen_rids.add(rid)
            try:
                part = rels[rid].target_part
                blob = part.blob
            except Exception:
                continue
            ext = os.path.splitext(str(part.partname))[1] or ".png"
            fpath = os.path.join(media_dir, f"row{ri:02d}_{rid}{ext}")
            with open(fpath, "wb") as fh:
                fh.write(blob)
            saved.append(fpath)
    return saved


def read_spec_rows(path: str, extract_images: bool = True) -> List[RawItem]:
    """Read the first spec-like table and return raw items."""
    import docx

    ext = os.path.splitext(path)[1].lower()
    work_path = path
    if ext == ".doc":
        work_path = _convert_doc_to_docx(path)

    document = docx.Document(work_path)
    if not document.tables:
        raise RuntimeError("В документе не найдено таблиц-спецификаций.")

    # Pick the table with the most rows (the spec table).
    table = max(document.tables, key=lambda t: len(t.rows))
    rows = table.rows
    header = [c.text for c in rows[0].cells]
    cols = _detect_columns(header)
    image_col = cols.get("image")
    if image_col is None:
        # heuristic: image column usually sits between name and characteristics
        image_col = 2

    media_dir = None
    if extract_images:
        media_dir = os.path.join(os.path.dirname(os.path.abspath(work_path)),
                                 "media_rows")
        os.makedirs(media_dir, exist_ok=True)

    items: List[RawItem] = []
    for ri, row in enumerate(rows[1:], 1):
        cells = [c.text for c in row.cells]

        def get(key: str) -> str:
            i = cols[key]
            return cells[i].strip() if i < len(cells) else ""

        name = get("name")
        char = get("char")
        if not name and not char:
            continue

        imgs: List[str] = []
        if extract_images:
            try:
                imgs = _row_images(document, row, ri, image_col, media_dir)
            except Exception:
                imgs = []
        items.append(
            RawItem(
                index=get("index"),
                name=name,
                characteristics=char,
                unit=get("unit") or "шт.",
                qty=get("qty"),
                image_path=imgs[0] if imgs else None,
                image_paths=imgs,
            )
        )
    return items
