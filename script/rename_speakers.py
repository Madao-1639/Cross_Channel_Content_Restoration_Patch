"""角色名汉化：把 ws2 里 `15 %LC<英文名>` 换成中文名，并重算绝对偏移。

背景
----
`0x15` 的说话人前缀是**脚本内的原始字节**，**不经 lng 替换**（lng 只覆盖 `14` 与 `0f`，
条数核对已证）。所以名字框显示的是 ws2 里的英文名，要汉化必须直接改脚本。

映射取自 doc/localization.md「角色名称映射」（由三方数据实证：原版 WSC 的 `0x42` 说话人、
汉化组 CCS 的 `[中文名]`、Steam 的 `%LC`）。

**编码（未定论，必须先实机确认）**
--------------------------------
`%LC` 用什么**字节编码**，我们**没有硬证据**：

* `14` 正文是 CP932 日文，但那**只说明源侧写法** —— 运行时它被 lng（UTF-16LE）按位置替换，
  引擎未必需要解码它；原生语料 27,122 处 `%LC` 名字**全是 ASCII**，没有任何多字节先例，
  Res303 也从未写过非 ASCII 名字 —— 所以「引擎只吃 CP932」只是推断，不是事实。
* 字体是中文的（Res303 替换的 Fonts.arc），说明引擎**能渲染汉字**；但**简体专用字
  （见/雾/贵/樱/游/纱/师/长/丰/咪…）不在 CP932 里**，若引擎真的只按 CP932 解码，
  这些字根本写不进去。

**所以名字一律用简体中文，编码做成开关**（`--encoding`，默认 `utf-8`）：
`utf-8` / `gbk` / `utf-16le` 都能编出全部 75 条；`cp932` 会有 35 条编不出（属预期）。
`asset/Script.arc` 里 `LegacyGame_utf8.lua` 与 `LegacyGame.lua` **并存**，
暗示引擎有 utf8 路径 —— 这也是默认选 `utf-8` 的理由。实机 A/B 的做法：
改一个角色、一个脚本，分别按三种编码写，看哪个显示正确**且与台词 lng 用字一致**。

变长 -> 必须重算绝对偏移
-----------------------
中文名多为 2–3 字节/字，`15` 会变长，其后所有字节偏移都会移动。带**文件内绝对偏移**的指令只有
两个：`06 <u32 目标>`（目标在指令 +1）与 `01 mode=0x85 <u32 b>`（在 +12）。本脚本序列化时
建立「旧偏移 -> 新偏移」映射，逐个改写这两个字段。

用法（项目根目录）：
    python script/rename_speakers.py                       # 只诊断
    python script/rename_speakers.py --write               # 写入（默认 utf-8）
    python script/rename_speakers.py --write --encoding gbk
"""
import argparse
import io
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tool import arcbuild, ws2, ws2disasm  # noqa: E402

if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

RIO = ROOT / 'asset' / 'Rio.arc'
BACKUP = ROOT / 'asset' / 'Rio.arc.before_speaker_zh'

# %LC 英文名 -> 中文名。注释标注「原版写法」= 简体的那个字 CP932 编不了，改用日文/繁体字形。
SPEAKER_ZH = {
    'Taichi': '太一',
    'Misato': '见里',
    'Miki': '美希',
    'Kiri': '雾',
    'Touko': '冬子',
    'Tomoki': '友贵',
    'Youko': '曜子',
    'Nanaca': '七香',
    'Sakuraba': '樱庭',
    'Yusa': '游纱',
    'Shinkawa': '新川',
    'Miyuki': '美幸',
    'Matron': '岳母',
    'Lunch Lady': '阿姨',
    'Voice': '一个声音',
    'Both': '二人',
    'All 3': '三人',
    'All 4': '冬子·见里·美希·友贵',
    'Stomach': '肚子里的虫',
    'Hayasugi': '曜子老师',
    'Dead Tomoki': '友贵的尸体',
    'Club President': '社长',
    'Kiri/Taichi': '雾·太一',
    'Karade Master': '老控手道大师',
    'Masamune': '政宗',
    'Yutaka': '丰',
    'Juuzou': '重藏',
    'Woman': '女声',
    'Girl': '少女',
    'Boy': '少年',
    '???': '＊＊',
    '??': '＊',

    # ---- 由基表机械派生（括号 / 斜杠组合 / Dead X 后缀）----
    '(Taichi)': '太一',
    '(Touko)': '冬子',
    'Taichi (Delusional)': '太一（妄想）',
    "Taichi's Voice": '太一的声音',
    'Sakuraba/Tomoki': '樱庭·友贵',
    'Miki/Kiri': '美希·雾',
    'Kiri/Miki': '雾·美希',
    'Misato/Kiri': '见里·雾',
    'Miki/Misato': '美希·见里',
    'Misato/Touko': '见里·冬子',
    'Taichi/Touko': '太一·冬子',
    'Tomoki/Taichi': '友贵·太一',
    'Kiri/Touko': '雾·冬子',
    'Tomoki/Misato': '友贵·见里',
    'Taichi/Miki': '太一·美希',
    'Taichi/Sakuraba': '太一·樱庭',
    'Miki/Dr. Oldman': '美希·老医生',
    'Dead Misato': '见里的尸体',
    'Dead Kiri': '雾的尸体',
    'Dead Sakuraba': '樱庭的尸体',

    # ---- 从台词的中文 lng 反推（这些角色出现在 Steam 独有剧情里，没有源 WSC 可配，
    #      但台词本身点名了中文写法）----
    'Mimi': '咪咪',
    'Mimimi': '咪咪咪',
    'Poko': '波可',
    'Akira': '阿基拉',        # "谢谢你，阿基拉"
    'Michiru': '美知留',      # "美知留……打起精神"
    'Usamimi': '兔耳',       # "《兔耳侦探》"（剧名）
    'Fukuhara': '福原',      # "这位是福原美幸"
    'Tomokichi': '友吉',     # "永别了，友吉"
    'Misako': '美佐子',       # "等等，美佐子！"

    # ---- 泛称 / 非人名，按语义直译 ----
    'Dr. Oldman': '老医生',   # 校医「剖腹产薰」，自称老夫（台词里给的名是「薰」，
                             # 这里按英文原样用泛称，不提前剧透真名）
    'Vet': '兽医',
    'Student': '学生',
    'Male Student A': '男学生A',
    'Male Student B': '男学生B',
    'Male Student C': '男学生C',
    'Female Students': '女学生们',
    'Anatomy Model': '解剖模型',
    'Announcement': '广播',
    'All': '全体',
    'All Girls': '全体女生',
    'All together': '大家',
    'Scream': '尖叫',
    'Attacker': '袭击者',
    'Recording': '录音',
    'Shark': '鲨鱼',
}


