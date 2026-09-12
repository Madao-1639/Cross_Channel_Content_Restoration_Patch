"""按 ws2 池位重建 lng：把汉化组 CCS 的译文重新对齐到 Steam 脚本的对话流。

问题
----
引擎按「`14`/`0f` 出现序」把 lng 第 k 条填进 ws2 第 k 个池位（见
[file-formats.md](../doc/file-formats.md)「匹配机制：位置对应」）。而现有 lng 是
Res303 按**汉化组 CCS 的行序**逐行抄的 —— **CCS 与 ws2 并非 1:1**：

* CCS 把某一句英文拆成两行中文（例：`CCA0002_en` 池位 128,
  `She hit me so hard, I actually saw lights, like effects from a video game.`
  对应 CCS 的 `%FF%LC　桐原流奥义积极直击` + `游戏里常见的奥义特效浮现于脑海之中。`）；
* 也有反向的合并，以及 Steam 相对原版新增/删节的句子。

一旦某处不是 1:1，其后所有条目的译文就整体错开 —— 表现为「台词与名字框对不上」
（名字框来自脚本的 `%LC`，正文来自 lng）。

做法
----
对每个脚本求「非控制池位 → CCS 行」的**最优单调对齐**（编辑距离 DP）：
允许 1:1、1:2（一句英文 ↔ 两行中文）、2:1（两行中文并进一句英文）、
以及两侧各自的插入/删除。代价以**说话人是否一致**为主导（`%LC` vs `[说话人]`，
旁白也算一种取值），旁白段辅以长度比例。

重建规则（依项目约定）：
* 控制行（正文为 `%N`/`%P`）→ lng 同名控制符，不消耗 CCS；
* 对齐到 1 行 CCS → 直接用该行（剥 `[说话人]` 包裹）；
* 对齐到 n 行 CCS → **给 ws2 插入 (n-1) 组 `15`+`14`**，各行中文各占一个池位，
  lng 也各是一条（「中文几行就几行」，见 `allow_insert`）；
* 未对齐到任何 CCS 行（Steam 新增）→ 沿用现有 lng（Res303 已译好）。

用法（项目根目录）：
    python script/realign_lng_to_ws2.py                    # 只诊断，汇总
    python script/realign_lng_to_ws2.py --stem CCA0002_en   # 单脚本详情
    python script/realign_lng_to_ws2.py --write             # 写入（备份+回读校验）
"""
import argparse
import collections
import io
import os
import re
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tool import arcbuild, lng, ws2, ws2disasm  # noqa: E402

if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

RIO = ROOT / 'asset' / 'Rio.arc'
BACKUP = ROOT / 'asset' / 'Rio.arc.before_lng_realign'
CCS_DIR = ROOT / '..' / 'cross-channel_chinese-localization_project' / 'Scripts' / '20150412'

SPK_RE = re.compile(r'^\[([^\]]+)\]')
CTRL_ONLY = re.compile(r'^(?:%[A-Za-z])+$')
CTRL_TAIL = re.compile(r'(?:%[A-Za-z])+$')
STRIP = re.compile(r'\\d|\\n')

# DP 代价
# 方针：**规则不求判准所有文本，只判高置信度的简单情形**，把范围缩小；
# 判不了的按「separate」（不合并）处理 —— 不合并只是保持原样，没有风险。
C_MATCH = 0        # 说话人一致
C_NARR = 1         # 两侧旁白（无信号，靠长度）
C_MISMATCH = 12    # 说话人明确冲突
C_MERGE = 4        # 1 句英文 ↔ 2 行中文（仅在 allow_insert 放行时可用）
C_SPLIT = 8        # 2 句英文 ↔ 1 行中文
C_WS_ONLY = 9      # ws2 有、CCS 无（Steam 新增，沿用现有译文）
C_CCS_ONLY = 20    # CCS 有、ws2 无 —— 高于 C_MISMATCH：宁可接受「说话人标记不符」
                   # 的 1:1，也别把中文行丢掉（实测案例见 §「两类成因不可分」）

