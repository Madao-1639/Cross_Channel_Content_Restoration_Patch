# -*- coding: utf-8 -*-
"""从**原版**归档重建补丁的还原 CG，写入 `asset/Chip2.arc`。

背景：原来这些图取自 Res303；基线和 CG 都改从**原版**起底后，本步按
`resource/cg_map.json` 的 `all`（原版名 -> 补丁名）把原版图**放大到 Steam 画布并改名**补进来。

放大方法：**LANCZOS 到 1280×960**。已实证：原版图按此法放大后与既有补丁 CG
**逐像素完全相同（最大差 0）** —— 所以换源不改观感。负责人亦已明确：放大**不必字节级一致**。

只同名替换/新增，不动其它成员；幂等（成员已存在且逐字节相同则跳过）。
用法（项目根目录）：
    python script/build_restored_cgs.py            # 预演
    python script/build_restored_cgs.py --write    # 落盘
"""
import argparse
import glob
import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image                                          # noqa: E402
from tool import arcbuild                                      # noqa: E402

if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

ORIG = ROOT.parent / 'CROSS_CHANNEL_Original'
CG_MAP = ROOT / 'resource' / 'cg_map.json'
CHIP2 = ROOT / 'asset' / 'Chip2.arc'
TARGET = (1280, 960)


def orig_images():
    """原版 `Chip*.arc` 里的 EVCC/SGCC PNG，按名字（大写）取。"""
    out = {}
    for p in sorted(glob.glob(str(ORIG / 'Chip*.arc'))):
        try:
            for nb, d in arcbuild.read_old_arc(p):
                k = nb.decode('shift_jis', 'replace').upper()
                if k.startswith(('EVCC', 'SGCC')) and k.endswith('.PNG'):
                    out.setdefault(k, d)
        except Exception:
            continue
    return out


def upscale(data):
    im = Image.open(io.BytesIO(data)).convert('RGB')
    if im.size != TARGET:
        im = im.resize(TARGET, Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, format='PNG')
    return buf.getvalue()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--write', action='store_true')
    args = ap.parse_args()

    m = json.loads(CG_MAP.read_text(encoding='utf-8'))
    pairs = sorted((o.upper(), p.upper()) for o, p in m['all'].items())
    print('cg_map.all：%d 条（原版名 -> 补丁名）' % len(pairs))

    src = orig_images()
    members = list(arcbuild.read_raw(CHIP2))
    index = {n.decode('utf-16-le').upper(): i for i, (n, _) in enumerate(members)}

    added, replaced, same, missing = 0, 0, 0, []
    for oname, pname in pairs:
        if oname not in src:
            missing.append(oname)
            continue
        new = upscale(src[oname])
        nb = pname.encode('utf-16-le')
        if pname not in index:
            members.append((nb, new)); added += 1
            print('  [新增] %s  <- 原版 %s  (%d 字节)' % (pname, oname, len(new)))
        elif members[index[pname]][1] == new:
            same += 1
        else:
            members[index[pname]] = (members[index[pname]][0], new); replaced += 1
            print('  [替换] %s  <- 原版 %s  (%d 字节)' % (pname, oname, len(new)))

    if missing:
        raise SystemExit('原版里找不到这些图：%s' % missing)
    print('新增 %d / 替换 %d / 已相同 %d' % (added, replaced, same))
    if not (added or replaced):
        print('（无需改动）')
        return 0
    if not args.write:
        print('（未指定 --write，未写入）')
        return 0
    arcbuild.write_arc(members, CHIP2)
    arcbuild.verify(CHIP2, expect_count=len(members))
    print('[写入] %s：%d 成员' % (CHIP2.relative_to(ROOT), len(members)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
