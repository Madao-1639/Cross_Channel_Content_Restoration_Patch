# -*- coding: utf-8 -*-
"""`CCB2017_EN` 的**选项分支**：在 `k=30` 与 `k=31` 之间插入「扭扭乐 / 一小时的Ｈ赌约」。

本步是本项目**唯一**「增加跳转」的地方 —— 缘由与取舍见
[doc/restoration-targets.md](../doc/restoration-targets.md)「例外处理」。

统一形状
--------
    [Steam 前缀 k=0..30]
    14 提示句（旁白、尾 `%K`）              ← 选项前那条提示句（原生惯例，中文走 lng）
    0e 00 02 00 01                       ← 选项头（原生每个 `0f` 前都有，见 ccb0007/cca0006）
    0f 02
      A「Twister」        label=11  jump = 06 <清框格 A>
      B「One-hour H bet」 label=12  jump = 06 <清框格 B>
    14 %P                                ← 清框格 B（分支入口；中文即标记串本身，见下）
    [孤岛：源 WSC 对话序 30..47 的切片转换（立绘 / 背景 / 录音 / 名字 / 正文随行）]
    06 <k=80 的设名 `15`>                ← 回到 Steam 共用后续
    14 %P                                ← 清框格 A（`k=31` 的设名 `15` 之前）
    [Steam k=31..128]
    [Steam 出口尾段]

要点
----
* **两条目都带显式 `06`** ⇒ `0f` 之后没有落空路径，扭扭乐分支不会穿过孤岛。
* **提示句**尾标记必须是 **`%K`**（不是 `%K%P`）：文本框留在屏上、选项按钮浮在上面
  —— 原生的 56 处选项里 45 处如此（`CCA0004_EN` 是「提示句 `%K` → 直接 `0e`」的最近例子）。
* **分支入口各有一张清框格 `14 "%P"`**：`%K` 只「留框、等输入」、**不清框** ⇒ 少了它，
  选完选项后提示句会**留在屏上与下一句连在一起**。原生惯例是落点先执行一张
  **纯标记 `14 "%P"`**：跨文件落点 **89/95** 条的目标脚本首格就是 `%P`（80/85 个脚本），
  文件内 `06` 落点 **25/32** 条直接落在 `%P`（其余落在 `SetFlag` / `Condition` 之后）。
  两张清框格**不带 `15`**（原生 1,378 张纯标记格零前导 `15`），其 lng 由 `text_map.json` 的
  `op:"ctrl"`（`zh == "%P"`）原样回填标记串。
* 孤岛由 `tool.wsc2ws2.convert_range` 从**源 WSC** 转出（与 12 个宿主同一套转换器、
  同一套改名/坐标适配）；源区间内**没有** `01`/`06`/`07`/`ff`（实测）⇒ 孤岛自身
  不含绝对偏移，不需要重定位。
* **只做结构**：正文 / 说话人 / 演出属性仍由 `script/apply_text_map.py` 按
  `resource/text_map.json` 落盘 ⇒ 本步之后 `CCB2017_EN` 的槽位从 129 变 **152**
  （+1 提示句 +2 选项条目 +2 清框格 +18 还原行），表、lng、`import_missing_voices`（闭环保底）随之对齐。
* **幂等**：已含本分支（存在 `0f` 且首条目文本 == `A_TEXT`）⇒ 跳过。
* 尾表（末尾 8 字节）按最终流重算（`0x14` 条数变了）。

用法：
    python script/splice_ccb2017_branch.py --check     # 只读校验 + 打印落点
    python script/splice_ccb2017_branch.py --write     # 写入 asset/Rio.arc（先备份）
"""
import argparse
import io
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tool import archprobe, arcbuild, rename_map as RN, ws2, ws2disasm, wsc, writer  # noqa: E402
from tool.wsc2ws2 import ConvertOptions, convert_range                                 # noqa: E402

if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