# 仅当满足以下条件才给 ws2 插入新行（高置信度物证）：
#   pct —— CCS 有一行是 `%` 开头的特效/控制行（引擎指令，天然属于同一句）。
#
# 曾试过「英文 ≥ MERGE_MIN_LEN 就插入」，**已弃用**：长度不是可靠物证 ——
# `CCA0027_en` 池位19 的 `*insert crazy onomatopoeia equivalent to such an attack*`
# （61 字符）语义上就是 CCS 的一句，并不该拆；而且原句长度不随处理变化，二次运行
# 必然重复命中、破坏幂等。方针是**只判高置信度的简单情形**，其余交给人工比较。
MERGE_MIN_LEN = 60
END_PUNCT = '。！？…”’』」!?—～'


def allow_insert(ws_item, a, b):
    """判定「这一句英文是否该让出位置给两行中文」——决定**是否给 ws2 插入 15+14**。

    注意：判定的动作是**插入**，不是把两行中文并成一条 lng。命中后 process() 会给
    脚本插一对 `15`+`14`，两行中文各占一个池位，lng 也各是一条（「中文几行就几行」）。

    只认 `pct`（`%` 特效/控制行）。`ctrl_suffix(...) == '%K'` 的判断是幂等性关键：
    插入时会把前半句正文的控制符改成 `%K`（见 process），二次运行一看便知
    「已经拆过了」，不会重复插入。
    """
    if ctrl_suffix(ws_item['text']) == '%K':
        return False
    return (a['text'] or '').startswith('%') or (b['text'] or '').startswith('%')


def norm(t):
    t = STRIP.sub('', t or '')
    t = CTRL_TAIL.sub('', t.strip())
    return ''.join(c for c in t if c not in '“”"’‘' and not c.isspace())


def load_name_map(members):
    """NameTable.txt → {中文名: {候选英文名}}（多对一，保留全部候选）。"""
    m = collections.defaultdict(set)
    for line in members['NameTable.txt'].decode('utf-16le').splitlines():
        if line.strip():
            k, v = line.split('\t')
            m[v[3:]].add(k[3:])
    return m


def ws2_pool(raw):
    """按**字符串池槽位**返回列表，元素为 dict：

        {'kind': 'dlg'|'ctrl'|'opt', 'spk': 英文名 or None, 'text': str,
         'ins': 该槽所属指令, 'ent': 选项条目 dict（仅 opt）}

    * `0f <count>` 的每个选项条目各占一个槽位（strid 即池序号）；
    * 选项译文**不在**汉化组 CCS 里（见 lessons-learned §22），因此 opt 槽位不参与
      CCS 对齐，一律沿用现有 lng；
    * `ctrl` 指正文为纯 `%N`/`%P` 的槽位，同样不消耗 CCS。
    """
    cur, out = None, []
    for i in ws2disasm.disassemble(ws2.decode(raw)):
        if i.opcode == 0x15:
            pre = i.fields.get('prefix')
            if isinstance(pre, bytes):
                pre = pre.decode('cp932', 'replace')
            pre = (pre or '').split('\x00')[0]
            cur = pre[3:] if pre.startswith('%LC') else (pre or None)
            cur = cur or None
        elif i.opcode == 0x14:
            t = i.fields.get('text', '')
            if isinstance(t, bytes):
                t = t.decode('cp932', 'replace')
            out.append({'kind': 'ctrl' if CTRL_ONLY.match(t or '') else 'dlg',
                        'spk': cur, 'text': t, 'ins': i, 'ent': None})
            cur = None
        elif i.opcode == 0x0f:
            for ent in (i.fields.get('entries') or []):
                t = ent.get('text', '')
                if isinstance(t, bytes):
                    t = t.decode('cp932', 'replace')
                out.append({'kind': 'opt', 'spk': None, 'text': t,
                            'ins': i, 'ent': ent})
    return out


def load_ccs(stem):
    """[(speaker_set, text_no_wrap, raw)]，1-based 行号。"""
    p = CCS_DIR / (stem + '.CCS')
    if not p.exists():
        return None
    e = lng.parse_ccs(p)
    if not e:
        return None
    return [e.get(k, '') for k in range(1, max(e) + 1)]


def ccs_meta(raw_lines, zh2en):
    out = []
    for raw in raw_lines:
        s = raw.strip()
        m = SPK_RE.match(s)
        known = zh2en.get(m.group(1), set()) if m else None
        out.append({'spk': (known if known else set()) if m else set(),
                    'named': bool(m), 'raw_spk': m.group(1) if m else None,
                    'text': lng.strip_speaker_wrap(raw)})
    return out


