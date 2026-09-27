# -*- coding: utf-8 -*-
"""把 Steam 删掉、原版有、且**当前没有任何槽位显示**的原版对话，插回原位。

清单来自 `tmp/textfix/orphan-insert.json`（分析产出：每段给出源 CCS 的行范围、
插入点 `after_k`、条数 `n`）。

    python script/insert_deleted_dialogues.py            # 只诊断
    python script/insert_deleted_dialogues.py --write    # 写入（备份 + 回读校验）

## 插什么

**只有对话，两条一组**：

    15 <prefix> 00 00                              SetDisplayName（prefix 空 = 清框）
    14 <u16 id> 00 00 "char" 00 <text> 00 00       DisplayMessage

**不搬任何演出指令**（立绘 / BGM / 渐变 / CG 一律不插）——「不新增原版不存在的演出」
是项目铁律，而这些行在原版当时是否伴随演出，此刻无法确认，保持现状最安全。
`text` 取**源 WSC 的日文原文**（与 12 个宿主插入段的做法一致：屏幕上的中文由 lng 覆写）。

## 池序号：重算，不顺延

`14` 的 `id` 与 `0f` 各条目的 `strid` 是**同一个「文件出现序」字符串池**
（`doc/wsc_to_ws2_conversion.md` §3.2）。实测**全库 363 个脚本的 `id`/`strid` 都严格
等于「按出现序分配的池序号」**，所以在插入后**整体重算**即可 —— 比「找插入点之后的
逐条 +n」简单得多，也不会漏掉 `0f` 里夹着的条目。

`01`（绝对偏移）在本批这些脚本里**实测全部为 0**，所以只有序号要动。

## lng 同步

lng 是**位置对应**的（第 N 条 lng ↔ 脚本里第 N 个占位，`14` 与 `0f` 各条目都占位）。
所以在插入点对应的 lng 位置插入 n 条中文即可，后面的自动顺延。
"""
import argparse
import io
import json
import shutil
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tool.lng import parse_ccs_both                      # noqa: E402
from tool import arcbuild, lng as lngmod, speaker, wsc, ws2, ws2disasm  # noqa: E402
from tool.wsc2ws2 import decrypt_wsc                          # noqa: E402

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

TMP = ROOT / 'tmp' / 'textfix'
RIO = ROOT / 'asset' / 'Rio.arc'
BACKUP = ROOT / 'asset' / 'Rio.arc.before_insert_deleted'
WSC_DIR = ROOT / 'resource' / 'corpus' / 'wsc'
CCS_DIR = ROOT.parent / 'cross-channel_chinese-localization_project' / 'Scripts' / '20150412'

# 中文/日文字形（汉化组 CCS 的 `[...]` 前缀）-> Steam 的 `%LC` 名。
# **从 `resource/speaker_map.json` 读**（`tool/speaker.zh_to_en()`），不再内联一份 ——
# 原先这里手抄 17 条，与 tool/wsc2ws2.py、script/rename_speakers.py 各自的表重复。
SPK2LC = speaker.zh_to_en()


def jl(p):
    return [json.loads(l) for l in Path(p).read_text(encoding='utf-8').splitlines() if l.strip()]


LC = '%' + 'LC'      # 引擎的说话人标记：`%LC<英文名>` → 查 NameTable.txt 换中文


def cstr(s):
    """CP932 NUL 结尾串。

    ⚠️ **必须用 `cp932` 而不是 `shift_jis`**（2026-09-15 更正）：Python 的 `shift_jis`
    没有微软扩展，`U+FF5E`（全角波浪号 `～`）之类编码不了，会被 `errors='replace'`
    静默写成 `?` —— 实测源 WSC 的 `５００～９００円` 插进去变成了 `５００?９００円`。
    `cp932` 把 `U+FF5E` 映到 `0x8160`，与游戏一致；项目其它工具（`tool/wsc2ws2.py`）
    用的也是 `cp932`。
    """
    return s.encode('cp932', 'replace') + b'\x00'


def mk15(prefix):
    return b'\x15' + (cstr(prefix) if prefix else b'\x00') + b'\x00'


