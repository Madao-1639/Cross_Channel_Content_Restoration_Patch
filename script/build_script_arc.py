# -*- coding: utf-8 -*-
"""构建语言包 `Script.arc` —— **自己的 Lua 成员，不依赖任何上游归档**。

## 形状

Steam 原版**本身就有** `Script.arc`（`backup/Script.arc`，14 个成员：13 个 Lua 5.3 字节码 +
`LegacyGame.inc`），但它是日文配置：`PatchFlag=false`、字体槽指向 `FOT-マティス Pro` /
`SHIFTJIS_CHARSET`。要让中文生效，**只需换掉两个成员里的几个常量**：

| 成员 | 改动 | 语义 |
|---|---|---|
| `ui_language.lua` | 布尔常量 `False` → `True` | 语言包开关（`getLangPatchFlag()` / `setInitLang()`）；`getFontName()` 据此选 `g_fonts2` 槽 |
| `LegacyGame_utf8.lua` | 字体名 → 思源族名；`CharSet` → `GB2312_CHARSET` | 字体槽（`.file` 不动 —— 那是 `Fonts.arc` 的成员名） |

**做法**：用 `tool.luac53` 的 `load()` / `dump()` **只替换常量表里的值**，指令、行号、
upvalue、调试信息全部原样保留 —— 所以产物与 Steam 原版的差异**精确等于那几个常量**，
可逐项核对（`--diff`）。

**为什么不按字节偏移打补丁**：字符串常量常被多处引用、长度还会变
（`FOT-マティス Pro M` → `Source Han Sans SC Medium`），按字节改必然错位。

## 产出

`asset/zh-CN/Script.arc` —— **只发与 Steam 原版不同的成员**（官方 IMHHW 的 `zh-CN/Script.arc`
只有 1 个成员即可生效，按成员覆盖是本引擎的既有机制）。归档内成员顺序照 Steam 原版。

## 用法（项目根目录）

    python script/build_script_arc.py            # 预演（打印改动）
    python script/build_script_arc.py --write    # 落盘
    python script/build_script_arc.py --diff     # 只打印与 Steam 原版的常量差异
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tool import arcbuild, layers, luac53  # noqa: E402

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

NAME = 'Script.arc'
ORIG = ROOT / 'backup' / NAME

# 字体槽：`name` 必须等于子集 TTF 的 name 表 id1（见 `script/fix_font_names.py`），
# `CharSet` 必须是引擎暴露给 Lua 的全局常量名。`.file` 不动 —— 那是 `Fonts.arc` 的成员名。
FONT_MEDIUM = 'Source Han Sans SC Medium'
FONT_BOLD = 'Source Han Sans SC Bold'
CHARSET = 'GB2312_CHARSET'

# 要替换的常量：(旧值, 新值)。按**值**匹配而不是下标 —— 下标随编译器版本变。
FONT_SUBS = (
    (FONT_MEDIUM, FONT_MEDIUM),
    (FONT_BOLD, FONT_BOLD),
)

SUB_OLD_NEW = (
    ('FOT-マティス Pro M', FONT_MEDIUM),   # g_fonts.name
    ('FOT-マティス Pro B', FONT_BOLD),     # g_fonts3.name
    ('Tahoma', FONT_MEDIUM),               # g_fonts2.name（原版 = 系统兜底字体）
    ('SHIFTJIS_CHARSET', CHARSET),         # 三处 CharSet 共用同一常量
)


def _load_members(path):
    """`{成员名: bytes}`（成员名按 UTF-16LE 解，与其它工具一致）。"""
    return {n.decode('utf-16-le'): d for n, d in arcbuild.read_raw(path)}


def sub_font_constants(top):
    """在 `top` 及其子原型的常量表里按值换字体/字符集常量，返回 {旧值: 次数}。"""
    hit = {}
    for f in top.walk():
        for i, (tag, value) in enumerate(f.constants):
            if tag not in luac53.STR_TAGS:
                continue
            for old, new in SUB_OLD_NEW:
                if value == old.encode('utf-8'):
                    f.constants[i] = (tag, new.encode('utf-8'))
                    hit[old] = hit.get(old, 0) + 1
                    break
    return hit


def flip_lang_switch(top):
    """把语言包开关置真，返回 (布尔常量翻转数, LOADBOOL 翻转数)。

    该成员的两个开关在**不同位置**（这是实测结论，不要按同一个假设写）：

    * `getLangPatchFlag()` 的 `return false` 编成 `LOADBOOL R0 := False`（**指令内嵌 B 字段**，
      不在常量表）；改成 `True` 只需把 B 置 1（C=0 不跳过下一条）。
    * `setInitLang()` 的 `g_altLanguage = false` 走 `SETTABUP ... := K(False)`（**在常量表**）。
    """
    from tool import luadis53
    lb = luadis53.OPINDEX['LOADBOOL']
    n_const = n_code = 0
    for f in top.walk():
        for i, (tag, value) in enumerate(f.constants):
            if tag == luac53.T_BOOLEAN and value is False:
                f.constants[i] = (tag, True)
                n_const += 1
        for i, w in enumerate(f.code):
            fl = luadis53.fields(w)
            if fl['op'] == lb and fl['B'] == 0:
                f.code[i] = (w & ~(0x1FF << 23)) | (1 << 23)   # B: 0 -> 1
                n_code += 1
    return n_const, n_code


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--write', action='store_true')
    ap.add_argument('--diff', action='store_true', help='只打印与 Steam 原版的常量差异')
    args = ap.parse_args(argv)

    src = ORIG
    if not src.exists():
        raise SystemExit('[中止] 找不到 %s\n'
                         '       它是 **Steam 原版**归档，需自带一份到 backup/（不入库）。' % ORIG)

    orig = _load_members(src)
    out = []            # [(name_bytes, data)] —— 顺序照基准归档
    changed = []
    for nb, data in arcbuild.read_raw(src):
        member = nb.decode('utf-16-le')
        if not data.startswith(b'\x1bLua'):
            out.append((nb, data))                      # LegacyGame.inc 等二进制成员：原样
            continue
        if member == 'ui_language.lua':
            top = luac53.load(data)
            nc, ni = flip_lang_switch(top)
            new = luac53.dump(top)
            print('[%s] 语言包开关置真：常量 %d 处 + LOADBOOL %d 处' % (member, nc, ni))
        elif member == 'LegacyGame_utf8.lua':
            top = luac53.load(data)
            hit = sub_font_constants(top)
            new = luac53.dump(top)
            print('[%s] 替换 %d 处常量：%s' % (
                member, sum(hit.values()),
                '，'.join('%s→%s ×%d' % (o, dict(SUB_OLD_NEW)[o], c) for o, c in hit.items())
                or '（无命中）'))
        else:
            out.append((nb, data))                      # 其余 11 个成员原样
            continue
        if new == data:
            print('[%s] 与基准相同，无需改动' % member)
            out.append((nb, data))
        else:
            changed.append(member)
            out.append((nb, new))

    got = {nb.decode('utf-16-le'): d for nb, d in out}
    if args.diff:
        print('\n== 与基准的常量差异 ==')
        n = 0
        for member in changed:
            a = luac53.load(orig[member]).strings
            b = luac53.load(got[member]).strings
            for i, (x, y) in enumerate(zip(a, b)):
                if x != y:
                    print('  %-22s K(%-2d) %-34r -> %r' % (member, i, x, y))
                    n += 1
        print('  共 %d 处' % n)
        return 0

    # **只发差异成员**：与基准逐字节相同的成员不入包（官方 IMHHW 的 `zh-CN/Script.arc` 只 1 个成员
    # 即可生效）。成员顺序照基准归档。若全无差异，改为发全量以保证归档非空。
    orig_bytes = {nb.decode('utf-16-le'): d for nb, d in arcbuild.read_raw(src)}
    shipped = [(nb, d) for nb, d in out
               if orig_bytes.get(nb.decode('utf-16-le')) != d]
    if not shipped:
        raise SystemExit('[中止] 没有任何改动 —— 常量匹配失效（编译器版本变了？）')

    print('\n源 %d 成员 -> 语言包 %d 成员（与 Steam 原版逐字节相同而剔除的 %d 个）'
          % (len(orig), len(shipped), len(orig) - len(shipped)))
    print('改动成员：%s' % ('，'.join(changed) or '无'))

    target = layers.pack_arc(NAME)
    if args.write:
        layers.write_layer(target, shipped, expect_count=len(shipped))
        print('[写盘] %s（%d 成员）' % (target, len(shipped)))
    else:
        print('[预演] 未写盘；加 --write 落盘')
    return 0


if __name__ == '__main__':
    sys.exit(main())
