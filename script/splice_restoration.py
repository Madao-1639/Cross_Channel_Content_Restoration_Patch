"""把源 WSC 的删除区间就地插入调用脚本（替换 Res303 的「追加还原脚本」接线）。

统一形状（见 doc/call-chain.md）
-------------------------------
    新宿主 = [前段: Steam 原生，源 0..keep-1]
           + [插入段: 源 lo_eff..hi 的切片转换（含场景末尾事件）]
           + [出口尾段: Steam 原档「末句对话之后」的字节，含原出口]

要点
----
* **保留宿主的全部开场件**（第一句对话之前的载图/BGM/鉴赏钩子）：插入点落在
  第一句对话之前的那个 `15` 上，开场件原样留在前段里。
* **`14` 的 id = 字符串池出现序**（原生语料实测：全语料 0..n-1 连续）。插入段的
  `pool_start` 必须等于前段保留下来的对话数，否则池序号错位。
* **不需要重编号**：插入点之后不再保留宿主的任何对话（尾段只有演出指令与出口），
  所以插入段之后的池序号不会被别的 `14` 用到。
* **出口取 Steam 原档**：Res303 把宿主的出口改成了 `07 CNRxxxx_EN`，这里恢复成
  原档出口；`01 mode=0x85` 的文件内偏移按新布局重算。
* 12 个宿主全部**没有** `06` / `01` / `0f`（已实测），所以拼接不破坏任何绝对偏移，
  唯一的例外是 `CCC0000` 出口尾段里的 `01`，由本脚本重算。

只读校验：`--check`；写入：`--write`（会先备份 asset/Rio.arc）。
"""
import argparse
import hashlib
import io
import re
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tool import arcbuild, ws2, ws2disasm, wsc  # noqa: E402
from tool.wsc2ws2 import ConvertOptions, convert_range  # noqa: E402

if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

WSC_DIR = ROOT / 'tmp' / 'corpus' / 'wsc'
RIO = ROOT / 'asset' / 'Rio.arc'
STEAM_RIO = ROOT / 'backup' / 'Rio.arc'
BACKUP = ROOT / 'asset' / 'Rio.arc.before_inline_splice'

# 逐场景参数。`lo`/`hi` 来自 script/audit_coverage.py（与 Res303 report.json 11/11 一致），
# `hi` = 插入段的**末句源序号（含）**，等于场景末句、或下一个承载脚本的源起点 − 1；
# `head_overlap` = 宿主开头与源「内容完全一致」的连续块长度（长连续块判据，≥5）；
# `keep` = 前段保留的宿主对话数 = 插入段首句在字符串池里的位置。
SCENES = [
    dict(host='CCA0025C_en.ws2', src='CCA0025C', lo=15, hi=571, head_overlap=0, keep=16),
    dict(host='CCB1014C_en.ws2', src='CCB1014C', lo=15, hi=570, head_overlap=0, keep=16),
    dict(host='CCB2013_en.ws2', src='CCB2013', lo=0, hi=546, head_overlap=0, keep=0),
    dict(host='CCB2101_en.ws2', src='CCB2101', lo=0, hi=236, head_overlap=6, keep=6),
    dict(host='CCC0000_en.ws2', src='CCC0000', lo=195, hi=363, head_overlap=0, keep=211),
    dict(host='CCC3027_en.ws2', src='CCC3027', lo=203, hi=808, head_overlap=0, keep=84),
    dict(host='CCC4022_en.ws2', src='CCC4022', lo=135, hi=614, head_overlap=6, keep=141),
    dict(host='CCD0022A_en.ws2', src='CCD0022A', lo=7, hi=348, head_overlap=0, keep=18),
    dict(host='CCD1001B_en.ws2', src='CCD1001', lo=980, hi=1560, head_overlap=13, keep=337),
    dict(host='CCD4003A_en.ws2', src='CCD4003', lo=486, hi=865, head_overlap=0, keep=539),
    dict(host='CCD5001A_en.ws2', src='CCD5001', lo=450, hi=1111, head_overlap=0, keep=444),
]
# 需要「截掉与插入段重合的头部」的第二个脚本。
# `from_dialogue` = 该脚本里对应源 `from_src` 的那条对话（实测 lng 对位定出）
TAIL_HOSTS = [
    dict(host='CCD5001B_en.ws2', src='CCD5001', from_dialogue=220, from_src=1112),
]


