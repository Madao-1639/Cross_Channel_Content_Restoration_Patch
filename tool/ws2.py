"""CROSS†CHANNEL .ws2 script codec.

Obfuscation is a per-byte rotate-left-6 (verified: round-trips byte-exactly,
and exposes ASCII opcode operands such as script names and CG filenames).

Relevant opcodes (operand forms confirmed against Steam story scripts):

    0x34  <slot>\0 <FILE.PNA>\0 0x01 0x01     display a CG into a layer slot
    0x33  <slot>\0 <FILE.PNG>\0 0x01 0x01     hard-load resource (background)
    0x66  ...                                  effect mask (tolerates missing)
    0x0b  <u16 var> 0x01                      push/set a variable
    0x04  <NAME>\0                            call another script by name
    0x07  <NAME>\0                            jump to another script (no return)
"""
import re
import struct


def decode(raw):
    """Deobfuscate a .ws2 member (rotate left 6)."""
    return bytes(((c << 6) | (c >> 2)) & 0xff for c in raw)


def encode(data):
    """Re-obfuscate a .ws2 member (inverse: rotate right 6 == left 2)."""
    return bytes(((c << 2) | (c >> 6)) & 0xff for c in data)


# ---------------------------------------------------------------------------
# 末尾 8 字节（尾表）：**明文**，不参与 rot6
# ---------------------------------------------------------------------------
# 引擎 `sub_4CCFE0` 加载 ws2 后取 `data[size-8]` / `data[size-4]` 两 个 u32，
# 交给 `sub_4B60A0` 分配「脚本已读位图」，容量 = (n14 + n0f + 7) / 8 字节；
# 而标记已读（`0x4B6330`）按**消息下标**写位且**不检查容量** ⇒
# 表值小于实际条数就会写越界堆（实机 `0xc0000374`）。
# 实测：Steam 原档 363/363 个脚本这 8 字节 == (该脚本 0x14 条数, 0f 条目数)。
# 详见 doc/engine-mechanics.md「MessageFlag.dat」。
TRAILER_LEN = 8


def trailer_counts(decoded):
    """解码流里的 (0x14 条数, 0f 条目数)——尾表应当等于这两个数。"""
    from tool import ws2disasm  # 局部导入避免与 ws2disasm 的模块级循环依赖
    n14 = n0f = 0
    for i in ws2disasm.disassemble(decoded):
        if i.opcode == 0x14:
            n14 += 1
        elif i.opcode == 0x0f:
            n0f += len(i.fields.get('entries') or [])
    return n14, n0f


def read_trailer(raw):
    """读末尾 8 字节（**明文**）：(0x14 条数, 0f 条目数)。"""
    if len(raw) < TRAILER_LEN:
        raise ValueError('ws2 太短（%d 字节），没有尾表' % len(raw))
    return struct.unpack('<II', raw[-TRAILER_LEN:])


def fix_trailer(raw, decoded=None, counts=None):
    """把末尾 8 字节写成**明文**的 (0x14 条数, 0f 条目数)，返回新 bytes。

    **插删对话格后必须调**：不改这两个数，引擎就会按旧消息数分配已读位图，
    标记已读时写下标越界（见本函数上方注释与 doc/engine-mechanics.md）。
    ⚠️ 是**明文**：必须在 `encode()` **之后**写；`decode()` 会把它们旋转成垃圾
    （`0f 00 00 00` → `c3 00 00 00`），所以计数用解码流、写回用原始字节。
    """
    if counts is None:
        counts = trailer_counts(decode(raw) if decoded is None else decoded)
    return raw[:-TRAILER_LEN] + struct.pack('<II', counts[0], counts[1])


def _walk(decoded):
    """[(指令结束偏移, (opcode, fields))] —— 供 seam_clear 用。"""
    from tool import ws2disasm
    return [(x.offset + x.size, (x.opcode, x.fields))
            for x in ws2disasm.disassemble(decoded)]


def seam_clear(decoded, want_slots):
    """在**末尾那句对话之后**补 `37 <stNN>`，返回新解码流（无需则原样返回）。

    用途（接缝类，2026-10-14）：原版在末句之后**清了立绘**再切背景，而本补丁的尾段按流水线口径
    取自 **Steam 原档**（Steam 删节版那里本来没立绘，所以它不需要清）⇒ 还原段把立绘留在屏上，
    立绘就挂到下一脚本才清。判据 = 「原版该处隐藏的槽位」∩「产物末句后仍在屏的槽位」。
    只插在最后一个 `14` 之后（= 尾段起点），不改尾段内部字节（不动它的 `01` 偏移重算）。
    """
    on, snap, last = set(), set(), None
    for end, (op, fields) in _walk(decoded):
        if op == 0x39:
            on.add((fields.get('name') or '').lower())
        elif op == 0x37:
            nm = (fields.get('name') or '').lower()
            on.clear() if nm == '*' else on.discard(nm)
        elif op == 0x14:
            last, snap = end, set(on)
    if last is None:
        return decoded
    hit = sorted(set(s.lower() for s in want_slots) & snap)
    if not hit:
        return decoded
    ins = b''.join(b'\x37' + s.encode('ascii') + b'\x00' for s in hit)
    return decoded[:last] + ins + decoded[last:]


# 0x34 <slot> 00 <STEM>.PNA 00 01 01
DISPLAY_PNA = re.compile(
    rb'\x34([A-Za-z0-9_]{1,12})\x00([A-Za-z0-9_]+)\.PNA\x00\x01\x01', re.I)

# 0x33 <slot> 00 <FILE>.PNG 00 01 01
DISPLAY_PNG = re.compile(
    rb'\x33([A-Za-z0-9_]{1,12})\x00([A-Za-z0-9_]+)\.PNG\x00\x01\x01', re.I)

# 0x04 <NAME> 00 (call)
CALL = re.compile(rb'\x04([A-Z0-9_]+)\x00')

# 0x07 <NAME> 00 (jump)
JUMP = re.compile(rb'\x07([A-Z0-9_]+)\x00')


def extract_pna_refs(decoded_data):
    """Extract all PNA file stems referenced in decoded script."""
    return [m.group(2).decode('ascii') for m in DISPLAY_PNA.finditer(decoded_data)]


def extract_png_refs(decoded_data):
    """Extract all PNG file names referenced in decoded script."""
    return [m.group(2).decode('ascii') for m in DISPLAY_PNG.finditer(decoded_data)]


def extract_calls(decoded_data):
    """Extract all script names called via 0x04 (call)."""
    return [m.group(1).decode('ascii') for m in CALL.finditer(decoded_data)]


def extract_jumps(decoded_data):
    """Extract all script names jumped to via 0x07 (jump)."""
    return [m.group(1).decode('ascii') for m in JUMP.finditer(decoded_data)]


def extract_all_jumps(decoded_data):
    """Extract all script jumps (both call and jump)."""
    return extract_calls(decoded_data) + extract_jumps(decoded_data)