STEAM_STEM = 'CCB2017'
HOST = 'CCB2017_EN.WS2'
RIO = ROOT / 'asset' / 'Rio.arc'
WSC_DIR = ROOT / 'resource' / 'corpus' / 'wsc'

# 分叉几何（两边逐行核过，见 doc/restoration-targets.md）
BRANCH_AT = 30            # 最后一条共用的 Steam 槽（源对话序 29）
SRC_LO, SRC_HI = 30, 47   # 孤岛 = 源对话序 30..47（CCS 行 31..48，18 行）
MERGE_AT = 80             # 孤岛结束后回到的 Steam 槽（源对话序 48）

# 注入选项条目的**占位正文**（ASCII，CP932 安全）。屏幕上的中文由 lng 提供
# （`resource/text_map.json` 的 `op:"opt"`），与 Steam 原生选项同一机制 ——
# 实测 `CCB0007_EN` 的 3 个条目正文完全相同、只有 lng 不同。
A_TEXT = 'Twister'
B_TEXT = 'One-hour H bet'
# 选项**前那条提示句**（旁白/内面）。原生惯例：选项前紧邻的那条 `14` 以 **`%K`**（不是 `%K%P`）
# 结尾 —— 文本框留在屏上、选项按钮浮在上面（56 处选项里 45 处如此；`CCA0004_EN` 是
# 「提示句 `%K` → 直接 `0e`」的最近例子）。正文同样是 ASCII 占位，中文走 lng。
PROMPT_TEXT = 'Now, what should I suggest?%K'
# 分支入口的**清框格**正文（纯标记 `14`）。同上：ASCII 占位，屏幕上什么都不显示。
CLEAR_TEXT = '%P'
LABEL_BASE = 10           # 原生 label = 10 + 序号（1-based）：11, 12, ...


def load(path):
    return {n.decode('utf-16le').upper(): d for n, d in arcbuild.read_raw(path)}


def src_instrs():
    raw = (WSC_DIR / (STEAM_STEM + '.WSC')).read_bytes()
    try:
        return wsc.disassemble(raw)
    except Exception:
        from tool.wsc2ws2 import decrypt_wsc
        return wsc.disassemble(decrypt_wsc(raw))


def mk_entry(text, label, target):
    """`<u16 strid> <text NUL> 00 <u16 label> 06 <u32 target>`（strid 由本步统一重编号）。"""
    return (struct.pack('<H', 0) + text.encode('ascii') + b'\x00\x00'
            + struct.pack('<H', label) + b'\x06' + struct.pack('<I', target))


def mk_dlg(text):
    """一条 `14` 对话（提示句）：`14 <id:u16=0> <u16 0> char NUL <text NUL> <u8 0>`。

    `id` 由 `repack_ids()` 统一重号；正文是 ASCII 占位（中文走 lng）。
    """
    return (b'\x14' + struct.pack('<H', 0) + b'\x00\x00' + b'char\x00'
            + text.encode('ascii') + b'\x00' + b'\x00')


