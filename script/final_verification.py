"""Full acceptance verification for the built patch in asset/.

Checks: Arc integrity (no padding), call-chain completeness, resource
classification rules, resource pairing, and rebuild idempotency (SHA256
stability).
"""
import hashlib
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tool import arcbuild, lng as lngmod, ws2, ws2disasm  # noqa: E402

ASSET = Path('asset')
STEAM_RIO = {m[0].decode('utf-16le'): m[1]
             for m in arcbuild.read_raw(Path('backup/Rio.arc'))}
STEAM_IDX = {k.upper(): v for k, v in STEAM_RIO.items()}

# 就地插入接线（见 doc/call-chain.md）：源 WSC 的删除区间直接插进宿主脚本，
# **不再有 CNR### 还原脚本、不再有额外跳转**。宿主保留 Steam 原档的出口，
# 所以这里的判据就是「宿主的出口集合 == Steam 原档的出口集合」。
# 12 个宿主全部没有 `06` / `01` / `0f` 绝对偏移，插入不破坏指针；
# `CCC0000_en` 的 `01 mode=0x85` 第二出口偏移由 script/splice_restoration.py 重算。
HOSTS_INLINE = ['CCA0025C_en.ws2', 'CCB1014C_en.ws2', 'CCB2013_en.ws2', 'CCB2101_en.ws2',
                'CCC0000_en.ws2', 'CCC3027_en.ws2', 'CCC4022_en.ws2', 'CCD0022A_en.ws2',
                'CCD1001B_en.ws2', 'CCD4003A_en.ws2', 'CCD5001A_en.ws2', 'CCD5001B_en.ws2']
# 每个宿主的对话数（`14` 条数）。逐值由 script/audit_inline.py 与源 CCS 对位核对，
# 要求「恰好一次、无缺失、无重复、无倒序」。
# `script/realign_lng_to_ws2.py` 会在「CCS 有一行是 `%` 开头的特效/控制行」时插入
# `15`+`14`（把一句英文拆成两行中文）；12 个宿主里目前只有未受影响的保持原值。
INLINE_DIALOGUES = {
    'CCA0025C_en.ws2': 573, 'CCB1014C_en.ws2': 572, 'CCB2013_en.ws2': 547,
    'CCB2101_en.ws2': 237, 'CCC0000_en.ws2': 380, 'CCC3027_en.ws2': 690,
    'CCC4022_en.ws2': 615, 'CCD0022A_en.ws2': 360, 'CCD1001B_en.ws2': 905,
    'CCD4003A_en.ws2': 919, 'CCD5001A_en.ws2': 1106, 'CCD5001B_en.ws2': 352,
}

failures = []
passes = 0


def ok(msg):
    global passes
    passes += 1
    print('[OK] %s' % msg)


def fail(msg):
    failures.append(msg)
    print('[FAIL] %s' % msg)


def name_of(entry):
    return entry[0].decode('utf-16le')


def load_arc(path):
    return {name_of(m): m[1] for m in arcbuild.read_raw(path)}


def check_arc_integrity():
    print('\n== Arc integrity ==')
    # 未改动的归档不必分发：缺失即表示安装器保留玩家原文件
    # 不分发的归档（安装器保留玩家原文件）：
    #   Graphic.arc / Chip1.arc —— 与 Steam 原档逐字节相同
    #   SysGraphic.arc —— 系统界面暂不汉化，沿用 Steam 版（2026-09-11 决定）
    OPTIONAL = {'Graphic.arc', 'Chip1.arc', 'SysGraphic.arc'}
    for f in ['Rio.arc', 'Graphic.arc', 'Chip2.arc', 'Voice.arc', 'Fonts.arc',
              'Script.arc', 'SysGraphic.arc']:
        path = ASSET / f
        if not path.exists():
            if f in OPTIONAL:
                ok('%s not shipped (unchanged vs Steam; installer keeps the original)' % f)
            else:
                fail('%s missing from asset/' % f)
            continue
        try:
            count, size, end = arcbuild.verify(path)
            ok('%s verified (%d members, %d bytes, no padding)' % (f, count, size))
        except Exception as e:
            fail('%s failed verify(): %s' % (f, e))