def step_cost(ws_item, cc_item):
    """单条 1:1 配对的代价。`ws_item` 是 ws2_pool 的槽位 dict。"""
    ws_set = {ws_item['spk']} if ws_item['spk'] else set()
    if cc_item['named'] and not cc_item['spk']:
        return C_MISMATCH           # CCS 有 [名字] 但不在映射表 —— 无法判定，视为可疑
    cc_set = cc_item['spk']
    if ws_set and cc_set:
        return C_MATCH if (ws_set & cc_set) else C_MISMATCH
    if not ws_set and not cc_set:
        return C_NARR
    return C_MISMATCH               # 一方旁白另一方有名 —— 明确冲突


def align(ws_items, ccs_items):
    """编辑距离 DP，返回 [(ws_idx, [ccs_idx...])]（单调，可能含空列表）。"""
    nw, nc = len(ws_items), len(ccs_items)
    INF = float('inf')
    dp = [[INF] * (nc + 1) for _ in range(nw + 1)]
    bt = [[None] * (nc + 1) for _ in range(nw + 1)]
    dp[0][0] = 0
    for i in range(nw + 1):
        for j in range(nc + 1):
            cur = dp[i][j]
            if cur == INF:
                continue
            if i < nw and j < nc:                    # 1:1
                c = cur + step_cost(ws_items[i], ccs_items[j])
                if c < dp[i + 1][j + 1]:
                    dp[i + 1][j + 1] = c
                    bt[i + 1][j + 1] = (i, j, [j])
            if (i < nw and j + 1 < nc
                    and allow_insert(ws_items[i], ccs_items[j], ccs_items[j + 1])):
                c = cur + C_MERGE + step_cost(ws_items[i], ccs_items[j])
                if c < dp[i + 1][j + 2]:
                    dp[i + 1][j + 2] = c
                    bt[i + 1][j + 2] = (i, j, [j, j + 1])
            if i + 1 < nw and j < nc:                # 2:1（拆分）
                c = (cur + C_SPLIT
                     + min(step_cost(ws_items[i], ccs_items[j]),
                           step_cost(ws_items[i + 1], ccs_items[j])))
                if c < dp[i + 2][j + 1]:
                    dp[i + 2][j + 1] = c
                    bt[i + 2][j + 1] = (i, j, ('split', [j]))
            if i < nw:                               # ws2 多出（需翻译）
                c = cur + C_WS_ONLY
                if c < dp[i + 1][j]:
                    dp[i + 1][j] = c
                    bt[i + 1][j] = (i, j, [])
            if j < nc:                               # CCS 多出（不该发生）
                c = cur + C_CCS_ONLY
                if c < dp[i][j + 1]:
                    dp[i][j + 1] = c
                    bt[i][j + 1] = (i, j, None)
    # 回溯
    out, i, j = [], nw, nc
    while i or j:
        prev = bt[i][j]
        if prev is None:
            break
        pi, pj, cc = prev
        if isinstance(cc, tuple) and cc[0] == 'split':
            out.append((pi, cc[1]))
            out.append((pi + 1, cc[1]))          # 两个池位共享该 CCS 行
        elif cc:
            out.append((pi, cc))
        i, j = pi, pj
    out.reverse()
    return out


def ctrl_suffix(t):
    """取文本末尾的控制符串（如 `%K%P`、`%N`）。"""
    m = CTRL_TAIL.search((t or '').strip())
    return m.group(0) if m else ''


def split_ctrl(ws_text, n):
    """原句控制符拆给 n 行：前 n-1 行用 `%K`（否则前一行会被立刻覆盖），末行用原样。

    实测原句控制符几乎恒为 `%K%P`；若不含 `%K`（如 `%N`），前面几行不给控制符。
    """
    s = ctrl_suffix(ws_text)
    if n <= 1:
        return [s]
    head = '%K' if '%K' in s else ''
    return [head] * (n - 1) + [s]


def rewrite_0f_strid(orig, new_strids):
    """把 `0f` 指令里每条选项条目的 strid 换成新值（其余字节原样）。

    `0f <u8 count>` + count × `<u16 strid> <text> 00 00 <u16 label> <jump>`，
    jump 为 `06 <u32>`（5B）或 `07 <name> 00`。
    """
    out = bytearray(orig[:2])
    p = 2
    for ns in new_strids:
        out += struct.pack('<H', ns)
        p += 2
        e = orig.index(b'\x00\x00', p)          # 文本终止 + 分隔
        out += orig[p:e + 2]
        p = e + 2
        out += orig[p:p + 2]                    # label
        p += 2
        if orig[p] == 0x06:
            out += orig[p:p + 5]
            p += 5
        else:
            e2 = orig.index(b'\x00', p + 1)
            out += orig[p:e2 + 1]
            p = e2 + 1
    return bytes(out)


