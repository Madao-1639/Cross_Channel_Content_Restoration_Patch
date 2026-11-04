"""译文覆盖审计：产物 `Rio.arc` 里**哪些格子的文本不是中文**（只读，报告不拦截）。

为什么需要它：流水线对 lng 的硬判据只有「每个槽位都有处置」「对话格必带尾控制符」
「lng 条数 == 14 条数 + Σ(0f 条目数)」（见 `script/apply_text_map.py`、`tool/textplan.py`）——
这些判据保证**结构**正确，但**不保证处置的内容是中文**：
`op:"ccs"` 直接照抄汉化组 CCS 的中文列，汉化组填罗马字/英文时链条无从识别；
35 个第三方带入 lng（`resource/carried_lng/`，`CC_*` 场景）更是整文件原样发布，从未做文本审计。
本脚本补这条：逐格分类，把「不是中文」的格子全部列出来供人工判定是刻意保留还是漏译。

分类（按优先级，取第一个命中的）：
  * `ctrl`    —— 剥掉尾部控制符后为空（`%N`/`%P` 这类结构格，Steam 原档同样如此）
  * `symbol`  —— 无汉字、无字母、无假名（`“……”`、`＊＊＊`、`†††`、`○×△□`）
  * `kana`    —— 无汉字但有假名（日文残留）
  * `latin`   —— 无汉字但是拉丁字母/数字串（罗马字拟音、外语、英文感叹）

每格同时标出 `==en`（与 ws2 内嵌英文**逐字相同**）—— 那是"连着 Steam 原文一起留着"的最硬信号。

用法（项目根目录）：
    python script/audit/audit_translation_coverage.py            # 汇总 + 全部非中文格子
    python script/audit/audit_translation_coverage.py --list     # 同上（明细不截断）
"""
import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tool import arcbuild, layers, lng, textplan as TP, ws2, ws2disasm  # noqa: E402

# `lng` 不覆盖的文本指令：0x8f = 语音+字幕行（通道 / 语音文件 / u8 a / 文本，各以 NUL 分隔）,
# 文本存在 ws2 里（Shift-JIS/ASCII），只能整段改写脚本 —— 本审计必须单列的一类盲区。
NON_LNG_TEXT_OPS = {0x8f: '0x8f 语音+字幕行（不经 lng）'}

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

ASSET_RIO = ROOT / 'asset' / 'Rio.arc'
CARRIED = ROOT / 'resource' / 'carried_lng' / 'manifest.json'

CJK = re.compile(r'[\u3400-\u9fff]')
KANA = re.compile(r'[\u3040-\u30ff]')
LATIN = re.compile(r'[A-Za-z0-9]')
TAIL = re.compile(r'((?:%[A-Za-z])+)\s*$')


def classify(text):
    body = TAIL.sub('', text or '').strip()
    if not body:
        return 'ctrl', ''
    if CJK.search(body):
        return None, body
    if KANA.search(body):
        return 'kana', body
    if LATIN.search(body):
        return 'latin', body
    return 'symbol', body


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--list', action='store_true', help='明细不截断')
    args = ap.parse_args(argv)

    carried = set()
    if CARRIED.exists():
        import json
        carried = {k[:-4].upper() for k in json.loads(CARRIED.read_text(encoding='utf-8'))['members']}

    members = layers.effective_dict('Rio.arc')   # 两层合并（语言包覆盖根目录）
    stems = sorted({k[:-4] for k in members if k.endswith('.LNG')})
    total_slots = 0
    mismatch = []
    buckets = {}
    for stem in stems:
        if stem + '.WS2' not in members:
            print('[告警] %s.lng 没有配对的 .ws2' % stem)
            continue
        en = TP.en_of_from(ws2disasm.disassemble(ws2.decode(members[stem + '.WS2'])))
        L = lng.parse_lng(members[stem + '.LNG'])
        total_slots += len(L)
        if len(L) != len(en):
            mismatch.append((stem, len(L), len(en)))
        for i, s in enumerate(L):
            kind, body = classify(s)
            if kind is None:
                continue
            e = TAIL.sub('', en.get(i, {}).get('ws2_text', '') or '').strip()
            same = '==' if TAIL.sub('', s or '').strip() == e else ''
            buckets.setdefault(kind, []).append(
                (stem, i, s, stem in carried, same))

    print('== 覆盖审计：asset/Rio.arc ==')
    print('脚本 %d 个（其中第三方带入 %d 个）、槽位 %d 格'
          % (len(stems), len([s for s in stems if s in carried]), total_slots))
    if mismatch:
        print('[FAIL] lng 条数 != ws2 槽位数：%d 个脚本（错位风险）' % len(mismatch))
        for s, a, b in mismatch:
            print('   %-18s lng=%d slots=%d' % (s, a, b))
    else:
        print('[OK] 全部脚本 lng 条数 == ws2 槽位数（无错位）')

    # lng 之外的文本指令（0x8f）：按 cp932 从 ws2 现读，语言包不放宽编码 ⇒ 保持英文原文
    outside = {}
    for stem in stems:
        if stem + '.WS2' not in members:
            continue
        for o in ws2disasm.disassemble(ws2.decode(members[stem + '.WS2'])):
            label = NON_LNG_TEXT_OPS.get(o.opcode)
            if not label:
                continue
            body = (o.fields.get('text') or '').strip()
            if not body:
                outside.setdefault(stem, []).append((o.offset, 'empty', ''))
                continue
            kind = 'cjk' if CJK.search(body) else ('kana' if KANA.search(body) else 'latin')
            outside.setdefault(stem, []).append((o.offset, kind, body))
    if outside:
        print('')
        print('-- lng 之外的文本指令（0x8f 语音+字幕行）--')
        for stem, rows in sorted(outside.items()):
            empty = [r for r in rows if r[1] == 'empty']
            bad = [r for r in rows if r[1] not in ('cjk', 'empty')]
            if not bad:
                print('   %-18s %d 条，空文本 %d 条（现行口径：保持 Steam 英文原文，见 doc/localization.md）'
                      % (stem, len(rows), len(empty)))
                continue
            print('   %-18s %d 条；非中文 %d 条' % (stem, len(rows), len(bad)))
            for off, kind, body in (bad if args.list else bad[:6]):
                print('      0x%06x [%s] %r' % (off, kind, body[:70]))

    order = ['latin', 'kana', 'symbol', 'ctrl']
    label = {'latin': '拉丁字母/数字（罗马字拟音、外语、英文感叹）',
             'kana': '假名（日文残留）',
             'symbol': '纯符号（引号省略号、记号、拟声记号）',
             'ctrl': '仅尾部控制符（结构格）'}
    for kind in order:
        rows = buckets.get(kind, [])
        print('\n-- %s：%d 格 --' % (label[kind], len(rows)))
        if kind in ('ctrl', 'symbol'):
            continue
        shown = rows if args.list else rows
        for stem, i, s, is_carried, same in shown:
            print('   %-18s #%-5d %s%s %r'
                  % (stem, i, '[第三方]' if is_carried else '[本项目]',
                     ' ==en' + same if same else '      ', s))
    return 0


if __name__ == '__main__':
    sys.exit(main())
