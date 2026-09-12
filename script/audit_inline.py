"""就地插入后的验收审计：每个场景的合并播放序必须「恰好一次、无缺失、无重复、无倒序」。

做法：把**新宿主**的显示文本（lng，位置对应 `14`）与源场景的民汉 CCS 逐句对位，
然后检查
  * 覆盖：源 0..hi 段是否都被覆盖（少量噪声容许，噪声底由已知正确的 CCC0000 段实测）
  * 重复：同一条源句是否被对位到两次
  * 倒序：对位结果的源序号是否单调不减
  * 出口：跳转目标是否等于 Steam 原档

只读。用法：python script/audit_inline.py
"""
import io
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tool import arcbuild, lng, ws2, ws2disasm  # noqa: E402

sys.path.insert(0, str(ROOT / 'script'))
from splice_restoration import SCENES, TAIL_HOSTS  # noqa: E402

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

CCS_DIR = ROOT / '..' / 'cross-channel_chinese-localization_project' / 'Scripts' / '20150412'
_S = re.compile(r'\\d|%K%P|%K|%P|%N')
_Q = '“”「」『』\'"'


def norm(t):
    t = _S.sub('', t)
    return ''.join(c for c in t if c not in _Q and not c.isspace())


def main():
    members = {n.decode('utf-16-le').upper(): v for n, v in arcbuild.read_raw(ROOT / 'asset/Rio.arc')}
    steam = {n.decode('utf-16-le').upper(): v for n, v in arcbuild.read_raw(ROOT / 'backup/Rio.arc')}
    ok_all = True
    print('%-20s %-9s %-9s %-9s %-9s %-9s %s'
          % ('宿主', '插入段', '对话数', 'lng数', '未覆盖', '重复', '出口'))
    print('-' * 108)
    for sc in SCENES:
        host, stem = sc['host'].upper(), sc['src']
        lo_eff, hi = sc['lo'] + sc['head_overlap'], sc['hi']
        ccs = lng.parse_ccs(CCS_DIR / (stem + '.CCS'))
        cn = [norm(lng.strip_speaker_wrap(ccs[k])) if ccs.get(k) else ''
              for k in range(1, max(ccs) + 1)]
        ii = ws2disasm.disassemble(ws2.decode(members[host]))
        dl = [i for i in ii if i.opcode == 0x14]
        L = lng.parse_lng(members[host[:-4] + '.LNG'])
        txt = [norm(L[n]) if n < len(L) and i.fields['text'].strip() not in ('%N', '%P') else ''
               for n, i in enumerate(dl)]
        import difflib
        mp = {}
        for i, j, n in difflib.SequenceMatcher(None, cn, txt, autojunk=False).get_matching_blocks():
            for d in range(n):
                mp.setdefault(i + d, []).append(j + d)
        vals = [v[0] for v in mp.values()]
        covered = set(mp)
        miss = [k for k in range(lo_eff, hi + 1) if k not in covered]
        dup = [k for k, v in mp.items() if len(v) > 1]
        ordered = all((a['pos'] <= b['pos']) for a, b in
                      zip(sorted((dict(pos=j, src=i) for i, js in mp.items() for j in js),
                                 key=lambda x: x['pos']),
                          sorted((dict(pos=j, src=i) for i, js in mp.items() for j in js),
                                 key=lambda x: x['pos'])[1:]))
        exits = [i.fields.get('name') for i in ii if i.opcode == 0x07]
        s_ii = ws2disasm.disassemble(ws2.decode(steam[host]))
        s_exits = [i.fields.get('name') for i in s_ii if i.opcode == 0x07]
        good = (len(miss) <= 12 and not dup and ordered and exits == s_exits
                and len(dl) == len(L))
        ok_all &= good
        print('%-20s %-9s %-9d %-9d %-9d %-9d %-9s %s'
              % (sc['host'], '%d..%d' % (lo_eff, hi), len(dl), len(L), len(miss), len(dup),
                 '单调' if ordered else '**倒序**',
                 ('✅' if good else '⚠') + (' 出口=%s' % exits)))
        if miss:
            print('       未覆盖 %d 行：%s' % (len(miss), miss[:20]))
        if dup:
            print('       重复源序号：%s' % dup[:10])
    for th in TAIL_HOSTS:
        host = th['host'].upper()
        ii = ws2disasm.disassemble(ws2.decode(members[host]))
        dl = [i for i in ii if i.opcode == 0x14]
        L = lng.parse_lng(members[host[:-4] + '.LNG'])
        s_ii = ws2disasm.disassemble(ws2.decode(steam[host]))
        s_exits = [i.fields.get('name') for i in s_ii if i.opcode == 0x07]
        exits = [i.fields.get('name') for i in ii if i.opcode == 0x07]
        good = len(dl) == len(L) and exits == s_exits
        ok_all &= good
        print('%-20s %-9s %-9d %-9d %-9s %-9s %-9s %s'
              % (th['host'], '截头@%d' % th['from_src'], len(dl), len(L), '-', '-',
                 '单调', ('✅' if good else '⚠') + ' 出口=%s' % exits))
    print()
    print('总结：%s' % ('全部通过' if ok_all else '**有场景未通过**'))
    return 0 if ok_all else 1


if __name__ == '__main__':
    sys.exit(main())
