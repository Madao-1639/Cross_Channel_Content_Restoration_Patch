"""PNA（AdvHD 分层图像）读写器。

PNA 是 AdvHD 引擎的分层图像格式，文件后缀 `.pna`。原先只读（Steam 原档一律原样保留）；
2026-10 起支持**重序列化**（`serialize`）：SysGraphic.arc 的 UI 汉化按「同名图层换图」
实施 —— 每个非空数据块本就是一张完整 PNG，换图只需改该图层的数据块与记录表的
宽高/大小字段（见 doc/localization.md「SysGraphic.arc」）。剧情资源（立绘/蒙版）仍不写。

⚠️ 写回的**唯一硬不变量**：记录表的 w/h 与 `data` 内嵌 PNG 的 IHDR 一致（原档即如此，
换图后必须维持）。`scanned=True`（记录表未覆盖全文件）的 PNA **不支持**重序列化：
数据区尾部的未解释字节机制未查明，重排数据区可能破坏它 —— 报 `PnaError`，不猜。

文件布局（全部小端序，逆向结论，见 doc/engine-mechanics.md「PNA 二进制布局」）：

    +0x00  char[4]  "PNAP"
    +0x04  u32      表大小字段 = 12 + 44 * layer_count（仅校验用，非偏移）
    +0x08  u32      画布宽
    +0x0c  u32      画布高
    +0x10  u32      图层记录数 layer_count
    +0x14  u32      未知（0 或 1）
    +0x18  s32      未知（-1 / 1 / layer_count-1）
    +0x1c  u32      未知
    +0x20  u32      未知
    +0x24  记录区   layer_count 条记录，步长 40 字节，
                    最后一条只有 24 字节（无尾随 16 字节）

记录字段（相对记录起点）：

    +0x00  u32      图层宽
    +0x04  u32      图层高
    +0x08  u32      恒 0
    +0x0c  u32      通常是 0
    +0x10  f32      恒 1.875（空图层为 0.0）
    +0x14  u32      数据块字节数（0 表示该图层无数据）
    +0x18  u32      u0：动画相位标记。0 = 基础帧；1/2 = 子图层组边界哨兵
    +0x1c  u32      相位计数器（同组内递减）
    +0x20  s32      图层左上角 x
    +0x24  s32      图层左上角 y

⚠️ **最后一条记录只有 24 字节**：+0x18 起的 u0/phase/x/y 四个字段**都不存在**
（「步长 40、最后一条 24」指的就是少了这尾随 16 字节）。旧实现对最后一条也读
u0/phase，实际读到的是数据区里 PNG 的头 8 字节 —— 只读不写时无害，重序列化时
就会把假字段写回（2026-10 往返回归抓出，现按「最后一条无这四个字段」处理）。

数据块自 0x24 + 40*layer_count - 16 起顺序排列，**每个非空数据块都是一张完整的
PNG**（含签名与 IEND），因此「PNA → PNG」是切片而非重绘，可逐字节保真。

注意：`SYS_GalleryBrowser.pna` 与 `Sys_Msw.pna` 的记录表算出的数据长度小于文件长度，
即文件尾部还有记录表未覆盖的数据块。这两个文件改用签名扫描（`png_spans`）取全。
"""
import struct
from pathlib import Path

MAGIC = b'PNAP'
PNG_SIG = b'\x89PNG\r\n\x1a\n'

HEADER_SIZE = 0x24
ENTRY_SIZE = 40
LAST_ENTRY_SIZE = 24


class PnaError(ValueError):
    pass


class Layer(object):
    """一个图层。`data` 为 None 表示该图层无数据块（空图层）。

    `zero` / `unk` 是记录 +0x08 / +0x0c 的原始字段（恒 0 / 通常是 0）—— serialize
    逐字段重建记录表，必须带着它们才能逐字节保真。"""

    __slots__ = ('index', 'width', 'height', 'zero', 'unk', 'u0', 'phase', 'x', 'y',
                 'opacity', 'offset', 'size', 'data')

    def __init__(self, index, width, height, zero, unk, u0, phase, x, y, opacity,
                 offset, size, data):
        self.index = index
        self.width = width
        self.height = height
        self.zero = zero
        self.unk = unk
        self.u0 = u0
        self.phase = phase
        self.x = x
        self.y = y
        self.opacity = opacity
        self.offset = offset
        self.size = size
        self.data = data

    def __repr__(self):
        return '<Layer %d %dx%d @(%s,%s) size=%d%s>' % (
            self.index, self.width, self.height, self.x, self.y, self.size,
            '' if self.data else ' empty')


