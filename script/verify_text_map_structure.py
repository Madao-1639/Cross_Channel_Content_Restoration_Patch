# -*- coding: utf-8 -*-
"""`text_map.json` 的**匹配层**验收 —— 只判表本身是否自洽，**不看 `asset`**。

产物的状态是**写盘阶段**的事（那时再判「哪段已落盘」），不该混进匹配结论里。
本脚本回答的是：**每个脚本的槽位定好了没有、表内部有没有矛盾**。

四条（全部为**定义性 / 机械性**判据，无阈值）：

  1. **格数自洽**：`n_final == n_steam + Σ(insert.n) − Σ(drop)`
  2. **覆盖无空档无重叠**：去掉删格与插入格后，剩下的必须恰好是 `0 .. n_steam-1` 各一次
  3. **行序单调**：按**播放序**取全部行号（`op=ccs` 取的行 ＋ 插入段的源行），必须**不递减**
     （回退会让内容在时间上倒流）。同一脚本内的**每一处**回退都报，不只报第一处。
  4. **复用可定位**：带 `span` 的条目，其 `[from,to)` 必须落在那行中文里且非空

用法：
    python script/verify_text_map_structure.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tool.lng import parse_ccs_both                      # noqa: E402
from tool import lng as lngmod                                # noqa: E402

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

TM = ROOT / 'resource' / 'text_map.json'
CCS_DIR = ROOT.parent / 'cross-channel_chinese-localization_project' / 'Scripts' / '20150412'


def norm_spk(v):
    return lngmod.strip_speaker_wrap(v or '')


def main():
    tmap = json.loads(TM.read_text(encoding='utf-8'))['scripts']
    ccs = {}
    for f in sorted(CCS_DIR.glob('*.CCS')):
        _, zh = parse_ccs_both(f)
        ccs[f.stem] = {n: norm_spk(v) for n, v in zh.items()}

    bad = []
    for s, m in sorted(tmap.items()):
        pos, drops, dropped = [], 0, set()
        for it in m['items']:
            op = it['op']
            if op == 'insert':
                pos.extend([None] * it['n'])
                continue
            ks = it['k'] if isinstance(it['k'], list) else [it['k'], it['k']]
            if op == 'drop':
                # **删格不算最终槽位**：它们连同 `15` 一起从脚本里去掉了
                drops += ks[1] - ks[0] + 1
                dropped.update(range(ks[0], ks[1] + 1))
                continue
            pos.extend(range(ks[0], ks[1] + 1))
        # ① 格数
        #    `split`（一格拆多格）：**输入面**仍占 1 格（覆盖检查照旧），但**产出 +len(at) 格**。
        n_ins = sum(x['n'] for x in m['items'] if x['op'] == 'insert')
        n_spl = sum(len(x['at']) for x in m['items'] if x['op'] == 'split')
        exp = m['n_steam'] + n_ins + n_spl - drops
        if len(pos) != m['n_steam'] - drops + n_ins:
            bad.append((s, '格数：展开 %d，n_steam %d − drop %d + insert %d'
                        % (len(pos), m['n_steam'], drops, n_ins)))
        if m['n_final'] != exp:
            bad.append((s, 'n_final %d != n_steam %d − drop %d + insert %d + split %d = %d'
                        % (m['n_final'], m['n_steam'], drops, n_ins, n_spl, exp)))
        for it in m['items']:
            if it['op'] != 'split':
                continue
            kk, at = it['k'], it['at']
            if not isinstance(kk, int) or not (0 <= kk < m['n_steam']):
                bad.append((s, 'split 的 k=%r 不是 0..%d 的输入格' % (kk, m['n_steam'] - 1)))
            if not at or any(b <= a for a, b in zip([0] + list(at), at)):
                bad.append((s, 'split 的 at=%r 不是严格递增的正偏移' % (at,)))
        # ② 覆盖（去掉插入与删格后）
        keep = [p for p in pos if p is not None]
        want = [x for x in range(m['n_steam']) if x not in dropped]
        if keep != want:
            bad.append((s, '覆盖不是 0..%d 去掉 %d 个删格后各一次（去重后 %d 个）'
                        % (m['n_steam'] - 1, len(dropped), len(set(keep)))))
        # ③ 行序单调（**按播放序**取全部行号：`op=ccs` 取的行 ＋ 插入段的源行）
        #    播放序 = 「槽位按 k 递增；每个槽位**之后**紧跟 `at_k` 等于它的插入段」，
        #    `at_k=-1` 的插入段在最前（与写盘器 `rebuild` 的语义一致）。
        #    ⚠️ 2026-09-25 两处更正：① 原先只扫 `op=ccs`、**每脚本遇到第一处就 break**
        #    （既漏了插入行的源行，也把后面的回退全盖住）；② 不能按 `items` 的**列表顺序**判 ——
        #    列表按 `(at_k or k)` 排序，`at_k=k` 的插入项会与槽 `k` 自己的条目并列、先后不定，
        #    与真实播放序无关（实测会报出"657 在 656 之前"这种假回退）。
        #    另：`rw = row0 or row` 有 0 号行的假值坑，改成显式判 None。
        slot_row, ins_at, pre = {}, {}, []
        for it in m['items']:
            if it['op'] == 'insert':
                a, b = it['src_rows']
                tgt = pre if it['at_k'] == -1 else ins_at.setdefault(it['at_k'], [])
                tgt.extend(range(a, b + 1))
                continue
            if it['op'] != 'ccs':
                continue
            rw = it['row0'] if 'row0' in it else it.get('row')
            if rw is None:
                continue
            ks = it['k'] if isinstance(it['k'], list) else [it['k'], it['k']]
            for j, k in enumerate(range(ks[0], ks[1] + 1)):
                slot_row[k] = rw + j
        seq = [('at_k=-1', r) for r in pre]
        for k in range(m['n_steam']):
            if k in slot_row:
                seq.append(('k=%d' % k, slot_row[k]))
            seq += [('insert@%d' % k, r) for r in ins_at.pop(k, [])]
        for k in sorted(ins_at):                       # 挂在删格槽位上的插入段（兜底，不应出现）
            seq += [('insert@%d' % k, r) for r in ins_at[k]]
        prev, prev_at = 0, None
        for at, r in seq:
            if r < prev:
                bad.append((s, '行序回退：%s 取 %d，而 %s 已用到 %d' % (at, r, prev_at, prev)))
            prev, prev_at = r, at
        # ④ span 可定位
        tab = ccs.get(m['ccs'], {})
        for it in m['items']:
            if 'span' not in it:
                continue
            full = tab.get(it.get('row'), '')
            a, b = it['span']
            if not (0 <= a < b <= len(full)):
                bad.append((s, 'span %s 越界（该行 %d 字）' % (it['span'], len(full))))

    tot = sum(m['n_final'] for m in tmap.values())
    ins = sum(x['n'] for m in tmap.values() for x in m['items'] if x['op'] == 'insert')
    dr = sum((it['k'][1] - it['k'][0] + 1) if isinstance(it['k'], list) else 1
             for m in tmap.values() for it in m['items'] if it['op'] == 'drop')
    print('表：%d 个脚本；最终槽位 %d（= Steam 侧 %d + 插入 %d − 删格 %d）'
          % (len(tmap), tot, sum(m['n_steam'] for m in tmap.values()), ins, dr))
    if bad:
        print('！%d 处不自洽（前 20）：' % len(bad))
        for s, why in bad[:20]:
            print('   %-16s %s' % (s, why))
        return 1
    print('✓ 四条全部通过：格数自洽 / 覆盖无空档无重叠 / 行序单调 / span 可定位')
    return 0


if __name__ == '__main__':
    sys.exit(main())
