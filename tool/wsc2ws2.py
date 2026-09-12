"""WSC (WillPlus) -> WS2 (AdvHD) 忠实指令集转换器。

原理与完整指令对照见 doc/wsc_to_ws2_conversion.md。设计原则：

1. 忠实还原 —— 每条原版演出指令要么映射为等价 WS2 指令序列，要么明确跳过并
   计入 report（绝不静默丢弃、绝不添加原版没有的内容）。
2. 核心功能：文本(0x41/0x42)、语音(0x23)、CG/背景(0x46/0x48)、场景切换(0x07/0x09/0xff)。
   完整支持：SE(0x00)、BGM(0x0a)、遮罩(0x54)、选项(0x02)、文件内跳转(0x06)。
3. 字节模板全部取自 Steam 原生脚本语料（tmp/ws2_analysis/opcode_table.md）与
   Res303 实测可玩的 CNR 块，见各 emit_* 函数，勿随手改动。
"""
import re
import struct

from tool.wsc import disassemble

# `0x48` 的形态判据：名字属于立绘族（Steam 侧是 `.PNA`）才发立绘块，其余一律发图像块。
# 见 `_Emitter.run()` 里 0x48 分支的说明。
PORTRAIT_RE = re.compile(r'^T[CB]')

# ---------------------------------------------------------------------------
# 说话人映射（WSC 日文 -> Steam 英文 %LC 名）
# 依据：两语料说话人频次对齐 + Res303 CNR 实测（Youko/Taichi 等）。
# 低置信度条目（Matron/Lunch Lady/Hayasugi 等）在 doc/wsc_to_ws2_conversion.md
# 中单独列出，待实机校对；表外名字原样保留并产生 warning。
# ---------------------------------------------------------------------------
SPEAKER_MAP = {
    '太一': 'Taichi',
    '見里': 'Misato',
    '美希': 'Miki',
    '霧': 'Kiri',
    '冬子': 'Touko',
    '曜子': 'Youko',
    '友貴': 'Tomoki',
    '七香': 'Nanaca',
    '桜庭': 'Sakuraba',
    '遊紗': 'Yusa',
    '新川': 'Shinkawa',
    '少女': 'Girl',
    '少年': 'Boy',
    'みゆき': 'Miyuki',
    'ママン': 'Matron',
    '声': 'Voice',
    'おばちゃん': 'Lunch Lady',
    '俺': 'Taichi',
    '二人': 'Both',
    '腹の虫': 'Stomach',
    '三人': 'All 3',
    '＊＊': '???',
    '曜子先生': 'Hayasugi',
    '冬子･見里･美希･友貴': 'All 4',
    '友貴の死体': 'Dead Tomoki',
    '＊': '??',
    '部長': 'Club President',
    '霧・太一': 'Kiri/Taichi',
    '老カラデ家': 'Karade Master',
    '政宗': 'Masamune',
    '豊': 'Yutaka',
    '重蔵': 'Juuzou',
    '女': 'Woman',
}

# BGM 曲目 -> Steam 音乐鉴赏 id（原生语料规则：id = 1049 + 曲目号，BGM015->1064）
BGM_ID_BASE = 1049

# 原版语音名前缀 -> Steam 语音通道名。**不能套「char + 前缀」**：原版前缀是日文名首字母
# （`MSA`=見里、`FYU`=冬子、`YKI`=友貴、`MMN`=ママン），Steam 通道用的是英文名
# （`charMIS`/`charTOU`/`charTOM`/`charOBA`）。表来自 doc/localization.md「角色名称映射」，
# 其「Steam 通道」列由 Steam 自己的 `2e` 指令与紧随的 `%LC` 逐句配对得出（原生 10,693 条），
# 且这 12 个通道名在原生语料里都真实存在。
# 注：Steam 侧**自身**的语音名（`MIK_0686.OGG` 等）确实满足「char + 前缀」，
# 但那与这套原版前缀是两码事，不要混用。
VOICE_CHANNEL = {
    'MSA': 'charMIS', 'MKI': 'charMIK', 'KRI': 'charKIR', 'FYU': 'charTOU',
    'YKI': 'charTOM', 'YOU': 'charYOU', 'NNK': 'charNNK', 'SKU': 'charSAK',
    'YSA': 'charYUS', 'SHI': 'charSIN', 'MYK': 'charMIY', 'MMN': 'charOBA',
}

