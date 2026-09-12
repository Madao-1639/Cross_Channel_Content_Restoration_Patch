"""Lua 5.3 反汇编器（配合 tool/luac53.py 使用）。

opcode 枚举与上游 5.3 不同：本引擎在索引 9 处多出一个操作码，
因此 `SETTABLE` 起的所有操作码整体后移 1 位。该码在整个 Script.arc
（753 个 proto、约 13 万条指令）里从未出现，身份未知也不影响反汇编。
详见 doc/engine-mechanics.md。

用法：
    python tool/luadis53.py <Script.arc 成员名> [--proto N] [--all]
"""
import sys

# 上游 Lua 5.3 lopcodes.h 顺序
_UPSTREAM = ['MOVE', 'LOADK', 'LOADKX', 'LOADBOOL', 'LOADNIL', 'GETUPVAL',
 'GETTABUP', 'GETTABLE', 'SETTABUP', 'SETTABLE', 'NEWTABLE', 'SELF', 'ADD',
 'SUB', 'MUL', 'MOD', 'POW', 'DIV', 'IDIV', 'BAND', 'BOR', 'BXOR', 'SHL', 'SHR',
 'UNM', 'BNOT', 'NOT', 'LEN', 'CONCAT', 'JMP', 'EQ', 'LT', 'LE', 'TEST',
 'TESTSET', 'CALL', 'TAILCALL', 'RETURN', 'FORLOOP', 'FORPREP', 'TFORCALL',
 'TFORLOOP', 'SETLIST', 'CLOSURE', 'VARARG', 'EXTRAARG']

OPCODES = _UPSTREAM[:9] + ['OP_UNKNOWN9'] + _UPSTREAM[9:]
OPINDEX = {n: i for i, n in enumerate(OPCODES)}

BIN_OPS = {'ADD': '+', 'SUB': '-', 'MUL': '*', 'MOD': '%', 'POW': '^',
           'DIV': '/', 'IDIV': '//', 'BAND': '&', 'BOR': '|', 'BXOR': '~',
           'SHL': '<<', 'SHR': '>>'}
UN_OPS = {'UNM': '-', 'BNOT': '~', 'NOT': 'not ', 'LEN': '#'}
RK_OPS = set(BIN_OPS) | {'EQ', 'LT', 'LE', 'GETTABLE', 'SETTABLE', 'SELF',
                         'GETTABUP', 'SETTABUP'}
CMP = {'EQ': '==', 'LT': '<', 'LE': '<='}
SBX_OPS = {'JMP', 'FORLOOP', 'FORPREP', 'TFORLOOP'}
MAXARG_sBx = (1 << 18) - 1 >> 1


def fields(w):
    return {
        'op': w & 0x3F,
        'A': (w >> 6) & 0xFF,
        'B': (w >> 23) & 0x1FF,
        'C': (w >> 14) & 0x1FF,
        'Bx': (w >> 14) & 0x3FFFF,
        'Ax': (w >> 6) & 0x3FFFFFF,
    }


def kstr(proto, i):
    if i >= len(proto.constants):
        return 'K(%d)<oob>' % i
    tag, v = proto.constants[i]
    if tag in (4, 20):
        try:
            return repr(v.decode('utf-8'))
        except UnicodeDecodeError:
            return repr(v)
    return repr(v)


