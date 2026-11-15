#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scan_font_usage.py — 判定一个 PSP 游戏怎么用字库，决定要不要劫持桩。

这是本技能的第 0 步，对任意 PSP 游戏都适用：先把「它怎么取字库」查清楚，
再决定用哪条接入路线，避免对已支持外部字库的游戏白做一套劫持。

判据（按证据强弱排序）：
  1. 导入表/字符串里出现 `sceFontOpenUserFile` 或 `sceFontOpenUserMemory`
     → 游戏本身就能从指定路径/内存读字体，**无需劫持桩**，直接内置 PGF 并改调用参数即可。
  2. 只出现 `sceFontOpen`（按索引取系统字库）→ **必须劫持**：桩把索引打开重定向为按路径打开。
  3. 出现 `flash0:/font/*.pgf` 等系统字库路径串 → 说明取字库走系统，进一步印证第 2 条。
  4. 出现 `.pgf` 但无任何 sceFont 符号 → 可能自带专用字体加载器，需人工看调用点。

用法：
  # 传解密后的 ELF/PRX/EBOOT（明文 ~PSP 先解成 ELF）
  python scripts/scan_font_usage.py /path/to/EBOOT.BIN
  # 顺带报告游戏自带某份 PGF 的字号，用于对齐生成参数
  python scripts/scan_font_usage.py /path/to/EBOOT.BIN --pgf /path/to/font.pgf

注意：加密的 EBOOT.BIN 直接扫只会得到噪声，需先用相应工具解密。
本脚本只做只读静态分析，不修改任何文件。
"""
import argparse
import os
import re
import struct
import sys

# 只保留可确证的 NID；其它 sceFont 符号按字符串识别，避免误报
KNOWN_NIDS = {
    0x57FCB733: "sceFontOpenUserFile",
    0xBB8E7FE6: "sceFontOpenUserMemory",
    0x0DA7535E: "sceFontOpen",
}

STR_RE = re.compile(rb"[ -~]{4,}")
PGF_PATH_RE = re.compile(rb"[A-Za-z0-9_:/.\-]*\.pgf", re.I)
FLASH_FONT_RE = re.compile(rb"flash\d:/[A-Za-z0-9_/.\-]*font[A-Za-z0-9_/.\-]*", re.I)


def read_pgf_size(path):
    """读取 PGF 头的 hSize/vSize（26.6 定点），返回像素字号。"""
    d = open(path, "rb").read()
    if len(d) < 0x30:
        return None
    hsize = struct.unpack_from("<I", d, 0x24)[0]
    vsize = struct.unpack_from("<I", d, 0x28)[0]
    return round(hsize / 64.0, 2), round(vsize / 64.0, 2)


def scan(path):
    d = open(path, "rb").read()
    out = {"size": len(d), "elf": False, "psp": False}
    out["elf"] = d[:4] == b"\x7fELF"
    out["psp"] = d[:4] == b"~PSP"

    strings = [m.group().decode("latin-1") for m in STR_RE.finditer(d)]
    out["font_paths"] = sorted({m.group().decode("latin-1")
                                for m in PGF_PATH_RE.finditer(d)})
    out["flash_font"] = sorted({m.group().decode("latin-1")
                                for m in FLASH_FONT_RE.finditer(d)})
    out["font_symbols"] = sorted({s for s in strings
                                  if s.startswith("sceFont") or "LibFtt" in s or s == "libfont"})
    # 目标本身是字库模块时（导出 sceLibFont 一类），不能当作游戏主模块判据
    out["is_font_module"] = any(s.startswith("sceLibFtt") or s == "sceLibFont"
                                for s in out["font_symbols"])
    # NID 扫描：4 字节小端
    nids = {}
    for nid, name in KNOWN_NIDS.items():
        pat = struct.pack("<I", nid)
        pos = []
        i = d.find(pat)
        while i >= 0 and len(pos) < 8:
            pos.append(i)
            i = d.find(pat, i + 1)
        if pos:
            nids[name] = pos
    out["nids"] = nids
    return out


def verdict(r):
    names = set(r["nids"])
    has_user = {"sceFontOpenUserFile", "sceFontOpenUserMemory"} & names
    has_open = "sceFontOpen" in names
    has_sym_user = any("UserFile" in s or "UserMemory" in s for s in r["font_symbols"])
    has_sym_open = any(s == "sceFontOpen" for s in r["font_symbols"])

    lines = []
    if r["psp"] and not r["elf"]:
        lines.append("判定：目标仍是加密 ~PSP 模块，静态字符串/NID 扫描无效。")
        lines.append("路线：先解密为 ELF 再重跑本脚本；解密不属于本技能范围。")
        return lines
    if r["is_font_module"]:
        lines.append("判定：目标自身是一个字体模块（导出 %s 一类的库），不是游戏主模块。"
                     % ", ".join(r["font_symbols"][:3] or ["font"]))
        lines.append("路线：请改扫游戏的 EBOOT / 主 PRX。若这个模块就是劫持桩，"
                     "下一步用 patch_libfont_path.py 改它内部的字库路径。")
        return lines
    if has_user or has_sym_user:
        lines.append("判定：游戏本身支持按路径/内存打开字体（sceFontOpenUser*）。")
        lines.append("路线：无需劫持桩。内置 PGF 后，把调用处的路径参数指向新字库即可。")
    elif has_open or has_sym_open:
        lines.append("判定：游戏只按索引打开系统字库（sceFontOpen）。")
        lines.append("路线：需要劫持桩，把索引式打开重定向为按路径打开镜像内 PGF。")
    elif r["flash_font"]:
        lines.append("判定：发现系统字库路径串，但未识别到固定 NID。")
        lines.append("路线：先反汇编确认导入符号（stub 表），再按结果选路线；大概率需要劫持。")
    elif r["font_paths"]:
        lines.append("判定：出现 .pgf 路径但没有 sceFont 符号。")
        lines.append("路线：可能自带字体加载器，需人工看引用点，不宜直接套用劫持桩。")
    else:
        lines.append("判定：未发现任何字库线索。")
        lines.append("路线：先确认字库是否被打包在容器内（BARC/CPK 等）或字库为内嵌位图字体。")
    return lines


def main():
    ap = argparse.ArgumentParser(
        description="判定 PSP 游戏的字库使用方式（是否走系统字库、是否需要劫持桩）")
    ap.add_argument("target", help="解密后的 ELF/PRX/EBOOT 文件路径")
    ap.add_argument("--pgf", help="可选：游戏自带或系统参考的 PGF，打印其字号", default=None)
    a = ap.parse_args()

    if not os.path.exists(a.target):
        raise SystemExit(f"文件不存在: {a.target}")
    r = scan(a.target)

    print(f"file: {a.target} ({r['size']} bytes)")
    print(f"format: {'ELF' if r['elf'] else ('~PSP' if r['psp'] else 'unknown')}")
    if r["psp"] and not r["elf"]:
        print("提示：这是 ~PSP 加密模块，字符串/NID 扫描结果不可靠，请先解密为 ELF。")
    print("font symbols:", r["font_symbols"] or "(none)")
    print("pgf paths:", r["font_paths"] or "(none)")
    print("flash font paths:", r["flash_font"] or "(none)")
    if r["nids"]:
        for k, v in r["nids"].items():
            print(f"NID {k}: at {[hex(x) for x in v]}")
    else:
        print("NID: (none matched)")

    if a.pgf:
        size = read_pgf_size(a.pgf)
        if size:
            print(f"pgf size: hSize={size[0]}px vSize={size[1]}px  (供 --size 对齐)")

    print()
    for line in verdict(r):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())