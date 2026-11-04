"""AdvHD「全局小快照」`Save000` 的只读解析器（画廊 CG / 追加剧本 / 场景回想解锁位）。

`Save000`（存档目录下，775 字节定长）是**跨存档、跨周目**的全局状态：引擎在启动时读它、
把旗标位图装进内存（`sub_46D470`），退出/改写时按内存状态回写（`sub_470F70`）。
Lua 画廊（`menu_gallery.lua`）用的 `LegacyGame__lua_GetFlag` 读的就是这张位图。

布局（逆向结论，已用本机存档逐字节验证）：

    +0x000  u8 × 375  旗标位图：flag id 1000..3999（bit = (id-1000)；**LSB 优先**，即 id%8 位）
    +0x177  f32 × 100 变量数组：var id 200..299

  * 位图容量来自 `VariableSize.lua::getVariableSize()` = (1000, 3000, 200, 100)：
    前 1000 个 id 只在本进程内有效（不落盘），1000..3999 才持久化；
  * `dword_594EDC`/`dword_594ED8` = 1000/3000，`dword_594EF4`/`dword_594ECC` = 200/100（偏移与个数）；
  * 读档/新周目**不会**改它；只有引擎自己回写（正常退出）才会覆盖 ⇒ 手工替换该文件必须在游戏关闭时做，
    且要先停 Steam Cloud，否则云端会把旧文件同步回来。

已知的旗标域（来自 `menu_gallery.lua` 常量表与脚本里的 `0b` 写入点）：

| 区间 | 用途 | 写入点 |
|---|---|---|
| 1100..1106 | 画廊「Scene」六条个人线（`CC0TOU`…`CC6SAK`） | 各线结局脚本 `CCD2002B`/`CCD1001D`/`CCD3003D`/`CCD4003A`/`CCD5001B`/`CCD6001` |
| **1107** | 画廊「Scene」`CC_30A`（追加剧本２《朋友塔》） | `CCE0001`（真结局，与 1108 同帧两条 `0b`） |
| **1108** | 画廊「Scene」`CC_10A`（追加剧本１） | 同上 |
| 1120..1248 | CG 图鉴（脚本 `0b` 写；`CG_ACHIEVEMENT` 表域） | 场景脚本 |
| 1260..1509 | 引擎/成就侧自用 | 引擎 |

用法（项目根目录）：

    python -m tool.saveglobal                       # 解析默认存档目录的 Save000
    python -m tool.saveglobal --path <Save000>      # 指定文件
    python -m tool.saveglobal --flags 1100:1110     # 只看某区间的置位情况
    python -m tool.saveglobal --vars                # 顺带打印变量数组（非零项）
    python -m tool.saveglobal --merge other/Save000 --out Save000.new   # 合并解锁位（按位 OR，变量取本方）
"""
import argparse
import struct
import sys
from pathlib import Path

SAVE_DIR = Path.home() / 'Saved Games' / 'MoeNovel' / 'CROSS†CHANNEL Steam Edition' / 'Save000'

FLAG_BASE = 1000
FLAG_COUNT = 3000
BITMAP_BYTES = (FLAG_COUNT + 7) // 8          # 375
VAR_BASE = 200
VAR_COUNT = 100
VAR_BYTES = 4 * VAR_COUNT                     # 400
FILE_SIZE = BITMAP_BYTES + VAR_BYTES          # 775


class GlobalSave(object):
    def __init__(self, blob, path='<bytes>'):
        if len(blob) != FILE_SIZE:
            raise ValueError('%s: 大小 %d != %d（不是全局快照，或是别的版本）'
                             % (path, len(blob), FILE_SIZE))
        self.path = path
        self.bitmap = blob[:BITMAP_BYTES]
        self.variables = list(struct.unpack('<%df' % VAR_COUNT, blob[BITMAP_BYTES:]))

    def flag(self, fid):
        """flag id 1000..3999 的值；范围外返回 None（那些位不落盘）。"""
        if not (FLAG_BASE <= fid < FLAG_BASE + FLAG_COUNT):
            return None
        idx = fid - FLAG_BASE
        return bool(self.bitmap[idx >> 3] & (1 << (idx & 7)))

    def set_flags(self):
        out = []
        for byte_i, byte in enumerate(self.bitmap):
            for bit in range(8):
                if byte & (1 << bit):
                    out.append(FLAG_BASE + byte_i * 8 + bit)
        return out


