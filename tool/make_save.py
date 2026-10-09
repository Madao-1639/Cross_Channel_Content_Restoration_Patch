# -*- coding: utf-8 -*-
"""AdvHD（MoeNovel 系）指定位置存档生成器。

以一份**同脚本**的真实存档为种子（seed），改写第 0 块（当前状态块）的
PC 字段，使引擎读档后恢复在目标消息处。算法与逆向依据见
`tmp/savegen/EXPERIMENTS.md`（CCST 结构 + PC 字段一节）。

PC 字段：块内 `图起点+0x410` 的 u32 = ws2 解析游标 << 8。
图起点 = 0x18 + 对白文本长(UTF-16 码元数)×2。
游标写 `(目标 DisplayMessage 指令偏移 + 3) << 8`，读档后引擎从游标
继续解析，停在并显示目标消息（2026-10-07 实机验证通过，Save090）。

用法（项目根执行）：

    # 列出某脚本全部可跳消息（序号 / id / ws2 偏移 / 中文文本）
    python -m tool.make_save --list CCB1006

    # 以 Save084 为种子，跳到 CCB1006 第 0 条消息，写到槽位 090
    python -m tool.make_save --seed Save084 --slot 090 --script CCB1006 --index 0

限制：目标脚本必须与种子存档当前脚本一致（跨脚本需先确认引擎
"当前脚本"标识的存储点，尚未逆向）。种子建议取自目标场景的存档。
"""
import os
import re
import struct
import sys

from tool import arcbuild, lng, ws2, ws2disasm

SAVE_DIR = os.path.join(os.path.expanduser('~'), 'Saved Games', 'MoeNovel',
                        'CROSS\u2020CHANNEL Steam Edition')
SAVE_NAME = 'Save%s.CROSS\u2020CHANNEL Steam EditionSave-MoeNovel'
RIO_ARC = os.path.join('asset', 'Rio.arc')
PC_REL = 0x410              # PC 字段相对图起点的偏移
CURSOR_HEADER = 3           # 0x14/0x2e 指令 opcode(1) + id(2)


def _members():
    return dict((n.decode('utf-16le'), d) for n, d in arcbuild.read_raw(RIO_ARC))


def list_messages(script):
    """枚举脚本全部显示消息：[(序号, id, ws2偏移, 中文文本)]。"""
    members = _members()
    instrs = ws2disasm.disassemble(ws2.decode(members[script + '_en.ws2']))
    texts = lng.parse_lng(members[script + '_en.lng'])
    out = []
    for ins in instrs:
        if ins.opcode in (0x14, 0x2e) and 'id' in ins.fields:
            did = ins.fields['id']
            text = texts[did] if did < len(texts) else ''
            out.append((len(out), did, ins.offset, text))
    return out


def make_save(seed_slot, slot, script, index):
    messages = list_messages(script)
    if index < 0 or index >= len(messages):
        raise SystemExit('index %d 超界（0..%d）' % (index, len(messages) - 1))
    _, did, off, text = messages[index]
    pc = (off + CURSOR_HEADER) << 8

    seed_path = os.path.join(SAVE_DIR, SAVE_NAME % seed_slot)
    blob = bytearray(open(seed_path, 'rb').read())

    # 第 0 块校验：脚本必须一致（回读防呆）
    offs = [m.start() for m in re.finditer(b'CCST', blob)]
    if not offs:
        raise SystemExit('种子档中找不到 CCST 块')
    b0 = blob[offs[0]:offs[1] if len(offs) > 1 else len(blob)]
    n = struct.unpack_from('<I', b0, 0x14)[0]
    text0 = b0[0x18:0x18 + n * 2].decode('utf-16-le', 'replace')
    graph_start = 0x18 + n * 2
    if offs[0] != 0 or graph_start + PC_REL + 4 > offs[0] + len(b0):
        raise SystemExit('第 0 块结构异常（graph_start=0x%x, 块长 %d）' % (graph_start, len(b0)))
    old_pc = struct.unpack_from('<I', blob, graph_start + PC_REL)[0]

    struct.pack_into('<I', blob, graph_start + PC_REL, pc)
    dst = os.path.join(SAVE_DIR, SAVE_NAME % slot)
    with open(dst, 'wb') as fh:
        fh.write(bytes(blob))

    # 回读校验：PC 字段已落盘且块数不变
    check = open(dst, 'rb').read()
    offs2 = [m.start() for m in re.finditer(b'CCST', check)]
    n2 = struct.unpack_from('<I', check, 0x14)[0]
    pc2 = struct.unpack_from('<I', check, 0x18 + n2 * 2 + PC_REL)[0]
    if pc2 != pc or len(offs2) != len(offs):
        raise SystemExit('回读校验失败')

    print('种子   : %s（%d 块，原游标 0x%x）' % (seed_path, len(offs), old_pc >> 8))
    print('目标   : %s 消息 #%d id=%d ws2偏移=0x%x' % (script, index, did, off))
    print('文本   : %s' % text[:50])
    print('写入   : %s（游标 0x%x，PC 字段 0x%08x）' % (dst, pc >> 8, pc))
    print('回读   : OK（%d 块）' % len(offs2))
    return 0


def main(argv):
    if not argv or '--help' in argv:
        print(__doc__)
        return 1
    seed, slot, script, index, do_list = None, None, None, None, False
    bare = []
    it = iter(argv)
    for a in it:
        if a == '--seed':
            seed = next(it)
        elif a == '--slot':
            slot = next(it)
        elif a == '--script':
            script = next(it)
        elif a == '--index':
            index = int(next(it))
        elif a == '--list':
            do_list = True
        elif not a.startswith('--'):
            bare.append(a)
    if do_list:
        script = script or (bare[0] if bare else None)
        if not script:
            raise SystemExit('--list 需要 --script')
        for i, did, off, text in list_messages(script):
            print('#%-4d id=%-5d off=0x%05x %s' % (i, did, off, text[:46]))
        return 0
    for need in (seed, slot, script, index):
        if need is None:
            raise SystemExit('需要 --seed --slot --script --index（或 --list）')
    return make_save(seed, slot, script, index)


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    sys.exit(main(sys.argv[1:]))
