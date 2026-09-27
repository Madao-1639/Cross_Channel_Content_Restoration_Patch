"""构建「就地插入」所需的资源重命名表（原版名 -> 本补丁可用的名字）。

1. **事件 CG**：**直接读 `resource/cg_map.json`**（由 `script/build_cg_map.py` 生成），
   不再在此推导。表里**只含改过名的**条目（原版名 -> `EVCC9XXX`）；查不到即保持原名
   （同名且内容一致，用 Steam 侧的文件即可）。识别方式=像素而非 sha：Res303 出货的图
   被 LANCZOS 放大并重编码过，同一张画的 sha 必然不同。
   还原范围（剧情顺序 + 切片区间）读 `resource/scene_slices.json`，与 splice 共用一份。

2. **立绘**：原版 `TC{角色}0{nnn}{变体}` -> Steam `TC{角色}1{nnn}{变体}`。
   立绘名三个维度：**档位**（0 最远/1 标准/2 最近，数字越大越放大）、
   **姿势编号**（动作/表情，`TCMM0002C` ↔ `TCMM1002C` 是同姿势同色调、只差缩放）、
   **变体**（色调，构图不变）。原版只用档位 0；Steam 三档都有，但档位 0 只有 41 个文件
   （本表引用的 38 个立绘里只有 19 个有同档），**混档会让同框角色大小不一致**，
   故统一取档位 1 —— 全覆盖且同框一致，代价是构图比原版略近。
   另：原版是 `.PNG`+`.MSK`、Steam 是 `.PNA`。

每个映射都要在归档里**核对目标资源真实存在**；解不掉的条目单独列出，不静默跳过。

用法（项目根目录）：
    python script/build_rename_map.py            # 打印表 + 覆盖度
    python script/build_rename_map.py --json out.json
"""
import argparse
import io
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tool import arcbuild, wsc  # noqa: E402

CG_MAP_PATH = ROOT / 'resource' / 'cg_map.json'
SLICES_PATH = ROOT / 'resource' / 'scene_slices.json'
CG_RE = re.compile(r'^(EVCC\d{4})([A-Z]?)$')
# 立绘：`TC{角色2}0{档位?}{编号3}{变体≤2}` —— 变体可有两字母（如 `TCYM0000AA`/`AS`）
TC_RE = re.compile(r'^(TC[A-Z]{2})0(\d{3})([A-Z]{0,2})$')
ARCHIVES = ['asset/Chip1.arc', 'asset/Chip2.arc', 'asset/Graphic.arc', 'asset/Voice.arc',
            'backup/Chip1.arc', 'backup/Chip2.arc', 'backup/Graphic.arc', 'backup/Voice.arc']


def load_cg_map():
    """读事件 CG 改名表 -> {原版文件名: 补丁文件名}。缺表即中止 —— 本脚本不自带推导逻辑。

    表里只有**改了名**的（同名且内容一致的不入表）；查不到即保持原名。
    """
    if not CG_MAP_PATH.exists():
        raise SystemExit('缺少 %s：先跑 python script/build_cg_map.py' % CG_MAP_PATH)
    d = json.loads(CG_MAP_PATH.read_text(encoding='utf-8'))['renamed']
    return {k.upper(): v['patch'].upper() for k, v in d.items()}


def load_slices():
    """还原范围（剧情顺序 + 切片区间）来自 resource/scene_slices.json，与别处共用一份。"""
    if not SLICES_PATH.exists():
        raise SystemExit('缺少 %s' % SLICES_PATH)
    return json.loads(SLICES_PATH.read_text(encoding='utf-8'))['scenes']


def load_arc(path):
    return {n.decode('utf-16-le').upper(): v for n, v in arcbuild.read_raw(path)}


def referenced_names(scenes):
    """所有插入区间引用到的图像/语音/蒙版名。区间由调用方从 scene_slices.json 取。"""
    imgs, voices = set(), set()
    for sc in scenes:
        stem, lo, hi = sc['src'], sc['lo'], sc['hi']
        p = ROOT / 'resource' / 'corpus' / 'wsc' / (stem + '.WSC')
        if not p.exists():
            raise SystemExit('缺少 %s' % p)
        instrs = wsc.disassemble(p.read_bytes())
        dl = [i for i in instrs if i.opcode in (0x41, 0x42)]
        hi = min(hi, len(dl) - 1)
        a, b = dl[lo].offset, dl[hi].offset + dl[hi].size
        for i in instrs:
            if not (a <= i.offset < b):
                continue
            if i.opcode in (0x46, 0x48):
                imgs.add(i.fields['name'].upper())
            elif i.opcode == 0x23:
                voices.add(i.fields['name'].upper() + '.OGG')
    return imgs, voices


