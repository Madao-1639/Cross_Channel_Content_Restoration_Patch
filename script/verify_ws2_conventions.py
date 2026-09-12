"""WS2/转换器 约定回归检查（只读语料，不碰 asset/）。

把「指令粒度」的约定固定成可重复运行的检查项，防止转换器/反汇编器再漂移。
每一项都对应一条经全语料实证的结论，见 doc/wsc_to_ws2_conversion.md。

覆盖的错误形态（历史上都真出现过）：
  * `28`/`1e` 的尾段长度写错 → 引擎少读若干字节、把后续指令吃进参数
  * 立绘槽名多写一个 0x73 → 产出 "sst01"（引擎按单串解析，认不出）
  * 背景渐变 `65` 的 a 字节写成 0x64（那是 `66` 蒙版自己的尾巴）
  * 每条对话后补两条 `15 00 00` → 造出原生不存在的三连 `15`
  * 选项 strid 写成 0 基表内序号 → 与字符串池（= `14` 的 id）错位

用法（项目根目录）：
    python script/verify_ws2_conventions.py
"""
import collections
import glob
import io
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tool import arcbuild, ws2, ws2disasm, wsc                      # noqa: E402
from tool.wsc2ws2 import VOICE_TAIL, SE_TAIL, ConvertOptions, convert  # noqa: E402

if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

WSC_DIR = ROOT / 'tmp' / 'corpus' / 'wsc'
WS2_DIR = ROOT / 'tmp' / 'corpus' / 'ws2'
RIO = ROOT / 'asset' / 'Rio.arc'

# 原生 `28` 尾段的模态形态（1086/1556），也是转换器应当发射的形态
SE_TAIL_NATIVE = bytes(10) + b'\x0a\x00' + bytes(5) + b'\x01' + bytes(4)
# 原生 `1e` 尾段恒定部分（音量字段除外）
BGM_TAIL_NATIVE = bytes(8) + b'\xff\xff' + b'\x0a\x00\x01' + bytes(4)

_results = []


def check(name, ok, detail=''):
    _results.append((name, ok, detail))
    print('  [%s] %-46s %s' % ('PASS' if ok else 'FAIL', name, detail))


def cstr(d, p, n=200):
    e = d.find(b'\x00', p, p + n)
    return (d[p:e], e + 1) if e >= 0 else (None, None)


def runs_of_15(d):
    """统计 "15 00 00" 的连跑长度分布。"""
    c = collections.Counter()
    i = 0
    while i < len(d) - 2:
        if d[i:i + 3] == b'\x15\x00\x00':
            k, j = 1, i + 3
            while d[j:j + 3] == b'\x15\x00\x00':
                k += 1
                j += 3
            c[k] += 1
            i = j
        else:
            i += 1
    return c


def native_scripts():
    return [(os.path.basename(f), open(f, 'rb').read())
            for f in sorted(glob.glob(str(WS2_DIR / '*.ws2')))]


def shipped_cnr():
    if not RIO.exists():
        return []
    members = {n.decode('utf-16-le'): v for n, v in arcbuild.read_raw(RIO)}
    return [(k, ws2.decode(v)) for k, v in members.items()
            if k.upper().startswith('CNR') and k.lower().endswith('.ws2')]


