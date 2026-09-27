# -*- coding: utf-8 -*-
"""核对事件 CG 的引用与命名 —— 对照 `resource/cg_map.json`。

**本脚本不改写任何东西。** 事件 CG 的原版↔补丁对应关系由
`script/build_cg_map.py` 生成到 `resource/cg_map.json`；真正应用改名的是
`tool/rename_map.py` + `script/splice_restoration.py`（每个宿主脚本都由
splice 从源 WSC 重新生成，改名在那一趟里落到 ws2 字节上）。

为什么不在这里做全局改名：**同一个原版名在不同脚本里含义不同**。例如
`SGCC0020.PNG` ——
  · `CCB2101_en.ws2` 的**插入段**引用的是**原版**的系统图 → 该用 `EVCC9004.PNG`；
  · `CCB2101_en.ws2` 的**宿主前缀**（Steam 自己的内容）用 `0x33 bg02 SGCC0020.PNG`
    引用 **Steam 的**同名图 → 必须保持原名；
  · `CCD2002B_en.ws2`、画廊页 `CG_PAGE.ws2` 也各自引用 Steam 的同名图 → 保持原名。
全局替换会把后两类改坏（加载不到图）。所以本脚本只**报告**。

核对项：
  1. `asset/Rio.arc` 里出现的每个 EVCC/SGCC 引用名，都必须能在
     `asset/*.arc` 或 `backup/*.arc` 里找到（找不到 → 场景会显示不出图）；
  2. 表里每个改名目标必须真实存在于归档里。
（**未入表的引用 = 同名同内容**，解析到 Steam 侧同名文件即可，无需核对。）

用法（项目根目录）：
    python script/renumber_evcc9xxx.py            # 核对，非 0 退出码表示有问题
    python script/renumber_evcc9xxx.py --list     # 附带打印引用清单
"""
import io
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tool import arcbuild, ws2  # noqa: E402

ASSET = ROOT / 'asset'
RIO = ASSET / 'Rio.arc'
CG_MAP = ROOT / 'resource' / 'cg_map.json'
NAME_RE = re.compile(rb'\x00(EVCC\d{4}[A-Z]?\.PNG|SGCC\d{4}[A-Z]?\.PNG)\x00', re.I)
ARCHIVES = ['asset/Chip1.arc', 'asset/Chip2.arc', 'asset/Graphic.arc', 'asset/Voice.arc',
            'backup/Chip1.arc', 'backup/Chip2.arc', 'backup/Graphic.arc', 'backup/Voice.arc']


def load_map():
    if not CG_MAP.exists():
        raise SystemExit('缺少 %s：先跑 python script/build_cg_map.py' % CG_MAP)
    return json.loads(CG_MAP.read_text(encoding='utf-8'))['renamed']


def main():
    if hasattr(sys.stdout, 'buffer'):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    listing = '--list' in sys.argv
    cg = load_map()
    avail = set()
    for p in ARCHIVES:
        if (ROOT / p).exists():
            avail |= {n.decode('utf-16-le').upper() for n, _ in arcbuild.read_raw(ROOT / p)}

    refs = {}
    for n, v in arcbuild.read_raw(RIO):
        script = n.decode('utf-16-le')
        for m in NAME_RE.finditer(ws2.decode(v)):
            f = m.group(1).decode('ascii').upper()
            refs.setdefault(f, []).append(script)

    problems = []
    for f, scripts in sorted(refs.items()):
        if f not in avail:
            problems.append('引用 %s（%s）在归档里找不到' % (f, '、'.join(sorted(set(scripts))[:3])))
    for f, e in sorted(cg.items()):
        if e['patch'].upper() not in avail:
            problems.append('表里 %s -> %s，但目标不在归档里' % (f, e['patch']))

    print('resource/cg_map.json：%d 个 CG；asset/Rio.arc 里引用到 %d 个名字'
          % (len(cg), len(refs)))
    ns = sorted(int(v['patch'][4:8]) for v in cg.values())
    print('改名映射 %d 条，编号 EVCC%04d..EVCC%04d（其余引用同名）'
          % (len(cg), ns[0], ns[-1]))
    if listing:
        print()
        for f, scripts in sorted(refs.items()):
            print('   %-16s -> %-16s  %s'
                  % (f, (cg.get(f) or {}).get('patch') or '（同名，不改）',
                     '、'.join(sorted(set(scripts)))))
    print()
    if problems:
        print('问题 %d 处：' % len(problems))
        for p in problems:
            print('   %s' % p)
        return 1
    print('[OK] 全部引用可解析，表内映射均已在归档里落实')
    return 0


if __name__ == '__main__':
    sys.exit(main())