# `2e`（语音）的 22 字节恒定尾 —— 与 `tool/wsc2ws2.py` 的 `VOICE_TAIL` 同源，
# 布局见 `tool/ws2disasm._op_2e`。少写这 12/22 字节会让引擎把后续指令吃进参数里。
VOICE_TAIL = bytes(10) + b'\x0a\x00\x00\x65' + bytes(8)


def mk2e(fname):
    """造一条 `2e` 语音指令：`\\x2e\\x28` + 通道 + NUL + 文件名 + NUL + 22B 尾。

    通道名（`charMIK` 等）= `char` + 文件名前三字母 —— 这是 **Steam 自己的**语音命名规则
    （原生语料零反例；原版名如 `MSA005B1101` 不适用，那种要走查表）。
    """
    f = fname.upper()
    if not f.endswith('.OGG'):
        f += '.OGG'
    # ⚠️ 通道名**必须查表**，不能套「char + 前缀」：
    # 原版语音名前缀是**日文名首字母**（`MSA`=見里、`FYU`=冬子），而 Steam 通道名用英文名
    # （`charMIS`/`charTOU`）—— 不是同一字母序列。真值取两处：① **原生语料实测**的
    # 「前缀→通道」（25 条，见 `backup/Rio.arc` 自己的 `2e` 指令）；② 项目表
    # `resource/speaker_map.json`（经 `tool/speaker.py`）。**查不到报错**，不许静默回退。
    ch = _NATIVE_CHAN.get(f[:3]) or speaker.voice_channel().get(f[:3])
    if not ch:
        raise ValueError('语音 %s 的前缀 %s 在「原生实测」与「项目表」里都查不到通道'
                         '（resource/speaker_map.json）' % (f, f[:3]))
    return b'\x2e\x28' + cstr(ch) + cstr(f) + VOICE_TAIL


_NATIVE_CHAN = None


def native_channels():
    """原生语料实测的「语音名前缀 → 通道名」（从出货版 `backup/Rio.arc` 的 `2e` 指令取）。"""
    global _NATIVE_CHAN
    if _NATIVE_CHAN is None:
        out = {}
        p = ROOT / 'backup' / 'Rio.arc'
        if p.exists():
            for nb, data in arcbuild.read_raw(p):
                if not nb.decode('utf-16-le').upper().endswith('.WS2'):
                    continue
                for i in ws2disasm.disassemble(ws2.decode(data)):
                    if i.opcode == 0x2e:
                        fn = (i.fields.get('file') or '').upper()
                        if fn:
                            out.setdefault(fn[:3], i.fields.get('chan'))
        _NATIVE_CHAN = out
    return _NATIVE_CHAN


_NATIVE_CHAN = native_channels()


def mk14(did, text):
    return (b'\x14' + struct.pack('<H', did) + b'\x00\x00'
            + cstr('char') + cstr(text) + b'\x00')


def spk_lc(ccs_line):
    """CCS 行的 `[说话人]` 前缀 → **完整的 `%LC<英文名>` 标记**（无前缀或认不出 = 清框）。

    ⚠️ **必须带 `%LC`**（2026-09-15 更正）：引擎是拿 `%LC<名>` 去查 `NameTable.txt`
    换成中文的，**裸英文名不会被汉化**。原先这里只返回英文名、漏了 `%LC` ——
    实测 63 处还原格的名字框直接显示 `Taichi`／`Kiri`／`Misato`。
    """
    t = (ccs_line or '').strip()
    if not t.startswith('[') or ']' not in t:
        return ''
    lc = SPK2LC.get(t[1:t.index(']')])
    return (LC + lc) if lc else ''


def opt_strid_offsets(body):
    """`0f` 指令体内各条目 strid 的偏移（body[0]=0f、body[1]=count，之后是各条目）。

    条目格式（见 `ws2disasm._parse_choice_entries`）：
        <u16 strid> <text NUL> <u8 0> <u16 label> <jump>      jump = `07 name` | `06 <u32>`

    **注意 `_read_cstring` 返回的是「NUL 之后」的位置**（`end + 1`），所以下面算完
    text 的 NUL 要 +1；早先按「NUL 本身」算，整体差 1，回读时 `0f` 直接解析失败。
    """
    offs, p = [], 2
    for _ in range(body[1]):
        offs.append(p)
        e1 = body.index(b'\x00', p + 2) + 1          # 第一条 text 的 NUL 之后
        op = body[e1 + 3]                            # jump 操作码
        p = (body.index(b'\x00', e1 + 4) + 1) if op == 0x07 else (e1 + 3 + 5)
    return offs


