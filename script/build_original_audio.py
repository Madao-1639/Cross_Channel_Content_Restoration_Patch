# -*- coding: utf-8 -*-
"""生成 `resource/original_audio.json`：**原版 WSC 里每一行挂了哪些"演出属性"**。

范围**只含行级演出属性**（用户 2026-09-20 界定）：
  - `0x23` 语音
  - `0x00` SE（音效）
**不含**：立绘/表情（`0x47`/`0x48`/`0x49`/`0x4a`）、BGM（`0x0a`）、计时器/标志/跳转/背景
—— 那些属场景与状态，**跟随 Steam 版**（与既有方针「不改演出时序/不加 BGM」一致）。

行号口径与 CCS 一致：`行 = 对话序 + 1`。
产出：`{ccs名: {"voice": [行…], "se": [行…]}}`
"""
import json
import sys
from pathlib import Path

ROOT = Path('.').resolve()
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding='utf-8')
from tool import wsc                                     # noqa: E402
from tool.wsc2ws2 import decrypt_wsc                     # noqa: E402

WSC = ROOT / 'resource' / 'corpus' / 'wsc'
OUT = ROOT / 'resource' / 'original_audio.json'
CCS = ROOT.parent / 'cross-channel_chinese-localization_project' / 'Scripts' / '20150412'

res = {}
n = 0
for p in sorted(WSC.glob('*.WSC')):
    if not (CCS / (p.stem + '.CCS')).exists():
        continue
    raw = p.read_bytes()
    try:
        ins = wsc.disassemble(raw)
    except Exception:
        ins = wsc.disassemble(decrypt_wsc(raw))
    d, pend = -1, set()
    voice, se = [], []
    for i in ins:
        if i.opcode in (0x23, 0x00):          # 语音 / SE —— 属于其后的那行
            pend.add(i.opcode)
        elif i.opcode in (0x41, 0x42):
            d += 1
            row = d + 1
            if 0x23 in pend:
                voice.append(row)
            if 0x00 in pend:
                se.append(row)
            pend = set()
    if voice or se:
        res[p.stem] = {'voice': voice, 'se': se}
        n += 1

OUT.write_text(json.dumps({'_说明': __doc__.split('范围')[0].strip(), **res},
                          ensure_ascii=False, indent=0), encoding='utf-8')
nv = sum(len(v['voice']) for v in res.values())
ns = sum(len(v['se']) for v in res.values())
print('写出 %s：%d 个脚本；原版有语音的行 %d、有 SE 的行 %d' % (OUT, n, nv, ns))
