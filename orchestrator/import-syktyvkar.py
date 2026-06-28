#!/usr/bin/env python3
"""Import Syktyvkar order/material training data into FurnitureSpec JSON.

The source files are real production artifacts:
- a ZIP with one-page PDF drawings and an order XLSX
- a material base XLSX
- an optional legacy Word DOC used only for heading/scope analysis

This importer intentionally uses only Python's standard library. XLSX files are
ZIP/XML containers, so we can read the needed tables without Excel or openpyxl.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET


DEFAULT_ZIP = Path("/Users/macintosheesh/Downloads/Сыктывкар 3 часть .Чертежи .zip")
DEFAULT_MATERIALS = Path("/Users/macintosheesh/Downloads/База материала.xlsx")
DEFAULT_TZ = Path("/Users/macintosheesh/Downloads/ТЗ мебель Коми РФ (1).doc")
DEFAULT_OUT = Path("demo/three-spike/generated/syktyvkar")
DEFAULT_LATEST = Path("demo/three-spike/generated/latest-spec.json")

MANUFACTURED_PATTERNS = [
    "стол",
    "столешниц",
    "брифинг",
    "шкаф",
    "гардероб",
    "тумб",
    "кухн",
    "система",
    "встроенн",
    "зона референтов",
    "добор",
    "трибуна",
]
PROCUREMENT_PATTERNS = [
    "стул",
    "кресл",
    "диван",
    "пуф",
    "банкет",
]

COLOR_MAP = {
    "дуб денвер трюфель": "#b09673",
    "дуб денвер графит": "#6f6358",
    "дуб кендал": "#c2a675",
    "светло-сер": "#d7d8d2",
    "серый уголь": "#575b5d",
    "серый дымчат": "#a7aaa8",
    "бело-сер": "#e3e1dc",
    "оникс сер": "#8c8d8b",
}


@dataclass
class OrderItem:
    row_number: int
    position: str
    drawing_number: str
    date: str
    title: str
    code: str
    quantity: float
    unit: str


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    zip_path = Path(args.zip)
    materials_path = Path(args.materials)
    tz_path = Path(args.tz) if args.tz else None
    out_dir = Path(args.out_dir)
    latest_path = Path(args.latest)

    order_name, order_bytes = read_order_xlsx_from_zip(zip_path)
    material_rows = load_material_rows(materials_path)
    order_items = parse_order_items(order_bytes)
    headings = extract_doc_headings(tz_path) if tz_path and tz_path.exists() else []

    out_dir.mkdir(parents=True, exist_ok=True)
    latest_path.parent.mkdir(parents=True, exist_ok=True)

    generated = []
    skipped = []
    for item in order_items:
        scope = classify_scope(item.title)
        if scope != "manufactured":
            skipped.append({"position": item.position, "title": item.title, "scope": scope})
            continue
        spec = build_spec(item, material_rows)
        spec_path = out_dir / f"{slug(spec['title'])}.json"
        write_json(spec_path, spec)
        generated.append({
            "id": spec["id"],
            "title": spec["title"],
            "type": spec["type"],
            "scope": spec["classification"]["scope"],
            "quantity": spec["source"]["quantity"],
            "spec": f"./generated/syktyvkar/{spec_path.name}",
        })

    manifest = {
        "source": {
            "zip": str(zip_path),
            "orderWorkbook": order_name,
            "materialsWorkbook": str(materials_path),
            "tzDocument": str(tz_path) if tz_path else "",
        },
        "generatedAt": source_timestamp([zip_path, materials_path, tz_path] if tz_path else [zip_path, materials_path]),
        "items": generated,
        "skipped": skipped,
        "trainingContext": {
            "manufacturedHeadingCount": len([h for h in headings if classify_scope(h) == "manufactured"]),
            "procurementHeadingCount": len([h for h in headings if classify_scope(h) == "procurement"]),
            "headingSamples": headings[:80],
        },
    }
    manifest_path = out_dir / "index.json"
    write_json(manifest_path, manifest)

    if generated:
        first = out_dir / Path(generated[0]["spec"]).name
        latest_path.write_text(first.read_text(encoding="utf-8"), encoding="utf-8")

    print(f"Imported {len(generated)} manufactured specs")
    if skipped:
        print(f"Skipped {len(skipped)} procurement/unknown specs")
    print(f"Manifest: {manifest_path.resolve()}")
    print(f"Latest: {latest_path.resolve()}")
    return 0


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zip", default=str(DEFAULT_ZIP), help="ZIP with Syktyvkar drawings/order workbook")
    parser.add_argument("--materials", default=str(DEFAULT_MATERIALS), help="Material base XLSX")
    parser.add_argument("--tz", default=str(DEFAULT_TZ), help="Optional legacy DOC for heading analysis")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT), help="Output FurnitureSpec directory")
    parser.add_argument("--latest", default=str(DEFAULT_LATEST), help="latest-spec.json path")
    return parser.parse_args(argv)


def read_order_xlsx_from_zip(zip_path: Path) -> tuple[str, bytes]:
    if not zip_path.exists():
        raise FileNotFoundError(f"ZIP not found: {zip_path}")
    with zipfile.ZipFile(zip_path) as zf:
        entries = []
        for info in zf.infolist():
            name = decode_zip_name(info)
            if name.lower().endswith(".xlsx"):
                entries.append((name, info))
        if not entries:
            raise FileNotFoundError(f"No XLSX workbook found inside {zip_path}")
        entries.sort(key=lambda value: ("заказ" not in normalize(value[0]), value[0]))
        name, info = entries[0]
        return name, zf.read(info)


def decode_zip_name(info: zipfile.ZipInfo) -> str:
    if info.flag_bits & 0x800:
        return info.filename
    try:
        return info.filename.encode("cp437").decode("cp866")
    except UnicodeError:
        return info.filename


def load_material_rows(path: Path) -> list[dict[str, Any]]:
    rows = read_xlsx_rows(path.read_bytes())
    if not rows:
        return []
    headers = [clean_cell(value) for value in rows[0]]
    material_rows = []
    for raw in rows[1:]:
        if not any(clean_cell(value) for value in raw):
            continue
        row = {headers[index]: raw[index] if index < len(raw) else "" for index in range(len(headers))}
        material_rows.append(row)
    return material_rows


def parse_order_items(order_bytes: bytes) -> list[OrderItem]:
    rows = read_xlsx_rows(order_bytes)
    items = []
    for row_number, row in enumerate(rows, start=1):
        title = clean_cell(cell_at(row, 8))
        position = clean_cell(cell_at(row, 2))
        if not title or not position.isdigit():
            continue
        quantity = parse_float(cell_at(row, 22), fallback=1)
        items.append(OrderItem(
            row_number=row_number,
            position=position,
            drawing_number=clean_cell(cell_at(row, 4)),
            date=clean_cell(cell_at(row, 5)),
            title=title,
            code=clean_cell(cell_at(row, 19)),
            quantity=quantity,
            unit=clean_cell(cell_at(row, 25)) or "шт",
        ))
    return items


def read_xlsx_rows(source: bytes) -> list[list[Any]]:
    with zipfile.ZipFile(io.BytesIO(source)) as zf:
        shared = read_shared_strings(zf)
        sheet_path = first_sheet_path(zf)
        root = ET.fromstring(zf.read(sheet_path))

    ns = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    rows: list[list[Any]] = []
    for row_el in root.findall(".//a:sheetData/a:row", ns):
        cells: dict[int, Any] = {}
        max_col = 0
        for c in row_el.findall("a:c", ns):
            ref = c.attrib.get("r", "")
            col = column_index(ref)
            if col <= 0:
                continue
            max_col = max(max_col, col)
            cells[col] = read_cell(c, shared)
        rows.append([cells.get(index, "") for index in range(1, max_col + 1)])
    return rows


def read_shared_strings(zf: zipfile.ZipFile) -> list[str]:
    try:
        root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    ns = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    values = []
    for si in root.findall("a:si", ns):
        values.append("".join(t.text or "" for t in si.findall(".//a:t", ns)))
    return values


def first_sheet_path(zf: zipfile.ZipFile) -> str:
    workbook = ET.fromstring(zf.read("xl/workbook.xml"))
    rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    ns = {
        "a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
        "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
        "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
    }
    first_sheet = workbook.find(".//a:sheets/a:sheet", ns)
    if first_sheet is None:
        raise ValueError("Workbook has no sheets")
    rel_id = first_sheet.attrib.get(f"{{{ns['r']}}}id")
    for rel in rels.findall("rel:Relationship", ns):
        if rel.attrib.get("Id") == rel_id:
            target = rel.attrib["Target"]
            return "xl/" + target.lstrip("/")
    raise ValueError("Cannot resolve first worksheet path")


def read_cell(cell: ET.Element, shared: list[str]) -> Any:
    ns = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        return "".join(t.text or "" for t in cell.findall(".//a:t", ns))
    value = cell.find("a:v", ns)
    raw = value.text if value is not None else ""
    if cell_type == "s":
        try:
            return shared[int(raw)]
        except (ValueError, IndexError):
            return raw
    if raw == "":
        return ""
    if re.fullmatch(r"-?\d+(?:\.\d+)?", raw):
        number = float(raw)
        return int(number) if number.is_integer() else number
    return raw


def column_index(ref: str) -> int:
    letters = "".join(ch for ch in ref if ch.isalpha())
    value = 0
    for ch in letters.upper():
        value = value * 26 + (ord(ch) - ord("A") + 1)
    return value


def cell_at(row: list[Any], one_based_index: int) -> Any:
    index = one_based_index - 1
    return row[index] if 0 <= index < len(row) else ""


def build_spec(item: OrderItem, materials: list[dict[str, Any]]) -> dict[str, Any]:
    dimensions, modules, dimension_note = extract_dimensions(item.title)
    archetype = classify_archetype(item.title)
    material_terms = infer_material_terms(item.title)
    material_refs = [match_material(term, materials) for term in material_terms]
    material_refs = [ref for ref in material_refs if ref]
    color = display_color(material_terms)
    body_material = material_label(material_refs[0]) if material_refs else "Материал по ТЗ"
    thickness = infer_thickness(archetype, material_refs)

    parts = {
        "top": {
            "label": "Столешница" if archetype in {"desk_panel", "countertop", "kitchen_run"} else "Корпус / фасады",
            "material": body_material,
            "thicknessMm": 25 if archetype in {"desk_panel", "countertop"} else thickness,
            "displayColor": color,
        },
        "sidePanels": {
            "label": "Корпус",
            "material": body_material,
            "thicknessMm": thickness,
            "displayColor": color,
        },
    }

    features = {
        "quantity": item.quantity,
        "sourceDrawing": item.drawing_number,
        "sectionCount": suggested_section_count(archetype, dimensions, modules),
        "metalFrame": has_any(item.title, ["металл", "9005", "опоры"]),
        "plinth": archetype in {"cabinet", "wardrobe", "drawer_unit", "built_in_run", "kitchen_run", "lectern"},
        "plinthBlack": has_any(item.title, ["серый уголь", "черн", "9005"]),
        "drawers": 3 if archetype == "drawer_unit" else 0,
        "shelves": 4 if archetype in {"cabinet", "wardrobe", "built_in_run"} else 0,
        "lock": archetype in {"cabinet", "drawer_unit"},
        "rod": "гардероб" in normalize(item.title),
        "hatShelf": "гардероб" in normalize(item.title),
        "shoeShelf": "гардероб" in normalize(item.title),
        "modules": len(modules),
    }

    if archetype in {"desk_panel", "countertop"}:
        parts["frontScreen"] = {
            "label": "Передний экран",
            "material": body_material,
            "thicknessMm": 16,
            "heightMm": 300,
            "displayColor": color,
        }
    if features["metalFrame"]:
        parts["metalFrame"] = {"label": "Металлокаркас / опоры", "material": "RAL 9005", "displayColor": "#111111"}
    if archetype in {"cabinet", "wardrobe", "built_in_run", "kitchen_run"}:
        parts["doors"] = {
            "label": "Фасады",
            "material": body_material,
            "count": max(2, features["sectionCount"]),
            "displayColor": color,
        }
        parts["shelves"] = {"label": "Полки", "material": body_material, "count": features["shelves"]}
    if archetype == "drawer_unit":
        parts["drawers"] = {"label": "Ящики", "count": 3, "material": body_material}
        parts["wheels"] = {"label": "Колесные опоры", "material": "черные"}

    spec = {
        "id": f"syktyvkar-{item.drawing_number or item.position}-{slug(item.title)}",
        "type": archetype,
        "title": item.title,
        "classification": {
            "scope": "manufactured",
            "procurementFiltered": False,
        },
        "ai": {
            "provider": "local-importer",
            "model": "rule-based-syktyvkar-v1",
        },
        "source": {
            "project": "Сыктывкар 3 часть",
            "position": item.position,
            "drawingNumber": item.drawing_number,
            "orderCode": item.code,
            "quantity": item.quantity,
            "unit": item.unit,
            "rawTitle": item.title,
        },
        "dimensions": dimensions,
        "dimensionNote": dimension_note,
        "modules": modules,
        "geometry": {
            "overhangMm": 25 if archetype in {"desk_panel", "countertop"} else 0,
            "sectionCount": features["sectionCount"],
        },
        "parts": parts,
        "features": features,
        "materialRefs": material_refs,
        "materials": material_lines(item.title, material_refs, dimension_note),
        "titleBlock": {
            "sheetName": item.title,
            "date": "20.02.2026",
            "manager": "Петрова А.А.",
        },
    }
    return spec


def extract_dimensions(title: str) -> tuple[dict[str, float], list[dict[str, float]], str]:
    text = title.replace("×", "х")
    pattern = re.compile(
        r"(?P<w1>\d{2,5})(?:\s*-\s*(?P<w2>\d{2,5}))?\s*[хxХX]\s*"
        r"(?P<d>\d{2,5})\s*[хxХX]\s*(?P<h1>\d{2,5})(?:\s*/\s*(?P<h2>\d{2,5}))?",
        re.I,
    )
    matches = list(pattern.finditer(text))
    if not matches:
        return {"widthMm": 1000, "depthMm": 500, "heightMm": 750}, [], "Размеры не распознаны автоматически"

    modules = []
    for match in matches:
        w1 = float(match.group("w1"))
        w2 = float(match.group("w2") or w1)
        d = float(match.group("d"))
        h1 = float(match.group("h1"))
        h2 = float(match.group("h2") or h1)
        module = {
            "widthMm": max(w1, w2),
            "depthMm": d,
            "heightMm": max(h1, h2),
        }
        if w1 != w2:
            module["widthMinMm"] = min(w1, w2)
        if h1 != h2:
            module["heightMinMm"] = min(h1, h2)
        modules.append(module)

    kitchen_like = "кухн" in normalize(text)
    if kitchen_like and len(modules) > 1:
        dimensions = {
            "widthMm": sum(module["widthMm"] for module in modules),
            "depthMm": max(module["depthMm"] for module in modules),
            "heightMm": max(module["heightMm"] for module in modules),
        }
        return dimensions, modules, "Несколько блоков кухни объединены в общий габарит"

    first = modules[0]
    return {
        "widthMm": first["widthMm"],
        "depthMm": first["depthMm"],
        "heightMm": first["heightMm"],
    }, modules if len(modules) > 1 else [], ""


def classify_scope(title: str) -> str:
    text = normalize(title)
    manufactured = any(pattern in text for pattern in MANUFACTURED_PATTERNS)
    procurement = any(pattern in text for pattern in PROCUREMENT_PATTERNS)
    if procurement and not manufactured:
        return "procurement"
    if manufactured:
        return "manufactured"
    return "unknown"


def classify_archetype(title: str) -> str:
    text = normalize(title)
    if "кухн" in text:
        return "kitchen_run"
    if "система" in text or "встроенн" in text or "зона референтов" in text:
        return "built_in_run"
    if "трибуна" in text:
        return "lectern"
    if "столешниц" in text:
        return "countertop"
    if "тумб" in text:
        return "drawer_unit"
    if "гардероб" in text or "одежд" in text:
        return "wardrobe"
    if "шкаф" in text:
        return "cabinet"
    if "стол" in text or "брифинг" in text:
        return "desk_panel"
    return "cabinet"


def infer_material_terms(title: str) -> list[str]:
    text = normalize(title)
    terms = []
    candidates = [
        "Дуб Денвер Трюфель",
        "Дуб Денвер графит",
        "Дуб Кендал",
        "Светло-серый",
        "Серый уголь",
        "Серый Дымчатый",
        "Бело-серый",
        "Оникс Серый",
    ]
    for term in candidates:
        if normalize(term) in text:
            terms.append(term)
    if "9005" in text:
        terms.append("RAL 9005")
    return terms or ["ЛДСП"]


def match_material(term: str, rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    wanted = normalize(term)
    if wanted.startswith("ral"):
        return {"query": term, "type": "color", "code": term.upper(), "label": term.upper()}

    tokens = [token for token in wanted.split() if len(token) > 2]
    best: tuple[int, dict[str, Any]] | None = None
    for row in rows:
        group = normalize(str(row.get("Наименование группы", "")))
        if "листовой материал" not in group and "кромоч" not in group:
            continue
        name = normalize(str(row.get("Наименование материала", "")))
        article = normalize(str(row.get("Артикул материала", "")))
        haystack = f"{article} {name}"
        if tokens and not all(token in haystack for token in tokens):
            continue
        score = len(tokens)
        if "лдсп" in name:
            score += 3
        if "16" in name or str(row.get("Толщина", "")) in {"16", "16.0"}:
            score += 1
        if "кромк" in group:
            score -= 1
        if best is None or score > best[0]:
            best = (score, row)

    if best is None:
        return {"query": term, "type": "unmatched", "label": term}
    row = best[1]
    return {
        "query": term,
        "article": clean_cell(row.get("Артикул материала")),
        "label": clean_cell(row.get("Наименование материала")),
        "group": clean_cell(row.get("Наименование группы")),
        "lengthMm": parse_float(row.get("Длина"), fallback=0),
        "widthMm": parse_float(row.get("Ширина"), fallback=0),
        "thicknessMm": parse_float(row.get("Толщина"), fallback=0),
        "unit": clean_cell(row.get("Единица измерения")),
    }


def material_label(ref: dict[str, Any]) -> str:
    label = clean_cell(ref.get("label"))
    if label:
        return label
    return clean_cell(ref.get("query")) or "Материал по ТЗ"


def infer_thickness(archetype: str, refs: list[dict[str, Any]]) -> float:
    for ref in refs:
        value = parse_float(ref.get("thicknessMm"), fallback=0)
        if value > 0:
            return value
    if archetype in {"desk_panel", "countertop"}:
        return 25
    return 16


def display_color(terms: list[str]) -> str:
    haystack = normalize(" ".join(terms))
    for key, value in COLOR_MAP.items():
        if key in haystack:
            return value
    if "ral 9005" in haystack:
        return "#111111"
    return "#eee6d7"


def suggested_section_count(archetype: str, dimensions: dict[str, float], modules: list[dict[str, float]]) -> int:
    if modules:
        return len(modules)
    width = dimensions.get("widthMm", 1000)
    if archetype == "built_in_run":
        return max(2, round(width / 500))
    if archetype == "kitchen_run":
        return max(3, round(width / 600))
    if archetype in {"wardrobe", "cabinet"}:
        return 2 if width <= 1200 else max(2, round(width / 500))
    return 1


def material_lines(title: str, refs: list[dict[str, Any]], note: str) -> list[str]:
    lines = []
    for ref in refs[:4]:
        if ref.get("type") == "color":
            lines.append(f"Металл / опоры: {ref['label']}")
        elif ref.get("type") == "unmatched":
            lines.append(f"Материал: {ref['label']}")
        else:
            thickness = ref.get("thicknessMm")
            suffix = f", {format_num(thickness)}мм" if thickness else ""
            lines.append(f"{ref['query']}: {ref['label']}{suffix}")
    if note:
        lines.append(note)
    lines.append("*Фактические замеры обязательны.")
    return lines


def extract_doc_headings(path: Path | None) -> list[str]:
    if not path or not path.exists():
        return []
    text = path.read_bytes().decode("utf-16le", errors="ignore")
    chunks = []
    for match in re.finditer(r"[А-Яа-яЁёA-Za-z0-9№%.,;:()\[\]«»\"\\\-–—+/×xXхХ=\s]{12,}", text):
        value = " ".join(match.group(0).split())
        cyr = sum(1 for ch in value if "А" <= ch <= "я" or ch in "Ёё")
        if cyr >= 5 and len(value) <= 160:
            chunks.append(value)
    heading = re.compile(
        r"^(Стол|Шкаф|Тумба|Тумбы|Кухня|Мини кухня|Брифинг|Система|Трибуна|Доборы|"
        r"Столешница|Стулья|Кресло|Диван)[А-Яа-яЁё0-9 №()\-\/.,xXхХ]{0,100}$",
        re.I,
    )
    result = []
    for chunk in chunks:
        if heading.search(chunk) and chunk not in result:
            result.append(chunk)
    return result


def has_any(text: str, needles: list[str]) -> bool:
    value = normalize(text)
    return any(normalize(needle) in value for needle in needles)


def normalize(value: Any) -> str:
    return str(value or "").replace("ё", "е").replace("Ё", "Е").casefold()


def clean_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return re.sub(r"\s+", " ", str(value)).strip()


def parse_float(value: Any, fallback: float = 0) -> float:
    try:
        text = clean_cell(value).replace(",", ".")
        if text == "":
            return fallback
        return float(text)
    except (TypeError, ValueError):
        return fallback


def format_num(value: Any) -> str:
    number = parse_float(value, fallback=0)
    return str(int(number)) if number.is_integer() else f"{number:.1f}".rstrip("0").rstrip(".")


def slug(text: str) -> str:
    table = {
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh",
        "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
        "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "c",
        "ч": "ch", "ш": "sh", "щ": "sch", "ы": "y", "э": "e", "ю": "yu", "я": "ya",
        "ь": "", "ъ": "",
    }
    value = "".join(table.get(ch, ch) for ch in text.lower())
    value = re.sub(r"[^a-z0-9]+", "_", value)
    return value.strip("_")[:90] or "item"


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def source_timestamp(paths: list[Path | None]) -> str:
    existing = [path for path in paths if path and path.exists()]
    if not existing:
        return datetime.fromtimestamp(0, timezone.utc).isoformat()
    latest = max(path.stat().st_mtime for path in existing)
    return datetime.fromtimestamp(latest, timezone.utc).isoformat()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"import-syktyvkar: {exc}", file=sys.stderr)
        raise SystemExit(1)
