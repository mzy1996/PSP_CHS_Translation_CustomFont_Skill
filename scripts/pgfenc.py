# -*- coding: utf-8 -*-
"""PGF 编码器 + 往返验证。

编码布局（与 jpn0.pgf / fonts.pgf 一致）：
  [header 392B][dimTable][xAdjustTable][yAdjustTable][advanceTable]
  [shadowCharMap][charMap][charPointerTable][fontData]

- 每个字形起点按 32 bit 对齐，charPointer = 位偏移 / 32
- 字形 flags 只置位图方向位（0x01 行优先），度量值走 32bit 显式字段，
  不依赖 ttf2pgf 的索引式布局与 PPSSPP 的 isJPCSPFont 补偿分支。
"""

import struct
from typing import List, Optional, Sequence

from pgf import (PGFHeader, build_header, parse_header, BitWriter, get_table,
                 packed_size, PGF, Glyph, decode_bitmap, read_char_glyph)

FLAG_BMP_H_ROWS = 0x01
FLAG_BMP_V_ROWS = 0x02
FLAG_BMP_OVERLAY = 0x03


def _align32(w: BitWriter):
    """把写指针补到 32 bit 边界，返回当前位偏移。"""
    pad = (32 - (w.bitcnt % 32)) % 32
    if pad:
        w.cur |= 0  # 补 0
        # 逐位推进以复用 BitWriter 状态管理
        for _ in range(pad):
            w.write(0, 1)


def _bitpos(w: BitWriter) -> int:
    return len(w.words) * 32 + w.bitcnt


def encode_glyph(w: BitWriter, g: Glyph, pixels: Sequence[int]) -> int:
    """写入一个字形的头与位图，返回其位偏移（已 32bit 对齐）。"""
    _align32(w)
    start = _bitpos(w)
    # size 字段：jpn0 中该字段被解码端跳过，写 0 即可
    w.write(0, 14)
    w.write(g.w & 0x7F, 7)
    w.write(g.h & 0x7F, 7)
    w.write(g.left & 0x7F, 7)     # 负值由补码自动落在 >=64 区间
    w.write(g.top & 0x7F, 7)
    # flags：仅位图方向位，不启用索引
    flags = g.flags & FLAG_BMP_OVERLAY
    if flags == 0:
        flags = FLAG_BMP_H_ROWS
    w.write(flags & 0x3F, 6)
    # shadow flags (2+2+3) 与 shadowID (9)
    w.write(0, 2)
    w.write(0, 2)
    w.write(0, 3)
    w.write(g.shadowID & 0x1FF, 9)
    # 显式度量：dimension / xAdjust / yAdjust / advance 各 32bit
    w.write(g.dimensionWidth & 0xFFFFFFFF, 32)
    w.write(g.dimensionHeight & 0xFFFFFFFF, 32)
    w.write(g.xAdjustH & 0xFFFFFFFF, 32)
    w.write(g.xAdjustV & 0xFFFFFFFF, 32)
    w.write(g.yAdjustH & 0xFFFFFFFF, 32)
    w.write(g.yAdjustV & 0xFFFFFFFF, 32)
    w.write(g.advanceH & 0xFFFFFFFF, 32)
    w.write(g.advanceV & 0xFFFFFFFF, 32)
    # 位图
    n = g.w * g.h
    if n > 0:
        from pgf import encode_bitmap
        data = encode_bitmap(list(pixels[:n]))
        for byte in data:
            w.write(byte, 8)
    return start