class Dis:
    def __init__(self, proto):
        self.p = proto

    def _rk(self, x):
        return ('K(%d)=%s' % (x & 0xFF, kstr(self.p, x & 0xFF))) if x & 0x100 \
            else 'R%d' % x

    def line(self, pc):
        w = self.p.code[pc]
        f = fields(w)
        op = f['op']
        nm = OPCODES[op] if op < len(OPCODES) else 'OP_%d' % op
        A, B, C = f['A'], f['B'], f['C']
        if nm == 'MOVE':
            s = 'R%d := R%d' % (A, B)
        elif nm == 'LOADK':
            s = 'R%d := K(%d)=%s' % (A, f['Bx'], kstr(self.p, f['Bx']))
        elif nm == 'LOADKX':
            s = 'R%d := K(extra)' % A
        elif nm == 'LOADBOOL':
            s = 'R%d := %s%s' % (A, bool(B), '; pc++' if C else '')
        elif nm == 'LOADNIL':
            s = 'R%d..R%d := nil' % (A, A + B)
        elif nm == 'GETUPVAL':
            s = 'R%d := U%d' % (A, B)
        elif nm == 'GETTABUP':
            s = 'R%d := U%d[%s]' % (A, B, self._rk(C))
        elif nm == 'GETTABLE':
            s = 'R%d := R%d[%s]' % (A, B, self._rk(C))
        elif nm == 'SETTABUP':
            s = 'U%d[%s] := %s' % (A, self._rk(B), self._rk(C))
        elif nm == 'SETTABLE':
            s = 'R%d[%s] := %s' % (A, self._rk(B), self._rk(C))
        elif nm == 'NEWTABLE':
            # B = 数组部分长度(na)，C = 哈希部分长度(nh)。
            # 实测依据：`{name=,file=,CharSet=}` 这种三键表解出 B=0 / C=3。
            s = 'R%d := {} (array=%d hash=%d)' % (A, B, C)
        elif nm == 'SELF':
            s = 'R%d := R%d; R%d := R%d[%s]' % (A + 1, B, A, B, self._rk(C))
        elif nm in BIN_OPS:
            s = 'R%d := %s %s %s' % (A, self._rk(B), BIN_OPS[nm], self._rk(C))
        elif nm in UN_OPS:
            s = 'R%d := %s%s' % (A, UN_OPS[nm], self._rk(B))
        elif nm == 'CONCAT':
            s = 'R%d := R%d .. R%d' % (A, B, C)
        elif nm == 'JMP':
            t = pc + 1 + f['Bx'] - MAXARG_sBx
            s = '-> %d%s' % (t, '  (close upvals >= R%d)' % (A - 1) if A else '')
        elif nm in CMP:
            s = 'if (%s %s %s) ~= %s then pc++' % (
                self._rk(B), CMP[nm], self._rk(C), bool(A))
        elif nm == 'TEST':
            s = 'if not (R%d %s nil) then pc++' % (A, '<=' if C else '>')
        elif nm == 'TESTSET':
            s = 'if (R%d %s nil) then R%d := R%d else pc++' % (
                B, '<=' if C else '>', A, B)
        elif nm == 'CALL':
            a = ', '.join('R%d' % r for r in range(A, A + B))
            s = '%s -> %s' % (a, 'R%d..R%d' % (A, A + C - 2) if C > 1 else '0 vals')
        elif nm == 'TAILCALL':
            s = 'return R%d(R%d..R%d)' % (A, A + 1, A + B - 1)
        elif nm == 'RETURN':
            s = 'return R%d..R%d' % (A, A + B - 2) if B > 1 else 'return'
        elif nm == 'FORLOOP':
            t = pc + 1 + f['Bx'] - MAXARG_sBx
            s = 'R%d += R%d; if R%d <= R%d then R%d := R%d; -> %d' % (
                A, A + 2, A, A + 1, A + 3, A, t)
        elif nm == 'FORPREP':
            t = pc + 1 + f['Bx'] - MAXARG_sBx
            s = 'R%d -= R%d; -> %d' % (A, A + 2, t)
        elif nm == 'TFORCALL':
            s = 'R%d..R%d := R%d(R%d, R%d)' % (A + 3, A + 2 + C, A, A + 1, A + 2)
        elif nm == 'TFORLOOP':
            t = pc + 1 + f['Bx'] - MAXARG_sBx
            s = 'if R%d ~= nil then R%d := R%d; -> %d' % (A + 1, A, A + 1, t)
        elif nm == 'SETLIST':
            s = 'R%d[%s..] := R%d..R%d' % (
                A, 'K(%d)' % C if C else '%d' % (C * 50), A + 1, A + B)
        elif nm == 'CLOSURE':
            s = 'R%d := proto[%d]' % (A, f['Bx'])
        elif nm == 'VARARG':
            s = 'R%d..R%d := ...' % (A, A + B - 2)
        elif nm == 'EXTRAARG':
            s = 'extra %d' % f['Ax']
        else:
            s = '(unknown) A=%d B=%d C=%d' % (A, B, C)
        return nm, s


def dump(proto, label='', depth=0):
    ind = '  ' * depth
    print('%s; %s lines %d-%d params=%d vararg=%d maxstack=%d upvals=%d'
          % (ind, label, proto.line_defined, proto.last_line_defined,
             proto.num_params, proto.is_vararg, proto.max_stack_size,
             len(proto.upvalues)))
    d = Dis(proto)
    for pc in range(len(proto.code)):
        nm, s = d.line(pc)
        ln = proto.lineinfo[pc] if pc < len(proto.lineinfo) else 0
        print('%s  %4d [%4d] %-16s %s' % (ind, pc, ln, nm, s))
    for i, sub in enumerate(proto.protos):
        print()
        dump(sub, '%s/%d' % (label, i), depth)


def load_member(target):
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from tool import arcbuild, luac53
    if os.path.exists(target):
        return luac53.load(open(target, 'rb').read())
    members = dict((n.decode('utf-16le'), d)
                   for n, d in arcbuild.read_raw('asset/Script.arc'))
    if target not in members:
        raise SystemExit('member %r not found' % target)
    return luac53.load(members[target])


def main(argv):
    if not argv:
        print(__doc__)
        return 1
    proto = load_member(argv[0])
    protos = list(proto.walk())
    if '--all' in argv:
        for i, p in enumerate(protos):
            if i:
                print()
            dump(p, 'proto %d' % i)
    else:
        idx = int(argv[argv.index('--proto') + 1]) if '--proto' in argv else 0
        dump(protos[idx], 'proto %d' % idx)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
