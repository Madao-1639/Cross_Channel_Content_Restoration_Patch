# -*- coding: utf-8 -*-
"""生成 `resource/original_stage.json`：**原版每个「被插入的行」随行的画面演出指令**。

用途（洞 1）：文本表的 `insert`（242 段 / 1,095 行）原先只发 `15+14(+2e)` —— 名字框、
正文、语音齐，**原版随行的立绘 / 背景 / CG 没带入** ⇒ 出现「有台词的语音与名字框、
画面却是空屏或别人」。本表把这些行的原版画面演出补上。

做法：对每个插入行的**随行区间**（上一句对话结束 → 本句对话开始，与 `original_audio.json`
的「属于其后的那行」同一口径）跑 `tool.wsc2ws2.convert_range` —— **与就地插入用的是同一个
转换器**，所以坐标适配（`steam_x()`）、PNA/CG 改名、`39` 帧号形态全部一致。

**只保留画面与音频演出**：剔掉文本（`14`）、名字框（`15`）、语音（`2e`）、选项（`0e`/`0f`）、
跳转/出口（`06`/`07`/`ff`）—— 前四者由写盘器另行处理；**立绘/背景/CG/BGM/SE 全部保留**
（原版随行的音要跟着台词走；名字在原版 `Bgm.arc`/`Se.arc` 里都在）。

产出：`{ccs名: {行号: "十六进制字节"}}`，行号口径与 CCS 一致（`行 = 对话序 + 1`）。
用法：`python script/build_original_stage.py`
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'script'))
sys.stdout.reconfigure(encoding='utf-8')

from tool import wsc                                    # noqa: E402
from tool import speaker as spk                         # noqa: E402
from tool import arcbuild, ws2, ws2disasm               # noqa: E402
from tool.wsc2ws2 import ConvertOptions, convert_range, decrypt_wsc   # noqa: E402
import build_rename_map                                 # noqa: E402
import splice_restoration as SR                         # noqa: E402

WSC = ROOT / 'resource' / 'corpus' / 'wsc'
OUT = ROOT / 'resource' / 'original_stage.json'
TM = ROOT / 'resource' / 'text_map.json'

# 剔掉：文本 / 名字框 / 语音 / 选项 / 跳转出口。
# BGM（`1e`/`1f` 及其曲目 id `0b`）与 SE（`28`）**保留** —— 原版随行的音也要跟着台词走；
# 名字在原版 `Bgm.arc`(26) / `Se.arc`(120) 里全部存在（实测插入行区间只涉 1 处 BGM、9 处 SE）。
DROP = {0x14, 0x15, 0x2e, 0x0e, 0x0f, 0x06, 0x07, 0xff}


def src_instrs(stem):
    raw = (WSC / (stem + '.WSC')).read_bytes()
    try:
        return wsc.disassemble(raw)
    except Exception:
        return wsc.disassemble(decrypt_wsc(raw))


def span_of(ins, row):
    """第 `row` 行（1-based）的**随行区间** = [上一句结束, 本句开始)。

    与 `original_audio.json` 同一口径：紧邻在某句之前的演出指令属于**该句**。
    区间内没有指令时返回 None。
    """
    dlgs = [i for i in ins if i.opcode in (0x41, 0x42)]
    if not (1 <= row <= len(dlgs)):
        return None
    j = row - 1
    end = dlgs[j].offset
    start = (dlgs[j - 1].offset + dlgs[j - 1].size) if j > 0 else 0
    if start >= end:
        return None
    return start, end


def curated_span(ins, row, pna_prefix):
    """洞 2 的区间：**该说话人最后一次出图的那一条 `48` 本身**。

    洞 2 的成因与洞 1 不同：原版在这些行**没有再发 `48`** —— 那张立绘从更早某行起就一直挂在屏上、
    直到本行都没撤（如 `CCC4014` 的雾：r102 显示、此后六句「人殺し！」她一直在屏）。而 Steam 骨架
    在中途把她撤掉了。所以要补的不是「本行的随行区间」（那是空的），而是**把原版当时在屏的
    那张立绘重新摆上**。

    ⚠️ 只取那**一条 `48`**，**不要**取「它到本行」的整段：那一整段里夹着原版十几行的
    `4a` 等待与别的演出，照搬会把它们**全挤到本格之前**、打乱节奏。
    """
    dlgs = [i for i in ins if i.opcode in (0x41, 0x42)]
    if not (1 <= row <= len(dlgs)):
        return None
    end = dlgs[row - 1].offset
    for i in reversed([x for x in ins if x.opcode == 0x48 and x.offset < end]):
        if (i.fields.get('name') or '').upper().startswith(pna_prefix):
            return i.offset, i.offset + i.size
    return None


def family_slots(rio_path):
    """`{WSC名: {立绘族(名前4字符): 该族在骨架里用到的槽集合}}`。

    用途：注入的立绘应当**沿用骨架给该角色用的槽** —— 否则与骨架正摆在别的槽上的同一角色
    **同屏重叠**（实测 `CCA0015`：骨架在 `st07` 摆見里，注入的落 `st03` ⇒ 两个見里）。

    ⚠️ 只返回**用到的槽集合**，由调用方决定要不要用：
    **只有唯一槽时才能直接沿用**（那是骨架的既定选择、有据）；用了多个槽时不能猜
    （「最常用」是无根据的启发式），要另想办法（局部对位 / 换槽前先清另一个槽）。
    """
    import collections
    m = {n.decode('utf-16-le').upper(): d for n, d in arcbuild.read_raw(rio_path)}
    acc = {}
    for stem, data in m.items():
        if not stem.endswith('.WS2'):
            continue
        pend, cnt = {}, collections.defaultdict(set)
        for x in ws2disasm.disassemble(ws2.decode(data)):
            if x.opcode == 0x34:
                pend['s' + str(x.fields.get('slot'))] = (x.fields.get('file') or '')[:4].upper()
            elif x.opcode == 0x39:
                s = x.fields.get('name')
                if s in pend:
                    cnt[pend[s]].add(s)
        acc.setdefault(stem[:-4], collections.defaultdict(set))   # 键 = 脚本名（去 `.WS2`）
        for fam, ss in cnt.items():
            acc[stem[:-4]][fam] |= ss
    return {w: dict(d) for w, d in acc.items()}


def main():
    tm = json.loads(TM.read_text(encoding='utf-8'))['scripts']
    rename, unresolved, _unchanged, _cg, conflicts = build_rename_map.build()
    if conflicts:
        raise SystemExit('CG 编号表有未入表项：%s' % conflicts[:5])
    if unresolved:
        raise SystemExit('有资源找不到出处（先跑 import_missing_voices）：%s' % unresolved[:5])
    inventory = SR.load_inventory()
    pna_layers = SR.load_pna_layers()
    # 骨架（接缝已合）里各角色用的槽 —— **只有唯一槽时才沿用**（有据；多槽/没有则不猜）。
    # `family_slots` 的键是脚本名（`CCD3003A_EN`），这里按 `text_map` 映射到 `ccs`（`CCD3003`）——
    # 同一 ccs 的多个脚本取**并集**：某个脚本里唯一、另一个里不是 ⇒ 变成多槽 ⇒ 自动放弃沿用（保守）。
    import collections as _c
    ccs_of = {k: s['ccs'] for k, s in tm.items()}
    _byccs = {}
    for _sk, _d in family_slots(ROOT / 'asset' / 'Rio.arc').items():
        _ccs = ccs_of.get(_sk)
        if not _ccs:
            continue
        for _f, _ss in _d.items():
            _byccs.setdefault(_ccs, _c.defaultdict(set))[_f] |= _ss
    FAM = {_ccs: {_f: next(iter(_ss)) for _f, _ss in _d.items() if len(_ss) == 1}
           for _ccs, _d in _byccs.items()}

    out = {}
    # 需要补演出的行 → 区间 (a, b)。**两类来源、两种区间算法**：
    #   洞 1：text_map 的 `insert` 行 ⇒ **随行区间**（上一句结束 → 本句开始）；原版在这些行**重新出图**。
    #   洞 2：顶层覆盖层登记的行 ⇒ **该说话人最后一次出图 → 本行**（见 `curated_span`）。
    spans = {}
    pna = spk.pna_prefix()                       # {en: 立绘前四字符}
    ja2en = spk.ja_to_en()

    for sc in (tm[k] for k in sorted(tm)):
        rows = [r for it in sc['items'] if it['op'] == 'insert'
                for r in range(it['src_rows'][0], it['src_rows'][1] + 1)]
        if not rows:
            continue
        ins = src_instrs(sc['ccs'])
        for r in rows:
            s = span_of(ins, r)
            if s:
                spans.setdefault(sc['ccs'], {})[r] = s

    STG = ROOT / 'resource' / 'text_map_stage.json'
    ccs_of = {k: s['ccs'] for k, s in tm.items()}
    if STG.exists():
        for stem, per in json.loads(STG.read_text(encoding='utf-8')).items():
            ccs = ccs_of.get(stem)
            if not ccs:
                continue                             # 以 `_` 开头的说明键自然落这里
            ins = src_instrs(ccs)
            dlgs = [i for i in ins if i.opcode in (0x41, 0x42)]
            for row in (int(r) for r in per.values()):
                if not (1 <= row <= len(dlgs)):
                    continue
                g = dlgs[row - 1]
                ja = ''
                if g.opcode == 0x42:
                    o = g.operands
                    e = o.index(0, 5)
                    ja = o[5:e].decode('cp932', 'replace')
                pre = pna.get(ja2en.get(ja, ''), '')
                s = curated_span(ins, row, pre) if pre else None
                if s:
                    spans.setdefault(ccs, {})[row] = s

    # 这些行**不在 `build_rename_map` 的覆盖面内**（它只扫 12 宿主的切片区间）⇒
    # 按同一条规则（档位 0 → 1）本地补全立绘改名；目标不存在就不入表，
    # 转换器 `emit_portrait_block` 会跳过它（绝不产出悬空引用）。
    for ccs, per in spans.items():
        ins = src_instrs(ccs)
        for _row, (a, b) in per.items():
            for i in ins:
                if a <= i.offset < b and i.opcode == 0x48:
                    nm = (i.fields.get('name') or '').upper()
                    t = build_rename_map.portrait_target(nm, inventory)
                    if t:
                        rename[nm] = t

    for ccs, per in spans.items():
        src = (WSC / (ccs + '.WSC')).read_bytes()
        ins = src_instrs(ccs)
        acc = {}
        for row in sorted(per):
            a, b = per[row]
            opts = ConvertOptions(slice_mode=True, pool_start=0, rename_map=rename,
                                  available=inventory, pna_layers=pna_layers,
                                  slot_context=ins, family_slots=FAM.get(ccs, {}))
            data, _rep = convert_range(src, ccs, opts, start_offset=a, end_offset=b)
            keep = b''.join(data[i.offset:i.offset + i.size]
                            for i in _disasm(data) if i.opcode not in DROP)
            if keep:
                acc[str(row)] = keep.hex()
        if acc:
            out.setdefault(ccs, {}).update(acc)
    # 幂等 + **字节确定**：内容相同就不写盘；写盘用显式 LF 字节（`write_text` 在 Windows
    # 会把 `\n` 转成 `\r\n`，于是同一份内容在不同机器/不同历史上会得到不同字节 ⇒ 不可复现）。
    # 此前无条件重写 ⇒ 每跑一次都刷新 mtime，把"本轮产物是否真的变了"这条线索抹掉
    # （复核 A8 正好踩在这个坑上：未先快照就跑生成器，覆盖了负责人构建时的那份）。
    blob = json.dumps({'_说明': __doc__.split('产出')[0].strip(), **out},
                      ensure_ascii=False, indent=0).encode('utf-8')
    if OUT.exists() and OUT.read_bytes() == blob:
        print('（%s 内容未变，不重写）' % OUT.relative_to(ROOT))
    else:
        OUT.write_bytes(blob)
    n_scr = len(out)
    n_row = sum(len(v) for v in out.values())
    n_bytes = sum(len(h) // 2 for v in out.values() for h in v.values())
    print('写出 %s：%d 个脚本、%d 行带画面演出、共 %d 字节'
          % (OUT.relative_to(ROOT), n_scr, n_row, n_bytes))


def _disasm(data):
    # `convert_range` 返回的是**未混淆**（plain）字节 —— 不要再走 `ws2.decode`（那会二次 rot6）
    from tool import ws2disasm
    return ws2disasm.disassemble(data)


if __name__ == '__main__':
    sys.exit(main())
