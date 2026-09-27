"""WSC (WillPlus) -> WS2 (AdvHD) 忠实指令集转换器。

原理与完整指令对照见 doc/wsc_to_ws2_conversion.md。设计原则：

1. 忠实还原 —— 每条原版演出指令要么映射为等价 WS2 指令序列，要么明确跳过并
   计入 report（绝不静默丢弃、绝不添加原版没有的内容）。
2. 核心功能：文本(0x41/0x42)、语音(0x23)、CG/背景(0x46/0x48)、场景切换(0x07/0x09/0xff)。
   完整支持：SE(0x00)、BGM(0x0a)、遮罩(0x54)、选项(0x02)、文件内跳转(0x06)。
3. 字节模板全部取自 Steam 原生脚本语料（tmp/ws2_analysis/opcode_table.md）与
   Res303 实测可玩的 CNR 块，见各 emit_* 函数，勿随手改动。
"""
import json
import os
import re
import struct

from tool import speaker
from tool.wsc import disassemble

# `0x48` 的形态判据：名字属于立绘族（Steam 侧是 `.PNA`）才发立绘块，其余一律发图像块。
# 见 `_Emitter.run()` 里 0x48 分支的说明。
PORTRAIT_RE = re.compile(r'^T[CB]')

# ---------------------------------------------------------------------------
# 说话人映射与语音通道：**从 `resource/speaker_map.json` 读**，本文件不再内联一份。
# 原先这里内联了 33 条日文->英文（SPEAKER_MAP）与 12 条前缀->通道（VOICE_CHANNEL），
# 与 script/rename_speakers.py、script/insert_deleted_dialogues.py 及文档各写一份、
# 已经漂移。现在由 `tool/speaker.py` 统一读表（表见 resource/README.md）。
# 键名保持不变，下游 `from tool.wsc2ws2 import SPEAKER_MAP` 不用改。
# 表外名字原样保留进 `%LC` 并产生 warning（低置信度条目待实机校对，表里标了
# `confidence: low`）。
# ---------------------------------------------------------------------------
SPEAKER_MAP = speaker.ja_to_en()

# 原版语音名前缀 -> Steam 语音通道名。**不能套「char + 前缀」**：原版前缀是日文名首字母
# （`MSA`=見里、`FYU`=冬子、`YKI`=友貴、`MMN`=ママン），Steam 通道用的是英文名
# （`charMIS`/`charTOU`/`charTOM`/`charOBA`）。表来自资源表的 channel 列。
# 注：Steam 侧**自身**的语音名（`MIK_0686.OGG` 等）确实满足「char + 前缀」，
# 但那与这套原版前缀是两码事，不要混用。
VOICE_CHANNEL = speaker.voice_channel()

# BGM 曲目 -> Steam 音乐鉴赏 id（原生语料规则：id = 1049 + 曲目号，BGM015->1064）
BGM_ID_BASE = 1049

# WS2 常量字节模板（全部取自原生语料，勿改）
VOICE_TAIL = bytes(10) + b'\x0a\x00\x00\x65' + bytes(8)           # 2e 的 22B 恒定尾
# 28 的 22B 尾（模态形态，全语料 1086/1556；布局见 tool/ws2disasm._op_28）。
# 早期误写成 10B：那会让引擎少读 12 字节、把后续指令吃进参数里。
SE_TAIL = bytes(10) + b'\x0a\x00' + bytes(5) + b'\x01' + bytes(4)
LAYER_ORDER = b'\x04LAYER_ORDER\x00'
VAR_10_13 = b''.join(b'\x09\x00' + bytes([v]) + b'\x00\x00\x00\x80\x3f'
                     for v in (0x0a, 0x0b, 0x0c, 0x0d))           # 图层透明度 1.0 x4
PORTRAIT_ATTRS = b'\x02\x01\x04\x03\x00\x00\x00\x01\x00\x02\x00'  # 39 槽位属性(c=4 形)
# `39 DisplayCharacterImage <通道> NUL <02 01 c> [c 个 u16 帧号]`
# **帧号 = PNA 记录表的下标（0-based）**，`c` = 画几条；形态只与目标 PNA 的**记录数**有关
# （原生交叉表零例外，见 doc/engine-mechanics.md）：
#   4 条记录 -> c=4，帧号 [3, 0, 1, 2] = 大图 + 3 个表情补丁（先大图后叠补丁）
#   1 条记录 -> c=1，帧号 [0]           = 唯一一条（也是大图）
# 另有一种「只用大图」形态 `c=1 [3]`（原生 29 处，渲染上静态），**不是槽的属性**而是逐次显示
# 的选择；本补丁统一用上面的形态，与周边原生段落一致（宿主 265 块里 263 块如此）。
# 记录数未知时按 4 条形态发射并逐条告警 —— 照抄 4 条形态去调 1 条记录的 PNA 会**越位**。
PORTRAIT_ATTRS_BY_LAYERS = {
    1: b'\x02\x01\x01\x00\x00',
    4: b'\x02\x01\x04\x03\x00\x00\x00\x01\x00\x02\x00',
}
# 「只用大图」形态（4 条记录的 PNA 只画下标 3 那一张）。当前**未使用**，留作对照与将来可选。
PORTRAIT_ATTRS_BIG_ONLY = b'\x02\x01\x01\x03\x00'
# 46 的「重置」形态：cfg = 06 00 f0，四个 f32 是 10/11/12/13 的占位值。
# 全语料 7,668 组 `34`+`39` 块里，这一条**恒为**紧跟 LAYER_ORDER 的第一条 46，不随场景变化。
PORTRAIT_46_RESET = (b'\x06\x00\xf0'
                     b'\x00\x00\x20\x41'      # 10.0
                     b'\x00\x00\x30\x41'      # 11.0
                     b'\x00\x00\x40\x41'      # 12.0
                     b'\x00\x00\x50\x41')     # 13.0

