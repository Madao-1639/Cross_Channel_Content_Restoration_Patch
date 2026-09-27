"""CROSS†CHANNEL .lng localization container codec.

Layout (verified byte-exact on CNR0005_en.lng, 90/90 entries decode to
readable Chinese with correct %K%P markers):
    u32                 count
    u16 * count         per-string byte length (includes trailing NUL pair)
    bytes               count strings, back-to-back, no padding

Each string is UTF-16LE XORed with 0x2C. Unlike ws2, the container itself
is NOT rot6-obfuscated — only the payload bytes are XORed.

Matching to the paired .ws2 is POSITIONAL: the engine replaces the Nth
dialogue (0x14) instruction encountered during script playback with the
Nth entry in the .lng file, in encounter order. There is no dialogue-id
field in the .lng format. This means inserting/removing dialogues in the
.ws2 shifts every subsequent .lng entry out of alignment.
"""
import re
import struct
from pathlib import Path

XOR = 0x2C
_TAB = bytes(c ^ XOR for c in range(256))

# CCS 行格式：>1●dddd●[说话人]"文本" 或 >1●dddd●文本（无说话人时不含方括号引号包裹）
_CCS_LINE = re.compile(r'^>1.(\d+).(.+)$')
# 引号成对表：汉化组 CCS 里 `“…”` 与 `‘…’` 都在用（后者是一般叙述里的引语），
# 只认前者会漏掉一整类包裹（例：`[樱庭]‘用这台ＮＥＷ自行车称霸山顶。’`）。
_QUOTE_PAIRS = [('“', '”'), ('‘', '’'), ('「', '」'), ('『', '』'), ('"', '"')]
_SPEAKER_PREFIX = re.compile(r'^\[[^\]]+\]')
# 尾部控制符 = `%K`/`%P`/`%N` 的任意组合。**带捕获组**：`fix_tail` 要把命中的串原样接到译文尾。
_TAIL = re.compile(r'((?:%[A-Za-z])+)\s*$')
_SPK_HEAD = re.compile(r'^\[[^\]]+\]\s*')     # 行首的 `[说话人]` 残留


def parse_ccs(path):
    """解析汉化组 CCS 文件（UTF-16LE），返回 {行号: 中文文本} dict。

    验证依据（CNR0005_en 还原，2026-09-09）：CCS 第 N 行的 >1 中文文本，
    与 WS2 的第 N-1 个 0x14 对话按脚本内顺序位置对应（见 doc/localization.md
    "位置对应"）。行号本身即 CCS 编号，不做任何偏移换算，由调用方决定
    落在哪一段 ws2 对话流。
    """
    with open(path, 'r', encoding='utf-16le') as f:
        content = f.read()
    entries = {}
    for line in content.split('\n'):
        m = _CCS_LINE.match(line.strip())
        if m:
            entries[int(m.group(1))] = m.group(2).strip()
    return entries


def parse_ccs_both(path):
    """解析汉化组 CCS（UTF-16LE），返回 `({行号: 日文}, {行号: 中文})` —— CCS 两列都带 `[说话人]` 前缀。

    与 `parse_ccs` 的区别：那个只取中文列（`>1`），本函数**连原版日文列也取**（`>0`）——
    写盘器要用日文列补插入格的尾标记、并核对「这一格取的是哪条源行」。
    """
    content = Path(path).read_text(encoding='utf-16le')
    jp, zh = {}, {}
    for line in content.split('\n'):
        s = line.strip()
        if s.startswith('>0'):
            m = re.match(r'^>0.(\d+).(.*)$', s)
            if m:
                jp[int(m.group(1))] = m.group(2).strip()
        elif s.startswith('>1'):
            m = re.match(r'^>1.(\d+).(.*)$', s)
            if m:
                zh[int(m.group(1))] = m.group(2).strip()
    return jp, zh


