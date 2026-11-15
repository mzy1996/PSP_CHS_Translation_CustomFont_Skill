# 实测参数与体积对照

以下数据是一组**完整跑通的实际样本**（PSP《高达：战争记忆》简繁双版汉化），
PGF 头部字段与 `scripts/pgf.py` 的解码结果逐项核对。数值仅作参照：
换游戏时字号必须按该游戏原字库的 `hSize/64` 重新确定，字符集按其译文用字重新统计。

## 1. 字体来源

| 项目 | 值 |
|---|---|
| 字体文件 | `WenQuanYiMicroHei-01.ttf`（文泉驿微米黑） |
| 渲染字号 | 18 px |
| 渲染方式 | Pillow `ImageFont.truetype`，灰度渲染后量化为 4 bpp |
| 度量口径 | 26.6 定点；`hSize = vSize = 18 × 64 = 1152` |

字号选择依据：PSP 系统日文字库 `jpn0.pgf` 的字面高度基准约为 18 px。字号偏离会让中文
相对原文偏大或偏小。

## 2. 字库体积对照

| 版本 | 文件 | 字形数 | 文件字节 | charMapBpe | charPointerBpe | 字符集 |
|---|---|---:|---:|---:|---:|---|
| 繁体 | `gmchtfonts.pgf` | 16629 | 3,040,804 | 16 | 24 | GB2312 + Big5 + 游戏文本 + ASCII + 标点 + 假名 |
| 简体 | `gmchsfonts.pgf` | 8302 | 1,525,972 | 16 | 24 | GB2312 + 游戏文本 + ASCII + 标点 + 假名 |

对应字符集的实际覆盖：

- GB2312：6763 个汉字 + 682 个符号；
- Big5：约 13053 个繁体字（仅繁体版纳入）；
- 游戏文本：从文本资源导出后按实际用字补齐；
- ASCII 可打印区、常用中文标点与全角符号、平假名/片假名作为兜底。

字体数据（fontData）长度：繁体 2,859,516 字节，简体 1,369,664 字节。

## 3. PGF 头部参数

| 字段 | 值 | 说明 |
|---|---|---|
| revision | 2 | PGF v2 布局，无 rev3 扩展 |
| version | 6 | |
| bpp | 4 | 4 bit 灰度 |
| headerSize | 0x188（392） | 头部固定长度 |
| hSize / vSize | 1152 / 1152 | 18 px × 64 |
| hResolution / vResolution | 8192 / 8192 | |
| charMapLength | 65504 | 覆盖 U+0020..U+FFFF |
| firstGlyph / lastGlyph | 0x20 / 0xFFFF | |
| dimTableLength | 0 | 度量走字形头内 32 位显式字段 |
| xAdjustTableLength | 0 | 同上 |
| yAdjustTableLength | 0 | 同上 |
| advanceTableLength | 0 | 同上 |
| fontName | `WenQuanYi Micro Hei` | |
| fontType | `Regular` | |

## 4. 编解码往返

- 两套成品字库生成后都用 `scripts/pgf.py` 重新加载并解码抽样字符，确认字宽、字高、
  `left`、`top`、`advance` 与非零像素数符合预期。
- 一轮全量往返校验（9777 字形样本）：把解码出的字形与位图重新编码，再解码比对，
  **位图逐像素零差异，全部度量字段零差异**。
- 全量校验的口径是「字形位图 + 度量字段」，不是文件字节相同：重新压缩后的位流长度
  允许变化，但语义必须一致。

## 5. 编码要点

1. **charPointer 单位是 32 bit**：`bit_offset = charPointer[i] * 32`，每个字形起点要
   对齐到 32 bit 边界。
2. **fontData 末尾补 ≥512 字节 0**：解码端有
   `charPtr + 1024 > fontDataSize * 8` 的越界检查（`charPtr` 以 32 bit 为单位），
   末尾字形离数据尾太近会被整字丢弃。补 512 字节 0 可以稳定规避。
3. **charPointerBpe 选 24 bit**：字库超过 2 MB 时 `fontData_size * 8 / 32` 超过
   19 bit，14/16 bit 都会溢出。
4. **32 位显式度量按有符号解释**：`xAdjustH`、`yAdjustH`、`advanceH` 等字段在
   C++ 侧是 `int`，负值必须按补码回绕读取，按无符号读会让字形位置错乱。
5. **charMap 越界值用 0xFFFF**：16 bit 的 charMap 足以表示 16629 个字形，
   未收录码位统一写 `0xFFFF`。