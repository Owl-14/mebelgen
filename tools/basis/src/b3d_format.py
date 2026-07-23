"""Кодек родного формата БАЗИС .b3d (BZ85) — reverse-engineered (AKD-98).

Файл = 'BZ85' + секции. Секция = маркер 01 00 00 ff + флаг(00 plain | 01 zlib)
+ тело: ТАБЛИЦА ИМЁН + ОДНО дерево узлов. В файле две секции: Header (plain)
и Document (zlib) — модель, фурнитура, присадки, камера.

Узел: nameIdx u32 · childCount u32 · type u8 · значение.
Типы: 00 контейнер · 01 true · 02 false · 03 u8 · 04 i32 · 05 f64 ·
      06 строка UTF-16LE (длина в символах) · 07 блоб (длина в байтах).

Таблица имён строится сортировкой (длина, ASCII) — как в облачных файлах.

ГРАНИЦА (проверено, task 12644 + эксперименты REPACK/ZTAIL): после zlib-секции
идёт 64-байтная ПОДПИСЬ целостности. БАЗИС-Просмотр её проверяет — оригинал с
обнулённым хвостом даёт «Ошибка чтения файла». Хвост высокоэнтропийный, зависит
от содержимого, но не сходится с sha512/sha3/blake2b по очевидным диапазонам и
солям → секрет зашит в бинарник БАЗИСа. Значит:
- ЧТЕНИЕ .b3d — полноценное (parse_b3d): геометрия, присадки, материалы, камера
  из ЛЮБОГО файла БАЗИС/облака, без облачного шага b3d→cfrn;
- ЗАПИСЬ openable-файла — заблокирована подписью (write_b3d даёт корректное дерево,
  но БАЗИС отвергнет без валидной подписи). Собирать .b3d по-прежнему через облако
  (оно подписывает) или десктоп с лицензией. См. Linear AKD-98.
"""

from __future__ import annotations

import struct
import zlib
from typing import Any

MAGIC = b"BZ85"
SECTION = b"\x01\x00\x00\xff"

# node = (name, kind, value); kind: obj|bool|u8|i32|f64|str|blob


# ------------------------------------------------------------------ низкий уровень

def _read_table(d: bytes, off: int) -> tuple[list[str], int]:
    n = struct.unpack_from("<I", d, off)[0]
    off += 4
    names = []
    for _ in range(n):
        ln = struct.unpack_from("<I", d, off)[0]
        off += 4
        names.append(d[off:off + ln].decode("utf-8"))
        off += ln
    return names, off


def _parse_node(d: bytes, p: int, names: list[str]) -> tuple[tuple, int]:
    nm, cnt = struct.unpack_from("<II", d, p)
    t = d[p + 8]
    q = p + 9
    # десктопный Мебельщик пишет АНОНИМНЫЕ узлы (элементы массивов, напр.
    # FurnList→ParData→Elements) с nameIdx=0xFFFFFFFF — файл из облака их
    # не содержит, файл технолога содержит (реверс правок производства)
    name = "" if nm == 0xFFFFFFFF else names[nm]
    if t == 0x00:
        kids = []
        for _ in range(cnt):
            k, q = _parse_node(d, q, names)
            kids.append(k)
        return (name, "obj", kids), q
    if t == 0x01:
        return (name, "bool", True), q
    if t == 0x02:
        return (name, "bool", False), q
    if t == 0x03:
        return (name, "u8", d[q]), q + 1
    if t == 0x04:
        return (name, "i32", struct.unpack_from("<i", d, q)[0]), q + 4
    if t == 0x05:
        return (name, "f64", struct.unpack_from("<d", d, q)[0]), q + 8
    if t == 0x06:
        ln = struct.unpack_from("<I", d, q)[0]
        s = d[q + 4:q + 4 + ln * 2].decode("utf-16-le")
        return (name, "str", s), q + 4 + ln * 2
    if t == 0x07:
        ln = struct.unpack_from("<I", d, q)[0]
        return (name, "blob", d[q + 4:q + 4 + ln]), q + 4 + ln
    if t == 0x09:                                          # Delphi TDateTime (f64)
        return (name, "date", struct.unpack_from("<d", d, q)[0]), q + 8
    raise ValueError(f"неизвестный тип 0x{t:02x} @{p} ({name})")


def _collect_names(node: tuple, acc: set) -> None:
    acc.add(node[0])
    if node[1] == "obj":
        for k in node[2]:
            _collect_names(k, acc)


