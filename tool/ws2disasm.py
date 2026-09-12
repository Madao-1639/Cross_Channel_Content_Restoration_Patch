# -*- coding: utf-8 -*-
"""
ws2_disasm.py - Linear disassembler for AdvHD engine .WS2 script format
(CROSS†CHANNEL Steam; corpus files in tmp/corpus are already rot6-decoded).

Usage:
    from ws2_disasm import disassemble, Instruction, UnknownInstruction
    instrs = disassemble(open('CCA0001_en.ws2','rb').read())

Instruction fields:
    offset   int    file offset of first byte
    opcode   int    first byte
    size     int    total byte length (opcode byte included)
    operands bytes  raw operand bytes (opcode byte excluded)
    fields   dict   parsed fields (names, u16/u32/f32 values, text, ...)

All layouts below were derived by requiring a single rule per opcode to tile
every one of the 363 Steam .ws2 files + 12 Res303 CNR*.ws2 files (375 files,
100% byte coverage, see verify_corpus.py).
"""
import struct

class UnknownInstruction(Exception):
    def __init__(self, offset, opcode, msg=''):
        self.offset = offset
        self.opcode = opcode
        self.msg = msg
        super().__init__('UnknownInstruction at 0x%x: opcode 0x%02x %s' % (offset, opcode, msg))

class Instruction:
    __slots__ = ('offset', 'opcode', 'size', 'operands', 'fields')
    def __init__(self, offset, opcode, size, operands, fields):
        self.offset = offset
        self.opcode = opcode
        self.size = size
        self.operands = operands
        self.fields = fields
    def __repr__(self):
        f = ' '.join('%s=%r' % (k, v) for k, v in self.fields.items())
        return '<%04x %02x sz=%d %s>' % (self.offset, self.opcode, self.size, f)

# ---------------------------------------------------------------------------
# low level helpers
# ---------------------------------------------------------------------------

def _read_cstring(data, pos, limit=4096):
    """Return (raw bytes w/o NUL, endpos just past the NUL)."""
    end = data.find(b'\x00', pos, pos + limit)
    if end < 0:
        raise UnknownInstruction(pos, data[pos] if pos < len(data) else 0,
                                 'unterminated string')
    return data[pos:end], end + 1

def _is_name(b):
    """Name may be ASCII or Shift-JIS (CNR scripts); reject control bytes."""
    if len(b) > 200:
        return False
    return all(0x20 <= c <= 0xfc and c != 0x7f for c in b)

def _is_ascii(b):
    return all(0x20 <= c < 0x7f for c in b)

def _dec_str(b):
    try:
        return b.decode('ascii')
    except UnicodeDecodeError:
        return b.decode('cp932', 'replace')

def _f32(data, pos):
    return struct.unpack_from('<f', data, pos)[0]

def _u16(data, pos):
    return struct.unpack_from('<H', data, pos)[0]

def _u32(data, pos):
    return struct.unpack_from('<I', data, pos)[0]

def _chk(cond, op_offset, opcode, msg):
    if not cond:
        raise UnknownInstruction(op_offset, opcode, msg)

# ---------------------------------------------------------------------------
# generic handler builders
# ---------------------------------------------------------------------------

def _fixed(n, *field_specs):
    """Fixed n operand bytes (total size n+1). field_specs: (name, kind, off);
    kind: u8/u16/u32/f32 or int = raw hex-dump of that many bytes."""
    def h(data, pos, op_offset):
        if pos + n > len(data):
            raise UnknownInstruction(op_offset, data[op_offset], 'truncated')
        fields = {}
        for name, kind, off in field_specs:
            if kind == 'u8':
                fields[name] = data[pos + off]
            elif kind == 'u16':
                fields[name] = _u16(data, pos + off)
            elif kind == 'u32':
                fields[name] = _u32(data, pos + off)
            elif kind == 'f32':
                fields[name] = round(_f32(data, pos + off), 6)
            else:
                fields[name] = data[pos + off:pos + off + kind].hex()
        return n + 1, data[pos:pos + n], fields
    return h

def _name_op(opcode, extra=0, extra_field='tail', pre_bytes=0, ascii_only=True,
             extra_check=None):
    """opcode [pre_bytes] <name NUL> [extra fixed bytes]"""
    def h(data, pos, op_offset):
        p = pos + pre_bytes
        name, end = _read_cstring(data, p)
        ok = _is_ascii(name) if ascii_only else _is_name(name)
        _chk(ok, op_offset, opcode, 'name not ascii: %r' % name[:40])
        if end + extra > len(data):
            raise UnknownInstruction(op_offset, opcode, 'truncated after name')
        fields = {'name': _dec_str(name)}
        if pre_bytes:
            fields['pre'] = data[pos:p].hex()
        if extra:
            fields[extra_field] = data[end:end + extra].hex()
            if extra_check is not None:
                _chk(extra_check(data[end:end + extra]), op_offset, opcode,
                     '%s %s' % (extra_field, data[end:end + extra].hex()))
        return end + extra - op_offset, data[pos + 1:end + extra], fields
    return h

