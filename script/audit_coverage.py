"""还原场景覆盖度审计：还原脚本覆盖到哪、宿主覆盖到哪、谁没被覆盖。

核心判据（见 doc/call-chain.md「两种接线法」）：
    还原脚本是否覆盖到插入点之后的所有部分？
只有「是」才谈得上替换式（截断宿主）；「否」时截断宿主会直接丢内容。

方法：做「源 WSC ↔ 宿主 lng ↔ 还原脚本 lng」三方对位。
  * 两版 lng 都是中文，可与汉化组 CCS（`>1●NNNN●` 的 NNNN = 原版 WSC 对话 id + 1）逐句对位；
  * CCS 行号 k ↔ WSC 对话 id k-1，所以对位结果直接是源序列下标；
  * 用 difflib.SequenceMatcher 等长块对位（**不要用"贪心前扫"**：遇到对不上的行会把指针
    推到后面，制造假间隙——旧版审计正是因此把已修好的 CCC0000 误判成"需截断"）。
  * **还原脚本一侧的区间是可靠的**（对位率 100%：它的 lng 就是 CCS 行的直抄），
    并与 Res303 `report.json` 的 `source_range` 11/11 逐值一致；
  * **宿主一侧是近似值**：Steam 删节时会**改写措辞**（`CCA0025C` 段 15..34 就被改成
    "人与人的关系，就是从主动伸手开始的……"这类意译），精确文本对位会漏配，
    所以"未覆盖行数"是**上界**，不是精确缺口。可信的信号是**尾部大段连续未覆盖**
    （如 `CNR0001` 的 530..570）与 Res303 报告的状态码。

校准：本脚本给出的「还原脚本覆盖区间」与 Res303 自己报告里的 `scenes[].source_range`
在 11 个场景上**逐值一致**，可交叉验证（`--res303` 打印对照）。

只读，不改动任何资源。用法（项目根目录）：
    python script/audit_coverage.py
"""
import argparse
import io
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tool import arcbuild, lng, ws2, ws2disasm  # noqa: E402

if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

CCS_DIR = ROOT / '..' / 'cross-channel_chinese-localization_project' / 'Scripts' / '20150412'
WSC_DIR = ROOT / 'tmp' / 'corpus' / 'wsc'
RES303_REPORT = (ROOT / '..' / 'CROSS_CHANNEL_Steam_CN_Restored_v3.0.3'
                 / 'docs' / 'report.json')
_STRIP = re.compile(r'\\d|%K%P|%K|%P|%N')
_QUOTES = '“”「」『』\'"'

# 场景噪声底：CCC0000（已实机验证通过）实测仍有 8 行对不上，是删节改写/错译导致的对位噪声
NOISE_FLOOR = 8

# (还原脚本, 源 WSC 名, 参与承载该场景的 Steam 脚本)
SCENES = [
    ('CNR0001_en.ws2', 'CCA0025C', ['CCA0025C_en.ws2']),
    ('CNR0002_en.ws2', 'CCB1014C', ['CCB1014C_en.ws2']),
    ('CNR0003_en.ws2', 'CCB2013', ['CCB2013_en.ws2']),
    ('CNR0004_en.ws2', 'CCB2101', ['CCB2101_en.ws2']),
    ('CNR0005_en.ws2', 'CCC0000', ['CCC0000_en.ws2']),
    ('CNR0006_en.ws2', 'CCC3027', ['CCC3027_en.ws2']),
    ('CNR0007_en.ws2', 'CCC4022', ['CCC4022_en.ws2']),
    ('CNR0008_en.ws2', 'CCD0022A', ['CCD0022A_en.ws2']),
    ('CNR00009_en.ws2', 'CCD1001', ['CCD1001A_en.ws2', 'CCD1001B_en.ws2',
                                    'CCD1001C_en.ws2', 'CCD1001D_en.ws2']),
    ('CNR00010_en.ws2', 'CCD4003', ['CCD4003A_en.ws2']),
    ('CNR00011_en.ws2', 'CCD5001', ['CCD5001A_en.ws2', 'CCD5001B_en.ws2']),
]


