"""lng 对齐修复：把 lng 按「`14` 出现序」的模型重新对齐到源 CCS。

模型（doc/localization.md「位置对应」）：lng 第 N 条 ↔ 脚本第 N 个 `14`；
* 控制行（正文就是 `%N`/`%P`）-> lng 该条也应是同名控制符；
* 其余 -> lng 该条应是源 CCS 第 n 条的译文（n = 到这句为止的非控制序，1 基）。

错位的典型成因：Steam 删节时**插入或删除 `%N`/`%P` 控制行**，而 Res303 的 lng 是按旧版
脚本生成的，槽位对不上 —— 表现为「某处之后整体后移」。本脚本按上述模型重建，
并**逐条与源 CCS 核对**，只有残留失配为 0 才写入。

用法：
    python script/fix_lng_alignment.py                 # 只诊断，列出每个脚本的失配数
    python script/fix_lng_alignment.py --write         # 修复残留失配为 0 的脚本
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
BACKUP = ROOT / 'asset' / 'Rio.arc.before_lng_realign'
_S = re.compile(r'\\d|%K%P|%K|%P|%N')
_Q = '“”「”」『』\'"'
CTRL = ('%N', '%P')


def nz(t):
    return ''.join(c for c in _S.sub('', t) if c not in _Q and not c.isspace())


CTRL_TAIL = re.compile(r'(?:%[A-Za-z])+$')
CTRL_ONLY = re.compile(r'^(?:%[A-Za-z])+$')


def split_ctrl(text):
    """(正文, 尾部控制符)。`%N` 这种整句控制符返回 ('', '%N')。"""
    t = text.strip()
    if CTRL_ONLY.match(t):
        return '', t
    m = CTRL_TAIL.search(t)
    return (t[:m.start()], m.group(0)) if m else (t, '')


def expected(rows, ccs):
    """按模型给出每个 `14` 应有的 lng 文本。

    **尾部控制符照抄同一条 ws2 正文的**（`%K` / `%K%P` / `%N` / `%P`）——
    引擎靠它控制换行与停顿，不能一律补 `%K%P`（见 doc/localization.md
    「配套 lng 的生成规则」，由 CNR0005 与 res303 产物逐字节比对实证）。
    """
    out, n = [], 0
    for t in rows:
        ts = t.strip()
        if CTRL_ONLY.match(ts):
            out.append(ts)
            continue
        n += 1
        body, ctrl = split_ctrl(ts)
        e = ccs.get(n)
        if not e:
            out.append('')        # 源 CCS 覆盖不到（Steam 新增行）-> 无从核对，保留原条目
            continue
        out.append(split_ctrl(lng.strip_speaker_wrap(e))[0] + ctrl)
    return out


def compare(rows, lng_list, ccs):
    """返回 (应有序列, 失配列表, 无从核对的条数)。`exp` 为空 = 源 CCS 覆盖不到
    （Steam 新增行），无从核对，不计入失配。"""
    exp = expected(rows, ccs)
    bad, unknown = [], 0
    for k in range(len(rows)):
        if not exp[k]:
            unknown += 1
            continue
        got = nz(lng_list[k]) if k < len(lng_list) else None
        if got != nz(exp[k]):
            bad.append((k, rows[k][:16], (lng_list[k][:16] if k < len(lng_list) else '**缺**'),
                        exp[k][:16]))
    return exp, bad, unknown


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--write', action='store_true')
    args = ap.parse_args()

    members = {n.decode('utf-16-le').upper(): v for n, v in arcbuild.read_raw(RIO)}
    todo, rows_out, skipped = [], [], []
    for name in sorted(k for k in members if k.endswith('.WS2')):
        stem = name[:-4]
        lk = stem + '.LNG'
        if lk not in members:
            continue
        src = stem[:-3] if stem.endswith('_EN') else stem
        p = CCS_DIR / (src + '.CCS')
        if not p.exists():
            continue
        ccs = lng.parse_ccs(p)
        ins = ws2disasm.disassemble(ws2.decode(members[name]))
        # **跳过有选项表（`0f`）的脚本**：lng 的槽位 = 字符串池位置，选项文本也占槽位
        # （见 doc/lessons-learned.md §8），本脚本的模型只覆盖 `14`，
        # 重排会把选项条目挤掉。这类脚本需要单独处理，暂不动。
        if any(i.opcode == 0x0f for i in ins):
            skipped.append((stem, '0f 选项表'))
            continue
        rows = [i.fields['text'] for i in ins if i.opcode == 0x14]
        # **只在「非控制行数 == 源行数」时才能用 1:1 模型**：
        # ws2 的对话序列是源序列的**子序列**（MoeNovel 只删不重排），等数量 + 子序列
        # ⇒ 逐条恒等。不等时（Steam 删节/改写）模型不成立 —— 例如 12 个宿主里
        # `CCC0000_en` 是 211 句对源 195 行，硬套会把前缀的译文整体错位。
        n_eff = sum(1 for r in rows if r.strip() not in CTRL)
        if n_eff != len(ccs):
            skipped.append((stem, '句数不等 %d vs 源 %d' % (n_eff, len(ccs))))
            continue
        cur = lng.parse_lng(members[lk])
        exp, bad, unk = compare(rows, cur, ccs)
        # 修复：控制行照模型补槽位，正文照源 CCS —— 但**保留原有条目的尾部**，
        # 源 CCS 覆盖不到的（Steam 新增行）留空会显示原文，比乱填安全
        fixed = []
        for k in range(len(rows)):
            if exp[k]:
                fixed.append(exp[k])
            else:
                fixed.append(cur[k] if k < len(cur) else '')
        _, bad2, unk2 = compare(rows, fixed, ccs)
        rows_out.append((stem, len(rows), len(cur), len(bad), len(bad2), fixed, unk))
        if bad2:
            todo.append((stem, len(bad), len(bad2), bad2[:2]))

    print('%-20s %-7s %-7s %-8s %-8s %s' % ('脚本', '14条数', '现lng', '现失配', '重建后失配', '无从核对'))
    for stem, nr, nl, b1, b2, _, unk in rows_out:
        if b1 or b2:
            print('%-20s %-7d %-7d %-8d %-8d %d' % (stem, nr, nl, b1, b2, unk))
    print()
    # 只修「原本有失配、重建后失配归零、且全部条目都能被源核对」的
    # 可安全修：原本有失配、重建后失配归零。源覆盖不到的条目**保留原值**，不删。
    clean = [r for r in rows_out if r[3] and r[4] == 0]
    print('现有失配的脚本 %d 个；其中重建后归零的 %d 个' % (len([r for r in rows_out if r[3]]),
                                                     len(clean)))
    if todo:
        print('\n重建后仍有残留（源 CCS 覆盖不到，属 Steam 新增行）%d 个：' % len(todo))
        for stem, b1, b2, ex in todo[:12]:
            print('  %-20s %d -> %d  例: %s' % (stem, b1, b2, ex))

    if not args.write:
        print('\n（未指定 --write，未写入）')
        return 0
    if not clean:
        print('没有可安全修复的脚本')
        return 0
    if BACKUP.exists():
        print('\n[备份] 已存在 %s' % BACKUP)
    else:
        arcbuild.write_arc(arcbuild.read_raw(RIO), BACKUP)
        print('\n[备份] asset/Rio.arc -> %s' % BACKUP)
    updated = 0
    out = []
    for nb, data in arcbuild.read_raw(RIO):
        nm = nb.decode('utf-16-le').upper()
        hit = next((r for r in clean if nm == r[0] + '.LNG'), None)
        if hit:
            data = lng.encode_lng(hit[5])
            updated += 1
        out.append((nb, data))
    arcbuild.write_arc(out, RIO)
    count, size, _ = arcbuild.verify(RIO)
    print('[写入] 重对齐 %d 个 lng；回读校验通过（%d 成员，%d 字节）' % (updated, count, size))
    return 0


if __name__ == '__main__':
    sys.exit(main())