def _two_name_op(opcode, extra=0, extra_field='tail', pre_bytes=0, ascii_only=True):
    """opcode [pre] <name1 NUL> <name2 NUL> [extra bytes]"""
    def h(data, pos, op_offset):
        p = pos + pre_bytes
        n1, end1 = _read_cstring(data, p)
        ok = _is_ascii(n1) if ascii_only else _is_name(n1)
        _chk(ok, op_offset, opcode, 'name1 bad: %r' % n1[:40])
        n2, end2 = _read_cstring(data, end1)
        ok = _is_ascii(n2) if ascii_only else _is_name(n2)
        _chk(ok, op_offset, opcode, 'name2 bad: %r' % n2[:40])
        if end2 + extra > len(data):
            raise UnknownInstruction(op_offset, opcode, 'truncated')
        fields = {'name': _dec_str(n1), 'name2': _dec_str(n2)}
        if pre_bytes:
            fields['pre'] = data[pos:p].hex()
        if extra:
            fields[extra_field] = data[end2:end2 + extra].hex()
        return end2 + extra - op_offset, data[pos + 1:end2 + extra], fields
    return h

# ---------------------------------------------------------------------------
# specific opcode handlers
# ---------------------------------------------------------------------------

def _op_01(data, pos, op_offset):
    # 01 <u8 mode> ...: mode 0x00 -> 4 operand bytes; else 15 operand bytes:
    #    01 <mode> <u16 id> <f32> <u32 a> <u32 b>
    if pos >= len(data):
        raise UnknownInstruction(op_offset, 0x01, 'truncated')
    mode = data[pos]
    if mode == 0x00:
        n = 4
        if pos + n > len(data):
            raise UnknownInstruction(op_offset, 0x01, 'truncated')
        return n + 1, data[pos:pos + n], {'mode': mode, 'raw': data[pos + 1:pos + n].hex()}
    n = 15
    if pos + n > len(data):
        raise UnknownInstruction(op_offset, 0x01, 'truncated')
    vid = _u16(data, pos + 1)
    val = _f32(data, pos + 3)
    a = _u32(data, pos + 7)
    b = _u32(data, pos + 11)
    return n + 1, data[pos:pos + n], {'mode': mode, 'id': vid, 'value': round(val, 6),
                                      'f32hex': data[pos + 3:pos + 7].hex(), 'a': a, 'b': b}

def _op_05(data, pos, op_offset):
    # 05 <u8 a> <u32 b> <u32 c>  (10B; a=0xff: stop-all, a=0x00 b=0 c=1: BGM stop)
    if pos + 9 > len(data):
        raise UnknownInstruction(op_offset, 0x05, 'truncated')
    p = data[pos:pos + 9]
    return 10, p, {'a': p[0], 'b': _u32(p, 1), 'c': _u32(p, 5)}

def _op_04(data, pos, op_offset):
    # 04 <name NUL>  (subroutine call, e.g. LAYER_ORDER)
    name, end = _read_cstring(data, pos)
    _chk(_is_name(name), op_offset, 0x04, 'name bad: %r' % name[:40])
    return end - op_offset, data[pos + 1:end], {'name': _dec_str(name)}

def _op_06(data, pos, op_offset):
    # 06 <u32 target>  (5B unconditional jump; target = file offset)
    if pos + 4 > len(data):
        raise UnknownInstruction(op_offset, 0x06, 'truncated')
    target = _u32(data, pos)
    return 5, data[pos:pos + 4], {'target': target}

def _op_07(data, pos, op_offset):
    # 07 <script name NUL>  (transfer to script; often followed by separate ff END)
    name, end = _read_cstring(data, pos)
    _chk(_is_ascii(name) and len(name) > 0, op_offset, 0x07,
         'name bad: %r' % name[:40])
    return end - op_offset, data[pos + 1:end], {'name': _dec_str(name)}

def _op_09(data, pos, op_offset):
    # 09 <mode u8> <id u16> <f32>   (mode 0 = set float var; mode 1 = ?)
    if pos + 7 > len(data):
        raise UnknownInstruction(op_offset, 0x09, 'truncated')
    mode = data[pos]
    vid = _u16(data, pos + 1)
    raw = data[pos + 3:pos + 7]
    val = _f32(data, pos + 3)
    return 8, data[pos + 1:pos + 7], {'mode': mode, 'id': vid,
                                      'value': round(val, 6), 'f32hex': raw.hex()}

def _op_11(data, pos, op_offset):
    # 11 <name NUL> 00 <f32 seconds>   (start timer)
    name, end = _read_cstring(data, pos)
    _chk(_is_name(name), op_offset, 0x11, 'name bad')
    if end + 5 > len(data):
        raise UnknownInstruction(op_offset, 0x11, 'truncated timer')
    z = data[end]
    _chk(z == 0, op_offset, 0x11, 'timer sep 0x%02x' % z)
    val = _f32(data, end + 1)
    return end + 5 - op_offset, data[pos + 1:end + 5], {'name': _dec_str(name),
                                                        'seconds': round(val, 6)}

def _op_12(data, pos, op_offset):
    # 12 <name NUL> <2 tail bytes>   (wait for timer; tail 0100 or 0000)
    name, end = _read_cstring(data, pos)
    _chk(_is_name(name), op_offset, 0x12, 'name bad')
    if end + 2 > len(data):
        raise UnknownInstruction(op_offset, 0x12, 'truncated')
    tail = data[end:end + 2]
    return end + 2 - op_offset, data[pos + 1:end + 2], {'name': _dec_str(name),
                                                        'tail': tail.hex()}

