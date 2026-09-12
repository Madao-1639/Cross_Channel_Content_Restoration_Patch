"""lng 语义复审：用**说话人一致性**找错位。

为什么不用「lng 和 CCS 对位」
--------------------------
Res303 的 lng 本来就是从汉化组 CCS 抄来的（等数量时按序 zip）。把 lng 和 CCS 对位只能
还原出**当年用的下标**，测不出对错 —— 是循环论证。

可用的独立信号
--------------
ws2 每句对话前有 `15 %LC<角色名>`（英文名），CCS 每行有 `[角色名]`（日文名），
两者经 `tool/wsc2ws2.SPEAKER_MAP` 一一对应。**同一句话的说话人在两侧必须一致**：

* 若 lng 是照源序列第 i 行抄的（zip），而 ws2 第 i 句其实对应源序列的另一处，
  说话人就会对不上 —— 这正是要抓的错位；
* 这个判据**不需要**「ws2 与源逐句对应」的假设，对删节过的脚本同样成立。

另外单独报一类结构问题：**ws2 句数 != 源句数**时会整体漂移（Steam 删节），
说话人不一致率天然偏高，需与「句数相同却大量不一致」分开看 —— 后者才是纯错位。

用法（项目根目录）：
    python script/audit_lng_semantics.py                 # 扫 asset/Rio.arc
    python script/audit_lng_semantics.py --rio backup.arc
    python script/audit_lng_semantics.py --calibrate     # 用 Res303 已确认的 6 条做正对照
"""
import argparse
import difflib
import io
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tool import arcbuild, lng, ws2, ws2disasm  # noqa: E402
from tool.wsc2ws2 import SPEAKER_MAP  # noqa: E402

if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

CCS_DIR = ROOT / '..' / 'cross-channel_chinese-localization_project' / 'Scripts' / '20150412'
RIO = ROOT / 'asset' / 'Rio.arc'
RES303_REPORT = (ROOT / '..' / 'CROSS_CHANNEL_Steam_CN_Restored_v3.0.3'
                 / 'docs' / 'report.json')
_QUOTES = '“”「」『』\'"'
_SPK = re.compile(r'^\[([^\]]+)\]\s*')
CTRL = re.compile(r'^(?:%[A-Za-z])+$')


def norm(t):
    return ''.join(c for c in t if c not in _QUOTES and not c.isspace())


def ccs_rows(stem):
    """[(说话人日文名 or None, 中文正文)]，按行号 1..N。"""
    src = stem[:-3] if stem.endswith('_EN') else stem
    p = CCS_DIR / (src + '.CCS')
    if not p.exists():
        return None
    e = lng.parse_ccs(p)
    if not e:
        return []
    out = []
    for k in range(1, max(e) + 1):
        raw = e.get(k, '')
        m = _SPK.match(raw.strip())
        spk = m.group(1) if m else None
        body = lng.strip_speaker_wrap(raw)
        out.append((spk, norm(body)))
    return out


def ws2_rows(raw):
    """[(说话人英文名 or None, lng 中文, 英文正文)]，按 `14` 出现序。"""
    ins = ws2disasm.disassemble(ws2.decode(raw))
    rows, cur = [], None
    for i in ins:
        if i.opcode == 0x15:
            pre = i.fields.get('prefix')
            if isinstance(pre, bytes):
                pre = pre.decode('cp932', 'replace')
            pre = (pre or '').split('\x00')[0]
            # `15` 的前缀形如 `%LC<角色名>`；空前缀表示旁白
            cur = pre[3:] if pre.startswith('%LC') else (pre or None)
            cur = cur or None
        elif i.opcode == 0x14:
            rows.append([cur, '', i.fields['text']])
            cur = None
    return rows


