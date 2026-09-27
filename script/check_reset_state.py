# -*- coding: utf-8 -*-
"""拒绝在**已建成的 `asset/Rio.arc`** 上重跑流水线 —— 防"忘了复位"把产物写坏。

背景（评审 2026-09-27 两次点名）：`apply_text_map` 在建成产物上重跑会**再插入一遍**，
其 `lng == n_final` 的守卫拦不住（格数恰好相等），产物被静默写坏。

判据（不含歧义）：对**有插入/删格**的在表脚本（`n_final != n_steam`），
「插入前」格数恒 `== n_steam`、「已插入」恒 `== n_final`。只要**有一个**这类脚本
的格数 `== n_final`，就说明 `asset/Rio.arc` 是建成态 ⇒ 拒跑，并提示复位。

（格数定义与 `verify_text_map` 一致：`14` 一格 + 每个 `0f` 条目一格。）
用法：`python script/check_reset_state.py`（非 0 退出 = 建成态，应复位）
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tool import arcbuild, ws2, ws2disasm  # noqa: E402

if hasattr(sys.stdout, 'buffer'):
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

RIO = ROOT / 'asset' / 'Rio.arc'
TM = json.loads((ROOT / 'resource' / 'text_map.json').read_text(encoding='utf-8'))['scripts']
RESET = 'asset/Rio.arc.before_inline_splice'


def cells(raw):
    n = 0
    for i in ws2disasm.disassemble(ws2.decode(raw)):
        if i.opcode == 0x14:
            n += 1
        elif i.opcode == 0x0f:
            n += len(i.fields['entries'])
    return n


def main():
    members = {n.decode('utf-16-le').upper(): d for n, d in arcbuild.read_raw(RIO)}
    # **宿主/tail_host 不参与判定**：它们的格数由 `splice_restoration` 产生，串接**前**本就等于
    # Steam 的格数（≠ `n_steam`）—— 只看它们会把合法的"串接前复位态"误判成"其它"。
    hosts = set()
    p = ROOT / 'resource' / 'scene_slices.json'
    if p.exists():
        sl = json.loads(p.read_text(encoding='utf-8'))
        # 注意 `host` 带 `.ws2` 扩展名，而表键不带 ⇒ 必须去掉扩展名再比，否则**宿主一个都跳不掉**，
        # 会把合法的"串接前复位点"误判成「其它」而拒跑（`cp before_inline_splice; build_patch` 被自己挡下）。
        hosts = {s['host'].upper()[:-4] for s in sl['scenes']} | \
                {t['host'].upper()[:-4] for t in sl['tail_hosts']}
    built, at_steam, no_insert, other = [], 0, 0, []
    for stem, sc in sorted(TM.items()):
        ns, nf = sc.get('n_steam'), sc.get('n_final')
        if ns is None or nf is None or ns == nf:
            no_insert += 1
            continue
        if stem.upper() in hosts:
            continue
        raw = members.get(stem.upper() + '.WS2')
        if raw is None:
            continue
        c = cells(raw)
        if c == nf:
            built.append((stem, c, ns, nf))
        elif c == ns:
            at_steam += 1
        else:
            other.append((stem, c, ns, nf))
    print('有插入/删格的**在表非宿主**脚本：插入前 %d 个 / **已插入（建成态）%d 个** / 其它 %d 个；无插入 %d 个'
          % (at_steam, len(built), len(other), no_insert))
    if built:
        print('\n[中止] `asset/Rio.arc` 是**建成态**（下列脚本已是插入后的格数 `n_final`）：')
        for stem, c, ns, nf in built[:10]:
            print('   %-16s 格数 %d（n_steam %d / n_final %d）' % (stem, c, ns, nf))
        print('\n请先复位再跑：`cp %s asset/Rio.arc`' % RESET)
        return 1
    if other:
        print('\n[中止] 下列脚本格数既非 n_steam 也非 n_final（输入不是预期的复位态）：')
        for stem, c, ns, nf in other[:10]:
            print('   %-16s 格数 %d（n_steam %d / n_final %d）' % (stem, c, ns, nf))
        return 1
    print('[OK] 未见建成态 —— 输入处于插入前状态')
    return 0


if __name__ == '__main__':
    sys.exit(main())
