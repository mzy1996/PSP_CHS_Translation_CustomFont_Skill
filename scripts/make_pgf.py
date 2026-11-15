# -*- coding: utf-8 -*-
"""用 TTF 渲染字形并生成 PSP PGF 字库。

度量口径（26.6 定点，与 jpn0.pgf 一致）：
  w/h             位图尺寸（像素）
  left = bbox.x0  左边距
  top  = ascent - bbox.y0  基线以上的高度
  xAdjustH = left << 6
  yAdjustH = top << 6
  advanceH = round(字宽 * 64)
  dimensionWidth/Height = w/h << 6
"""

import json
import os
import sys

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pgf import Glyph, PGFHeader, PGF, decode_bitmap  # noqa: E402
from pgfenc import build_pgf  # noqa: E402

# 默认值留空：实际路径由 --ttf / --text 指定，避免把作者本机路径写进公开发布的脚本
TTF = ""
TEXT_JSONL = ""

FIRST_GLYPH = 0x20
CHAR_MAP_LEN = 0xFFFF - FIRST_GLYPH + 1   # 覆盖 U+0020..U+FFFF

# 兜底：常用标点与符号，避免文本集外的 UI 字符缺失
EXTRA_PUNCT = "　、。，．・：；？！「」『』（）［］｛｝〈〉《》【】〔〕" \
              "～〰…‥―─｜／＼＋－＝＜＞＄％＃＆＊＠＾＿｜" \
              "○×△□☆★●◎◇◆■□▲▼←→↑↓↔↕" \
              "０１２３４５６７８９" \
              "ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ" \
              "ａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ"