# 立绘：「句柄」与「位置」是**两条不同的东西**。
#   * `34 <通道名> <PNA>` 只是把资源绑到一个具名句柄（`st01`..`st12`），回答"哪一层"，
#     **不回答"画在哪"**。同一通道名在语料里左右两侧都出现过。
#   * **位置由 `46 MoveBackground <通道名> <u8×3> <f32 x> <f32 y> …` 给出**（原点=屏幕中心，
#     单位像素，立绘 `y` 恒 `-40`）。实测三人同框：`st03=-180 / st05=+400 / st07=-399`。
#     站位词表 `{0, ±275, ±400}`；多人组合 `(-275,+275)`=左|右、`(-399,0,+400)`=左|中|右。
# 句柄选择只求"别撞上宿主正在用的那一个"（同句柄 → 新图顶掉旧图；不同句柄 → 两层并存）：
#   * 在场 1 人                -> st03（全语料 5128/5603 = 92%）
#   * 源槽 3 且槽 4 同时在屏   -> st05（「同框第二张」；角色偏侧是 73–89% 的惯例，不是规则）
#   * 其余                     -> st03
# 本补丁 12 个宿主的插入段：33/34 个时刻是单人，全部落在高置信分支。
# 位置由原版 `48` 的 `xabspos` 经 `steam_x()` 量化为 Steam 的 `{0, ±275, ±400}` 之一。
# ⚠️ **这只是启发式，未获真值验证**。曾用「角色代码 + 同角色内 x 排序」配对两侧，得一致率 94.7%，
# 但**基准是配对本身**（配对错则一致率无意义），不是误差度量 —— 那个标定脚本已随锚点输入清理而删除。
# 抽查分歧可见**真实反例**：CCA0007 里原版 `TCST` x=237（中），Steam 却给 -275（左），与「237→中」矛盾。
# 根因：**Steam 对场景重新编排**——同一角色的站位取决于当时谁在场，**不存在纯 x 的函数**。
# 精确复现须按**对白内容**对齐两侧帧（尚未做）；现表只保证大致的左/中/右。
# 详见 doc/wsc_to_ws2_conversion.md §3.3。
PORTRAIT_SLOT_MAIN = 'st03'
PORTRAIT_SLOT_SECOND = 'st05'

# 原版 x（**有符号** u16；800 宽屏幕）-> Steam 站位（屏幕中心为原点，1280 宽）。
# ⚠️ **启发式，未验证为真值**（见上方说明；那个 94.7% 是自指一致率）。
# 元组为 (上界, 输出值)，`None` = 兜底。
POSITION_TIERS = (
    (0, -399.0),        # 远左
    (150, -275.0),      # 左
    (350, 0.0),         # 中（原版 187…300 的主簇）
    (470, 275.0),       # 右（原版 362…450 的主簇）
    (None, 400.0),      # 远右
)
PORTRAIT_Y = -40.0      # 立绘标准高度（全语料 7,663/7,849 = 98%，这一条是实测的）


def steam_x(x_raw):
    """原版 `48.xabspos`（无符号读出的 u16）-> Steam `46` 的 x。见上方 POSITION_TIERS。"""
    x = x_raw - 0x10000 if x_raw >= 0x8000 else x_raw
    if x == 0:          # ⚠️ 原版 x==0 是**贴左缘**（引擎无分支，见 doc/engine-mechanics.md）；
                        # 此处特判成 0 是**已知偏差**，会与同屏居中角色叠（CCD0022A 实证）。
                        # 保留原行为待定；精确处理走 resource/position_overrides.json。
        return 0.0
    for bound, val in POSITION_TIERS:
        if bound is None or x < bound:
            return val
    return 0.0


# 立绘位置**覆盖表**（`resource/position_overrides.json`）：逐条记录**不符合 `steam_x()`** 的
# `(源脚本, 源行, 角色) -> Steam x`。⚠️ 它是**已定稿的输入**：生成它的标定脚本因锚点输入
# （原判定链的语音锚点）已清理而**不可重跑**；要重标定须先重建锚点。
# 命中就用表值、未命中走 `steam_x()`。**懒加载、只读**；表缺失时为空（退化为纯 `steam_x()`）。
_OVERRIDES = None


def _load_overrides():
    global _OVERRIDES
    if _OVERRIDES is None:
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            'resource', 'position_overrides.json')
        try:
            with open(path, encoding='utf-8') as fh:
                _OVERRIDES = json.load(fh).get('overrides', {})
        except FileNotFoundError:
            _OVERRIDES = {}
    return _OVERRIDES


def position_override(stem, dialogue_row, char):
    """查覆盖表：`(源脚本 stem, 源行 dialogue_row(1-based), 角色代码)` -> x；无则 None。"""
    return _load_overrides().get(stem, {}).get(str(dialogue_row), {}).get(char)


def _lead_code(name):
    """立绘文件名 -> 角色代码（前导字母，如 `TCMM0002` -> `TCMM`）。
    `position_overrides.json` 的键按**同一规则**生成，键才能对上。"""
    m = re.match(r'[A-Za-z]+', name or '')
    return m.group().upper() if m else (name or '').upper()


# 场景级立绘位置覆盖（`resource/portrait_position_overrides.json`）：**手工**维护，
# 修正 `steam_x()` 处理不了的（如还原场景里 `x==0` 是贴左缘）。**优先级高于**自动覆盖表。
_SCENE_OVERRIDES = None


def _load_scene_overrides():
    global _SCENE_OVERRIDES
    if _SCENE_OVERRIDES is None:
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            'resource', 'portrait_position_overrides.json')
        try:
            with open(path, encoding='utf-8') as fh:
                _SCENE_OVERRIDES = json.load(fh).get('rules', [])
        except FileNotFoundError:
            _SCENE_OVERRIDES = []
    return _SCENE_OVERRIDES