def norm(t):
    t = _STRIP.sub('', t)
    return ''.join(c for c in t if c not in _QUOTES and not c.isspace())


def load_rio(path):
    return {n.decode('utf-16-le').upper(): v for n, v in arcbuild.read_raw(path)}


def script_display(mem, name):
    """按 lng 位置返回每个 `14` 对话的归一化显示文本（%N/%P 控制句记为空串）。"""
    key = name.upper()
    if key not in mem:
        return None
    dl = [i for i in ws2disasm.disassemble(ws2.decode(mem[key])) if i.opcode == 0x14]
    lk = key[:-4] + '.LNG'
    L = lng.parse_lng(mem[lk]) if lk in mem else []
    out = []
    for n, i in enumerate(dl):
        if i.fields['text'].strip() in ('%N', '%P'):
            out.append('')
        else:
            out.append(norm(L[n]) if n < len(L) else '')
    return out


def align(texts, ccs_n):
    import difflib
    out = {}
    for i, j, n in difflib.SequenceMatcher(None, ccs_n, texts,
                                           autojunk=False).get_matching_blocks():
        for d in range(n):
            out[j + d] = i + d
    return out


def runs_of(missing):
    runs = []
    for i in missing:
        if runs and runs[-1][1] == i - 1:
            runs[-1][1] = i
        else:
            runs.append([i, i])
    return runs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--res303', action='store_true', help='打印 Res303 报告的对照值')
    args = ap.parse_args()

    mem = load_rio(ROOT / 'asset' / 'Rio.arc')
    ref = {}
    if args.res303 and RES303_REPORT.exists():
        d = json.loads(RES303_REPORT.read_text(encoding='utf-8'))
        for s in d.get('scenes', []):
            k = s['cnr'].upper()
            k = k[:-3] if k.endswith('_EN') else k
            ref[k] = (s.get('source_range'), s.get('integration_status'),
                                     s.get('reason_codes'))

    print('%-18s %-9s %-14s %-14s %s' % ('还原脚本', '源行数', 'CNR 覆盖区间',
                                          '宿主对位区间', '宿主∪CNR 未覆盖'))
    print('-' * 108)
    bad = []
    for cnr, stem, hosts in SCENES:
        ccs = lng.parse_ccs(CCS_DIR / (stem + '.CCS'))
        ccs_n = [norm(lng.strip_speaker_wrap(ccs[k])) if ccs.get(k) else ''
                 for k in range(1, len(ccs) + 1)]
        N = len(ccs)
        cnr_set = set(align(script_display(mem, cnr), ccs_n).values())
        hsets = {}
        for h in hosts:
            t = script_display(mem, h)
            if t is not None:
                hsets[h] = set(align(t, ccs_n).values())
        union = set(cnr_set)
        for s in hsets.values():
            union |= s
        miss = [i for i in range(N) if i not in union]
        hspan = ('%d..%d' % (min(min(s) for s in hsets.values()),
                             max(max(s) for s in hsets.values()))) if hsets else '-'
        cover = '到场景末 ✅' if max(cnr_set) == N - 1 else '差 %d 行 ❌' % (N - 1 - max(cnr_set))
        print('%-18s %-9d %-14s %-14s %d 行 %s'
              % (cnr, N, '%d..%d %s' % (min(cnr_set), max(cnr_set), cover),
                 hspan, len(miss),
                 sorted(runs_of(miss), key=lambda r: r[0] - r[1])[:4]))
        if len(miss) > NOISE_FLOOR:
            bad.append((cnr, len(miss)))
        ckey = cnr.upper()[:-4]
        ckey = ckey[:-3] if ckey.endswith('_EN') else ckey
        if args.res303 and ckey in ref:
            print('        Res303: source_range=%s  status=%s'
                  % (ref[ckey][0], ref[ckey][1]))
            for c in ref[ckey][2] or []:
                print('          · %s' % c)

    print()
    print('噪声底 = %d 行（CCC0000 基准实测），超过即判为真缺口。' % NOISE_FLOOR)
    print('需要补全还原脚本（或改走插入式）的场景：%s'
          % (', '.join('%s(%d 行)' % b for b in bad) if bad else '无'))


if __name__ == '__main__':
    main()