def main():
    native = native_scripts()
    if not native:
        print('tmp/corpus/ws2 缺失；语料相关检查跳过')
    print('原生语料 %d 个脚本' % len(native))
    # 反汇编只做一次（每个脚本被多项检查用到）
    disasm = []
    for name, d in native + shipped_cnr():
        try:
            disasm.append((name, d, ws2disasm.disassemble(d)))
        except Exception as e:
            disasm.append((name, d, None))
            print('  反汇编失败 %s: %s' % (name, str(e)[:60]))
    native_dis = disasm[:len(native)]

    # ---- 1. 反汇编器 100% 覆盖 + 尾段结构 ----
    n28 = n1e = 0
    bad = []
    for name, d, ins in disasm:
        if ins is None:
            bad.append('%s: 反汇编失败' % name)
            continue
        if sum(i.size for i in ins) != len(d):
            bad.append('%s: coverage' % name)
        for i in ins:
            if i.opcode == 0x28:
                n28 += 1
                slot, e1 = cstr(d, i.offset + 1)
                _, e2 = cstr(d, e1)
                t = d[e2:e2 + 22]
                if len(t) < 22 or t[11] or t[12] or t[14:17] != bytes(3) \
                        or t[18:22] != bytes(4) or (t[13], t[17]) not in ((0, 1), (101, 0)):
                    bad.append('%s: 0x28 tail %s' % (name, t.hex()))
            elif i.opcode == 0x1e:
                n1e += 1
                slot, e1 = cstr(d, i.offset + 1)
                _, e2 = cstr(d, e1)
                t = d[e2:e2 + 17]
                if len(t) < 17 or t[4:17] != BGM_TAIL_NATIVE[4:17]:
                    bad.append('%s: 0x1e tail %s' % (name, t.hex()))
    check('反汇编 100% 覆盖 + 28/1e 尾段结构', not bad,
          '%d 文件, 0x28=%d, 0x1e=%d%s' % (len(disasm), n28, n1e,
                                           '' if not bad else ' | ' + '; '.join(bad[:3])))

    # ---- 2. 发射端模板与原生一致 ----
    check('SE_TAIL 与原生模态形态一致(22B)', SE_TAIL == SE_TAIL_NATIVE, SE_TAIL.hex())
    check('VOICE_TAIL 22B', len(VOICE_TAIL) == 22 and VOICE_TAIL[:10] == bytes(10), '')

    # ---- 3. 立绘槽名是**一个** NUL 串（不是 tag+串） ----
    # 0x34 的首字节是 tag：0x73('s') 是立绘槽（整串 "stNN"），另有 0x40('@') 的
    # 动画形态。槽名字段在反汇编器里被拆成 tag + 余下，所以这里按整串检查。
    slots = collections.Counter()
    bad_slot = []
    for name, d, ins in native_dis:
        if ins is None:
            continue
        for i in ins:
            if i.opcode == 0x34:
                s, _ = cstr(d, i.offset + 1, 20)
                slots[s] += 1
                if i.fields['tag'] == 0x73 and not s.startswith(b'st'):
                    bad_slot.append((name, s))
    check('0x34 立绘槽名(tag=0x73)形如 stNN', not bad_slot,
          '%d 种, 例: %s%s' % (len(slots), [s.decode('ascii', '?') for s in list(slots)[:3]],
                              '' if not bad_slot else ' | ' + str(bad_slot[:3])))

    # ---- 4. 背景块 65 的 a 字节恒为 0 ----
    a65 = collections.Counter()
    for name, d, ins in native_dis:
        if ins is None:
            continue
        for i in ins:
            if i.opcode == 0x65:
                a65[i.fields['a']] += 1
    check('0x65 首操作数字节恒为 0x00', set(a65) == {0},
          {'0x%02x' % k: v for k, v in a65.most_common(3)})

    # ---- 5. 原生 15 连跑只有 1/2 ----
    rn = collections.Counter()
    for name, d in native:
        rn += runs_of_15(d)
    check('原生 "15 00 00" 连跑 ⊆ {1,2}', set(rn) <= {1, 2}, dict(sorted(rn.items())))

    # ---- 6. 转换器：324 个 WSC 全量往返 ----
    if not WSC_DIR.exists():
        print('tmp/corpus/wsc 缺失；转换检查跳过')
    else:
        opts = ConvertOptions()
        conv_bad = []
        n_choice_ok = n_choice = 0
        n_id_ok = 0
        n_files = 0
        for p in sorted(glob.glob(str(WSC_DIR / '*.WSC'))):
            stem = os.path.basename(p)[:-4]
            raw = open(p, 'rb').read()
            winstr = wsc.disassemble(raw)
            try:
                out, _ = convert(raw, stem, opts)
                oins = ws2disasm.disassemble(out)
            except Exception as e:
                conv_bad.append('%s: %s' % (stem, str(e)[:60]))
                continue
            n_files += 1
            if sum(i.size for i in oins) != len(out):
                conv_bad.append('%s: coverage' % stem)
            # 对话文本逐字节一致（**按文件顺序**收集，0x41/0x42 交错出现）
            wd = []
            for i in winstr:
                if i.opcode == 0x41:
                    wd.append((i.fields['id'], i.operands[4:-1]))
                elif i.opcode == 0x42:
                    wd.append((i.fields['id'],
                               i.operands[i.operands.index(0, 5) + 1:-1]))
            gd = [(i.fields['id'], i.operands[i.operands.index(0, 3) + 1:-2])
                  for i in oins if i.opcode == 0x14]
            if wd != gd:
                conv_bad.append('%s: dialogue text/id mismatch' % stem)
            else:
                n_id_ok += 1
            want = [[it['id'] for it in i.fields['items']]
                    for i in winstr if i.opcode == 0x02]
            got = [[e['strid'] for e in i.fields['entries']]
                   for i in oins if i.opcode == 0x0f]
            if want:
                n_choice += 1
                if want == got:
                    n_choice_ok += 1
                else:
                    conv_bad.append('%s: choice strid %s != %s' % (stem, got[:2], want[:2]))
            r = runs_of_15(out)
            if set(r) - {1, 2}:
                conv_bad.append('%s: 15-run %s' % (stem, dict(r)))
            for i in oins:
                if i.opcode == 0x34:
                    s, _ = cstr(out, i.offset + 1, 20)
                    if not s.startswith(b'st'):
                        conv_bad.append('%s: 0x34 slot %r' % (stem, s))
                        break
        check('转换 324 个 WSC：文本/id/槽名/15 连跑', not conv_bad,
              '%d 文件, 对话 id+文本一致 %d, 选项表 %d/%d%s'
              % (n_files, n_id_ok, n_choice_ok, n_choice,
                 '' if not conv_bad else ' | ' + '; '.join(conv_bad[:3])))

    n_bad = sum(1 for _, ok, _ in _results if not ok)
    print()
    print('%d checks passed, %d failed' % (len(_results) - n_bad, n_bad))
    return 1 if n_bad else 0


if __name__ == '__main__':
    sys.exit(main())