def scene_portrait_x(stem, char, x_src):
    """查场景级覆盖：`(源脚本, 角色, 源 x)` -> x_steam；无匹配则 None。先匹配先胜。"""
    for r in _load_scene_overrides():
        if r.get('script') != stem:
            continue
        if r.get('char') not in (None, char):
            continue
        if 'when_x' in r and r['when_x'] != x_src:
            continue
        return r['x_steam']
    return None


def portrait_slots(instrs):
    """预处理每个立绘 `48` 的 Steam 槽位，返回 {wsc_offset: 'stNN'}。

    槽位开关 `49` 出现在 `48` **之后**，而 `48` 是发射时机，所以必须先扫一遍。
    这是**近似**（见上方说明），不是从原版推导出来的对应关系。
    """
    vis = {}            # 原版槽号 -> 是否在屏
    out = {}
    for k, i in enumerate(instrs):
        if i.opcode == 0x49:
            a, b = i.fields.get('a'), i.fields.get('b')
            if a is not None and b is not None:
                vis[a] = (b == 1)
        elif i.opcode == 0x48 and PORTRAIT_RE.match(i.fields.get('name', '')):
            src = None
            for j in instrs[k + 1:k + 6]:
                if j.opcode == 0x49 and j.fields.get('b') == 1:
                    src = j.fields.get('a')
                    break
            second = (src == 3 and vis.get(4))
            out[i.offset] = PORTRAIT_SLOT_SECOND if second else PORTRAIT_SLOT_MAIN
    return out

# WSC 0x03/0x49/0x47 等引擎状态原语 —— WS2 由图层块/引擎默认承担，跳过
SKIPPED_OPS = None  # 由 default 分支统一处理并计数


class ConvertOptions:
    """转换参数。资源重命名与出口改写通过 dict 注入，缺省全部忠实原名。"""

    def __init__(self, **kw):
        self.voice_channel = kw.get('voice_channel', 'charCN')
        self.transfer_suffix = kw.get('transfer_suffix', '_EN')
        self.exit_ff_a = kw.get('exit_ff_a', 8)          # 场景出口 ff.a（实测 8 可玩）
        self.bgm_slot = kw.get('bgm_slot', 'bgm01')
        self.se_slot = kw.get('se_slot', 'se01')
        self.bg_slot = kw.get('bg_slot', 'bg01')
        self.default_fade = kw.get('default_fade', 1.0)  # 无 0x22 时的图像渐变秒数
        self.bgm_stop_fade = kw.get('bgm_stop_fade', 1.0)
        self.mask_fade = kw.get('mask_fade', 1.0)
        self.rename_map = kw.get('rename_map', {})       # {'EVCC0001': 'CN_EVCC0001'}
        self.exit_target_map = kw.get('exit_target_map', {})   # stem -> 出口脚本名
        self.transfer_target_map = kw.get('transfer_target_map', {})  # stem -> {原目标: 新名}
        self.steam_exit_map = kw.get('steam_exit_map', {})     # stem -> Steam 对应脚本出口名
        self.steam_choice_map = kw.get('steam_choice_map', {})  # stem -> [各选项目标名或 None]
        self.steam_scripts = kw.get('steam_scripts', set())    # Steam 脚本名集合（大写）
        # -- 切片模式（就地插入调用脚本时用，见 doc/call-chain.md）--------------
        # 字符串池起始序号：`14` 的 id / `0f` 的 strid 在脚本内是「文件出现序」，
        # 就地插入时必须接续宿主在该点已有的池位置，不能从 0 重来。
        self.pool_start = kw.get('pool_start', 0)
        # True = 这是插入到宿主中间的一段，不是独立脚本：
        #   * 不写出口（`07`/`ff` 跳过，宿主保留自己的出口尾段）
        #   * 文件尾的 `0a`（停 BGM）照常发射——独立脚本里由出口交接，切片里不能丢
        #   * 抑制开头的 `16 01 00`（宿主此时对话框已开）
        self.slice_mode = kw.get('slice_mode', False)
        # 运行时可用的资源名集合（大写）。给了才做「不存在就不发射」的防御；
        # 目前只用于蒙版（见 emit_mask）与立绘（见 emit_portrait_block）。
        self.available = kw.get('available', None)
        # 立绘槽位判定（`portrait_slots`）要用**整脚本**的指令流才有上下文 ——
        # 切片转换（`convert_range`）里它只看得到切片那几条，会把「同框第二张」判成主槽。
        # 给了它就按它算槽位（切片只决定**转哪一段**，不决定槽位判定）。
        self.slot_context = kw.get('slot_context', None)
        # `{立绘族(前4字符): 槽名}` —— **沿用宿主/骨架给该角色用的那一个槽**。
        # 不给的话，注入的立绘可能落到别的槽，而骨架在同段时间里也摆着同一个角色 ⇒ **两个同屏**。
        # 见 doc/wsc_to_ws2_conversion.md §3.3「句柄选择的实质是「别撞上宿主正在用的那一个」」。
        self.family_slots = kw.get('family_slots', {}) or {}
        # {PNA 名(大写): 记录数} —— 决定 `39` 的帧号形态（见 PORTRAIT_ATTRS_BY_LAYERS）。
        # 不传时按 4 条形态发射并逐条告警：1 条记录的 PNA 用 4 条形态会**帧号越位**。
        self.pna_layers = kw.get('pna_layers', {})
        # True = 立绘一律用「只用大图」形态（`c=1 [3]`，渲染上静态），不叠加表情补丁。
        # 默认 False：与周边 Steam 原生段落一致（宿主 265 块里 263 块用「大图+补丁」）。
        self.portrait_big_only = kw.get('portrait_big_only', False)


def _f32(x):
    return struct.pack('<f', x)


def _u16(x):
    return struct.pack('<H', x)


def _u32(x):
    return struct.pack('<I', x)


