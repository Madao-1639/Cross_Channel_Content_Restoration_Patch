# -*- coding: utf-8 -*-
"""思源黑体 TTF 子集化：按 `resource/fonts/sourcehan_subset/codepoints.txt` 的码点
从全量源裁剪，输出到 `resource/fonts/sourcehan_subset/`，供 `script/build_fonts_arc.py` 打包。

体 name 表**先保留全部**记录（不做族名改写，与 Adobe 原始 id1/2/16/17 保持一致）。

**全量源不入库**（~40 MB），放到 `resource/fonts/sourcehan_full/`；
**目录缺失或没有目标 TTF 时幂等跳过** —— pipeline 里 `build_fonts_arc.py`
只依赖 `sourcehan_subset/` 已有子集，本脚本只在**码点集变更后**重跑。
可用环境变量 `SOURCEHAN_FULL_DIR` 指向其他目录。
"""
import os
import sys

from fontTools import subset
from fontTools.ttLib import TTFont

sys.stdout.reconfigure(encoding='utf-8')

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.environ.get('SOURCEHAN_FULL_DIR',
                         os.path.join(_ROOT, 'resource', 'fonts', 'sourcehan_full'))
DST_DIR = os.path.join(_ROOT, 'resource', 'fonts', 'sourcehan_subset')

CODEPOINTS = os.path.join(DST_DIR, 'codepoints.txt')

SUBSET_SOURCES = (
    'SourceHanSansSC-Medium.ttf',
    'SourceHanSansSC-Bold.ttf',
)


def subset_font(src, dst, unicodes):
    f = TTFont(src)
    options = subset.Options()
    options.name_IDs = ['*']          # 保留全部 name 记录（族名不动）
    options.notdef_outline = True     # 保留 .notdef 轮廓
    options.recalc_bounds = True
    options.drop_tables += ['DSIG']   # 数字签名随子集无效，直接去掉
    ss = subset.Subsetter(options)
    ss.populate(unicodes=unicodes)
    ss.subset(f)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    f.save(dst)
    return os.path.getsize(dst)


def main():
    if not os.path.isdir(SRC_DIR):
        # pipeline 只用 sourcehan_subset 的已有子集；幂等跳过不会让 build_patch 挂掉
        print('subset skipped: source dir missing: %s' % SRC_DIR)
        return
    missing = [fn for fn in SUBSET_SOURCES if not os.path.exists(os.path.join(SRC_DIR, fn))]
    if missing:
        print('subset skipped: full TTFs missing: %r' % missing)
        return
    with open(CODEPOINTS) as fh:
        unicodes = [int(line.strip(), 16) for line in fh if line.strip()]
    print('unicodes:', len(unicodes))
    os.makedirs(DST_DIR, exist_ok=True)
    for fn in SUBSET_SOURCES:
        src = os.path.join(SRC_DIR, fn)
        dst = os.path.join(DST_DIR, fn)
        size = subset_font(src, dst, unicodes)
        print('  %s -> %s (%d bytes)' % (fn, dst, size))


if __name__ == '__main__':
    main()