def opt_jump_fixups(body):
    """`0f` 指令体内，各条目 `06 <u32>` 跳转字段的 **(body 内偏移, 旧目标)**。

    条目里的 `jump` 是 `07 <脚本名>`（脚本名，不随布局变）或 `06 <u32>`（**文件内绝对偏移**）。
    后者必须在插入后回写 —— 实测 `CCB0007` 的 3 个选项、`CCB2007` 的 2 个都是 `06`。
    """
    out, p = [], 2
    for _ in range(body[1]):
        e1 = body.index(b'\x00', p + 2) + 1          # 第一条 text 的 NUL 之后
        op = body[e1 + 3]                            # jump 操作码
        if op == 0x07:
            p = body.index(b'\x00', e1 + 4) + 1
        else:                                        # 0x06 <u32>
            out.append((e1 + 4, struct.unpack_from('<I', body, e1 + 4)[0]))
            p = e1 + 3 + 5
    return out


def eff_names(ins, skip=frozenset()):
    """每个占位（`14` 与 `0f` 条目各占一格）的**生效名字框**：其上最近一条 `15` 的前缀，
    到此为止没有任何 `15` 则为 `None`。`skip`（指令偏移集合）视为不存在。

    引擎只认「下一句之前最后一条 `15`」—— 这就是「生效」的含义（Steam 的写法是
    每句都发一条：有名字发 `%LC<名>`、旁白发空；两条相邻时后者胜）。
    """
    out, cur = [], None
    for x in ins:
        if x.offset in skip:
            continue
        if x.opcode == 0x15:
            cur = x.fields.get('prefix') or ''
        elif x.opcode == 0x14:
            out.append(cur)
        elif x.opcode == 0x0f:
            out.extend([cur] * len(x.fields['entries']))
    return out


def drop_units(ins, drops):
    """被删格要**一并移除**的指令偏移：各被删格的 `14` + 它的**设名/清框 `15`**。

    **守卫（2026-09-25 定）**：Steam 里两格之间有时只有一条 `15`（另一条被省略），
    单看位置认不出它属于哪一格。所以逐条试删：**只要会让任何存活格的「生效名字框」改变，
    那条 `15` 就留下**。⇒ 不变式：删完单元后，每个存活格的名字框与删前逐格相同。
    （一刀切地删「紧邻的两条 `15`」实测会打坏 14 个脚本的名字框，含一处换人。）
    """
    slot, s = {}, 0
    for x in ins:
        if x.opcode == 0x14:
            slot[x.offset] = s
            s += 1
        elif x.opcode == 0x0f:
            for _ in x.fields['entries']:
                slot[x.offset] = s
                s += 1
    kill = {x.offset for x in ins if x.opcode == 0x14 and slot.get(x.offset) in drops}
    idx = {x.offset: j for j, x in enumerate(ins)}
    cand = set()
    for o in kill:
        j = idx[o]
        for k in (j - 1, j + 1):
            if 0 <= k < len(ins) and ins[k].opcode == 0x15:
                cand.add(ins[k].offset)
    if not cand:
        return kill
    base = eff_names(ins, kill)
    keep = {o for o in cand if eff_names(ins, kill | {o}) != base}
    return kill | (cand - keep)


def _raw_slot34(x):
    """`0x34` 的槽名（反汇编器把首字节拆成 `tag`，剩下的是 `t03` ⇒ 补回 `s`）。"""
    return 's' + str(x.fields.get('slot'))


