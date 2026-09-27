"""从 `resource/speaker_map.json` 生成 `asset/Rio.arc` 里的 `NameTable.txt`。

为什么要有这一步
----------------
`NameTable.txt` 是引擎的**名字替换表**：脚本里的 `%LC<英文名>` 会拿去查它，命中则用
右列替换（UTF-16LE，所以简体中文能经这条路显示，不受脚本内窄字节 CP932 的限制）。
**它是玩家实际看到的名字框文本**，因而必须由唯一的映射表驱动，不能手工维护：

* 原先这张表是上游补丁的产物 + `script/fix_nametable_prefix.py` 就地改前缀；
  而 `script/build_patch.py` 的 bootstrap 分支从上游补丁铺基线，**不跑那个脚本** ——
  重建一次就会把 `%LR` 前缀和已判定的中文名一起丢掉。
* 现在表是唯一来源（`resource/README.md`），本脚本把表**每次构建都写回去**，
  顺带取代 `fix_nametable_prefix.py` 的前缀修正职责（生成时前缀天然是 `%LC`）。

行的取法
--------
行集合 = **脚本里实际出现的 `%LC` 键**（扫 `15` 指令的 prefix，与引擎取键的方式一致），
顺序沿用表里当前的顺序以便 diff 最小、新增键追加在末尾。表里那些「原版语料有、Steam 脚本
没有」的记录（`Masamune`/`Yutaka`/`Woman`/`Kiri/Taichi`）不写进来 —— 它们只服务转换器。

用法（项目根目录）
------------------
    python script/build_nametable.py            # 只报告差异，不写入
    python script/build_nametable.py --check    # 同上，差异时退出码 1（回归用）
    python script/build_nametable.py --write    # 备份后写回 asset/Rio.arc（幂等）
"""
import argparse
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tool import arcbuild, speaker, ws2, ws2disasm  # noqa: E402

if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

RIO = ROOT / 'asset' / 'Rio.arc'
BACKUP = ROOT / 'asset' / 'Rio.arc.before_nametable_build'
TABLE_MEMBER = 'NameTable.txt'
KEY_PREFIX = '%LC'
CRLF = b'\r\x00\n\x00'
SEP = b'\t\x00'


def lc_keys(members):
    """脚本里 `15` 指令带的名字前缀集合（`%LC` 开头的那些）。

    与 `script/audit/audit_lng_semantics.py` 的 `ws2_rows()` 是同一套取法（引擎也是这么取键的）。
    这里就地实现而不 import 它，是因为那个模块在 import 期会包装 `sys.stdout`，
    与本脚本自己的包装叠加会关掉底层 buffer（见 README.local.md「环境注意」）。
    """
    keys = set()
    for name, data in members:
        if not name.decode('utf-16le').lower().endswith('.ws2'):
            continue
        for ins in ws2disasm.disassemble(ws2.decode(data)):
            if ins.opcode != 0x15:
                continue
            pre = ins.fields.get('prefix')
            if isinstance(pre, bytes):
                pre = pre.decode('cp932', 'replace')
            pre = (pre or '').split('\x00')[0]
            if pre.startswith(KEY_PREFIX):
                keys.add(pre[len(KEY_PREFIX):])
    return keys


def current_order(table):
    """现有表的键顺序（用于最小 diff；新键追加在后面）。"""
    out = []
    for line in table.split(CRLF):
        if not line.strip():
            continue
        key = line.decode('utf-16le').split('\t')[0]
        if key.startswith(KEY_PREFIX):
            out.append(key[len(KEY_PREFIX):])
    return out


def build_rows(members):
    """返回 (新表字节, 行键列表, 缺失映射的键)。

    `NameTable.txt` **由本步生成**：`backup/`（Steam 原版）里没有它，从 backup 起底时
    首次运行会**新建**该成员（此前假定它已在档，会在 backup 基线上中止）。
    """
    table = dict((n.decode('utf-16le'), d) for n, d in members).get(TABLE_MEMBER, b'')
    keys = lc_keys(members)
    missing = speaker.missing_keys(keys)
    if missing:
        raise SystemExit('[中止] 这些 %LC 键在 resource/speaker_map.json 里查不到：%s\n'
                         '       名字框会回落成显示英文原名，需先补表。' % missing)
    zh = speaker.en_to_zh()
    order = [k for k in current_order(table) if k in keys]
    order += sorted(keys - set(order))
    body = CRLF.join(('%s%s\t%s%s' % (KEY_PREFIX, en, KEY_PREFIX, zh[en]))
                     .encode('utf-16-le') for en in order)
    return body + CRLF, order, []


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--write', action='store_true')
    ap.add_argument('--check', action='store_true')
    args = ap.parse_args(argv)

    members = arcbuild.read_raw(RIO)
    new, order, _ = build_rows(members)
    cur = dict((n.decode('utf-16le'), d) for n, d in members).get(TABLE_MEMBER, b'')

    if new == cur:
        print('[一致] %s 与 resource/speaker_map.json 一致（%d 行），无需修改'
              % (TABLE_MEMBER, len(order)))
        return 0

    old_rows = [l.decode('utf-16le') for l in cur.split(CRLF) if l.strip()]
    new_rows = new.decode('utf-16le').split('\r\n')[:-1]
    old_map = dict(r.split('\t') for r in old_rows)
    new_map = dict(r.split('\t') for r in new_rows)
    changed = [(k, old_map.get(k), v) for k, v in new_map.items() if old_map.get(k) != v]
    print('[差异] %s：%d 行 -> %d 行，%d 行内容变化'
          % (TABLE_MEMBER, len(old_rows), len(new_rows), len(changed)))
    for k, a, b in changed[:20]:
        print('    %-22s %s -> %s' % (k, a, b))
    if len(changed) > 20:
        print('    …（共 %d 行）' % len(changed))

    if args.check or not args.write:
        print('\n[只报告] 未写入。加 --write 落盘。')
        return 1 if args.check else 0

    if not (BACKUP.exists() and arcbuild.same_file(BACKUP, RIO)):
        arcbuild.write_arc(members, BACKUP)
        print('[备份] %s -> %s' % (RIO, BACKUP))
    out = [(n, new if n.decode('utf-16le') == TABLE_MEMBER else d) for n, d in members]
    if TABLE_MEMBER not in {n.decode('utf-16le') for n, _ in members}:
        out.append((TABLE_MEMBER.encode('utf-16le'), new))    # backup 基线上首次新建
    arcbuild.write_arc(out, RIO)

    back = dict((n.decode('utf-16le'), d) for n, d in arcbuild.read_raw(RIO))
    if back.get(TABLE_MEMBER) != new:
        raise SystemExit('[失败] 回读的 %s 与预期不符' % TABLE_MEMBER)
    count, size, _ = arcbuild.verify(RIO)
    print('[回读] %s %d 成员 %d 字节 verify OK；%s 已写入 %d 行'
          % (RIO, count, size, TABLE_MEMBER, len(new_rows)))
    print('[完成] 复跑应为 0 处。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
