"""按「脚本引用顺序」重编 EVCC9XXX 编号，并把 CN_SGCC0020.PNG 并入该序列。

规则
----
1. **脚本顺序 = 剧情顺序**：`CNR0001 → CNR0002 → … → CNR00011`（CNR 编号本身即按章节与
   场景号递增，与调用链一致，见 doc/call-chain.md）；同一脚本内按指令偏移递增。
2. 在该顺序下**首次被引用的「基号」**拿 9000，其后依次 9001、9002……。
   同一张 CG 的不同差分（`EVCC0031` / `EVCC0031A` / `EVCC0031B`…）**共用一个编号**，
   只用变体字母区分——编号数 = 基号数，不是文件数。
3. `CN_SGCC0020.PNG` 按其在 `CNR0004_en.ws2` 中的引用位置并入同一序列，不再直接取"下一个空号"。

基号由**内容哈希**查表（`BASE_OF_SHA`，由 Res303 的 `CN_EVCC*/CN_SGCC*` 命名反查得到），
与文件名无关，因此本脚本可安全重复运行。

这样"编号小的 CG 一定先被引用"，编号本身可读。

其余一并处理（该项目此前的遗留问题）：
- `CN_SGCC0020.PNG` 违反 `CN_` 前缀（引擎只认 `EVCC`，见 doc/lessons-learned.md §12）
  与归档规则（事件 CG 归 Chip2.arc），本脚本把它改名为序列内的 `EVCC####.PNG` 存入 Chip2.arc，
  并从 Graphic.arc 删除。
- 改名只改数字段，名字长度不变（`EVCC` + 4 位 + 可选字母 + `.PNG`），因此**脚本内不含需要
  重算的文件内偏移**；唯一变短的是 `CN_SGCC0020.PNG`（15 → 12 字节），其宿主 CNR0004 已确认
  无 `0x06` / `01 mode=0x85`。

用法（项目根目录）：
    python script/renumber_evcc9xxx.py            # 应用并回读校验
    python script/renumber_evcc9xxx.py --check    # 只显示映射表，不写入
"""
import hashlib
import io
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tool import arcbuild, ws2, ws2disasm  # noqa: E402


ASSET = Path('asset')
GRAPHIC, CHIP2, RIO = ASSET / 'Graphic.arc', ASSET / 'Chip2.arc', ASSET / 'Rio.arc'

# 剧情顺序（= CNR 编号顺序 = 调用链顺序）
SCRIPT_ORDER = ['CNR0001_en.ws2', 'CNR0002_en.ws2', 'CNR0003_en.ws2', 'CNR0004_en.ws2',
                'CNR0005_en.ws2', 'CNR0006_en.ws2', 'CNR0007_en.ws2', 'CNR0008_en.ws2',
                'CNR00009_en.ws2', 'CNR00010_en.ws2', 'CNR00011_en.ws2']
BASE = 9000

