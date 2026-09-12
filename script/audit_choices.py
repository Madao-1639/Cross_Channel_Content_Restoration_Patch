"""选项（`0f`）正确性审计与修复。

原理
----
WS2 的 `14`（对话）与 `0f`（选项）共用**同一个字符串池**，池按文件出现顺序填充：
第 N 个 `14` 的 id 与第 N 组 `0f` 条目的 strid 都等于「到它为止的池位置」
（见 doc/lessons-learned.md §8）。lng 是**位置对应**的：引擎拿池位置去查 lng 第 N 条。

选项错位的典型成因：**lng 条数比池槽位数少**，于是某个位置之后整体提前一格 ——
表现为「第一个选项的译文显示在台词槽位、第二个选项显示成第一个、最后一个槽位取不到
（越界）」三联症状。

三项检查
--------
1. **池位连续性**：`14` 的 id 与 `0f` 的 strid 必须落在同一条连续池序列上；
2. **lng 条数 == 池槽位数**（`14` 条数 + `0f` 条目数）；
3. **选项槽位内容形态**：选项译文应当是短句，不应是带 `%K%P`/`%K` 的长台词。

修复（`--write`）
----------------
只修「整体提前/推后一格」这一类：逐条与源 CCS 比对定出偏移，在头或尾补一条把
整个 lng 推回去。**补的内容从源 CCS 取，不臆造**；源 CCS 覆盖不到的条目不补。

用法（项目根目录）：
    python script/audit_choices.py            # 只诊断
    python script/audit_choices.py --write    # 修复可判定的偏移
"""
import argparse
import io
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tool import arcbuild, lng, ws2, ws2disasm  # noqa: E402

if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

CCS_DIR = ROOT / '..' / 'cross-channel_chinese-localization_project' / 'Scripts' / '20150412'
RIO = ROOT / 'asset' / 'Rio.arc'
BACKUP = ROOT / 'asset' / 'Rio.arc.before_choice_fix'
_S = re.compile(r'\\d|%K%P|%K|%P|%N')
_Q = '“”「」『』\'"'
CTRL_ONLY = re.compile(r'^(?:%[A-Za-z])+$')
CTRL_TAIL = re.compile(r'(?:%[A-Za-z])+$')


def nz(t):
    return ''.join(c for c in _S.sub('', t) if c not in _Q and not c.isspace())


def split_ctrl(t):
    t = t.strip()
    if CTRL_ONLY.match(t):
        return '', t
    m = CTRL_TAIL.search(t)
    return (t[:m.start()], m.group(0)) if m else (t, '')


def pool_layout(raw):
    """返回 [(kind, payload)]：('14', id) / ('0f', [strid...])，按文件顺序。"""
    ins = ws2disasm.disassemble(ws2.decode(raw))
    out = []
    for i in ins:
        if i.opcode == 0x14:
            out.append(('14', i.fields['id'], i.fields['text']))
        elif i.opcode == 0x0f:
            out.append(('0f', [e['strid'] for e in i.fields['entries']],
                        [e['text'] for e in i.fields['entries']]))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--write', action='store_true')
    args = ap.parse_args()

    members = {n.decode('utf-16-le').upper(): d for n, d in arcbuild.read_raw(RIO)}
    problems, fixable = [], []
    n_opt = n_scripts = 0

    for name in sorted(k for k in members if k.endswith('.WS2')):
        stem = name[:-4]
        lk = stem + '.LNG'
        if lk not in members:
            continue
        layout = pool_layout(members[name])
        if not any(k == '0f' for k, _, _ in layout):
            continue
        n_scripts += 1
        L = lng.parse_lng(members[lk])

        # 1) 池位连续性
        pool, broken = 0, []
        for kind, a, b in layout:
            if kind == '14':
                if a != pool:
                    broken.append('14 id=%d 应为 %d' % (a, pool))
                pool += 1
            else:
                for n, s in enumerate(a):
                    if s != pool + n:
                        broken.append('0f strid=%d 应为 %d' % (s, pool + n))
                pool += len(a)
        # 2) 条数
        if len(L) != pool:
            broken.append('lng %d 条 != 池槽位 %d' % (len(L), pool))
        # 3) 选项槽位形态
        for kind, a, b in layout:
            if kind != '0f':
                continue
            for n, (s, txt) in enumerate(zip(a, b)):
                n_opt += 1
                if s >= len(L):
                    broken.append('选项%d 槽%d 越界（%r）' % (n + 1, s, txt))
                elif '%K' in L[s] or len(L[s]) > 26:
                    broken.append('选项%d 槽%d 像台词而非选项：%r -> %r'
                                  % (n + 1, s, txt[:14], L[s][:16]))
        if broken:
            problems.append((stem, len(L), pool, broken))

        # 修复：只在「lng 比池少一条、且整体偏移一格」时，从源 CCS 补一条
        if len(L) == pool - 1:
            src = stem[:-3] if stem.endswith('_EN') else stem
            p = CCS_DIR / (src + '.CCS')
            if p.exists():
                ccs = lng.parse_ccs(p)
                c0 = ccs.get(1)
                if c0 and nz(L[0]) == nz(lng.strip_speaker_wrap(ccs.get(2, ''))):
                    # lng[k] 拿的是源第 k+2 条 -> 头部缺一条
                    ctrl = split_ctrl(layout[0][2])[1]
                    head = split_ctrl(lng.strip_speaker_wrap(c0))[0] + ctrl
                    fixable.append((stem, head, L))

    print('含选项表的脚本 %d 个，选项条目 %d 个' % (n_scripts, n_opt))
    if not problems:
        print('✅ 三项检查全部通过')
    for stem, nl, pool, br in problems:
        print('⚠ %-18s lng %-4d 池槽位 %-4d  %s' % (stem, nl, pool, '; '.join(br[:3])))
    if fixable:
        print()
        print('可修（头部补一条）：%s' % [f[0] for f in fixable])

    if not args.write or not fixable:
        if not args.write:
            print('\n（未指定 --write，未写入）')
        return 0
    if BACKUP.exists():
        print('\n[备份] 已存在 %s' % BACKUP)
    else:
        arcbuild.write_arc(arcbuild.read_raw(RIO), BACKUP)
        print('\n[备份] asset/Rio.arc -> %s' % BACKUP)
    out = []
    for nb, data in arcbuild.read_raw(RIO):
        nm = nb.decode('utf-16-le').upper()
        hit = next((f for f in fixable if nm == f[0] + '.LNG'), None)
        if hit:
            data = lng.encode_lng([hit[1]] + hit[2])
        out.append((nb, data))
    arcbuild.write_arc(out, RIO)
    count, size, _ = arcbuild.verify(RIO)
    print('[写入] 修复 %d 个 lng；回读校验通过（%d 成员，%d 字节）'
          % (len(fixable), count, size))
    return 0


if __name__ == '__main__':
    sys.exit(main())
