# -*- coding: utf-8 -*-
"""编译语言包里的 `AdvHDLang.dll`（`MultiByteToWideChar` 位置门 hook + **中文设置界面串表**）。

流程：
  1. （无需白名单：hook 只按位置判定，见 `tool/hook/advhdlang_hook.c`）
  2. 读 `resource/AdvHDLang_zh.json`（设置界面中文串，唯一来源）+ 从 `resource/AdvHDLang.orig.dll`
     取 manifest，生成 `app.rc` + `app.manifest`，用 **windres** 编成 `.res`
     （链接器生成**合法**资源树；手工搬 `.rsrc` 会被 `LoadStringW` 判为非法 → 设置界面文字全空）；
  3. 用 32 位 MinGW 编译并链接 hook + 资源 → `asset/zh-CN/AdvHDLang.dll`；
  4. 校验：i386 / DLL 标志 / 资源键集合 = {2052 的 RT_STRING 块} + manifest / **`LoadStringW` 实测**
     逐条等于表值 / 表覆盖原件的全部字符串 ID。

**为什么只发 2052、不发 1033+1041**：设置界面的每条文字都是 `LoadStringW(hInstance, id)`，
`hInstance` = 引擎 `LoadLibraryW("<游戏目录>\\<语言>\\AdvHDLang.dll")` 的结果 ⇒ 语言包选中后
界面语言由本 DLL 的串表决定（官中 IMHHW 的 `zh-CN/AdvHDLang.dll` 同样只有一块 2052）。
只发 2052 ⇒ 玩家选中文后界面必定是中文（系统语言是日文也走 "any language" 兜底命中 2052）；
根目录那份 Steam 原档 DLL 不动 ⇒ 英文模式下界面仍是原样。

工具链：`tmp/toolchain/root/mingw32/bin/`（可用 `MINGW32_BIN` 覆盖）。

用法：`python script/build_lang_dll.py [--check]`
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tool import arcbuild, layers, peres, ws2, ws2disasm  # noqa: E402

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

ORIG_DLL = ROOT / 'resource' / 'AdvHDLang.orig.dll'
ZH_JSON = ROOT / 'resource' / 'AdvHDLang_zh.json'
HOOK_C = ROOT / 'tool' / 'hook' / 'advhdlang_hook.c'
OUT = ROOT / 'asset' / 'zh-CN' / 'AdvHDLang.dll'
WL_H = ROOT / 'tool' / 'hook' / 'whitelist.h'
BUILD = ROOT / 'tmp' / 'build_dll'

RT_STRING, RT_MANIFEST = peres.RT_STRING, peres.RT_MANIFEST
LANG_ZH = 2052          # LANG_CHINESE，与官中语言包一致（只发这一块）
read_resources, extract_strings = peres.read_resources, peres.extract_strings


def whitelist_from_pack():
    """从语言包 ws2 里扫出所有非 ASCII 的 8f/90 字幕（**自动**生成兜底白名单）。"""
    pack_path = layers.pack_arc('Rio.arc')
    out = set()
    if not Path(pack_path).exists():
        return []
    for nb, data in arcbuild.read_raw(pack_path):
        if not nb.decode('utf-16-le').lower().endswith('.ws2'):
            continue
        raw = ws2.decode(data)
        for o in ws2disasm.disassemble(raw):
            if o.opcode == 0x8f:
                p = o.offset + 1
                for _ in range(2):
                    p = raw.index(bytes([0]), p) + 1
                p += 1
                t = raw[p:raw.index(bytes([0]), p)]
            elif o.opcode == 0x90:
                p = o.offset + 1
                t = raw[o.offset + 1:raw.index(bytes([0]), p)]
            else:
                continue
            if any(b >= 0x80 for b in t):
                out.add(t)
    return sorted(out)


def gen_whitelist(blobs):
    lines = ['/* 自动生成：script/build_lang_dll.py（扫描语言包 ws2 的 8f/90 非 ASCII 字幕）—— 不要手改 */',
             '#ifndef WHITELIST_H', '#define WHITELIST_H', '']
    for i, b in enumerate(blobs):
        lines.append('static const unsigned char WL_%d[] = { %s };'
                     % (i, ', '.join('0x%02x' % x for x in b)))
    lines += ['', 'static const char *const WL_PTR[] = { %s };'
              % ', '.join('(const char *)WL_%d' % i for i in range(len(blobs))),
              'static const unsigned int WL_LEN[] = { %s };'
              % ', '.join(str(len(b)) for b in blobs),
              '#define WL_COUNT %d' % len(blobs), '', '#endif', '']
    Path(WL_H).write_text(chr(10).join(lines), encoding='utf-8')
    return len(blobs)


def tool(*names):
    env = os.environ.get('MINGW32_BIN')
    for name in names:
        cands = []
        if env:
            cands.append(Path(env) / name)
        cands.append(ROOT / 'tmp' / 'toolchain' / 'root' / 'mingw32' / 'bin' / name)
        for c in cands:
            if c.exists():
                return c
    raise SystemExit('[中止] 找不到 %s；设 MINGW32_BIN 或解压工具链到 tmp/toolchain/' % (names[0],))


def run(cmd, what):
    env = dict(os.environ)
    env['PATH'] = str(Path(cmd[0]).parent) + os.pathsep + env.get('PATH', '')
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, env=env)
    if r.returncode != 0:
        raise SystemExit('[中止] %s 失败:\n%s\n%s' % (what, r.stdout, r.stderr))
    return r


def load_zh():
    """→ {字符串 ID: 中文}（`resource/AdvHDLang_zh.json`）。"""
    data = json.loads(ZH_JSON.read_text(encoding='utf-8'))
    return {int(k): v for k, v in data['strings'].items()}


def rc_escape(s):
    out = []
    for ch in s:
        if ch == '"':
            out.append('\\"')
        elif ch == '\\':
            out.append('\\\\')
        elif ch == '\n':
            out.append('\\n')
        elif ch == '\r':
            out.append('\\r')
        elif ch == '\t':
            out.append('\\t')
        else:
            out.append(ch)
    return ''.join(out)


def gen_rc(zh, manifest, rc_path, man_path):
    """生成 `.rc`：**只有一块 LANG_CHINESE(2052) 串表** + manifest。

    中文直接写在 `.rc` 里 ⇒ 用 `#pragma code_page(65001)` 声明文件是 UTF-8，
    并给 windres 传 `--codepage=65001`（两者同时给：pragma 管解析，选项管默认输入码页）。
    """
    lines = ['#pragma code_page(65001)', '#include <winres.h>', '']
    # **必须写成 SUBLANG_CHINESE_SIMPLIFIED**：`LANG_CHINESE, 1` 是 SUBLANG_CHINESE_TRADITIONAL（1028），
    # 官中用 2052 = `LANG_CHINESE, 2`（简中）。
    lines.append('LANGUAGE LANG_CHINESE, SUBLANG_CHINESE_SIMPLIFIED')
    lines.append('STRINGTABLE')
    lines.append('BEGIN')
    for sid in sorted(zh):
        lines.append('  %d "%s"' % (sid, rc_escape(zh[sid])))
    lines.append('END')
    lines.append('')
    if manifest:
        man_path.write_bytes(manifest)
        lines.append('LANGUAGE LANG_ENGLISH, 1')
        lines.append('2 24 "%s"' % man_path.name)
    rc_path.write_text(chr(10).join(lines), encoding='utf-8')
    return rc_path


def verify(dll_path, zh):
    data = Path(dll_path).read_bytes()
    bad = []
    if not peres.pe_machine_is_i386(dll_path):
        bad.append('不是 i386（machine != 0x14c）')
    if not peres.is_dll(dll_path):
        bad.append('缺少 IMAGE_FILE_DLL 标志')

    # ① 资源键集合：只允许 2052 的 RT_STRING 块 + manifest（多一块 1033/1041 都会让
    #    “选了中文却看到日文/英文界面”）
    got = read_resources(dll_path)
    blocks = sorted({sid // 16 + 1 for sid in zh})
    want_keys = sorted([(RT_STRING, b, LANG_ZH) for b in blocks] + [(RT_MANIFEST, 2, 1033)])
    if sorted(got) != want_keys:
        bad.append('资源键集合不对：%r vs %r' % (sorted(got), want_keys))

    # ② 表必须覆盖原件（英文 1033）的全部字符串 ID -- 漏一条就是界面上空白一项
    orig_ids = sorted({sid for d in extract_strings(read_resources(ORIG_DLL))[0].values()
                       for sid in d})
    if sorted(zh) != orig_ids:
        bad.append('表 ID 集合与原件不同：缺 %r / 多 %r'
                   % (sorted(set(orig_ids) - set(zh)), sorted(set(zh) - set(orig_ids))))

    # ③ 写进去的串表 + `LoadStringW` 实测都得逐条等于表值（后者是“资源树真能读”的唯一凭据）
    parsed = extract_strings(got)[0]
    if set(parsed) != {LANG_ZH}:
        bad.append('串表语言不是单一 %d：%r' % (LANG_ZH, sorted(parsed)))
    else:
        diff = [sid for sid in sorted(zh) if parsed[LANG_ZH].get(sid) != zh[sid]]
        if diff:
            bad.append('串表内容与表不符（%d 项，首项 %d）' % (len(diff), diff[0]))

    ids = sorted(zh)
    real = peres.load_strings(dll_path, ids)
    if real is None:
        bad.append('LoadLibraryExW(DATAFILE) 失败')
    else:
        diff = [sid for sid in ids if real.get(sid) != zh[sid]]
        if diff:
            bad.append('LoadStringW 与表不符（%d 项，首项 %d：%r≠%r）'
                       % (len(diff), diff[0], real.get(diff[0]), zh[diff[0]]))
    return bad, {'size': len(data), 'resources': len(got), 'ids': len(ids)}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--check', action='store_true')
    ap.add_argument('--debug', action='store_true', help='诊断版：写全量日志（默认是静默发布版）')
    args = ap.parse_args(argv)


    wl = whitelist_from_pack()
    print('[白名单] 语言包字幕非 ASCII 项 %d 条（兜底用）' % gen_whitelist(wl))
    BUILD.mkdir(parents=True, exist_ok=True)
    zh = load_zh()
    manifest = extract_strings(read_resources(ORIG_DLL))[1]
    print('[串表] %s：%d 条中文（2052）/ manifest %d 字节'
          % (ZH_JSON.name, len(zh), len(manifest or b'')))
    rc = gen_rc(zh, manifest, BUILD / 'app.rc', BUILD / 'app.manifest')
    res_o = BUILD / 'app_res.o'
    run([tool('windres.exe', 'i686-w64-mingw32-windres.exe'), '--codepage=65001',
         str(rc), '-O', 'coff', '-o', str(res_o)], 'windres')
    print('[资源] 编译 %s（%d 字节）' % (res_o.name, res_o.stat().st_size))

    hook_dll = BUILD / 'AdvHDLang.hook.dll'
    dbg = [] if args.debug else ['-DADVHDHOOK_SILENT']
    # `--no-insert-timestamp`：不写 PE 头的时间戳 ⇒ 同一份输入重复构建**字节一致**
    # （否则流水线“复跑 0 处改动”会每次都报这个文件变了）
    run([tool('i686-w64-mingw32-gcc.exe'), '-O2', '-fno-omit-frame-pointer', '-s', '-shared',
         '-Wall', '-Wextra'] + dbg + [
         '-Wl,--no-insert-timestamp',
         '-o', str(hook_dll), str(HOOK_C), str(res_o), '-lkernel32'], '编译 DLL')
    print('[编译] %s（%d 字节）' % (hook_dll.name, hook_dll.stat().st_size))

    bad, info = verify(hook_dll, zh)
    print('[校验] 大小 %d，资源 %d 项，LoadStringW 逐条比对 %d 个 id'
          % (info['size'], info['resources'], info['ids']))
    if bad:
        for m in bad:
            print('[FAIL] %s' % m)
        raise SystemExit(1)
    print('[校验] i386 / DLL 标志 / 资源树（单一 2052）/ LoadStringW 逐条一致 OK')
    if args.check:
        print('[检查] 未写盘')
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_bytes(hook_dll.read_bytes())
    print('[写盘] %s' % OUT)
    return 0


if __name__ == '__main__':
    sys.exit(main())