def fix_injected_slots(blob, bound, inj):
    """给**注入块**补「换槽前清槽」：块内每个 `34`，若**同族**（名前 4 字符）此刻还绑在别的槽上，
    就先发一条 `37 <那个槽>` 把它清掉 —— 否则同一个角色会**两个槽同屏**（重叠）。

    `bound` = `{槽名: 族}`，`inj` = **此刻占着该槽的是不是注入块摆的**（槽名集合），
    两者都由调用方在**流式重建**过程中维护（只认 `34` 绑定 / `37` 清除）。
    返回改写后的字节；同时把本块绑定更新进 `bound` / `inj`。
    """
    ins = ws2disasm.disassemble(blob)
    if not ins:
        return blob
    out, pos = bytearray(), 0
    for x in ins:
        out += blob[pos:x.offset]
        pos = x.offset + x.size
        if x.opcode == 0x34:
            s = _raw_slot34(x)
            f = (x.fields.get('file') or '')[:4].upper()
            for s2, f2 in sorted(bound.items()):
                if f2 == f and s2 != s:
                    out += b'\x37' + s2.encode('ascii') + b'\x00'   # 先清掉同族的另一个槽
                    bound.pop(s2, None); inj.discard(s2)
            bound[s] = f
            inj.add(s)
        out += blob[x.offset:x.offset + x.size]
    out += blob[pos:]
    return bytes(out)