def process(members, by_name, stem, zh2en):
    """对单个脚本算出 (新 ws2 字节, 新 lng 字节, 统计)。

    * CCS 有 n 行而 ws2 只有 1 句 → 插入 (n-1) 组 `15`+`14`，使两侧 1:1；
    * 插入使脚本变长 → 重算 `06`(+1) / `01 mode=0x85`(+12) 的绝对偏移；
    * `14` 的 id 与 `0f` 的 strid 都等于**池槽位序**，插入后整体顺延；
    * 选项（`0f`）槽位与 `%N`/`%P` 控制槽位不参与 CCS 对齐，沿用现有 lng。
    """
    raw = by_name[stem + '.ws2']
    dec = ws2.decode(raw)
    ins = ws2disasm.disassemble(dec)
    cc = ccs_meta(load_ccs(stem[:-3]), zh2en)
    pool = ws2_pool(raw)
    old_lng = lng.parse_lng(by_name[stem + '.lng'])
    body_idx = [k for k, s in enumerate(pool) if s['kind'] == 'dlg']
    lut = {}
    for bi, ccidx in align([pool[k] for k in body_idx], cc):
        lut[body_idx[bi]] = (pool[body_idx[bi]]['text'], ccidx)

    out = bytearray()
    old2new, patches = {}, []
    pool_i, new_id, n_ins, n_keep, n_split = -1, 0, 0, 0, 0
    for i in ins:
        old2new[i.offset] = len(out)
        if i.opcode == 0x14:
            pool_i += 1
            orig = dec[i.offset:i.offset + i.size]
            ent = lut.get(pool_i)
            splitting = bool(ent and len(ent[1]) > 1)
            if splitting:
                # 被拆开的**前半句**：把原句 14 的控制符换成 `%K`（不换页），
                # 否则二次运行时该行按 1:1 处理会取回原 `%K%P`，与首次结果不一致
                # （幂等性）。正文本身会被 lng 覆盖，改它无副作用。
                sfx = ctrl_suffix(ent[0])
                head = '%K' if '%K' in sfx else ''
                body = ent[0][:len(ent[0]) - len(sfx)] if sfx else ent[0]
                blob = (body + head).encode('cp932', 'replace')
                orig = (b'\x14' + struct.pack('<H', new_id) + b'\x00\x00'
                        + b'char\x00' + blob + b'\x00\x00')
                out += ws2.encode(orig)
            else:
                out += ws2.encode(orig[:1] + struct.pack('<H', new_id) + orig[3:])
            new_id += 1
            if splitting:
                n_split += 1
                for cj in ent[1][1:]:
                    spk = cc[cj]['spk']
                    pre = ('%LC' + sorted(spk)[0]).encode('ascii') if spk else b''
                    out += ws2.encode(b'\x15' + pre + b'\x00\x00')
                    # 兜底正文（正常会被 lng 覆盖）。必须让它在**结构上不可能**
                    # 触发 allow_insert，否则本函数二次运行会重复插入、破坏幂等：
                    #   * 去掉行首 `%` —— 否则 pct 判据命中；
                    #   * 截断到 MERGE_MIN_LEN 以下 —— 否则 long 判据命中。
                    # 但**必须保留控制符后缀**（%K%P）：二次运行时 build_entry 用
                    # ctrl_suffix(兜底) 取控制符，截掉它会让 lng 缺 %K%P。
                    body = (cc[cj]['text'] or '').lstrip('%')
                    sfx = ctrl_suffix(ent[0])
                    # 截到**严格小于** MERGE_MIN_LEN（含控制符），否则 len 恰好等于
                    # 阈值时 long 判据仍命中，二次运行又合并。
                    if len(body) + len(sfx) >= MERGE_MIN_LEN:
                        body = body[:MERGE_MIN_LEN - len(sfx) - 1]
                    txt = (body + sfx).encode('cp932', 'replace')
                    out += ws2.encode(b'\x14' + struct.pack('<H', new_id)
                                      + b'\x00\x00char\x00' + txt + b'\x00\x00')
                    new_id += 1
                    n_ins += 1
            continue
        if i.opcode == 0x0f:
            n = i.fields.get('count', 0)
            out += ws2.encode(rewrite_0f_strid(dec[i.offset:i.offset + i.size],
                                               range(new_id, new_id + n)))
            pool_i += n
            new_id += n
            continue
        if i.opcode == 0x06:
            patches.append((len(out) + 1, i.fields['target']))
        elif i.opcode == 0x01 and i.fields.get('mode') == 0x85:
            patches.append((len(out) + 12, i.fields['b']))
        out += raw[i.offset:i.offset + i.size]
    for at, old_t in patches:
        nt = old2new.get(old_t)
        if nt is None:
            raise SystemExit('%s: 绝对偏移 %d 不在指令边界' % (stem, old_t))
        out[at:at + 4] = ws2.encode(struct.pack('<I', nt))

    new_raw = bytes(out)

    # --- 新 lng：按新池槽位序 1:1 ---
    new_lng = []
    for k, s in enumerate(pool):
        if s['kind'] != 'dlg':
            new_lng.append(old_lng[k] if k < len(old_lng) else s['text'])
            n_keep += 1
            continue
        ent = lut.get(k)
        if ent is None or not ent[1]:
            new_lng.append(old_lng[k] if k < len(old_lng) else s['text'])
            n_keep += 1
            continue
        ws_text, idxs = ent
        ctrls = split_ctrl(ws_text, len(idxs))
        for cj, cfx in zip(idxs, ctrls):
            new_lng.append((cc[cj]['text'] or '') + cfx)
    return new_raw, lng.encode_lng(new_lng), dict(
        n_pool=len(pool), n_new=len(new_lng), ins=n_ins,
        split=n_split, keep=n_keep, size=len(raw), size_new=len(new_raw))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--write', action='store_true', help='写入（备份+回读校验）')
    ap.add_argument('--stem', help='只看某个脚本的详情')
    ap.add_argument('--limit', type=int, default=40)
    ap.add_argument('--dry', action='store_true', help='重建演练（与 --write 同样计算，但不落盘）')
    args = ap.parse_args()

    members = arcbuild.read_raw(RIO)
    by_name = {n.decode('utf-16le'): d for n, d in members}
    zh2en = load_name_map(by_name)

    if args.write or args.dry:
        rows = []
        for name in sorted(by_name):
            if not name.endswith('_en.ws2'):
                continue
            stem = name[:-4]
            if stem + '.lng' not in by_name:
                continue
            if not load_ccs(stem[:-3]):
                continue
            nw, nl, st = process(members, by_name, stem, zh2en)
            changed = (nw != by_name[name]) or (nl != by_name[stem + '.lng'])
            rows.append((stem, st, changed, nw, nl))
        ch = [r for r in rows if r[2]]
        print('扫描 %d 个脚本；需要改动 %d' % (len(rows), len(ch)))
        print('  插入行合计 %d；拆分段合计 %d；沿用现有译文 %d'
              % (sum(r[1]['ins'] for r in rows), sum(r[1]['split'] for r in rows),
                 sum(r[1]['keep'] for r in rows)))
        print()
        print('=== 插入最多的前 20 ===')
        ch.sort(key=lambda r: -r[1]['ins'])
        for stem, st, _, _, _ in ch[:20]:
            print('  %-20s 池位 %-5d -> %-5d (+%d)  拆分 %-3d 沿用 %d'
                  % (stem, st['n_pool'], st['n_new'], st['ins'], st['split'], st['keep']))
        if not args.write:
            print()
            print('[只诊断] 未写入。确认无误后加 --write 落盘。')
            return 0
        if BACKUP.exists():
            print('[备份] 已存在 %s（保留原样）' % BACKUP)
        else:
            arcbuild.write_arc(members, BACKUP)
            print('[备份] %s -> %s' % (RIO, BACKUP))
        newmap = {}
        for stem, st, chg, nw, nl in ch:
            newmap[stem + '.ws2'] = nw
            newmap[stem + '.lng'] = nl
        arcbuild.write_arc([(n, newmap.get(n.decode('utf-16le'), d))
                            for n, d in members], RIO)
        back = {n.decode('utf-16le'): d for n, d in arcbuild.read_raw(RIO)}
        bad = [k for k, v in newmap.items() if back.get(k) != v]
        if bad:
            raise SystemExit('[失败] 回读不一致：%s' % bad[:5])
        cnt, size, _ = arcbuild.verify(RIO)
        print('[回读] %s %d 成员 %d 字节 verify OK' % (RIO, cnt, size))
        print('[完成] 已重建 %d 个脚本的 ws2+lng；复跑应为 0 处改动。' % len(ch))
        return 0

    todo = [s for s in args.stem.split(',')] if args.stem else None
    report, needs_tr = [], []
    for name in sorted(by_name):
        if not name.endswith('_en.ws2'):
            continue
        stem = name[:-4]
        if stem + '.lng' not in by_name:
            continue
        if todo and stem not in todo:
            continue
        raw_lines = load_ccs(stem[:-3])
        if not raw_lines:
            continue
        cc = ccs_meta(raw_lines, zh2en)
        pool = ws2_pool(by_name[name])
        L = lng.parse_lng(by_name[stem + '.lng'])

        ctrl_idx = [k for k, (_, _, c) in enumerate(pool) if c]
        body_idx = [k for k, (_, _, c) in enumerate(pool) if not c]
        ws_items = [pool[k] for k in body_idx]
        mapping = align(ws_items, cc)

        # 统计
        merged = sum(1 for _, ccidx in mapping if len(ccidx) > 1)
        untr = [bi for bi, ccidx in mapping if not ccidx]
        split = sum(1 for _, ccidx in mapping if ccidx) - merged - len(mapping) + len(untr)
        covered = {k for _, ccidx in mapping for k in ccidx}
        report.append(dict(stem=stem, n_pool=len(pool), n_lng=len(L),
                           n_ccs=len(cc), merged=merged,
                           untr=[body_idx[bi] for bi in untr],
                           ccs_unused=[k + 1 for k in range(len(cc)) if k not in covered]))

    print('扫描 %d 个脚本（DP 对齐）' % len(report))
    bad_cnt = [r for r in report if r['n_pool'] != r['n_lng']]
    with_untr = [r for r in report if r['untr']]
    with_merge = [r for r in report if r['merged']]
    with_unused = [r for r in report if r['ccs_unused']]
    print('  lng 条数 != 池位 : %d' % len(bad_cnt))
    print('  含 1:2 合并段    : %d' % len(with_merge))
    print('  含未对齐(待译)句 : %d  共 %d 句'
          % (len(with_untr), sum(len(r['untr']) for r in report)))
    print('  CCS 行未被使用   : %d' % len(with_unused))
    print()

    if args.stem:
        for name in sorted(by_name):
            if not name.endswith('_en.ws2'):
                continue
            stem = name[:-4]
            if stem not in (args.stem.split(',')):
                continue
            raw_lines = load_ccs(stem[:-3])
            if not raw_lines:
                continue
            cc = ccs_meta(raw_lines, zh2en)
            pool = ws2_pool(by_name[name])
            body_idx = [k for k, (_, _, c) in enumerate(pool) if not c]
            body = [pool[k] for k in body_idx]
            mapping = align(body, cc)
            print('=== %s  池位 %d / CCS %d ===' % (stem, len(pool), len(cc)))
            for bi, ccidx in mapping:
                k = body_idx[bi]
                spk, txt, _ = body[bi]
                if len(ccidx) > 1:
                    print('  [合并] 池位%-4d %-8s %s' % (k, spk or '—', txt[:56]))
                    for c in ccidx:
                        print('         CCS[%3d] %s' % (c + 1, cc[c]['text'][:56]))
                elif not ccidx:
                    print('  [待译] 池位%-4d %-8s %s' % (k, spk or '—', txt[:56]))
        return 0

    print('=== 待译句最多的脚本（前 %d）===' % args.limit)
    with_untr.sort(key=lambda r: -len(r['untr']))
    for r in with_untr[:args.limit]:
        print('  %-20s 待译 %-4d / 池位 %-5d' % (r['stem'], len(r['untr']), r['n_pool']))
    print()
    print('=== CCS 行未被使用（中文被丢，需查）===')
    for r in with_unused[:10]:
        print('  %-20s 未用 %-4d / CCS %-5d  行号 %s'
              % (r['stem'], len(r['ccs_unused']), r['n_ccs'], r['ccs_unused'][:12]))
    return 0


if __name__ == '__main__':
    sys.exit(main())