def load(path):
    return {n.decode('utf-16-le').upper(): v for n, v in arcbuild.read_raw(path)}


def rebase_offsets(data, base):
    """把切片**内部**的文件内绝对偏移整体平移 `base` 字节（拼接前必须做）。

    切片由 `convert_range` 独立生成，它只知道切片自己的坐标系；里面 `06 <u32 目标>`
    与 `01 mode=0x85 <u32 b>` 记的都是切片内偏移。拼到宿主前缀之后，这些值要整体加
    `len(prefix)`，否则会指向前缀区里的无关位置（实测 11 处落在指令中间）。
    返回 (新字节, 改动处数)。
    """
    ins = ws2disasm.disassemble(data)
    out = bytearray(data)
    n = 0
    for i in ins:
        if i.opcode == 0x06:
            out[i.offset + 1:i.offset + 5] = struct.pack('<I', i.fields['target'] + base)
            n += 1
        elif i.opcode == 0x01 and i.fields.get('mode') == 0x85:
            out[i.offset + 12:i.offset + 16] = struct.pack('<I', i.fields['b'] + base)
            n += 1
    return bytes(out), n


def load_rename_map():
    """资源重命名表。直接调用构建器，不经过 tmp/ 中转文件。"""
    sys.path.insert(0, str(ROOT / 'script'))
    import build_rename_map
    rename, unresolved, unchanged, _cg, conflicts = build_rename_map.build()
    print('资源重命名表 %d 条；无需改名且已存在 %d 个' % (len(rename), len(unchanged)))
    if conflicts:
        raise SystemExit('CG 编号表有未入表项：%s' % conflicts)
    if unresolved:
        raise SystemExit('有资源找不到出处，先跑 script/import_missing_voices.py：%s'
                         % unresolved[:10])
    return rename


def src_instrs(stem):
    return wsc.disassemble((WSC_DIR / (stem + '.WSC')).read_bytes())