# WS2 常量字节模板（全部取自原生语料，勿改）
VOICE_TAIL = bytes(10) + b'\x0a\x00\x00\x65' + bytes(8)           # 2e 的 22B 恒定尾
# 28 的 22B 尾（模态形态，全语料 1086/1556；布局见 tool/ws2disasm._op_28）。
# 早期误写成 10B：那会让引擎少读 12 字节、把后续指令吃进参数里。
SE_TAIL = bytes(10) + b'\x0a\x00' + bytes(5) + b'\x01' + bytes(4)
LAYER_ORDER = b'\x04LAYER_ORDER\x00'
VAR_10_13 = b''.join(b'\x09\x00' + bytes([v]) + b'\x00\x00\x00\x80\x3f'
                     for v in (0x0a, 0x0b, 0x0c, 0x0d))           # 图层透明度 1.0 x4
PORTRAIT_ATTRS = b'\x02\x01\x04\x03\x00\x00\x00\x01\x00\x02\x00'  # 39 槽位属性(c=4 形)

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
        # 目前只用于蒙版（见 emit_mask）。
        self.available = kw.get('available', None)


def _f32(x):
    return struct.pack('<f', x)


def _u16(x):
    return struct.pack('<H', x)


def _u32(x):
    return struct.pack('<I', x)


class _Emitter:
    def __init__(self, stem, instrs, opts):
        self.stem = stem
        self.instrs = instrs
        self.opts = opts
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
        self.last_was_15 = True   # 脚本开头无需清框，抑制首条 maybe_clear
        self.portrait_slot = 0
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
        self.last_was_15 = False

    def maybe_clear(self):
        """对话/出口前需要 15 清框；连续两条 15 无害但保持单条更贴近原生语料。"""
        if not self.last_was_15:
            self.out += b'\x15\x00\x00'
            self.last_was_15 = True

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
        if pre != self.opts.voice_channel:
            self.warn('语音前缀 %s 不在通道映射表里，回退到 %s' % (pre, self.opts.voice_channel))
        return self.opts.voice_channel


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
        self.raw(b'\x15' + prefix + b'\x00')
        self.raw(b'\x14' + _u16(did) + b'\x00\x00char\x00' + text_bytes + b'\x00\x00')
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
        self.maybe_clear()
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

    def emit_portrait_block(self, name):
        """0x48-04 -> 立绘块（34+39+LAYER_ORDER，槽位取最近 0x64）。"""
        fname = self.rename(name) + '.PNA'
        slot = 'st%02d' % (self.portrait_slot + 1)
        self.report['images'].append(('portrait', name, fname))
        self.raw(b'\x34' + slot.encode('ascii') + b'\x00'
                 + fname.encode('ascii') + b'\x00\x01\x01'
                 + b'\x39' + slot.encode('ascii') + b'\x00' + PORTRAIT_ATTRS
                 + LAYER_ORDER)
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
        """15 00 00 | 07 <target> | ff a b —— WS2 场景出口固定搭配。"""
        self.maybe_clear()
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
        self.maybe_clear()
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
                    self.emit_portrait_block(name)
                else:
                    self.emit_image_block(name, self.take_fade())
            elif op == 0x64:
                self.portrait_slot = ins.fields['a']   # 立绘槽位(0..4) -> st01..st05
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
    instrs = [i for i in disassemble(wsc_decrypted)
              if i.offset >= start_offset and (end_offset is None or i.offset < end_offset)]
    if not instrs:
        raise ValueError('切片为空：offset [%s, %s)' % (start_offset, end_offset))
    em = _Emitter(stem, instrs, opts)
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
