"""把 `resource/SysGraphic/` 里的汉化 UI 图层换进 `asset/SysGraphic.arc`（零破坏性）。

背景：Steam 版系统界面的文案烙在 `SysGraphic.arc` 的 PNA 图层里（设置页、标题菜单等），
引擎按「PNA 记录下标」取图层显示（见 doc/engine-mechanics.md「PNA 二进制布局」）。
汉化素材按**图层**提供，换掉对应图层的数据块即完成 UI 汉化 —— 归档成员集合与顺序、
pna 记录数、图层相位/坐标一律不动。

约定（资源目录布局）：
    resource/SysGraphic/<pna 名>/L<图层下标>.png
  * 子目录名 = SysGraphic.arc 内的目标 pna 成员（不带 `.pna` 后缀，大小写不敏感匹配）；
  * 文件名 `L<下标>.png` = 替换该 pna 内**记录下标** `<下标>` 的图层（`39` 显示指令的
    帧号就是记录下标，见 doc/pna-resources.md「图形指令」）；
  * 子目录里出现任何不叫 `L<数字>.png` 的条目都报错 —— 不静默忽略，防误带源文件/缓存。

尺寸：**允许与原图层不同**（中文常比英文宽）。替换后同步更新该图层记录的宽高与数据块
大小；记录的 x/y（左上角锚点）、画布尺寸一律不动。实测 sys_config_P1 的 153 张替换图
75 张变了尺寸，全部仍落在 1280x720 画布内；越界**告警不拦截**（报告而非修改）。

约束（与项目零破坏性口径一致）：
  * **只换图层**：不新增/删除归档成员或图层，记录数不变；
  * **幂等**：图层已与替换图逐字节相同则跳过；一个 pna 全跳过则不重写它；
    所有 pna 都无变化则不重写归档；
  * **回读校验**：重建 pna 后立即重新解析，断言画布/记录数/图层内容/未替换图层逐字节
    不变/「记录表 w/h == IHDR」不变量，通过才放回归档；落盘后再 `arcbuild.verify()`。

限制：`SYS_GalleryBrowser.pna` / `Sys_Msw.pna` 是「记录表未覆盖全文件」的签名扫描件
（`Pna.scanned`，数据区尾部有机制未查明的字节），**不支持重序列化** —— 资源目录里
若有它们的替换子目录，直接报错退出，不猜。

用法（项目根目录）：

    python script/apply_sysgraphic.py            # 预演（只报告，不改写）
    python script/apply_sysgraphic.py --write    # 落盘
"""
import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tool import arcbuild, pna  # noqa: E402

ASSET = ROOT / 'asset'
RES = ROOT / 'resource' / 'SysGraphic'
ARC = 'SysGraphic.arc'
LAYER_FILE = re.compile(r'L(\d+)\.png')


def load_plan():
    """读资源目录 → {子目录名: {图层下标: 替换图路径}}；布局不合法即报错。"""
    if not RES.is_dir():
        raise SystemExit('[ERR] 缺少资源目录 %s（UI 汉化素材应放 <pna 名>/L<图层下标>.png）' % RES)
    plan = {}
    for sub in sorted(p for p in RES.iterdir() if p.is_dir()):
        layers = {}
        for f in sorted(sub.iterdir()):
            m = LAYER_FILE.fullmatch(f.name)
            if m is None:
                raise SystemExit('[ERR] %s: 不合约定的条目 %r（只允许 L<图层下标>.png）'
                                 % (sub.name, f.name))
            layers[int(m.group(1))] = f
        if layers:
            plan[sub.name] = layers
        else:
            print('[跳过] %s: 子目录里没有替换图' % sub.name)
    return plan


