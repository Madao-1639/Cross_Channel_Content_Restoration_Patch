# -*- coding: utf-8 -*-
"""编译语言包里的 `AdvHDLang.dll`（`MultiByteToWideChar` 位置门 hook + 原样资源）。

流程：
  1. （无需白名单：hook 只按位置判定，见 `tool/hook/advhdlang_hook.c`）
  2. 从 `resource/AdvHDLang.orig.dll` 解析资源（RT_STRING 各 id 的英/日串 + manifest），
     生成 `app.rc` + `app.manifest`，用 **windres** 编成 `.res`（链接器生成**合法**资源树；
     手工搬 `.rsrc` 会被 `LoadStringW` 判为非法 → 设置界面文字全空）；
  3. 用 32 位 MinGW 编译并链接 hook + 资源 → `asset/zh-CN/AdvHDLang.dll`；
  4. 校验：i386 / DLL 标志 / 资源键集合与原件一致 / **`LoadStringW` 实测**与原件同值。

工具链：`tmp/toolchain/root/mingw32/bin/`（可用 `MINGW32_BIN` 覆盖）。

用法：`python script/build_lang_dll.py [--check]`
"""
import argparse
import ctypes
import json
import os
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tool import arcbuild, layers, ws2, ws2disasm  # noqa: E402

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

ORIG_DLL = ROOT / 'resource' / 'AdvHDLang.orig.dll'
HOOK_C = ROOT / 'tool' / 'hook' / 'advhdlang_hook.c'
OUT = ROOT / 'asset' / 'zh-CN' / 'AdvHDLang.dll'
WL_H = ROOT / 'tool' / 'hook' / 'whitelist.h'
BUILD = ROOT / 'tmp' / 'build_dll'

RT_STRING, RT_MANIFEST = 6, 24


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


def _pe(d):
    pe = struct.unpack_from('<I', d, 0x3c)[0]
    nsec = struct.unpack_from('<H', d, pe + 6)[0]
    opt = pe + 24
    dd = opt + (96 if struct.unpack_from('<H', d, opt)[0] == 0x10b else 112)
    so = opt + struct.unpack_from('<H', d, pe + 20)[0]
    secs = []
    for i in range(nsec):
        n, vs, va, rs, ra = struct.unpack_from('<8sIIII', d, so + i * 40)
        secs.append((va, max(vs, rs), ra))
    return {'pe': pe, 'nsec': nsec, 'opt': opt, 'dd': dd, 'so': so, 'secs': secs}


def read_resources(path):
    """→ {(type, name, lang): 字节}（本 DLL 的资源偏移是节内相对，按此约定解析）。"""
    d = Path(path).read_bytes()
    info = _pe(d)
    res_rva, _sz = struct.unpack_from('<II', d, info['dd'] + 16)

    def r2o(rva):
        for va, sz, ra in info['secs']:
            if va <= rva < va + sz:
                return ra + (rva - va)
        raise ValueError('rva 0x%x 不在任何节' % rva)

    out = {}

    def walk(off, path):
        nname, nid = struct.unpack_from('<HH', d, off + 12)
        for i in range(nname + nid):
            nm, tgt = struct.unpack_from('<II', d, off + 16 + i * 8)
            label = (nm & 0xFFFF) if not (nm & 0x80000000) else 'named'
            if tgt & 0x80000000:
                walk(r2o(res_rva + (tgt & 0x7FFFFFFF)), path + (label,))
            else:
                drva, dsz = struct.unpack_from('<II', d, r2o(res_rva + tgt))
                o = r2o(drva)
                out[path + (label,)] = d[o:o + dsz]
    walk(r2o(res_rva), ())
    return out


def extract_strings(res):
    per_lang, manifest = {}, None
    for (typ, name, lang), data in res.items():
        if typ == RT_STRING:
            block = name
            pos, k = 0, 1
            while pos + 2 <= len(data):
                ln = struct.unpack_from('<H', data, pos)[0]
                pos += 2
                s = data[pos:pos + ln * 2].decode('utf-16-le')
                pos += ln * 2
                sid = (block - 1) * 16 + k - 1     # 实测：块覆盖 (block-1)*16 .. block*16-1
                k += 1
                if s:
                    per_lang.setdefault(lang, {})[sid] = s
        elif typ == RT_MANIFEST:
            manifest = data
    return per_lang, manifest


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