def load_inventory():
    """运行时可用资源名集合（大写）。用于「不存在就不发射」的防御（蒙版）。"""
    inv = set()
    sys.path.insert(0, str(ROOT / 'script'))
    import build_rename_map
    for p in build_rename_map.ARCHIVES:
        q = ROOT / p
        if q.exists():
            inv |= {n.decode('utf-16-le').upper() for n, _ in arcbuild.read_raw(q)}
    return inv


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--check', action='store_true')
    ap.add_argument('--write', action='store_true')
    args = ap.parse_args()

    members = load(RIO)
    steam = load(STEAM_RIO)
    rename = load_rename_map()
    inventory = load_inventory()
    print('运行时可用资源 %d 个' % len(inventory))

    results, reports = {}, []
    for sc in SCENES:
        host, stem = sc['host'], sc['src']
        lo_eff = sc['lo'] + sc['head_overlap']
        host_raw = members[host.upper()]
        host_ins = ws2disasm.disassemble(ws2.decode(host_raw))
        dl = [i for i in host_ins if i.opcode == 0x14]
        if len(dl) < sc['keep']:
            raise SystemExit('%s 对话数 %d < keep %d' % (host, len(dl), sc['keep']))

        # ---- 插入点：第 keep 句对话之前的那条 `15`（保留宿主全部开场件）----
        if sc['keep'] < len(dl):
            first_dropped = host_ins.index(dl[sc['keep']])
            j = first_dropped - 1
            ins_at = host_ins[j].offset if host_ins[j].opcode == 0x15 else dl[sc['keep']].offset
        else:
            ins_at = dl[-1].offset + dl[-1].size
        prefix = host_raw[:ins_at]

        # ---- 插入段 ----
        win = src_instrs(stem)
        wdl = [i for i in win if i.opcode in (0x41, 0x42)]
        if lo_eff >= len(wdl):
            raise SystemExit('%s lo_eff %d 越界' % (stem, lo_eff))
        start = wdl[lo_eff].offset
        # 插入段终点：hi 是场景末句 -> 取到该句之后、`ff` 之前的场景末尾事件（等待/停 BGM 等）；
        # hi 是交接点（场景在本脚本之后还有内容）-> 只取到该句末，不能越界吞掉后继脚本的内容。
        if sc['hi'] == len(wdl) - 1:
            end = next((i.offset for i in win
                        if i.opcode == 0xff and i.offset > wdl[sc['hi']].offset), None)
            if end is None:
                raise SystemExit('%s 末句之后找不到 ff' % stem)
        else:
            end = wdl[sc['hi']].offset + wdl[sc['hi']].size
        opts = ConvertOptions(slice_mode=True, pool_start=sc['keep'], rename_map=rename,
                              available=inventory)
        src_raw = (WSC_DIR / (stem + '.WSC')).read_bytes()
        slice_plain, rep = convert_range(src_raw, stem, opts,
                                         start_offset=start, end_offset=end)
        if rep['exit'] is not None:
            raise SystemExit('%s 切片不应产生出口' % host)
        n_slice_dlg = rep['dialogues']
        if n_slice_dlg != sc['hi'] - lo_eff + 1:
            raise SystemExit('%s 切片对话数 %d != 源区间 %d'
                             % (host, n_slice_dlg, sc['hi'] - lo_eff + 1))
        # 切片里的**文件内绝对偏移**（`06` 目标 / `01 mode=0x85` 的 `b`）是**切片相对**的，
        # 拼到宿主里必须整体加上前缀长度，否则会指到别处（实测 11 处目标落在指令中间）。
        slice_plain, n_rebase = rebase_offsets(slice_plain, len(prefix))
        if n_rebase:
            print('  %-20s 重定位切片内绝对偏移 %d 处（+%d）' % (host, n_rebase, len(prefix)))

        # ---- 出口尾段：Steam 原档「末句对话之后」 ----
        s_ins = ws2disasm.disassemble(ws2.decode(steam[host.upper()]))
        s_dl = [i for i in s_ins if i.opcode == 0x14]
        tail_off = s_dl[-1].offset + s_dl[-1].size
        tail = bytearray(steam[host.upper()][tail_off:])
        # 尾段里的 `01 mode=0x85`：第二出口的文件内偏移按新布局重算
        seg_enc = ws2.encode(slice_plain)
        base = len(prefix) + len(seg_enc)
        fixed = 0
        for i in (x for x in s_ins if x.offset >= tail_off):
            if i.opcode != 0x01 or i.fields.get('mode') != 0x85:
                continue
            rel = i.offset - tail_off
            new_off = base + (i.fields['b'] - tail_off)
            tail[rel + 12:rel + 16] = ws2.encode(struct.pack('<I', new_off))
            fixed += 1
        new_host = bytes(prefix) + seg_enc + bytes(tail)

        # ---- 校验 ----
        ni = ws2disasm.disassemble(ws2.decode(new_host))
        if sum(i.size for i in ni) != len(ws2.decode(new_host)):
            raise SystemExit('%s 解析不完整' % host)
        ids = [i.fields['id'] for i in ni if i.opcode == 0x14]
        if ids != list(range(len(ids))):
            raise SystemExit('%s 对话 id 不连续：%s..%s' % (host, ids[:3], ids[-3:]))
        for x in (y for y in ni if y.opcode == 0x01 and y.fields.get('mode') == 0x85):
            tgt = next((y.fields.get('name') for y in ni if y.offset == x.fields['b']), None)
            if tgt is None:
                raise SystemExit('%s var-133 b=%d 未指向指令' % (host, x.fields['b']))
        results[host.upper()] = new_host
        reports.append((host, len(host_raw), len(new_host), len(ids), sc['keep'],
                        fixed, [i.fields.get('name') for i in ni if i.opcode == 0x07],
                        rep))

    # ---- 第二个脚本：截掉与插入段重合的头部 ----
    # `from_dialogue` 由实测的 lng 对位定出（源 1112 起 B 有 26/97/21/56/59/67 六个长连续块，
    # 起点正是 B 的第 220 条对话）。截头后 `14` 的 id 必须重编号成 0..n-1 ——
    # 「id = 字符串池出现序」在原生 363 个脚本上成立（9 个反例都是有 `0f` 选项表的脚本，
    # 跳号来自选项文本占槽），截头会让 id 从 220 起，不重编号池序号就错位。
    for th in TAIL_HOSTS:
        host = th['host']
        # 必须从 **Steam 原档** 截，不能从当前 asset 截：asset 里这份可能已经截过了，
        # 再截一次会变成「截两次」（实测 352 → 132 句），违反幂等性。
        raw = steam[host.upper()]
        ins = ws2disasm.disassemble(ws2.decode(raw))
        dl = [i for i in ins if i.opcode == 0x14]
        k = th['from_dialogue']
        if k >= len(dl):
            raise SystemExit('%s 对话数 %d <= from_dialogue %d' % (host, len(dl), k))
        j = ins.index(dl[k]) - 1
        cut = ins[j].offset if ins[j].opcode == 0x15 else dl[k].offset
        new_host = bytearray(raw[cut:])
        ni = ws2disasm.disassemble(ws2.decode(bytes(new_host)))
        n_dl = [i for i in ni if i.opcode == 0x14]
        for idx, i in enumerate(n_dl):
            # ni 的 offset 已经是相对 new_host 起点（=1 时按 cut 减会双重偏移）
            new_host[i.offset + 1:i.offset + 3] = ws2.encode(struct.pack('<H', idx))
        new_host = bytes(new_host)
        chk = ws2disasm.disassemble(ws2.decode(new_host))
        ids = [i.fields['id'] for i in chk if i.opcode == 0x14]
        if ids != list(range(len(ids))):
            raise SystemExit('%s 截头后 id 重编号失败' % host)
        if sum(i.size for i in chk) != len(ws2.decode(new_host)):
            raise SystemExit('%s 截头后解析不完整' % host)
        if any(i.opcode in (0x06, 0x01) for i in chk):
            raise SystemExit('%s 截头后出现绝对偏移指令，需重算' % host)
        results[host.upper()] = new_host
        reports.append((host, len(raw), len(new_host), len(ids), '-',
                        0, [i.fields.get('name') for i in chk if i.opcode == 0x07], None))

    print()
    print('%-20s %-9s %-9s %-7s %-7s %-6s %s' %
          ('宿主', '原大小', '新大小', '对话数', 'keep', '重算b', '出口'))
    for host, o, n, nd, keep, fixed, ex, rep in reports:
        print('%-20s %-9d %-9d %-7d %-7s %-6s %s' % (host, o, n, nd, keep, fixed, ex))

    # 幂等性：产出必须等于当前 asset 里的那份（即 asset 已是本变换的固定点）。
    # 这条能抓住「从当前 asset 做增量」而不是「从原档重建」这类错误
    # —— CCD5001B 的截头一度就是那样，二次运行会再截一次（352 → 132 句）。
    drift = [h for h, v in results.items()
             if h in members and members[h] != v]
    if drift:
        print()
        print('[幂等] 以下成员与当前 asset 不一致（首次运行正常；若已写入过请查原因）：')
        for h in drift:
            print('   %-20s %d -> %d 字节' % (h, len(members[h]), len(results[h])))
        if args.check:
            raise SystemExit('[中止] 非幂等')
    else:
        print()
        print('[幂等] 12 个成员与当前 asset 逐字节一致（已是固定点）')

    if not (args.write or args.check):
        print('\n（未指定 --check / --write，仅预览）')
        return 0
    if args.check:
        print('\n[--check] 未写入')
        return 0

    if BACKUP.exists():
        print('\n[备份] 已存在 %s（保留原样）' % BACKUP)
    else:
        arcbuild.write_arc(arcbuild.read_raw(RIO), BACKUP)
        print('\n[备份] asset/Rio.arc -> %s' % BACKUP)

    out, replaced = [], []
    for name_bytes, data in arcbuild.read_raw(RIO):
        name = name_bytes.decode('utf-16-le')
        new = results.get(name.upper())
        if new is not None:
            replaced.append(name)
            data = new
        out.append((name_bytes, data))
    arcbuild.write_arc(out, RIO)
    print('[写入] 替换 %d 个成员：%s' % (len(replaced), sorted(replaced)))
    back = {n.decode('utf-16-le').upper(): v for n, v in arcbuild.read_raw(RIO)}
    for k, v in results.items():
        if back[k] != v:
            raise SystemExit('[失败] 回读不一致：%s' % k)
    count, size, _ = arcbuild.verify(RIO)
    print('[回读] 校验通过（%d 成员，%d 字节，无 padding）' % (count, size))
    return 0


if __name__ == '__main__':
    sys.exit(main())
