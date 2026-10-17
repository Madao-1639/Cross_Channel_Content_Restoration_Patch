# -*- coding: utf-8 -*-
"""独立构建 Fonts.arc —— 完全基于 resource/fonts/ 下的资源，不依赖任何先前归档产物。

产出 asset/Fonts.arc，仅包含两个成员：
  FOT-MatissePro-M.PTF ← resource/fonts/sourcehan_subset/SourceHanSansSC-Medium.ttf
                          （思源黑体 Medium 子集，TrueType glyf）
  FOT-MatissePro-B.PTF ← resource/fonts/sourcehan_subset/SourceHanSansSC-Bold.ttf
                          （思源黑体 Bold 子集，TrueType glyf，人名框用）

PTF 打包采用 **纯字面量 LZSS**（不做 match token），实现统一来自 `tool/ptf.py`：
  头 4 字节 = 明文长度；之后按 8 字节一组，每组前 1 字节 flag=0xFF 表示
  8 个 literal；全部字节先 XOR 0x2C。与引擎 sub_4591E0 的解压逻辑完全
  对应（ring 写指针 esi=1、flag 从 bit0 消费、XOR 0x2C）。
  压缩率固定 ~112.5%，无 match 坐标错位风险。
  双端回读 + `arcbuild.verify()` 两道闸。
"""
import os
import struct
import sys

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tool import arcbuild, ptf as ptf_mod  # noqa: E402
from tool.arcbuild import write_arc, read_raw  # noqa: E402

RESOURCE_ROOT = 'resource/fonts'
MEMBERS = [
    # (arc 成员名, 源文件, 期望文件头 magic)
    ('FOT-MatissePro-M.PTF',
     os.path.join(RESOURCE_ROOT, 'sourcehan_subset', 'SourceHanSansSC-Medium.ttf'),
     b'\x00\x01\x00\x00'),   # TrueType(glyf) 思源黑体 Medium 子集
    ('FOT-MatissePro-B.PTF',
     os.path.join(RESOURCE_ROOT, 'sourcehan_subset', 'SourceHanSansSC-Bold.ttf'),
     b'\x00\x01\x00\x00'),   # TrueType(glyf) 思源黑体 Bold 子集（人名框）
]


def main():
    out_path = os.path.join('asset', 'Fonts.arc')
    members = []
    for arc_name, src, magic in MEMBERS:
        if not os.path.exists(src):
            raise SystemExit('missing source: %s' % src)
        with open(src, 'rb') as fh:
            data = fh.read()
        if data[:4] != magic:
            raise AssertionError('font magic mismatch for %s (head=%s, expect=%s)' % (src, data[:4], magic))
        blob = struct.pack('<I', len(data)) + ptf_mod.lzss_encode_literal(data)
        if ptf_mod.unpack_ptf(blob) != data:
            raise AssertionError('PTF round-trip failed for %s' % src)
        print('%-24s 源 %10d -> PTF %10d bytes（回读 OK）' % (arc_name, len(data), len(blob)))
        members.append((arc_name.encode('utf-16-le'), blob))
    n = write_arc(members, out_path, skip_if_identical=True)
    print('written %s: %d bytes' % (out_path, n))
    # 回读校验（arcbuild.verify 检查每个 member 的边界合法）
    arcbuild.verify(out_path, expect_count=len(members))
    print('arcbuild.verify() OK')


if __name__ == '__main__':
    main()