def rebuild(raw, ins, plan, drops=frozenset(), vc=None, del2e=frozenset(), plan2e=None,
            stage=None, cell_stage=None):
    """按 plan={占位序: [(prefix, text, zh), ...]} 在指定占位**之后**插入、
    按 drops={占位序} **删格**、按 `vc={占位序: 名}` **同步名字框**、按 `del2e={语音文件名}` **删语音**；
    并重算池序号与**文件内跳转**。

    ⚠️ 跳转回写是 2026-09-19 补的（评审 §7.1）：在此之前 `rebuild` 只重算 `14` 的 id 与
    `0f` 的 strid，**不碰任何文件内偏移**。做法与 `splice_restoration.rebase_offsets` 同源：
    先记「旧指令偏移 → 新偏移」，再把每个跳转目标按该映射改写。

    **删格 = 删整格**（2026-09-25 改）：不只去掉那一格的 `14`，连同它的**设名/清框 `15`**
    一并移除（`drop_units`，带「不改动任何存活格名字框」的守卫）。旧行为只删 `14`、留下
    孤立的 `15`（死件），并让「一格」的边界糊掉。

    **`stage={at_k: [字节…]}`（2026-09-26 补）**：插入行**随行的原版画面演出**（立绘/背景/CG，
    由 `script/build_original_stage.py` 用同一个转换器转出）。按位置与 `plan[at_k]` 一一对应，
    在每行的 `2e`/`15`/`14` **之前**发出（原生顺序：演出块 → `2e` → `15` → `14`）。

    **`cell_stage={槽位: 字节}`（同日补，洞 2）**：给**某个已有格**补原版随行画面演出 ——
    用于「台词取自原版、但 Steam 骨架没把说话人立绘摆上屏」的格（顶层覆盖层
    `resource/text_map_stage.json`），发法同上。

    **指令排布照抄 Steam**：相邻的两条 `15` 一律**原样发出**（此前是「后者覆盖前者」⇒ 把
    Steam 每句自带的清框压掉了，全库 273 个脚本因此偏离原生写法）。

    **`vc` / `del2e`（2026-09-20 补，为「覆盖」类处置）**：把某格的显示文本换成**另一个人**的原版台词时，
    该格的 `%LC` 必须同步（「不得改动名字框」的本意就是**台词与角色名一致**）；
    而该格上若挂着**借自别处**的语音，也要一并删掉 —— 否则音文不符。
    - `vc[占位序] = 名` ⇒ 把**紧邻该格之前的那条 `15`** 的前缀改成 `%LC<名>`；
    - `del2e` ⇒ 删掉文件里 `file` 命中该集合的 `2e`。
    """
    out = bytearray()
    remap = {}                      # 旧指令偏移 → 新输出偏移
    fix = []                        # (新输出里的绝对位置, 旧目标)
    pos, pool, si = 0, 0, 0
    buf15 = None                    # 尚未发出的最后一条 `15`（其前缀可能要按下面那格改写）
    buf_pfx = ''                    # 缓冲的 `15` 的前缀（插入段发完要**恢复现场**）
    kill = drop_units(ins, drops) if drops else set()   # 被删格：`14` + 其设名/清框 `15`
    nxt = {ins[j].offset: ins[j + 1] for j in range(len(ins) - 1)}
    bound = {}                      # {槽名: 立绘族} —— 流式维护，供「换槽前清槽」判据用
    inj = set()                     # 此刻占着该槽的是「注入块摆的」—— 只有它会成为被清对象
    for i in ins:
        out += raw[pos:i.offset]
        pos = i.offset + i.size
        if i.offset in kill:
            if i.opcode == 0x14:
                si += 1                        # 删格：整格不输出（含其设名/清框）
            continue
        # `plan[-1]` ⇒ **补在脚本最前**（首格之前）。必须发在缓冲的 `15` **之前**，
        # 否则首格自己的名字框会被插入段的 `15` 顶掉。
        if i.opcode == 0x14 and si == 0 and plan and -1 in plan:
            _st = (stage or {}).get(-1, [])
            for _j, (prefix, text, _zh, *_v) in enumerate(plan[-1]):
                if _j < len(_st) and _st[_j]:
                    out += fix_injected_slots(_st[_j], bound, inj)   # 随行演出（含换槽前清槽）
                if _v and _v[0]:
                    out += mk2e(_v[0])
                out += mk15(prefix)
                out += mk14(pool, text)
                pool += 1
            if buf15 is None:              # 首格自带 `15` 时无需恢复（下面会原样发出）
                out += mk15(buf_pfx)
        # 到了非 `15` 指令：先把缓冲的 `15` 发出（若下面那格要改名，先改）
        if i.opcode != 0x15 and buf15 is not None:
            if i.opcode == 0x14 and vc and si in vc:
                buf15 = mk15(vc[si]); buf_pfx = vc[si]
            out += buf15
            buf15 = None
        elif i.opcode == 0x14 and vc and si in vc:
            # 该格**不自带名字框**（原布局靠继承上一格）。若不对它改名，它会沿用上一个人的名字
            # —— 全库 1,263 个这样的格子，此刻恰好同人所以屏上没露，换人即张冠李戴。
            # 主动补一条 `15`（引擎只看最后一条，行为不变，但不再依赖运气）。
            out += mk15(vc[si]); buf_pfx = vc[si]
        # `cell_stage={槽位: 字节}`（2026-09-26 补，洞 2）：该格**补原版随行画面演出** ——
        # 用于「台词取自原版、但 Steam 骨架没把说话人立绘摆上屏」的格（见 resource/text_map_stage.json）。
        # 发在 `2e`/`15`/`14` 之前（原生顺序），且必须在 `remap` 之前（否则跳转偏移会指到它）。
        if cell_stage and i.opcode in (0x14, 0x0f) and cell_stage.get(si):
            out += fix_injected_slots(cell_stage[si], bound, inj)
        # `plan2e={占位序: 文件名}` ⇒ 给该格**补挂**一条 `2e`。
        # 位置与原生一致：`15`(清名) … `2e` … `15 <名>` … `14`，故发在缓冲的 `15` **之前**。
        if i.opcode == 0x14 and plan2e and si in plan2e and si not in drops:
            out += mk2e(plan2e[si])
        # **骨架自己的立绘绑定**：换槽前清槽（**跨块**）。若同族此刻还挂在**注入块留下的**槽上，
        # 先补一条 `37 <那个槽>` —— 否则同一角色两槽同屏（实测 CCA0030 見里：洞 2 注入 `st05`、
        # 骨架随后改绑 `st03`，注入的那张一直没撤 ⇒ 两个見里）。
        # ⚠️ 只清**注入来源**的槽：骨架原生的同族双槽是原生行为，一律不动。
        #    （口径：族 = 文件名前 4 字符；在每个 `14` 处采样槽占用；数「状态跳变」处数。
        #      `backup/Rio.arc` 全库 = **6 处**。）
        if i.opcode == 0x34:
            _s = _raw_slot34(i); _f = (i.fields.get('file') or '')[:4].upper()
            for _s2, _f2 in sorted(bound.items()):
                if _f2 == _f and _s2 != _s and _s2 in inj:
                    out += b'\x37' + _s2.encode('ascii') + b'\x00'
                    bound.pop(_s2, None); inj.discard(_s2)
        remap[i.offset] = len(out)
        if i.opcode == 0x15:
            if buf15 is not None:
                out += buf15                       # 保持 Steam 排布：相邻 `15` 全部保留
            remap[i.offset] = len(out)
            buf15 = bytes(raw[i.offset:pos])       # 先不发，等看到下一格再决定
            buf_pfx = i.fields.get('prefix') or ''
            continue
        if i.opcode == 0x2e and del2e and (i.fields.get('file') or '') in del2e:
            continue                               # 删该语音（借自别处、与新台词不符）
        body = bytearray(raw[i.offset:pos])
        new_at, at_key = [], None
        if i.opcode == 0x14:
            at_key = si
            struct.pack_into('<H', body, 1, pool)     # id 在指令内偏移 1
            pool += 1
            new_at = plan.get(si, [])
            si += 1
        elif i.opcode == 0x0f:
            for off in opt_strid_offsets(body):
                struct.pack_into('<H', body, off, pool)
                pool += 1
            for boff, tgt in opt_jump_fixups(body):
                fix.append((len(out) + boff, tgt))
            si += len(i.fields['entries'])
            at_key = si - 1
            new_at = plan.get(at_key, [])             # 「选项块之后」= 最后一个条目之后
        elif i.opcode == 0x06:                        # 独立 `06 <u32>` 无条件跳转
            fix.append((len(out) + 1, i.fields['target']))
        elif i.opcode == 0x01 and i.fields.get('mode') == 0x85:
            fix.append((len(out) + 12, i.fields['b']))
        out += body
        if i.opcode == 0x34:                     # 骨架自己的立绘绑定也进状态
            _s = _raw_slot34(i)
            bound[_s] = (i.fields.get('file') or '')[:4].upper()
            inj.discard(_s)                      # 骨架摆的不是「注入来源」
        elif i.opcode == 0x37:
            _n = i.fields.get('name')
            if _n == '*':
                bound.clear(); inj.clear()
            else:
                bound.pop(_n, None); inj.discard(_n)
        _st = (stage or {}).get(at_key, []) if at_key is not None else []
        for _j, (prefix, text, _zh, *_v) in enumerate(new_at):
            # 该行在原版**有画面演出** ⇒ 先原样补回（与就地插入同一套转换器转出的字节）
            if _j < len(_st) and _st[_j]:
                out += fix_injected_slots(_st[_j], bound, inj)       # 含「换槽前清槽」
            # 该行在原版**有录音** ⇒ 照原生布局先发一条 `2e`（`2e` … `15 <名>` … `14`）
            if _v and _v[0]:
                out += mk2e(_v[0])
            out += mk15(prefix)
            out += mk14(pool, text)
            pool += 1
        if new_at:
            # **恢复现场**：插入段最后那格会把名字框改成它自己的说话人；若紧随的原始格
            # **不自带名字框**（靠继承上一格），它就会张冠李戴 —— 全库 1,263 个这样的格子。
            # ⚠️ 紧随的原始指令本身就是 `15`（Steam 每句自带清框）时**无需恢复** ——
            #    下面会把它原样发出，恢复反而多插一条非原生的 `15`。
            _nx = nxt.get(i.offset)
            if _nx is None or _nx.opcode != 0x15:
                out += mk15(buf_pfx)
    if buf15 is not None:
        out += buf15
    out += raw[pos:]
    bad = 0
    for o, tgt in fix:
        nt = remap.get(tgt)
        if nt is None:                                # 目标不是指令边界 ⇒ 不回写，如实告警
            bad += 1
            continue
        struct.pack_into('<I', out, o, nt)
    if bad:
        print('   ⚠ %d 处文件内跳转的目标不是指令边界，未回写' % bad)
    return bytes(out)


