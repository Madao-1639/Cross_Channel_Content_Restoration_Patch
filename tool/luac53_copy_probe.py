"""Lua 5.3 bytecode reader (undump).

`Script.arc` 里的 *.lua 成员是 Lua 5.3 编译产物（签名 `\\x1bLuaS`），不是混淆 ——
之前"解不出"是因为它是字节码而非源码文本。本模块按 lundump.c 的结构读出 Proto 树，
用于提取字符串常量、定位界面文本、比对 `LegacyGame.lua` / `LegacyGame_utf8.lua`。

**与上游 Lua 5.3 的差异（实测得出，见 doc/engine-mechanics.md）**：

| 字段 | 上游 5.3 | 本引擎 |
|---|---|---|
| 主 chunk 前置字节 | `sizeupvalues`（1 字节） | 同 |
| 字符串长度 `LoadSize` | `sizeof(size_t)` = 4 字节 | **1 字节** |
| 计数 `LoadInt` | `sizeof(int)` = 4 字节 | 同 |

字符串长度 1 字节这一点是穷举验证的：把 (前置字节数, 字符串长度宽度, 计数宽度,
行号宽度) 的全部组合跑一遍，只有 (1, 1, 4, 4) 能让 Script.arc 的 13 个成员
**逐个精确消耗到文件末尾**，且提取出的字符串全部可读。全部 13 个文件的自洽
也反证了没有任何字符串长度 ≥ 256（否则必然错位）。
"""
import struct

SIGNATURE = b'\x1bLua'

# 常量 tag（lua.h：LUA_Txxx | (variant << 4)）
T_NIL = 0
T_BOOLEAN = 1
T_NUMFLT = 3          # LUA_TNUMBER | (0 << 4)
T_SHORT_STR = 4       # LUA_TSTRING | (0 << 4)
T_NUMINT = 19         # LUA_TNUMBER | (1 << 4)
T_LONG_STR = 20       # LUA_TSTRING | (1 << 4)

STR_TAGS = (T_SHORT_STR, T_LONG_STR)


class LuaFormatError(Exception):
    pass


class Reader:
    def __init__(self, blob):
        self.blob = blob
        self.pos = 0

    def bytes(self, n):
        if self.pos + n > len(self.blob):
            raise LuaFormatError('truncated at %d (+%d)' % (self.pos, n))
        out = self.blob[self.pos:self.pos + n]
        self.pos += n
        return out

    def u8(self):
        return self.bytes(1)[0]

    def uint(self, n):
        """n 字节小端无符号整数（Lua 的 LoadInt/LoadSize 都是逐字节拼的）。"""
        return int.from_bytes(self.bytes(n), 'little', signed=False)

    def integer(self, n):
        return int.from_bytes(self.bytes(n), 'little', signed=True)

    def number(self, n):
        if n == 8:
            return struct.unpack('<d', self.bytes(8))[0]
        if n == 4:
            return struct.unpack('<f', self.bytes(4))[0]
        raise LuaFormatError('unsupported lua_Number size %d' % n)


class Proto:
    """一个函数原型。常量表里既可能是数字也可能是字符串；`strings` 是其中的字符串。"""

    def __init__(self):
        self.source = None          # 源文件名（来自调试信息），bytes
        self.line_defined = 0
        self.last_line_defined = 0
        self.num_params = 0
        self.is_vararg = 0
        self.max_stack_size = 0
        self.code = []              # list[int] 4 字节指令
        self.constants = []         # list[(tag, value)]
        self.upvalues = []          # list[(instack, idx)]
        self.protos = []            # list[Proto]
        self.lineinfo = []
        self.locvars = []           # list[(name_bytes, startpc, endpc)]
        self.upvalue_names = []     # list[bytes | None]

    @property
    def strings(self):
        return [v for t, v in self.constants if t in STR_TAGS]

    def walk(self):
        """先序遍历自身与所有子原型。"""
        yield self
        for p in self.protos:
            yield from p.walk()


