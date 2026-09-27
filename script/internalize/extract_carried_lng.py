# -*- coding: utf-8 -*-
"""把「本项目处理集之外、但需随产物发布」的汉化 lng 从 Res303 归档提取到仓库。

为什么：基线要改成 `backup/`（Steam 原版），而 Steam 侧**没有** lng。这类脚本不在
`resource/text_map.json` 的 293 处理集内（不会被 `apply_text_map` 重生成），但**要保留**
Res303 的汉化 —— 于是把它们**内化**成仓库数据，基线构建时显式带过，不再依赖外部 Res303 目录。

取哪些：Res303 `Rio.arc` 里 `.lng`、脚本名**不在表内**、且**不以 `CNR` 开头**的
（`CNR###` 是 Res303 的还原脚本，本项目走「就地插入」，由 `remove_cnr_scripts` 移除）。

产出：`resource/carried_lng/<原成员名>`（逐字节原样）；清单 `resource/carried_lng/manifest.json`。
用法（项目根目录）：
    python script/internalize/extract_carried_lng.py            # 预演
    python script/internalize/extract_carried_lng.py --write    # 落盘
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tool import arcbuild  # noqa: E402

if hasattr(sys.stdout, 'buffer'):
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

RES303_RIO = ROOT.parent / 'CROSS_CHANNEL_Steam_CN_Restored_v3.0.3' / 'Rio.arc'
TM = ROOT / 'resource' / 'text_map.json'
OUT_DIR = ROOT / 'resource' / 'carried_lng'


def wanted():
    in_table = {k.upper() for k in json.loads(TM.read_text(encoding='utf-8'))['scripts']}
    rio = arcbuild.read_raw(RES303_RIO)
    out = {}
    for nb, d in rio:
        name = nb.decode('utf-16-le')
        if not name.upper().endswith('.LNG'):
            continue
        stem = name.upper()[:-4]
        if stem in in_table or stem.startswith('CNR'):
            continue
        out[name] = d
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--write', action='store_true')
    args = ap.parse_args()

    items = wanted()
    total = sum(len(v) for v in items.values())
    print('待内化 lng %d 条，合计 %d 字节（%.1f KB）' % (len(items), total, total / 1024))
    if not args.write:
        print('（未指定 --write，未写入）')
        return 0

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    changed = 0
    for name, d in sorted(items.items()):
        p = OUT_DIR / name
        if p.exists() and p.read_bytes() == d:
            continue
        p.write_bytes(d)
        changed += 1
    man = {'_说明': '本项目处理集之外、需随产物发布的汉化 lng（取自 Res303 Rio.arc）。'
                    '基线构建（build_patch --bootstrap）时由 build_patch 显式带回 asset/Rio.arc。',
           'members': {name: {'bytes': len(d), 'sha256': hashlib.sha256(d).hexdigest()[:16]}
                       for name, d in sorted(items.items())}}
    (OUT_DIR / 'manifest.json').write_text(
        json.dumps(man, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    print('写出 %d 条（跳过相同 %d 条）→ %s' % (changed, len(items) - changed, OUT_DIR.relative_to(ROOT)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
