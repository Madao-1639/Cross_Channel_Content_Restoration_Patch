"""把 `resource/graphic_overrides/` 里的汉化图替换进 `asset/` 的归档（**零破坏性**）。

背景：Steam 版 `Graphic.arc` 里有几张整屏图是日文/英文的 —— 标语图 `efcca0030`、
封面 `SGCC0003`、图鉴页 `sgcc0011`。汉化组把原版（日文）图汉化过，加工到 Steam 的画布
尺寸后按**目标成员名**放在 `resource/graphic_overrides/`，本步把它们换进归档。

约定：**替换图 = 同目录下的同名文件** —— 清单只登记「哪个归档里换掉哪些成员」，
路径不再重复一遍（`resource/graphic_overrides/<成员名>`）。

约束（与项目零破坏性口径一致）：
  * **只同名替换**：不新增、不删除成员，归档成员数与顺序不变；
  * **幂等**：成员内容已与替换图逐字节相同则跳过，一个归档全跳过时不重写它；
  * **回读校验**：写完后 `arcbuild.verify()` 断言每个成员都落在文件内。

清单：`resource/graphic_overrides/manifest.json`（key = 归档名，value = 成员名列表）。
用法（项目根目录）：

    python script/apply_graphic_overrides.py            # 预演（只报告，不改写）
    python script/apply_graphic_overrides.py --write    # 落盘
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tool import arcbuild  # noqa: E402

ASSET = ROOT / 'asset'
IMG_DIR = ROOT / 'resource' / 'graphic_overrides'
TABLE = IMG_DIR / 'manifest.json'


def load_table():
    data = json.loads(TABLE.read_text(encoding='utf-8'))
    return {k: v for k, v in data.items() if not k.startswith('_')}


def apply_arc(arc_name, want, write):
    arc_path = ASSET / arc_name
    if not arc_path.exists():
        raise SystemExit('[ERR] %s 不存在（先跑 build_patch.py --bootstrap 或铺好 asset/）' % arc_path)
    members = arcbuild.read_raw(arc_path)
    index = {nb.decode('utf-16le'): i for i, (nb, _) in enumerate(members)}

    changed = 0
    for member in want:
        if member not in index:
            raise SystemExit('[ERR] %s 里没有成员 %r' % (arc_name, member))
        src = IMG_DIR / member
        if not src.exists():
            raise SystemExit('[ERR] 替换图不存在: %s' % src)
        new_data = src.read_bytes()
        i = index[member]
        old_data = members[i][1]
        if old_data == new_data:
            print('  [跳过] %s :: %s 已是目标内容（%d 字节）' % (arc_name, member, len(old_data)))
            continue
        changed += 1
        print('  [替换] %s :: %s  %d -> %d 字节' % (arc_name, member, len(old_data), len(new_data)))
        if write:
            members[i] = (members[i][0], new_data)

    if not changed:
        return 0
    if not write:
        print('  [预演] %s 有 %d 处待替换（未落盘）' % (arc_name, changed))
        return changed

    size = arcbuild.write_arc(members, arc_path)
    count, total, end = arcbuild.verify(arc_path, expect_count=len(members))
    print('  [写盘] %s: %d members, %d bytes' % (arc_name, count, size))
    return changed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--write', action='store_true', help='落盘（缺省只预演）')
    args = ap.parse_args()

    table = load_table()
    total = 0
    for arc_name, want in table.items():
        total += apply_arc(arc_name, want, args.write)

    if args.write:
        print('\n[DONE] 共替换 %d 处' % total)
    else:
        print('\n[预演] 共 %d 处待替换；加 --write 落盘' % total)
    return 0


if __name__ == '__main__':
    sys.exit(main())
