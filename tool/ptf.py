# -*- coding: utf-8 -*-
"""PTF（Fonts.arc 字体封装）编解码。

## 格式（AdvHD.exe 加载器 sub_4591E0，与实机验证一致）

  1. 文件前 4 字节 = 解压后大小（LE u32），后接 LZSS 字节流：
     - flag 字节按 **bit0 先出** 消费 8 位；bit=1 → 1 字节 literal；
       bit=0 → 2 字节 token：距离源 = `(b1>>4) + 16*b0`（12-bit，环形窗 0x1000），
       长度 = `(b1 & 0xF) + 2`；
     - 环形窗存的是 **XOR 后的字节**；写指针从 1 起（0x4593CD：mov esi,1）；
  2. 解压输出逐字节与 0x2C 异或（key 在原版 PTF 上实测，'OTTO' 头对齐）
     得到标准字体文件（OTF/CFF 或 TTF/glyf 均可）；
  3. 结果交给 `AddFontMemResourceEx` 注册到进程字体表，随后引擎按
     Lua 传入的族名经 GDI 匹配（`lfCharSet=GB2312_CHARSET`）。

## 本仓库的打包约定：纯字面量 LZSS

字体文件熵高，LZSS 压缩无收益（原版反而膨胀 ~12.5%）。因此打包器只
生成 flag=0xFF、每字节 literal 的流：格式任意合法、语义零歧义、膨胀固定
~12.5%（与原版作者用通用压缩包装器的结果同级）。黑体源以 TrueType
（`\x00\x01\x00\x00`）打包，GDI 能在 GB2312_CHARSET 下正确匹配；这是实机
打通的路径。
"""
import struct

XOR_KEY = 0x2C
WINDOW = 0x1000


def lzss_decode(data: bytes, out_len: int, xor_key: int = XOR_KEY) -> bytes:
    """按引擎语义解压 LZSS 字节流（不含前 4 字节大小头，大小由 out_len 给出）。"""
    out = bytearray(out_len)
    ring = bytearray(WINDOW)
    w = 1
    flag = 0
    it = 0
    o = 0
    mask = 0
    flagbyte = 0
    while o < out_len and it < len(data):
        if mask == 0:
            flagbyte = data[it]
            it += 1
            mask = 8
        bit = flagbyte & 1
        flagbyte >>= 1
        mask -= 1
        if bit:
            b = data[it]
            it += 1
            if o < out_len:
                out[o] = b ^ xor_key
                o += 1
            ring[w] = b
            w = (w + 1) & (WINDOW - 1)
        else:
            b0 = data[it]
            b1 = data[it + 1]
            it += 2
            src = (b1 >> 4) + 16 * b0
            length = (b1 & 0xF) + 2
            for _ in range(length):
                c = ring[src & (WINDOW - 1)]
                if o < out_len:
                    out[o] = c ^ xor_key
                    o += 1
                ring[w] = c
                w = (w + 1) & (WINDOW - 1)
                src = (src + 1) & (WINDOW - 1)
    return bytes(out)


def lzss_encode_literal(data: bytes, xor_key: int = XOR_KEY) -> bytes:
    """纯字面量 LZSS 打包：先逐字节 XOR，再每 8 字节前插 1 字节 flag=0xFF。

    与 decode 严格互逆（literal 路径），且不产生任何 match token。"""
    payload = bytes(b ^ xor_key for b in data)
    out = bytearray()
    i = 0
    while i < len(payload):
        out.append(0xFF)
        out.extend(payload[i:i + 8])
        i += 8
    return bytes(out)


def pack_ptf(plain_otf: bytes) -> bytes:
    """明文字体 → PTF（[u32 解压大小][纯字面量 LZSS(XOR(otf))]），含回读自检。"""
    blob = struct.pack('<I', len(plain_otf)) + lzss_encode_literal(plain_otf)
    check = unpack_ptf(blob)
    if check != plain_otf:
        raise AssertionError('PTF round-trip mismatch')
    return blob


def unpack_ptf(ptf: bytes) -> bytes:
    """PTF → 明文字体。"""
    (decl,) = struct.unpack_from('<I', ptf, 0)
    return lzss_decode(ptf[4:], decl)
