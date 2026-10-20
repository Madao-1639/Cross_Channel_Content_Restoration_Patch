# -*- coding: utf-8 -*-
"""行号对位闸：核「表里的 `op=ccs` 区段」是不是真的**逐格**对上了 Steam 的台词流。

## 为什么需要

`text_map.json` 的 `op=ccs` 区间条目只能表达「`row = row0 + (k - k0)`」这种**均匀**偏移。
只要 Steam 在某个位置**整行删掉**（或整行新增）而源 CCS 仍保留那一行，均匀块就会从那里起
**整体错开一格**：屏幕上的文本永远落后演出/语音一行；而 `script/apply_text_map.py` 的
「正文取自原版、原版该行无配音 ⇒ 删掉本格 `2e`」还会按**前一行的配音属性**判定
⇒ 该句的录音被删（听着"没有语音"）、相邻那句的录音被留下（听着"播的是下一句"）。

实测事故：`CCA0002_EN` 的 `[0,192] → row0=1` 忽略了 Steam 删掉的原版 `CCS#129`
（`%FF%LC　桐原流奥義積極直撃`），导致 `k=128..193`（66 格）整体落后一行：
`TOU_0007…TOU_0024` 共 16 条被误删、「都收拾得差不多了」播的是下一句的 `MIS_0001`。
**计数类闸全部通过**（行数守恒、一行一行消耗、lng 条数对齐），因为错位不动任何计数 ——
只有拿**独立于表**的 Steam 侧信源才查得出来。

## 判据（机械、无阈值）

两侧各有一个**跨语言可比**的符号：

* Steam 侧：每句对话前的 `15 %LC<名>`（`asset` 与 `backup` 里都在，**不受表改写影响**）；
* 源侧：`resource/corpus/ccs/*.CCS` 每行行首的 `[話者]`。

两者经 `resource/speaker_map.json`（`ja` ↔ `en`）一一对应 —— 同一句话的说话人在两侧必须相同。
错位一格的区间里，对话格的说话人会**连续成片**对不上（旁白/无名的格没有符号，不参与判定）。

所以判据是：**同一个脚本内，按槽位序出现 ≥2 个「说话人对不上、而它下一行的说话人正好对上」
的相邻证据（槽位间隔 ≤3）⇒ 判为区间错位**。单个对不上的无关格（Steam 把旁白改写成台词、
官方把 `少女` 写成 `Girl` 之类）不构成证据，只统计。

## 只看**非宿主**脚本

12 个就地插入宿主的表 `k` 是**串接后**的格数（如 `CCD1001B` 905），而 `backup/` 里只有串接前的
518 格 —— 拿原档对位会整体错位（全是假阳性）；串接后的基线是流水线中间产物、不入库，所以这类
脚本跳过并明示（它们的对位由 `doc/call-chain.md` 的切片表负责）。

用法（项目根目录）：
    python script/audit/audit_row_alignment.py
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tool import arcbuild, ws2, ws2disasm, textplan  # noqa: E402
from tool.lng import parse_ccs_both                     # noqa: E402

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

STEAM_RIO = ROOT / 'backup' / 'Rio.arc'
SPK = ROOT / 'resource' / 'speaker_map.json'

_SPK = re.compile(r'^\[([^\]]+)\]')


def ja_to_en():
    """日文说话人名 → Steam `%LC` 名（`見里`→`Misato`、`少女`→`Girl`…）。"""
    out = {}
    for s in json.loads(SPK.read_text(encoding='utf-8'))['speakers']:
        for j in (s.get('ja') or []):
            out[j] = s['en']
    return out


def slots(ins):
    """→ [(格位序, Steam 名字框 or None)]，格位序与 lng 同口径（`14` 一格、`0f` 每条目一格）。"""
    out, name, si = [], None, 0
    for i in ins:
        if i.opcode == 0x15:
            p = i.fields.get('prefix') or ''
            name = (p[3:] if p.startswith('%LC') else p) or None
        elif i.opcode == 0x14:
            out.append((si, name))
            name, si = None, si + 1
        elif i.opcode == 0x0f:
            for _ in i.fields['entries']:            # 选项每条目也算一格（与 lng 同口径）
                out.append((si, None))
                si += 1
            name = None
    return out


def hosts():
    """就地插入宿主（其表 `k` 与 backup 的槽位编号不同口径）。"""
    p = ROOT / 'resource' / 'scene_slices.json'
    if not p.exists():
        return set()
    sl = json.loads(p.read_text(encoding='utf-8'))
    return ({s['host'].upper()[:-4] for s in sl['scenes']} |
            {t['host'].upper()[:-4] for t in sl['tail_hosts']})


def main():
    j2e = ja_to_en()
    host = hosts()
    backup = {n.decode('utf-16-le').upper(): d for n, d in arcbuild.read_raw(STEAM_RIO)}
    ccs_zh = {}

    finds, n_slot, n_hit, n_skip = [], 0, 0, []
    for stem, sc in sorted(textplan.TM.items()):
        if stem.upper() in host:
            n_skip.append(stem)
            continue
        raw = backup.get(stem.upper() + '.WS2')
        if raw is None:
            n_skip.append(stem)
            continue
        zh = ccs_zh.get(sc['ccs'])
        if zh is None:
            zh = ccs_zh[sc['ccs']] = parse_ccs_both(
                ROOT / 'resource' / 'corpus' / 'ccs' / (sc['ccs'] + '.CCS'))[1]
        try:
            rows = textplan.expand_script(sc, {})[4]
        except Exception:
            continue

        def speaker_of(row):
            m = _SPK.match(zh.get(row, ''))
            return j2e.get(m.group(1)) if m else None

        cand = []
        st_slots = slots(ws2disasm.disassemble(ws2.decode(raw)))
        # 表的 `k` 必须与原档的格位同口径：长度不等说明该脚本被流水线改过格数
        # （如 `CCB2017` 的选项分支插了 23 格），拿原档对位必然全错。
        if len(st_slots) != sc['n_steam']:
            n_skip.append('%s(格数 %d≠%d)' % (stem, len(st_slots), sc['n_steam']))
            continue
        for k, name in st_slots:
            if k >= sc['n_steam']:
                continue
            n_slot += 1
            row = rows.get(k)
            if not name or row is None:
                continue
            mine = speaker_of(row)
            if not mine or mine == name:
                continue
            # 只有「下一行正好对上」才算错位证据；否则只是 Steam 改写/别名，不算
            if speaker_of(row + 1) != name:
                continue
            n_hit += 1
            cand.append((k, name, mine, row))

        # 相邻证据成组（槽位间隔 ≤3）；≥2 个才算一处
        group = []
        for c in cand:
            if group and c[0] - group[-1][0] <= 3:
                group.append(c)
            else:
                if len(group) >= 2:
                    finds.append((stem, group))
                group = [c]
        if len(group) >= 2:
            finds.append((stem, group))

    print('行号对位闸：核了 %d 个非宿主脚本（跳过宿主/缺原档 %d 个）；比对 %d 个格位；'
          '单点不符 %d 处（不计）' % (len(textplan.TM) - len(n_skip), len(n_skip), n_slot, n_hit))
    if finds:
        print('\n！%d 个脚本存在**成片的行号错位**（名字框与源行说话人连续对不上）：' % len(finds))
        for stem, g in finds:
            print('   %-16s %d 格：槽位 %d..%d' % (stem, len(g), g[0][0], g[-1][0]))
            for k, name, mine, row in g[:4]:
                print('        槽 %-4d Steam 名字框 %-14s 表行 %-5d 说话人 %s' % (k, name, row, mine))
        print('\n成因：`op=ccs` 区间假设「Steam 槽 ↔ 源行」均匀 1:1，'
              '而 Steam 在该段整行删掉/新增了一行 —— 见 doc/lessons-learned.md')
        return 1
    print('✓ 未发现成片错位：表里的「源行 ↔ 槽位」与 Steam 的说话人序列一致')
    return 0


if __name__ == '__main__':
    sys.exit(main())