class Pna(object):
    __slots__ = ('path', 'width', 'height', 'count', 'layers', 'data_start', 'scanned',
                 'holes', 'table_size_field', 'header_tail')

    def __init__(self, path, width, height, count, layers, data_start, scanned, holes=(),
                 table_size_field=None, header_tail=b''):
        self.path = path
        self.width = width            # 画布宽
        self.height = height          # 画布高
        self.count = count            # 记录表条目数
        self.layers = layers
        self.data_start = data_start
        self.scanned = scanned        # True = 记录表未覆盖全文件，图层由签名扫描得出
        self.holes = holes            # 数据区中不含 PNG 的字节区间 [(start, size), ...]
        # 头部的原始字段（serialize 逐字节重建头部用；scanned 件不给）
        self.table_size_field = table_size_field   # +0x04 的原始 u32（仅校验用，非偏移）
        self.header_tail = header_tail             # +0x14..0x24 的 4 个未知 u32 原文

    @property
    def png_layers(self):
        return [l for l in self.layers if l.data is not None]


def _png_span_size(blob, start):
    """从签名处走 PNG chunk 链到 IEND，返回整块长度；结构不合法返回 None。"""
    p = start + 8
    while p + 8 <= len(blob):
        length, = struct.unpack_from('>I', blob, p)
        ctype = blob[p + 4:p + 8]
        p += 12 + length
        if p > len(blob):
            return None
        if ctype == b'IEND':
            return p - start
    return None


def png_spans(blob, start=0):
    """扫描内嵌 PNG 的 (offset, size)，按出现顺序。size 含签名与 IEND。"""
    spans = []
    pos = start
    while True:
        i = blob.find(PNG_SIG, pos)
        if i < 0:
            break
        size = _png_span_size(blob, i)
        if size is None:
            pos = i + 8
            continue
        spans.append((i, size))
        pos = i + size
    return spans


def _scanned_layers(blob, data_start, path):
    """记录表覆盖不全时的回退：图层全部由签名扫描得出。

    少数 PNA（`SYS_GalleryBrowser.pna`、`Sys_Msw.pna`）在记录表描述的最后一个数据块
    之后还有一段既非 PNG 也无表结构的字节，随后才是若干张 PNG（数量比记录表多）。
    这些额外图层的来源机制未查明，这里按出现顺序全部取出，并把跳过的字节区间记在
    `Pna.holes` 里，不静默丢弃。
    """
    spans = png_spans(blob, data_start)
    if not spans:
        raise PnaError('%s: 数据区未找到任何 PNG' % path)
    if spans[0][0] != data_start:
        raise PnaError('%s: 数据区起点 0x%x 处不是 PNG' % (path, data_start))
    holes = []
    layers = []
    end = data_start
    for k, (off, size) in enumerate(spans):
        if off > end:
            holes.append((end, off - end))
        end = off + size
        w, h = struct.unpack_from('>II', blob, off + 16)
        layers.append(Layer(k, w, h, None, None, None, None, None, None, None,
                            off, size, blob[off:off + size]))
    if end < len(blob):
        holes.append((end, len(blob) - end))
    return layers, holes


def read(path):
    """解析 PNA 文件，返回 Pna。"""
    return parse(Path(path).read_bytes(), path=str(path))


