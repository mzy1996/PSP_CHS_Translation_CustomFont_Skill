#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""反汇编 PSP PRX 的指定代码段，并标注重定位。

读取 ELF32 little-endian 的节表与程序头，按 --section（默认 .text）取出
代码字节交给 capstone（CS_ARCH_MIPS + CS_MODE_MIPS32_LE）反汇编；
若存在对应的 .rel.<section>（如 .rel.text），在落在重定位地址的行尾追加
`; REL <r_info hex>`。

用法：
  python scripts/dis_libfont.py --prx libfont.prx --out libfont.dis
  python scripts/dis_libfont.py --prx libfont.prx --out libfont.dis --section .text

输出：
  - --out 指定的反汇编文本（每行 `0x地址: 助记符 操作数`）
  - stdout 打印 ELF/节信息、重定位数量与前若干行
"""

import argparse
import struct
import sys
from pathlib import Path

try:
    from capstone import Cs, CS_ARCH_MIPS, CS_MODE_MIPS32, CS_MODE_LITTLE_ENDIAN
except ImportError:  # pragma: no cover
    Cs = None

SHDR = struct.Struct('<10I')
PHDR = struct.Struct('<8I')
REL = struct.Struct('<II')
RELA = struct.Struct('<III')


class ElfError(Exception):
    """ELF/PRX 结构不符合预期。"""


def _u16(buf, off):
    return struct.unpack_from('<H', buf, off)[0]


def _u32(buf, off):
    return struct.unpack_from('<I', buf, off)[0]


def _cstr(strtab, off):
    end = strtab.find(b'\x00', off)
    if end < 0:
        end = len(strtab)
    return strtab[off:end].decode('latin-1', 'replace')


def parse_elf(data):
    """解析 ELF32 LE 的节表与程序头，返回 (sections, segments)。"""
    if len(data) < 52 or data[:4] != b'\x7fELF':
        raise ElfError('不是 ELF 文件')
    if data[4] != 1:
        raise ElfError('不是 ELF32（EI_CLASS != 1）')
    if data[5] != 1:
        raise ElfError('不是 little-endian ELF（EI_DATA != 1）')

    e_shoff = _u32(data, 0x20)
    e_shentsize = _u16(data, 0x2E)
    e_shnum = _u16(data, 0x30)
    e_shstrndx = _u16(data, 0x32)
    if e_shentsize != SHDR.size:
        raise ElfError(f'节头大小异常: {e_shentsize}')
    if e_shoff == 0 or e_shnum == 0:
        raise ElfError('缺少节表（可能是加密/压缩 PRX）')
    if e_shoff + e_shnum * e_shentsize > len(data):
        raise ElfError('节表越界')

    sections = []
    for i in range(e_shnum):
        vals = SHDR.unpack_from(data, e_shoff + i * e_shentsize)
        sections.append({
            'idx': i,
            'name_off': vals[0],
            'type': vals[1],
            'flags': vals[2],
            'addr': vals[3],
            'off': vals[4],
            'size': vals[5],
            'link': vals[6],
            'info': vals[7],
            'align': vals[8],
            'entsize': vals[9],
        })

    if e_shstrndx >= len(sections):
        raise ElfError('e_shstrndx 越界')
    st = sections[e_shstrndx]
    strtab = data[st['off']:st['off'] + st['size']]
    for sec in sections:
        sec['name'] = _cstr(strtab, sec['name_off'])

    e_phoff = _u32(data, 0x1C)
    e_phentsize = _u16(data, 0x2A)
    e_phnum = _u16(data, 0x2C)
    segments = []
    if e_phoff and e_phnum and e_phentsize == PHDR.size:
        if e_phoff + e_phnum * e_phentsize > len(data):
            raise ElfError('程序头表越界')
        for i in range(e_phnum):
            t, off, va, pa, fsz, msz, fl, al = PHDR.unpack_from(
                data, e_phoff + i * e_phentsize)
            segments.append({
                'type': t, 'off': off, 'vaddr': va, 'filesz': fsz,
                'memsz': msz, 'flags': fl,
            })

    return sections, segments


def load_relocations(data, sections, section_name):
    """读取 .rel.<section>（8 字节项）或 .rela.<section>（12 字节项）。

    返回 {r_offset: r_info}；没有重定位节时返回空 dict。
    """
    rel_name = '.rel' + section_name
    rela_name = '.rela' + section_name
    rel = next((s for s in sections if s['name'] == rel_name), None)
    if rel is not None:
        count = rel['size'] // REL.size
        out = {}
        for i in range(count):
            r_offset, r_info = REL.unpack_from(data, rel['off'] + i * REL.size)
            out[r_offset] = r_info
        return out, rel['name'], count

    rela = next((s for s in sections if s['name'] == rela_name), None)
    if rela is not None:
        count = rela['size'] // RELA.size
        out = {}
        for i in range(count):
            r_offset, r_info, _addend = RELA.unpack_from(
                data, rela['off'] + i * RELA.size)
            out[r_offset] = r_info
        return out, rela['name'], count

    return {}, None, 0


def disassemble(data, section, relocations, preview=20):
    """反汇编一个节，返回文本行列表。"""
    code = data[section['off']:section['off'] + section['size']]
    if len(code) != section['size']:
        raise ElfError(f"节 {section['name']} 读取越界")
    base = section['addr']

    md = Cs(CS_ARCH_MIPS, CS_MODE_MIPS32 + CS_MODE_LITTLE_ENDIAN)
    lines = []
    for ins in md.disasm(code, base):
        line = f"0x{ins.address:08x}: {ins.mnemonic:<8} {ins.op_str}"
        if ins.address in relocations:
            line += f"        ; REL {relocations[ins.address]:#x}"
        lines.append(line)
    return lines


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog='dis_libfont.py',
        description='反汇编 PSP PRX 的指定代码段并标注 .rel 重定位。',
        epilog='例: python scripts/dis_libfont.py --prx libfont.prx '
               '--out libfont.dis --section .text',
    )
    parser.add_argument('--prx', required=True, help='明文 ELF/PRX 文件')
    parser.add_argument('--out', required=True, help='反汇编输出文本路径')
    parser.add_argument('--section', default='.text',
                        help='要反汇编的节名，默认 .text；可省略前导点')
    args = parser.parse_args(argv)

    if Cs is None:
        print('error: 需要 capstone（pip install capstone）', file=sys.stderr)
        return 2

    prx_path = Path(args.prx)
    if not prx_path.is_file():
        print(f'error: 文件不存在: {prx_path}', file=sys.stderr)
        return 2

    data = prx_path.read_bytes()
    try:
        sections, segments = parse_elf(data)
    except ElfError as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 2

    section_name = args.section
    if not section_name.startswith('.'):
        section_name = '.' + section_name
    section = next((s for s in sections if s['name'] == section_name), None)
    if section is None:
        available = ', '.join(s['name'] for s in sections
                               if s['name'] and s['type'] != 8)
        print(f'error: 找不到节 {section_name}；可用节: {available}',
              file=sys.stderr)
        return 2
    if section['size'] == 0:
        print(f'error: 节 {section_name} 长度为 0', file=sys.stderr)
        return 2

    try:
        relocations, rel_name, rel_count = load_relocations(
            data, sections, section_name)
        lines = disassemble(data, section, relocations)
    except ElfError as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 2
    if not lines:
        print(f'error: 节 {section_name} 反汇编结果为空', file=sys.stderr)
        return 2

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text('\n'.join(lines) + '\n', encoding='utf-8')

    print(f'prx: {prx_path} ({len(data)} bytes)')
    print(f'ELF32 LE: sections={len(sections)} segments={len(segments)}')
    for seg in segments:
        print(f'  PT type=0x{seg["type"]:x} off=0x{seg["off"]:x} '
              f'vaddr=0x{seg["vaddr"]:x} filesz=0x{seg["filesz"]:x}')
    print(f'section {section_name}: off=0x{section["off"]:x} '
          f'addr=0x{section["addr"]:x} size=0x{section["size"]:x} '
          f'({section["size"]} bytes)')
    if rel_name:
        print(f'relocations: {rel_name} {rel_count} entries '
              f'(annotated on {len(relocations)} addresses)')
    else:
        print(f'relocations: 无 .rel{section_name}，不标注')
    print(f'disassembled: {len(lines)} instructions -> {out_path}')
    print(f'--- first {min(20, len(lines))} lines ---')
    for line in lines[:20]:
        print(line)
    return 0


if __name__ == '__main__':
    sys.exit(main())