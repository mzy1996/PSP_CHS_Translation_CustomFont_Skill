# -*- coding: utf-8 -*-
"""PSP PGF (v2) 编解码器。

规范依据：PPSSPP Core/Font/PGF.h + PGF.cpp
- 头部 PGFHeader（packed，392 字节固定布局）
- dimTable / xAdjustTable / yAdjustTable / advanceTable：每项两个 s32
- shadowCharMap / charMap / charPointerTable：位打包（BPE）
- fontData：位流，字形头 + nibble RLE 位图
"""

import struct
from dataclasses import dataclass, field
from typing import List, Optional

# ---------------- 位读取（与 PGF.cpp getBits 语义一致） ----------------

def get_bits(buf: bytes, bitpos: int, nbits: int) -> int:
    """按 little-endian 32bit word 为单位、自低位向高位取 nbits。"""
    wordpos = bitpos >> 5
    bitoff = bitpos & 31
    base = wordpos * 4
    if base + 4 > len(buf):
        w0 = 0
    else:
        w0 = struct.unpack_from("<I", buf, base)[0]
    if bitoff + nbits <= 32:
        return (w0 >> bitoff) & ((1 << nbits) - 1)
    v = w0 >> bitoff
    done = 32 - bitoff
    remaining = nbits - done
    base2 = (wordpos + 1) * 4
    w1 = struct.unpack_from("<I", buf, base2)[0] if base2 + 4 <= len(buf) else 0
    v |= (w1 & ((1 << remaining) - 1)) << done
    return v


def get_table(buf: bytes, bpe: int, length: int):
    return [get_bits(buf, bpe * i, bpe) for i in range(length)]


def packed_size(length: int, bpe: int) -> int:
    """位打包表占用的字节数（按 32bit 对齐）"""
    if length <= 0 or bpe <= 0:
        return 0
    return (((length * bpe) + 31) & ~31) // 8


class BitWriter:
    """按 little-endian 32bit word 语义写位流（低位先写）。"""

    def __init__(self):
        self.words = []        # 每个 u32
        self.cur = 0
        self.bitcnt = 0        # 当前 word 已写位数

    def write(self, value: int, nbits: int):
        value &= (1 << nbits) - 1
        # 可能跨 word
        remain = nbits
        v = value
        while remain > 0:
            space = 32 - self.bitcnt
            take = min(space, remain)
            chunk = v & ((1 << take) - 1)
            self.cur |= chunk << self.bitcnt
            self.bitcnt += take
            v >>= take
            remain -= take
            if self.bitcnt == 32:
                self.words.append(self.cur)
                self.cur = 0
                self.bitcnt = 0

    def finish(self) -> bytes:
        if self.bitcnt > 0:
            self.words.append(self.cur)
            self.cur = 0
            self.bitcnt = 0
        return b"".join(struct.pack("<I", w & 0xFFFFFFFF) for w in self.words)


# ---------------- 头部 ----------------

HEADER_SIZE_STRUCT = 392  # 与实测 0x188 一致


@dataclass
class PGFHeader:
    headerOffset: int = 0
    headerSize: int = HEADER_SIZE_STRUCT
    magic: bytes = b"PGF0"
    revision: int = 2          # 注意：文件里 0x08 是 revision
    version: int = 6           # 0x0C 是 version
    charMapLength: int = 0
    charPointerLength: int = 0
    charMapBpe: int = 14
    charPointerBpe: int = 19
    pad1: bytes = b"\x00\x00"
    bpp: int = 4
    pad2: bytes = b"\x00"
    hSize: int = 648
    vSize: int = 648
    hResolution: int = 8192
    vResolution: int = 8192
    pad3: bytes = b"\x00"
    fontName: bytes = b""
    fontType: bytes = b""
    pad4: bytes = b"\x00"
    firstGlyph: int = 0
    lastGlyph: int = 0
    pad5: bytes = bytes(26)
    maxAscender: int = 0
    maxDescender: int = 0
    maxLeftXAdjust: int = 0
    maxBaseYAdjust: int = 0
    minCenterXAdjust: int = 0
    maxTopYAdjust: int = 0
    maxAdvance: tuple = (0, 0)
    maxSize: tuple = (0, 0)
    maxGlyphWidth: int = 0
    maxGlyphHeight: int = 0
    pad6: bytes = b"\x00\x00"
    dimTableLength: int = 0
    xAdjustTableLength: int = 0
    yAdjustTableLength: int = 0
    advanceTableLength: int = 0
    pad7: bytes = bytes(102)
    shadowMapLength: int = 0
    shadowMapBpe: int = 0
    unknown1: float = 0.0
    shadowScale: tuple = (0, 0)
    pad8: bytes = bytes(8)


