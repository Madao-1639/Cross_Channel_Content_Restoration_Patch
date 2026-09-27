# -*- coding: utf-8 -*-
"""把「**完全复用**的上游归档」（`Fonts.arc` 汉字字体、`Script.arc` Lua 系统界面）内化进仓库。

为什么：这两份是上游汉化产出的、**原样采用**（不涉裁定）。原先由 `build_patch.py` 从
`../CROSS_CHANNEL_Steam_CN_Restored_v3.0.3/` 取 ⇒ **构建期仍依赖那个外部目录**（也把上游的名字
带进了构建链）。内化后基线构建只读 `resource/`，构建期不再需要任何上游知识。

产出：`resource/reused_archives/{Fonts.arc, Script.arc}`（逐字节原样）+ `manifest.json`（名/大小/哈希）。
用法（项目根目录）：
    python script/extract_reused_archives.py            # 预演
    python script/extract_reused_archives.py --write    # 落盘
"""
import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, 'buffer'):
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

OUT_DIR = ROOT / 'resource' / 'reused_archives'
ASSET = ROOT / 'asset'
UPSTREAM = ROOT.parent / 'CROSS_CHANNEL_Steam_CN_Restored_v3.0.3'
NAMES = ('Fonts.arc', 'Script.arc')


def source_of(name):
    """优先取上游目录（**溯源**用）；没有则退回 `asset/`（内容与之逐字节相同）。"""
    up = UPSTREAM / name
    if up.exists():
        return up, 'upstream'
    a = ASSET / name
    if a.exists():
        return a, 'asset'
    raise SystemExit('[中止] 找不到 %s（上游目录与 asset/ 都没有）' % name)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--write', action='store_true')
    args = ap.parse_args()

    got = {}
    for name in NAMES:
        p, src = source_of(name)
        got[name] = (p.read_bytes(), src)
        print('%-12s 取自 %-9s %d 字节' % (name, src, len(got[name][0])))
    # 与 asset/ 交叉核对（应逐字节相同 —— 它一直是 bootstrap 从上游拷来的）
    for name, (d, _) in got.items():
        a = ASSET / name
        if a.exists() and a.read_bytes() != d:
            print('  ⚠ %s 与 asset/ 不同（asset 是流水线产出的那份，以上游为准）' % name)
    if not args.write:
        print('（未指定 --write，未写入）')
        return 0

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, (d, _) in got.items():
        dst = OUT_DIR / name
        if dst.exists() and dst.read_bytes() == d:
            continue
        dst.write_bytes(d)
    (OUT_DIR / 'manifest.json').write_text(json.dumps(
        {'_说明': '完全复用的上游归档（汉字字体 / Lua 系统界面）。基线构建据此带入 asset/，'
                  '构建期不再依赖任何外部上游目录。',
         'members': {n: {'bytes': len(d), 'sha256': hashlib.sha256(d).hexdigest()[:16]}
                     for n, (d, _) in sorted(got.items())}},
        ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    print('写出 %d 份 -> %s' % (len(got), OUT_DIR.relative_to(ROOT)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