class _Emitter:
    def __init__(self, stem, instrs, opts, dialogue_row_base=0):
        self.stem = stem
        self.instrs = instrs
        self.opts = opts
        # 覆盖表(`position_overrides`)的行号是**全脚本** 1-based；切片转换时
        # `report['dialogues']` 只数切片内的对白，须加上切片前已发的对白数才是绝对行。
        self.dialogue_row_base = dialogue_row_base
        self.out = bytearray()
        self.fixups = []        # (out_off, wsc_target_offset | 'EXIT')
        self.labels = {}        # wsc_offset -> out_off
        self.jump_targets = {i.fields['target_offset'] for i in instrs
                             if i.opcode == 0x06}
        self.consumed = set()   # 已被选项块吸收的 06 指令下标
        self.report = {
            'stem': stem,
            'wsc_instructions': len(instrs),
            'script_index': None,
            'emitted': {},
            'skipped': {},
            'warnings': [],
            'dialogues': 0,
            'dialogue_wsc_ids': [],  # 每条输出对话对应的原始 WSC id（CCS 行号 = id+1）
            'voices': [],
            'images': [],       # (kind, 原名, 输出名)
            'sounds': [],       # (kind, 原名, 输出名)
            'masks': [],
            'choices': [],
            'exit': None,
            'dropped_tail': 0,
        }
        self.textbox_open = False
        self.pending_voice = None
        self.pending_fade = None
        self.suppress_next_wait = False
        self.portrait_slots = portrait_slots(opts.slot_context or instrs)   # 见模块顶部说明
        self.exit_label_off = None
        self.has_choice = False
        self.pool_index = opts.pool_start  # 字符串池序号：`14` 的 id 与 `0f` 的 strid 共用
        self.choice_index = 0  # 本脚本内第几张选项表（用于逐表取 Steam 目标）
        self.slice_mode = opts.slice_mode
        self._lead_textbox_suppressed = False   # 切片模式只抑制开头的第一条 16

    # -- 基础设施 ----------------------------------------------------------
    def warn(self, msg):
        self.report['warnings'].append(msg)

    def count(self, kind=None, op=None):
        if kind is not None:
            self.report['emitted'][kind] = self.report['emitted'].get(kind, 0) + 1
        else:
            key = 'skip %02x' % op
            self.report['skipped'][key] = self.report['skipped'].get(key, 0) + 1

    def mark_labels(self, ins):
        if ins.offset in self.jump_targets:
            self.labels.setdefault(ins.offset, len(self.out))

    def raw(self, b):
        self.out += b

    def emit_name_clear(self):
        """发一条名字框清框 `15 00 00`。

        **只用在每句台词之后**（原生写法：`15[设名] 14 15[清框]`，逐句成对）。
        实测原生 97,904 条 `15` **没有一条游离在 `14` 之外** —— 背景块 / 出口 / 选项表
        之前**都不清框**。此前有个 `maybe_clear()` 在这三处插清框，注释写「保持单条更贴近
        原生语料」，实测**正好相反**（原生每句两条）—— 已删。
        """
        self.out += b'\x15\x00\x00'

    def rename(self, name):
        return self.opts.rename_map.get(name, name)

    def expire_fade(self):
        self.pending_fade = None

    # -- WS2 模板发射 ------------------------------------------------------
    def emit_voice(self, name):
        fname = self.rename(name).upper() + '.OGG'
        self.raw(b'\x2e\x28' + self.voice_channel(name).encode('ascii') + b'\x00'
                 + fname.encode('ascii') + b'\x00' + VOICE_TAIL)
        self.report['voices'].append((name, fname))
        self.count('voice')

    def voice_channel(self, name):
        """由语音名定通道。原版语音名前缀是**日文名首字母**（`MSA`=見里、`FYU`=冬子、
        `YKI`=友貴），而 Steam 侧的通道名用的是**英文名**（`charMIS`/`charTOU`/`charTOM`），
        两者不是同一个字母序列 —— 不能套「char + 前缀」的规则，必须查表。
        表见 doc/localization.md「角色名称映射」，其「Steam 通道」列由 Steam 自己的
        `2e` 指令与紧随的 `%LC` 配对得出（原生 10,693 条），且这些通道名在原生语料里都真实存在。
        """
        pre = name[:3].upper()
        ch = VOICE_CHANNEL.get(pre)
        if ch:
            return ch
        # ⚠️ 静默兜底（回退 `charCN`）已废（2026-09-24）：`charCN` 在原生语料里**不存在**，
        # 回退会把通道名写错却不报错。查不到就**报错** —— 映射表已覆盖原生全部 25 种通道。
        raise ValueError('语音前缀 %s（%s）不在通道映射表里 —— 请先登记到 resource/speaker_map.json'
                         % (pre, name))


    def emit_dialogue(self, ins):
        text_bytes = ins.operands[4:-1]
        speaker_jp = None
        if ins.opcode == 0x42:
            spk_end = ins.operands.index(0, 5)
            speaker_jp = ins.operands[5:spk_end].decode('cp932')
            text_bytes = ins.operands[spk_end + 1:-1]
        # 对话 id = 字符串池序号。原生实证：`14` 的 id 与 `0f` 条目的 strid 属于
        # 同一个按文件出现顺序填充的字符串池，选项文本也占槽位——选项之后的对话
        # id 会跳过被占用的号（CCA0006：204 条对话 id 0..203 → 选项 strid
        # 204,205,206 → 下一句 id 207）。
        did = self.pool_index
        self.pool_index += 1
        # 原始 WSC id 保留供配套 lng 生成用（CCS 行号 = 原始 id + 1，见
        # doc/localization.md "翻译来源"；CNR0005_en 还原已验证此关系）
        self.report['dialogue_wsc_ids'].append(ins.fields['id'])

        if self.pending_voice is not None:
            self.emit_voice(self.pending_voice)
            self.pending_voice = None
        self.emit_textbox_open()

        prefix = b'\x00'                          # 空前缀：15 00 00
        if speaker_jp is not None:
            en = SPEAKER_MAP.get(speaker_jp)
            if en is None:
                en = speaker_jp
                self.warn('unknown speaker %r (kept as-is, will render as CJK)' % speaker_jp)
            # CP932 而不是 ASCII：表外说话人是日文名，用 ASCII 编码会直接抛异常
            prefix = b'%LC' + en.encode('cp932') + b'\x00'

        # Steam 版对话格式：15[prefix]00 直接跟 14，无尾部清框指令。
        # 正文写**原版 CP932 原文**（含 %K%P / ruby / \n），与原生脚本同构；
        # 显示文本由同名 .lng 按位置整体替换（原生 CCA0001_en 32 句 ↔ 32 条 lng
        # 逐条对应，见 doc/localization.md）。早期写过 "CN line N" 占位符，会丢掉
        # 原文、让 lng 缺条目时露出占位串，并让往返忠实性校验永远失败。
        # `%N`/`%P` 纯翻页标记格：原生**不带任何 `15`**（实测 1,378 例零前导 `15`）——
        # 它沿用当前名字框。若也给它发 `15[空前缀]`，会把名字框**误清**。
        marker = bool(text_bytes) and not text_bytes.replace(b'%N', b'').replace(b'%P', b'')
        if not marker:
            self.raw(b'\x15' + prefix + b'\x00')
        self.raw(b'\x14' + _u16(did) + b'\x00\x00char\x00' + text_bytes + b'\x00\x00')
        if not marker:
            self.emit_name_clear()  # 原生：每句之后自带清框 `15[]`（`15[设名] 14 15[清框]`）
        self.expire_fade()
        self.count('dialogue')
        self.report['dialogues'] += 1

    def emit_textbox_open(self):
        if self.slice_mode and not self._lead_textbox_suppressed:
            # 切片模式：宿主此时对话框已开，开头的 16 01 00 是多余的
            self._lead_textbox_suppressed = True
            self.textbox_open = True
            return
        if not self.textbox_open:
            self.raw(b'\x16\x01\x00')
            self.textbox_open = True

    def emit_image_block(self, name, fade):
        """0x46 与 0x48-05 -> 背景块（bg01 槽 + LAYER_ORDER + 65 渐变）。"""
        fname = self.rename(name) + '.PNG'
        self.report['images'].append(('bg', name, fname))
        self.raw(b'\x16\x00\x00\x64\x00\x37\x2a\x00'
                 + b'\x33' + self.opts.bg_slot.encode('ascii') + b'\x00'
                 + fname.encode('ascii') + b'\x00\x01\x01'
                 + LAYER_ORDER + VAR_10_13
                 + b'\x46' + self.opts.bg_slot.encode('ascii') + b'\x00' + bytes(19)
                 + b'\x65\x00\x00\x00' + _f32(fade) + _u32(0) + _u16(2))
        self.textbox_open = False
        self.suppress_next_wait = True   # 紧随的 0x4a 是渐变等待，已并入 65
        self.expire_fade()
        self.count('image_block')

    def emit_portrait_block(self, name, offset, x_raw, x_override=None):
        """0x48-04 -> 立绘块（34 + 39 + LAYER_ORDER + 46 重置 + 46 位置）。

        句柄选择见模块顶部说明；**位置**由原版 `xabspos` 经 `steam_x()` 量化而来 ——
        位置不在句柄里（`46` 才是位置），两条 `46` 是 Steam 原生块的标准形态。
        `x_override` 非 None 时改用覆盖表值（见 `position_override`），跳过 `steam_x()`。
        """
        fname = self.rename(name) + '.PNA'
        # 目标 PNA 不在可用集合里 ⇒ **不发射**（绝不产出悬空引用，与 `emit_mask` 同款防御）。
        # 原版有些立绘变体在 Steam 侧没有同档对应物（如 `TCDY0003S`），或双字母变体
        # （`TCYM0000AA`）—— 这类只能跳过并计数。
        if self.opts.available is not None and fname.upper() not in self.opts.available:
            self.count('portrait_skipped_missing')
            self.warn('立绘 %s（-> %s）在归档里不存在，已跳过' % (name, fname))
            return
        # **槽位**：优先沿用「本脚本里该角色（立绘族=名前4字符）用的那一个」（`family_slots`）——
        # 否则注入的立绘可能落到别的槽，而骨架在同段时间里也摆着同一个角色 ⇒ **两个同屏重叠**
        # （实测 `CCA0015` 見里：骨架 st07、注入 st03）。这正是模块顶部说的
        # 「句柄选择的实质是「别撞上宿主正在用的那一个」」。
        slot = self.opts.family_slots.get(fname[:4].upper()) \
            or self.portrait_slots.get(offset, PORTRAIT_SLOT_MAIN)
        s = slot.encode('ascii') + b'\x00'
        # `39` 的帧号形态必须匹配目标 PNA 的记录数（见 PORTRAIT_ATTRS_BY_LAYERS）。
        layers = self.opts.pna_layers.get(fname.upper())
        if self.opts.portrait_big_only and layers == 4:
            attrs = PORTRAIT_ATTRS_BIG_ONLY          # 只用大图（渲染上静态）
        else:
            attrs = PORTRAIT_ATTRS_BY_LAYERS.get(layers)
        if attrs is None:
            self.warn('立绘 %s 的 PNA 记录数未知（%r），按 4 条形态发射' % (fname, layers))
            attrs = PORTRAIT_ATTRS
        x_out = steam_x(x_raw) if x_override is None else x_override
        if x_override is not None:
            self.count('portrait_x_override')
        self.report['images'].append(('portrait', name, fname))
        self.raw(b'\x34' + s + fname.encode('ascii') + b'\x00\x01\x01'
                 + b'\x39' + s + attrs
                 + LAYER_ORDER
                 + b'\x46' + s + PORTRAIT_46_RESET
                 + b'\x46' + s + b'\x00\x00\x00'
                 + _f32(x_out) + _f32(PORTRAIT_Y) + _f32(0.0) + _f32(0.0))
        self.expire_fade()
        self.count('portrait_block')

    def emit_se(self, name):
        fname = self.rename(name).upper()
        self.report['sounds'].append(('se', name, fname))
        self.raw(b'\x28' + self.opts.se_slot.encode('ascii') + b'\x00'
                 + fname.encode('ascii') + b'\x00' + SE_TAIL)
        self.expire_fade()
        self.count('se')

    def emit_bgm_play(self, name):
        fname = self.rename(name).upper() + '.OGG'
        self.report['sounds'].append(('bgm', name, fname))
        self.raw(b'\x1e' + self.opts.bgm_slot.encode('ascii') + b'\x00'
                 + fname.encode('ascii') + b'\x00' + _f32(0.0) + _u32(0) + b'\xff\xff'
                 + b'\x0a\x00\x01\x00\x00\x00\x00')
        num = ''.join(ch for ch in name if ch.isdigit())
        if num:
            self.raw(b'\x0b' + _u16(BGM_ID_BASE + int(num)) + b'\x01')
        else:
            self.warn('BGM name without number: %r' % name)
        self.expire_fade()
        self.count('bgm_play')

    def emit_bgm_stop(self):
        self.report['sounds'].append(('bgm_stop', None, None))
        self.raw(b'\x1f' + self.opts.bgm_slot.encode('ascii') + b'\x00'
                 + _f32(self.opts.bgm_stop_fade))
        self.count('bgm_stop')

    def emit_mask(self, name):
        fname = self.rename(name).upper() + '.PNG'
        # 蒙版资源只有原版有，且是 WillPlus 私有格式（`WIPF`，非 PNG）。
        # 关卡的 `EFMSK_11`/`EFMSK_21` 随 H 场景被 Steam 删除，Steam 的 Graphic.arc 里没有对应
        # PNG（编号与其他蒙版同源，实测 Graphic.arc 有 41 个但独缺这两个）。
        # 传递 `opts.available` 时，指向不存在资源的蒙版**不发射**并计入 report（绝不静默丢弃）
        # —— 与 Res303 的处理一致（它的还原脚本 0 个蒙版，实机验证可玩）。
        if self.opts.available is not None and fname not in self.opts.available:
            self.count('mask_skipped_missing')
            self.warn('mask %s 在归档里不存在（原版独有资源，WillPlus 格式未解码），已跳过'
                      % fname)
            return
        self.report['masks'].append((name, fname))
        self.raw(b'\x66' + fname.encode('ascii') + b'\x00\x65\x64\x00\x00'
                 + _f32(self.opts.mask_fade) + _u32(0) + b'\x02\x00')
        self.count('mask')

    def emit_wait(self, ms):
        self.raw(b'\x11timer01\x00\x00' + _f32(ms / 1000.0)
                 + b'\x12timer01\x00\x01\x00')
        self.count('wait')

    def emit_exit(self, target, b_flag, source):
        """07 <target> | ff a b —— WS2 场景出口。"""
        self.exit_label_off = len(self.out)
        if target:
            self.raw(b'\x07' + target.encode('ascii') + b'\x00')
        self.raw(b'\xff' + _u32(self.opts.exit_ff_a) + _u32(b_flag))
        self.report['exit'] = {'target': target, 'source': source, 'b': b_flag}
        self.count('exit')

    # -- 选项 --------------------------------------------------------------
    def emit_choice(self, idx, ins):
        items = ins.fields['items']
        count = ins.fields['count']
        if count == 0 or not items:
            self.count(None, 0x02)
            return
        self.has_choice = True

        # 分支目标解析（逐选项）：1) 文件内 06 阶梯  2) Steam 对应脚本的选项表
        # 3) 回退到脚本出口（计入 warning，由调用链整合步骤人工接线）
        ladder_idx = []
        j = idx + 1
        while j < len(self.instrs) and len(ladder_idx) < count:
            op = self.instrs[j].opcode
            if op == 0x06:
                ladder_idx.append(j)
            elif op == 0x02:
                break
            j += 1

        # 选项条目的 strid 是**字符串池序号**（= 该处对话 id 计数器），不是 0 基
        # 表内序号：转出来的脚本要和原生一样，让 `14` 的 id 与 `0f` 的 strid
        # 落在同一个池里（strid == 表前对话数 + 更早的选项文本数）。
        strid = self.pool_index

        # 逐表取 Steam 目标：同一脚本可能有多张表（CCD0102 有 12 张），
        # 不能把一张表的目标复用给另一张。
        tables = self.opts.steam_choice_map.get(
            self.stem.upper() + self.opts.transfer_suffix) or []
        if tables and isinstance(tables[0], list):
            steam_targets = tables[self.choice_index] if self.choice_index < len(tables) else []
        else:
            steam_targets = tables if self.choice_index == 0 else []
        self.choice_index += 1
        if len(ladder_idx) == count:
            mode = 'in-file ladder'
        elif len(steam_targets) == count and all(steam_targets):
            mode = 'steam counterpart'
        elif any(steam_targets):
            mode = 'mixed'
        else:
            mode = 'fallback to exit'
        if mode in ('fallback to exit', 'mixed'):
            self.warn('choice targets partially unresolved (%d options, mode=%s, '
                      'ladder=%d, steam=%s)'
                      % (count, mode, len(ladder_idx),
                         len(steam_targets) if steam_targets else None))

        self.report['choices'].append({'count': count, 'mode': mode,
                                       'texts': [it['text'] for it in items]})
        self.raw(b'\x0e\x0b\x00' + bytes([count]) + b'\x00\x01')
        self.raw(b'\x0f' + bytes([count]))
        for i, item in enumerate(items):
            if len(ladder_idx) == count:
                # u32 占位 '@@@@'，apply_fixups 修正为 WS2 输出偏移
                head = _u16(strid + i) + item['_raw_text'] + b'\x00\x00' + _u16(0x0b + i) + b'\x06'
                self.fixups.append((len(self.out) + len(head),
                                    self.instrs[ladder_idx[i]].fields['target_offset']))
                self.consumed.add(ladder_idx[i])
                self.raw(head + b'@@@@')
            elif steam_targets and i < len(steam_targets) and steam_targets[i]:
                self.raw(_u16(strid + i) + item['_raw_text'] + b'\x00\x00' + _u16(0x0b + i)
                         + b'\x07' + steam_targets[i].encode('ascii') + b'\x00')
            else:
                head = _u16(strid + i) + item['_raw_text'] + b'\x00\x00' + _u16(0x0b + i) + b'\x06'
                self.fixups.append((len(self.out) + len(head), 'EXIT'))
                self.raw(head + b'@@@@')
        # 选项文本占用字符串池的 count 个槽位，后续对话 id 顺延
        self.pool_index += count
        self.textbox_open = False
        self.count('choice')

    # -- 主循环 ------------------------------------------------------------
    def run(self):
        instrs = self.instrs
        n = len(instrs)
        i = 0
        while i < n:
            ins = instrs[i]
            self.mark_labels(ins)
            op = ins.opcode

            if i in self.consumed:
                # 选项阶梯中的 06：目标已写进 0e/0f 条目，除非它同时是别的跳转目标
                if ins.offset not in self.jump_targets:
                    i += 1
                    continue

            if op == 0x8c:
                self.report['script_index'] = ins.fields['script_index']
                self.count(None, op)      # WS2 无脚本头（引擎路由层承担）
            elif op == 0xe0:
                self.count(None, op)      # 章节标题：WS2 侧由引擎/图形标题承担
            elif op in (0x41, 0x42):
                self.emit_dialogue(ins)
            elif op == 0x23:
                self.pending_voice = ins.fields['name']
                self.count(None, op)
            elif op == 0x46:
                name = ins.fields['name']
                if name == 'CG_BACK':
                    self.count(None, op)
                    self.warn('0x46 CG_BACK (恢复上一张CG) 无 WS2 等价指令，已跳过')
                else:
                    self.emit_image_block(name, self.take_fade())
            elif op == 0x48:
                name = ins.fields['name']
                # **名字**才是形态判据，不是 params。全语料实测 params 与名字并不一一对应
                # （`01` 既有 SGCC 也有 TC，`04` 既有 TC 也有 BGCC/EFCC），按 params 判会把
                # `SGCC0020` 这类整屏系统图判成立绘，生成引擎加载不到的 `.PNA` 引用。
                # 立绘族只有 `TC**`/`TB**`（Steam 侧 `.PNA`），其余（BGCC/SGCC/EFCC/EVCC）
                # 都是整屏图（`.PNG`）。
                if PORTRAIT_RE.match(name):
                    x_raw = struct.unpack_from('<H', ins.operands, 1)[0]
                    char = _lead_code(name)
                    x_signed = x_raw - 0x10000 if x_raw >= 0x8000 else x_raw
                    # 场景级覆盖优先（还原场景用）；未命中再查自动覆盖表
                    # （键：绝对源行(1-based) = 切片前对白数 + 切片内已发对白数 + 1）
                    x_ov = scene_portrait_x(self.stem, char, x_signed)
                    if x_ov is None:
                        x_ov = position_override(
                            self.stem,
                            self.dialogue_row_base + self.report['dialogues'] + 1, char)
                    self.emit_portrait_block(name, ins.offset, x_raw, x_ov)
                else:
                    self.emit_image_block(name, self.take_fade())
            elif op == 0x64:
                # `64` 既不是句柄也不是位置（见模块顶部说明），不参与发射，只计数
                self.count(None, op)
            elif op == 0x02:
                self._stage_choice_raw(ins)
                self.emit_choice(i, ins)
            elif op == 0x06:
                self.raw(b'\x06@@@@')                  # u32 占位，apply_fixups 修正
                self.fixups.append((len(self.out) - 4, ins.fields['target_offset']))
                self.count('jump')
            elif op in (0x07, 0x09):
                tgt = ins.fields['name']
                tgt_u = tgt.upper()
                if self.slice_mode:
                    # 切片模式：出口由宿主保留的尾段承担，切片自身不写出口
                    self.count(None, op)
                    i += 1
                    continue
                # 原版此处的 07/09 兼有调用语义（EVRET/RestBGM/CG_WAIT 后仍有活代码，
                # 语料实证为 call-and-return）；WS2 的 07+ff 是终止语义，无等价调用。
                # 因此只有位于有效文件尾（其后仅 0a/ff）的转移才映射为出口。
                at_tail = all(x.opcode in (0x0a, 0xff) for x in instrs[i + 1:])
                mapped = self.opts.transfer_target_map.get(self.stem, {}).get(tgt_u)
                if not mapped:
                    if (tgt_u + self.opts.transfer_suffix) in self.opts.steam_scripts:
                        mapped = tgt_u + self.opts.transfer_suffix
                    elif tgt_u in self.opts.steam_scripts and op == 0x07 \
                            and tgt_u in ('MAINMENU', 'TITLE'):
                        mapped = tgt_u     # Steam 剧本同样裸名调用的系统脚本
                if not at_tail or not mapped:
                    self.count(None, op)
                    if not at_tail:
                        self.warn('mid-file transfer to %r is call-and-return in the '
                                  'original engine (live code follows); no WS2 call '
                                  'equivalent, skipped' % tgt)
                    else:
                        self.warn('tail transfer to %r has no Steam counterpart, '
                                  'skipped (engine routing; wire exit in call-chain '
                                  'step)' % tgt)
                    i += 1
                    continue
                out_tgt = mapped
                self._flush_voice_orphan()
                self.emit_exit(out_tgt, 128 if self.has_choice else 0,
                               'wsc transfer %02x -> %s' % (op, tgt))
                self.report['dropped_tail'] = sum(
                    1 for x in instrs[i + 1:] if x.opcode not in (0x0a, 0xff))
                if self.report['dropped_tail']:
                    self.warn('transfer to %r: %d trailing instructions dropped '
                              '(WS2 出口为终止语义)' % (tgt, self.report['dropped_tail']))
                break
            elif op == 0x0a:
                if ins.fields.get('mode') == 0xff:
                    self._flush_voice_orphan()
                    self.emit_bgm_play(ins.fields['name'])
                else:
                    at_tail = (not self.slice_mode) and all(
                        x.opcode in (0x0a, 0xff) for x in instrs[i + 1:])
                    if at_tail:
                        self.count(None, op)   # 文件尾停 BGM：WS2 出口自身交接口音频
                    else:
                        self.emit_bgm_stop()
            elif op == 0x00:
                self._flush_voice_orphan()
                self.emit_se(ins.fields['name'])
            elif op == 0x54:
                self.emit_mask(ins.fields['name'])
            elif op in (0x4a, 0x82):
                ms = ins.fields['b'] if op == 0x4a else ins.fields['ms']
                if op == 0x4a and self.suppress_next_wait:
                    self.suppress_next_wait = False   # 渐变等待已并入图像块 65
                    self.count(None, op)
                else:
                    self.emit_wait(ms)
            elif op == 0x22:
                self.pending_fade = ins.fields['b'] / 1000.0
                self.count(None, op)
            elif op == 0xff:
                if self.slice_mode:
                    self.count(None, op)   # 切片模式：不写出口
                    i += 1
                    continue
                b_raw = ins.fields['b']
                if b_raw == 192:
                    b_flag = 192
                elif b_raw == 128 or self.has_choice:
                    b_flag = 128
                else:
                    b_flag = 0
                target = self.opts.exit_target_map.get(self.stem) \
                    or self.opts.steam_exit_map.get(
                        self.stem.upper() + self.opts.transfer_suffix)
                if target:
                    src = ('explicit' if self.stem in self.opts.exit_target_map
                           else 'steam counterpart')
                elif self.has_choice:
                    src = 'via choice block'   # 选项脚本原生即 0e|0f|ff，无 07 出口
                else:
                    src = 'none'
                    self.warn('no exit target (原版由引擎按脚本号路由); 输出终止型 ff，'
                              '需在调用链整合步骤接线')
                self.emit_exit(target, b_flag, src)
            else:
                self.count(None, op)
                self._flush_voice_orphan()
            i += 1

        self.apply_fixups()
        return bytes(self.out)

    # -- 辅助 --------------------------------------------------------------
    def take_fade(self):
        if self.pending_fade is not None:
            f = self.pending_fade
            self.pending_fade = None
            return f
        return self.opts.default_fade

    def _flush_voice_orphan(self):
        """0x23 后跟的不是对话：原位补发语音，保持音频时序。"""
        if self.pending_voice is not None:
            self.emit_voice(self.pending_voice)
            self.count('voice_orphan')
            self.pending_voice = None

    def _stage_choice_raw(self, ins):
        """0x02 items 的原始文本字节（反汇编器 fields 只给了 str）。"""
        raw = ins.operands
        p = 2
        for it in ins.fields['items']:
            end = raw.index(0, p + 2)
            it['_raw_text'] = raw[p + 2:end]
            p = end + 1 + 11

    def apply_fixups(self):
        for off, tgt in self.fixups:
            if tgt == 'EXIT':
                resolved = self.exit_label_off
                if resolved is None:
                    self.warn('choice fallback target: script has no exit')
                    resolved = len(self.out)
            else:
                resolved = self.labels.get(tgt)
                if resolved is None:
                    self.warn('jump target 0x%x never emitted' % tgt)
                    resolved = len(self.out)
            self.out[off:off + 4] = _u32(resolved)