def apply_pna(sub_name, data, layer_files):
    """把替换图换进 pna 字节，返回 (新字节, 换了几处)；无变化返回 (None, 0)。"""
    p = pna.parse(data, path=sub_name + '.pna')
    if p.scanned:
        raise SystemExit('[ERR] %s.pna 是签名扫描件（记录表未覆盖全文件），不支持图层替换 —— '
                         '数据区尾部的未解释字节机制未查明（见 doc/engine-mechanics.md「PNA '
                         '二进制布局」），保持原样' % sub_name)
    orig = [l for l in p.layers]
    changed = 0
    for idx in sorted(layer_files):
        f = layer_files[idx]
        if idx >= p.count:
            raise SystemExit('[ERR] %s: %s 的图层下标 %d 越界（该 pna 只有 %d 条记录）'
                             % (sub_name, f.name, idx, p.count))
        layer = p.layers[idx]
        if layer.data is None:
            raise SystemExit('[ERR] %s: %s 指向空图层（无数据块）。空图层是相位/结构哨兵，'
                             '不是可显示帧，不能填图' % (sub_name, f.name))
        png = f.read_bytes()
        try:
            w, h = pna.png_dimensions(png)
        except pna.PnaError as e:
            raise SystemExit('[ERR] %s: %s 不是完整的 PNG：%s' % (sub_name, f.name, e))
        if png == layer.data:
            print('  [跳过] %s :: L%d 已是目标内容（%d 字节）' % (sub_name, idx, len(png)))
            continue
        if (w, h) != (layer.width, layer.height):
            print('  [尺寸] %s :: L%d  %dx%d -> %dx%d（记录表 w/h 随新图更新，x/y 不动）'
                  % (sub_name, idx, layer.width, layer.height, w, h))
        if layer.x is not None and (layer.x + w > p.width or layer.y + h > p.height
                                    or layer.x < 0 or layer.y < 0):
            print('  [告警] %s :: L%d 替换图越出画布（xy=(%d,%d) 尺寸 %dx%d，画布 %dx%d）'
                  % (sub_name, idx, layer.x, layer.y, w, h, p.width, p.height))
        changed += 1
        layer.data = png
        layer.size = len(png)
        layer.width, layer.height = w, h

    if not changed:
        return None, 0
    new_bytes = pna.serialize(p)

    # 回读校验：重建字节必须解析回同一结构，且满足全部不变量。
    q = pna.parse(new_bytes, path='%s.pna(重建)' % sub_name)
    if (q.count, q.width, q.height) != (p.count, p.width, p.height):
        raise SystemExit('[ERR] %s: 重建后画布/记录数变了（%d/%dx%d -> %d/%dx%d）'
                         % (sub_name, p.count, p.width, p.height,
                            q.count, q.width, q.height))
    for k, (a, b) in enumerate(zip(orig, q.layers)):
        want = layer_files[k].read_bytes() if k in layer_files else a.data
        if b.data != want:
            raise SystemExit('[ERR] %s: 重建后图层 %d 内容与预期不符' % (sub_name, k))
        if b.data is not None and pna.png_dimensions(b.data) != (b.width, b.height):
            raise SystemExit('[ERR] %s: 重建后图层 %d 记录表 w/h(%dx%d) != IHDR(%dx%d)'
                             % (sub_name, k, b.width, b.height,
                                *pna.png_dimensions(b.data)))
        if (a.x, a.y, a.u0, a.phase, a.opacity) != (b.x, b.y, b.u0, b.phase, b.opacity):
            raise SystemExit('[ERR] %s: 重建后图层 %d 的坐标/相位字段变了' % (sub_name, k))
    return new_bytes, changed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--write', action='store_true', help='落盘（缺省只预演）')
    args = ap.parse_args()

    arc_path = ASSET / ARC
    if not arc_path.exists():
        raise SystemExit('[ERR] %s 不存在（先跑 build_patch.py --bootstrap 或铺好 asset/）' % arc_path)
    plan = load_plan()
    if not plan:
        print('[DONE] 没有配置任何替换，%s 保持原样' % ARC)
        return 0

    members = arcbuild.read_raw(arc_path)
    index = {nb.decode('utf-16-le').lower(): i for i, (nb, _) in enumerate(members)}

    total = dirty = 0
    for sub_name in sorted(plan):
        key = sub_name.lower() + '.pna'
        if key not in index:
            raise SystemExit('[ERR] %s 里没有成员 %s.pna（子目录名必须对应归档内的 pna）'
                             % (ARC, sub_name))
        i = index[key]
        new_bytes, n = apply_pna(sub_name, members[i][1], plan[sub_name])
        total += n
        if new_bytes is not None:
            dirty += 1
            print('  [替换] %s.pna：换 %d 个图层，%d -> %d 字节'
                  % (sub_name, n, len(members[i][1]), len(new_bytes)))
            if args.write:
                members[i] = (members[i][0], new_bytes)

    if not args.write:
        print('\n[预演] %d 个 pna 共 %d 处待替换；加 --write 落盘' % (dirty, total))
        return 0
    if dirty:
        arcbuild.write_arc(members, arc_path)
        arcbuild.verify(arc_path, expect_count=len(members))
        print('\n[写盘] %s: %d members, %d bytes' % (ARC, len(members), arc_path.stat().st_size))
    else:
        print('\n[DONE] %d 处替换均已生效，归档未改动' % total)
    print('[DONE] 共替换 %d 处' % total)
    return 0


if __name__ == '__main__':
    sys.exit(main())