def check_call_chain(rio):
    print('\n== Call chain ==')
    names_upper = {n.upper(): n for n in rio.keys()}

    for name, data in rio.items():
        if not name.upper().endswith('.WS2'):
            continue
        stem = name.upper()[:-4]
        steam = STEAM_IDX.get(name.upper())
        if steam is None:
            continue
        now = [i.fields.get('name') for i in ws2disasm.disassemble(ws2.decode(data))
               if i.opcode == 0x07]
        was = [i.fields.get('name') for i in ws2disasm.disassemble(ws2.decode(steam))
               if i.opcode == 0x07]
        if stem in {h.upper()[:-4] for h in HOSTS_INLINE}:
            if now != was:
                fail('%s 出口与 Steam 原档不一致：%s != %s' % (name, now, was))
            else:
                ok('%s 出口保持 Steam 原档 %s' % (name, now))
        elif now != was and stem.startswith('CNR'):
            fail('%s 是遗留的还原脚本，接线已改为就地插入' % name)

    for entry in HOSTS_INLINE:
        h_ws2 = entry.upper()
        if h_ws2 not in names_upper:
            fail('宿主脚本缺失: %s' % h_ws2)
            continue
        jumps = [i.fields.get('name') for i in
                 ws2disasm.disassemble(ws2.decode(rio[names_upper[h_ws2]])) if i.opcode == 0x07]
        for tgt in jumps:
            if (tgt + '.ws2').upper() not in names_upper:
                fail('跳转目标缺失: %s.ws2' % tgt)


def check_inline_splice(rio):
    """就地插入接线的回归守卫（doc/call-chain.md「就地插入 + 首尾合并」）。"""
    print('\n== Inline splice ==')
    idx = {k.upper(): v for k, v in rio.items()}
    for name, expect in sorted(INLINE_DIALOGUES.items()):
        data = idx.get(name.upper())
        if data is None:
            fail('script missing: %s' % name)
            continue
        ins = ws2disasm.disassemble(ws2.decode(data))
        dlgs = [i for i in ins if i.opcode == 0x14]
        ids = [i.fields['id'] for i in dlgs]
        if ids != list(range(len(ids))):
            fail('%s 对话 id 不连续（`14` 的 id = 字符串池出现序）' % name)
            continue
        if sum(i.size for i in ins) != len(ws2.decode(data)):
            fail('%s 指令解析不完整' % name)
            continue
        if len(dlgs) != expect:
            fail('%s 对话数 %d != 预期 %d' % (name, len(dlgs), expect))
            continue
        lng_name = name[:-4] + '.lng'
        lng_members = [k for k in rio if k.upper() == lng_name.upper()]
        if not lng_members:
            fail('%s 缺少配套 lng' % name)
            continue
        from tool import lng as lngmod
        entries = lngmod.parse_lng(rio[lng_members[0]])
        if len(entries) != len(dlgs):
            fail('%s lng %d 条 != 对话 %d 条' % (name, len(entries), len(dlgs)))
            continue
        ok('%s: %d 句对话 / %d 条 lng，id 连续' % (name, len(dlgs), len(entries)))

    cc = ws2disasm.disassemble(ws2.decode(idx['CCC0000_EN.WS2']))
    for i in (x for x in cc if x.opcode == 0x01 and x.fields.get('mode') == 0x85):
        tgt = next((y.fields.get('name') for y in cc if y.offset == i.fields['b']), None)
        if tgt is None:
            fail('CCC0000_en var-133 第二出口偏移 %d 未指向指令' % i.fields['b'])
        else:
            ok('CCC0000_en var-133 第二出口指向 %s' % tgt)


