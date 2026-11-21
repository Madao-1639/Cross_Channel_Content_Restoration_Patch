# -*- coding: utf-8 -*-
"""解锁位点闸：CG 画廊条目号 / `CG_ACHIEVEMENT` 调用 / 还原段伪造解锁。

判据与形状对齐参照项目 `IHMW/script/checks/cg_unlock_check.py`：**全部落在内容面**，
判「不减」与「不串号」两组，**不判位置**。

## 为什么需要这道闸

CG 显示点后恒跟一组解锁位点（本作形状见 doc/file-formats.md「CG 解锁的旗标域」）。
这些位点**跟着显示走、却又不是演出**：覆盖 Steam 区间时它们会跟显示一起被丢弃，
症状**完全没有报错** —— 玩家侧只能看到「画廊格子永久锁死、收集类成就不弹」。

## 判据（两组）

**第一组：不得丢失**

1. `0x04 CG_ACHIEVEMENT` 逐宿主数量不减；
2. `0b` 置位值集合不减（Steam 置过的每个 A/B，产物里仍存在）；
3. `CG_PAGE` 的每个条目都仍可置位（`B` 有写入者）。

**第二组：归属不得串号**

4. 产物里每个解锁对 `(A,B)` 都必须属于 **Steam 的编号集合**。

   原版与 Steam 是两套独立编号，原版段照抄进产物 ⇒ 数量**变多**、集合**变大**，
   第一组全绿，但 `CG_PAGE` 会点亮不相干的条目。**这类缺陷是「值错」不是「值少」
   ⇒ 只查「不减」的闸对它结构性失明。**这是参照项目点名的那一类。**

## 为什么不判「位置相等」

参照项目已定案，本项目的实测同验：**位置比对会把合法形态误判成缺陷**。两种合法漂移：

- 删格 / 覆盖区间：解锁组被搬到第一个删格前的存活格前缀（**提前**，是设计要求）；
- `apply_text_map` 会在 Steam 保留前缀里插格（实测 `CCC0000` +4 格、`CCD4003A` +5 格）
  ⇒ 用「前面对话数」当拍点，量出来的「推迟」全是假象。

故本闸只报位置变化、不判它。**唯一要拦的方向是丢失与串号。**

## 本体固有缺口（WARN，不修）

`CG_PAGE` 的 `B=1393`（`EVCC0350B`）与 `B=1501`（`SGCC0305B`）在 **Steam 原档里也没有
任何写入者**，属本体固有，不属本补丁修复范围（铁律：不为新增/固有内容扩鉴赏表）。

用法（项目根目录）：python script/audit/audit_cg_unlock.py
退出码 1 = 有丢失或串号。
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tool import arcbuild, cgunlock, layers, ws2, ws2disasm  # noqa: E402

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

PAGE = 'CG_PAGE.WS2'
_SL = json.loads((ROOT / 'resource' / 'scene_slices.json').read_text(encoding='utf-8'))
HOSTS = [sc['host'].upper() for sc in _SL['scenes']] + \
        [th['host'].upper() for th in _SL['tail_hosts']]


def scripts(path_or_dict):
    """只要 `.ws2` 成员（`.lng` 等非脚本不进扫描面）。"""
    if isinstance(path_or_dict, dict):
        return {n: d for n, d in path_or_dict.items() if n.endswith('.WS2')}
    return {n.decode('utf-16-le').upper(): d
            for n, d in arcbuild.read_raw(path_or_dict)
            if n.decode('utf-16-le').upper().endswith('.WS2')}


SKIPPED = []


def flags(raw, name='?'):
    """`0b` 置位值集合（全部 opcode 0x0b）。线性解析不自洽的记入 `SKIPPED`，不静默吞。"""
    try:
        ins = ws2disasm.disassemble(ws2.decode(raw))
    except Exception as ex:                       # noqa: BLE001 —— 记账不吞
        SKIPPED.append('%s(%s)' % (name, type(ex).__name__))
        return set()
    return {i.fields['id'] for i in ins if i.opcode == cgunlock.FLAG}


def ach_calls(raw, name='?'):
    try:
        ins = ws2disasm.disassemble(ws2.decode(raw))
    except Exception as ex:                       # noqa: BLE001
        SKIPPED.append('%s(%s)' % (name, type(ex).__name__))
        return 0
    return sum(1 for i in ins if i.opcode == 0x04
               and str(i.fields.get('name')) == cgunlock.ACH_NAME.decode('ascii'))


def page_entries(steam):
    """`CG_PAGE` 的条目表 → `{B: 缩略图名（大写）}`。"""
    pend, out = [], {}
    for i in ws2disasm.disassemble(ws2.decode(steam[PAGE])):
        if (i.opcode == 0x01 and i.fields.get('mode') == 2
                and i.fields.get('value') == 1.0
                and cgunlock.B_LO <= i.fields.get('id', 0) <= cgunlock.B_HI):
            pend.append(i.fields['id'])
        elif i.opcode == 0x1c and i.fields.get('name') == 'openCgBrowser' and pend:
            out[pend.pop()] = str(i.fields.get('name2')).upper()
    return out


def main():
    fails, warns, info = [], [], []
    steam = scripts(ROOT / 'backup' / 'Rio.arc')
    patch = scripts(layers.effective_dict('Rio.arc'))
    page = page_entries(steam)

    # ---- 前提：图名 → (A,B) 必须严格单射（本作不用帧归一，见 tool/cgunlock.py）----
    smap, conflicts = cgunlock.steam_unlock_map(ROOT / 'backup' / 'Rio.arc')
    dup = cgunlock.verify_injective(smap)
    print('Steam 解锁映射：%d 个图名；冲突 %d；非单射 %d'
          % (len(smap), len(conflicts), len(dup)))
    if conflicts or dup:
        fails.append('图名 → (A,B) 映射不唯一（反查前提不成立）：%s'
                     % (conflicts or dup)[:5])

    steam_pairs = set(smap.values())
    wrote_s = set()
    for name, raw in sorted(steam.items()):
        wrote_s |= flags(raw, name)
    wrote_p = set()
    pairs_p = set()
    for name, raw in sorted(patch.items()):
        wrote_p |= flags(raw, name)
        pairs_p |= {ab for ab in cgunlock.named_pairs(raw).values() if None not in ab}
    dom_p = {f for f in wrote_p if cgunlock.is_domain(f)}

    # ---- 第一组：不得丢失 ------------------------------------------------
    print('\n【第一组】不得丢失')

    # 1/2. 逐宿主 CG_ACHIEVEMENT 数量与旗标集合
    for host in HOSTS:
        s, a = steam.get(host), patch.get(host)
        if s is None or a is None:
            fails.append('%s 缺失（Steam 或产物）' % host)
            continue
        sc, ac = ach_calls(s, host), ach_calls(a, host)
        miss = {f for f in flags(s, host) if cgunlock.is_domain(f)} - flags(a, host)
        if ac < sc:
            fails.append('%s CG_ACHIEVEMENT %d → %d' % (host, sc, ac))
        if miss:
            fails.append('%s 丢解锁域旗标 %s' % (host, sorted(miss)))
        print('   %-20s CG_ACH %2d→%2d  域内旗标 %2d→%2d  %s'
              % (host, sc, ac,
                 len([f for f in flags(s, host) if cgunlock.is_domain(f)]),
                 len([f for f in flags(a, host) if cgunlock.is_domain(f)]),
                 '✅' if (ac >= sc and not miss) else '**FAIL**'))

    # 3. 画廊条目仍可置位
    dead = [b for b in sorted(page) if b not in wrote_s]
    lost = [b for b in sorted(page) if b in wrote_s and b not in wrote_p]
    if lost:
        for b in lost:
            fails.append('CG_PAGE 条目 B=%d（%s）被还原覆盖丢了解锁' % (b, page[b]))
    print('   CG_PAGE 条目 %d 个：Steam 能解锁的 %d 个全部仍可置位 %s'
          % (len(page), len(page) - len(dead), '✅' if not lost else '**FAIL**'))
    for b in dead:
        warns.append('B=%d（%s）在 Steam 原档里也没有写入者（本体固有缺口，不修）'
                     % (b, page[b]))

    # ---- 第二组：归属不得串号 --------------------------------------------
    # 4a. 集合面：每个解锁对都必须属于 Steam 的编号集合（参照项目的口径）。
    # 4b. **逐帧面**：有图名的解锁对必须等于 Steam 侧**同一张图**的那一对。
    #     ——— 4a 结构性漏判「合法但配错帧」：实测 (1199,1416) 本是 Steam 自己的合法对，
    #     把它写到 `EVCC9006B` 上过得了两组集合，但会点亮错的画廊条目。
    #     本作图名映射是单射（见 `tool/cgunlock.py`），所以可以逐帧对账。
    print('\n【第二组】归属不得串号')
    foreign = sorted(pairs_p - steam_pairs)
    if foreign:
        for ab in foreign:
            who = [n for n, raw in patch.items()
                   if ab in cgunlock.named_pairs(raw).values()]
            fails.append('解锁对 %s 不在 Steam 编号集合（出现在 %s）' % (list(ab), who[:3]))
    else:
        print('   4a 产物 %d 个解锁对全部属于 Steam 编号集合 ✅' % len(pairs_p))

    misframe = []
    for name, raw in sorted(patch.items()):
        for disp, ab in cgunlock.named_pairs(raw).items():
            want = cgunlock.resolve_pair(disp, smap)
            if want is not None and want != ab:
                misframe.append('%s @%s：写的是 %s，Steam 同图是 %s'
                                % (name, disp, list(ab), list(want)))
    if misframe:
        for x in misframe[:10]:
            fails.append('串号（配错帧）：%s' % x)
        if len(misframe) > 10:
            fails.append('（另有 %d 处配错帧）' % (len(misframe) - 10))
    else:
        print('   4b 有图名的解锁对全部等于 Steam 同图的那一对 ✅')

    # 4c. **还原段不得伪造解锁**：`EVCC9XXX` 是本补丁新增的图名（Steam CG_PAGE 里没有
    #     条目），所以它们一律不得带解锁对 —— 带了一定是给不存在的格子写旗标。
    #     这是「C 区原版独有解锁一律抛弃」的机械化。
    forged = []
    for name, raw in sorted(patch.items()):
        for disp in cgunlock.named_pairs(raw):
            if disp.startswith('EVCC9') and disp not in smap:
                forged.append('%s @%s' % (name, disp))
    if forged:
        for x in forged[:10]:
            fails.append('给还原段 CG 写了伪造解锁：%s' % x)
        if len(forged) > 10:
            fails.append('（另有 %d 处伪造解锁）' % (len(forged) - 10))
    else:
        print('   4c 还原段 CG（EVCC9XXX）无一带解锁对 ✅')

    # ---- 报告（不判）：位置变化 ------------------------------------------
    print('\n【报告】解锁组的位置变化（不判 —— 提前是设计要求，插格会让拍点漂移）')
    for host in HOSTS:
        s, a = steam.get(host), patch.get(host)
        if s is None or a is None:
            continue
        sg = cgunlock.unlock_groups(s)
        ag = cgunlock.unlock_groups(a)
        detach = [(d1, ab1) for (t1, d1, _o1, ab1), (_t2, d2, _o2, _ab2)
                  in zip(sg, ag) if d1 and not d2]
        if detach:
            info.append('%s 搬到前缀 %d 组：%s' % (host, len(detach),
                                               [d for d, _ in detach]))

    # ---- 信息 ------------------------------------------------------------
    print('\n域划分：A(CG 组) = %d..%d，B(画廊条目) = %d..%d；产物域内旗标 %d 个'
          % (cgunlock.A_LO, cgunlock.A_HI, cgunlock.B_LO, cgunlock.B_HI, len(dom_p)))
    for x in info:
        print('   ' + x)
    sk = sorted(set(SKIPPED))
    if sk:
        print('   线性解析不自洽（跳过，非静默）：%d 个 %s' % (len(sk), sk[:6]))

    print('\n== 结论：FAIL %d 项 / WARN %d 项 ==' % (len(fails), len(warns)))
    for w in warns:
        print('  [WARN] %s' % w)
    for f in fails:
        print('  [FAIL] %s' % f)
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
