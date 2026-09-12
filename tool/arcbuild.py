"""ARC reader/writer that preserves member names byte-exactly.

The earlier build scripts round-tripped members through the filesystem, which
mangled the Japanese UTF-16LE names in Graphic.arc, and wrote *absolute* data
offsets even though read_arc (and the engine) treat the offset field as
relative to 8 + table_size. Both bugs are avoided here by carrying raw name
bytes and computing relative offsets.
"""
import os
import struct
from pathlib import Path

HEADER = struct.Struct('<II')
ENTRY = struct.Struct('<II')


def read_raw(path):
    """Return [(name_bytes, data)] with names exactly as stored on disk."""
    blob = Path(path).read_bytes()
    count, table_size = HEADER.unpack_from(blob, 0)
    data_start = 8 + table_size
    out = []
    off = 8
    for _ in range(count):
        size, rel = ENTRY.unpack_from(blob, off)
        off += 8
        start = off
        while blob[off:off + 2] != b'\0\0':
            off += 2
        name_bytes = blob[start:off]
        off += 2
        begin = data_start + rel
        data = blob[begin:begin + size]
        if len(data) != size:
            raise ValueError('truncated member %r in %s' % (name_bytes, path))
        out.append((name_bytes, data))
    return out


def write_arc(members, output_path):
    """Write members as [(name_bytes, data)]; offsets are relative.

    先写同目录临时文件再 os.replace，而不是直接 open(target, 'wb')：
      * 本机对已存在的大归档（如 340 MB 的 Chip2.arc）做 O_TRUNC 会偶发
        `OSError: [Errno 22] Invalid argument`（读写、追加、替换都正常）；
      * 顺带获得原子性——写入中断不会把原归档截断成半个文件。
    """
    table_size = sum(8 + len(n) + 2 for n, _ in members)
    table = bytearray()
    rel = 0
    for name_bytes, data in members:
        table += ENTRY.pack(len(data), rel)
        table += name_bytes + b'\0\0'
        rel += len(data)
    if len(table) != table_size:
        raise AssertionError('table size mismatch')

    output_path = Path(output_path)
    tmp_path = output_path.with_name(output_path.name + '.tmp')
    try:
        with open(tmp_path, 'wb') as fh:
            fh.write(HEADER.pack(len(members), table_size))
            fh.write(table)
            for _, data in members:
                fh.write(data)
        os.replace(tmp_path, output_path)
    except BaseException:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
        raise
    return output_path.stat().st_size


def verify(path, expect_count=None):
    """Re-read an archive and assert every member lies inside the file."""
    blob = Path(path).read_bytes()
    count, table_size = HEADER.unpack_from(blob, 0)
    data_start = 8 + table_size
    off, seen, end = 8, 0, data_start
    for _ in range(count):
        size, rel = ENTRY.unpack_from(blob, off)
        off += 8
        while blob[off:off + 2] != b'\0\0':
            off += 2
        off += 2
        stop = data_start + rel + size
        if stop > len(blob):
            raise ValueError('member overruns file: %d > %d' % (stop, len(blob)))
        end = max(end, stop)
        seen += 1
    if seen != count:
        raise ValueError('entry count mismatch')
    if expect_count is not None and count != expect_count:
        raise ValueError('expected %d members, got %d' % (expect_count, count))

    # Check for spurious padding after table (before first data chunk)
    if end < len(blob) and blob[data_start:data_start + 2] == b'\0\0':
        raise ValueError(
            'spurious null padding at table end (position %d). '
            'Run normalize_arc_padding() to remove it' % data_start
        )

    return count, len(blob), end


def normalize_arc_padding(path):
    """Remove spurious null padding and re-write to canonical form."""
    members = read_raw(path)
    write_arc(members, path)
    return Path(path).stat().st_size


def read_old_arc(path):
    """原版（WillPlus）Rio.arc 的老式归档：类型表 + 21 字节定长条目。

    结构（见 CROSS_CHANNEL_Original/unpack_method.md）：
        [u32 类型数] n×[4B 类型名][u32 该类文件数][u32 条目区起始偏移]
        条目: 13B 文件名 + u32 大小 + u32 绝对偏移
    返回 [(name_bytes, data)]，名字形如 b'CCC0000'（不含扩展名，扩展名即类型名）。
    """
    blob = Path(path).read_bytes()
    n_types, = struct.unpack_from('<I', blob, 0)
    off = 4
    types = []
    for _ in range(n_types):
        tname = blob[off:off + 4].rstrip(b'\x00')
        cnt, start = struct.unpack_from('<II', blob, off + 4)
        types.append((tname, cnt, start))
        off += 12
    out = []
    for tname, cnt, start in types:
        p = start
        for _ in range(cnt):
            name = blob[p:p + 13].split(b'\x00')[0]
            size, foff = struct.unpack_from('<II', blob, p + 13)
            data = blob[foff:foff + size]
            if len(data) != size:
                raise ValueError('truncated member %r in %s' % (name, path))
            out.append((name + b'.' + tname, data))
            p += 21
    return out