def parse_header(d: bytes) -> PGFHeader:
    h = PGFHeader()
    o = 0
    def u16(off): return struct.unpack_from("<H", d, off)[0]
    def i32(off): return struct.unpack_from("<i", d, off)[0]
    h.headerOffset = u16(0x00)
    h.headerSize = u16(0x02)
    h.magic = d[0x04:0x08]
    h.revision = i32(0x08)
    h.version = i32(0x0C)
    h.charMapLength = i32(0x10)
    h.charPointerLength = i32(0x14)
    h.charMapBpe = i32(0x18)
    h.charPointerBpe = i32(0x1C)
    h.pad1 = d[0x20:0x22]
    h.bpp = d[0x22]
    h.pad2 = d[0x23:0x24]
    h.hSize = i32(0x24)
    h.vSize = i32(0x28)
    h.hResolution = i32(0x2C)
    h.vResolution = i32(0x30)
    h.pad3 = d[0x34:0x35]
    h.fontName = d[0x35:0x35 + 64]
    h.fontType = d[0x75:0x75 + 64]
    h.pad4 = d[0xB5:0xB6]
    h.firstGlyph = u16(0xB6)
    h.lastGlyph = u16(0xB8)
    h.pad5 = d[0xBA:0xD4]
    h.maxAscender = i32(0xD4)
    h.maxDescender = i32(0xD8)
    h.maxLeftXAdjust = i32(0xDC)
    h.maxBaseYAdjust = i32(0xE0)
    h.minCenterXAdjust = i32(0xE4)
    h.maxTopYAdjust = i32(0xE8)
    h.maxAdvance = (i32(0xEC), i32(0xF0))
    h.maxSize = (i32(0xF4), i32(0xF8))
    h.maxGlyphWidth = u16(0xFC)
    h.maxGlyphHeight = u16(0xFE)
    h.pad6 = d[0x100:0x102]
    h.dimTableLength = d[0x102]
    h.xAdjustTableLength = d[0x103]
    h.yAdjustTableLength = d[0x104]
    h.advanceTableLength = d[0x105]
    h.pad7 = d[0x106:0x106 + 102]
    h.shadowMapLength = i32(0x16C)
    h.shadowMapBpe = i32(0x170)
    h.unknown1 = struct.unpack_from("<f", d, 0x174)[0]
    h.shadowScale = (i32(0x178), i32(0x17C))
    h.pad8 = d[0x180:0x188]
    return h


