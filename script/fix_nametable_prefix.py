"""修正 Rio.arc 里 NameTable.txt 的前缀：`%LR` -> `%LC`。

背景
----
AdvHD 引擎有一套**名字替换表**机制：脚本里的 `%L?<名>` 标记会被引擎拿去查
`NameTable.txt`，命中则替换成表右列的内容。表的编码是 **UTF-16LE**，因此
**不受脚本内窄字节 CP932 解码的限制** —— 简体中文名可以经这条路显示。

铁证（ASF = A Sky Full of Stars，官方简中）：

| 游戏 | 脚本里的标记 | 表键前缀 | 结果 |
|---|---|---|---|
| ASF  | `%LF`（26,130 处） | `%LF`（140 行，零偏差） | 生效 |
| CC   | `%LC`（27,132 处） | `%LR`（73 行，零偏差） | **不生效** |

两个游戏的**表键集合都与脚本标记集合一一对应**（"只在表里"均为 0），
唯一差别是 CC 这张表的前缀被写成了 `%LR`，而脚本用的是 `%LC` ——
键对不上，引擎查不到，名字框就一直显示英文。

本脚本只做一件事：把表的行首与制表符后的 `%LR` 改成 `%LC`。
**不改 ws2 脚本、不动任何偏移、不涉及 `%LC` 的字节编码**。
（`%LR` / `%LC` / `%LF` 都在 CC 的 AdvHD.exe 命令标记表里，是引擎合法标记。）

用法（项目根目录）：
    python script/fix_nametable_prefix.py            # 只报告，不写入
    python script/fix_nametable_prefix.py --write    # 备份后写入，幂等
"""
import io
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tool import arcbuild, ws2  # noqa: E402

if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

ROOT = Path(__file__).parent.parent
RIO = ROOT / 'asset' / 'Rio.arc'
BACKUP = ROOT / 'asset' / 'Rio.arc.before_nametable_prefix'
TABLE_MEMBER = 'NameTable.txt'
TAB = b'\t\x00'
CRLF = b'\r\x00\n\x00'

# 只改「行首」或「制表符后」的 %LR，避免误伤正文里可能出现的字面量。
FIX = re.compile(rb'(?:' + re.escape(CRLF) + rb'|' + re.escape(TAB) +
                 rb'|\A)' + re.escape(b'%\x00L\x00') + rb'R\x00')
FIX_REPL = lambda m: m.group(0)[:-2] + b'C\x00'  # noqa: E731


def split_lines(blob):
    return [l for l in blob.split(CRLF) if l.strip()]


def prefix_stats(blob):
    """按 `%L?` 前缀统计表行数。"""
    stats = {}
    for line in split_lines(blob):
        head = line[:6]
        key = head.decode('utf-16le', 'replace') if len(head) == 6 else repr(head)
        stats[key] = stats.get(key, 0) + 1
    return stats


def table_keys(blob):
    keys = set()
    for line in split_lines(blob):
        text = line.decode('utf-16le')
        parts = text.split('\t')
        if parts and parts[0].startswith('%L'):
            keys.add(parts[0][3:])
    return keys


def script_names(rio):
    """脚本里 %LC 后的名字集合（含少量正文误匹配，仅用于方向性核对）。"""
    names = set()
    for name, data in rio.items():
        if not name.lower().endswith('.ws2'):
            continue
        for m in re.finditer(rb'%LC([^\x00]{0,30})', ws2.decode(data)):
            names.add(m.group(1).decode('cp932', 'replace'))
    return names


def main():
    write = '--write' in sys.argv
    members = arcbuild.read_raw(RIO)
    table = [d for n, d in members if n.decode('utf-16le') == TABLE_MEMBER]
    if not table:
        raise SystemExit('[中止] %s 里没有 %s' % (RIO, TABLE_MEMBER))
    table = table[0]

    before = prefix_stats(table)
    print('[现状] %s 表行前缀分布: %s' % (TABLE_MEMBER, before))

    fixed = FIX.sub(FIX_REPL, table)
    changed = len(FIX.findall(table))

    if changed == 0:
        print('[跳过] 表里已无 %LR 前缀（幂等），无需修改')
        return 0

    print('[计划] 将替换 %d 处 %%LR -> %%LC（共 %d 行）'
          % (changed, len(split_lines(table))))

    rio = {n.decode('utf-16le'): d for n, d in members}
    keys, names = table_keys(fixed), script_names(rio)
    only_table = sorted(keys - names)
    print('[核对] 改后表键 %d 个；表键中脚本里找不到的: %d 个 %s'
          % (len(keys), len(only_table), only_table[:5]))
    if only_table:
        raise SystemExit('[中止] 改后仍有表键无法对应脚本标记，需人工检查')

    if not write:
        print('\n[只报告] 未写入。确认无误后加 --write 落盘。')
        return 0

    if BACKUP.exists():
        print('[备份] 已存在 %s（保留原样）' % BACKUP)
    else:
        arcbuild.write_arc(members, BACKUP)
        print('[备份] %s -> %s' % (RIO, BACKUP))

    arcbuild.write_arc([(n, fixed if n.decode('utf-16le') == TABLE_MEMBER else d)
                        for n, d in members], RIO)

    back = dict((n.decode('utf-16le'), d) for n, d in arcbuild.read_raw(RIO))
    if back.get(TABLE_MEMBER) != fixed:
        raise SystemExit('[失败] 回读的 %s 与预期不符' % TABLE_MEMBER)
    if prefix_stats(back[TABLE_MEMBER]):
        print('[回读] %s 前缀分布: %s' % (TABLE_MEMBER, prefix_stats(back[TABLE_MEMBER])))
    count, size, _ = arcbuild.verify(RIO)
    print('[回读] %s %d 成员 %d 字节 verify OK' % (RIO, count, size))
    print('[完成] 已修正 %s 前缀；复跑应为 0 处。' % TABLE_MEMBER)
    return 0


if __name__ == '__main__':
    sys.exit(main())