# 内容哈希 -> 原版「基号」（由 Res303 的 CN_EVCC*/CN_SGCC* 命名按内容哈希反查得到）。
# 以**内容哈希**为键而非文件名，所以本脚本可安全重复运行：改名不会让表失效。
# 表外的文件（例如名字已按本规则编号）直接读名字里的基号。
BASE_OF_SHA = {
    '6ee97bf9b51b1544e34860b8f9ba62614e66c4ed25f4d53c61263f732bbf188a': 'EVCC0012',
    'f8f99fbac98d895dd95dde5ca749f406d0b4b10329492cdbf890fd74f675bb53': 'EVCC0013',
    'db6852947762c7d287ecee166a2b3a376cf7f9c8792cf8f1d406e9ca91811a63': 'EVCC0014',
    'a2a9b892e5c5dc32bf4a38d381140393e1aa8e63d92c0d7f56ad72942f8c8a3f': 'EVCC0015',
    '41728a8ba65d58c3a42d8e4eb7124c804495a3d5bebd0bbde8889c8ab48ed486': 'SGCC0020',
    'd559a49af9c2aa3e788ed8ec3ed53a50a42a517c539a52b78bbb3fad24803b49': 'EVCC0002',
    '943a0385e286cbc45f3b38cb95b351cd7e7d9ba3ba03235405a7fd769aa6ad70': 'EVCC0048',
    'cdd80a04f7f3ccee3c922dca931334e1fd95c2347262ed2d4fa350d2bb6cad1c': 'EVCC0047',
    'a5a2a2b0d151425b8e28bade458f37d2d7d8ae173eed07dcd8002bd98890f8cc': 'EVCC0019',
    '1b334300c4a0861a76dbbb33c2b007c6a33dbcbdab88ed378f448381d8551bdb': 'EVCC0020',
    'a12ec0f326d1692c0ea02e31a178ce9398361543cdd6592a1d83fb7ad95d7602': 'EVCC0018',
    '701691077c65850c4e70110d2b01467db662d24977d5477cbb2175182a53b1b3': 'EVCC0034',
    '440a3cf1eb04c48514b6b024d1ed771873c74ea89ef81db5416e23ebfdb74e1d': 'EVCC0035',
    '44248f299e0fe92dfbb4e33591140576bb3487a9ec90c8101f9befbfa35b1dd6': 'EVCC0037',
    'e713b5ce1b35d08ad9a3839deee7fbad6d54979f355f43441029b9365e75f16d': 'EVCC0038',
    '4a8ce6b4c543d7e0a5886ecfffbefbea065ca6c02aa1dccdc82dc3fbddd7b3c1': 'EVCC0030',
    '38d2e11ec1df245a207f354ddc57a6957cd181d3532f1a28bf3f900deecf2356': 'EVCC0031',
    '43e738343689a6fbc28eed8d1a0aa0513debecf2107b7211632e088d407f116c': 'EVCC0014',
    '915079d349fcd5adc829a4e795ddd515cde0ed3ca58c58d389947533418983cd': 'EVCC0013',
    'a0ce32ae97657a21c39d196cb9779da8913614ea3813e753c29a2f0c92281b15': 'EVCC0014',
    '3d1e0bfad587bd0fb49b0f0cdbe64dd1662b8b446dfae0b886505ac80917b0ef': 'EVCC0015',
    'c1d2fb734c34a644fb2a5260cb040bc5d5e37ab34ad84121aa3b75acfd30a3d5': 'EVCC0002',
    '652ab986416c861785d78a09eae682f169528075d9c6431031705f60c376f8e3': 'EVCC0017',
    '93574df08834a70b780604b7aed7cdfe3217646ee3a31e14b407ca8d5139fa12': 'EVCC0027',
    '1e0ff8f012a0c85549f05d7fa17d5dc7c16f26b37eabf99778da455b52f5b8ee': 'EVCC0048',
    'a0b52cda821be701ccb4fbebee08bffe5071cbeed581f352aaf7b0a2f9940856': 'EVCC0048',
    '33ee52d3448919a9d77a2835c9dca56c83a3a246e80b1b539dec84e8b0f6a234': 'EVCC0047',
    'a9d5187ed91c5cdb6ef62d9b613e850f514971e27aea4f39fd5094f9fbfebcbf': 'EVCC0019',
    '277b34cbee208c4a5e96a22eab622326ab1141cc0facd0a593c543d771d073b3': 'EVCC0020',
    '888717727d532c7c0ec4d95d19364fcc9cd7d660eff254147047b7f8b8a21045': 'EVCC0018',
    '9fe180454f24b26c7e8fe137ef8c6cdad04f8e93098f6cd77bbce4b9b932298f': 'EVCC0018',
    '5660ad81004d32b09a78f94fdbbbfca8e25309fa3f7af58f8a309d22310396d6': 'EVCC0034',
    'e488db4e2d6e5c305936d23dbbc434cd50076741fb377f189b435abbbda297b1': 'EVCC0030',
    'c027ddacd55ce1ff2f010b25cdfcce472538a254f23f52f3b3c061ee6b6751da': 'EVCC0038',
    '29606574882e3ec51a4549ee454603ddcd0aa0423ff08e32f54df3422870a54a': 'EVCC0030',
    '5443a616c4be48b6fbc54c3e379d87a069efb4fe9f01d44fc2542284f1525d82': 'EVCC0030',
    'a51be8c30642de071a001be7684f8dab543537abb123d6bd946618247ae8f6e2': 'EVCC0031',
}
IN_SEQUENCE = re.compile(r'^EVCC9\d{3}[A-Z]?\.PNG$')
MIGRATING = 'CN_SGCC0020.PNG'          # 由 Graphic.arc 迁入 Chip2.arc，并入本序列
NAME_RE = re.compile(rb'\x00(EVCC9\d{3}[A-Z]?\.PNG|CN_SGCC0020\.PNG)\x00')


def norm(name):
    return name.upper()


def base_name(key):
    """把 base_of 的键还原成可读名（('EVCC','0012') -> 'EVCC0012'）。"""
    return key[0] + key[1]


def base_of(digest, name):
    """取「基号」：内容哈希查表优先（与文件名无关，脚本可重复运行），否则读名字。"""
    if digest in BASE_OF_SHA:
        m = re.match(r'^([A-Z]+)(\d+)$', BASE_OF_SHA[digest])
        if not m:
            raise SystemExit('BASE_OF_SHA 表项格式异常：%s' % BASE_OF_SHA[digest])
        return (m.group(1), m.group(2))
    stem = name[:-4]
    letter = stem[-1] if stem[-1].isalpha() else ''
    body = stem[:-1] if letter else stem
    return ('EVCC', body[4:])