def repack_ids(plain):
    """把 `14` 的 id / `0f` 条目的 strid 按**出现序**重编号（字符串池序号）。

    `0f` 与 `14` 共用同一个池（`strid` 即槽位序，见 doc/localization.md「位置对应」）。
    `apply_text_map` 的 `rebuild` 稍后会再编号一次；这里先编一遍是为了让**本步的产物**
    自身自洽（可解析、id 连续、能被后续步骤当普通输入读）。
    """
    buf = bytearray(plain)
    pool = 0
    for i in ws2disasm.disassemble(plain):
        if i.opcode == 0x14:
            struct.pack_into('<H', buf, i.offset + 1, pool)
            pool += 1
        elif i.opcode == 0x0f:
            body = bytes(buf[i.offset:i.offset + i.size])
            for off in writer.opt_strid_offsets(body):
                struct.pack_into('<H', buf, i.offset + off, pool)
                pool += 1
    return bytes(buf)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--check', action='store_true')
    ap.add_argument('--write', action='store_true')
    ap.add_argument('--rio', default=str(RIO), help='要改的归档（默认 asset/Rio.arc）')
    args = ap.parse_args()
    rio = Path(args.rio)

    members = load(rio)
    if HOST not in members:
        raise SystemExit('%s 里没有 %s' % (rio, HOST))
    plain = ws2.decode(members[HOST])
    ins = ws2disasm.disassemble(plain)

    # ---- 幂等 ----
    for i in ins:
        if i.opcode == 0x0f and i.fields['entries'] and i.fields['entries'][0]['text'] == A_TEXT:
            print('[跳过] %s 已含本分支（首条目文本 == %r）' % (HOST, A_TEXT))
            return 0

    dls = [i for i in ins if i.opcode == 0x14]
    if len(dls) != 129:
        raise SystemExit('%s 对话数 %d != 129（本步只适用于未改动的原始脚本）'
                         % (HOST, len(dls)))
    if any(i.opcode == 0x0f for i in ins):
        raise SystemExit('%s 已含别的 `0f`，拒绝自动改写' % HOST)

    # ---- 插入点：`k=31` 的设名 `15`（`k=30` 的清框 `15` 之后）----
    idx31 = ins.index(dls[BRANCH_AT + 1])
    if idx31 == 0 or ins[idx31 - 1].opcode != 0x15:
        raise SystemExit('%s：`k=31` 之前不是设名 `15`' % HOST)
    off = ins[idx31 - 1].offset                # 插入点 = `k=31` 的设名 `15`（插入在它**之前**）
    pfx31 = ins[idx31 - 1].fields.get('prefix') or ''

    # ---- 汇合点：`k=80` 的设名 `15` ----
    idx80 = ins.index(dls[MERGE_AT])
    if idx80 == 0 or ins[idx80 - 1].opcode != 0x15:
        raise SystemExit('%s：`k=80` 之前不是设名 `15`' % HOST)
    merge_off = ins[idx80 - 1].offset

    # ---- 孤岛：源对话序 30..47 ----
    win = src_instrs()
    wdl = [i for i in win if i.opcode in (0x41, 0x42)]
    if len(wdl) <= SRC_HI:
        raise SystemExit('源语料只有 %d 条对话' % len(wdl))
    # 起点取**上一句对话之后**（而不是本句自身）：原版把「本行的随行演出」（立绘/背景等）
    # 放在**前一句结束、本句开始**之间（与 resource/original_stage.json 同一口径），
    # 从本句自身起切会把第一行的立绘落在切片外 ⇒ H 分支首行沿用扭扭乐分支的立绘。
    start = wdl[SRC_LO - 1].offset + wdl[SRC_LO - 1].size
    end = wdl[SRC_HI + 1].offset
    opts = ConvertOptions(slice_mode=True, pool_start=0, rename_map=RN.build()[0],
                          available=archprobe.load_inventory(),
                          pna_layers=archprobe.load_pna_layers())
    island, rep = convert_range((WSC_DIR / (STEAM_STEM + '.WSC')).read_bytes(),
                                STEAM_STEM, opts, start_offset=start, end_offset=end)
    if rep['exit'] is not None:
        raise SystemExit('孤岛切片不应产生出口')
    if rep['dialogues'] != SRC_HI - SRC_LO + 1:
        raise SystemExit('孤岛对话数 %d != %d' % (rep['dialogues'], SRC_HI - SRC_LO + 1))

    # ---- 组装注入块（目标先按「插入后」坐标写，见下面 d 的用法）----
    # 选项头（原生字节形态：`0e 0b 00 <count> 00 01`，6B；见 ws2disasm._op_0e 与 CCB0007/CCA0006）
    hdr = b'\x0e\x0b\x00\x02\x00\x01'
    prompt = mk_dlg(PROMPT_TEXT)         # 选项前的提示句（旁白）
    clear = mk_dlg(CLEAR_TEXT)           # 分支入口清框格（A/B 各一张，字节相同）
    n_island_dlg = rep['dialogues']
    # 条目 A/B 的目标：A = 清框格 A（`k=31` 的设名 `15` 之前）；B = 清框格 B（孤岛首字节之前）
    choice_len = 2 + len(mk_entry(A_TEXT, LABEL_BASE + 1, 0)) + len(mk_entry(B_TEXT, LABEL_BASE + 2, 0))
    head_b = len(prompt) + len(hdr) + choice_len         # `0f` 之后、清框格 B 之前
    d = head_b + len(clear) + len(island) + 5 + len(clear)   # 注入块总长（含 `06` + 清框格 A）
    tgt_a = off + d - len(clear)                         # 清框格 A（其前是孤岛末条 `06`）
    tgt_b = off + head_b                                 # 清框格 B（其后是孤岛首指令）
    tgt_c = merge_off + d                                # `k=80` 的设名 `15`
    block = (prompt + hdr + b'\x0f\x02'
             + mk_entry(A_TEXT, LABEL_BASE + 1, tgt_a)
             + mk_entry(B_TEXT, LABEL_BASE + 2, tgt_b)
             + clear + island + b'\x06' + struct.pack('<I', tgt_c) + clear)

    new_plain = repack_ids(plain[:off] + block + plain[off:])

    # ---- 校验 ----
    ni = ws2disasm.disassemble(new_plain)
    if sum(i.size for i in ni) != len(new_plain):
        raise SystemExit('解析不完整：%d != %d' % (sum(i.size for i in ni), len(new_plain)))
    ids = [i.fields['id'] for i in ni if i.opcode == 0x14]
    # 池序号：`14` 的 id 与 `0f` 条目的 strid 共用一个计数器（有选项表时 `id` 会跳号）
    pool_seq = []
    for x in ni:
        if x.opcode == 0x14:
            pool_seq.append(x.fields['id'])
        elif x.opcode == 0x0f:
            pool_seq.extend(e['strid'] for e in x.fields['entries'])
    if pool_seq != list(range(len(pool_seq))):
        raise SystemExit('池序号不连续：%s…%s' % (pool_seq[:3], pool_seq[-3:]))
    starts = {i.offset: i for i in ni}
    pos_of = {i.offset: j for j, i in enumerate(ni)}
    n_dlg = len(ids)
    if n_dlg != 129 + 1 + 2 + n_island_dlg:
        raise SystemExit('对话数 %d != 129 + 1（提示句）+ 2（清框格）+ %d' % (n_dlg, n_island_dlg))
    # 提示句：旁白、尾标记必须是 `%K`（不带 `%P`）—— 否则会清屏，选项就悬浮在空屏上
    p_ins = starts.get(off)
    if p_ins is None or p_ins.opcode != 0x14:
        raise SystemExit('提示句定位失败（off=%d）' % off)
    _pt = p_ins.fields.get('text') or ''
    if not _pt.endswith('%K') or _pt.endswith('%K%P'):
        raise SystemExit('提示句尾标记应为 `%%K`，实测 %r' % _pt[-8:])
    pool, at_choice = 0, None
    for x in ni:
        if x.opcode == 0x14:
            pool += 1
        elif x.opcode == 0x0f:
            at_choice = pool
            pool += len(x.fields['entries'])
    # 选项条目的池序号 = 31（Steam 前段）+ 1（提示句） = 32；`BRANCH_AT + 2`
    if at_choice != BRANCH_AT + 2:
        raise SystemExit('选项条目的池序号 %s != %d' % (at_choice, BRANCH_AT + 2))
    # 落点必须是**指令首字节**，且语义正确：A/B -> 清框格 `14 '%P'`（且其前不得有 `15`）；
    # C（孤岛末条 `06`）-> `k=80` 的设名 `15`
    # 清框格**之后**：A 恒为 `k=31` 的设名 `15`；B 为孤岛首指令（可能是孤岛首行的设名 `15`，
    # 因为切片起点处在上一句与首行之间）。
    for tag, tgt, ok_nxt in (('A', tgt_a, frozenset({0x15})), ('B', tgt_b, frozenset({0x14, 0x15}))):
        x = starts.get(tgt)
        if x is None or x.opcode != 0x14 or (x.fields.get('text') or '') != CLEAR_TEXT:
            raise SystemExit('条目 %s 的落点 %d 不是清框格 `14 %r`（实测 %s）'
                             % (tag, tgt, CLEAR_TEXT,
                                ws2disasm.opcode_name(x.opcode) if x else '非指令首字节'))
        if ni[pos_of[tgt] - 1].opcode == 0x15:
            raise SystemExit('清框格 %s @%d 前不应有 `15`（原生纯标记格不带设名/清框）' % (tag, tgt))
        if ni[pos_of[tgt] + 1].opcode not in ok_nxt:
            raise SystemExit('清框格 %s @%d 之后应为 %s，实测 %s'
                             % (tag, tgt, '/'.join(ws2disasm.opcode_name(o) for o in sorted(ok_nxt)),
                                ws2disasm.opcode_name(ni[pos_of[tgt] + 1].opcode)))
    x = starts.get(tgt_c)
    if x is None or ws2disasm.opcode_name(x.opcode) != 'SetDisplayName':
        raise SystemExit('条目 C 的落点 %d 应为 SetDisplayName（实测 %s）'
                         % (tgt_c, ws2disasm.opcode_name(x.opcode) if x else '非指令首字节'))
    print('%s：对话 %d（+%d 孤岛 +1 提示句 +2 清框格）、槽位 %d（+%d）'
          % (HOST, n_dlg, n_island_dlg, pool, 5 + n_island_dlg))
    print('  插入点 @%d（原坐标）  注入块 %d 字节' % (off, len(block)))
    print('  提示句 @%d（旁白，尾 %r）' % (off, _pt[-2:]))
    print('  条目 A「%s」 -> @%d（清框格 %r，其后 `k=31` 设名名字框 %r）'
          % (A_TEXT, tgt_a, CLEAR_TEXT, pfx31))
    print('  条目 B「%s」 -> @%d（清框格 %r，其后孤岛首行）' % (B_TEXT, tgt_b, CLEAR_TEXT))
    print('  孤岛 %d 行（源对话序 %d..%d），末条 `06` -> @%d（%s，名字框 %r）'
          % (n_island_dlg, SRC_LO, SRC_HI, tgt_c, ws2disasm.opcode_name(starts[tgt_c].opcode),
             starts[tgt_c].fields.get('prefix')))

    if not args.write:
        print('（未写入；加 --write 才改 %s）' % rio)
        return 0

    bck = rio.with_name(rio.name + '.before_ccb2017_branch')
    if not (bck.exists() and arcbuild.same_file(bck, rio)):
        arcbuild.write_arc(arcbuild.read_raw(rio), bck)
        print('[备份] %s' % bck)
    n14 = len(ids)
    n0f = sum(len(i.fields['entries']) for i in ni if i.opcode == 0x0f)
    members[HOST] = ws2.fix_trailer(ws2.encode(new_plain), counts=(n14, n0f))
    order = [n for n, _ in arcbuild.read_raw(rio)]
    merged = [(nb, members[nb.decode('utf-16le').upper()]) for nb in order]
    arcbuild.write_arc(merged, rio)
    back = load(rio)[HOST]
    if back != members[HOST]:
        raise SystemExit('[失败] 回读不一致')
    cnt, size, _ = arcbuild.verify(rio)
    print('[写入] 回读校验通过（%d 个成员，%d 字节）；尾表 (%d, %d)' % (cnt, size, n14, n0f))
    return 0


if __name__ == '__main__':
    sys.exit(main())
