"""构建「就地插入」所需的资源重命名表（原版名 -> 本补丁可用的名字）。

三类，全部按**内容/规则**推导，不手抄：

1. **事件 CG**：原版基号 -> 当前 `EVCC9XXX` 编号。
   编号已经由 `script/renumber_evcc9xxx.py` 按引用顺序分配并稳定下来；这里反过来用
   `asset/Chip2.arc` 里每个 `EVCC9xxx` 文件的**内容哈希**查 `BASE_OF_SHA` 得到原版基号，
   再读文件名里的编号 —— 所以本表可重复生成，且与 `renumber` 工具同源。
   变体字母（`EVCC0002B` 的 `B`）原样保留：`EVCC0002` + `B` -> `EVCC9005` + `B`。

2. **立绘**：原版 `TC{角色}0{nnn}{变体}` -> Steam `TC{角色}1{nnn}{变体}`。
   两版立绘被重编过（原版 `TCMM0002C` ↔ Steam `TCMM1001C`），且原版是 `.PNG`+`.MSK`、
   Steam 是 `.PNA`；实测这条规则在全部还原区间的引用上成立。

3. **并入序列的系统图**：`SGCC0020` -> 序列内编号（同样由内容哈希反查）。

每个映射都要在归档里**核对目标资源真实存在**；解不掉的条目单独列出，不静默跳过。

用法（项目根目录）：
    python script/build_rename_map.py            # 打印表 + 覆盖度
    python script/build_rename_map.py --json out.json
"""
import argparse
import hashlib
import io
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tool import arcbuild, wsc  # noqa: E402

sys.path.insert(0, str(ROOT / 'script'))
# 先导入：renumber_evcc9xxx 在导入时会自己包一层 sys.stdout，先包后包会互相关掉底层 buffer
from renumber_evcc9xxx import BASE_OF_SHA, new_name  # noqa: E402


# 抽取区间 = 各场景的插入段（源 WSC 索引，含端点）
SLICES = {
    'CCA0025C': (15, 570), 'CCB1014C': (15, 569), 'CCB2013': (0, 546),
    'CCB2101': (0, 236), 'CCC0000': (195, 363), 'CCC3027': (203, 807),
    'CCC4022': (135, 614), 'CCD0022A': (7, 348), 'CCD1001': (980, 1560),
    'CCD4003': (486, 864), 'CCD5001': (450, 1111),
}
CG_RE = re.compile(r'^(EVCC\d{4})([A-Z]?)$')
TC_RE = re.compile(r'^(TC[A-Z]{2})0(\d{3})([A-Z]?)$')
ARCHIVES = ['asset/Chip1.arc', 'asset/Chip2.arc', 'asset/Graphic.arc', 'asset/Voice.arc',
            'backup/Chip1.arc', 'backup/Chip2.arc', 'backup/Graphic.arc', 'backup/Voice.arc']


def load_arc(path):
    return {n.decode('utf-16-le').upper(): v for n, v in arcbuild.read_raw(path)}


def cg_base_to_number():
    """原版 CG 基号 -> 当前 EVCC9XXX 编号（内容哈希反查，与 renumber 工具同源）。"""
    chip2 = load_arc(ROOT / 'asset' / 'Chip2.arc')
    out, conflicts = {}, []
    for name, data in chip2.items():
        if not name.startswith('EVCC9'):
            continue
        base = BASE_OF_SHA.get(hashlib.sha256(data).hexdigest())
        if base is None:
            conflicts.append(name)
            continue
        num = int(name[4:8])
        if out.setdefault(base, num) != num:
            conflicts.append('%s: %s 与 %d 冲突' % (name, base, out[base]))
    return out, conflicts


def referenced_names():
    """所有插入区间引用到的图像/语音/蒙版名。"""
    imgs, voices = set(), set()
    for stem, (lo, hi) in SLICES.items():
        p = ROOT / 'tmp' / 'corpus' / 'wsc' / (stem + '.WSC')
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


def build():
    """返回 (rename, unresolved, unchanged, cgmap, conflicts)。供其它脚本直接调用，
    不经过 tmp/ 中转文件（流水线不应依赖临时目录）。"""
    cgmap, conflicts = cg_base_to_number()
    avail = set()
    for p in ARCHIVES:
        if (ROOT / p).exists():
            avail |= set(load_arc(ROOT / p))
    rename, unresolved, unchanged = {}, [], []
    imgs, voices = referenced_names()
    # (原名, 扩展名) 的统一清单
    items = [(n, ext_of(n)) for n in sorted(imgs)] + \
            [(v[:-4], '.OGG') for v in sorted(voices)]
    for n, ext in items:
        new = None
        if n == 'SGCC0020' and 'SGCC0020' in cgmap:
            new = new_name(n + '.PNG', cgmap['SGCC0020'])[:-4]
        else:
            m = CG_RE.match(n)
            if m and m.group(1) in cgmap:
                new = new_name(n + '.PNG', cgmap[m.group(1)])[:-4]
            else:
                t = TC_RE.match(n)
                if t:
                    cand = '%s1%s%s' % (t.group(1), t.group(2), t.group(3))
                    if (cand + '.PNA') in avail:
                        new = cand
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
    print('原版 CG 基号 -> 编号：%d 个（编号 %d..%d）'
          % (len(cgmap), min(cgmap.values()), max(cgmap.values())))
    if conflicts:
        print('  ⚠ 未入表（哈希不在 BASE_OF_SHA）: %s' % conflicts)

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