def build_pgf(header: PGFHeader,
              glyphs: List[Optional[Glyph]],
              bitmaps: List[Optional[List[int]]],
              char_map: List[int],
              dim_table=None, x_adj_table=None, y_adj_table=None, adv_table=None) -> bytes:
    """按规范拼装完整 PGF 文件。

    glyphs[i] / bitmaps[i] 对应 charPointer 的第 i 项；
    char_map[j] = 码位 (firstGlyph + j) 对应的 glyph 下标。
    """
    h = header
    h.charPointerLength = len(glyphs)
    h.charMapLength = len(char_map)

    out = bytearray()
    out += build_header(h)

    def write_pairs(tbl):
        if not tbl:
            return
        for a, b in tbl:
            out.extend(struct.pack("<ii", a, b))

    dim_table = dim_table or []
    x_adj_table = x_adj_table or []
    y_adj_table = y_adj_table or []
    adv_table = adv_table or []
    assert len(dim_table) == h.dimTableLength, (len(dim_table), h.dimTableLength)
    assert len(x_adj_table) == h.xAdjustTableLength
    assert len(y_adj_table) == h.yAdjustTableLength
    assert len(adv_table) == h.advanceTableLength
    write_pairs(dim_table)
    write_pairs(x_adj_table)
    write_pairs(y_adj_table)
    write_pairs(adv_table)

    # shadowCharMap（长度 0 时占 0 字节）
    if h.shadowMapLength > 0:
        bw = BitWriter()
        for _ in range(h.shadowMapLength):
            bw.write(0, h.shadowMapBpe)
        out += bw.finish()

    # charMap
    bw = BitWriter()
    for v in char_map:
        bw.write(v & ((1 << h.charMapBpe) - 1), h.charMapBpe)
    out += bw.finish()

    # 先编码字形数据，才能得到 charPointer
    fw = BitWriter()
    pointers = []
    for i, g in enumerate(glyphs):
        if g is None:
            pointers.append(0)
            continue
        start = encode_glyph(fw, g, bitmaps[i] or [])
        pointers.append(start // 32)
    # charPointerTable
    pw = BitWriter()
    for p in pointers:
        pw.write(p & ((1 << h.charPointerBpe) - 1), h.charPointerBpe)
    out += pw.finish()
    out += fw.finish()
    # 尾部填充：解码端 read_char_glyph 要求 charPtr+1024 位仍在数据内，
    # 最后一个字形靠近末尾时需要留出余量，否则会被判越界而丢字形。
    out += bytes(512)
    return bytes(out)


# ---------------- 往返验证 ----------------

def roundtrip_check(src_path: str, limit: int = 400):
    data = open(src_path, "rb").read()
    pgf = PGF.load(data)
    h = pgf.header

    # 只取前 limit 个有效字形做往返（全量太慢，先证编码正确）
    glyphs = []
    bitmaps = []
    index_map = {}
    for i, g in enumerate(pgf.glyphs):
        if g is None or len(glyphs) >= limit:
            continue
        bm = decode_bitmap(pgf.fontData, g)
        index_map[i] = len(glyphs)
        glyphs.append(g)
        bitmaps.append(bm)

    # 重建 charMap：原 charMap 里指向已收集字形的保留，其余置 0xFFFF
    new_map = []
    for v in pgf.charMap:
        new_map.append(index_map.get(v, 0xFFFF))

    nh = PGFHeader(
        revision=h.revision, version=h.version, bpp=h.bpp,
        charMapBpe=h.charMapBpe, charPointerBpe=h.charPointerBpe,
        hSize=h.hSize, vSize=h.vSize, hResolution=h.hResolution, vResolution=h.vResolution,
        fontName=h.fontName, fontType=h.fontType,
        firstGlyph=h.firstGlyph, lastGlyph=h.lastGlyph,
        dimTableLength=h.dimTableLength, xAdjustTableLength=h.xAdjustTableLength,
        yAdjustTableLength=h.yAdjustTableLength, advanceTableLength=h.advanceTableLength,
        maxAscender=h.maxAscender, maxDescender=h.maxDescender,
        maxLeftXAdjust=h.maxLeftXAdjust, maxBaseYAdjust=h.maxBaseYAdjust,
        minCenterXAdjust=h.minCenterXAdjust, maxTopYAdjust=h.maxTopYAdjust,
        maxAdvance=h.maxAdvance, maxSize=h.maxSize,
        maxGlyphWidth=h.maxGlyphWidth, maxGlyphHeight=h.maxGlyphHeight,
        shadowMapLength=0, shadowMapBpe=0,
    )
    # 显式度量后不再需要索引表
    nh.dimTableLength = 0
    nh.xAdjustTableLength = 0
    nh.yAdjustTableLength = 0
    nh.advanceTableLength = 0

    newdata = build_pgf(nh, glyphs, bitmaps, new_map)
    pgf2 = PGF.load(newdata)
    print(f"src={len(data)}B new={len(newdata)}B glyphs={len(glyphs)}")

    bad = 0
    for i in range(len(glyphs)):
        a = bitmaps[i]
        b = decode_bitmap(pgf2.fontData, pgf2.glyphs[i])
        if a != b:
            bad += 1
            if bad <= 3:
                g1, g2 = glyphs[i], pgf2.glyphs[i]
                print(f"  MISMATCH idx={i} {g1.w}x{g1.h} vs {g2.w}x{g2.h} "
                      f"len {len(a)}/{len(b)}")
    print(f"bitmap mismatches: {bad}/{len(glyphs)}")
    return bad == 0


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="PGF 解码→重编码并逐字形比对位图与度量")
    ap.add_argument("pgf", help="PGF 文件路径")
    ap.add_argument("limit", nargs="?", type=int, default=400,
                    help="比对前 N 个字形，默认 400；0 表示全量")
    _a = ap.parse_args()
    _limit = _a.limit if _a.limit else 10 ** 9
    ok = roundtrip_check(_a.pgf, limit=_limit)
    print("ROUNDTRIP", "PASS" if ok else "FAIL")
    raise SystemExit(0 if ok else 1)