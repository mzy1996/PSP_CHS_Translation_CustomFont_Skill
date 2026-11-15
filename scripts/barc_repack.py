#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BARC 成员级原地替换（PSP ISO9660 镜像内）。

本脚本只做「成员级原地替换」：
  1. 从 ISO9660 目录树定位 --arc-rel 对应的 BARC 容器（LBA*2048 的 extent）；
  2. 用 zlib raw deflate（windowBits=-15）把 --new-plain 重新压缩；
  3. 新压缩流不长于原流时，就地覆盖该成员的负载区，并只更新 BARC 尾部表
     中该成员的 data_size。新流变短时，旧流尾部残字节保持原样、不补 0，
     有效长度由 data_size 界定——除「目标负载区前 N 字节」与「data_size 的
     4 字节」之外，镜像里不允许出现任何字节变化。

硬约束：容器总长（BARC 头 total 字段）、BARC 头本身、其它成员的名字块偏移
与负载偏移必须完全不变。禁止重建整个容器（重排成员、重算 total 在实测中
会导致游戏启动即退出）。

BARC 结构（实测）：
  头  '<4sIIIIIII'：'BARC'、version、head_len、total、count、rec_len、
      tail_off、zero
  成员 = u16(名长+6) + 名字 + 4 字节 0 + raw deflate 数据
  尾部表每项 '<3I' = tag、data_size、name_blk_offset
  data_size == 0 的成员是未压缩明文，长度由下一成员的 name_blk_offset
  或 tail_off 兜底。

用法：
  python scripts/barc_repack.py --iso game.iso \\
      --arc-rel PSP_GAME/USRDIR/CmnPack/CMN.arc \\
      --member Indicator/IndInfo.prm --new-plain new.bin \\
      [--out-iso patched.iso]

