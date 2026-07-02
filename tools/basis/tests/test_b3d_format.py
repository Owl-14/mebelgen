"""Кодек BZ85 (.b3d): чтение/запись дерева round-trip'ится на облачных файлах.

Проверяем: parse → write → parse даёт идентичное дерево (детерминированная
сериализация), и что из дерева достаётся геометрия/присадки/материал.
Запись openable-файла НЕ тестируем — блокирует подпись целостности (см. модуль).
"""

from __future__ import annotations

import glob
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.b3d_format import parse_b3d, write_b3d, find, child   # noqa: E402

OUT = Path(r"D:/claude/bazis/out")
FILES = [f for f in glob.glob(str(OUT / "*.b3d"))
         if "REPACK" not in f and "TAIL" not in f]


def test_roundtrip_trees_on_cloud_b3d():
    if not FILES:
        return                              # нет облачных файлов на этой машине — пропуск
    checked = 0
    for f in FILES:
        data = Path(f).read_bytes()
        try:
            doc = parse_b3d(data)
        except Exception:
            continue                        # чужой/битый файл — не наш кейс
        rebuilt = write_b3d(doc["sections"], doc["trailer"])
        doc2 = parse_b3d(rebuilt)
        assert doc["sections"] == doc2["sections"], f
        assert doc["trailer"] == doc2["trailer"], f
        checked += 1
    assert checked > 0


def test_reads_geometry_and_holes():
    f = OUT / "komi_39_FINAL.b3d"
    if not f.exists():
        return
    doc = parse_b3d(f.read_bytes())
    model = find([r for _, r in doc["sections"]], "Model")
    assert model is not None
    objs = find(model, "Objs")
    assert objs and len(objs[2]) >= 20      # детали корпуса
    first = objs[2][0]
    assert child(first, "Name")[2]          # у детали есть имя
    assert child(first, "Contour")          # и контур-блоб
    furn = find([r for _, r in doc["sections"]], "F399582340")
    assert furn is not None
    holes = child(furn, "Holes")
    assert holes and len(holes[2]) == 64    # присадки в файле