def build_header(h: PGFHeader) -> bytes:
    b = bytearray(HEADER_SIZE_STRUCT)

    def put_u16(off, v): struct.pack_into("<H", b, off, v & 0xFFFF)
    def put_i32(off, v): struct.pack_into("<i", b, off, v)
    def put_bytes(off, data, size):
        b[off:off + min(len(data), size)] = data[:size]

    put_u16(0x00, h.headerOffset)
    put_u16(0x02, h.headerSize)
    b[0x04:0x08] = h.magic
    put_i32(0x08, h.revision)
    put_i32(0x0C, h.version)
    put_i32(0x10, h.charMapLength)
    put_i32(0x14, h.charPointerLength)
    put_i32(0x18, h.charMapBpe)
    put_i32(0x1C, h.charPointerBpe)
    b[0x20:0x22] = h.pad1
    b[0x22] = h.bpp
    b[0x23:0x24] = h.pad2
    put_i32(0x24, h.hSize)
    put_i32(0x28, h.vSize)
    put_i32(0x2C, h.hResolution)
    put_i32(0x30, h.vResolution)
    b[0x34:0x35] = h.pad3
    put_bytes(0x35, h.fontName, 64)
    put_bytes(0x75, h.fontType, 64)
    b[0xB5:0xB6] = h.pad4
    put_u16(0xB6, h.firstGlyph)
    put_u16(0xB8, h.lastGlyph)
    b[0xBA:0xD4] = h.pad5
    put_i32(0xD4, h.maxAscender)
    put_i32(0xD8, h.maxDescender)
    put_i32(0xDC, h.maxLeftXAdjust)
    put_i32(0xE0, h.maxBaseYAdjust)
    put_i32(0xE4, h.minCenterXAdjust)
    put_i32(0xE8, h.maxTopYAdjust)
    put_i32(0xEC, h.maxAdvance[0])
    put_i32(0xF0, h.maxAdvance[1])
    put_i32(0xF4, h.maxSize[0])
    put_i32(0xF8, h.maxSize[1])
    put_u16(0xFC, h.maxGlyphWidth)
    put_u16(0xFE, h.maxGlyphHeight)
    b[0x100:0x102] = h.pad6
    b[0x102] = h.dimTableLength
    b[0x103] = h.xAdjustTableLength
    b[0x104] = h.yAdjustTableLength
    b[0x105] = h.advanceTableLength
    b[0x106:0x106 + 102] = h.pad7
    put_i32(0x16C, h.shadowMapLength)
    put_i32(0x170, h.shadowMapBpe)
    struct.pack_into("<f", b, 0x174, h.unknown1)
    put_i32(0x178, h.shadowScale[0])
    put_i32(0x17C, h.shadowScale[1])
    b[0x180:0x188] = h.pad8
    return bytes(b)


# ---------------- 字形 ----------------

def s32(v: int) -> int:
    """把 32 位无符号值按有符号解释（对应 C++ int 回绕语义）。"""
    v &= 0xFFFFFFFF
    return v - (1 << 32) if v >= (1 << 31) else v


@dataclass
class Glyph:
    w: int = 0
    h: int = 0
    left: int = 0
    top: int = 0
    flags: int = 0
    shadowFlags: int = 0
    shadowID: int = 0
    advanceH: int = 0
    advanceV: int = 0
    dimensionWidth: int = 0
    dimensionHeight: int = 0
    xAdjustH: int = 0
    xAdjustV: int = 0
    yAdjustH: int = 0
    yAdjustV: int = 0
    ptr: int = 0          # 位图数据的字节偏移（相对 fontData）
    size_bits: int = 0    # 头部跳过的 14bit size 字段


def read_char_glyph(fontdata: bytes, charPtr: int, dimTable, xAdjTable, yAdjTable, advTable,
                    dimLen, xLen, yLen, advLen, header_name: bytes) -> Optional[Glyph]:
    """charPtr 是位偏移。"""
    if charPtr + 1024 > len(fontdata) * 8:
        return None
    g = Glyph()
    pos = charPtr
    def take(n):
        nonlocal pos
        v = get_bits(fontdata, pos, n)
        pos += n
        return v
    size_field = take(14)
    g.size_bits = size_field
    g.w = take(7)
    g.h = take(7)
    g.left = take(7)
    if g.left >= 64:
        g.left -= 128
    g.top = take(7)
    if g.top >= 64:
        g.top -= 128
    g.flags = take(6)
    g.shadowFlags = take(2) << 5
    g.shadowFlags |= take(2) << 3
    g.shadowFlags |= take(3)
    g.shadowID = take(9)

    is_jpcsp = header_name.split(b"\x00")[0] in (b"Liberation Sans", b"Liberation Serif",
                                                 b"Sazanami", b"UnDotum", b"Microsoft YaHei")

    if g.flags & 0x04:
        idx = take(8)
        if idx < dimLen:
            g.dimensionWidth, g.dimensionHeight = dimTable[idx]
        if idx == 0 and is_jpcsp:
            g.dimensionWidth = g.w << 6
            g.dimensionHeight = g.h << 6
    else:
        g.dimensionWidth = s32(take(32))
        g.dimensionHeight = s32(take(32))

    if g.flags & 0x08:
        idx = take(8)
        if idx < xLen:
            g.xAdjustH, g.xAdjustV = xAdjTable[idx]
        if idx == 0 and is_jpcsp:
            g.xAdjustH = g.left << 6
            g.xAdjustV = g.left << 6
    else:
        g.xAdjustH = s32(take(32))
        g.xAdjustV = s32(take(32))

    if g.flags & 0x10:
        idx = take(8)
        if idx < yLen:
            g.yAdjustH, g.yAdjustV = yAdjTable[idx]
        if idx == 0 and is_jpcsp:
            g.yAdjustH = g.top << 6
            g.yAdjustV = g.top << 6
    else:
        g.yAdjustH = s32(take(32))
        g.yAdjustV = s32(take(32))

    if g.flags & 0x20:
        idx = take(8)
        if idx < advLen:
            g.advanceH, g.advanceV = advTable[idx]
    else:
        g.advanceH = s32(take(32))
        g.advanceV = s32(take(32))

    g.ptr = pos // 8
    return g


