# -*- coding: utf-8 -*-
"""修复内化语料里**被截断的中文列**（一次性、幂等）：`>1` 行丢了行号与正文。

## 症状与根因

`resource/corpus/ccs/*.CCS` 副本里有 28 行中文列只剩一个光秃秃的 `>1`
（本该是 `>1●dddd●<中文>`）—— 上游文件里它们是完整的。这些行恰好都含有
「13 种笔误字」之一（揺/抜/拋/強/恥/時/訳/謝/離/額/髄/黒/牳），所以截断发生在
**笔误勘误那一趟替换**里：该行正文被整段吃掉。

后果是**静默的**：`text_map` 里按 `op=ccs` 引用这些行的格，中文列取到空串，
产物对应格只写出一条 `%K%P` —— 屏幕上**一行空白**。格数/条数/覆盖类闸全都照过，
只有 `verify_text_map_structure.py` 的「引用行必须有中文列」那一条才拦得住
（span 可定位只覆盖带 `span` 的条目）。

## 做法

以上游 MewX 项目 `Scripts/20150412/*.CCS` 为源（**只在本次修复时读，构建不依赖它**）：
对每个 `>1` 光秃行，按它前面那条 `>0○dddd○` 的行号取上游同行的 `>1●dddd●` 全文，
替换后全表再套一遍 13 字勘误（与原勘误同一口径），写回副本。

幂等：没有光秃 `>1` 行、也没有残留笔误字时无事可做。

用法（项目根目录）：
    python script/internalize/repair_corpus_truncated.py [--check]
"""
import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

CCS_DIR = ROOT / 'resource' / 'corpus' / 'ccs'
UPSTREAM = ROOT.parent / 'cross-channel_chinese-localization_project' / 'Scripts' / '20150412'

# 与 doc/localization.md「上游笔误勘误」同一张表（日文旧字形 → 简体/惯用写法）
TYPOS = {'揺': '摇', '抜': '拔', '拋': '抛', '強': '强', '恥': '耻', '時': '时',
         '訳': '译', '謝': '谢', '離': '离', '額': '额', '髄': '髓', '黒': '黑', '牳': '姆'}

_ROW = re.compile(r'^>0.{1}(\d+).')
_ZH = re.compile(r'^>1.{1}(\d+).')


def fix_typos(s):
    for a, b in TYPOS.items():
        s = s.replace(a, b)
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--check', action='store_true', help='只报告，不写盘')
    args = ap.parse_args()
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')

    total_fixed, files_fixed, left_typo = 0, [], []
    for dst in sorted(CCS_DIR.glob('*.CCS')):
        orig = dst.read_bytes()
        lines = orig.decode('utf-16-le').splitlines(keepends=True)
        bare = [i for i, l in enumerate(lines) if l.strip() == '>1']

        if bare:
            up_path = UPSTREAM / dst.name
            if not up_path.exists():
                print('[跳过] %s：上游缺 %s' % (dst.name, up_path))
                continue
            up_zh = {}
            for l in up_path.read_bytes().decode('utf-16-le').splitlines(keepends=True):
                m = _ZH.match(l.strip())
                if m:
                    up_zh[int(m.group(1))] = l
            for i in bare:
                row = None
                for j in range(i - 1, max(-1, i - 4), -1):
                    m = _ROW.match(lines[j].strip())
                    if m:
                        row = int(m.group(1))
                        break
                if row is None or row not in up_zh:
                    print('[警告] %s 第 %d 行找不到对应行（row=%s）' % (dst.name, i + 1, row))
                    continue
                lines[i] = up_zh[row]

        # 全表套一遍勘误（幂等；顺带把早先手工回填、未归一的那几行一并归位）
        for i, l in enumerate(lines):
            if _ZH.match(l.strip()):
                lines[i] = fix_typos(l)

        new = ''.join(lines).encode('utf-16-le')
        if new == orig:
            continue
        if not args.check:
            dst.write_bytes(new)
        n = len(bare)
        print('%-16s 回填 %d 行 / 勘误归一（%d → %d 字节）' % (dst.name, n, len(orig), len(new)))
        total_fixed += n
        files_fixed.append(dst.name)

    # 复核：不应再有光秃 `>1`，也不应再有笔误字
    for f in sorted(CCS_DIR.glob('*.CCS')):
        for i, l in enumerate(f.read_bytes().decode('utf-16-le').splitlines()):
            if l.strip() == '>1':
                left_typo.append((f.name, i + 1, 'bare'))
            elif _ZH.match(l.strip()) and any(c in l for c in TYPOS):
                left_typo.append((f.name, i + 1, 'typo'))
    print('\n合计：%d 个文件 / %d 行%s' % (len(files_fixed), total_fixed,
                                          '（--check 未写盘）' if args.check else ''))
    if left_typo:
        print('！仍有 %d 处残留：%s' % (len(left_typo), left_typo[:6]))
        return 1
    print('✓ 无光秃 `>1`、无残留笔误字')
    return 0


if __name__ == '__main__':
    sys.exit(main())