def new_name(old, number):
    """EVCC9004A.PNG + 编号 9017 -> EVCC9017A.PNG（保留变体字母）"""
    stem = old[:-4]
    letter = stem[-1] if stem[-1].isalpha() else ''
    return 'EVCC%04d%s.PNG' % (number, letter)


def build_mapping(rio, digests):
    """按脚本引用顺序、以「基号」为单位返回 [(old, new, first_script, first_offset)]。"""
    seq, numbers = [], {}
    for script in SCRIPT_ORDER:
        if script not in rio:
            raise SystemExit('缺少脚本：%s' % script)
        for ins in ws2disasm.disassemble(ws2.decode(rio[script])):
            if ins.opcode != 0x33:
                continue
            f = norm(ins.fields['file'])
            if not (IN_SEQUENCE.match(f) or f == MIGRATING):
                continue
            if f not in [x[0] for x in seq]:
                seq.append((f, None, script, ins.offset))
            key = base_of(digests.get(f, ''), f)
            if key not in numbers:
                numbers[key] = BASE + len(numbers)
    return [(old, new_name(old, numbers[base_of(digests.get(old, ''), old)]), script, off)
            for old, _, script, off in seq], seq


def _all_old_names(rio):
    names = set()
    for v in rio.values():
        for m in NAME_RE.finditer(ws2.decode(v)):
            names.add(m.group(1).decode('ascii'))
    return names


