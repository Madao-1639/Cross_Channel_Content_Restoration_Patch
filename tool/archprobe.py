# -*- coding: utf-8 -*-
"""归档环境探针：问「运行时到底有哪些资源可用」。

给转换器当「不存在就不发射」的防御依据（蒙版、立绘、PNA 层数）。两个函数原在
`script/splice_restoration.py` 里，因 `script/build_original_stage.py` 也要用而被抽出 ——
免得一个流水线步骤去 import 另一个流水线步骤。
"""
import struct
from pathlib import Path

from tool import arcbuild
from tool.rename_map import ARCHIVES

ROOT = Path(__file__).resolve().parent.parent


def load_inventory():
    """运行时可用资源名集合（大写）。用于「不存在就不发射」的防御（蒙版）。"""
    inv = set()
    for p in ARCHIVES:
        q = ROOT / p
        if q.exists():
            inv |= {n.decode('utf-16-le').upper() for n, _ in arcbuild.read_raw(q)}
    return inv


def load_pna_layers():
    """{PNA 名(大写): 层数}。决定 `39` 的帧号形态（见 wsc2ws2.PORTRAIT_ATTRS_BY_LAYERS）。

    不查层数而照抄「4 层形态」，对 1 层 PNA 就是**子图层越位**（别的游戏补丁踩过的坑）。
    """
    out = {}
    for p in ARCHIVES:
        q = ROOT / p
        if not q.exists():
            continue
        for n, d in arcbuild.read_raw(q):
            name = n.decode('utf-16-le').upper()
            if name.endswith('.PNA') and d[:4] == b'PNAP':
                out.setdefault(name, struct.unpack_from('<I', d, 0x10)[0])
    return out
