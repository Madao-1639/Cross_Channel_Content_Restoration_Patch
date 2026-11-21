# -*- coding: utf-8 -*-
"""CG 解锁对的**编号归属**：解锁对 `(A,B)` 只认 Steam 的编号空间。

## 数据模型

CG 的显示点后恒跟一组解锁位点（本作形状，全语料 658 处统一，见
doc/file-formats.md「CG 解锁的旗标域」）：

```
0x33 <槽> <资源.PNG> 00 01 01
0x0b <u16 A> 01           CG 组号（menu_gallery.lua 的 CG 表域）
0x0b <u16 B> 01           画廊缩略图条目号（CG_PAGE）
0x04 "CG_ACHIEVEMENT" 00  按 A 计数、按阈值发成就
```

* `A` ∈ 1120..1248，`B` ∈ 1260..1509。
* `CG_PAGE`（250 条）按 `B` 读条目、`CG_ACHIEVEMENT` 按 `A` 计数。

## 为什么需要这个模块

原版（2015）与 Steam 是**两套独立编号空间**。原版脚本里带的是原版编号，
落进 `CG_PAGE` 的值域时只是「碰巧撞上」Steam 的另一个条目 —— 照抄会点亮一张
不相干的画廊条目，并把 CG 计数成就推高。ASFS / IHMW 两侧的定案口径同此：
「A/B 必须取自被替换的 Steam 脚本字节，不能拿原版编号冒充」。

## 判据（比参照项目的 `frame_key()` 更严）

参照项目 `IHMW/tool/cgunlock.py` 把图名去扩展名、去末尾 `S`/`B` 分辨率后缀归一成「帧」，
再按帧反查 —— 那以「同一帧的 S/B 共用同一个 (A,B)」为前提。

**本作不成立**：`CG_PAGE` 里 `EVCC0001.PNG` = B 1408 而 `EVCC0001S.png` = B 1410，
是**两个不同条目**；按帧归一会把两张画混成一个。

本作改用**完整图名**做键，并实测它是**严格单射**（全库 255 个带解锁对的图名，
零冲突 ⇒ 可以按名反查）。`verify_injective()` 就是这个前提的机械断言。

## 处置规则（产出端与闸共用）

| 情形 | 处置 |
|---|---|
| 原版帧在 Steam 侧**有同名** | 解锁对改写为 **Steam 的 `(A,B)`**（反查） |
| 原版帧 Steam 侧**没有**（原版独有） | **丢弃**该解锁对 —— 该帧在 Steam 没有画廊条目 |
| 落在被覆盖区间内的 **Steam 字节** | **保留**，提前到删格前的存活格前缀（解锁是全局置位，与显示解耦） |

⚠️ 本作的第三行是**唯一实际生效**的一行：原版 WSC 的 `0x0b` 是「等 N 秒」的 timer
（取值 `(5,0,5,1,0)` 等，见 doc/wsc_to_ws2_conversion.md），**不是旗标写** ——
而且 `tool/wsc2ws2.py` 对它一律不发射。于是前两行在本作是空集，但它们仍是**纪律**：
将来转换器若开始发射原版解锁对，这里就是唯一允许的改写入囗。
"""
import struct

from tool import arcbuild, ws2, ws2disasm

DISPLAY = (0x33, 0x34)          # 显示指令（本作的解锁对只跟 0x33）
FLAG = 0x0b                     # 旗标置位：`0b <u16> 01`
ACH_NAME = b'CG_ACHIEVEMENT'

A_LO, A_HI = 1120, 1248         # CG 组号
B_LO, B_HI = 1260, 1509         # 画廊缩略图条目号


def is_domain(fid):
    """该旗标 id 是否落在 CG 解锁域（A ∪ B）。"""
    return A_LO <= fid <= A_HI or B_LO <= fid <= B_HI


