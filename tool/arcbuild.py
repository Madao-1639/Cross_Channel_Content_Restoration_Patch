"""ARC reader/writer that preserves member names byte-exactly.

The earlier build scripts round-tripped members through the filesystem, which
mangled the Japanese UTF-16LE names in Graphic.arc, and wrote *absolute* data
offsets even though read_arc (and the engine) treat the offset field as
relative to 8 + table_size. Both bugs are avoided here by carrying raw name
bytes and computing relative offsets.
"""
import hashlib
import os
import struct
import time
from pathlib import Path

HEADER = struct.Struct('<II')
ENTRY = struct.Struct('<II')


def same_file(a, b, chunk=1 << 20):
    """两个文件是否**逐字节相同**（先比大小，再分块 sha256；任一步不可读即视为不同）。"""
    try:
        if Path(a).stat().st_size != Path(b).stat().st_size:
            return False
    except OSError:
        return False

    def _digest(p):
        h = hashlib.sha256()
        with open(p, 'rb') as fh:
            for blk in iter(lambda: fh.read(chunk), b''):
                h.update(blk)
        return h.digest()
    return _digest(a) == _digest(b)



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


def write_arc(members, output_path, skip_if_identical=True):
    """Write members as [(name_bytes, data)]; offsets are relative.

    先写同目录临时文件再 os.replace，而不是直接 open(target, 'wb')：
      * 本机对已存在的大归档（如 340 MB 的 Chip2.arc）做 O_TRUNC 会偶发
        `OSError: [Errno 22] Invalid argument`（读写、追加、替换都正常）；
      * 顺带获得原子性——写入中断不会把原归档截断成半个文件。

    `skip_if_identical`（默认 **True**）：写出的内容与**现有文件逐字节相同**时**不替换**目标 ——
    目标文件的 mtime 保持不变（"本轮到底改没改"这条线索不被抹掉），也省下一次替换。
    这是项目幂等性要求的一部分；需要强制重写时显式传 `False`。
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
        if skip_if_identical and output_path.exists() and same_file(tmp_path, output_path):
            tmp_path.unlink()
            return output_path.stat().st_size
        # `os.replace` 在大归档上偶发瞬时的 `PermissionError`（WinError 5/32：杀软/索引/落盘
        # 尚未释放句柄）—— 重试几次，而不是让整条流水线在这里失败。
        for _attempt in range(8):
            try:
                os.replace(tmp_path, output_path)
                break
            except OSError as e:
                if _attempt == 7 or getattr(e, 'winerror', None) not in (5, 32):
                    raise
                time.sleep(0.5)
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

    # Check for spurious padding at the **end of the table** —— 即「名字扫描终点」与
    # 「数据起点」之间若有游离字节，就是非规范写法。
    # ⚠️ 2026-09-24 更正：旧检查看的是 `blob[data_start:data_start+2]`（**数据区头 2 字节**），
    # 位置查错了 —— 实测游离在 `off`（名字扫描终点，1,344,142）与 `data_start`（1,344,144）
    # **之间**，旧检查永远看不到，同类问题会复发（`write_arc` 少写/多写一个名字结尾就产生）。
    if off < data_start and blob[off:data_start] == b'\x00' * (data_start - off):
        raise ValueError(
            'spurious null padding at table end: 名字扫描到 %d，数据起点 %d（游离 %d 字节）。'
            '运行 normalize_arc_padding() 清理' % (off, data_start, data_start - off)
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

    ⚠️ **2026-09-21**：名字是 **ASCII**，要用 `.decode('shift_jis')` 或 `ascii` 解
    （项目脚本一直这么用，是对的）。**不要用 `utf-16-le`** —— 它会**静默**把偶数长度
    的名字解成乱码（`b'FYU000A0005A.OGG'` → `'奆さ〰ぁ〰䄵伮䝇'`），不抛异常，
    实测 8,704 条中有 1,045 条会因此"看不见"。
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
