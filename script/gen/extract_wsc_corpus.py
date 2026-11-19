# -*- coding: utf-8 -*-
"""从**用户自备的原版游戏归档**生成 WSC 语料到 `resource/corpus/wsc/`（构建期派生，不入库）。

为什么：`resource/corpus/wsc/` 的 324 个 `.WSC` 是原版游戏剧本文本，著作权属 FlyingShine /
MoeNovel，**不随本补丁分发**。它们只由本地正版副本解出：原版 `Rio.arc` 的存储形态为每字节
`ror 2`（`tool.wsc2ws2.decrypt_wsc`），解出后逐字节等于历史版本里入库的那份语料（324/324 一致）。

输入（按顺序尝试，命中即用）：
    1. `CROSS_ORIGINAL_RIO` 环境变量
    2. `<仓库上级>/CROSS_CHANNEL_Original/Rio.arc`（本机参考副本，**不入库**）

产出：`resource/corpus/wsc/<NAME>.WSC`（已解密，324 个）+ `manifest.json`（名/大小/哈希）。

落盘规则：目标已存在且内容相同则跳过，因此重复运行幂等。

用法（项目根目录）：
    python script/gen/extract_wsc_corpus.py            # 预演
    python script/gen/extract_wsc_corpus.py --write    # 落盘
"""
import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tool import arcbuild  # noqa: E402
from tool.wsc2ws2 import decrypt_wsc  # noqa: E402

if hasattr(sys.stdout, 'buffer'):
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

OUT_DIR = ROOT / 'resource' / 'corpus' / 'wsc'
EXPECT = 324


def source_rio():
    """定位原版 `Rio.arc`：环境变量优先，其次仓库上级的参考副本。"""
    env = os.environ.get('CROSS_ORIGINAL_RIO')
    if env:
        p = Path(env)
        if p.exists():
            return p, 'env'
        raise SystemExit('[中止] CROSS_ORIGINAL_RIO=%s 不存在' % env)
    p = ROOT.parent / 'CROSS_CHANNEL_Original' / 'Rio.arc'
    if p.exists():
        return p, 'sibling'
    raise SystemExit(
        '[中止] 找不到原版 Rio.arc。\n'
        '       请设置 CROSS_ORIGINAL_RIO 指向你自己正版副本里的 Rio.arc，或把它放到\n'
        '       %s\n'
        '       本仓库不随补丁分发原版剧本（著作权属 FlyingShine / MoeNovel）。' % p)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--write', action='store_true')
    args = ap.parse_args()

    rio, src = source_rio()
    members = arcbuild.read_old_arc(rio)
    got = {}
    for nb, data in members:
        name = nb.decode('ascii')
        if not name.upper().endswith('.WSC'):
            continue
        got[name] = decrypt_wsc(data)
    print('原版 Rio.arc 取自 %s（%s）：WSC %d 个' % (rio, src, len(got)))
    if len(got) != EXPECT:
        print('  ⚠ 期望 %d 个，实际 %d 个（原版归档版本可能不同）' % (EXPECT, len(got)))

    # 与已有语料交叉核对（应逐字节相同 —— 库里那份也是这么解出来的）
    if OUT_DIR.exists():
        diff = [n for n, d in got.items()
                if (OUT_DIR / n).exists() and (OUT_DIR / n).read_bytes() != d]
        extra = [p.name for p in OUT_DIR.glob('*.WSC') if p.name not in got]
        if diff:
            print('  ⚠ %d 个与现有语料不同：%s' % (len(diff), ', '.join(sorted(diff)[:5])))
        if extra:
            print('  ⚠ 现有语料里有 %d 个不在原版归档中：%s'
                  % (len(extra), ', '.join(sorted(extra)[:5])))
        if not diff and not extra:
            print('  现有语料与本次解出一致（逐字节）')

    if not args.write:
        print('（未指定 --write，未写入）')
        return 0

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    written = 0
    for name, data in sorted(got.items()):
        dst = OUT_DIR / name
        if dst.exists() and dst.read_bytes() == data:
            continue
        dst.write_bytes(data)
        written += 1
    (OUT_DIR / 'manifest.json').write_text(json.dumps(
        {'_说明': '原版 WSC 语料（已解密）。**构建期派生数据，不入库** —— 由 '
                  'script/gen/extract_wsc_corpus.py 从用户自备的原版 Rio.arc 解出；'
                  '著作权属 FlyingShine / MoeNovel，本仓库不随补丁分发。',
         'source': 'CROSS_CHANNEL_Original/Rio.arc（每字节 ror 2）',
         'members': {n: {'bytes': len(d), 'sha256': hashlib.sha256(d).hexdigest()[:16]}
                     for n, d in sorted(got.items())}},
        ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    print('写出 %d 个 -> %s（其余未变）' % (written, OUT_DIR.relative_to(ROOT)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