def check_lng_pairing(rio):
    print('\n== LNG pairing ==')
    idx = {k.upper(): v for k, v in rio.items()}
    bad = []
    for name in sorted(k for k in rio if k.upper().endswith('.WS2')):
        ins = ws2disasm.disassemble(ws2.decode(rio[name]))
        n_dlg = sum(1 for i in ins if i.opcode == 0x14)
        n_opt = sum(i.fields['count'] for i in ins if i.opcode == 0x0f)
        # lng 是位置对应的：第 N 条 ↔ 第 N 个 `14`；选项文本也占池槽位
        # （见 doc/localization.md「位置对应」）
        expect = n_dlg + n_opt
        lng_name = name[:-4] + '.lng'
        data = idx.get(lng_name.upper())
        if expect == 0:
            if data is not None:
                bad.append('%s 无文本却有 lng' % name)
            continue
        if data is None:
            bad.append('%s 缺配套 lng' % name)
            continue
        n = len(lngmod.parse_lng(data))
        if n != expect:
            bad.append('%s lng %d 条 != 对话 %d + 选项 %d' % (name, n, n_dlg, n_opt))
    # 就地插入的 12 个宿主是本项目产出的，必须严格通过；
    # 其余是 Res303 遗留的已知债务（已确定要重开语义复审），只报告不判失败。
    host_bad = [b for b in bad if b.split()[0].upper() in
                {h.upper() for h in HOSTS_INLINE}]
    debt = [b for b in bad if b not in host_bad]
    if host_bad:
        fail('本项目产出的宿主 lng 配对异常 %d 处：%s' % (len(host_bad), host_bad[:8]))
    else:
        ok('12 个就地插入宿主的 lng 条数 == `14` 条数 + `0f` 条目数')
    if debt:
        print('[债务] Res303 遗留的 lng 条数不符 %d 处（待语义复审）：%s'
              % (len(debt), debt[:8]))


def check_resource_classification(graphic, chip2):
    print('\n== Resource classification ==')
    bad_chip2 = [n for n in chip2 if not n.upper().startswith('EVCC') and not n.upper().startswith('CN_EVCC')]
    if bad_chip2:
        fail('Chip2.arc has non-EVCC members: %s' % bad_chip2[:10])
    else:
        ok('Chip2.arc contains only EVCC*/CN_EVCC* (%d members)' % len(chip2))

    bad_graphic = [n for n in graphic if n.upper().startswith('CN_EVCC')]
    if bad_graphic:
        fail('Graphic.arc contains CN_EVCC* members (should be in Chip2.arc): %s' % bad_graphic)
    else:
        ok('Graphic.arc contains no CN_EVCC* members')

    # 引擎只加载 EVCC 前缀的 CG（doc/lessons-learned.md §12）；CN_ 前缀一律不得残留
    bad_prefix = [n for n in list(graphic) + list(chip2) if n.upper().startswith('CN_')]
    if bad_prefix:
        fail('CN_-prefixed members must not exist (engine will ignore them): %s' % bad_prefix[:10])
    else:
        ok('no CN_-prefixed members in Graphic.arc / Chip2.arc')

    cn_cgs = sorted(n for n in chip2 if n.upper().startswith('EVCC9'))
    if len(cn_cgs) < 37:
        fail('Chip2.arc has only %d restored CGs (expected >= 37)' % len(cn_cgs))
    else:
        ok('Chip2.arc has %d restored CGs (EVCC9XXX)' % len(cn_cgs))

    # 成员必须真的是 PNG：曾出现过把原版 MOS（文件头 WIPF）改扩展名塞进来的情况
    not_png = [n for n, v in chip2.items() if not v.startswith(b'\x89PNG\r\n\x1a\n')]
    if not_png:
        fail('Chip2.arc has non-PNG members (engine cannot decode): %s' % not_png[:10])
    else:
        ok('all %d Chip2.arc members carry the PNG signature' % len(chip2))


