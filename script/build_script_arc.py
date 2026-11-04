"""构建语言包的 `Script.arc`（**唯一所有者**）：只放与 Steam 原版不同的 Lua 成员。

内容 = `resource/reused_archives/Script.arc`（内化中文 UI，逐字节保留汉化组原样）中**与 Steam 原版
不同的成员**。本步是该归档的**唯一所有者**：每次都按最终内容重算成员集合（幂等、无残留）。

**按成员覆盖已被官方证实**：IMHHW 的 `zh-CN/Script.arc` 只有 **1 个成员**（`ui_language.lua`，
302 字节）即可生效。本补丁与 Steam 原版的差异只有 2 个成员：
`ui_language.lua`（语言包开关）与 `LegacyGame_utf8.lua`（字体槽 → 思源）。其余 12 个逐字节相同，
不入包 —— 免得用旧版 Lua 覆盖新引擎的 Lua。

用法：`python script/build_script_arc.py [--write] [--full]`（`--full` 发全部 14 个成员）
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tool import arcbuild, layers  # noqa: E402

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

SRC = ROOT / 'resource' / 'reused_archives' / 'Script.arc'


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--write', action='store_true')
    ap.add_argument('--full', action='store_true',
                    help='发全部成员（默认只发与 backup/Script.arc 不同的成员）')
    args = ap.parse_args(argv)
    if not SRC.exists():
        raise SystemExit('[ERR] 缺少 %s（先跑 script/internalize/extract_reused_archives.py）' % SRC)
    src = arcbuild.read_raw(SRC)
    orig_path = ROOT / 'backup' / 'Script.arc'
    orig = {nb: d for nb, d in arcbuild.read_raw(orig_path)} if orig_path.exists() else {}

    out = []
    dropped = 0
    for nb, data in src:
        if not args.full and nb in orig and orig[nb] == data:
            dropped += 1
            continue
        out.append((nb, data))
    print('[Script.arc] 源 %d 成员 -> 语言包 %d 成员（与原档相同的 %d 个剔除）'
          % (len(src), len(out), dropped))
    target = layers.pack_arc('Script.arc')
    if args.write:
        layers.write_layer(target, out, expect_count=len(out))
        print('[写盘] %s（%d 成员）' % (target, len(out)))
    else:
        print('[预演] 未写盘；加 --write 落盘')
    return 0


if __name__ == '__main__':
    sys.exit(main())
