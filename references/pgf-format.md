# PGF 字库格式说明（PSP sceFont）

本文基于对官方 `jpn0.pgf`、`kr0.pgf`、`aroy.pgf`、`fonts.pgf`（ttf2pgf 产物）的实测，
并与 PPSSPP `Core/Font/PGF.h` / `PGF.cpp` 的实现逐字段核对。

## 1. 文件总体布局

```
[PGFHeader 392 字节]
[dimTable]        dimTableLength      × 8 字节（两个 s32）
[xAdjustTable]    xAdjustTableLength  × 8
[yAdjustTable]    yAdjustTableLength  × 8
[advanceTable]    advanceTableLength  × 8
[shadowCharMap]   位打包
[charMap]         位打包
[charPointerTable]位打包
[fontData]        字形头 + nibble RLE 位图
```

`revision == 3` 时，头部之后还有一个 20 字节的 `rev3extra`，并在 shadowCharMap 后
追加两张压缩字符映射表。

## 2. 头部字段（packed，共 392 字节 = 0x188）

| 偏移 | 类型 | 字段 | 说明 |
|---|---|---|---|
| 0x00 | u16 | headerOffset | 常为 0 |
| 0x02 | u16 | headerSize | 实测 0x188 |
| 0x04 | char[4] | magic | `PGF0` |
| 0x08 | s32 | revision | 2 或 3 |
| 0x0C | s32 | version | 常见 6 |
| 0x10 | s32 | charMapLength | 字符码覆盖长度（本例 65504） |
| 0x14 | s32 | charPointerLength | **字形数量**（本例 16629） |
| 0x18 | s32 | charMapBpe | charMap 每项位数（本例 16） |
| 0x1C | s32 | charPointerBpe | charPointer 每项位数（本例 24） |
| 0x20 | u8[2] | pad1 | 官方字库为 `04 04` |
| 0x22 | u8 | bpp | 位深，4 |
| 0x23 | u8[1] | pad2 | |
| 0x24 | s32 | hSize | 26.6 定点，18px → 1152 |
| 0x28 | s32 | vSize | 同上 |
| 0x2C | s32 | hResolution | 常见 8192 |
| 0x30 | s32 | vResolution | 常见 8192 |
| 0x34 | u8[1] | pad3 | |
| 0x35 | char[64] | fontName | 字体名（ttf2pgf 产物为 TTF 名） |
| 0x75 | char[64] | fontType | 如 `Regular` |
| 0xB5 | u8[1] | pad4 | |
| 0xB6 | u16 | firstGlyph | 字符码起点（常为 0x20） |
| 0xB8 | u16 | lastGlyph | 常为 0xFFFF |
| 0xBA | u8[26] | pad5 | |
| 0xD4 | s32 | maxAscender | |
| 0xD8 | s32 | maxDescender | |
| 0xDC | s32 | maxLeftXAdjust | |
| 0xE0 | s32 | maxBaseYAdjust | |
| 0xE4 | s32 | minCenterXAdjust | |
| 0xE8 | s32 | maxTopYAdjust | |
| 0xEC | s32[2] | maxAdvance | {H, V} |
| 0xF4 | s32[2] | maxSize | {W, H} |
| 0xFC | u16 | maxGlyphWidth | |
| 0xFE | u16 | maxGlyphHeight | |
| 0x100 | u8[2] | pad6 | |
| 0x102 | u8 | dimTableLength | |
| 0x103 | u8 | xAdjustTableLength | |
| 0x104 | u8 | yAdjustTableLength | |
| 0x105 | u8 | advanceTableLength | |
| 0x106 | u8[102] | pad7 | |
| 0x16C | s32 | shadowMapLength | 0 表示无阴影映射 |
| 0x170 | s32 | shadowMapBpe | |
| 0x174 | float | unknown1 | |
| 0x178 | s32[2] | shadowScale | |
| 0x180 | u8[8] | pad8 | |

## 3. 位打包表

三张表都是**按位**存储，读取语义（与 PPSSPP `getBits` 一致）：

- 以 **little-endian 32 位字**为单位
- 位序自低到高，位置 `pos` 表示第 `pos` 位
- 表字节数 = `((length * bpe) + 31) & ~31) / 8`

```python
def get_bits(buf, bitpos, nbits):
    wordpos, bitoff = bitpos >> 5, bitpos & 31
    w0 = u32(buf, wordpos * 4)
    if bitoff + nbits <= 32:
        return (w0 >> bitoff) & ((1 << nbits) - 1)
    v = w0 >> bitoff
    done = 32 - bitoff
    w1 = u32(buf, (wordpos + 1) * 4)
    return v | ((w1 & ((1 << (nbits - done)) - 1)) << done)
```

## 4. charMap 与字符解析

```python
if char_code < firstGlyph: return None
idx = char_code - firstGlyph
if idx < charMapLength:
    idx = charMap[idx]          # 码位 → 字形下标
if idx >= charPointerLength:
    return None
```

`charMap` 用 `0xFFFF` 之类越界值表示「无字形」。因此 **charMapBpe 必须能表示到
`charPointerLength-1`**：字形数超过 16383 时 14bit 不够，用 16bit 更安全。

## 5. charPointer 与字形定位

`charPointer[i]` 是**位偏移除以 32**（即单位 32 bit）：

```python
bit_offset = charPointer[i] * 32
```

因此每个字形起点必须在 32 bit 边界，编码时前面要补零对齐。
`charPointerBpe` 要能容纳 `fontData_size * 8 / 32`：字库超过 2MB 时 19bit 溢出，取 24bit。

## 6. 字形头（位于 fontData 内，位流）

按顺序读取：

| 位数 | 字段 |
|---|---|
| 14 | size（解码端跳过，写 0 即可） |
| 7 | w（位图宽） |
| 7 | h（位图高） |
| 7 | left（有符号：>=64 则减 128） |
| 7 | top（同上） |
| 6 | flags |
| 2+2+3 | shadowFlags |
| 9 | shadowID |
| 8 或 32+32 | dimension（flags&0x04 时用索引，否则显式） |
| 8 或 32+32 | xAdjust（flags&0x08） |
| 8 或 32+32 | yAdjust（flags&0x10） |
| 8 或 32+32 | advance（flags&0x20） |

flags 的位图方向位：`0x01` 行优先（H_ROWS）、`0x02` 列优先（V_ROWS）、`0x03` overlay。

**32 位显式度量必须按有符号解释**（C++ 里 `int` 自然回绕）。按无符号读会让负值变成
约 42 亿，字形位置错乱。

## 7. 位图：nibble RLE

从 `glyph.ptr * 8` 位开始，按 4 位为一组解码，输出 `w*h` 个 4bit 像素：

```
nibble = 4bit
if nibble < 8:  value = 4bit;  count = nibble + 1        # 重复同一值
else:           count = 16 - nibble                      # 每个像素各读 4bit
```

行优先（flags & 0x03 == 0x01）时索引为 `y*w + x`，否则 `x*h + y`。

编码时的最小实现：连续相同值 ≥2 用第一种形式（最多 8 个），否则用第二种形式打包
最多 8 个不同值。压缩率不如官方，但可被硬件正确解码。

## 8. 编码要点清单

1. 头部 392 字节先写全，`charMapLength` 与 `charPointerLength` 与实际表一致
2. 每字形头写入前把当前位偏移对齐到 32 bit
3. `charPointer[i] = bit_offset // 32`
4. fontData 末尾补 ≥512 字节 0（否则末尾字形被越界检查丢弃）
5. 度量按 26.6 定点且**有符号**写入
6. 生成后用解码器重新读回，逐字形比对位图与度量，做到零差异