def _write_node(node: tuple, idx: dict[str, int], out: bytearray) -> None:
    name, kind, val = node
    # анонимные узлы (десктоп): nameIdx = 0xFFFFFFFF, в таблицу имён не входят
    nid = 0xFFFFFFFF if name == "" else idx[name]
    if kind == "obj":
        out += struct.pack("<II", nid, len(val)) + b"\x00"
        for k in val:
            _write_node(k, idx, out)
        return
    if kind == "bool":
        out += struct.pack("<II", nid, 0) + (b"\x01" if val else b"\x02")
        return
    tag, payload = {
        "u8": (b"\x03", lambda v: bytes([v])),
        "i32": (b"\x04", lambda v: struct.pack("<i", v)),
        "f64": (b"\x05", lambda v: struct.pack("<d", float(v))),
        "str": (b"\x06", lambda v: struct.pack("<I", len(v)) + v.encode("utf-16-le")),
        "blob": (b"\x07", lambda v: struct.pack("<I", len(v)) + bytes(v)),
        "date": (b"\x09", lambda v: struct.pack("<d", float(v))),
    }[kind]
    out += struct.pack("<II", nid, 0) + tag + payload(val)


def _serialize_section(nodes: list[tuple]) -> bytes:
    names: set[str] = set()
    for n in nodes:
        _collect_names(n, names)
    names.add("")
    table = sorted(names, key=lambda s: (len(s), s))
    idx = {s: i for i, s in enumerate(table)}
    out = bytearray(struct.pack("<I", len(table)))
    for s in table:
        b = s.encode("utf-8")
        out += struct.pack("<I", len(b)) + b
    for n in nodes:
        _write_node(n, idx, out)
    return bytes(out)


def _parse_section(d: bytes, off: int = 0, count: int | None = None) -> list[tuple]:
    names, p = _read_table(d, off)
    nodes = []
    while p < len(d) if count is None else len(nodes) < count:
        node, p = _parse_node(d, p, names)
        nodes.append(node)
        if count is None and p >= len(d):
            break
    return nodes


# ------------------------------------------------------------------ файл целиком

def parse_b3d(data: bytes) -> dict[str, Any]:
    """→ {'sections': [(flag, root)], 'trailer': bytes}. Секции: Header(0)+Document(1).
    trailer — 64 байта подписи после zlib-потока (сохраняем как есть)."""
    if data[:4] != MAGIC:
        raise ValueError("не BZ85")
    p = 4
    sections: list[tuple[int, tuple]] = []
    trailer = b""
    while p < len(data):
        if data[p:p + 4] != SECTION:
            raise ValueError(f"нет маркера секции @{p}: {data[p:p+8].hex(' ')}")
        flag = data[p + 4]
        p += 5
        if flag == 0x01:                                  # zlib-секция
            dec = zlib.decompressobj()
            body = dec.decompress(data[p:])
            consumed = len(data) - p - len(dec.unused_data)
            names, q = _read_table(body, 0)
            root, _ = _parse_node(body, q, names)
            sections.append((flag, root))
            p += consumed
            trailer = data[p:]                            # хвост-подпись после потока
            break
        elif flag == 0x00:                                # plain: таблица + одно дерево
            names, q = _read_table(data, p)
            root, q = _parse_node(data, q, names)
            sections.append((flag, root))
            p = q
        else:
            raise ValueError(f"неизвестный флаг секции 0x{flag:02x}")
    return {"sections": sections, "trailer": trailer}


def write_b3d(sections: list[tuple[int, tuple]], trailer: bytes = b"") -> bytes:
    """Собрать .b3d из секций [(flag, root)] (+ trailer-подпись после zlib-секции)."""
    out = bytearray(MAGIC)
    for flag, root in sections:
        body = _serialize_section([root])
        out += SECTION + bytes([flag])
        out += zlib.compress(body, 9) if flag == 0x01 else body
    out += trailer
    return bytes(out)


# ------------------------------------------------------------------ утилиты дерева

def child(node: tuple, name: str):
    if node[1] == "obj":
        for k in node[2]:
            if k[0] == name:
                return k
    return None


def find(nodes: list[tuple] | tuple, name: str):
    stack = list(nodes) if isinstance(nodes, list) else [nodes]
    while stack:
        nd = stack.pop(0)
        if nd[0] == name:
            return nd
        if nd[1] == "obj":
            stack.extend(nd[2])
    return None


def dump(node: tuple, depth: int = 0, out: list[str] | None = None, max_lines: int = 4000) -> list[str]:
    out = out if out is not None else []
    if len(out) >= max_lines:
        return out
    name, kind, val = node
    if kind == "obj":
        out.append("  " * depth + f"{name} ({len(val)})")
        for k in val:
            dump(k, depth + 1, out, max_lines)
    elif kind == "blob":
        out.append("  " * depth + f"{name}: blob[{len(val)}] {bytes(val[:16]).hex(' ')}")
    else:
        out.append("  " * depth + f"{name}: {kind} = {val!r}")
    return out
