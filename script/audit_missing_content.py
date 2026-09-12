"""核对"原版场景里没有被还原脚本覆盖的那几段"内容到底在哪。

结论导向：原版场景的内容是**分散**在 宿主脚本 / 还原脚本 / 后继脚本 里的
（CCC0000 就是"宿主 0..194 + 还原脚本 195..363"），所以只看还原脚本的覆盖率
不能判断"漏没漏"。

方法（三步）：

  1. 以民汉 CCS 为源，对每个缺失区间取若干**长句**（归一化长度 ≥ 14，短句没有区分度）；
  2. 每句在**全档案 lng**（两版 lng 都是中文）里投票"最像哪个脚本"；
  3. 用**负对照**（还原脚本已覆盖的区间）验证方法有效——负对照应当在所有场景都投给
     还原脚本自己。

注意：本作是轮回结构，同一段文本会在多条线复用，所以必须看投票的**归属脚本**，
不能只看匹配率高低。

运行较慢（约数分钟，逐句与全档案比对）。用法：python script/audit_missing_content.py
"""
import difflib
import io
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tool import arcbuild, lng, ws2, ws2disasm, wsc  # noqa: E402

if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

SCENES = [
    ('CNR0001_en.ws2', 'CCA0025C_en.ws2', 'CCA0025C', ['CCA0029_EN', 'CCA0028_EN', 'CCA0027_EN']),
    ('CNR0002_en.ws2', 'CCB1014C_en.ws2', 'CCB1014C', ['CCB0020_EN', 'CCB1015_EN']),
    ('CNR0003_en.ws2', 'CCB2013_en.ws2', 'CCB2013', ['CCB2014_EN']),
    ('CNR0004_en.ws2', 'CCB2101_en.ws2', 'CCB2101', ['CCB2019_EN']),
    ('CNR0005_en.ws2', 'CCC0000_en.ws2', 'CCC0000', ['CCC0001_EN', 'CCC0002_EN']),
    ('CNR0006_en.ws2', 'CCC3027_en.ws2', 'CCC3027', ['CCC3028_EN']),
    ('CNR0007_en.ws2', 'CCC4022_en.ws2', 'CCC4022', ['CCC4023_EN']),
    ('CNR0008_en.ws2', 'CCD0022A_en.ws2', 'CCD0022A', ['CCD0000_EN', 'CCD0023_EN']),
    ('CNR00009_en.ws2', 'CCD1001B_en.ws2', 'CCD1001', ['CCD1001C_EN', 'CCD1001A_EN', 'CCD1001D_EN']),
    ('CNR00010_en.ws2', 'CCD4003A_en.ws2', 'CCD4003', ['CCD0023A_EN', 'CCD4003B_EN']),
    ('CNR00011_en.ws2', 'CCD5001A_en.ws2', 'CCD5001', ['CCD5001B_EN']),
]
CCS_DIR = Path('../cross-channel_chinese-localization_project/Scripts/20150412')
RIO = {n.decode('utf-16-le').upper(): v for n, v in arcbuild.read_raw('asset/Rio.arc')}
_STRIP = re.compile(r'%[A-Z]|\\d')
_QUOTES = '「」『』“”"\''


def norm(t):
    t = _STRIP.sub('', t)
    return ''.join(c for c in t if not c.isspace() and c not in _QUOTES)


LNG = {}
for name, data in RIO.items():
    if name.endswith('.LNG'):
        try:
            LNG[name[:-4]] = [norm_t for norm_t in
                              (norm(t) for t in lng.parse_lng(data))]
        except Exception:
            pass
ALL = [t for v in LNG.values() for t in v if len(t) >= 4]


def score(sample, pool):
    """sample 里每一行在 pool 中的最佳匹配率平均。"""
    tot = 0.0
    n = 0
    for s in sample:
        best = 0.0
        for t in pool:
            if abs(len(t) - len(s)) > max(6, len(s) * 0.6):
                continue
            r = difflib.SequenceMatcher(None, s, t).ratio()
            if r > best:
                best = r
                if best > 0.95:
                    break
        tot += best
        n += 1
    return (tot / n if n else 0.0), n


def best_script(s, min_len=10):
    """这一行最像哪个脚本的 lng（长句才有区分度）。"""
    if len(s) < min_len:
        return None, 0.0
    best, bs = 0.0, None
    for script, pool in LNG.items():
        for t in pool:
            if abs(len(t) - len(s)) > max(8, len(s) * 0.5):
                continue
            r = difflib.SequenceMatcher(None, s, t).ratio()
            if r > best:
                best, bs = r, script
                if best > 0.97:
                    return bs, best
    return bs, best


print('%-9s %-9s %-6s %-5s | %-34s | %s' % (
    '还原脚本', '原版场景', '区间', '长句', '最像的脚本（票数）', '判定'))
print('-' * 118)
for cnr, host, stem, succs in SCENES:
    wi = wsc.disassemble(open('tmp/corpus/wsc/%s.WSC' % stem, 'rb').read())
    n = 0
    vline = []
    for i in wi:
        if i.opcode in (0x41, 0x42):
            n += 1
        elif i.opcode == 0x23:
            vline.append(n)
    total = n
    ci = ws2disasm.disassemble(ws2.decode(RIO[cnr.upper()]))
    vc = []
    for i in ci:
        if i.opcode == 0x2e:
            vc.append(i.fields['file'].upper())
        elif i.opcode == 0x28 and i.fields['slot'].startswith('se'):
            vc.append(i.fields['file'].upper())
    vn = [i.fields['name'].upper() + '.OGG' for i in wi if i.opcode == 0x23]
    c0 = next((s for s in range(len(vn) - len(vc) + 1) if vn[s:s + len(vc)] == vc), None)
    cdlg = sum(1 for i in ci if i.opcode == 0x14)
    if c0 is None:
        continue
    lo = vline[c0] if c0 < len(vline) else total
    covered = min(cdlg, total - lo)
    ccs = lng.parse_ccs(CCS_DIR / (stem + '.CCS'))
    hostname = host[:-4].upper()

    for label, rng in (('前段', list(range(0, lo))),
                       ('后段', list(range(lo + covered, total))),
                       ('负对照(已覆盖)', list(range(lo, lo + covered)))):
        if not rng:
            continue
        votes = {}
        nvote = 0
        step = max(1, len(rng) // 25)
        for ln in rng[::step]:
            src = ccs.get(ln + 1)
            if not src:
                continue
            s = norm(lng.strip_speaker_wrap(src))
            bs, r = best_script(s)
            if bs and r >= 0.70:
                votes[bs] = votes.get(bs, 0) + 1
                nvote += 1
        top = sorted(votes.items(), key=lambda kv: -kv[1])[:3]
        isneg = label.startswith('负对照')
        if isneg:
            mark = ''
        else:
            win = top[0][0] if top else None
            if win and win == hostname:
                mark = '内容在宿主 ✅'
            elif win and win in succs:
                mark = '内容在后继脚本(%s) ✅' % win
            elif win:
                mark = '落在 %s（非宿主/后继）' % win
            else:
                mark = '无匹配 → 可能真缺 ⚠'
        print('%-9s %-9s %-13s %-5d | %-34s | %s' % (
            cnr.replace('_en.ws2', ''), stem, label, nvote, str(top), mark))