def strip_speaker_wrap(text):
    """整行为 `[说话人]<引号>文本<引号>` 时去掉说话人前缀与外层引号，只留纯文本。

    验证依据：res303 的 CNR0005_en.lng 与 CCC0000.CCS 逐行比对，169 条中
    30 条属于此整行包裹格式，lng 里全部去掉了说话人前缀和外层引号；句中
    出现的引号（如"随便你"）不受影响，因为它不是整行包裹。说话人身份由
    WS2 的 `0x15 %LC<Name>` 单独承载，不应在文本正文重复。

    引号成对匹配（`“”`/`‘’`/`「」`/`『』`/`""`）：CCS 里两种引号混用，
    只认 `“”` 会漏掉 `[樱庭]‘用这台ＮＥＷ自行车称霸山顶。’` 这类包裹。
    """
    m = _SPEAKER_PREFIX.match(text)
    if not m:
        return text
    rest = text[m.end():]
    for lq, rq in _QUOTE_PAIRS:
        if len(rest) >= 2 and rest[0] == lq:
            if rest[-1] == rq:
                return rest[1:-1]                   # 整行包裹（原规则）
            e = rest.find(rq, 1)
            if e > 0:
                # **引号后还带注释**（`※…`／`（译注…）`）的：只剥引号、**注释原样留下**。
                # 原先要求「整行恰为 `[名]“…”`」，这类行剥不掉，于是 `[太一]` 留在正文里 ——
                # 而该格的名字框**已经**显示同一个人名，项目规矩是「说话人身份由 `%LC`
                # 单独承载，不应在正文重复」。实测全库 68 格残留（2026-09-15 评审发现）。
                return rest[1:e] + rest[e + 1:]
            # **开引号没有闭合** —— 民汉源文件里确实有这样写的行
            # （如 `[太一]“我承认，……现实问题。`）。剥掉前缀与那个开引号即可，
            # 不为它凭空补一个闭合引号。
            return rest[1:]
    return text


def strip_speaker_only(text):
    """只去掉行首 `[说话人]` 前缀，**保留外层引号** —— 写盘用。

    ⚠️ **引号是原版正文内容，不是包裹记号**：原版 WSC 的文本字段里就带 `「」`
    （实测 `CCD1001` 正文 2261 行中 1294 行带），民汉 CCS 作 `“”`/`‘’`。
    `strip_speaker_wrap` 把「说话人前缀 + 外层引号」一起去掉，会让成品中文丢掉
    2 万余格的引号（官方中文带引号的 20,560 格，产物只剩 345）。
    说话人身份确实由 `%LC` 承载、正文不该重复，但**引号该留**。

    审计/比对脚本继续用 `strip_speaker_wrap`（那里只关心正文，剥引号便于匹配）。
    """
    m = _SPEAKER_PREFIX.match(text)
    if not m:
        return text
    return text[m.end():]


def has_speaker_prefix(text):
    """该行是否以 `[说话人]` 开头（决定 `span` 的标定基准，见 `slice_span`）。"""
    return bool(_SPEAKER_PREFIX.match(text or ''))


def slice_span(text, a, b, wrapped=False):
    """按 `text_map.json` 的 `span` 切一段（一格拆半行）。

    ⚠️ `span` 的标定基准是**旧 `strip_speaker_wrap` 的产物**，而它只对带 `[说话人]`
    前缀的行剥外层引号。所以：

    - `wrapped=True`（原行有 `[说话人]`，旧代码把外层引号剥了）⇒ span 落在**无引号**串上，
      故先摘引号再切，并把引号**按原位置**插回该片（开引号随片首，闭引号插在它原来的位置 ——
      如 `“台词”\\n※注释` 被切成两片时，闭引号该落在第一片的句末而非片尾）；
    - `wrapped=False`（原行没有 `[说话人]`，旧代码原样保留引号）⇒ span 就落在**带引号**串上，
      直接切，行为与旧版完全一致。
    """
    if not wrapped:
        return text[a:b]
    for lq, rq in _QUOTE_PAIRS:
        if not text.startswith(lq):
            continue
        e = text.find(rq, 1)
        # 民汉 CCS 里确实有「开引号没闭合」的行（如 `[太一]“可惜能再奔放点就好了。像是…`）。
        # 旧 `strip_speaker_wrap` 对这类行会把开引号一并剥掉，span 就是在这个串上标的 ——
        # 故此处同样按「剥掉开引号」的串切；开引号只在片首（`a == 0`）补回。
        s = text[1:e] + text[e + 1:] if e > 0 else text[1:]
        cp = e - 1 if e > 0 else None        # 闭引号在 s 中的位置（无闭引号则 None）
        seg = s[a:b]
        if a == 0:
            seg = lq + seg
        # ⚠️ 区间必须是 `a < cp <= b`（**右端取等**）。原先写 `a <= cp < b`，两端都错：
        #   · `cp == b`（闭引号**恰在片尾**，最常见的是"整行一步到片尾"或"切点压在闭引号上"）：
        #     旧条件不成立 ⇒ **闭引号永远补不回来** —— 全库实测 41 处台词两页下去开引号不闭合；
        #   · `cp == a`（切点**恰在闭引号之后**）：旧条件成立 ⇒ 把闭引号挪到**下一片开头**，
        #     与本文档上面的意图（"闭引号该落在第一片的句末"）相反。
        # 改后每个位置至多命中一片（片互不重叠且有序）⇒ 不会重复补。
        if cp is not None and a < cp <= b:
            seg = seg[:cp - a + (1 if a == 0 else 0)] + rq + seg[cp - a + (1 if a == 0 else 0):]
        return seg
    return text[a:b]