def decode_bitmap(fontdata: bytes, g: Glyph) -> List[int]:
    """nibble RLE 解码，返回行优先的像素值数组（4bit）。"""
    n = g.w * g.h
    if n <= 0:
        return []
    bitPtr = g.ptr * 8
    out = [0] * n
    i = 0
    total_bits = len(fontdata) * 8
    while i < n and bitPtr + 8 < total_bits:
        nibble = get_bits(fontdata, bitPtr, 4)
        bitPtr += 4
        if nibble < 8:
            value = get_bits(fontdata, bitPtr, 4)
            bitPtr += 4
            count = nibble + 1
        else:
            count = 16 - nibble
        for _ in range(count):
            if i >= n:
                break
            if nibble >= 8:
                value = get_bits(fontdata, bitPtr, 4)
                bitPtr += 4
            out[i] = value
            i += 1
    return out


def encode_bitmap(pixels: List[int]) -> bytes:
    """把 4bit 像素序列编码为 nibble RLE 位流（返回按 32bit 对齐的字节）。"""
    bw = BitWriter()
    n = len(pixels)
    i = 0
    while i < n:
        v = pixels[i] & 0xF
        run = 1
        while i + run < n and pixels[i + run] == v and run < 8:
            run += 1
        if run >= 2:
            # nibble < 8: value + count(nibble+1)
            bw.write(run - 1, 4)
            bw.write(v, 4)
            i += run
        else:
            # 收集最多 8 个连续中不同的像素，用 nibble>=8 形式
            lit = [v]
            j = i + 1
            while j < n and len(lit) < 8:
                if j + 1 < n and pixels[j] == pixels[j + 1]:
                    break
                lit.append(pixels[j] & 0xF)
                j += 1
            bw.write(16 - len(lit), 4)
            for p in lit:
                bw.write(p, 4)
            i += len(lit)
    return bw.finish()


# ---------------- 顶层 PGF ----------------