不传 --out-iso 时就地写回 --iso（失败会自动回滚已写入的成员区）。
"""

import argparse
import shutil
import struct
import sys
import zlib
from pathlib import Path

SECTOR = 2048
BARC_HEADER = struct.Struct('<4sIIIIIII')
TAIL_ENTRY = struct.Struct('<3I')


class BarcError(Exception):
    """BARC 或 ISO 结构不符合预期。"""


def _u16(buf, off):
    return struct.unpack_from('<H', buf, off)[0]


def _u32(buf, off):
    return struct.unpack_from('<I', buf, off)[0]


# ---------------------------------------------------------------- ISO9660

def find_iso_file(iso_path, rel_path):
    """按 ISO9660 目录树精确查找文件，返回 (实际相对路径, 字节偏移, 长度)。

    名字大小写不敏感比较（PSP 镜像常见 Lang/CmnPack 这类大小写混合目录名），
    但仍要求完整路径逐段匹配，不做模糊匹配。找不到或有歧义时抛 BarcError。
    """
    target = rel_path.replace('\\', '/').strip('/').lower()
    if not target:
        raise BarcError('--arc-rel 不能为空')

    iso_size = iso_path.stat().st_size
    matches = []

    with iso_path.open('rb') as stream:
        stream.seek(16 * SECTOR)
        pvd = stream.read(SECTOR)
        if len(pvd) != SECTOR or pvd[0:1] != b'\x01' or pvd[1:6] != b'CD001':
            raise BarcError('不是 ISO9660 镜像（扇区 16 未找到 PVD）')
        root_len = pvd[156]
        root = pvd[156:156 + root_len]

        stack = [('', root)]
        while stack:
            parent, record = stack.pop()
            lba = _u32(record, 2)
            size = _u32(record, 10)
            if size == 0:
                continue
            stream.seek(lba * SECTOR)
            directory = stream.read(size)
            if len(directory) != size:
                raise BarcError(f'目录读取越界: {parent or "/"}')

            pos = 0
            while pos < len(directory):
                length = directory[pos]
                if length == 0:
                    # 目录记录不跨扇区，0 表示跳到下一个扇区
                    pos = (pos // SECTOR + 1) * SECTOR
                    continue
                item = directory[pos:pos + length]
                pos += length
                if len(item) < 34:
                    continue
                nlen = item[32]
                raw_name = item[33:33 + nlen]
                if raw_name in (b'\x00', b'\x01'):
                    continue
                name = raw_name.decode('ascii', 'replace').split(';', 1)[0]
                relative = f'{parent}/{name}' if parent else name
                if item[25] & 0x02:
                    stack.append((relative, item))
                    continue
                if relative.lower() == target:
                    file_lba = _u32(item, 2)
                    file_len = _u32(item, 10)
                    offset = file_lba * SECTOR
                    if offset + file_len > iso_size:
                        raise BarcError(f'文件超出镜像边界: {relative}')
                    matches.append((relative, offset, file_len))

    if not matches:
        raise BarcError(f'镜像内找不到: {rel_path}')
    if len(matches) > 1:
        listing = '\n  '.join(m[0] for m in matches)
        raise BarcError(f'路径有歧义（大小写不敏感匹配到多个）:\n  {listing}')
    return matches[0]


# ---------------------------------------------------------------- BARC

def parse_barc(data):
    """解析 BARC 容器字节，返回头字段与成员列表（负载为原始压缩流）。"""
    if len(data) < BARC_HEADER.size:
        raise BarcError('BARC 数据短于头部长度')
    magic, version, head_len, total, count, rec_len, tail_off, zero = \
        BARC_HEADER.unpack_from(data, 0)
    if magic != b'BARC':
        raise BarcError(f'不是 BARC 容器（magic={magic!r}）')
    if rec_len < TAIL_ENTRY.size:
        raise BarcError(f'尾部记录长度异常: {rec_len}')
    if total > len(data):
        raise BarcError(f'BARC total={total} 超出文件长度 {len(data)}')
    if tail_off + count * rec_len > len(data):
        raise BarcError('BARC 尾部表越界')

    records = [TAIL_ENTRY.unpack_from(data, tail_off + i * rec_len)
               for i in range(count)]
    members = []
    for index, (tag, data_size, name_off) in enumerate(records):
        if name_off + 2 > len(data):
            raise BarcError(f'成员 {index} 名字块越界')
        name_len = _u16(data, name_off)
        data_off = name_off + name_len
        if data_off > len(data):
            raise BarcError(f'成员 {index} 名字块长度越界')
        raw_name = data[name_off + 2:data_off]
        name = raw_name.split(b'\x00', 1)[0].decode('ascii', 'replace')
        # data_size == 0：未压缩成员，长度取下一成员名字块起点或尾部表起点
        limit = records[index + 1][2] if index + 1 < count else tail_off
        length = data_size if data_size > 0 else max(0, limit - data_off)
        if data_off + length > len(data):
            raise BarcError(f'成员 {index} 负载越界: {name}')
        members.append({
            'index': index,
            'tag': tag,
            'data_size': data_size,
            'length': length,
            'name_off': name_off,
            'name_len': name_len,
            'name': name,
            'data_off': data_off,
        })

    return {
        'version': version,
        'head_len': head_len,
        'total': total,
        'count': count,
        'rec_len': rec_len,
        'tail_off': tail_off,
        'members': members,
    }


def norm_member(name):
    return name.replace('\\', '/').strip('/').lower()


def find_member(arc, member):
    """按完整成员名查找；只给 basename 时要求全局唯一。"""
    target = norm_member(member)
    exact = [m for m in arc['members'] if norm_member(m['name']) == target]
    if exact:
        return exact[0]
    base = [m for m in arc['members']
            if norm_member(m['name']).rsplit('/', 1)[-1] == target]
    if len(base) == 1:
        return base[0]
    if len(base) > 1:
        listing = '\n  '.join(m['name'] for m in base)
        raise BarcError(f'成员名有歧义，请给出完整路径:\n  {listing}')
    raise BarcError(f'容器内找不到成员: {member}')


def inflate_raw(payload):
    """解 raw deflate；不是完整 deflate 流时返回 None。"""
    if not payload:
        return None
    try:
        obj = zlib.decompressobj(-15)
        out = obj.decompress(payload)
        out += obj.flush()
    except zlib.error:
        return None
    if not obj.eof:
        return None
    return out


def deflate_raw(data):
    obj = zlib.compressobj(9, zlib.DEFLATED, -15)
    return obj.compress(data) + obj.flush()


# ---------------------------------------------------------------- 替换计划

def build_patch(container, member_arg, new_plain):
    """计算成员级替换所需的最小写入集合，不改动任何偏移。"""
    arc = parse_barc(container)
    member = find_member(arc, member_arg)

    old_payload = container[member['data_off']:
                              member['data_off'] + member['length']]

    if member['data_size'] > 0:
        old_plain = inflate_raw(old_payload)
        if old_plain is None:
            raise BarcError(
                f'成员 {member["name"]} 标称已压缩（data_size={member["data_size"]}），'
                '但负载不是完整的 raw deflate 流')
        new_payload = deflate_raw(new_plain)
        if len(new_payload) > member['length']:
            raise BarcError(
                f'重新压缩后变长: {len(new_payload)} > {member["length"]}，'
                '无法在不重建容器的前提下替换')
        new_data_size = len(new_payload)
        kind = 'deflate'
        old_plain_len = len(old_plain)
    else:
        # 未压缩成员：保持 data_size=0，并用 0 补齐到原有长度
        if len(new_plain) > member['length']:
            raise BarcError(
                f'新明文长于原成员: {len(new_plain)} > {member["length"]}，'
                '无法在不重建容器的前提下替换')
        new_payload = new_plain
        new_data_size = 0
        kind = 'plain'
        old_plain_len = member['length']

    slack = member['length'] - len(new_payload)
    size_field_off = (arc['tail_off'] + member['index'] * arc['rec_len']
                      + TAIL_ENTRY.size - 8)
    return {
        'arc': arc,
        'member': member,
        'kind': kind,
        'old_plain_len': old_plain_len,
        'new_payload': new_payload,
        'new_data_size': new_data_size,
        'slack': slack,
        'size_field_off': size_field_off,
        'old_payload': old_payload,
        'old_data_size': member['data_size'],
    }


def apply_patch(iso_path, extent_off, plan):
    """把负载与新 data_size 写入 ISO 的成员区。

    只写「新负载」与「data_size 的 4 字节」，余量里的旧流残字节保持原样：
    有效长度由 data_size 界定，不做补 0，也不触碰同一容器内的其它字节。
    """
    member = plan['member']
    with iso_path.open('r+b') as stream:
        stream.seek(extent_off + member['data_off'])
        stream.write(plan['new_payload'])
        if plan['new_data_size'] != plan['old_data_size']:
            stream.seek(extent_off + plan['size_field_off'])
            stream.write(struct.pack('<I', plan['new_data_size']))


def rollback_patch(iso_path, extent_off, plan):
    """就地写回失败时恢复成员负载与 data_size。"""
    member = plan['member']
    with iso_path.open('r+b') as stream:
        stream.seek(extent_off + member['data_off'])
        stream.write(plan['old_payload'])
        stream.seek(extent_off + plan['size_field_off'])
        stream.write(struct.pack('<I', plan['old_data_size']))


def verify_patch(iso_path, extent_off, extent_len, plan, new_plain):
    """重新读回输出镜像，逐字节比对目标成员并核对未移动任何偏移。"""
    member = plan['member']
    with iso_path.open('rb') as stream:
        stream.seek(extent_off)
        data = stream.read(extent_len)
    if len(data) != extent_len:
        raise BarcError('输出镜像读回短读')

    arc2 = parse_barc(data)
    member2 = find_member(arc2, member['name'])

    if arc2['total'] != plan['arc']['total']:
        raise BarcError(f'容器总长被改变: {arc2["total"]} != {plan["arc"]["total"]}')
    if arc2['tail_off'] != plan['arc']['tail_off']:
        raise BarcError('BARC 尾部表偏移被改变')
    for before, after in zip(plan['arc']['members'], arc2['members']):
        if before['name_off'] != after['name_off']:
            raise BarcError(f'成员偏移被改变: {before["name"]}')

    payload = data[member2['data_off']:
                   member2['data_off'] + member2['length']]
    if plan['kind'] == 'deflate':
        got = inflate_raw(payload)
        if got is None:
            raise BarcError('替换后的负载无法解压')
    else:
        got = payload[:len(new_plain)]

    if got != new_plain:
        raise BarcError(
            f'回读内容与 --new-plain 不一致: got {len(got)} bytes, '
            f'expected {len(new_plain)} bytes')
    return len(got)


# ---------------------------------------------------------------- CLI

def main(argv=None):
    parser = argparse.ArgumentParser(
        prog='barc_repack.py',
        description='BARC 成员级原地替换：只换目标成员压缩流并更新 data_size，'
                    '容器总长与其它成员偏移保持不变。',
        epilog='约束：禁止重建整个容器。重新压缩后若长于原流即报错退出，'
               '不会重排成员或改动 BARC total。未指定 --out-iso 时就地写回。',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('--iso', required=True,
                        help='PSP ISO9660 镜像路径')
    parser.add_argument('--arc-rel', required=True,
                        help='BARC 容器在镜像内的相对路径，如 '
                             'PSP_GAME/USRDIR/CmnPack/CMN.arc（大小写不敏感）')
    parser.add_argument('--member', required=True,
                        help='目标成员名（完整相对名，或唯一 basename）')
    parser.add_argument('--new-plain', required=True,
                        help='替换后的成员明文文件')
    parser.add_argument('--out-iso', default=None,
                        help='输出镜像路径；省略时原地写回 --iso')
    args = parser.parse_args(argv)

    iso_path = Path(args.iso).resolve()
    if not iso_path.is_file():
        print(f'error: ISO 不存在: {iso_path}', file=sys.stderr)
        return 2
    plain_path = Path(args.new_plain)
    if not plain_path.is_file():
        print(f'error: --new-plain 不存在: {plain_path}', file=sys.stderr)
        return 2
    new_plain = plain_path.read_bytes()
    if not new_plain:
        print('error: --new-plain 是空文件', file=sys.stderr)
        return 2

    try:
        arc_rel, extent_off, extent_len = find_iso_file(iso_path, args.arc_rel)
        with iso_path.open('rb') as stream:
            stream.seek(extent_off)
            container = stream.read(extent_len)
        if len(container) != extent_len:
            raise BarcError('BARC 容器读回短读')

        print(f'iso: {iso_path} ({iso_path.stat().st_size} bytes)')
        print(f'arc: {arc_rel} @ offset {extent_off} size {extent_len}')

        plan = build_patch(container, args.member, new_plain)
        arc = plan['arc']
        member = plan['member']
        print(f'BARC: version=0x{arc["version"]:x} members={arc["count"]} '
              f'total={arc["total"]} tail_off={arc["tail_off"]}')
        print(f'member: [{member["index"]}] {member["name"]} '
              f'kind={plan["kind"]} data_size={member["data_size"]} '
              f'plain={plan["old_plain_len"]}')
        print(f'recompress: {len(plan["new_payload"])} <= {member["length"]} '
              f'(slack {plan["slack"]} bytes)')

        out_path = Path(args.out_iso).resolve() if args.out_iso else iso_path
        in_place = out_path == iso_path
        if not in_place:
            print(f'copy: {iso_path} -> {out_path}')
            out_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(iso_path, out_path)

        apply_patch(out_path, extent_off, plan)
        print(f'patch: data_size {plan["old_data_size"]} -> '
              f'{plan["new_data_size"]}')

        try:
            n = verify_patch(out_path, extent_off, extent_len, plan, new_plain)
        except BarcError as exc:
            print(f'VERIFY FAILED: {exc}', file=sys.stderr)
            if in_place:
                rollback_patch(iso_path, extent_off, plan)
                print('已回滚目标成员区', file=sys.stderr)
            return 1

        print(f'verify: container total unchanged, {arc["count"]} member '
              'offsets unchanged')
        print(f'OK bytes={n}')
        if not in_place:
            print(f'wrote: {out_path}')
        return 0

    except BarcError as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())