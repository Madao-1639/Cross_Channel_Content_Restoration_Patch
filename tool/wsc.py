# -*- coding: utf-8 -*-
"""
wsc_disasm.py - Linear disassembler for WillPlus engine .WSC script format
(CROSS^CHANNEL, decrypted form: every byte ror 2 from the stored file).

Usage:
    from wsc_disasm import disassemble, Instruction, UnknownInstruction
    instrs = disassemble(open('CCC0000.WSC','rb').read())

Instruction fields:
    offset   int   file offset of first byte
    opcode   int   first byte
    size     int   total byte length
    operands bytes  raw operand bytes (without opcode)
    fields   dict  parsed fields (u16/u32 values, names, text, jump target...)
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

def _read_cstring(data, pos, limit=None):
    """Return (bytes, endpos_including_nul). Raises if no NUL found."""
    end = data.find(b'\x00', pos, limit if limit else len(data))
    if end < 0:
        raise UnknownInstruction(pos - 1, data[pos - 1] if pos else 0, 'unterminated string')
    return data[pos:end], end + 1

def _is_ascii_name(b):
    return all(0x20 <= c < 0x7f for c in b)

def _is_printable_name(b):
    return all(0x20 <= c for c in b)

# ---------------------------------------------------------------------------
# opcode handlers.  each returns (size, operands, fields); raises on error.
# data/pos: pos points just after the opcode byte.
# ---------------------------------------------------------------------------

def _op_00(data, pos, op_offset):
    # play SE sound: [u8 00][u8 00] filename<NUL> (filename e.g. "SE001.ogg")
    if pos + 2 > len(data) or data[pos] != 0x00 or data[pos + 1] != 0x00:
        raise UnknownInstruction(op_offset, 0, 'not SE form')
    name, end = _read_cstring(data, pos + 2)
    if not _is_ascii_name(name):
        raise UnknownInstruction(op_offset, 0, 'SE name not ascii')
    return end - op_offset, data[pos:end], {'name': name.decode('ascii')}

def _op_01(data, pos, op_offset):
    # var assignment/compare primitive: [u8 type][u16 addr][u8 c][u8 d][u32 val]
    if pos + 10 > len(data):
        raise UnknownInstruction(op_offset, 1, 'truncated')
    typ = data[pos]
    addr = struct.unpack_from('<H', data, pos + 1)[0]
    c = data[pos + 3]
    d = data[pos + 4]
    val = struct.unpack_from('<I', data, pos + 5)[0]
    return 11, data[pos:pos + 10], {
        'type': typ, 'addr': addr, 'c': c, 'd': d, 'value': val}

def _op_02(data, pos, op_offset):
    # option list: [u8 count][u8 00] then count x:
    #   [u16 string_id] text\0 [11-byte selector 01 (0x51+i) 03 03 01 02 00 00 (i+1) 00 00]
    count = data[pos]
    if data[pos + 1] != 0:
        raise UnknownInstruction(op_offset, 2, 'opt sep!=0')
    p = pos + 2
    items = []
    for i in range(count):
        sid = struct.unpack_from('<H', data, p)[0]
        text, p = _read_cstring(data, p + 2)
        # 11-byte descriptor
        if p + 11 > len(data) or data[p] != 0x01:
            raise UnknownInstruction(op_offset, 2, 'opt desc')
        desc = data[p:p + 11]
        p += 11
        items.append({'id': sid, 'text': text.decode('cp932', 'replace'),
                      'desc_type': desc[1], 'index': desc[8]})
    return p - op_offset, data[pos:p], {'count': count, 'items': items}

def _op_03(data, pos, op_offset):
    # [u8 a][u16 b][u8 c][u8 d][u8 e][u8 f]  (a: 0/1/2 seen; likely var compare/assign)
    if pos + 7 > len(data):
        raise UnknownInstruction(op_offset, 3, 'truncated')
    a = data[pos]
    b = struct.unpack_from('<H', data, pos + 1)[0]
    c, d, e, f = data[pos + 3], data[pos + 4], data[pos + 5], data[pos + 6]
    return 8, data[pos:pos + 7], {'a': a, 'b': b, 'c': c, 'd': d, 'e': e, 'f': f}

def _op_06(data, pos, op_offset):
    # unconditional jump: [u32 absolute target][u8 00 trailer]
    if pos + 5 > len(data):
        raise UnknownInstruction(op_offset, 6, 'truncated')
    target = struct.unpack_from('<I', data, pos)[0]
    trailer = data[pos + 4]
    if trailer != 0x00:
        raise UnknownInstruction(op_offset, 6, 'jump trailer 0x%02x' % trailer)
    return 6, data[pos:pos + 5], {'target_offset': target}

def _op_name(data, pos, op_offset, opcode):
    name, end = _read_cstring(data, pos)
    if not _is_ascii_name(name):
        raise UnknownInstruction(op_offset, opcode, 'name not ascii')
    return end - op_offset, data[pos:end], {'name': name.decode('ascii')}

def _op_08(data, pos, op_offset):
    if pos + 4 > len(data):
        raise UnknownInstruction(op_offset, 8, 'truncated')
    v = struct.unpack_from('<I', data, pos)[0]
    return 5, data[pos:pos + 4], {'value': v}

def _op_0a(data, pos, op_offset):
    # BGM: mode 0xff -> [u8 loop][u32 fade] name\0 ; mode 0x00 -> stop (nothing)
    mode = data[pos]
    if mode == 0xff:
        loop = data[pos + 1]
        fade = struct.unpack_from('<I', data, pos + 2)[0]
        name, end = _read_cstring(data, pos + 6)
        if not _is_ascii_name(name):
            raise UnknownInstruction(op_offset, 0x0a, 'bgm name not ascii')
        return end - op_offset, data[pos:end], {
            'mode': mode, 'loop': loop, 'fade': fade, 'name': name.decode('ascii')}
    if mode == 0x00:
        return 2, data[pos:pos + 1], {'mode': 0}
    raise UnknownInstruction(op_offset, 0x0a, 'bgm mode 0x%02x' % mode)

def _op_0b(data, pos, op_offset):
    if pos + 5 > len(data):
        raise UnknownInstruction(op_offset, 0x0b, 'truncated')
    vals = tuple(data[pos:pos + 5])
    return 6, data[pos:pos + 5], {'v': vals}

def _op_21(data, pos, op_offset):
    if pos + 3 > len(data):
        raise UnknownInstruction(op_offset, 0x21, 'truncated')
    a, b = struct.unpack_from('<HB', data, pos)
    return 4, data[pos:pos + 3], {'a': a, 'b': b}

def _op_22(data, pos, op_offset):
    if pos + 4 > len(data):
        raise UnknownInstruction(op_offset, 0x22, 'truncated')
    a = data[pos]
    b = struct.unpack_from('<H', data, pos + 1)[0]
    c = data[pos + 3]
    return 5, data[pos:pos + 4], {'a': a, 'b': b, 'c': c}

def _op_23(data, pos, op_offset):
    # voice: [00][ff ff][00 00 00 00][65 00] name\0
    if pos + 9 > len(data):
        raise UnknownInstruction(op_offset, 0x23, 'truncated')
    a = data[pos]
    w1 = struct.unpack_from('<H', data, pos + 1)[0]
    w2 = struct.unpack_from('<I', data, pos + 3)[0]
    w3 = struct.unpack_from('<H', data, pos + 7)[0]
    name, end = _read_cstring(data, pos + 9)
    if not _is_ascii_name(name):
        raise UnknownInstruction(op_offset, 0x23, 'voice name not ascii')
    return end - op_offset, data[pos:end], {
        'a': a, 'w1': w1, 'w2': w2, 'w3': w3, 'name': name.decode('ascii')}

def _op_25(data, pos, op_offset):
    # [u8 var][u8 mode][4 bytes param]
    if pos + 6 > len(data):
        raise UnknownInstruction(op_offset, 0x25, 'truncated')
    var, mode = data[pos], data[pos + 1]
    param = data[pos + 2:pos + 6]
    return 7, data[pos:pos + 6], {'var': var, 'mode': mode, 'param': param.hex()}

def _op_26(data, pos, op_offset):
    if pos + 2 > len(data):
        raise UnknownInstruction(op_offset, 0x26, 'truncated')
    var, z = data[pos], data[pos + 1]
    return 3, data[pos:pos + 2], {'var': var, 'z': z}

def _op_41(data, pos, op_offset):
    # narration: [u16 id][00][0f] text\0
    if pos + 3 > len(data):
        raise UnknownInstruction(op_offset, 0x41, 'truncated')
    sid = struct.unpack_from('<H', data, pos)[0]
    z, flg = data[pos + 2], data[pos + 3]
    if z != 0 or flg != 0x0f:
        raise UnknownInstruction(op_offset, 0x41, 'bad hdr %02x %02x' % (z, flg))
    text, end = _read_cstring(data, pos + 4)
    return end - op_offset, data[pos:end], {
        'id': sid, 'text': text.decode('cp932', 'replace')}

def _op_42(data, pos, op_offset):
    # dialogue: [u16 id][00][0f][0f] speaker\0 text\0
    if pos + 4 > len(data):
        raise UnknownInstruction(op_offset, 0x42, 'truncated')
    sid = struct.unpack_from('<H', data, pos)[0]
    z, f1, f2 = data[pos + 2], data[pos + 3], data[pos + 4]
    if z != 0 or f1 != 0x0f or f2 != 0x0f:
        raise UnknownInstruction(op_offset, 0x42, 'bad hdr %02x %02x %02x' % (z, f1, f2))
    name, p = _read_cstring(data, pos + 5)
    if len(name) > 64:
        raise UnknownInstruction(op_offset, 0x42, 'speaker too long')
    text, end = _read_cstring(data, p)
    return end - op_offset, data[pos:end], {
        'id': sid, 'speaker': name.decode('cp932', 'replace'),
        'text': text.decode('cp932', 'replace')}

def _op_43(data, pos, op_offset):
    # [6 bytes] + name\0
    if pos + 6 > len(data):
        raise UnknownInstruction(op_offset, 0x43, 'truncated')
    params = data[pos:pos + 6]
    name, end = _read_cstring(data, pos + 6)
    if not _is_ascii_name(name):
        raise UnknownInstruction(op_offset, 0x43, 'name not ascii')
    return end - op_offset, data[pos:end], {
        'params': params.hex(), 'name': name.decode('ascii')}

def _op_45(data, pos, op_offset):
    if pos + 4 > len(data):
        raise UnknownInstruction(op_offset, 0x45, 'truncated')
    a = data[pos]
    b = struct.unpack_from('<H', data, pos + 1)[0]
    c = data[pos + 3]
    return 5, data[pos:pos + 4], {'a': a, 'b': b, 'c': c}

def _op_46(data, pos, op_offset):
    # CG display: [9 bytes] + name\0
    if pos + 9 > len(data):
        raise UnknownInstruction(op_offset, 0x46, 'truncated')
    params = data[pos:pos + 9]
    name, end = _read_cstring(data, pos + 9)
    if not _is_ascii_name(name):
        raise UnknownInstruction(op_offset, 0x46, 'name not ascii')
    return end - op_offset, data[pos:end], {
        'params': params.hex(), 'name': name.decode('ascii')}

def _op_47(data, pos, op_offset):
    if pos + 2 > len(data):
        raise UnknownInstruction(op_offset, 0x47, 'truncated')
    a = struct.unpack_from('<H', data, pos)[0]
    return 3, data[pos:pos + 2], {'a': a}

def _op_48(data, pos, op_offset):
    # background: [11 bytes] + name\0
    if pos + 11 > len(data):
        raise UnknownInstruction(op_offset, 0x48, 'truncated')
    params = data[pos:pos + 11]
    name, end = _read_cstring(data, pos + 11)
    if not _is_ascii_name(name):
        raise UnknownInstruction(op_offset, 0x48, 'name not ascii')
    return end - op_offset, data[pos:end], {
        'params': params.hex(), 'name': name.decode('ascii')}

def _op_49(data, pos, op_offset):
    if pos + 3 > len(data):
        raise UnknownInstruction(op_offset, 0x49, 'truncated')
    a, b, c = data[pos], data[pos + 1], data[pos + 2]
    return 4, data[pos:pos + 3], {'a': a, 'b': b, 'c': c}

def _op_4a(data, pos, op_offset):
    if pos + 6 > len(data):
        raise UnknownInstruction(op_offset, 0x4a, 'truncated')
    a = data[pos]
    b = struct.unpack_from('<I', data, pos + 1)[0]
    c = data[pos + 5]
    return 7, data[pos:pos + 6], {'a': a, 'b': b, 'c': c}

def _op_4d(data, pos, op_offset):
    # 13 operand bytes: [u8 s][u32][u32][u16][u16][u8]
    if pos + 13 > len(data):
        raise UnknownInstruction(op_offset, 0x4d, 'truncated')
    s = data[pos]
    v1 = struct.unpack_from('<I', data, pos + 1)[0]
    v2 = struct.unpack_from('<I', data, pos + 5)[0]
    w1 = struct.unpack_from('<H', data, pos + 9)[0]
    w2 = struct.unpack_from('<H', data, pos + 11)[0]
    t = data[pos + 13 - 1 + 0] if False else data[pos + 12]
    return 14, data[pos:pos + 13], {'s': s, 'v1': v1, 'v2': v2, 'w1': w1, 'w2': w2, 't': t}

def _op_4f(data, pos, op_offset):
    if pos + 4 > len(data):
        raise UnknownInstruction(op_offset, 0x4f, 'truncated')
    a = data[pos]
    b = struct.unpack_from('<H', data, pos + 1)[0]
    c = data[pos + 3]
    return 5, data[pos:pos + 4], {'a': a, 'b': b, 'c': c}

def _op_51(data, pos, op_offset):
    if pos + 5 > len(data):
        raise UnknownInstruction(op_offset, 0x51, 'truncated')
    a = struct.unpack_from('<H', data, pos)[0]
    b = struct.unpack_from('<H', data, pos + 2)[0]
    c = data[pos + 4]
    return 6, data[pos:pos + 5], {'a': a, 'b': b, 'c': c}

def _op_55(data, pos, op_offset):
    if pos + 1 > len(data):
        raise UnknownInstruction(op_offset, 0x55, 'truncated')
    return 2, data[pos:pos + 1], {'a': data[pos]}

def _op_65(data, pos, op_offset):
    if pos + 5 > len(data):
        raise UnknownInstruction(op_offset, 0x65, 'truncated')
    a = struct.unpack_from('<H', data, pos)[0]
    b = struct.unpack_from('<H', data, pos + 2)[0]
    c = data[pos + 4]
    return 6, data[pos:pos + 5], {'a': a, 'b': b, 'c': c}

def _op_68(data, pos, op_offset):
    # textbox rect: [u16 x][u16 y][u16 w][u16 h][u8]
    if pos + 9 > len(data):
        raise UnknownInstruction(op_offset, 0x68, 'truncated')
    a, b, c, d = struct.unpack_from('<4H', data, pos)
    e = data[pos + 8]
    return 10, data[pos:pos + 9], {'x': a, 'y': b, 'w': c, 'h': d, 'e': e}

def _op_69(data, pos, op_offset):
    if pos + 2 > len(data):
        raise UnknownInstruction(op_offset, 0x69, 'truncated')
    a = struct.unpack_from('<H', data, pos)[0]
    return 3, data[pos:pos + 2], {'a': a}

def _op_82(data, pos, op_offset):
    # wait [u16 ms][u8 00]
    if pos + 3 > len(data):
        raise UnknownInstruction(op_offset, 0x82, 'truncated')
    ms = struct.unpack_from('<H', data, pos)[0]
    z = data[pos + 2]
    return 4, data[pos:pos + 3], {'ms': ms, 'z': z}

def _op_85(data, pos, op_offset):
    if pos + 2 > len(data):
        raise UnknownInstruction(op_offset, 0x85, 'truncated')
    a = struct.unpack_from('<H', data, pos)[0]
    return 3, data[pos:pos + 2], {'a': a}

def _op_8c(data, pos, op_offset):
    # script header: [u16 script index][u8 00]
    if pos + 3 > len(data):
        raise UnknownInstruction(op_offset, 0x8c, 'truncated')
    idx = struct.unpack_from('<H', data, pos)[0]
    z = data[pos + 2]
    return 4, data[pos:pos + 3], {'script_index': idx, 'z': z}

def _op_8d(data, pos, op_offset):
    if pos + 1 > len(data):
        raise UnknownInstruction(op_offset, 0x8d, 'truncated')
    return 2, data[pos:pos + 1], {'a': data[pos]}

def _op_90(data, pos, op_offset):
    if pos + 4 > len(data):
        raise UnknownInstruction(op_offset, 0x90, 'truncated')
    a = data[pos]
    b = struct.unpack_from('<H', data, pos + 1)[0]
    c = data[pos + 3]
    return 5, data[pos:pos + 4], {'a': a, 'b': b, 'c': c}

def _op_e0(data, pos, op_offset):
    # scene title (Shift-JIS allowed, e.g. "CROSS†CHANNEL")
    name, end = _read_cstring(data, pos)
    if not _is_printable_name(name):
        raise UnknownInstruction(op_offset, 0xe0, 'title not printable')
    return end - op_offset, data[pos:end], {'name': name.decode('cp932', 'replace')}

def _op_ff(data, pos, op_offset):
    # end of script: [u32 a][u32 b]
    if pos + 8 > len(data):
        raise UnknownInstruction(op_offset, 0xff, 'truncated')
    a = struct.unpack_from('<I', data, pos)[0]
    b = struct.unpack_from('<I', data, pos + 4)[0]
    return 9, data[pos:pos + 8], {'a': a, 'b': b}

def _fixed(op, size):
    def h(data, pos, op_offset):
        if pos + size - 1 > len(data):
            raise UnknownInstruction(op_offset, op, 'truncated')
        return size, data[pos:pos + size - 1], {}
    return h

def _op_52(data, pos, op_offset):
    if pos + 2 > len(data):
        raise UnknownInstruction(op_offset, 0x52, 'truncated')
    a = struct.unpack_from('<H', data, pos)[0]
    return 3, data[pos:pos + 2], {'a': a}

def _op_04(data, pos, op_offset):
    # 1-byte instruction (label / nop?) - seen in system scripts
    return 1, b'', {}

def _op_6466(data, pos, op_offset, opcode):
    # [u8 a][u16 x][u16 y][u8 z][u16 w]
    if pos + 8 > len(data):
        raise UnknownInstruction(op_offset, opcode, 'truncated')
    a = data[pos]
    x = struct.unpack_from('<H', data, pos + 1)[0]
    y = struct.unpack_from('<H', data, pos + 3)[0]
    z = data[pos + 5]
    w = struct.unpack_from('<H', data, pos + 6)[0]
    return 9, data[pos:pos + 8], {'a': a, 'x': x, 'y': y, 'z': z, 'w': w}

def _op_64(data, pos, op_offset):
    return _op_6466(data, pos, op_offset, 0x64)

def _op_66(data, pos, op_offset):
    return _op_6466(data, pos, op_offset, 0x66)

def _op_67(data, pos, op_offset):
    if pos + 8 > len(data):
        raise UnknownInstruction(op_offset, 0x67, 'truncated')
    return 9, data[pos:pos + 8], {'raw': data[pos:pos + 8].hex()}

def _op_fa(data, pos, op_offset):
    if pos + 13 > len(data):
        raise UnknownInstruction(op_offset, 0xfa, 'truncated')
    return 14, data[pos:pos + 13], {'raw': data[pos:pos + 13].hex()}

def _op_b8(data, pos, op_offset):
    if pos + 3 > len(data):
        raise UnknownInstruction(op_offset, 0xb8, 'truncated')
    a = data[pos]
    b = struct.unpack_from('<H', data, pos + 1)[0]
    return 4, data[pos:pos + 3], {'a': a, 'b': b}

def _op_74(data, pos, op_offset):
    if pos + 2 > len(data):
        raise UnknownInstruction(op_offset, 0x74, 'truncated')
    a = struct.unpack_from('<H', data, pos)[0]
    return 3, data[pos:pos + 2], {'a': a}

def _op_8e(data, pos, op_offset):
    if pos + 1 > len(data):
        raise UnknownInstruction(op_offset, 0x8e, 'truncated')
    return 2, data[pos:pos + 1], {'a': data[pos]}

def _op_0d(data, pos, op_offset):
    if pos + 7 > len(data):
        raise UnknownInstruction(op_offset, 0x0d, 'truncated')
    return 8, data[pos:pos + 7], {'raw': data[pos:pos + 7].hex()}

def _op_e4(data, pos, op_offset):
    if pos + 2 > len(data):
        raise UnknownInstruction(op_offset, 0xe4, 'truncated')
    return 3, data[pos:pos + 2], {'raw': data[pos:pos + 2].hex()}

def _op_ab(data, pos, op_offset):
    if pos + 1 > len(data):
        raise UnknownInstruction(op_offset, 0xab, 'truncated')
    return 2, data[pos:pos + 1], {'a': data[pos]}

def _op_a1(data, pos, op_offset):
    if pos + 7 > len(data):
        raise UnknownInstruction(op_offset, 0xa1, 'truncated')
    a = data[pos]
    b = struct.unpack_from('<H', data, pos + 1)[0]
    c = struct.unpack_from('<I', data, pos + 3)[0]
    return 8, data[pos:pos + 7], {'a': a, 'b': b, 'c': c}


def _op_61(data, pos, op_offset):
    # [u8 flag] + filename (e.g. CCOP_A.DAT)
    if pos + 1 > len(data):
        raise UnknownInstruction(op_offset, 0x61, 'truncated')
    a = data[pos]
    name, end = _read_cstring(data, pos + 1)
    if not _is_ascii_name(name):
        raise UnknownInstruction(op_offset, 0x61, 'name not ascii')
    return end - op_offset, data[pos:end], {'a': a, 'name': name.decode('ascii')}

def _op_72(data, pos, op_offset):
    if pos + 1 > len(data):
        raise UnknownInstruction(op_offset, 0x72, 'truncated')
    return 2, data[pos:pos + 1], {'a': data[pos]}

def _op_4e(data, pos, op_offset):
    if pos + 4 > len(data):
        raise UnknownInstruction(op_offset, 0x4e, 'truncated')
    v = struct.unpack_from('<I', data, pos)[0]
    return 5, data[pos:pos + 4], {'value': v}

def _op_89(data, pos, op_offset):
    if pos + 1 > len(data):
        raise UnknownInstruction(op_offset, 0x89, 'truncated')
    return 2, data[pos:pos + 1], {'a': data[pos]}

def _op_b2(data, pos, op_offset):
    # [u8 a][u8 b] + lowercase name
    if pos + 2 > len(data):
        raise UnknownInstruction(op_offset, 0xb2, 'truncated')
    a, b = data[pos], data[pos + 1]
    name, end = _read_cstring(data, pos + 2)
    if not _is_ascii_name(name):
        raise UnknownInstruction(op_offset, 0xb2, 'name not ascii')
    return end - op_offset, data[pos:end], {'a': a, 'b': b, 'name': name.decode('ascii')}

def _op_b4(data, pos, op_offset):
    if pos + 12 > len(data):
        raise UnknownInstruction(op_offset, 0xb4, 'truncated')
    return 13, data[pos:pos + 12], {'raw': data[pos:pos + 12].hex()}

def _op_b9(data, pos, op_offset):
    if pos + 3 > len(data):
        raise UnknownInstruction(op_offset, 0xb9, 'truncated')
    a = data[pos]
    b = struct.unpack_from('<H', data, pos + 1)[0]
    return 4, data[pos:pos + 3], {'a': a, 'b': b}

def _op_bd(data, pos, op_offset):
    if pos + 2 > len(data):
        raise UnknownInstruction(op_offset, 0xbd, 'truncated')
    a, b = data[pos], data[pos + 1]
    return 3, data[pos:pos + 2], {'a': a, 'b': b}

def _op_8b(data, pos, op_offset):
    if pos + 1 > len(data):
        raise UnknownInstruction(op_offset, 0x8b, 'truncated')
    return 2, data[pos:pos + 1], {'a': data[pos]}

def _op_b3(data, pos, op_offset):
    if pos + 2 > len(data):
        raise UnknownInstruction(op_offset, 0xb3, 'truncated')
    return 3, data[pos:pos + 2], {'raw': data[pos:pos + 2].hex()}

def _op_83(data, pos, op_offset):
    if pos + 1 > len(data):
        raise UnknownInstruction(op_offset, 0x83, 'truncated')
    return 2, data[pos:pos + 1], {'a': data[pos]}

def _op_e2(data, pos, op_offset):
    if pos + 1 > len(data):
        raise UnknownInstruction(op_offset, 0xe2, 'truncated')
    return 2, data[pos:pos + 1], {'a': data[pos]}

HANDLERS = {
    0x00: _op_00,
    0x01: _op_01,
    0x02: _op_02,
    0x03: _op_03,
    0x04: _op_04,
    0x06: _op_06,
    0x0d: _op_0d,
    0x07: lambda d, p, o: _op_name(d, p, o, 0x07),
    0x08: _op_08,
    0x09: lambda d, p, o: _op_name(d, p, o, 0x09),
    0x0a: _op_0a,
    0x0b: _op_0b,
    0x21: _op_21,
    0x22: _op_22,
    0x23: _op_23,
    0x25: _op_25,
    0x26: _op_26,
    0x41: _op_41,
    0x42: _op_42,
    0x43: _op_43,
    0x45: _op_45,
    0x46: _op_46,
    0x47: _op_47,
    0x48: _op_48,
    0x49: _op_49,
    0x4a: _op_4a,
    0x4e: _op_4e,
    0x4d: _op_4d,
    0x4f: _op_4f,
    0x61: _op_61,
    0x72: _op_72,
    0x89: _op_89,
    0xb2: _op_b2,
    0xb4: _op_b4,
    0xb9: _op_b9,
    0xbd: _op_bd,
    0xe2: _op_e2,
    0x50: lambda d, p, o: _op_name(d, p, o, 0x50),
    0x51: _op_51,
    0x52: _op_52,
    0x54: lambda d, p, o: _op_name(d, p, o, 0x54),
    0x55: _op_55,
    0x64: _op_64,
    0x65: _op_65,
    0x66: _op_66,
    0x67: _op_67,
    0x74: _op_74,
    0x8e: _op_8e,
    0xb8: _op_b8,
    0xfa: _op_fa,
    0x68: _op_68,
    0x69: _op_69,
    0x82: _op_82,
    0x83: _op_83,
    0xb3: _op_b3,
    0x85: _op_85,
    0x8c: _op_8c,
    0x8b: _op_8b,
    0x8d: _op_8d,
    0x90: _op_90,
    0xa1: _op_a1,
    0xab: _op_ab,
    0xe4: _op_e4,
    0xe0: _op_e0,
    0xff: _op_ff,
}

def disassemble(data):
    """Linearly disassemble a decrypted WSC script. Returns list[Instruction].
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
        out.append(Instruction(pos, op, size, operands, fields))
        pos += size
    return out
