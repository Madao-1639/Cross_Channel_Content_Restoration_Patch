# -*- coding: utf-8 -*-
"""结构写盘器：把「还原插入格」「删格」「名字框同步」「借用语音删除」落到 ws2 字节上。

**唯一调用方**是 `script/apply_text_map.py`（经 `tool.textplan` 算出计划后交给 `rebuild`）。
它原为 `script/insert_deleted_dialogues.py`，兼作独立脚本；那个入口早已废弃
（只写结构、不含表驱动的 lng 与名字框同步，单跑会把 asset 写回旧口径且不报错）—— 已删除。

## 插什么

**只有对话，两条一组**：

    15 <prefix> 00 00                              SetDisplayName（prefix 空 = 清框）
    14 <u16 id> 00 00 "char" 00 <text> 00 00       DisplayMessage

**不搬任何演出指令**（立绘 / BGM / 渐变 / CG 一律不插）——「不新增原版不存在的演出」
是项目铁律。`text` 取**源 WSC 的日文原文**（屏幕上的中文由 lng 覆写）。

## 池序号：重算，不顺延

`14` 的 `id` 与 `0f` 各条目的 `strid` 是**同一个「文件出现序」字符串池**
（见 [wsc_to_ws2_conversion.md](../doc/wsc_to_ws2_conversion.md)）。实测全库 363 个脚本都
严格等于「按出现序分配的池序号」，所以插入后**整体重算**即可 —— 比逐条 `+n` 简单，
也不会漏掉 `0f` 夹着的条目。

## 公开 API

- `rebuild(...)` —— 主入口：在 `plan` 指定的格后插 `15+14`、按 `drops` 删格、
  按 `vc` 同步名字框、按 `del2e`/`plan2e` 增删语音、按 `stage` 补随行演出
- `src_lines(stem)` / `src_voices(stem)` —— 源 WSC 的逐行正文 / 录音名
- `spk_lc(ccs_line)` / `LC` —— 说话人标记（`%LC<名>`）
"""
import struct
from pathlib import Path

from tool import arcbuild, speaker, wsc, ws2, ws2disasm
from tool.wsc2ws2 import decrypt_wsc

ROOT = Path(__file__).resolve().parent.parent
WSC_DIR = ROOT / 'resource' / 'corpus' / 'wsc'

# 中→英说话人名反查（从 `resource/speaker_map.json` 读，不再内联一份）
SPK2LC = speaker.zh_to_en()


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
