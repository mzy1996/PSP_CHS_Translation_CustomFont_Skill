# -*- coding: utf-8 -*-
"""向 PSP ISO 的 USRDIR 目录新增文件（不重建镜像）。

做法：
1) 文件数据追加到镜像末尾（2048 对齐）
2) 在 USRDIR 目录记录区空闲处写入新目录项
3) 更新 PVD 的 volume space size
"""
import os
import shutil
import struct
import sys

SECTOR = 2048


def find_dir(iso, path_parts):
    """按路径返回 (dirname_lba, dir_size)。"""
    with open(iso, "rb") as f:
        f.seek(16 * SECTOR)
        pvd = f.read(SECTOR)
        root = pvd[156:156 + pvd[156]]
        lba = struct.unpack_from("<I", root, 2)[0]
        size = struct.unpack_from("<I", root, 10)[0]
        for part in path_parts:
            f.seek(lba * SECTOR)
            data = f.read(size)
            pos = 0
            found = None
            while pos < len(data):
                ln = data[pos]
                if ln == 0:
                    pos = (pos // SECTOR + 1) * SECTOR
                    continue
                rec = data[pos:pos + ln]
                nlen = rec[32]
                name = rec[33:33 + nlen]
                if name == part.encode():
                    found = (struct.unpack_from("<I", rec, 2)[0],
                             struct.unpack_from("<I", rec, 10)[0], pos, ln)
                    break
                pos += ln
            if not found:
                raise SystemExit(f"目录不存在: {part} in {path_parts}")
            lba, size = found[0], found[1]
    return lba, size


def dir_free_slot(iso, dir_lba, dir_size):
    """返回目录记录区内可写新记录的偏移与剩余空间。"""
    with open(iso, "rb") as f:
        f.seek(dir_lba * SECTOR)
        data = f.read(dir_size)
    pos = 0
    while pos < len(data):
        ln = data[pos]
        if ln == 0:
            # pos 之后到下一个扇区边界为空洞
            nxt = (pos // SECTOR + 1) * SECTOR
            return pos, nxt - pos
        pos += ln
    return None, 0


def make_record(name: bytes, lba: int, size: int, flags: int, date: bytes) -> bytes:
    nlen = len(name)
    length = 33 + nlen
    if length % 2:
        length += 1
    rec = bytearray(length)
    rec[0] = length
    struct.pack_into("<I", rec, 2, lba)
    struct.pack_into(">I", rec, 6, lba)
    struct.pack_into("<I", rec, 10, size)
    struct.pack_into(">I", rec, 14, size)
    rec[18:25] = date
    rec[25] = flags
    rec[26] = 0
    rec[27] = 0
    struct.pack_into("<H", rec, 28, 1)
    struct.pack_into(">H", rec, 30, 1)
    rec[32] = nlen
    rec[33:33 + nlen] = name
    return bytes(rec)


def add_file(iso, src_path, iso_name, dir_parts):
    dir_lba, dir_size = find_dir(iso, dir_parts)
    slot, free = dir_free_slot(iso, dir_lba, dir_size)
    if slot is None:
        raise SystemExit("目录区已满，无法新增")
    data = open(src_path, "rb").read()
    name = iso_name.encode()
    rec = make_record(name, 0, len(data), 0, bytes.fromhex("6f05120e2b3324"))
    if len(rec) > free:
        raise SystemExit(f"空间不足: 需要 {len(rec)}, 剩余 {free}")
    iso_size = os.path.getsize(iso)
    total_sectors = iso_size // SECTOR
    lba = total_sectors
    # 写入目录项（LBA 待定，先算好）
    rec = bytearray(rec)
    struct.pack_into("<I", rec, 2, lba)
    struct.pack_into(">I", rec, 6, lba)
    with open(iso, "r+b") as f:
        # 追加数据
        f.seek(lba * SECTOR)
        f.write(data)
        pad = (-len(data)) % SECTOR
        if pad:
            f.write(b"\x00" * pad)
        # 写入目录项
        f.seek(dir_lba * SECTOR + slot)
        f.write(bytes(rec))
        # 更新 PVD volume space size
        new_sectors = lba + (len(data) + pad) // SECTOR
        f.seek(16 * SECTOR + 80)
        f.write(struct.pack("<I", new_sectors))
        f.write(struct.pack(">I", new_sectors))
    print(f"added {iso_name}: lba={lba} size={len(data)} "
          f"dir_slot={slot} rec_len={len(rec)} volume_sectors={new_sectors}")
    return lba, len(data)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(
        description="向 PSP ISO9660 镜像的某个目录新增文件（不重建镜像）")
    ap.add_argument("src_iso", help="基准镜像路径")
    ap.add_argument("dst_iso", help="输出镜像路径（不存在时自动从 src 复制）")
    ap.add_argument("pairs", nargs="+", metavar="本地文件=ISO内文件名",
                    help="要新增的文件，可给多个")
    ap.add_argument("--dir", default="PSP_GAME/USRDIR",
                    help="目标目录（以 / 分隔），默认 PSP_GAME/USRDIR")
    _a = ap.parse_args()
    src_iso, dst_iso = _a.src_iso, _a.dst_iso
    dir_parts = [x for x in _a.dir.strip("/").split("/") if x]
    pairs = []
    for arg in _a.pairs:
        local, _, isoname = arg.partition("=")
        pairs.append((local, isoname))
    if not os.path.exists(dst_iso):
        print("copying base iso...")
        shutil.copyfile(src_iso, dst_iso)
    # 校验 USRDIR 空闲
    lba, size = find_dir(dst_iso, dir_parts)
    slot, free = dir_free_slot(dst_iso, lba, size)
    print(f"{'/'.join(dir_parts)} lba={lba} size={size} free_slot={slot} free_bytes={free}")
    for local, isoname in pairs:
        add_file(dst_iso, local, isoname, dir_parts)
    print("final iso size:", os.path.getsize(dst_iso))