def convert_range(wsc_decrypted, stem, opts=None, start_offset=0, end_offset=None):
    """按 WSC 字节偏移区间转换（左闭右开）。用于把源场景的一段就地插入调用脚本。

    `start_offset` / `end_offset` 必须落在**指令边界**上（取 disassemble 出来的
    Instruction.offset）。切片后 label 仍以 WSC 绝对偏移为键，所以 `06` 的
    文件内跳转只要两端都在切片内就照常解析；落在切片外的目标会在
    `apply_fixups` 里告警并指到切片末尾。

    切片模式（`opts.slice_mode=True`）下不写出口、抑制开头的对话框打开指令、
    字符串池从 `opts.pool_start` 起算 —— 三条都是「接在宿主中间」所必需的。
    """
    opts = opts or ConvertOptions()
    all_ins = disassemble(wsc_decrypted)
    instrs = [i for i in all_ins
              if i.offset >= start_offset and (end_offset is None or i.offset < end_offset)]
    if not instrs:
        raise ValueError('切片为空：offset [%s, %s)' % (start_offset, end_offset))
    # 切片前已发的对白数：覆盖表(`position_overrides`)行号是全脚本 1-based
    row_base = sum(1 for i in all_ins if i.opcode in (0x41, 0x42) and i.offset < start_offset)
    em = _Emitter(stem, instrs, opts, dialogue_row_base=row_base)
    data = em.run()
    em.report['ws2_size'] = len(data)
    em.report['slice'] = {'start_offset': start_offset, 'end_offset': end_offset,
                          'instructions': len(instrs)}
    return data, em.report


def convert(wsc_decrypted, stem, opts=None):
    """转换单个已解密 WSC。返回 (ws2_decoded_bytes, report_dict)。"""
    return convert_range(wsc_decrypted, stem, opts)


def decrypt_wsc(raw):
    """原版 Rio.arc 内 WSC 的存储形态为每字节 ror 2。"""
    return bytes(((b >> 2) | (b << 6)) & 0xff for b in raw)