def _op_14(data, pos, op_offset):
    # 14 <u16 id> <u16 0x0000> <char NUL> <text NUL> <u8 0>
    # text contains %K/%P/%N markers and \d..\d delays
    if pos + 5 > len(data):
        raise UnknownInstruction(op_offset, 0x14, 'truncated')
    did = _u16(data, pos)
    _chk(data[pos + 2:pos + 4] == b'\x00\x00', op_offset, 0x14,
         'dialogue hdr %s' % data[pos:pos + 4].hex())
    char, p = _read_cstring(data, pos + 4)
    text, p2 = _read_cstring(data, p)
    if p2 >= len(data):
        raise UnknownInstruction(op_offset, 0x14, 'missing tail')
    tail = data[p2]
    _chk(tail == 0x00, op_offset, 0x14, 'dialogue tail 0x%02x' % tail)
    size = p2 + 1 - op_offset
    return size, data[pos + 1:p2 + 1], {'id': did, 'char': _dec_str(char),
                                        'text': _dec_str(text)}

def _op_15(data, pos, op_offset):
    # 15 <prefix NUL> 00   ; clear dialog box; prefix usually '' or '%LC<speaker>'
    s, end = _read_cstring(data, pos, limit=300)
    if end >= len(data):
        raise UnknownInstruction(op_offset, 0x15, 'truncated')
    tail = data[end]
    _chk(tail == 0x00, op_offset, 0x15, 'clear tail 0x%02x' % tail)
    _chk(len(s) < 256, op_offset, 0x15, 'prefix too long')
    return end + 1 - op_offset, data[pos + 1:end + 1], {'prefix': _dec_str(s)}

def _op_0e(data, pos, op_offset):
    # 0e timing block (byte1 == 0x00): 14 raw operand bytes
    # 0e choice header (byte1 == 0x0b): operands 0b 00 <count> 00 01  (6B total)
    #   followed by either 0f <count> text-entry list, or a list of 01-instrs
    #   (mode 2, b = jump target) interleaved with 0b <u16 flag> 00 markers
    kind = data[pos]
    if kind == 0x00:
        # timing block: 14 operand bytes (menu transition: u16s 1000/13/200 seen)
        n = 14
        if pos + n > len(data):
            raise UnknownInstruction(op_offset, 0x0e, 'truncated')
        p = data[pos:pos + n]
        return n + 1, p, {'form': 'timing', 'raw': p.hex()}
    _chk(kind == 0x0b, op_offset, 0x0e, 'kind 0x%02x' % kind)
    if pos + 5 > len(data):
        raise UnknownInstruction(op_offset, 0x0e, 'truncated header')
    hdr = data[pos:pos + 5]
    _chk(hdr[1] == 0x00 and hdr[3] == 0x00 and hdr[4] == 0x01, op_offset, 0x0e,
         'hdr %s' % hdr.hex())
    return 6, hdr, {'form': 'choice-hdr', 'count': hdr[2]}

def _parse_choice_entries(data, op_offset, opcode, count, p):
    """count x (<u16 strid> <text NUL> 00 <u16 label> <jump>); jump = 07 name | 06 u32"""
    entries = []
    for idx in range(count):
        if p + 2 > len(data):
            raise UnknownInstruction(op_offset, opcode, 'truncated entry %d' % idx)
        strid = _u16(data, p)
        text, e1 = _read_cstring(data, p + 2)
        # 原生语料全 ASCII；WSC 转换产物合法携带 CP932 选项文本，仅要求非空
        _chk(len(text) > 0, op_offset, opcode, 'entry text %r' % text[:40])
        _chk(e1 < len(data) and data[e1] == 0x00, op_offset, opcode, 'entry sep')
        _chk(e1 + 3 <= len(data), op_offset, opcode, 'truncated label')
        label = _u16(data, e1 + 1)
        p = e1 + 3
        op = data[p]
        if op == 0x07:
            name, e2 = _read_cstring(data, p + 1)
            _chk(_is_ascii(name), op_offset, opcode, 'entry script %r' % name[:40])
            entries.append({'strid': strid, 'text': _dec_str(text),
                            'label': label, 'jump_op': 7, 'name': _dec_str(name)})
            p = e2
        elif op == 0x06:
            _chk(p + 5 <= len(data), op_offset, opcode, 'truncated entry jump')
            entries.append({'strid': strid, 'text': _dec_str(text),
                            'label': label, 'jump_op': 6, 'target': _u32(data, p + 1)})
            p += 5
        else:
            raise UnknownInstruction(op_offset, opcode, 'entry jump op 0x%02x' % op)
    return entries, p

def _op_1e(data, pos, op_offset):
    # 1e <slot NUL> <file NUL> <17-byte tail>   (BGM play)
    # 尾段是**单条指令的一部分**，不是"10 字节尾 + 一条 0a 伪指令"——
    # [10] 的 0x0a 是参数。布局（Ws2Explorer v3 'ffhhbf'）：
    #   f32 vol @0 | f32 0 @4 | u16 0xffff @8 | u16 0x000a @10 | u8 1 @12 | f32 0 @13
    # 全语料 877 条：仅 @0 的音量有变化（0.0 / 1.0 / 2.0），其余恒定。
    slot, end1 = _read_cstring(data, pos)
    _chk(_is_name(slot), op_offset, 0x1e, 'bgm slot bad')
    fn, end2 = _read_cstring(data, end1)
    _chk(_is_name(fn), op_offset, 0x1e, 'bgm file bad')
    TAIL = 17
    if end2 + TAIL > len(data):
        raise UnknownInstruction(op_offset, 0x1e, 'truncated bgm tail')
    tail = data[end2:end2 + TAIL]
    _chk(tail[4:10] == b'\x00\x00\x00\x00\xff\xff' and tail[10:13] == b'\x0a\x00\x01'
         and tail[13:17] == bytes(4), op_offset, 0x1e, 'bgm tail %s' % tail.hex())
    size = end2 + TAIL - op_offset
    return size, data[pos + 1:end2 + TAIL], {'slot': _dec_str(slot), 'file': _dec_str(fn),
                                             'vol': round(_f32(tail, 0), 6),
                                             'f32hex': tail[0:4].hex()}

