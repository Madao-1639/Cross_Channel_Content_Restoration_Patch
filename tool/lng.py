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

XOR = 0x2C
_TAB = bytes(c ^ XOR for c in range(256))

# CCS 行格式：>1●dddd●[说话人]"文本" 或 >1●dddd●文本（无说话人时不含方括号引号包裹）
_CCS_LINE = re.compile(r'^>1.(\d+).(.+)$')
# 引号成对表：汉化组 CCS 里 `“…”` 与 `‘…’` 都在用（后者是一般叙述里的引语），
# 只认前者会漏掉一整类包裹（例：`[樱庭]‘用这台ＮＥＷ自行车称霸山顶。’`）。
_QUOTE_PAIRS = [('“', '”'), ('‘', '’'), ('「', '」'), ('『', '』'), ('"', '"')]
_SPEAKER_PREFIX = re.compile(r'^\[[^\]]+\]')


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
        if len(rest) >= 2 and rest[0] == lq and rest[-1] == rq:
            return rest[1:-1]
    return text


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
