"""Full acceptance verification for the built patch in asset/.

Checks: Arc integrity (no padding), call-chain completeness, resource
classification rules, resource pairing, and rebuild idempotency (SHA256
stability).
"""
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tool import arcbuild, lng as lngmod, ws2, ws2disasm  # noqa: E402

# 控制台按 UTF-8 输出（与流水线里其余脚本一致）。缺这一句时，**失败消息里任何 GBK 编不出的字符
# 都会在 `print` 处抛 UnicodeEncodeError、把整套验收中止** —— 而失败消息恰恰是带 `−`/`⇒` 那些。
# 2026-09-25 实测：`check_insert_counts` 第一次真的走到 `fail()` 时就这么崩了（见 doc/lessons-learned.md）。
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

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
# 宿主对话数（`14` 条数）现由 `script/apply_text_map.py` 按 `resource/text_map.json` 一次产出：
# 还原插入（`insert`）增格、`drop` 减格、同一行的拆分记为 `span`（**不**增格）。
# （早期的 `script/realign_lng_to_ws2.py` 会为「CCS 有一行以 `%` 开头」插 `15`+`14`，
#  已于 2026-09-22 废弃 —— 实测它不改任何脚本、贡献为 0。）
INLINE_DIALOGUES = {
    'CCA0025C_en.ws2': 573, 'CCB1014C_en.ws2': 572, 'CCB2013_en.ws2': 547,
    'CCB2101_en.ws2': 237, 'CCC0000_en.ws2': 384, 'CCC3027_en.ws2': 809,
    'CCC4022_en.ws2': 615, 'CCD0022A_en.ws2': 359, 'CCD1001B_en.ws2': 906,
    'CCD4003A_en.ws2': 948, 'CCD5001A_en.ws2': 1112, 'CCD5001B_en.ws2': 352,
    # ⚠️ 上列 12 个值**必须等于 `resource/text_map.json` 各宿主的 `n_final`**（每次表加了
    # 插入段或删格都要同步）。2026-09-20 起已把「对话数 == 表 n_final」交给哨兵
    # `verify_text_map.py` 做机械断言 —— 这里的硬编码是**独立**的第二道，别让它过期。
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
    #   Chip1.arc —— 与 Steam 原档逐字节相同（本补丁不改背景）
    #   SysGraphic.arc —— 系统界面暂不汉化，沿用 Steam 版（2026-09-11 决定）
    # 注：Graphic.arc 自 2026-09-27 起**要分发**了 —— 三张整屏图被汉化版替换
    # （efcca0030 / SGCC0003 / sgcc0011，见 resource/graphic_overrides/manifest.json），
    # 不再与 Steam 原档逐字节相同。
    OPTIONAL = {'Chip1.arc', 'SysGraphic.arc'}
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

    # 引擎只加载 EVCC 前缀的 CG（doc/lessons-learned.md 「Chip2 事件 CG 的前缀白名单」）；CN_ 前缀一律不得残留
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


TAIL = re.compile(r'(?:%[A-Za-z])+$')       # 尾部控制符（`%K`/`%P`/`%N` 的任意组合）


def check_tails(rio):
    """**尾部控制符结构**：对话格必须带 `%K`/`%P`/`%N`，选项格必须不带。

    全库**唯一**能独立证伪「尾标记整体丢失」的判据，且不需要任何外部参照 —— 两版语料
    在这条上都**零例外**（Steam 原档 51,452 个对话格全带尾、127 个选项条目全不带；
    源 WSC 41,629 行全带尾）。2026-09-27 事故：每格英文取自派生缓存，缓存路径失效后
    `fix_tail(zh, '')` 静默什么都不补 ⇒ 39,113 个对话格裸尾（引擎不再等待点击、一屏堆满
    对话），而当时流水线**所有闸照过**。本项即为此而立。
    """
    idx = {k.upper(): v for k, v in rio.items()}
    bare = tailed = n_dlg = n_opt = n_seen = 0
    ex = []
    for name in sorted(k for k in rio if k.upper().endswith('.WS2')):
        data = idx.get((name[:-4] + '.lng').upper())
        if data is None:
            continue
        kinds = []
        for i in ws2disasm.disassemble(ws2.decode(rio[name])):
            if i.opcode == 0x14:
                kinds.append('dlg')
            elif i.opcode == 0x0f:
                kinds.extend(['opt'] * len(i.fields['entries']))
        L = lngmod.parse_lng(data)
        if len(L) != len(kinds):
            continue                       # 条数不符由 `check_lng_pairing` 报，这里不重复
        n_seen += 1
        for t, kd in zip(L, kinds):
            m = TAIL.search(t)
            if kd == 'dlg':
                n_dlg += 1
                if not m:
                    bare += 1
                    if len(ex) < 8:
                        ex.append('%s dlg %r' % (name, t[:30]))
            else:
                n_opt += 1
                if m:
                    tailed += 1
                    if len(ex) < 8:
                        ex.append('%s opt %r' % (name, t[:30]))
    if n_seen == 0:
        fail('尾部控制符：一个脚本都没核到 —— 闸不得空转')
        return
    if bare or tailed:
        fail('尾部控制符不成立：对话格缺尾 %d/%d 处；选项格带尾 %d/%d 处。例：%s'
             % (bare, n_dlg, tailed, n_opt, ex))
    else:
        ok('尾部控制符：对话格 %d 处全带尾、选项格 %d 处全不带（核了 %d 个脚本）'
           % (n_dlg, n_opt, n_seen))


def check_insert_counts(rio):
    """表声明的插入行 / 删格，必须逐脚本在产物里兑现 —— 三方对齐里「表 ↔ 脚本」那一环
    （「表 ↔ lng」由 `check_lng_pairing` 管；「lng ↔ 槽位」由它和哨兵 `verify_text_map` 管）。
    此前 42 项里没有任何一项数过「本轮补回了几行 / 删了几格」，1,094 与 272 只活在口头。"""
    tm = json.loads(Path('resource/text_map.json').read_text(encoding='utf-8'))['scripts']
    # 基线槽数取**表里的 `n_steam`**（"插入前"的格数，本就是表的一条不变量）—— 原先读
    # `asset/Rio.arc.before_insert_deleted`：那是旧流程的备份，既随轮次变化、又已不再生成。
    # ⚠️ 2026-09-25 修：归档里的成员名是 `XXX_en.ws2`（小写扩展名），而表的键是 `XXX_EN` ——
    #    查表必须先把两边都规范成大写。此前直接拿 `stem + '.WS2'` 去查 `rio`，**恒为 False**，
    #    于是整道检查对每个脚本都 `continue`、**一条都没验**却照样打印 OK
    #    （当时表与产物已在 8 个脚本上不符，验收仍报 "0 failed"）。
    rio_idx = {k.upper(): v for k, v in rio.items()}
    ti = td = ts = n_seen = n_bad = 0

    def slots(d):
        return sum((1 if i.opcode == 0x14 else len(i.fields['entries']) if i.opcode == 0x0f else 0)
                   for i in ws2disasm.disassemble(ws2.decode(d)))

    for stem, sc in sorted(tm.items()):
        ins = sum(it['n'] for it in sc['items'] if it['op'] == 'insert')
        dr = sum(1 for it in sc['items'] if it['op'] == 'drop')
        sp = sum(len(it['at']) for it in sc['items'] if it['op'] == 'split')   # 一格拆多格：多出的格数
        ti += ins
        td += dr
        ts += sp
        wk = stem + '.WS2'
        if wk not in rio_idx:
            fail('%s 不在产物里' % wk)
            continue
        n_seen += 1
        n1, n0 = slots(rio_idx[wk]), sc['n_steam']
        if n1 != n0 + ins + sp - dr:
            fail('%s 产物槽数 %d != 表 n_steam %d + 插入 %d + split %d - 删格 %d'
                 % (stem, n1, n0, ins, sp, dr))
            n_bad += 1
    # 兜底：一个脚本都没核到 ⇒ 说明查表方式又对不上了，不能算通过
    if n_seen == 0:
        fail('插入/删格逐脚本核对：一个脚本都没核到（成员名大小写对不上？）')
        return
    if not n_bad:
        ok('插入行 %d ／ 删格 %d ／ 拆格 %d 逐脚本兑现（核了 %d 个脚本）' % (ti, td, ts, n_seen))


def check_voice_channels(rio):
    """产物里每条 `2e` 的通道名都必须在 `resource/speaker_map.json` 的映射表里。

    此前验收里唯一沾语音的一项只遍历 12 个宿主、且只看录音文件在不在、**不看通道名** ——
    本轮 358 条通道名写错正是从这个缺口漏过去的（见 doc/lessons-learned.md）。"""
    from tool import speaker as spk
    reg = set(spk.voice_channel().values())
    bad, n = {}, 0
    for name, data in rio.items():
        # ⚠️ 2026-09-25 修：成员名是小写扩展名 `XXX_en.ws2`，`name.endswith('.WS2')`
        #    一 true 都判不到 ⇒ 一条 `2e` 都没扫、却打印 OK（当时那句"全部 0 条"就是空转的证据）。
        if not name.upper().endswith('.WS2'):
            continue
        for i in ws2disasm.disassemble(ws2.decode(data)):
            if i.opcode == 0x2e:
                n += 1
                if (i.fields.get('chan') or '') not in reg:
                    bad[i.fields.get('chan')] = bad.get(i.fields.get('chan'), 0) + 1
    if n == 0:
        fail('一条 `2e` 都没扫到（成员名过滤条件对不上？）')
    elif bad:
        fail('通道名不在映射表里：%s' % bad)
    else:
        ok('全部 %d 条 `2e` 的通道名都在映射表里（%d 种）' % (n, len(reg)))


def check_row_once(rio):
    """一行只消耗一次：同一行被多格承载时须全是该行的切分且互不重叠。
    切分 ⇒ 次数 = 切分段数；分支 ⇒ 跨脚本各算一次；两者可叠加。"""
    import collections
    tm = json.loads(Path('resource/text_map.json').read_text(encoding='utf-8'))['scripts']
    bad = 0
    for stem, sc in sorted(tm.items()):
        occ, spanof = collections.defaultdict(list), {}
        for it in sc['items']:
            if it['op'] != 'ccs':
                continue
            if 'row' in it and not isinstance(it['k'], list):
                occ[it['row']].append(it['k']); spanof[it['k']] = it.get('span')
            elif 'row0' in it and isinstance(it['k'], list):
                for j, k in enumerate(range(it['k'][0], it['k'][1] + 1)):
                    occ[it['row0'] + j].append(k); spanof[k] = None
        for r, kk in occ.items():
            if len(kk) <= 1:
                continue
            sps = [spanof.get(k) for k in kk]
            if any(x is None for x in sps):
                fail('%s 行 %d 被 %d 格承载且非全为切分 ⇒ 重复消耗' % (stem, r, len(kk))); bad += 1
            else:
                sps = sorted(sps)
                if any(b[0] < a[1] for a, b in zip(sps, sps[1:])):
                    fail('%s 行 %d 切分重叠 %s' % (stem, r, sps)); bad += 1
    if not bad:
        ok('一行只消耗一次（切分＝段数、分支跨脚本各算一次）')


def check_row_consumption():
    """**一行只消耗一次**：同一脚本内，被绑到同一行的每一格都必须是该行的 `span` 片段。

    切分 ⇒ 次数 ＝ 切分段数；分支（同一 CCS 被多个 Steam 脚本使用）⇒ 按各脚本分别计；两者可叠加。
    例外至此为止 —— 一行被两个**非 span** 格承载，就是屏上同一句话出现两遍（见 doc/lessons-learned.md）。"""
    tm = json.loads(Path('resource/text_map.json').read_text(encoding='utf-8'))['scripts']
    bad = 0
    for stem, sc in sorted(tm.items()):
        rows = {}
        for it in sc['items']:
            if it['op'] != 'ccs':
                continue
            kk = it['k'] if isinstance(it['k'], list) else [it['k'], it['k']]
            for j, k in enumerate(range(kk[0], kk[1] + 1)):
                r = it['row'] if 'row' in it else (it['row0'] + j if 'row0' in it else None)
                if r is not None:
                    rows.setdefault(r, []).append((k, 'span' in it))
        for r, ks in rows.items():
            if len(ks) > 1 and not all(sp for _, sp in ks):
                fail('%s：行 %d 被 %s 格重复消耗（只有 span 切分才能占多格）'
                     % (stem, r, [k for k, _ in ks]))
                bad += 1
    if not bad:
        ok('一行只消耗一次（span 切分／分支除外）')


def check_voice_format_and_sidecar():
    """语音**格式**与**侧车**（评审 2026-09-27 要求的两条产物级断言）：

      ① `asset/Voice.arc` 里**所有 OGG 采样率必须 = 48000**（Steam 语料形态；44100 分支是隐患）；
      ② `.soundlevel` 侧车条数**不得低于** `backup` 的侧车数 + `resource/carried_soundlevel.json`
         的内化条数（防"基线从 backup 起底"时静默丢侧车）。
    """
    def load(p):
        return {n.decode('utf-16-le'): d for n, d in arcbuild.read_raw(p)}

    def rate(d):
        p = d.find(b'\x01vorbis')
        return int.from_bytes(d[p + 12:p + 16], 'little') if p >= 0 and p + 16 <= len(d) else None

    a, b = load(ASSET / 'Voice.arc'), load(Path('backup/Voice.arc'))
    ogg = [k for k in a if k.upper().endswith('.OGG')]
    bad = sorted(k for k in ogg if rate(a[k]) != 48000)
    if bad:
        fail('asset/Voice.arc 有 %d 条 OGG 采样率 != 48000：%s' % (len(bad), bad[:5]))
    else:
        ok('asset/Voice.arc 的 OGG 采样率集合 = {48000}（%d 条）' % len(ogg))

    carried = Path('resource/carried_soundlevel.json')
    floor = sum(1 for k in b if k.lower().endswith('.soundlevel'))
    if carried.exists():
        floor += len([k for k in json.loads(carried.read_text(encoding='utf-8')) if not k.startswith('_')])
    n = sum(1 for k in a if k.lower().endswith('.soundlevel'))
    if n < floor:
        fail('`.soundlevel` 侧车 %d 条 < 下限 %d（backup 侧车 + 内化清单）—— 侧车被丢了' % (n, floor))
    else:
        ok('`.soundlevel` 侧车 %d 条 ≥ 下限 %d（backup + 内化清单）' % (n, floor))


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
    check_tails(rio)
    check_resource_classification(graphic, chip2)
    check_resource_pairing(rio, graphic, chip2, chip1, voice)
    check_row_consumption()
    check_insert_counts(rio)
    check_row_once(rio)
    check_voice_channels(rio)
    check_voice_format_and_sidecar()
    check_idempotency()

    print('\n== Summary ==')
    print('%d checks passed, %d failed' % (passes, len(failures)))
    if failures:
        print('[FAIL] verification failed')
        sys.exit(1)
    print('[OK] all checks passed')


if __name__ == '__main__':
    main()