def _op_1f(data, pos, op_offset):
    # 1f <slot NUL> <f32>   (BGM stop with fade seconds)
    name, end = _read_cstring(data, pos)
    _chk(_is_name(name), op_offset, 0x1f, 'name bad')
    if end + 4 > len(data):
        raise UnknownInstruction(op_offset, 0x1f, 'truncated')
    val = _f32(data, end)
    return end + 4 - op_offset, data[pos + 1:end + 4], {'name': _dec_str(name),
                                                        'value': round(val, 6)}

def _op_28(data, pos, op_offset):
    # 28 <slot NUL> <file NUL> <22-byte tail>   (SE play；槽名为 char* 时即语音)
    # 布局（Ws2Explorer v3 'ssffhhbhhbf'）：
    #   f32 vol @0 | f32 @4 | u16 mode @8(0|ffff) | u16 code @10(3..10) | u8 @12
    #   u16 @13(0|101) | u16 @15 | u8 @17(0|1) | f32 @18
    # 全语料 1556 条的不变量：[11]==[12]==0、[14:17]==0、[18:22]==0，
    # 且 ([13],[17]) 只取 (0,1)（SE，1248 条）或 (101,0)（char* 槽语音，308 条）。
    # 其余位置（音量、mode、code）随实例变化，不做断言。
    slot, end1 = _read_cstring(data, pos)
    _chk(_is_name(slot), op_offset, 0x28, 'se slot bad')
    fn, end2 = _read_cstring(data, end1)
    _chk(_is_name(fn), op_offset, 0x28, 'se file bad')
    TAIL = 22
    if end2 + TAIL > len(data):
        raise UnknownInstruction(op_offset, 0x28, 'truncated se tail')
    tail = data[end2:end2 + TAIL]
    _chk(tail[11] == 0 and tail[12] == 0 and tail[14:17] == bytes(3)
         and tail[18:22] == bytes(4), op_offset, 0x28, 'se tail %s' % tail.hex())
    _chk((tail[13], tail[17]) in ((0, 1), (101, 0)), op_offset, 0x28,
         'se tail variant %s' % tail.hex())
    size = end2 + TAIL - op_offset
    return size, data[pos + 1:end2 + TAIL], {'slot': _dec_str(slot), 'file': _dec_str(fn),
                                             'vol': round(_f32(tail, 0), 6),
                                             'mode': _u16(tail, 8), 'code': _u16(tail, 10),
                                             'chan_flag': tail[13], 'tail': tail.hex()}

def _op_29(data, pos, op_offset):
    # 29 <slot NUL> <f32>   (fade-stop sound in slot; slot may be '*')
    name, end = _read_cstring(data, pos)
    _chk(_is_name(name), op_offset, 0x29, 'name bad')
    if end + 4 > len(data):
        raise UnknownInstruction(op_offset, 0x29, 'truncated')
    val = _f32(data, end)
    return end + 4 - op_offset, data[pos + 1:end + 4], {'name': _dec_str(name),
                                                        'value': round(val, 6)}

def _op_2e(data, pos, op_offset):
    # 2e 28 <channel NUL> <file NUL> <CONSTANT 22-byte tail>   (voice play)
    _chk(data[pos] == 0x28, op_offset, 0x2e, 'voice sub 0x%02x' % data[pos])
    chan, end1 = _read_cstring(data, pos + 1)
    _chk(_is_ascii(chan) and len(chan) > 0, op_offset, 0x2e, 'voice chan bad')
    fn, end2 = _read_cstring(data, end1)
    _chk(_is_ascii(fn) and len(fn) > 0, op_offset, 0x2e, 'voice file bad')
    TAIL = 22
    if end2 + TAIL > len(data):
        raise UnknownInstruction(op_offset, 0x2e, 'truncated voice tail')
    tail = data[end2:end2 + TAIL]
    _chk(tail == bytes.fromhex('00000000000000000000' '0a000065' '0000000000000000'),
         op_offset, 0x2e, 'voice tail %s' % tail.hex())
    size = end2 + TAIL - op_offset
    return size, data[pos + 1:end2 + TAIL], {'chan': _dec_str(chan), 'file': _dec_str(fn)}

def _op_33(data, pos, op_offset):
    # 33 <slot NUL> <file NUL> <2 bytes flags>   (load image into layer slot)
    slot, end1 = _read_cstring(data, pos)
    _chk(_is_name(slot) and len(slot) > 0, op_offset, 0x33, 'slot bad')
    fn, end2 = _read_cstring(data, end1)
    _chk(_is_name(fn) and len(fn) > 0, op_offset, 0x33, 'file bad')
    if end2 + 2 > len(data):
        raise UnknownInstruction(op_offset, 0x33, 'truncated')
    flags = data[end2:end2 + 2]
    size = end2 + 2 - op_offset
    return size, data[pos + 1:end2 + 2], {'slot': _dec_str(slot), 'file': _dec_str(fn),
                                          'flags': flags.hex()}

