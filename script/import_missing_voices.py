"""把就地插入扩出来的覆盖范围所需、而补丁里还没有的语音从原版 Voice.arc 补入。

背景：Res303 的还原脚本只覆盖了场景的一部分，就地插入把每段补到「交接点」之后，
新覆盖的那几百句所引用的语音 `asset/Voice.arc` 里没有（共 186 条，如
`KRI061C3027.OGG`、`MKI179D4001.OGG`），但**原版 Voice.arc 里都有**。

要补哪些：直接调用 `script/build_rename_map.py` 的 `build()`，取它的「找不到出处」清单
（它已经把所有插入区间的引用与 asset+backup 全量核对过）。

关于 `.soundlevel`：原版 Voice.arc 只有 `.OGG`，Steam 侧才带 `.soundlevel`（ASCII 的
逐段音量包络）。本项目已有先例：`YOU035A5000`..`YOU042A5000` 这 8 条就是无 soundlevel
补入的，而 CNR0005 实机验证通过、它们正常播放 —— 所以**只补 OGG 即可**，不凭空造包络
（`CLAUDE.md`「缺少的信息不应猜测补全」）。

用法（项目根目录）：
    python script/import_missing_voices.py --check
    python script/import_missing_voices.py --write
"""
import argparse
import io
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tool import arcbuild  # noqa: E402

if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

ORIG_VOICE = ROOT / '..' / 'CROSS_CHANNEL_Original' / 'Voice.arc'
ASSET_VOICE = ROOT / 'asset' / 'Voice.arc'
BACKUP = ROOT / 'asset' / 'Voice.arc.before_import'
UNRESOLVED_RE = re.compile(r'^(.+?)（缺 (.+?)）$')


def wanted():
    """待补语音清单。直接调用重命名表构建器，不经过 tmp/ 中转文件。"""
    sys.path.insert(0, str(ROOT / 'script'))
    import build_rename_map
    _rename, unresolved, _unchanged, _cg, _conf = build_rename_map.build()
    missing = []
    for u in unresolved:
        m = UNRESOLVED_RE.match(u)
        if m and m.group(2).endswith('.OGG'):
            missing.append(m.group(2))
    return sorted(set(missing))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--check', action='store_true')
    ap.add_argument('--write', action='store_true')
    args = ap.parse_args()

    need = wanted()
    if not need:
        print('没有需要补入的语音（重命名表核对的「找不到出处」清单为空）')
        return 0
    print('待补语音 %d 条' % len(need))

    asset = list(arcbuild.read_raw(ASSET_VOICE))
    have = {n.decode('utf-16-le').upper() for n, _ in asset}
    print('asset/Voice.arc 现有 %d 个成员（%.1f MB）'
          % (len(asset), sum(len(d) for _, d in asset) / 1e6))

    print('读取原版 %s ...' % ORIG_VOICE)
    orig = {n.decode('shift_jis', 'replace').upper(): d
            for n, d in arcbuild.read_old_arc(ORIG_VOICE)}
    got, absent = [], []
    for name in need:
        if name.upper() in have:
            continue
        if name.upper() in orig:
            got.append((name.upper(), orig[name.upper()]))
        else:
            absent.append(name)
    print('  可补入 %d 条；原版也没有的 %d 条 %s' % (len(got), len(absent), absent[:5]))
    if absent:
        raise SystemExit('原版缺资源，中止（不许静默跳过）')
    print('  补入后总大小 +%.1f MB' % (sum(len(d) for _, d in got) / 1e6))

    if not args.write:
        print('\n（未指定 --write，未写入）')
        return 0

    if BACKUP.exists():
        print('\n[备份] 已存在 %s' % BACKUP)
    else:
        arcbuild.write_arc(asset, BACKUP)
        print('\n[备份] asset/Voice.arc -> %s' % BACKUP)

    out = list(asset) + [(n.encode('utf-16-le'), d) for n, d in got]
    arcbuild.write_arc(out, ASSET_VOICE)
    back = {n.decode('utf-16-le').upper() for n, _ in arcbuild.read_raw(ASSET_VOICE)}
    miss = [n for n, _ in got if n not in back]
    if miss:
        raise SystemExit('[失败] 回读缺少：%s' % miss[:5])
    count, size, _ = arcbuild.verify(ASSET_VOICE)
    print('[写入] 新增 %d 条；回读校验通过（%d 成员，%d 字节，无 padding）'
          % (len(got), count, size))
    return 0


if __name__ == '__main__':
    sys.exit(main())