def ext_of(name):
    """引用名 -> 归档里的扩展名。立绘是 .PNA，事件 CG / 背景 / 蒙版是 .PNG。"""
    return '.PNA' if TC_RE.match(name) else '.PNG'


def portrait_target(name, avail):
    """原版立绘名 -> 本补丁用的名字（**档位 0 → 1**，保姿势与变体）；目标不存在则 None。

    档位统一取 1 的理由见模块头。`avail` 是归档里实际存在的名字集合（大写）。
    """
    t = TC_RE.match(name)
    if not t:
        return None
    cand = '%s1%s%s' % (t.group(1), t.group(2), t.group(3))
    return cand if (cand + '.PNA') in avail else None


def build():
    """返回 (rename, unresolved, unchanged, cgmap, conflicts)。供其它脚本直接调用，
    不经过 tmp/ 中转文件（流水线不应依赖临时目录）。

    事件 CG 的对应关系一律取自 `resource/cg_map.json`；本函数只负责
    「按表算出可用的目标文件名，并在归档里核对它真实存在」。
    """
    cg_by_file = load_cg_map()               # {原版文件名: 补丁文件名}，只含改过名的
    cgmap = {k[:-4]: v[:-4] for k, v in cg_by_file.items()}
    conflicts = []
    avail = set()
    for p in ARCHIVES:
        if (ROOT / p).exists():
            avail |= set(load_arc(ROOT / p))
    rename, unresolved, unchanged = {}, [], []
    imgs, voices = referenced_names(load_slices())
    # (原名, 扩展名) 的统一清单
    items = [(n, ext_of(n)) for n in sorted(imgs)] + \
            [(v[:-4], '.OGG') for v in sorted(voices)]
    for n, ext in items:
        new = None
        if ext == '.PNG':
            # 表里有 → 改成补丁名；表里没有 → 同名同内容，**保持原名**
            new = cg_by_file.get((n + '.PNG').upper())
            if new is not None:
                new = new[:-4]
        else:
            t = TC_RE.match(n)
            if t:
                new = portrait_target(n, avail)
        target = (new or n) + ext
        if new is not None:
            rename[n] = new
        if target in avail:
            if new is None:
                unchanged.append(target)
        else:
            unresolved.append('%s%s%s' % (n, ' -> %s' % new if new else '', '（缺 %s）' % target))
    return rename, unresolved, unchanged, cgmap, conflicts


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    ap = argparse.ArgumentParser()
    ap.add_argument('--json', default=None, help='把映射表写到指定 JSON')
    args = ap.parse_args()

    rename, unresolved, unchanged, cgmap, conflicts = build()
    print('事件 CG：读 resource/cg_map.json，本补丁有 EVCC9XXX 副本的 %d 个'
          % len(cgmap))
    print('  编号区间 EVCC%04d..EVCC%04d'
          % (min(int(v[4:8]) for v in cgmap.values()),
             max(int(v[4:8]) for v in cgmap.values())))
    if conflicts:
        print('  ⚠ %s' % conflicts)

    print()
    print('重命名条目 %d 条：' % len(rename))
    for k in sorted(rename):
        print('   %-14s -> %s' % (k, rename[k]))
    print()
    print('无需改名且已存在 %d 个' % len(unchanged))
    print('**找不到出处** %d 个:' % len(unresolved))
    for u in sorted(unresolved)[:40]:
        print('   ', u)
    if len(unresolved) > 40:
        print('    ... 另 %d 个' % (len(unresolved) - 40))

    if args.json:
        Path(args.json).write_text(json.dumps(
            {'rename': rename, 'unresolved': sorted(unresolved)}, ensure_ascii=False, indent=1),
            encoding='utf-8')
        print('\n已写出 %s' % args.json)
    return 0


if __name__ == '__main__':
    sys.exit(main())