def _op_34(data, pos, op_offset):
    # 34 <u8 tag> <slot NUL> <file NUL> <2 bytes flags>   (PNA character sprite)
    a = data[pos]
    slot, end1 = _read_cstring(data, pos + 1)
    _chk(_is_name(slot) and len(slot) > 0, op_offset, 0x34, 'slot bad')
    fn, end2 = _read_cstring(data, end1)
    _chk(_is_name(fn) and len(fn) > 0, op_offset, 0x34, 'file bad')
    if end2 + 2 > len(data):
        raise UnknownInstruction(op_offset, 0x34, 'truncated')
    flags = data[end2:end2 + 2]
    size = end2 + 2 - op_offset
    return size, data[pos + 1:end2 + 2], {'tag': a, 'slot': _dec_str(slot),
                                          'file': _dec_str(fn), 'flags': flags.hex()}

def _op_35(data, pos, op_offset):
    # 35 <name NUL> <file NUL> 01 01 01   (movie play)
    n1, end1 = _read_cstring(data, pos)
    _chk(_is_name(n1), op_offset, 0x35, 'name bad')
    n2, end2 = _read_cstring(data, end1)
    _chk(_is_name(n2), op_offset, 0x35, 'file bad')
    N = 3
    if end2 + N > len(data):
        raise UnknownInstruction(op_offset, 0x35, 'truncated')
    tail = data[end2:end2 + N]
    _chk(tail == b'\x01\x01\x01', op_offset, 0x35, 'tail %s' % tail.hex())
    return end2 + N - op_offset, data[pos + 1:end2 + N], {'name': _dec_str(n1),
                                                          'file': _dec_str(n2)}

def _op_39(data, pos, op_offset):
    # 39 <name NUL> 02 01 <c> <d> 00 [when c==4: 00 00 01 00 02 00]
    name, end = _read_cstring(data, pos)
    _chk(_is_name(name), op_offset, 0x39, 'name bad')
    if end + 5 > len(data):
        raise UnknownInstruction(op_offset, 0x39, 'truncated')
    head = data[end:end + 5]
    _chk(head[0] == 0x02 and head[1] == 0x01 and head[4] == 0x00, op_offset,
         0x39, 'params %s' % head.hex())
    c = head[2]
    N = 11 if c == 0x04 else 5
    if end + N > len(data):
        raise UnknownInstruction(op_offset, 0x39, 'truncated')
    p = data[end:end + N]
    if c == 0x04:
        _chk(p[5:11] == bytes.fromhex('000001000200'), op_offset, 0x39,
             'ext params %s' % p[5:11].hex())
    return end + N - op_offset, data[pos + 1:end + N], {'name': _dec_str(name),
                                                        'params': p.hex()}

def _op_3f(data, pos, op_offset):
    # 3f <u8 count> <count x name NUL>   (layer order list, used by LAYER_ORDER.ws2)
    if pos >= len(data):
        raise UnknownInstruction(op_offset, 0x3f, 'truncated')
    count = data[pos]
    p = pos + 1
    names = []
    for i in range(count):
        n, e = _read_cstring(data, p)
        _chk(_is_ascii(n) and len(n) > 0, op_offset, 0x3f, 'name %d bad' % i)
        names.append(_dec_str(n))
        p = e
    return p - op_offset, data[pos + 1:p], {'count': count, 'names': names}

def _op_45(data, pos, op_offset):
    # 45 <name NUL> <u16 0> <f32 v1> <f32 v2> <f32 v3> <f32 v4>   (18 params)
    name, end = _read_cstring(data, pos)
    _chk(_is_name(name), op_offset, 0x45, 'name bad: %r' % name[:40])
    N = 18
    if end + N > len(data):
        raise UnknownInstruction(op_offset, 0x45, 'truncated')
    p = data[end:end + N]
    return end + N - op_offset, data[pos + 1:end + N], {
        'name': _dec_str(name),
        'v1': round(_f32(p, 2), 4), 'v2': round(_f32(p, 6), 4),
        'v3': round(_f32(p, 10), 4), 'v4': round(_f32(p, 14), 4), 'raw': p.hex()}

def _op_46(data, pos, op_offset):
    # 46 <name NUL> <19 raw bytes>   (layer effect params)
    name, end = _read_cstring(data, pos)
    _chk(_is_name(name), op_offset, 0x46, 'name bad: %r' % name[:40])
    N = 19
    if end + N > len(data):
        raise UnknownInstruction(op_offset, 0x46, 'truncated')
    p = data[end:end + N]
    return end + N - op_offset, data[pos + 1:end + N], {
        'name': _dec_str(name), 'raw': p.hex()}

def _op_0f(data, pos, op_offset):
    # 0f <u8 count> count x (<u16 strid> <text NUL> 00 <u16 label> <jump>)
    if pos >= len(data):
        raise UnknownInstruction(op_offset, 0x0f, 'truncated')
    count = data[pos]
    entries, p = _parse_choice_entries(data, op_offset, 0x0f, count, pos + 1)
    return p - op_offset, data[pos + 1:p], {'count': count, 'entries': entries}