def _seg_done(ins, after_k, seg):
    """**逐段**判这一趟是否已经插过（2026-09-16 修）。

    ⚠️ 判据必须**按段**，不能按脚本 —— 原来只要**任何一段**命中就返回 True、
    整脚本跳过。实测 `CCD4003A_EN` 的两段状态不同：`after_k=143` 已插、
    `after_k=398` 从未插，于是那 24 行被**永久跳过**。

    判据用「插入点之后紧邻的那个**占位**（`14` 各占一格、`0f` 的每个条目也各占一格，
    lng 就是这么对应的）的文本，是否正是插入段的第一条」。不能按 `14` 的下标算 ——
    有选项表的脚本里两者不等。
    """
    slots = []
    for i in ins:
        if i.opcode == 0x14:
            slots.append(i)
        elif i.opcode == 0x0f:
            slots.extend([None] * len(i.fields['entries']))
    if not seg or after_k + 1 >= len(slots):
        return False
    i = slots[after_k + 1]
    return i is not None and i.fields.get('text') == seg[0][1]


def src_lines(stem):
    """源 WSC 的 {CCS 行号: 日文文本}。

    ⚠️ **映射是「对话序 + 1」，不是「`id` + 1」**（2026-09-15 实测更正）：
    `id` 在部分脚本里**有跳号**（`CCB2007` 的 id 走 0..110 而只有 109 条对话、
    `CCB0007` 走 0..162 而只有 160 条），拿 `id + 1` 当 CCS 行会整体错位 ——
    实测 `CCB2007` 会差 2 行（插入的日文变成前两句、与中文对不上）。
    改用对话序后在**全库 41,629 条上 100% 吻合**（逐条比日文正文，去掉 `[说话人]`
    包裹与控制符后逐字符相等）。

    `build_host_lng.py` 用的是 `id + 1`；它在 12 个宿主上实测正确（那些脚本的 id 恰好
    连续），但对有跳号的脚本会错 —— 已记入 `doc/lessons-learned.md`。
    """
    p = WSC_DIR / (stem + '.WSC')
    if not p.exists():
        return {}
    raw = p.read_bytes()
    try:
        instrs = wsc.disassemble(raw)
    except Exception:
        instrs = wsc.disassemble(decrypt_wsc(raw))
    dlg = [i for i in instrs if i.opcode in (0x41, 0x42)]
    return {k + 1: i.fields.get('text', '') for k, i in enumerate(dlg)}