def gen_rc(per_lang, manifest, rc_path, man_path):
    lines = ['#include <winres.h>', '']
    for lang in sorted(per_lang):
        name = {1033: 'LANG_ENGLISH', 1041: 'LANG_JAPANESE'}.get(lang)
        lines.append('LANGUAGE %s, 1' % name if name else 'LANGUAGE %d, 1' % lang)
        lines.append('STRINGTABLE')
        lines.append('BEGIN')
        for sid in sorted(per_lang[lang]):
            lines.append('  %d "%s"' % (sid, rc_escape(per_lang[lang][sid])))
        lines.append('END')
        lines.append('')
    if manifest:
        man_path.write_bytes(manifest)
        lines.append('LANGUAGE LANG_ENGLISH, 1')
        lines.append('2 24 "%s"' % man_path.name)
    rc_path.write_text(chr(10).join(lines), encoding='utf-8')
    return rc_path


def verify(dll_path):
    data = Path(dll_path).read_bytes()
    info = _pe(data)
    machine = struct.unpack_from('<H', data, info['pe'] + 4)[0]
    chars = struct.unpack_from('<H', data, info['pe'] + 22)[0]
    bad = []
    if machine != 0x14c:
        bad.append('machine=0x%04x 不是 i386' % machine)
    if not (chars & 0x2000):
        bad.append('缺少 IMAGE_FILE_DLL 标志')

    got = read_resources(dll_path)
    want = read_resources(ORIG_DLL)
    if sorted(got) != sorted(want):
        bad.append('资源键集合不同：%r vs %r' % (sorted(got), sorted(want)))

    k32 = ctypes.WinDLL('kernel32', use_last_error=True)
    u32 = ctypes.WinDLL('user32')
    k32.LoadLibraryExW.restype = ctypes.c_void_p
    u32.LoadStringW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_wchar_p, ctypes.c_int]
    u32.LoadStringW.restype = ctypes.c_int

    ids = sorted({sid for d in extract_strings(want)[0].values() for sid in d})[:24]

    def load_strings(path):
        h = k32.LoadLibraryExW(str(Path(path).resolve()), None, 0x2)   # AS_DATAFILE
        if not h:
            return None
        try:
            out = {}
            for sid in ids:
                buf = ctypes.create_unicode_buffer(512)
                n = u32.LoadStringW(ctypes.c_void_p(h), sid, buf, 512)
                out[sid] = buf.value if n else None
            return out
        finally:
            k32.FreeLibrary(ctypes.c_void_p(h))

    a = load_strings(ORIG_DLL)
    b = load_strings(dll_path)
    if b is None:
        bad.append('LoadLibraryExW(DATAFILE) 失败')
    elif a != b:
        diff = [k for k in ids if a.get(k) != b.get(k)]
        bad.append('LoadStringW 与原件不同（%d 项）' % len(diff))
    return bad, {'size': len(data), 'resources': len(got), 'ids': len(ids)}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--check', action='store_true')
    ap.add_argument('--debug', action='store_true', help='诊断版：写全量日志（默认是静默发布版）')
    args = ap.parse_args(argv)


    wl = whitelist_from_pack()
    print('[白名单] 语言包字幕非 ASCII 项 %d 条（兜底用）' % gen_whitelist(wl))
    BUILD.mkdir(parents=True, exist_ok=True)
    res = read_resources(ORIG_DLL)
    per_lang, manifest = extract_strings(res)
    print('[资源] 原件解析：%d 种语言 / %d 个字符串 / manifest %d 字节'
          % (len(per_lang), sum(len(v) for v in per_lang.values()), len(manifest or b'')))
    rc = gen_rc(per_lang, manifest, BUILD / 'app.rc', BUILD / 'app.manifest')
    res_o = BUILD / 'app_res.o'
    run([tool('windres.exe', 'i686-w64-mingw32-windres.exe'), str(rc), '-O', 'coff', '-o', str(res_o)],
        'windres')
    print('[资源] 编译 %s（%d 字节）' % (res_o.name, res_o.stat().st_size))

    hook_dll = BUILD / 'AdvHDLang.hook.dll'
    dbg = [] if args.debug else ['-DADVHDHOOK_SILENT']
    run([tool('i686-w64-mingw32-gcc.exe'), '-O2', '-fno-omit-frame-pointer', '-s', '-shared',
         '-Wall', '-Wextra'] + dbg + [
         '-o', str(hook_dll), str(HOOK_C), str(res_o), '-lkernel32'], '编译 DLL')
    print('[编译] %s（%d 字节）' % (hook_dll.name, hook_dll.stat().st_size))

    bad, info = verify(hook_dll)
    print('[校验] 大小 %d，资源 %d 项，LoadStringW 抽查 %d 个 id'
          % (info['size'], info['resources'], info['ids']))
    if bad:
        for m in bad:
            print('[FAIL] %s' % m)
        raise SystemExit(1)
    print('[校验] i386 / DLL 标志 / 资源树 / LoadStringW 全部与原件一致 OK')
    if args.check:
        print('[检查] 未写盘')
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_bytes(hook_dll.read_bytes())
    print('[写盘] %s' % OUT)
    return 0


if __name__ == '__main__':
    sys.exit(main())