def _op_20(data, pos, op_offset):
    # 20 <slot NUL> <f32> <u16>   (BGM volume/loop config)
    name, end = _read_cstring(data, pos)
    _chk(_is_name(name), op_offset, 0x20, 'name bad')
    if end + 6 > len(data):
        raise UnknownInstruction(op_offset, 0x20, 'truncated')
    val = _f32(data, end)
    w = _u16(data, end + 4)
    return end + 6 - op_offset, data[pos + 1:end + 6], {'name': _dec_str(name),
                                                        'value': round(val, 6), 'w': w}

def _op_8f(data, pos, op_offset):
    # 8f <voice channel NUL> <file NUL> <u8> <text NUL>  (voice + subtitle line)
    ch, end1 = _read_cstring(data, pos)
    _chk(_is_ascii(ch) and len(ch) > 0, op_offset, 0x8f, 'chan bad')
    fn, end2 = _read_cstring(data, end1)
    _chk(_is_ascii(fn) and len(fn) > 0, op_offset, 0x8f, 'file bad')
    if end2 + 1 > len(data):
        raise UnknownInstruction(op_offset, 0x8f, 'truncated')
    a = data[end2]
    text, end3 = _read_cstring(data, end2 + 1)
    size = end3 - op_offset
    return size, data[pos + 1:end3], {'chan': _dec_str(ch), 'file': _dec_str(fn),
                                      'a': a, 'text': _dec_str(text)}

def _op_44(data, pos, op_offset):
    # 44 <name1 NUL> <name2 NUL> <u8>
    n1, end1 = _read_cstring(data, pos)
    _chk(_is_ascii(n1), op_offset, 0x44, 'name1 bad')
    n2, end2 = _read_cstring(data, end1)
    _chk(_is_ascii(n2), op_offset, 0x44, 'name2 bad')
    if end2 + 1 > len(data):
        raise UnknownInstruction(op_offset, 0x44, 'truncated')
    a = data[end2]
    return end2 + 1 - op_offset, data[pos + 1:end2 + 1], {'name': _dec_str(n1),
                                                          'name2': _dec_str(n2), 'a': a}

def _op_58(data, pos, op_offset):
    # 58 <slot NUL> <effect NUL> <7 raw bytes>  (effect stop dispatch)
    n1, end1 = _read_cstring(data, pos)
    _chk(_is_ascii(n1), op_offset, 0x58, 'name1 bad')
    n2, end2 = _read_cstring(data, end1)
    _chk(_is_ascii(n2), op_offset, 0x58, 'name2 bad')
    if end2 + 7 > len(data):
        raise UnknownInstruction(op_offset, 0x58, 'truncated')
    tail = data[end2:end2 + 7]
    return end2 + 7 - op_offset, data[pos + 1:end2 + 7], {'name': _dec_str(n1),
                                                          'name2': _dec_str(n2),
                                                          'tail': tail.hex()}

def _op_66(data, pos, op_offset):
    # 66 <name NUL> 65 <u8 tag> 00 00 <f32> <u32 0> [<u16 2>]   (effect mask)
    name, end = _read_cstring(data, pos)
    _chk(_is_name(name), op_offset, 0x66, 'name bad: %r' % name[:40])
    N = 12
    if end + N > len(data):
        raise UnknownInstruction(op_offset, 0x66, 'truncated')
    p = data[end:end + N]
    _chk(p[0] == 0x65 and p[2:4] == b'\x00\x00' and p[8:12] == b'\x00' * 4,
         op_offset, 0x66, 'params %s' % p.hex())
    extra = 0
    if end + N + 2 <= len(data) and data[end + N:end + N + 2] == b'\x02\x00':
        extra = 2
    return end + N + extra - op_offset, data[pos + 1:end + N + extra], {
        'name': _dec_str(name), 'tag': p[1], 'value': round(_f32(p, 4), 4),
        'mode2': bool(extra)}

def _op_47(data, pos, op_offset):
    # 47 <name1 NUL> <name2 NUL> <30 raw bytes>
    #    [0..3] flags, f32 v1@4, f32 v2@8, 8x0@12, f32 v3@20, u32 0@24, u16 w@28
    n1, end1 = _read_cstring(data, pos)
    _chk(_is_ascii(n1), op_offset, 0x47, 'name1 bad: %r' % n1[:40])
    n2, end2 = _read_cstring(data, end1)
    _chk(_is_ascii(n2), op_offset, 0x47, 'name2 bad: %r' % n2[:40])
    N = 30
    if end2 + N > len(data):
        raise UnknownInstruction(op_offset, 0x47, 'truncated')
    p = data[end2:end2 + N]
    _chk(p[24:28] == b'\x00' * 4, op_offset, 0x47, 'tail %s' % p[24:30].hex())
    return end2 + N - op_offset, data[pos + 1:end2 + N], {
        'name': _dec_str(n1), 'name2': _dec_str(n2), 'head': p[0:4].hex(),
        'v1': round(_f32(p, 4), 4), 'v2': round(_f32(p, 8), 4),
        'v3': round(_f32(p, 20), 6), 'w': _u16(p, 28), 'raw': p.hex()}

