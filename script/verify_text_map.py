# -*- coding: utf-8 -*-
"""`text_map.json` 的哨兵：写完盘后断言「表」与「实际产物」对得上。

**它不是数据、也不引入冗余** —— 只做断言，跑完即丢。七条：

  1. **槽位覆盖**：`0..n_steam-1` 每个槽**恰有一个**非插入项（插入项是"在原槽之后插"，不占 Steam 槽）；
  2. **`n_final` 自洽**：`n_final == n_steam + Σ插入 − Σ删格`；
  3. 每个脚本的**实际 ws2 占位数**（`14` 条数 + Σ`0f` 条目数）== 表里的 `n_final`；
  4. `op=insert` 声明的那几段，**确实落在 `at_k` 之后、长度是 `n`**，
     且那几格的正文是**源 WSC 对应行的日文**（而不是 Steam 英文）；
  5. 每个脚本的 **lng 条数** == `n_final`；
  6. `op=drop` 的格，其原文**不是**某条仍被承载的原版行的真子串（＝没有删掉半行）；
  7. **一行只消耗一次**：同一脚本内，被绑到同一行的每一格都必须是该行的 `span` 片段
     （切分 ⇒ 次数＝段数；分支 ⇒ 按各脚本分别计，两者可叠加）。

断言 1、2 是**表侧**不变量，与产物是否已同步无关：少一个槽 ⇒ 表对该槽**没有任何处置**，
写盘时它照旧留在脚本里、lng 成一条**空条目**（屏幕上是一行空白）；多一个 ⇒ 两项争同一槽。
`n_final` 少算 ⇒ 写盘时 `build_lng` 的条数断言（`len(newL) != n_final`）会中止构建，
但中止发生在**构建阶段**、且只报"lng 条数不符"，不会指出是哪一格的问题。
其余五条里，只有**第 4 条（插入段）依赖槽位对齐**；第 5、6、7 条与产物布局无关，
所以槽位序对不上时**只跳过第 4 条**，其余照跑（产物过期时它们是唯一还验得动的检查）。

## 为什么需要它

`op=insert` 的「插多少格」在两边**各有一份声明** —— 表里写 `n`，源 CCS 的 `src_rows`
区间决定实际条数。两处是**独立算出来**的：哪天源区间被改了而表没跟着改，两边会静默错开，
写出来的 lng 比脚本少若干条 —— 而引擎是**按位置替换**的，一少就**整场串行**。

这是本项目反复踩过的坑（缺陷 #1 的 M:1 重复、`CCC3027` 的槽位表过时）。
阶段 6 的「严格等式」能兜住同类错位，本脚本只是把「**哪一段**对不上」定位得更准。

用法：
    python script/verify_text_map.py
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tool import arcbuild, lng as lngmod, wsc, ws2, ws2disasm       # noqa: E402
from tool.wsc2ws2 import decrypt_wsc                                 # noqa: E402
from tool.lng import parse_ccs_both                             # noqa: E402

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

TM = ROOT / 'resource' / 'text_map.json'
RIO = ROOT / 'asset' / 'Rio.arc'
WSC_DIR = ROOT / 'resource' / 'corpus' / 'wsc'
CCS_DIR = ROOT.parent / 'cross-channel_chinese-localization_project' / 'Scripts' / '20150412'


def nrm(t):
    """去控制标记、说话人前缀、空白 —— 只留正文用于比对。"""
    return ''.join(ch for ch in (lngmod.strip_speaker_only(t or '').replace('%K', '')
                                 .replace('%P', '').replace('%N', '').replace('\\n', ''))
                   if not ch.isspace())


def slots_of(raw):
    """→ [('dlg'|'opt', 指令)]，按播放序（`14` 各一格、`0f` 的每个条目也各一格）。"""
    out = []
    for i in ws2disasm.disassemble(raw):
        if i.opcode == 0x14:
            out.append(('dlg', i))
        elif i.opcode == 0x0f:
            out.extend([('opt', i)] * len(i.fields['entries']))
    return out


def src_dialogues(stem):
    """源 WSC 的对话文本，按**对话序**（非 `id`）—— CCS 行 = 序 + 1。"""
    p = WSC_DIR / (stem + '.WSC')
    if not p.exists():
        return []
    raw = p.read_bytes()
    try:
        ins = wsc.disassemble(raw)
    except Exception:
        ins = wsc.disassemble(decrypt_wsc(raw))
    return [i.fields.get('text', '') for i in ins if i.opcode in (0x41, 0x42)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--quiet', action='store_true')
    ap.add_argument('--rio', default=str(RIO), help='要校验的归档（默认 asset/Rio.arc）')
    args = ap.parse_args()
    rio = Path(args.rio)

    TMAP = json.loads(TM.read_text(encoding='utf-8'))['scripts']
    members = {n.decode("utf-16-le").upper(): v for n, v in arcbuild.read_raw(rio)}
    err = []

    for s, m in sorted(TMAP.items()):
        if (s + '.WS2') not in members:
            err.append((s, '脚本不在 asset 里'))
            continue
        slots = slots_of(ws2.decode(members[s + '.WS2']))
        n_final = m['n_final']

        # ① 槽位覆盖（**表侧**：与产物是否已同步无关）
        cov = {}
        for it in m['items']:
            if it['op'] == 'insert':
                continue
            ks = it['k'] if isinstance(it['k'], list) else [it['k'], it['k']]
            for k in range(ks[0], ks[1] + 1):
                cov.setdefault(k, []).append(it['op'])
        miss = [k for k in range(m['n_steam']) if k not in cov]
        dupk = sorted(k for k, ops in cov.items() if len(ops) > 1)
        over = sorted(k for k in cov if k >= m['n_steam'])
        if miss or dupk or over:
            err.append((s, '槽位覆盖不全：缺 %s ／ 重复 %s ／ 越界 %s'
                        % (miss[:8], dupk[:8], over[:8])))

        # ② `n_final` 自洽（**表侧**）：写盘器只在遇到 `drop` 时跳过该槽、插入段另加格，
        #    所以槽数只由这三项决定，与「覆盖是否有空洞」无关（空洞只是让 lng 多一条空条目）。
        _ins = sum(it['n'] for it in m['items'] if it['op'] == 'insert')
        _spl = sum(len(it['at']) for it in m['items'] if it['op'] == 'split')   # 一格拆多格：多出的格数
        _dr = sum(1 for it in m['items'] if it['op'] == 'drop')
        _want = m['n_steam'] + _ins + _spl - _dr
        if n_final != _want:
            err.append((s, 'n_final %d != n_steam %d + 插入 %d + split %d - 删格 %d = %d'
                        % (n_final, m['n_steam'], _ins, _spl, _dr, _want)))

        # ③ 占位数
        if len(slots) != n_final:
            err.append((s, '占位数 %d != n_final %d' % (len(slots), n_final)))

        # ④ 插入段：落在 at_k 之后、长度 n、正文是源日文
        src = src_dialogues(m['ccs'])
        # 展开出「每个最终槽位对应哪个 Steam 槽位 / 是插入的」
        # ⚠️ 2026-09-19：**必须跳过 `drop` 的槽位** —— 删格后它不再占位（此前漏了，
        # 含删格的脚本会报「展开出的槽位序 191 != 实际 190」之类的假错）。
        # ⚠️ 2026-09-25：必须按**播放序**展开 —— 「槽位按 k 递增；每个槽**之后**紧跟 `at_k` 等于它的
        # 插入段；`at_k=-1` 的插入段在最前」（与写盘器 `rebuild` 一致）。
        # 不能按 `items` 的**列表顺序**展开：列表按 `(at_k or k)` 排序，`at_k=k` 的插入项会与
        # 槽 `k` 自己的条目**并列**、先后不定 ⇒ 插入格会落在错误的相对位置，
        # 检查会去比对错误的相邻格（实测报出「at_k=73 之后 1 格不全是插入格」这种假错）。
        drops = {it['k'] for it in m['items'] if it['op'] == 'drop'}
        ins_by_at, n_pre = {}, 0
        for it in m['items']:
            if it['op'] != 'insert':
                continue
            if it['at_k'] == -1:
                n_pre += it['n']
            else:
                ins_by_at[it['at_k']] = ins_by_at.get(it['at_k'], 0) + it['n']
        pos = [None] * n_pre
        spl_by_k = {}
        for it in m['items']:
            if it['op'] == 'split':
                spl_by_k[it['k']] = spl_by_k.get(it['k'], 0) + len(it['at'])
        for k in range(m['n_steam']):
            if k not in drops:
                pos.append(k)
                pos.extend([None] * spl_by_k.get(k, 0))    # `split` 多出的格（同格续页）
            pos.extend([None] * ins_by_at.pop(k, 0))
        for k in sorted(ins_by_at):                       # 挂在删格槽位上的插入段（兜底）
            pos.extend([None] * ins_by_at[k])
        # ⚠️ 槽位序对不上时**不能 `continue` 跳过整段** —— 那会把后面几条**与产物无关**的断言
        #    （⑤ lng 条数、⑥ 删格未删半行、⑦ 一行只消耗一次）**连坐跳过**，而它们恰恰是
        #    产物过期时唯一还验得动的检查（2026-09-25 第 28 轮验收指出：被跳过的正是刚改过的 8 场）。
        #    只跳过**依赖槽位对齐**的那一条（插入段的位置与正文）。
        pos_ok = len(pos) == len(slots)
        if not pos_ok:
            err.append((s, '展开出的槽位序 %d != 实际 %d' % (len(pos), len(slots))))
        off_at = {}                       # 同一 at_k 可能有多条插入项 ⇒ 按声明顺序累加偏移
        for it in (m['items'] if pos_ok else []):
            if it['op'] != 'insert':
                continue
            base = -1 if it['at_k'] == -1 else pos.index(it['at_k'])   # -1 ＝ 补在脚本最前
            at = base + off_at.get(it['at_k'], 0)
            off_at[it['at_k']] = off_at.get(it['at_k'], 0) + it['n']
            seg = list(range(at + 1, at + 1 + it['n']))
            if any(p is not None for p in pos[at + 1:at + 1 + it['n']]):
                err.append((s, 'at_k=%d 之后 %d 格不全是插入格' % (it['at_k'], it['n'])))
                continue
            a, b = it['src_rows']
            want = [src[r - 1] for r in range(a, b + 1) if 0 <= r - 1 < len(src)]
            got = [slots[p][1].fields.get('text', '') for p in seg if slots[p][0] == 'dlg']
            if len(want) != len(got):
                err.append((s, '插入段 CCS#%d-%d 有 %d 行，脚本里只有 %d 个对话格'
                            % (a, b, len(want), len(got))))
            elif [x.strip() for x in want] != [x.strip() for x in got]:
                bad = next(i for i in range(len(want)) if want[i].strip() != got[i].strip())
                err.append((s, '插入段第 %d 条正文不符：期望 %r，实际 %r'
                            % (bad, want[bad][:26], got[bad][:26])))

        # ⑤ lng 条数
        L = lngmod.parse_lng(members.get(s + '.LNG', b''))
        if len(L) != n_final:
            err.append((s, 'lng 条数 %d != n_final %d' % (len(L), n_final)))

        # ⑥ 删格不得「只删掉一行的一部分」
        #    （删掉的正文若恰是某条**仍被承载**的原版行的**真子串** ⇒ 那是被切开的半行，
        #      删了句子会从半句开始 / 半句收尾。判据：equal 全行才算重复、可删；
        #      只是子串 ⇒ 半段 ⇒ 报警。）
        d2 = [it for it in m['items'] if it['op'] == 'drop' and it.get('was') and len(nrm(it['was'])) >= 4]
        if d2:
            carried = set()
            for it in m['items']:
                if it['op'] != 'ccs':
                    continue
                if 'row' in it and not isinstance(it['k'], list):
                    carried.add(it['row'])
                elif 'row0' in it and isinstance(it['k'], list):
                    for j in range(it['k'][0], it['k'][1] + 1):
                        carried.add(it['row0'] + (j - it['k'][0]))
            _, zh = parse_ccs_both(CCS_DIR / (m['ccs'] + '.CCS'))
            for it in d2:
                w = nrm(it['was'])
                for r in carried:
                    full = nrm(zh.get(r, ''))
                    if full and full != w and w in full:
                        err.append((s, '删格 k=%d 的正文是原版行 %d 的**半段**（非整行）⇒ 句子会从半句开始'
                                    % (it['k'], r)))
                        break

        # ⑦ 一行只消耗一次（span 切分除外；分支按脚本分别计）
        _rows = {}
        for it in m['items']:
            if it['op'] != 'ccs':
                continue
            kk = it['k'] if isinstance(it['k'], list) else [it['k'], it['k']]
            for j, k in enumerate(range(kk[0], kk[1] + 1)):
                r = it['row'] if 'row' in it else (it['row0'] + j if 'row0' in it else None)
                if r is not None:
                    _rows.setdefault(r, []).append((k, 'span' in it))
        for r, ks in _rows.items():
            if len(ks) > 1 and not all(sp for _, sp in ks):
                err.append((s, '行 %d 被 %s 格重复消耗（只有 span 切分才能占多格）'
                            % (r, [k for k, _ in ks])))

    if not args.quiet:
        print('哨兵：脚本 %d 个（表内）。' % len(TMAP))
    if err:
        print('！%d 处不符：' % len(err))
        for s, why in err[:25]:
            print('   %-16s %s' % (s, why))
        return 1
    print('✓ 七条断言全部通过：槽位覆盖完整、n_final 自洽、占位数 == n_final、'
          '插入段正文与源 CCS 逐条相同、lng 条数 == n_final、删格未删半行、一行只消耗一次')
    return 0


if __name__ == '__main__':
    sys.exit(main())