def fix_tail(zh, en):
    """把英文末尾的控制符补到译文末尾。

    ⚠️ **`op=text` 的 `zh` 不会像 `op=ccs` 那样继承原文尾巴，必须手补**
    （2026-09-15 评审发现）：实测 17 格的中文**一个控制符都没有**，而原文以 `%K` 结尾
    （等待点击）—— 下一格带 `%K` 会把这一格的文字**直接顶掉**，玩家根本读不到。
    命中的全是最显眼的格：`CCC0007` 的场景收尾「【完】」、`CCC4025` 的日记日期头
    「○月×日」、`CCB1004` 的名单枚举。

    ⚠️ `en` 取不到时**什么都不补** —— 这正是 2026-09-27 尾标记整体丢失的路径
    （见 `apply_text_map.en_of_from`）。所以调用方必须保证 `en` 来自**输入归档本身**、
    而不是任何派生缓存。
    """
    zh = zh or ''
    if not en or _TAIL.search(zh):
        return zh
    m = _TAIL.search(en)
    return (zh + m.group(1)) if m else zh


def normalize_zh(zh, en):
    """体例归一化 —— **只改写法，不改内容**。

    1. **真实换行 → 字面 `\\n`**：项目惯例是字面（脚本里就是反斜杠 + n），
       落裸 CR/LF 若引擎不认会显示成方块。
    2. **行首 `[说话人]` 残留 → 剥掉**：说话人身份由名字框（`%LC`）承载，正文不重复。

    ⚠️ **不再动引号**。原先有两条去引号的规则，都是错的：
      - 「整句引号包裹 → 去掉」（称既有译文一律不带外层引号）；
      - 「英文槽不含 `\\d` 就剥掉中文外层引号」（把 `\\d` 当引号标记的真值）。
    二者都把**官方中文的正常引号**误判成本项目自加的记号，实测丢掉 **20,215 格**
    （官方中文带引号的 20,560 格 → 产物只剩 345）。引号是原版正文内容
    （原版 WSC 有 `「」`，官方中文作 `“”`/`‘’`），**以中文为准保留**。
    `en` 参数保留只为不改调用点，现已不用。
    """
    z = zh or ''
    z = z.replace('\r\n', '\n').replace('\r', '\n')
    z = z.replace('\n', chr(92) + 'n')          # 真实换行 → **字面** `\n`（项目惯例）
    spk = _SPK_HEAD.match(z)
    if spk:
        z = z[spk.end():]
    return z


def parse_lng(raw):
    """Return the list of decoded strings (trailing NUL stripped)."""
    count = struct.unpack_from('<I', raw, 0)[0]
    lens = struct.unpack_from('<%dH' % count, raw, 4)
    off = 4 + 2 * count
    if off + sum(lens) != len(raw):
        raise ValueError('lng length table does not cover payload')
    out = []
    for length in lens:
        chunk = raw[off:off + length]
        off += length
        out.append(chunk.translate(_TAB).decode('utf-16le').rstrip('\0'))
    return out


def encode_lng(strings):
    """Inverse of parse_lng."""
    blobs = [(s + '\0').encode('utf-16le').translate(_TAB) for s in strings]
    out = bytearray(struct.pack('<I', len(blobs)))
    for blob in blobs:
        if len(blob) > 0xffff:
            raise ValueError('string exceeds u16 length field')
        out += struct.pack('<H', len(blob))
    for blob in blobs:
        out += blob
    return bytes(out)