def parse(blob, path='<bytes>'):
    if blob[:4] != MAGIC:
        raise PnaError('%s: 不是 PNA 文件（magic=%r）' % (path, blob[:4]))
    table_size_field, width, height, count = struct.unpack_from('<IIII', blob, 4)
    data_start = HEADER_SIZE + ENTRY_SIZE * (count - 1) + LAST_ENTRY_SIZE if count > 0 else HEADER_SIZE
    if count <= 0 or data_start > len(blob):
        raise PnaError('%s: 记录表越界（layer_count=%d）' % (path, count))

    layers = []
    off = data_start
    for k in range(count):
        base = HEADER_SIZE + ENTRY_SIZE * k
        w, h, zero, unk, opacity, size = struct.unpack_from('<IIIIfI', blob, base)
        # ⚠️ 最后一条记录只有 24 字节：**没有 u0/phase，也没有 x/y**（「无尾随 16 字节」
        #   指的就是这 16 字节）。旧实现对最后一条也读 base+0x18 的 u0/phase —— 实际读到的
        #   是数据区里 PNG 的头 8 字节；只读不写时无害，重序列化时就会把它当字段写回去
        #   （2026-10 往返回归抓出）。u0/phase/x/y 对最后一条一律记 None。
        if k < count - 1:
            u0, phase = struct.unpack_from('<ii', blob, base + 0x18)
            x, y = struct.unpack_from('<ii', blob, base + 0x20)
        else:
            u0 = phase = x = y = None
        data = None
        if size:
            if off + size > len(blob):
                raise PnaError('%s: 图层 %d 数据越界（%d+%d > %d）'
                               % (path, k, off, size, len(blob)))
            data = blob[off:off + size]
            if data[:8] != PNG_SIG:
                raise PnaError('%s: 图层 %d 的数据块不是 PNG' % (path, k))
            off += size
        layers.append(Layer(k, w, h, zero, unk, u0, phase, x, y, opacity,
                            off - size if size else None, size, data))

    if off != len(blob):
        # 记录表没覆盖到文件尾：改用签名扫描，保证不丢图层。
        layers, holes = _scanned_layers(blob, data_start, path)
        return Pna(path, width, height, count, layers, data_start, True, holes)

    return Pna(path, width, height, count, layers, data_start, False,
               table_size_field=table_size_field, header_tail=blob[0x14:0x24])


def serialize(p):
    """把 `parse` 出的 Pna 重序列化为字节。头部与记录表逐字段重建（含原始未知字段），
    数据区按记录顺序拼接非空图层的 PNG —— 对未改动的图层逐字节保真（已全量回归）。

    替换图层时由调用方改 `Layer` 的 `data` / `size` / `width` / `height`；
    **记录表的 w/h 必须与 data 内嵌 PNG 的 IHDR 一致**（写回的唯一硬不变量，见模块 docstring）。
    `scanned=True` 的 PNA 报 `PnaError` —— 数据区尾部未解释字节机制未查明，不猜。"""
    if p.scanned:
        raise PnaError('%s: 记录表未覆盖全文件（签名扫描件），不支持重序列化' % p.path)
    out = bytearray()
    out += MAGIC
    out += struct.pack('<IIII', p.table_size_field, p.width, p.height, p.count)
    out += p.header_tail
    for k, l in enumerate(p.layers):
        out += struct.pack('<IIIIfI', l.width, l.height, l.zero, l.unk, l.opacity, l.size)
        if k < p.count - 1:
            out += struct.pack('<ii', l.u0, l.phase)
            out += struct.pack('<ii', l.x, l.y)
    for l in p.layers:
        if l.data is not None:
            out += l.data
    return bytes(out)


def png_dimensions(data):
    """校验 `data` 是一张**完整**的 PNG（签名 + chunk 链到 IEND 且无尾随字节），
    返回 (宽, 高)；否则抛 `PnaError`。供换图前验证替换图。"""
    if data[:8] != PNG_SIG:
        raise PnaError('不是 PNG（签名=%r）' % data[:8])
    size = _png_span_size(data, 0)
    if size is None:
        raise PnaError('PNG chunk 链结构不合法（走不到 IEND）')
    if size != len(data):
        raise PnaError('PNG 不完整或有尾随字节（IEND 在 %d / 共 %d 字节）' % (size, len(data)))
    w, h = struct.unpack_from('>II', data, 16)
    return w, h