def load(blob):
    """解析字节码，返回顶层 Proto。"""
    r = Reader(blob)
    if r.bytes(4) != SIGNATURE:
        raise LuaFormatError('not Lua bytecode (bad signature)')
    version = r.u8()
    if version != 0x53:
        raise LuaFormatError('expected Lua 5.3 (0x53), got 0x%02x' % version)
    fmt = r.u8()
    if fmt != 0:
        raise LuaFormatError('unsupported dump format %d' % fmt)
    r.bytes(6)                                  # LUAC_DATA
    sizes = {
        'int': r.u8(),
        'size_t': r.u8(),
        'Instruction': r.u8(),
        'lua_Integer': r.u8(),
        'lua_Number': r.u8(),
    }
    if sizes['Instruction'] != 4:
        raise LuaFormatError('Instruction size %d' % sizes['Instruction'])
    if r.integer(sizes['lua_Integer']) != 0x5678:
        raise LuaFormatError('LUAC_INT check failed')
    if r.number(sizes['lua_Number']) != 370.5:
        raise LuaFormatError('LUAC_NUM check failed')

    def load_string():
        # 本引擎的字符串长度是 1 字节，不是头里声明的 sizeof(size_t)；见模块 docstring。
        size = r.u8()
        if size == 0:
            return None
        if size == 0xFF:
            # 1 字节长度能表达的最大值。真出现这么长的字符串，说明长度字段
            # 可能并不是 1 字节而是变长编码，此处的假设需要重新验证。
            raise LuaFormatError('string of length 255 at %d — length encoding '
                                 'assumption needs revisiting' % r.pos)
        return r.bytes(size - 1)                # 末尾自带 \0，size 含它

    def load_function(parent_source):
        f = Proto()
        src = load_string()
        f.source = src if src is not None else parent_source
        f.line_defined = r.uint(sizes['int'])
        f.last_line_defined = r.uint(sizes['int'])
        f.num_params = r.u8()
        f.is_vararg = r.u8()
        f.max_stack_size = r.u8()

        n = r.uint(sizes['int'])
        f.code = list(struct.unpack('<%dI' % n, r.bytes(4 * n))) if n else []

        n = r.uint(sizes['int'])
        for _ in range(n):
            tag = r.u8()
            if tag == T_NIL:
                f.constants.append((tag, None))
            elif tag == T_BOOLEAN:
                f.constants.append((tag, bool(r.u8())))
            elif tag == T_NUMFLT:
                f.constants.append((tag, r.number(sizes['lua_Number'])))
            elif tag == T_NUMINT:
                f.constants.append((tag, r.integer(sizes['lua_Integer'])))
            elif tag in STR_TAGS:
                f.constants.append((tag, load_string()))
            else:
                raise LuaFormatError('bad constant tag %d' % tag)

        n = r.uint(sizes['int'])
        for _ in range(n):
            f.upvalues.append((r.u8(), r.u8()))

        n = r.uint(sizes['int'])
        for _ in range(n):
            f.protos.append(load_function(f.source))

        n = r.uint(sizes['int'])
        f.lineinfo = list(r.bytes(n)) if n else []

        n = r.uint(sizes['int'])
        for _ in range(n):
            name = load_string()
            f.locvars.append((name, r.uint(sizes['int']), r.uint(sizes['int'])))

        n = r.uint(sizes['int'])
        f.upvalue_names = [load_string() for _ in range(n)]
        return f

    # 主 closure 的 upvalue 数（luaU_dump 的 `DumpByte(f->sizeupvalues)`）。
    # 普通 chunk 是 1（_ENV），不是 0，别拿它当错误的判据。
    load_upvalue_count = r.u8()
    top = load_function(None)
    if r.pos != len(blob):
        raise LuaFormatError('%d trailing bytes after top-level function'
                             % (len(blob) - r.pos))
    return top