def check_resource_pairing(rio, graphic, chip2, chip1, voice):
    print('\n== Resource pairing (inlined hosts) ==')
    available = (set(n.upper() for n in graphic)
                 | set(n.upper() for n in chip2)
                 | set(n.upper() for n in chip1)
                 | set(n.upper() for n in voice))
    idx = {k.upper(): v for k, v in rio.items()}
    # BLACK/WHITE 是引擎纯色占位图；BGM/SE 走游戏安装目录，不在本项目分发的归档里
    placeholders = {'BLACK', 'WHITE'}
    missing = []
    for host in HOSTS_INLINE:
        ins = ws2disasm.disassemble(ws2.decode(idx[host.upper()]))
        for i in ins:
            if i.opcode == 0x33:
                fn, kind = i.fields['file'].upper(), 'PNG'
            elif i.opcode == 0x34:
                fn, kind = i.fields['file'].upper(), 'PNA'
            elif i.opcode == 0x66:
                # `66` 的 name 字段**自带** .PNG 扩展名（转换器 emit_mask 就是这么写的）
                fn, kind = i.fields['name'].upper(), 'PNG'
            elif i.opcode == 0x2e:
                fn, kind = i.fields['file'].upper(), 'OGG'
            else:
                continue
            if fn.rsplit('.', 1)[0] in placeholders:
                continue
            if fn not in available:
                missing.append((host, fn, kind))
    if missing:
        fail('还原脚本引用的资源无法解析 %d 处：%s' % (len(missing), missing[:8]))
    else:
        ok('12 个就地插入宿主引用的图像/立绘/蒙版/语音全部可解析')


def _same_file(a, b, chunk=1 << 22):
    """分块比对两个文件是否逐字节相同（避免把 340 MB 整个读进内存）。"""
    if a.stat().st_size != b.stat().st_size:
        return False
    with open(a, 'rb') as fa, open(b, 'rb') as fb:
        while True:
            x, y = fa.read(chunk), fb.read(chunk)
            if x != y:
                return False
            if not x:
                return True


def check_idempotency():
    print('\n== Idempotency (rebuild stability) ==')
    for f in ['Rio.arc', 'Graphic.arc', 'Chip2.arc']:
        path = ASSET / f
        if not path.exists():
            ok('%s not shipped; nothing to rebuild' % f)
            continue
        # 重写到**临时文件**再比对，不覆盖原件：
        #   * 验证力相同（读→写→逐字节比对）；
        #   * 不动原文件的修改时间 —— 就地重写会把 340 MB 的 Chip2.arc 时间戳
        #     刷新成与 Rio.arc 同期，看起来像"每次都被改了"，实际内容没变；
        #   * 顺带避免写坏原件。
        # 代价是临时文件 + 原文件两份空间，磁盘不足时跳过而不是冒 ENOSPC 的风险。
        tmp = path.with_name(path.name + '.idempotency_tmp')
        free = shutil.disk_usage(path.parent).free
        need = path.stat().st_size * 2
        if free < need:
            print('[SKIP] %s: free space %d B < %d B needed for a safe rewrite'
                  % (f, free, need))
            continue
        try:
            arcbuild.write_arc(arcbuild.read_raw(path), tmp)
            same = _same_file(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)
        if not same:
            fail('%s differs after re-read/re-write' % f)
        else:
            ok('%s byte-identical after rebuild (%s)'
               % (f, hashlib.sha256(path.read_bytes()).hexdigest()[:12]))


def main():
    check_arc_integrity()

    rio = load_arc(ASSET / 'Rio.arc')
    # 未改动时 asset/ 下没有 Graphic.arc，退回 Steam 原档做资源检查
    graphic_path = ASSET / 'Graphic.arc'
    if not graphic_path.exists():
        graphic_path = Path('backup/Graphic.arc')
    graphic = load_arc(graphic_path)
    chip2 = load_arc(ASSET / 'Chip2.arc')
    # 背景（BGCC*）归 Chip1.arc；本补丁不改动背景，安装器保留玩家原文件，
    # 故 asset/ 下没有 Chip1.arc 时退回 Steam 原档 backup/Chip1.arc。
    chip1_path = ASSET / 'Chip1.arc'
    if not chip1_path.exists():
        chip1_path = Path('backup/Chip1.arc')
    chip1 = load_arc(chip1_path)
    # 语音只在就地插入时被引用（H 场景语音）
    voice = load_arc(ASSET / 'Voice.arc')

    check_call_chain(rio)
    check_inline_splice(rio)
    check_lng_pairing(rio)
    check_resource_classification(graphic, chip2)
    check_resource_pairing(rio, graphic, chip2, chip1, voice)
    check_idempotency()

    print('\n== Summary ==')
    print('%d checks passed, %d failed' % (passes, len(failures)))
    if failures:
        print('[FAIL] verification failed')
        sys.exit(1)
    print('[OK] all checks passed')


if __name__ == '__main__':
    main()