def collect_charset(mode: str = "cht") -> set:
    """mode: cht = 含 Big5 繁体全集；chs = 仅 GB2312 + 游戏文本。"""
    chars = set()
    if TEXT_JSONL and os.path.exists(TEXT_JSONL):
        with open(TEXT_JSONL, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    chars |= set(json.loads(line)["text"])
                except Exception:
                    chars |= set(line)
    # ASCII 可打印
    chars |= {chr(c) for c in range(0x20, 0x7F)}
    # 标点与符号
    chars |= set(EXTRA_PUNCT)
    # 日文假名（游戏内可能残留）
    chars |= {chr(c) for c in range(0x3041, 0x3097)}
    chars |= {chr(c) for c in range(0x30A1, 0x30FB)}
    # 简体全集
    chars |= gb2312_all()
    if mode == "cht":
        chars |= big5_all()
    # 过滤控制字符
    chars = {c for c in chars if ord(c) >= 0x20}
    return chars


def gb2312_all() -> set:
    """GB2312 全集（6763 汉字 + 682 符号）。"""
    out = set()
    for hi in range(0xA1, 0xF8):
        for lo in range(0xA1, 0xFF):
            try:
                ch = bytes([hi, lo]).decode("gb2312")
            except UnicodeDecodeError:
                continue
            out.add(ch)
    return out


def big5_all() -> set:
    """Big5 全集（13053 字），覆盖繁体常用与次常用字。"""
    out = set()
    for hi in range(0xA1, 0xFA):
        for lo in range(0x40, 0xFF):
            try:
                ch = bytes([hi, lo]).decode("big5")
            except UnicodeDecodeError:
                continue
            out.add(ch)
    return out


def render_glyph(font: ImageFont.FreeTypeFont, ascent: int, descent: int, ch: str):
    """渲染单字，返回 (Glyph, 行优先 4bit 像素列表) 或 (None, None)。"""
    adv = font.getlength(ch)
    cell_w = int(adv) + 8
    cell_h = ascent + descent + 4
    if cell_w <= 0 or cell_h <= 0:
        return None, None
    img = Image.new("L", (cell_w, cell_h), 0)
    d = ImageDraw.Draw(img)
    d.text((2, 0), ch, font=font, fill=255)
    bbox = img.getbbox()
    if bbox is None:
        # 空白字符（如空格）：零尺寸字形，仅保留 advance
        g = Glyph(w=0, h=0, left=0, top=0, flags=0x01,
                  advanceH=int(round(adv * 64)), advanceV=0,
                  dimensionWidth=0, dimensionHeight=0,
                  xAdjustH=0, xAdjustV=0, yAdjustH=0, yAdjustV=0)
        return g, []
    x0, y0, x1, y1 = bbox
    w, h = x1 - x0, y1 - y0
    if w > 127 or h > 127:
        # 超出 7bit 字段范围，裁剪（正常字号不会发生）
        w, h = min(w, 127), min(h, 127)
    crop = img.crop((x0, y0, x0 + w, y0 + h))
    gray = crop.tobytes()
    pixels = [int(round(v * 15 / 255)) for v in gray]
    left = x0 - 2
    top = ascent - y0
    g = Glyph(
        w=w, h=h, left=left, top=top, flags=0x01,
        advanceH=int(round(adv * 64)), advanceV=0,
        dimensionWidth=w << 6, dimensionHeight=h << 6,
        xAdjustH=(left << 6), xAdjustV=(left << 6),
        yAdjustH=(top << 6), yAdjustV=(top << 6),
    )
    return g, pixels


def build(font_path: str, size: int, out_path: str, font_name: str = None, mode: str = "cht"):
    font = ImageFont.truetype(font_path, size)
    ascent, descent = font.getmetrics()
    if not font_name:
        # 默认沿用 TTF 自身的家族名，便于在多份自制字库之间辨认
        font_name = font.getname()[0] or "CustomFont"
    print(f"font={font.getname()} size={size} ascent={ascent} descent={descent}")

    charset = sorted(collect_charset(mode), key=ord)
    print(f"charset: {len(charset)} chars")

    # 只保留有字形的字符
    glyphs, bitmaps, code_to_index = [], [], {}
    missing = []
    for ch in charset:
        code = ord(ch)
        if code < FIRST_GLYPH or code > 0xFFFF:
            continue
        g, px = render_glyph(font, ascent, descent, ch)
        if g is None:
            missing.append(ch)
            continue
        code_to_index[code] = len(glyphs)
        glyphs.append(g)
        bitmaps.append(px)
    print(f"rendered glyphs: {len(glyphs)}, skipped: {len(missing)}")

    char_map = []
    for i in range(CHAR_MAP_LEN):
        code = FIRST_GLYPH + i
        char_map.append(code_to_index.get(code, 0xFFFF))

    max_w = max((g.w for g in glyphs), default=0)
    max_h = max((g.h for g in glyphs), default=0)
    max_adv = max((g.advanceH for g in glyphs), default=0)
    max_asc = max((g.yAdjustH for g in glyphs), default=0)
    min_desc = min((g.yAdjustH - (g.h << 6) for g in glyphs), default=0)

    h = PGFHeader(
        revision=2, version=6, bpp=4,
        charMapBpe=16, charPointerBpe=24,
        hSize=size * 64, vSize=size * 64,
        hResolution=8192, vResolution=8192,
        fontName=font_name.encode("latin-1", "replace")[:63],
        fontType=b"Regular",
        firstGlyph=FIRST_GLYPH, lastGlyph=0xFFFF,
        dimTableLength=0, xAdjustTableLength=0, yAdjustTableLength=0, advanceTableLength=0,
        maxAscender=max_asc, maxDescender=min_desc,
        maxLeftXAdjust=max((g.xAdjustH for g in glyphs), default=0),
        maxBaseYAdjust=min((g.yAdjustH for g in glyphs), default=0),
        minCenterXAdjust=0,
        maxTopYAdjust=max((g.yAdjustH for g in glyphs), default=0),
        maxAdvance=(max_adv, max_adv),
        maxSize=(max_w << 6, max_h << 6),
        maxGlyphWidth=max_w, maxGlyphHeight=max_h,
        shadowMapLength=0, shadowMapBpe=0,
    )
    data = build_pgf(h, glyphs, bitmaps, char_map)
    out_dir = os.path.dirname(out_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(data)
    print(f"written {out_path} ({len(data)} bytes)")
    return data, glyphs, bitmaps, char_map


def probe_chars(text_path, mode):
    """自检探针：优先取游戏文本前几个字，其次用常见汉字，再退回 ASCII。

    这样换到别的游戏时不必改脚本，探针会自然跟着该游戏的文本走。
    """
    cand = ""
    if text_path and os.path.exists(text_path):
        try:
            with open(text_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        cand += json.loads(line)["text"]
                    except Exception:
                        cand += line
                    if len(cand) >= 40:
                        break
        except Exception:
            pass
    # 去掉空白与控制字符，取前 6 个可显示字符
    cand = "".join(c for c in cand if ord(c) >= 0x20)
    if len(cand) >= 6:
        return cand[:6] + "ABC1."
    return "中文字库測試ABC1。" if mode == "cht" else "中文字库测试ABC1。"


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="TTF 渲染生成 PSP PGF 字库")
    ap.add_argument("--ttf", required=True, help="TTF/OTF 字体路径")
    ap.add_argument("--size", type=int, default=18, help="字号像素，默认 18（与 jpn0 基准一致）")
    ap.add_argument("--charset", choices=["cht", "chs"], default="cht",
                    help="cht=含 Big5 繁体全集；chs=仅 GB2312+游戏文本")
    ap.add_argument("--text", default=None,
                    help="可选文本 jsonl（每行含 \"text\" 字段），用于补齐游戏实际用字")
    ap.add_argument("--out", required=True, help="输出 PGF 路径")
    ap.add_argument("--font-name", default=None,
                    help="写入 PGF 头的字体名，默认取 TTF 的家族名")
    a = ap.parse_args()

    globals()["TTF"] = a.ttf
    globals()["TEXT_JSONL"] = a.text

    data, glyphs, bitmaps, char_map = build(a.ttf, a.size, a.out,
                                             font_name=a.font_name, mode=a.charset)

    pgf = PGF.load(data)
    print(f"reload: glyphs={len(pgf.glyphs)} charMap={len(pgf.charMap)} fontData={len(pgf.fontData)}")
    bad = 0
    probe = probe_chars(a.text, a.charset)
    for ch in probe:
        g = pgf.glyph_for(ord(ch))
        if g is None:
            print(f"  {ch}: MISSING")
            bad += 1
            continue
        bm = decode_bitmap(pgf.fontData, g)
        nz = sum(1 for v in bm if v)
        print(f"  {ch}: {g.w}x{g.h} left={g.left} top={g.top} adv={g.advanceH} nonzero={nz}")
    print("SELFCHECK", "OK" if bad == 0 else "FAIL")