def _op_48(data, pos, op_offset):
    # 48 <name1 NUL> <name2 NUL> <u32 0> <u8 a> [name3 NUL when a==2]
    n1, end1 = _read_cstring(data, pos)
    _chk(_is_ascii(n1), op_offset, 0x48, 'name1 bad')
    n2, end2 = _read_cstring(data, end1)
    _chk(_is_ascii(n2), op_offset, 0x48, 'name2 bad')
    if end2 + 5 > len(data):
        raise UnknownInstruction(op_offset, 0x48, 'truncated')
    a = data[end2 + 4]
    fields = {'name': _dec_str(n1), 'name2': _dec_str(n2),
              'u32': _u32(data, end2), 'a': a}
    end3 = end2 + 5
    if a == 0x02:
        n3, end3 = _read_cstring(data, end2 + 5)
        _chk(_is_ascii(n3), op_offset, 0x48, 'name3 bad')
        fields['name3'] = _dec_str(n3)
    return end3 - op_offset, data[pos + 1:end3], fields

def _op_51(data, pos, op_offset):
    # 51 40 <param slot NUL> <key slot NUL> <u8 idx> <u8 flag> <f32> <u8 0>
    _chk(data[pos] == 0x40, op_offset, 0x51, 'sub 0x%02x' % data[pos])
    n1, end1 = _read_cstring(data, pos + 1)
    _chk(_is_name(n1), op_offset, 0x51, 'name bad')
    n2, end2 = _read_cstring(data, end1)
    _chk(_is_name(n2), op_offset, 0x51, 'name2 bad')
    if end2 + 7 > len(data):
        raise UnknownInstruction(op_offset, 0x51, 'truncated keyframe')
    idx = data[end2]
    flg = data[end2 + 1]
    val = _f32(data, end2 + 2)
    z = data[end2 + 6]
    _chk(z == 0, op_offset, 0x51, 'kf trailer 0x%02x' % z)
    size = end2 + 7 - op_offset
    return size, data[pos + 1:end2 + 7], {'param': _dec_str(n1), 'key': _dec_str(n2),
                                          'idx': idx, 'flag': flg,
                                          'value': round(val, 6)}

def _op_52(data, pos, op_offset):
    # 52 <key slot NUL> 40 <param slot NUL> <f32> <u32 0> <u8 0>
    n1, end1 = _read_cstring(data, pos)
    _chk(_is_name(n1), op_offset, 0x52, 'name bad')
    _chk(end1 < len(data) and data[end1] == 0x40, op_offset, 0x52,
         'sub 0x%02x' % (data[end1] if end1 < len(data) else -1))
    n2, end2 = _read_cstring(data, end1 + 1)
    _chk(_is_name(n2), op_offset, 0x52, 'name2 bad')
    if end2 + 6 > len(data):
        raise UnknownInstruction(op_offset, 0x52, 'truncated')
    val = _f32(data, end2)
    z = data[end2 + 4:end2 + 9]
    _chk(z == b'\x00' * 5, op_offset, 0x52, 'tail %s' % z.hex())
    size = end2 + 6 - op_offset
    return size, data[pos + 1:end2 + 6], {'key': _dec_str(n1), 'param': _dec_str(n2),
                                          'value': round(val, 6)}

def _op_53(data, pos, op_offset):
    # 53 40 <name NUL>                 (anim: bind key, from *_ANIME_ERASE.ws2)
    # 53 <name NUL> <2 bytes>          (anim: trigger effect key, e.g. ST_QUAKE)
    if data[pos] == 0x40:
        return _name_op(0x53, pre_bytes=1, ascii_only=True)(data, pos, op_offset)
    name, end = _read_cstring(data, pos)
    _chk(_is_ascii(name), op_offset, 0x53, 'name bad: %r' % name[:40])
    if end + 2 > len(data):
        raise UnknownInstruction(op_offset, 0x53, 'truncated')
    tail = data[end:end + 2]
    return end + 2 - op_offset, data[pos + 1:end + 2], {'name': _dec_str(name),
                                                        'tail': tail.hex()}

def _op_65(data, pos, op_offset):
    # 65 <u8 a> <u8 0> <u8 0> <f32> <u32 0> <u16 mode>   (fade; a=0 or 0x64,
    #                                                     mode 0/1/2)
    if pos + 13 > len(data):
        raise UnknownInstruction(op_offset, 0x65, 'truncated')
    p = data[pos:pos + 13]
    _chk(p[1:3] == b'\x00\x00' and p[7:11] == b'\x00' * 4, op_offset,
         0x65, 'params %s' % p.hex())
    return 14, p, {'a': p[0], 'value': round(_f32(p, 3), 6), 'f32hex': p[3:7].hex(),
                   'mode': _u16(p, 11)}

def _op_67(data, pos, op_offset):
    # 67 <25 raw bytes>  (a,b flags + f32 v1@4 v2@8 v3@12)
    if pos + 25 > len(data):
        raise UnknownInstruction(op_offset, 0x67, 'truncated')
    p = data[pos:pos + 25]
    return 26, p, {'a': p[0], 'b': p[1], 'v1': round(_f32(p, 4), 4),
                   'v2': round(_f32(p, 8), 4), 'v3': round(_f32(p, 12), 4), 'raw': p.hex()}

def _op_ff(data, pos, op_offset):
    # ff <u32 a> <u32 b>   (END of script; a: 0=menu/title, 4/0xc4=title return,
    #                       8=scene-jump load; b: 128 when script has choices)
    if pos + 8 > len(data):
        raise UnknownInstruction(op_offset, 0xff, 'truncated')
    a = _u32(data, pos)
    b = _u32(data, pos + 4)
    return 9, data[pos:pos + 8], {'a': a, 'b': b}

