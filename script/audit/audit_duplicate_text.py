# -*- coding: utf-8 -*-
"""检测：**同一脚本内**出现完全相同的显示文本的格。**只检测、不判错**（不参与流水线的通过/失败）。

⚠️ 同文本重复是**正常现象**：
  · 短句与纯符号句（`……`、`“……”`、`“呀！？”`、`“呼……”`）在原版里本就反复出现；
  · **跨脚本**的重复（姊妹场同一句对白）更是既定设计；
  · 同一原版行被切分成多片（`span`）时，各片的文本本来就会与别处重合。
⇒ 所以本脚本**只列、不断言**，并做三道降噪：

  1. **只比同一脚本内**的格（跨脚本一律不比）；
  2. **只列归一化后长度 ≥ 8 的**（短句 / 纯符号句一律略过）；
  3. 每组标注每格的 `k / op / 出处`（绑了哪条原版行、还是自撰格）——
     "两条都绑原版行"多半是原版自己就重复；**"至少一条是自撰格"才值得人看一眼**。

用法：
    python script/audit/audit_duplicate_text.py [--min-len 8] [--limit 40]
"""
import argparse
import collections
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tool import lng as lngmod                      # noqa: E402

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

TM = ROOT / 'resource' / 'text_map.json'
CCS = ROOT.parent / 'cross-channel_chinese-localization_project' / 'Scripts' / '20150412'
TAIL = re.compile(r'((?:%[A-Za-z])+)\s*$')


def nrm(t):
    t = lngmod.strip_speaker_only(t or '')
    t = TAIL.sub('', t)
    return ''.join(ch for ch in t.replace('\\n', '') if not ch.isspace())


def read_ccs(name):
    p = CCS / (name + '.CCS')
    raw = None
    for _ in range(15):
        try:
            raw = p.read_bytes()
            break
        except Exception:
            time.sleep(2.0)
    if raw is None:
        raise SystemExit('读不到 %s' % p)
    out = {}
    for line in raw.decode('utf-16le', errors='replace').splitlines():
        m = re.match(r'^>1[●○](\d+)[●○](.*)$', line.strip())
        if m:
            out[int(m.group(1))] = m.group(2)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--min-len', type=int, default=8)
    ap.add_argument('--limit', type=int, default=40)
    args = ap.parse_args()

    tmap = json.loads(TM.read_text(encoding='utf-8'))['scripts']
    groups, ncell, skipped_short = [], 0, 0
    for stem, sc in sorted(tmap.items()):
        zh = read_ccs(sc['ccs'])
        cells = []                       # (k, 归一化文本, 出处说明)
        for it in sc['items']:
            op = it['op']
            if op in ('drop',):
                continue
            if op == 'insert':
                a, b = it['src_rows']
                for r in range(a, b + 1):
                    t = nrm(zh.get(r, ''))
                    if t:
                        cells.append(('insert@%s' % it['at_k'], t, '原版行 %d（插入）' % r))
                continue
            k = it.get('k')
            kk = k if isinstance(k, list) else [k, k]
            for x in range(kk[0], kk[1] + 1):
                if op == 'ccs':
                    r = it['row'] if 'row' in it else (it['row0'] + (x - kk[0]) if 'row0' in it else None)
                    if r is None:
                        continue
                    t = nrm(zh.get(r, ''))
                    if 'span' in it:
                        base = lngmod.strip_speaker_only(zh.get(r, ''))
                        a, b = it['span']
                        t = nrm(base[a:b])
                    cells.append((x, t, '原版行 %s' % r))
                else:
                    cells.append((x, nrm(it.get('zh') or ''), '%s（自撰/控制）' % op))
        ncell += len(cells)
        by = collections.defaultdict(list)
        for k, t, src in cells:
            if not t:
                continue
            if len(t) < args.min_len:
                skipped_short += 1
                continue
            by[t].append((k, src))
        for t, ws in by.items():
            if len(ws) > 1:
                groups.append((stem, t, ws))

    print('=== 检测报告：同一脚本内的相同显示文本（**只列，不判错**）===')
    print('比对格数 %d；长度 <%d 的格已略过 %d 个（短句/纯符号属正常重复）'
          % (ncell, args.min_len, skipped_short))
    print('命中 %d 组（长度 ≥%d）—— 下列各组请人工看一眼：' % (len(groups), args.min_len))
    susp = [g for g in groups if any('自撰' in s for _, s in g[2])]
    print('其中**至少含一条自撰格**的 %d 组（更值得看）：' % len(susp))
    shown = 0
    for stem, t, ws in groups:
        if not any('自撰' in s for _, s in ws):
            continue
        shown += 1
        if shown > args.limit:
            break
        print('  %-16s %s' % (stem, json.dumps(t, ensure_ascii=False)[:60]))
        for k, src in ws:
            print('        k=%-8s %s' % (k, src))
    if shown > args.limit:
        print('  …（只列前 %d 组）' % args.limit)
    print('\n（**这不是判错**：原版自己重复、切分片重合都属正常；本报告仅供人工扫一眼。）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