def main():
    if hasattr(sys.stdout, 'buffer'):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    check_only = '--check' in sys.argv
    rio = {n.decode('utf-16-le'): v for n, v in arcbuild.read_raw(RIO)}
    chip2 = {n.decode('utf-16-le'): v for n, v in arcbuild.read_raw(CHIP2)}

    # 迁入只在首次运行需要；完成后 Graphic.arc 与 Steam 原档相同、不再分发
    migrating = any(MIGRATING in ws2.decode(v).decode('latin1') for v in rio.values())
    graphic_file = GRAPHIC if GRAPHIC.exists() else Path('backup/Graphic.arc')
    graphic = ({n.decode('utf-16-le'): v for n, v in arcbuild.read_raw(graphic_file)}
               if migrating else {})
    digests = {k.upper(): hashlib.sha256(v).hexdigest() for k, v in chip2.items()}
    digests.update({k.upper(): hashlib.sha256(v).hexdigest() for k, v in graphic.items()})

    mapping, seq = build_mapping(rio, digests)
    # 幂等：映射恒等说明命名已符合本规则（编号按基号、按引用顺序分配）
    already = bool(mapping) and all(o == n for o, n, _, _ in mapping)

    if check_only:
        bases = sorted({int(n[4:8]) for _, n, _, _ in mapping})
        print('引用序列（剧情顺序）：%d 个文件 / %d 个基号，EVCC%04d..EVCC%04d'
              % (len(mapping), len(bases), bases[0], bases[-1]))
        print('  %-6s %-18s %-12s %-14s %s' % ('编号', 'Chip2 文件', '原版基号', '首次引用', '内容 sha256'))
        for old, new, script, off in mapping:
            print('  %-6s %-18s %-12s %-14s %s'
                  % (new[4:8], old, base_name(base_of(digests.get(old.upper(), ''), old)),
                     '%s @0x%x' % (script.replace('_en.ws2', ''), off),
                     digests.get(old.upper(), '')[:16]))
        print('[--check] 仅显示映射表，未写入%s' % ('（命名已符合规则）' if already else ''))
        return 0

    if already:
        bases = sorted({int(new[4:8]) for _, new, _, _ in mapping})
        print('[跳过] 已按基号 + 引用顺序编号：%d 个文件 / %d 个基号，EVCC%04d..EVCC%04d'
              % (len(mapping), len(bases), bases[0], bases[-1]))
        return 0
    print('引用序列（剧情顺序，共 %d 个 CG）：' % len(mapping))
    print('  %-6s %-18s %-18s %-14s %s' % ('新号', '旧名', '新名', '首次引用', '原版基号'))
    same = sum(1 for o, n, _, _ in mapping if o == n)
    for old, new, script, off in mapping:
        print('  %-6s %-18s %-18s %-14s %s'
              % (new[4:8], old, new, '%s @0x%x' % (script.replace('_en.ws2', ''), off),
                 base_name(base_of(digests.get(old.upper(), ''), old))))
    print('  与旧编号相同的有 %d / %d 个' % (same, len(mapping)))

    # 冲突与完整性检查
    olds = [m[0] for m in mapping]
    news = [m[1] for m in mapping]
    if len(set(news)) != len(news):
        raise SystemExit('[中止] 新编号有重复')
    for old in olds:
        if old == MIGRATING:
            if old not in graphic:
                raise SystemExit('[中止] %s 不在 %s' % (old, graphic_file))
        elif old not in chip2:
            raise SystemExit('[中止] Chip2.arc 缺少 %s' % old)
    leftover = [k for k in _all_old_names(rio)
                if IN_SEQUENCE.match(k) and k not in olds]
    if leftover:
        raise SystemExit('[中止] 序列外的 EVCC9XXX 引用未处理：%s' % leftover)

    print()
    if migrating:
        print('迁移：%s（%d B）Graphic.arc -> Chip2.arc，改名 %s' % (
            MIGRATING, len(graphic[MIGRATING]), dict((o, n) for o, n, _, _ in mapping)[MIGRATING]))
    else:
        print('（本次无迁入，仅重编号）')
    if check_only:
        print('[--check] 仅显示映射表，未写入')
        return 0

    for path in ((graphic_file,) if migrating else ()) + (CHIP2, RIO):
        backup = path.with_name(path.name + '.before_renumber')
        if backup.exists():
            print('[备份] 已存在 %s（保留原样）' % backup)
        else:
            arcbuild.write_arc(arcbuild.read_raw(path), backup)
            print('[备份] %s -> %s' % (path, backup))

    # 1) Chip2.arc：按映射改名 + 追加迁入的成员
    out = []
    for n, v in arcbuild.read_raw(CHIP2):
        name = n.decode('utf-16-le')
        out.append(((dict((o, x) for o, x, _, _ in mapping).get(norm(name), name)
                     if norm(name) in olds else name).encode('utf-16-le'), v))
    if migrating:
        out.append((dict((o, x) for o, x, _, _ in mapping)[MIGRATING].encode('utf-16-le'),
                    graphic[MIGRATING]))
    arcbuild.write_arc(out, CHIP2)

    # 2) Graphic.arc：删除迁出的成员（仅迁入时）
    if migrating:
        arcbuild.write_arc([(n, v) for n, v in arcbuild.read_raw(graphic_file)
                            if n.decode('utf-16-le') != MIGRATING], graphic_file)

    # 3) Rio.arc：单趟同时替换（避免链式改名）
    table = {o.encode('ascii'): x.encode('ascii') for o, x, _, _ in mapping}
    out, total = [], 0
    for n, v in arcbuild.read_raw(RIO):
        dec = ws2.decode(v)
        if NAME_RE.search(dec):
            dec2, n_sub = NAME_RE.subn(lambda m: b'\x00' + table[m.group(1)] + b'\x00', dec)
            total += n_sub
            v = ws2.encode(dec2)
        out.append((n, v))
    arcbuild.write_arc(out, RIO)
    print('[改写] Rio.arc 中共替换 %d 处引用' % total)

    # 回读校验
    r2 = {n.decode('utf-16-le'): v for n, v in arcbuild.read_raw(RIO)}
    c2 = {n.decode('utf-16-le').upper() for n, _ in arcbuild.read_raw(CHIP2)}
    g2 = ({n.decode('utf-16-le') for n, _ in arcbuild.read_raw(graphic_file)}
          if migrating else set())
    problems = []
    if MIGRATING in g2:
        problems.append('Graphic.arc 仍含 %s' % MIGRATING)
    for _, new, _, _ in mapping:
        if new.upper() not in c2:
            problems.append('Chip2.arc 缺少 %s' % new)
    # 引用序列：按剧情顺序，**基号**首次出现的先后必须是 BASE..BASE+n-1
    after, numbers = [], []
    for script in SCRIPT_ORDER:
        for ins in ws2disasm.disassemble(ws2.decode(r2[script])):
            if ins.opcode != 0x33:
                continue
            f = ins.fields['file'].upper()
            if IN_SEQUENCE.match(f) and f not in after:
                after.append(f)
                n = int(f[4:8])
                if n not in numbers:
                    numbers.append(n)
    if after != [new for _, new, _, _ in mapping]:
        problems.append('引用序列与映射表不一致')
    if numbers != list(range(BASE, BASE + len(numbers))):
        problems.append('基号序列不是 %d..%d：%s' % (BASE, BASE + len(numbers) - 1, numbers))
    # 引用的名字集合必须与 Chip2 成员集合一致（改名是置换，不能靠"旧名残留"判断）
    if {x for x in after} != {k for k in c2 if k.startswith('EVCC9')}:
        problems.append('脚本引用集合与 Chip2 成员集合不一致')
    if problems:
        print('\n'.join('  %s' % p for p in problems))
        raise SystemExit('[失败] 回读校验不通过')
    for path in ((graphic_file,) if migrating else ()) + (CHIP2, RIO):
        count, size, _ = arcbuild.verify(path)
        print('[回读] %-18s %4d 成员 %12d 字节 verify OK' % (path, count, size))
    print('[完成] 重编号 + 迁移已写入')
    return 0


if __name__ == '__main__':
    sys.exit(main())