def split_ab(ids):
    """解锁对的旗标值列表 → `(A, B)`（按**值域**区分，不靠顺序；缺失侧为 None）。"""
    a = next((v for v in ids if A_LO <= v <= A_HI), None)
    b = next((v for v in ids if B_LO <= v <= B_HI), None)
    return a, b


def unlock_groups(raw):
    """一个脚本里的解锁组 → `[(拍点, 显示名, 显示 opcode, (A,B))]`。

    「拍点」= 该组之前有几条 `0x14`。**只作报告用，不作判据** ——
    `apply_text_map` 会在 Steam 保留前缀里插格，拍点会合法漂移
    （参照项目的教训：「位置比对会把合法移位误判成缺陷」）。

    线性解析不自洽的脚本返回 `[]`（菜单/数据表类）；调用方应自行记账，不得静默吞。
    """
    try:
        ins = ws2disasm.disassemble(ws2.decode(raw))
    except Exception:                             # noqa: BLE001 —— 调用方记账
        return []
    out, tick = [], 0
    for k, x in enumerate(ins):
        if x.opcode == 0x14:
            tick += 1
            continue
        if x.opcode != 0x04 or str(x.fields.get('name')) != ACH_NAME.decode('ascii'):
            continue
        j, ids = k - 1, []
        while j >= 0 and ins[j].opcode == FLAG:
            ids.append(ins[j].fields['id'])
            j -= 1
        if not ids:
            continue
        ids.reverse()
        name, op = '', None
        if j >= 0 and ins[j].opcode in DISPLAY:
            name, op = str(ins[j].fields.get('file') or '').upper(), ins[j].opcode
        out.append((tick, name, op, split_ab(ids)))
    return out


def named_pairs(raw):
    """`{图名: (A,B)}` —— 只有**紧邻显示**的解锁组才算（用于反查映射）。"""
    return {n: ab for _t, n, _op, ab in unlock_groups(raw) if n}


def can_parse(raw):
    """该成员能否线性解析（菜单/数据表类的 `.ws2` 不行，需记账跳过）。"""
    try:
        ws2disasm.disassemble(ws2.decode(raw))
        return True
    except Exception:                             # noqa: BLE001
        return False


def steam_unlock_map(backup_rio):
    """Steam 基线 → `({图名: (A,B)}, 冲突清单)`。只读 `backup/Rio.arc`。"""
    table, conflicts = {}, []
    for nb, data in arcbuild.read_raw(backup_rio):
        name = nb.decode('utf-16le', 'replace')
        if not name.lower().endswith('.ws2'):
            continue
        try:
            pairs = named_pairs(data)
        except Exception as ex:                       # noqa: BLE001 —— 记账不吞
            conflicts.append((name, 'parse: %s' % ex))
            continue
        for disp, ab in pairs.items():
            prev = table.get(disp)
            if prev is not None and prev != ab:
                conflicts.append((disp, prev, ab))
            table.setdefault(disp, ab)
    return table, conflicts


def verify_injective(table):
    """映射必须严格单射（同一图名只一个 `(A,B)`）—— 反查的前提，闸里当断言用。"""
    seen = {}
    dup = []
    for name, ab in table.items():
        if name in seen and seen[name] != ab:
            dup.append((name, seen[name], ab))
        seen[name] = ab
    return dup


def resolve_pair(display_name, steam_map):
    """原版显示名 → 该点应写的 `(A,B)`；`None` ＝ Steam 侧没有该帧（应丢弃解锁对）。"""
    return steam_map.get((display_name or '').upper())


def flag_pair_bytes(a, b):
    """`0b A 01 0b B 01`（可用侧跳过 None）。"""
    out = b''
    for v in (a, b):
        if v is not None:
            out += b'\x0b' + struct.pack('<H', v) + b'\x01'
    return out


def ach_call_bytes():
    """`04 CG_ACHIEVEMENT 00`。"""
    return b'\x04' + ACH_NAME + b'\x00'