@dataclass
class PGF:
    header: PGFHeader
    dimTable: list = field(default_factory=list)
    xAdjustTable: list = field(default_factory=list)
    yAdjustTable: list = field(default_factory=list)
    advanceTable: list = field(default_factory=list)
    shadowCharMap: list = field(default_factory=list)
    charMap: list = field(default_factory=list)
    charPointers: list = field(default_factory=list)
    glyphs: list = field(default_factory=list)
    fontData: bytes = b""
    raw: bytes = b""

    @staticmethod
    def load(data: bytes) -> "PGF":
        h = parse_header(data)
        if h.magic != b"PGF0":
            raise ValueError("not PGF0: %r" % (h.magic,))
        ptr = h.headerSize
        rev3_extra = None
        if h.revision == 3:
            rev3_extra = struct.unpack_from("<iiiii", data, ptr)
            ptr += 20
        dimLen, xLen, yLen, advLen = (h.dimTableLength, h.xAdjustTableLength,
                                      h.yAdjustTableLength, h.advanceTableLength)
        def read_pairs(count):
            nonlocal ptr
            out = []
            for _ in range(count):
                a, b = struct.unpack_from("<ii", data, ptr)
                out.append((a, b))
                ptr += 8
            return out
        dimTable = read_pairs(dimLen)
        xAdjustTable = read_pairs(xLen)
        yAdjustTable = read_pairs(yLen)
        advanceTable = read_pairs(advLen)
        shadowSize = packed_size(h.shadowMapLength, h.shadowMapBpe)
        shadowBuf = data[ptr:ptr + shadowSize]
        ptr += shadowSize
        if h.revision == 3 and rev3_extra:
            c1 = rev3_extra[1]; c2 = rev3_extra[3]
            ptr += (c1 + c2) * 4
        charMapSize = packed_size(h.charMapLength, h.charMapBpe)
        charMapBuf = data[ptr:ptr + charMapSize]
        ptr += charMapSize
        charPtrSize = packed_size(h.charPointerLength, h.charPointerBpe)
        charPtrBuf = data[ptr:ptr + charPtrSize]
        ptr += charPtrSize
        fontDataOffset = ptr
        fontData = data[fontDataOffset:]

        charMap = get_table(charMapBuf, h.charMapBpe, h.charMapLength)
        charMap = [v if v < h.charPointerLength else 0xFFFF for v in charMap]
        charPointers = get_table(charPtrBuf, h.charPointerBpe, h.charPointerLength)
        shadowMap = get_table(shadowBuf, h.shadowMapBpe, h.shadowMapLength) if h.shadowMapLength else []

        glyphs = []
        for cp in charPointers:
            if cp < 0:
                glyphs.append(None)
                continue
            g = read_char_glyph(fontData, cp * 32, dimTable, xAdjustTable, yAdjustTable, advanceTable,
                                dimLen, xLen, yLen, advLen, h.fontName)
            glyphs.append(g)
        return PGF(header=h, dimTable=dimTable, xAdjustTable=xAdjustTable, yAdjustTable=yAdjustTable,
                   advanceTable=advanceTable, shadowCharMap=shadowMap, charMap=charMap,
                   charPointers=charPointers, glyphs=glyphs, fontData=fontData, raw=data)

    def glyph_for(self, char_code: int) -> Optional[Glyph]:
        h = self.header
        if char_code < h.firstGlyph:
            return None
        idx = char_code - h.firstGlyph
        if idx < len(self.charMap):
            idx = self.charMap[idx]
        if idx >= len(self.glyphs):
            return None
        return self.glyphs[idx]

    def bitmap_for(self, char_code: int) -> Optional[List[int]]:
        g = self.glyph_for(char_code)
        if not g:
            return None
        return decode_bitmap(self.fontData, g)


if __name__ == "__main__":
    import argparse, os
    ap = argparse.ArgumentParser(description="解析 PGF 字库结构并抽样解码字形")
    ap.add_argument("pgf", help="PGF 文件路径")
    ap.add_argument("--chars", default="あA漢高",
                    help="抽样检查的字符，默认 'あA漢高'（换成你的目标语言用字更直观）")
    _a = ap.parse_args()
    p = _a.pgf
    data = open(p, "rb").read()
    pgf = PGF.load(data)
    h = pgf.header
    print("file:", os.path.basename(p), len(data), "bytes")
    print(f"revision={h.revision} version={h.version} bpp={h.bpp}")
    print(f"charMapLength={h.charMapLength} charPointerLength={h.charPointerLength} "
          f"charMapBpe={h.charMapBpe} charPointerBpe={h.charPointerBpe}")
    print(f"firstGlyph={h.firstGlyph} lastGlyph={h.lastGlyph}")
    print(f"fontName={h.fontName.split(b'\x00')[0]!r} fontType={h.fontType.split(b'\x00')[0]!r}")
    print(f"tables: dim={h.dimTableLength} xAdj={h.xAdjustTableLength} "
          f"yAdj={h.yAdjustTableLength} adv={h.advanceTableLength} shadow={h.shadowMapLength}/{h.shadowMapBpe}")
    print(f"fontData: offset={len(data)-len(pgf.fontData)} size={len(pgf.fontData)}")
    print(f"maxSize={h.maxSize} maxGlyph={h.maxGlyphWidth}x{h.maxGlyphHeight}")
    # 试渲染几个字符，统计非零
    for ch in _a.chars:
        code = ord(ch)
        bm = pgf.bitmap_for(code)
        g = pgf.glyph_for(code)
        if bm is None:
            print(f"  {ch!r} U+{code:04X}: no glyph")
        else:
            nz = sum(1 for v in bm if v)
            print(f"  {ch!r} U+{code:04X}: glyph {g.w}x{g.h} left={g.left} top={g.top} "
                  f"adv={g.advanceH} nonzero={nz}/{len(bm)}")