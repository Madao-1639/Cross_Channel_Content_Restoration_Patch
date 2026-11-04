# -*- coding: utf-8 -*-
"""PE 资源树的最小只读解析（**只服务 `AdvHDLang.dll` 这一类纯资源/资源+hook 的 DLL**）。

为什么需要它：语言包 DLL 的可行性全靠"资源树能被 `LoadStringW` 正常读"这一点 ——
手工把 `.rsrc` 节搬到别的 RVA 会被 `LoadStringW` 判为非法（实测访问违例 → 设置界面文字全空，
见 `doc/lessons-learned.md`）。所以构建期与验收期都要能**不启动游戏**就把产物拆开对账。

提供：
  * `read_resources(path)` → `{(type, name, lang): 字节}`
  * `extract_strings(res)` → `({lang: {字符串ID: 文本}}, manifest 字节)`
  * `load_strings(path, ids)` → `LoadLibraryExW(AS_DATAFILE)` + `LoadStringW` 实测值（**仅 Windows**）

字符串 ID 约定（实测）：`RT_STRING` 块 `name` 覆盖 `(name-1)*16 … name*16-1`，块内按序编号。
"""

import ctypes
import struct
from pathlib import Path

RT_STRING, RT_MANIFEST = 6, 24


def _pe(d):
    pe = struct.unpack_from('<I', d, 0x3c)[0]
    nsec = struct.unpack_from('<H', d, pe + 6)[0]
    opt = pe + 24
    dd = opt + (96 if struct.unpack_from('<H', d, opt)[0] == 0x10b else 112)
    so = opt + struct.unpack_from('<H', d, pe + 20)[0]
    secs = []
    for i in range(nsec):
        _n, vs, va, rs, ra = struct.unpack_from('<8sIIII', d, so + i * 40)
        secs.append((va, max(vs, rs), ra))
    return {'pe': pe, 'nsec': nsec, 'opt': opt, 'dd': dd, 'so': so, 'secs': secs}


def read_resources(path):
    """→ {(type, name, lang): 字节}（纯资源 DLL 的偏移是节内相对，按此约定解析）。"""
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
    """→ ({资源语言ID: {字符串ID: 文本}}, manifest 字节)。空串不返回。"""
    per_lang, manifest = {}, None
    for (typ, name, lang), data in res.items():
        if typ == RT_STRING:
            pos, k = 0, 1
            while pos + 2 <= len(data):
                ln = struct.unpack_from('<H', data, pos)[0]
                pos += 2
                s = data[pos:pos + ln * 2].decode('utf-16-le')
                pos += ln * 2
                sid = (name - 1) * 16 + k - 1
                k += 1
                if s:
                    per_lang.setdefault(lang, {})[sid] = s
        elif typ == RT_MANIFEST:
            manifest = data
    return per_lang, manifest


def pe_machine_is_i386(path):
    d = Path(path).read_bytes()
    info = _pe(d)
    return struct.unpack_from('<H', d, info['pe'] + 4)[0] == 0x14c


def is_dll(path):
    d = Path(path).read_bytes()
    info = _pe(d)
    return bool(struct.unpack_from('<H', d, info['pe'] + 22)[0] & 0x2000)


def load_strings(path, ids):
    """用 `LoadLibraryExW(AS_DATAFILE)` + `LoadStringW` 实测每个 ID 的文本（**仅 Windows**）。

    → {id: 文本或 None}；`python` 不能加载时返回 None。
    """
    k32 = ctypes.WinDLL('kernel32', use_last_error=True)
    u32 = ctypes.WinDLL('user32')
    k32.LoadLibraryExW.restype = ctypes.c_void_p
    u32.LoadStringW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_wchar_p, ctypes.c_int]
    u32.LoadStringW.restype = ctypes.c_int
    h = k32.LoadLibraryExW(str(Path(path).resolve()), None, 0x2)   # LOAD_LIBRARY_AS_DATAFILE
    if not h:
        return None
    try:
        out = {}
        for sid in ids:
            buf = ctypes.create_unicode_buffer(2048)
            n = u32.LoadStringW(ctypes.c_void_p(h), sid, buf, 2048)
            out[sid] = buf.value if n else None
        return out
    finally:
        k32.FreeLibrary(ctypes.c_void_p(h))
