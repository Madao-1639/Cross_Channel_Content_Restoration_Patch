"""最小 TTF/OTF name 表读取（纯标准库，**不依赖 fontTools**）。

用途：验收里核对「我们打包进 `Fonts.arc` 的字体」与「Steam 原版字体」的**族名是否一致**。
这条不变量是硬要求（见 `doc/localization.md`「字体族名必须与原版一致」）：

    引擎选字体 = Lua 的 `g_fonts.name`（族名）→ GDI 按族名找面
    ⇒ 若我们的字体报的是思源族名、而 Lua 要的是原版族名（或反过来），GDI 找不到面
      就会掉到**兜底字体**（实测事故：英文模式下字体变兜底）。

只解析 name 表（id1 族名 / id2 子族 / id4 全名 / id6 PostScript 名 / id16 排版族名 /
id17 排版子族），按 (platformID, langID) 分组返回。
"""
import struct


class TtfError(ValueError):
    pass


def _tables(blob):
    if len(blob) < 12:
        raise TtfError('文件过短')
    tag = blob[:4]
    if tag == b'ttcf':
        raise TtfError('TrueType Collection（TTC）不支持')
    if tag not in (b'\x00\x01\x00\x00', b'OTTO', b'true'):
        raise TtfError('不是 TTF/OTF（magic=%r）' % tag)
    numtables = struct.unpack_from('>H', blob, 4)[0]
    out = {}
    off = 12
    for _ in range(numtables):
        t, checksum, offset, length = struct.unpack_from('>4sIII', blob, off)
        off += 16
        out[t] = (offset, length)
    return out


def names(blob):
    """→ {(platformID, langID): {nameID: str}}"""
    tabs = _tables(blob)
    if b'name' not in tabs:
        raise TtfError('没有 name 表')
    base, _len = tabs[b'name']
    _fmt, count, str_off = struct.unpack_from('>HHH', blob, base)
    out = {}
    for i in range(count):
        rec = base + 6 + i * 12
        pid, eid, lid, nid, length, off = struct.unpack_from('>HHHHHH', blob, rec)
        raw = blob[base + str_off + off: base + str_off + off + length]
        try:
            if pid == 3 or (pid == 0):
                text = raw.decode('utf-16-be')
            else:
                text = raw.decode('latin-1')
        except UnicodeDecodeError:
            continue
        out.setdefault((pid, lid), {})[nid] = text
    return out


def family_key(blob, ids=(1, 2, 4, 6, 16, 17), lang=0x411):
    """取 (platformID=3, langID=lang) 的那组关键记录；没有就退回 0x409 / 第一个 3/x。"""
    all_names = names(blob)
    grouped = {k: v for k, v in all_names.items() if k[0] == 3}
    pick = grouped.get((3, lang)) or grouped.get((3, 0x409)) or (next(iter(grouped.values())) if grouped else {})
    return {i: pick.get(i) for i in ids if pick.get(i) is not None}