def ranges(ids):
    """[1100,1101,1103] → '1100-1101, 1103'"""
    ids = sorted(ids)
    out = []
    start = prev = None
    for i in ids:
        if start is None:
            start = prev = i
        elif i == prev + 1:
            prev = i
        else:
            out.append((start, prev))
            start = prev = i
    if start is not None:
        out.append((start, prev))
    return ', '.join('%d' % a if a == b else '%d-%d' % (a, b) for a, b in out)


SCENE_FLAGS = [(1100, 'CC0TOU  冬子线'), (1101, 'CC1MIS  美希线'), (1102, 'CC2KIR  雾线'),
               (1103, 'CC3MIK  美希线(个人)'), (1104, 'CC4YOU  曜子线'), (1105, 'CC5TOM  友贵线'),
               (1106, 'CC6SAK  樱庭线'), (1107, 'CC_30A  追加剧本２《朋友塔》'),
               (1108, 'CC_10A  追加剧本１')]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--path', default=str(SAVE_DIR))
    ap.add_argument('--flags', help='区间 a:b（闭开）——只打印该区间')
    ap.add_argument('--vars', action='store_true', help='打印非零变量')
    ap.add_argument('--merge', help='另一份 Save000：把它的解锁位并进本方（按位 OR）')
    ap.add_argument('--out', help='--merge 的输出路径（缺省只预演）')
    args = ap.parse_args(argv)
    p = Path(args.path)
    if not p.exists():
        raise SystemExit('[ERR] 找不到 %s（%s）' % (p, '存档目录通常在 %%USERPROFILE%%\\Saved Games\\MoeNovel\\<英文标题>\\'))
    blob = p.read_bytes()
    gs = GlobalSave(blob, str(p))
    if args.merge:
        other = GlobalSave(Path(args.merge).read_bytes(), args.merge)
        merged = bytes(a | b for a, b in zip(gs.bitmap, other.bitmap))
        extra = [i for i in range(FLAG_BASE, FLAG_BASE + FLAG_COUNT)
                 if (merged[i - FLAG_BASE >> 3] >> ((i - FLAG_BASE) & 7) & 1)
                 and not gs.flag(i)]
        print('== 合并：%s + %s ==' % (p, args.merge))
        print('本方新增置位 %d 个：%s' % (len(extra), ranges(extra) if extra else '（无）'))
        if args.out:
            Path(args.out).write_bytes(merged + blob[BITMAP_BYTES:])
            print('[写出] %s（变量数组沿用本方）' % args.out)
        else:
            print('[预演] 未写盘；加 --out <路径> 落盘')
        return 0
    print('== %s（%d 字节）==' % (p, FILE_SIZE))
    print('画廊「Scene」解锁位：')
    for fid, label in SCENE_FLAGS:
        print('  %5d %-28s %s' % (fid, label, '已解锁' if gs.flag(fid) else '—'))
    if args.flags:
        a, b = (int(x) for x in args.flags.split(':'))
        ids = [i for i in range(a, b) if gs.flag(i)]
        print('区间 %d:%d 置位：%s' % (a, b, ranges(ids) if ids else '（无）'))
        return 0
    cg = [i for i in range(1120, 1249) if gs.flag(i)]
    ach = [i for i in range(1260, 1510) if gs.flag(i)]
    print('CG 图鉴（1120..1248）置位 %d 个：%s' % (len(cg), ranges(cg) if cg else '（无）'))
    print('成就域（1260..1509）置位 %d 个：%s' % (len(ach), ranges(ach) if ach else '（无）'))
    others = [i for i in gs.set_flags() if not (1100 <= i <= 1509)]
    print('其余置位 %d 个：%s' % (len(others), ranges(others) if others else '（无）'))
    if args.vars:
        print('变量（id 200..299，只列非零）：')
        for k, v in enumerate(gs.variables):
            if v:
                print('  %d = %g' % (VAR_BASE + k, v))
    return 0


if __name__ == '__main__':
    sys.exit(main())