def _op_c9(data, pos, op_offset):
    # c9 <layer NUL> <voice-char NUL> <6 bytes>  (voice/layer mapping)
    return _two_name_op(0xc9, extra=6)(data, pos, op_offset)

# opcode -> handler
HANDLERS = {
    0x00: lambda d, p, o: (1, b'', {}),                      # nop / label marker
    0x01: _op_01,
    0x02: _fixed(4, ('a', 'u32', 0)),                        # set next-script index?
    0x03: lambda d, p, o: _op_05(d, p, o),                   # 03 <u8> <u32> <u32> (SE stop)
    0x04: _op_04,
    0x05: _op_05,
    0x06: _op_06,
    0x07: _op_07,
    0x08: _fixed(1, ('a', 'u8', 0)),                         # checkpoint? (mainmenu)
    0x09: _op_09,
    0x0a: _fixed(6, ('a', 'u8', 0), ('b', 'u32', 1), ('c', 'u8', 5)),
    0x0b: _fixed(3, ('id', 'u16', 0), ('a', 'u8', 2)),
    0x0e: _op_0e,
    0x0f: _op_0f,
    0x11: _op_11,
    0x12: _op_12,
    0x13: _fixed(9, ('a', 'u8', 0)),                         # 13 ff 00*8 seen
    0x14: _op_14,
    0x15: _op_15,
    0x16: _fixed(2, ('a', 'u8', 0), ('b', 'u8', 1)),
    0x1a: _name_op(0x1a, ascii_only=True),                   # Lua call, no args
    0x1b: _fixed(1, ('a', 'u8', 0)),
    0x1c: lambda d, p, o: (_name_op(0x1c, extra=4, ascii_only=True) if p < len(d) and d[p] == 0
                           else _two_name_op(0x1c, extra=3, ascii_only=True))(d, p, o),
    0x1d: _fixed(2, ('a', 'u16', 0)),
    0x1e: _op_1e,
    0x1f: _op_1f,
    0x20: _op_20,                                            # BGM volume/loop config
    0x28: _op_28,
    0x29: _op_29,
    0x2e: _op_2e,
    0x32: _fixed(5, ('raw', 5, 0)),                          # menu click sfx? const 0068011771
    0x33: _op_33,
    0x34: _op_34,
    0x35: _op_35,
    0x37: _name_op(0x37, ascii_only=True),                   # anim stop on slot ('*'=all)
    0x38: lambda d, p, o: (_name_op(0x38, extra=1, pre_bytes=1, ascii_only=True) if d[p] == 0x40
                           else (_ for _ in ()).throw(UnknownInstruction(o, 0x38, 'sub 0x%02x' % d[p])))(d, p, o),
    0x39: _op_39,
    0x3a: lambda d, p, o: _name_op(0x3a, extra=2, ascii_only=True)(d, p, o),  # movie stop
    0x3d: _fixed(2, ('a', 'u16', 0)),
    0x3e: lambda d, p, o: (1, b'', {}),
    0x3f: _op_3f,
    0x40: _name_op(0x40, ascii_only=True),
    0x44: _op_44,
    0x45: _op_45,
    0x46: _op_46,
    0x47: _op_47,
    0x48: _op_48,
    0x51: _op_51,
    0x52: _op_52,
    0x53: _op_53,
    0x55: _two_name_op(0x55, extra=2, ascii_only=True),      # remove sprite (TOREMOVE)
    0x57: lambda d, p, o: _name_op(0x57, extra=2, ascii_only=True)(d, p, o),
    0x58: _op_58,
    0x64: _fixed(1, ('a', 'u8', 0)),
    0x65: _op_65,
    0x66: _op_66,
    0x67: _op_67,
    0x68: _fixed(1, ('a', 'u8', 0)),                         # sound-related prelude
    0x6e: lambda d, p, o: (_two_name_op(0x6e, pre_bytes=1, ascii_only=True) if d[p] == 0x40
                           else (_ for _ in ()).throw(UnknownInstruction(o, 0x6e, 'sub 0x%02x' % d[p])))(d, p, o),
    0x6f: lambda d, p, o: (_name_op(0x6f, pre_bytes=1, ascii_only=True) if d[p] == 0x40
                           else (_ for _ in ()).throw(UnknownInstruction(o, 0x6f, 'sub 0x%02x' % d[p])))(d, p, o),
    0x8f: _op_8f,
    0xc9: _op_c9,
    0xf0: _fixed(1, ('a', 'u8', 0)),
    0xfb: _fixed(1, ('a', 'u8', 0)),
    0xfd: lambda d, p, o: (1, b'', {}),
    0xff: _op_ff,
}

def disassemble(data):
    """Linearly disassemble a decoded WS2 script. Returns list[Instruction].
    Raises UnknownInstruction on the first unparsable position."""
    out = []
    pos = 0
    n = len(data)
    while pos < n:
        op = data[pos]
        h = HANDLERS.get(op)
        if h is None:
            raise UnknownInstruction(pos, op)
        size, operands, fields = h(data, pos + 1, pos)
        if size <= 0:
            raise UnknownInstruction(pos, op, 'bad size')
        out.append(Instruction(pos, op, size, operands, fields))
        pos += size
    return out