def rewrite(raw, mapping, encoding='utf-8'):
    """改 `15 %LC` 前缀并重算绝对偏移。返回 (新字节, 改动数, 未映射的名字集合)。

    `encoding` 决定中文名写成什么字节 —— 见文件头「编码」一节，**需实机确认**。
    """
    ins = ws2disasm.disassemble(ws2.decode(raw))
    out = bytearray()
    old2new, patches, n_changed = {}, [], 0
    unmapped = set()
    for i in ins:
        old2new[i.offset] = len(out)
        prefix_new = None
        if i.opcode == 0x15:
            # 用反汇编器已解好的字段：`raw` 是 rot6 编码态，逐字节都被变换过，
            # 直接拿编码字节去比对 `%LC` 字面量永远不会命中
            pre = str(i.fields.get('prefix') or '')
            if pre.startswith('%LC'):
                nm = pre[3:]
                zh = mapping.get(nm)
                if zh is None:
                    if nm:
                        unmapped.add(nm)
                elif zh != nm:
                    prefix_new = '%LC' + zh
        if prefix_new is not None:
            out += ws2.encode(b'\x15' + prefix_new.encode(encoding) + b'\x00\x00')
            n_changed += 1
            continue
        if i.opcode == 0x06:
            patches.append((len(out) + 1, i.fields['target']))
        elif i.opcode == 0x01 and i.fields.get('mode') == 0x85:
            patches.append((len(out) + 12, i.fields['b']))
        out += raw[i.offset:i.offset + i.size]
    for at, old_t in patches:
        new_t = old2new.get(old_t)
        if new_t is None:
            raise SystemExit('绝对偏移 %d 不对应任何指令起点' % old_t)
        out[at:at + 4] = ws2.encode(struct.pack('<I', new_t))
    return bytes(out), n_changed, unmapped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--write', action='store_true')
    ap.add_argument('--encoding', default='utf-8',
                    choices=['utf-8', 'gbk', 'utf-16le', 'cp932'],
                    help='%LC 中文名写成什么字节（默认 utf-8；需实机确认）')
    args = ap.parse_args()

    # 先自检映射表可编码
    for en, zh in SPEAKER_ZH.items():
        try:
            zh.encode(args.encoding)
        except UnicodeEncodeError as e:
            raise SystemExit('映射表里 %r -> %r 无法用 %s 编码：%s' % (en, zh, args.encoding, e))
    print('映射表 %d 条，全部可用 %s 编码' % (len(SPEAKER_ZH), args.encoding))

    members = list(arcbuild.read_raw(RIO))
    results, n_scripts, n_total, unmapped_all = {}, 0, 0, set()
    for nb, data in members:
        nm = nb.decode('utf-16-le').upper()
        if not nm.endswith('.WS2'):
            continue
        new, n, un = rewrite(data, SPEAKER_ZH, args.encoding)
        unmapped_all |= un
        if n:
            n_scripts += 1
            n_total += n
            results[nm] = new
    print('待改名：%d 个脚本，%d 处' % (n_scripts, n_total))
    print('表外名字（保持英文）%d 种：%s' % (len(unmapped_all), sorted(unmapped_all)[:20]))

    if not args.write:
        print('\n（未指定 --write，未写入）')
        return 0
    if BACKUP.exists():
        print('\n[备份] 已存在 %s' % BACKUP)
    else:
        arcbuild.write_arc(members, BACKUP)
        print('\n[备份] asset/Rio.arc -> %s' % BACKUP)
    out = [(nb, results.get(nb.decode('utf-16-le').upper(), d)) for nb, d in members]
    arcbuild.write_arc(out, RIO)
    count, size, _ = arcbuild.verify(RIO)
    print('[写入] %d 个脚本；回读校验通过（%d 成员，%d 字节）' % (len(results), count, size))

    # 幂等复跑
    again = 0
    for nb, data in arcbuild.read_raw(RIO):
        if nb.decode('utf-16-le').upper().endswith('.WS2'):
            again += rewrite(data, SPEAKER_ZH, args.encoding)[1]
    print('[幂等] 复跑待改 %d 处（应为 0）' % again)
    return 0


if __name__ == '__main__':
    sys.exit(main())
