# -*- coding: utf-8 -*-
"""生成 `resource/voice_plan.json`：**给「原版该行有配音、产物却没挂」的格补挂 `2e`**。

用户口径（2026-09-21）：
  · **原版那一行有语音，播放时就必须有语音** —— 核不上只说明没找对，不是静音的借口；
  · 语音与台词**严格绑定**（两版都是「录音紧邻其台词」）⇒ 录音唯一确定那句话；
  · 时长指纹只用来**找候选**；定案看**台词**。

选段规则（依次）：
  1. 候选里**在 Steam 侧绑定的那一格，其显示中文与本格中文逐字相同** ⇒ 取它（高置信）；
  2. 否则若同长候选**唯一** ⇒ 取它；
  3. 否则取"中文最像"的那个，并标 `ambiguous` 待人工复核；
  4. 同长候选为 0 ⇒ 录音被 Steam 删了 ⇒ 标 `deleted`，**需先从原版导入**。

键用**表的 `k`**（= 写盘器 `rebuild` 的 `si`，与 `vc` 同一空间）。
"""
import collections
import json
import re
import struct
import sys
from pathlib import Path

ROOT = Path('.').resolve()
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'script'))
sys.stdout.reconfigure(encoding='utf-8')
from tool import arcbuild, ws2, ws2disasm, lng as lngmod, wsc   # noqa: E402
from tool.wsc2ws2 import decrypt_wsc                            # noqa: E402
import apply_text_map as ATM                                     # noqa: E402

WSC = ROOT / 'resource' / 'corpus' / 'wsc'
MEM = {n.decode('utf-16-le').upper(): d for n, d in arcbuild.read_raw(ROOT / 'asset' / 'Rio.arc')}
OUT = ROOT / 'resource' / 'voice_plan.json'


# ↓ 原在 `textfix_voice_id.py` 里；判定链归档时把它内联进来（本文件只用到这一个函数）。
def ogg_info(b):
    """→ (采样率, 时长秒) 或 (None, None)。末页 granule ÷ 采样率 ＝ 时长（与编码无关）。"""
    if b[:4] != b'OggS':
        return None, None
    nseg = b[26]
    p = 27 + nseg
    if b[p + 1:p + 7] != b'vorbis':
        return None, None
    rate = struct.unpack_from('<I', b, p + 12)[0]
    last, i = 0, 0
    while True:
        j = b.find(b'OggS', i)
        if j < 0:
            break
        if j + 27 > len(b):
            break
        g = struct.unpack_from('<q', b, j + 6)[0]
        if g >= 0:
            last = g
        i = j + 4
    if not rate:
        return None, None
    return rate, last / rate


def dsec(b):
    _, s = ogg_info(b)
    return None if s is None else round(s, 3)


def norm(s):
    return re.sub(r'[%A-Za-z0-9\s“”‘’「」『』。、，！？…—・\n]', '', s or '')


def oname(b):
    try:
        return b.decode('ascii').upper()
    except UnicodeDecodeError:
        return b.decode('shift_jis', 'replace').upper()


# 产物侧：录音 → 绑定的格（含该格中文）；录音 → 时长
bind = collections.defaultdict(list)
for stem in sorted(ATM.TM):
    L = lngmod.parse_lng(MEM.get(stem + '.LNG', b''))
    vo, k = None, -1
    for i in ws2disasm.disassemble(ws2.decode(MEM[stem + '.WS2'])):
        if i.opcode == 0x2e:
            vo = (i.fields.get('file') or '').upper()
        elif i.opcode == 0x14:
            k += 1
            if vo:
                bind[vo].append(L[k] if k < len(L) else '')
            vo = None
        elif i.opcode == 0x0f:
            k += len(i.fields['entries'])
            vo = None
dur2names = collections.defaultdict(list)
for n, d in arcbuild.read_raw(ROOT / 'asset' / 'Voice.arc'):
    s = dsec(d)
    if s is not None:
        dur2names[s].append(n.decode('utf-16-le').upper())
orig_sec, orig_row = {}, {}
for n, d in arcbuild.read_old_arc(ROOT.parent / 'CROSS_CHANNEL_Original' / 'Voice.arc'):
    k = oname(n)
    if k.endswith('.OGG'):
        orig_sec[k] = dsec(d)


def ov_names(stem):
    """原版：行 → 录音名（大写 .OGG）。"""
    p = WSC / (stem + '.WSC')
    if not p.exists():
        return {}
    raw = p.read_bytes()
    try:
        ins = wsc.disassemble(raw)
    except Exception:
        ins = wsc.disassemble(decrypt_wsc(raw))
    out, d, pend = {}, -1, None
    for i in ins:
        if i.opcode == 0x23:
            pend = (i.fields.get('name') or '').split('.')[0]
        elif i.opcode in (0x41, 0x42):
            d += 1
            if pend:
                out[d + 1] = pend.upper() + '.OGG'
                pend = None
    return out


def main():
    plan, stat = {}, collections.Counter()
    for stem, sc in sorted(ATM.TM.items()):
        ovn = ov_names(sc['ccs'])
        if not ovn:
            continue
        texts, _ins, _dr, _nm, rows, _st = ATM.expand_script(sc)
        cv = ATM.cur_voice(ws2disasm.disassemble(ws2.decode(MEM[stem + '.WS2'])), frozenset())
        rc = collections.Counter(rows.values())
        ent = {}
        for k, r in sorted(rows.items()):
            if rc[r] > 1 or r not in ovn or cv.get(k):
                continue                       # 切分格 / 原版无配音 / 已有语音 ⇒ 不动
            nm = ovn[r]
            mine = norm(texts.get(k, ''))
            cands = dur2names.get(orig_sec.get(nm), []) if orig_sec.get(nm) is not None else []
            if not cands:
                ent[str(k)] = {'f': None, 'why': 'deleted-需导入', 'orig': nm}
                stat['需导入'] += 1
                continue
            exact = [c for c in cands if any(norm(z) == mine and mine for z in bind.get(c, []))]
            if exact:
                pick, why = exact[0], 'verified'
                stat['台词核对通过'] += 1
            elif len(cands) == 1:
                pick, why = cands[0], 'unique'
                stat['同长唯一'] += 1
            else:
                def sim(c):
                    return max((len(set(norm(z)) & set(mine)) / max(1, len(set(norm(z)) | set(mine)))
                                for z in bind.get(c, []) if z), default=0)
                pick, why = max(cands, key=sim), 'ambiguous'
                stat['同长多解·待复核'] += 1
            ent[str(k)] = {'f': pick, 'why': why, 'orig': nm}
        if ent:
            plan[stem] = ent

    OUT.write_text(json.dumps({'_说明': __doc__.split('选段规则')[0].strip(), **plan},
                              ensure_ascii=False, indent=0), encoding='utf-8')
    print('写出 %s：%d 个脚本' % (OUT, len(plan)))
    for k, v in stat.most_common():
        print('   %-18s %d' % (k, v))


if __name__ == '__main__':
    main()