def src_voices(stem):
    """源 WSC 的 {CCS 行号: 原版录音名}（`0x23` 紧随其后那条对话）。

    行号口径与 `src_lines` 一致（对话序 + 1）。无录音的行不入表。
    """
    p = WSC_DIR / (stem + '.WSC')
    if not p.exists():
        return {}
    raw = p.read_bytes()
    try:
        instrs = wsc.disassemble(raw)
    except Exception:
        instrs = wsc.disassemble(decrypt_wsc(raw))
    out, pend, d = {}, None, 0
    for i in instrs:
        if i.opcode == 0x23:
            pend = (i.fields.get('name') or '').split('.')[0]
        elif i.opcode in (0x41, 0x42):
            if pend:
                out[d + 1] = pend.upper() + '.OGG'
                pend = None
            d += 1
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--write', action='store_true')
    args = ap.parse_args()

    items = json.loads((TMP / 'orphan-insert.json').read_text(encoding='utf-8'))
    idx = json.loads((TMP / 'index.json').read_text(encoding='utf-8'))
    members = {n.decode('utf-16-le').upper(): v for n, v in arcbuild.read_raw(RIO)}
    by = {}
    for it in items:
        by.setdefault(it['script'], []).append(it)

    out, newdata = io.StringIO(), {}
    out.write('%-14s %-14s %3s %8s %-22s %s\n'
              % ('脚本', '源 CCS 行', '条', '插于第几格后', '说话人', '首行（源日文）'))
    for s in sorted(by):
        raw = ws2.decode(members[s + '.WS2'])
        ins = ws2disasm.disassemble(raw)
        slots_kind = []
        for i in ins:
            if i.opcode == 0x14:
                slots_kind.append('dlg')
            elif i.opcode == 0x0f:
                slots_kind.extend(['opt'] * len(i.fields['entries']))
        jp, zh = parse_ccs_both(CCS_DIR / (idx[s]['ccs'] + '.CCS'))
        src = src_lines(idx[s]['ccs'])
        L = lngmod.parse_lng(members[s + '.LNG'])

        plan = {}
        for it in sorted(by[s], key=lambda x: x['after_k']):
            a, b = it['rows']
            # 插入点须是**占位格**（`14` 或 `0f` 条目各占一格）。允许落在 `0f` 条目之后
            # =「选项块之后」（2026-09-19 放宽）——`rebuild` 会一并回写该选项的 `06` 跳转。
            assert slots_kind[it['after_k']] in ('dlg', 'opt'), '%s: 插入点不是占位格' % s
            rows = list(range(a, b + 1))
            plan[it['after_k']] = [
                (spk_lc(jp.get(n, '')), src.get(n, jp.get(n, '')),
                 lngmod.strip_speaker_wrap(zh.get(n, ''))) for n in rows]
            out.write('%-14s %-14s %3d %8d %-22s %s\n'
                      % (s, 'CCS#%d-%d' % (a, b), len(rows), it['after_k'],
                         ','.join(repr(x[0]) for x in plan[it['after_k']][:3]),
                         plan[it['after_k']][0][1][:26]))
        # **幂等守卫（逐段）**：已经插过的段**过滤掉**，只插缺的。
        # ⚠️ **不能按脚本跳过** —— 实测 `CCD4003A_EN` 的两段状态不同
        # （`after_k=143` 已插、`after_k=398` 从未插），按脚本跳过会让缺的那段永远补不上。
        done = [ak for ak, seg in plan.items() if _seg_done(ins, ak, seg)]
        if done:
            print('%-14s 已有 %d 段插过，本次只插余下 %d 段'
                  % (s, len(done), len(plan) - len(done)))
            plan = {ak: seg for ak, seg in plan.items() if ak not in done}
        if not plan:
            continue
        raw2 = rebuild(raw, ins, plan)
        # lng：**按占位序重建**（插入的格取源 CCS 中文，其余沿用原 lng）。
        # 不要「从后往前往 L 里插」—— 有多个插入段时下标会漂移（实测 `CCD0023_EN`
        # 的第二个段被第一个推移了 3 格，正是第一段的条数）。
        newL, oi = [], 0
        for si in range(len(slots_kind)):
            newL.append(L[oi])
            oi += 1
            for _spk, _ja, zt, *_v in plan.get(si, []):
                newL.append(zt)
        L = newL
        newdata[s + '.WS2'] = ws2.encode(raw2)
        newdata[s + '.LNG'] = lngmod.encode_lng(L)
        back = ws2disasm.disassemble(ws2.decode(newdata[s + '.WS2']))
        n14 = sum(1 for i in back if i.opcode == 0x14)
        n0f = sum(len(i.fields['entries']) for i in back if i.opcode == 0x0f)
        if n14 + n0f != len(L):
            raise SystemExit('%s: 回读不符 ws2 占位 %d != lng %d' % (s, n14 + n0f, len(L)))
    sys.stdout.write(out.getvalue())

    print()
    print('脚本 %d 个、区段 %d 个、共插入 %d 行' % (len(by), len(items), sum(x['n'] for x in items)))
    if not args.write:
        print('（未写入；加 --write 才改 asset/Rio.arc）')
        return 0
    if not BACKUP.exists():
        shutil.copy2(RIO, BACKUP)
        print('[备份] %s' % BACKUP)
    members.update(newdata)
    order = [n for n, _ in arcbuild.read_raw(RIO)]
    merged = [(nb, members[nb.decode('utf-16-le').upper()]) for nb in order]
    arcbuild.write_arc(merged, RIO)
    back = {n.decode('utf-16-le').upper(): v for n, v in arcbuild.read_raw(RIO)}
    for k, v in newdata.items():
        if back[k] != v:
            raise SystemExit('[失败] 回读不一致：%s' % k)
    cnt, size, _ = arcbuild.verify(RIO)
    print('[写入] 回读校验通过（%d 个成员，%d 字节）' % (cnt, size))
    return 0


if __name__ == '__main__':
    # ⚠️ 已废弃（2026-09-20）：本文件现在只作**工具库**被 `script/apply_text_map.py` import
    # （`rebuild` / `src_lines` / `spk_lc` / `LC`）。**不要再单独运行它** ——
    # 它写的只有结构，**不含**表驱动的 lng、名字框同步与借用语音删除，
    # 跑一次就会把 asset 写回旧口径（且不报错）。
    raise SystemExit('已废弃：结构 + lng 请用 `python script/apply_text_map.py --write` 一次产出。')
