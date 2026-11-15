#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""patch_libfont_path.py — 改写 PSP libfont 劫持桩内的字体路径字符串。

适用：劫持桩（如梦幻之星2/合金装备汉化版的改版 libfont.prx）内部写死了字体路径，
需要改成自己的字库名（长度可与原串不同）。

做法：
  1) 定位原路径字符串（默认自动查找 disc0:/ 开头且以 .pgf 结尾的串）
  2) 在 .rodata 段之后的空隙写入新路径（不需要移动任何已有数据）
  3) 扫描 .text 中立即数等于旧虚拟地址的 addiu 指令，改写为新地址
  4) 同步扩展 .rodata 节大小
  5) 校验 ELF 段/节结构与新字符串可读

用法：
  python patch_libfont_path.py <libfont.prx> -n "disc0:/PSP_GAME/USRDIR/xxx.pgf" -o out.prx
  python patch_libfont_path.py <libfont.prx> -n "..." -o out.prx -old "disc0:/PSP_GAME/USRDIR/fonts.pgf"
"""
import argparse
import struct
import sys
from pathlib import Path

ELF_HDR = "<4s5B7x2H5I6H"
SHDR = "<10I"


def parse_sections(d):
    e_shoff = struct.unpack_from("<I", d, 0x20)[0]
    e_shentsize = struct.unpack_from("<H", d, 0x2E)[0]
    e_shnum = struct.unpack_from("<H", d, 0x30)[0]
    e_shstrndx = struct.unpack_from("<H", d, 0x32)[0]
    secs = []
    for i in range(e_shnum):
        o = e_shoff + i * e_shentsize
        vals = struct.unpack_from(SHDR, d, o)
        secs.append({
            "idx": i, "name_off": vals[0], "type": vals[1], "flags": vals[2],
            "addr": vals[3], "off": vals[4], "size": vals[5],
            "link": vals[6], "info": vals[7], "align": vals[8], "entsize": vals[9],
            "hdr_off": o,
        })
    st = secs[e_shstrndx]
    strtab = d[st["off"]:st["off"] + st["size"]]

    def nm(off):
        e = strtab.find(b"\x00", off)
        return strtab[off:e].decode("latin-1", "replace")

    for s in secs:
        s["name"] = nm(s["name_off"])
    return secs


def parse_segments(d):
    e_phoff = struct.unpack_from("<I", d, 0x1C)[0]
    e_phentsize = struct.unpack_from("<H", d, 0x2A)[0]
    e_phnum = struct.unpack_from("<H", d, 0x2C)[0]
    out = []
    for i in range(e_phnum):
        o = e_phoff + i * e_phentsize
        t, off, va, pa, fsz, msz, fl, al = struct.unpack_from("<8I", d, o)
        out.append(dict(type=t, off=off, vaddr=va, filesz=fsz, memsz=msz, flags=fl))
    return out


def find_path_string(d, old=None):
    """返回 (文件偏移, 虚拟地址, 字符串字节, 长度含 NUL)"""
    if old:
        b = old.encode("ascii") + b"\x00"
        i = d.find(b)
        if i < 0:
            raise SystemExit(f"未找到指定旧路径: {old}")
        return i, None, b
    # 自动：disc0: 开头 + .pgf 结尾
    i = 0
    while True:
        i = d.find(b"disc0:", i)
        if i < 0:
            break
        e = d.find(b"\x00", i)
        s = d[i:e]
        if s.endswith(b".pgf") or s.endswith(b".PGF"):
            return i, None, s + b"\x00"
        i += 1
    raise SystemExit("未找到 disc0:...*.pgf 路径串，请用 -old 指定")


def vaddr_of(secs, file_off):
    for s in secs:
        if s["off"] <= file_off < s["off"] + max(s["size"], 1) and s["addr"]:
            return s["addr"] + (file_off - s["off"])
    return None


def find_free_space(d, secs):
    """.rodata 之后到 .data 之前的连续零区，返回 (文件偏移, 可用长度, .rodata 节)"""
    ro = next((s for s in secs if s["name"] == ".rodata"), None)
    if ro is None:
        raise SystemExit("缺少 .rodata 节")
    start = ro["off"] + ro["size"]
    # 连续零区
    end = start
    while end < len(d) and d[end] == 0:
        end += 1
    return start, end - start, ro


def patch(prx_path, new_path, out_path, old_path=None):
    d = bytearray(Path(prx_path).read_bytes())
    if d[:4] != b"\x7fELF":
        raise SystemExit("不是 ELF（明文 PRX）")
    secs = parse_sections(d)
    segs = parse_segments(d)

    off_old, _, old_bytes = find_path_string(bytes(d), old_path)
    old_vaddr = vaddr_of(secs, off_old)
    if old_vaddr is None:
        raise SystemExit("无法确定旧路径的虚拟地址")
    print(f"旧路径: {old_bytes[:-1].decode()} @file 0x{off_old:x} vaddr 0x{old_vaddr:x}")

    new_bytes = new_path.encode("ascii") + b"\x00"
    free_off, free_len, ro = find_free_space(d, secs)
    if free_len < len(new_bytes):
        raise SystemExit(f"空隙不足：需要 {len(new_bytes)}，可用 {free_len}")
    d[free_off:free_off + len(new_bytes)] = new_bytes
    new_vaddr = ro["addr"] + (free_off - ro["off"])
    print(f"新路径: {new_path} @file 0x{free_off:x} vaddr 0x{new_vaddr:x}")

    # 改写引用旧地址的 addiu（指令编码 0x24xx xxxx，立即数在低 16 位）
    patched = 0
    text = next((s for s in secs if s["name"] == ".text"), None)
    if text is None:
        raise SystemExit("缺少 .text 节")
    for i in range(text["off"], text["off"] + text["size"], 4):
        if i + 4 > len(d):
            break
        ins = struct.unpack_from("<I", bytes(d), i)[0]
        if (ins >> 26) == 0x09 and (ins & 0xFFFF) == (old_vaddr & 0xFFFF):
            new_ins = (ins & 0xFFFF0000) | (new_vaddr & 0xFFFF)
            struct.pack_into("<I", d, i, new_ins)
            patched += 1
            print(f"  改写 addiu @vaddr 0x{text['addr'] + (i - text['off']):x}: 0x{ins:08x} -> 0x{new_ins:08x}")
    if patched == 0:
        print("  警告：未找到引用旧地址的 addiu 指令")

    # 扩展 .rodata 节大小以覆盖新串
    new_ro_size = (free_off + len(new_bytes)) - ro["off"]
    struct.pack_into("<I", bytes(0) and d or d, ro["hdr_off"] + 20, new_ro_size)
    print(f".rodata size 0x{ro['size']:x} -> 0x{new_ro_size:x}")

    # 校验段覆盖
    covered = any(s["vaddr"] <= new_vaddr and new_vaddr + len(new_bytes) <= s["vaddr"] + s["filesz"] for s in segs)
    print("新串位于已加载段内:", covered)

    Path(out_path).write_bytes(bytes(d))
    print(f"已写出 {out_path} ({len(d)} 字节)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("prx")
    ap.add_argument("-n", "--new", required=True, help="新路径，如 disc0:/PSP_GAME/USRDIR/foo.pgf")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("-old", "--old", default=None, help="旧路径（默认自动查找 .pgf 串）")
    a = ap.parse_args()
    patch(a.prx, a.new, a.out, a.old)


if __name__ == "__main__":
    main()