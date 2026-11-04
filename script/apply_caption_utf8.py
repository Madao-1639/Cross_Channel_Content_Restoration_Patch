# -*- coding: utf-8 -*-
"""把 UTF-8 中文字幕写进**语言包里的 ws2 副本**，并重生成 hook 白名单表。

为什么需要副本：`0x8f`（语音 + 字幕行）的文本按 CP932 解码、不参与 lng 池，简体字写不进去；
只有语言包里的副本承载 UTF-8 文本（引擎按成员覆盖，英文模式读根层原档）。
DLL 侧用白名单（`resource/caption_utf8.json`）逐字节匹配后才改按 UTF-8 解码。

输入：`resource/captions_zh.json`
    { "edits": [ {"script": "CC_30A_EN", "old": "<ws2 里的原文>", "new": "<中文>"} ] }
    `old` 用于在该脚本里唯一定位（0x8f 文本比较时按 cp932 解码后比较）。

输出：
    * `asset/zh-CN/Rio.arc` 里该脚本的 ws2 副本（只改文本字节，不动 id/尾表/指令数）；
    * `resource/caption_utf8.json`（白名单唯一来源，由**实际写入的文本**生成，避免二者不一致）。

用法：`python script/apply_caption_utf8.py [--write]`
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tool import arcbuild, layers, ws2, ws2disasm  # noqa: E402

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

TABLE = ROOT / 'resource' / 'captions_zh.json'
WHITELIST = ROOT / 'resource' / 'caption_utf8.json'


def caption_spans(raw):
    """→ [(start, end, text_bytes)]：所有 0x8f/0x90 的文本区间。"""
    out = []
    for o in ws2disasm.disassemble(raw):
        if o.opcode == 0x8f:
            p = o.offset + 1
            for _ in range(2):
                p = raw.index(bytes([0]), p) + 1
            p += 1
            out.append((p, raw.index(bytes([0]), p)))
        elif o.opcode == 0x90:
            p = o.offset + 1
            out.append((p, raw.index(bytes([0]), p)))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--write', action='store_true')
    args = ap.parse_args(argv)

    if not TABLE.exists():
        print('[跳过] 没有 %s（尚无字幕译文）' % TABLE.name)
        return 0
    doc = json.loads(TABLE.read_text(encoding='utf-8'))
    by_script = {}
    for e in doc.get('edits', []):
        by_script.setdefault(e['script'].upper(), []).append(e)
    if not by_script:
        print('[跳过] 译表为空')
        return 0

    pack_path = layers.pack_arc('Rio.arc')
    pack = {n.decode('utf-16-le').upper(): [n, d] for n, d in arcbuild.read_raw(pack_path)}
    base = {n.decode('utf-16-le').upper(): d for n, d in arcbuild.read_raw(layers.base_arc('Rio.arc'))}

    written = set()
    total = 0
    for stem in sorted(by_script):
        key = stem + '.WS2'
        if key not in base:
            raise SystemExit('[ERR] 根层没有 %s' % key)
        # 以根层原档为源（幂等：每次从原档重做，不叠加上一轮的副本）
        raw = ws2.decode(base[key])
        spans = caption_spans(raw)
        todo = []
        for e in by_script[stem]:
            hits = [s for s in spans if raw[s[0]:s[1]].decode('cp932', 'replace') == e['old']]
            if len(hits) != 1:
                raise SystemExit('[ERR] %s：原文 %r 命中 %d 处（需唯一）' % (stem, e['old'], len(hits)))
            todo.append((hits[0], e['new'].encode('utf-8')))
        todo.sort(key=lambda t: t[0][0], reverse=True)
        n0, ids0, tr0 = len(ws2disasm.disassemble(raw)), None, raw[-8:]
        for (a, b), new in todo:
            raw = raw[:a] + new + raw[b:]
        # 自洽性：指令数与尾表不变（只换文本字节）
        ins2 = ws2disasm.disassemble(raw)
        if len(ins2) != n0:
            raise SystemExit('[ERR] %s：指令数变了 %d -> %d' % (stem, n0, len(ins2)))
        if raw[-8:] != tr0:
            raise SystemExit('[ERR] %s：尾表变了' % stem)
        for _a, new in todo:
            written.add(new.decode('utf-8'))
            total += 1
        pack[key] = [pack[key][0] if key in pack else key.encode('utf-16-le'), ws2.encode(raw)]
        print('[写字幕] %s：%d 条（副本 %d 字节）' % (stem, len(todo), len(raw)))

    print('共写入 %d 条，去重后 %d 条 → 白名单' % (total, len(written)))
    if not args.write:
        print('[预演] 未写盘；加 --write 落盘')
        return 0

    members = [(v[0], v[1]) for k, v in pack.items()]
    layers.write_layer(pack_path, members, expect_count=len(members))
    print('[写盘] %s：%d 成员' % (pack_path, len(members)))

    WHITELIST.write_text(json.dumps(
        {'_说明': '自动生成：script/apply_caption_utf8.py 写入的中文字幕（hook DLL 白名单的唯一来源）。',
         'strings': sorted(written)}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('[写盘] %s：%d 条' % (WHITELIST.name, len(written)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