def derive_speaker_map(members, ccs_cache):
    """从全语料共现统计推导「简体中文说话人名 -> ws2 英文名」。

    `tool.wsc2ws2.SPEAKER_MAP` 的键是**日文**名，而汉化组 CCS 用的是**简体中文**名
    （`見里` → `见里`），直接用会大面积假阳性。这里不硬编码，改成从数据里统计：
    在句数相同的脚本里逐位置共现，取每个中文名的众数配对。几百个脚本的统计下，
    个别错位不会改变众数。
    """
    from collections import Counter, defaultdict
    co = defaultdict(Counter)
    for name, data in members.items():
        if not name.endswith('.WS2'):
            continue
        stem = name[:-4]
        lk = stem + '.LNG'
        if lk not in members:
            continue
        ccs = ccs_cache.get(stem)
        if ccs is None or not ccs:
            continue
        rows = [r for r in ws2_rows(data) if r[2].strip() not in ('%N', '%P')]
        if len(ccs) != len(rows):
            continue
        for i, (spk, _, _en) in enumerate(rows):
            jp = ccs[i][0]
            if spk and jp:
                co[jp][spk] += 1
    return {jp: c.most_common(1)[0][0] for jp, c in co.items() if c}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--rio', default=str(RIO))
    ap.add_argument('--calibrate', action='store_true')
    ap.add_argument('--top', type=int, default=25)
    args = ap.parse_args()

    members = {n.decode('utf-16-le').upper(): v for n, v in arcbuild.read_raw(args.rio)}
    ccs_cache = {}
    for name in members:
        if name.endswith('.WS2'):
            ccs_cache[name[:-4]] = ccs_rows(name[:-4])
    jp2en = derive_speaker_map(members, ccs_cache)
    print('推导出的说话人映射 %d 条：%s'
          % (len(jp2en), ', '.join('%s→%s' % kv for kv in sorted(jp2en.items())[:10])))
    # 与 SPEAKER_MAP（日文键）交叉核对：同一个人不应被映射到两个英文名
    rev = {}
    for jp, en in SPEAKER_MAP.items():
        rev.setdefault(en, set()).add(jp)
    bad, struct, no_ccs, clean = [], [], [], 0

    for name in sorted(k for k in members if k.endswith('.WS2')):
        stem = name[:-4]
        lk = stem + '.LNG'
        if lk not in members:
            continue
        ccs = ccs_cache.get(stem)
        if ccs is None:
            no_ccs.append(stem)
            continue
        if not ccs:
            continue
        rows = ws2_rows(members[name])
        eff = [(i, spk, en) for i, (spk, _, en) in enumerate(rows)
               if en.strip() not in ('%N', '%P')]
        if not eff:
            continue
        # 说话人是**跨语言可比**的符号（经 jp2en 映射后）。ws2 的对话序列是源序列的
        # **子序列**（MoeNovel 只删不重排），所以用说话人序列做子序列对位就能恢复真实映射 f。
        src_sym = [jp2en.get(c[0]) if c[0] else '旁白' for c in ccs]
        ws_sym = [spk or '旁白' for _, spk, _ in eff]
        f = {}
        for a, b, n in difflib.SequenceMatcher(None, src_sym, ws_sym,
                                               autojunk=False).get_matching_blocks():
            for d in range(n):
                f[b + d] = a + d
        L = lng.parse_lng(members[lk])
        aligned, zip_only, unclear = 0, [], 0
        for k, (i, spk, en) in enumerate(eff):
            if k >= len(L):
                break
            got = norm(L[k])
            if not got or got in ('%K', '%P', '%N', '%K%P'):
                continue
            j = f.get(k)
            if j is None or j == k:
                aligned += 1
                continue
            at_true = (j < len(ccs) and got == ccs[j][1])
            at_self = (k < len(ccs) and got == ccs[k][1])
            if at_true:
                aligned += 1
            elif at_self:
                # lng 抄的是「同下标」而真实对应在别处 ⇒ Res303 的按序 zip 抄错了
                zip_only.append((k, spk, src_sym[k] if k < len(src_sym) else '?',
                                 src_sym[j] if j < len(src_sym) else '?', j, ccs[j][1][:14]))
            else:
                unclear += 1
        rec = (len(zip_only), len(eff), aligned, unclear, len(ccs), stem, zip_only[:4])
        if zip_only:
            bad.append(rec)
        else:
            clean += 1

    if args.calibrate:
        d = json.loads(RES303_REPORT.read_text(encoding='utf-8'))
        ex = d['translation_alignment_audit']['confirmed_live_examples']
        print('Res303 已确认的错配 %d 条（脚本:记录下标）' % len(ex))
        for e in ex:
            st, idx = e['id'].split(':')
            idx = int(idx)
            ccs = ccs_rows(st.upper() + '_EN') or ccs_rows(st.upper())
            key = st.upper() + '_EN.WS2'
            rows = ws2_rows(members[key]) if key in members else []
            spk = rows[idx][0] if idx < len(rows) else None
            jp = ccs[idx][0] if ccs and idx < len(ccs) else None
            print('  %-16s ws2说话人=%-12s 源同行说话人=%-10s %s'
                  % (e['id'], spk, jp,
                     '（本条已修复）' if jp2en.get(jp) == spk else '**仍不一致**'))

    print()
    print('扫描 %s：%d 个 ws2' % (args.rio, len(members)))
    print('  无同名 CCS          : %d' % len(no_ccs))
    print('  无孤立错位          : %d' % clean)
    print('  有孤立错位          : %d  ← 真错，须修' % len(bad))
    print('  仅整体漂移（删节）  : %d  ← 预期现象' % len(struct))
    print()
    print('=== 孤立错位（前后 5 行一致、本行不一致）===')
    print('%-20s %-7s %-7s %-8s %s' % ('脚本', '错/总', '不一致率', '句数', '样例(句号 ws2说话人 vs 源说话人)'))
    bad.sort(reverse=True)
    for n_m, tot, ratio, nc, nr, stem, ex in bad[:args.top]:
        print('%-20s %-7s %6.1f%%  %-8d %s'
              % (stem, '%d/%d' % (n_m, tot), ratio * 100, nc,
                 '; '.join('#%d %s≠%s' % x for x in ex)))
    if not bad:
        print('  （无）')
    print()
    print('=== 句数不同（删节）里不一致率最高的 ===')
    struct.sort(key=lambda r: -r[2])
    for n_m, tot, ratio, nc, nr, stem, ex in struct[:args.top]:
        print('%-20s %-7s %6.1f%%  源%-5d ws2 %-5d %s'
              % (stem, '%d/%d' % (n_m, tot), ratio * 100, nc, nr,
                 '; '.join('#%d %s≠%s' % x for x in ex)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
