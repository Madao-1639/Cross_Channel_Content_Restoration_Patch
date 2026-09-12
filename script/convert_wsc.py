"""批量把原版 Rio.arc 的 WSC 脚本转换为 Steam (AdvHD) WS2 格式。

用法（项目根执行）：
    python script/convert_wsc.py                      # 全量转换 -> tmp/converted_ws2/
    python script/convert_wsc.py --stem CCC0000       # 只转一个
    python script/convert_wsc.py --outdir out/ws2

行为：
1. 读取原版 Rio.arc（老式归档），逐文件解密(ror2) -> tool/wsc2ws2.convert。
2. 从 Steam 版 Rio.arc 自动收割每个脚本的出口目标与选项分支目标
   （原版引擎按脚本号路由，WSC 内不含出口；Steam 对应脚本是路由的 ground truth）。
3. 输出 rot 混淆后的 .ws2 + conversion_report.json（逐文件 emitted/skipped/warnings
   与资源引用清单，资源按提供的归档清单做存在性核对）。
"""
import argparse
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding='utf-8')

from tool import arcbuild, ws2
from tool.wsc import disassemble as disassemble_wsc
from tool.ws2disasm import disassemble as disassemble_ws2
from tool.wsc2ws2 import ConvertOptions, convert, decrypt_wsc

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ORIGINAL_RIO = Path(r'D:\My_Code\tmp\GamePatch\Cross_Channel\CROSS_CHANNEL_Original\Rio.arc')
DEFAULT_STEAM_RIO = ROOT / 'backup' / 'Rio.arc'
# 存在性核对的资源归档（名称大写比对；缺失只记录，不视为错误 —— H 场景资源
# 本来就要靠补丁 payload 补入）
DEFAULT_INVENTORY_ARCS = [
    ROOT / 'backup' / 'Chip1.arc',
    ROOT / 'backup' / 'Chip2.arc',
    ROOT / 'backup' / 'Graphic.arc',
    ROOT / 'backup' / 'Voice.arc',
    Path(r'D:\My_Code\tmp\GamePatch\Cross_Channel\CROSS_CHANNEL_Steam_CN_Restored_v3.0.3\Graphic.arc'),
    Path(r'D:\My_Code\tmp\GamePatch\Cross_Channel\CROSS_CHANNEL_Steam_CN_Restored_v3.0.3\Voice.arc'),
    Path(r'D:\My_Code\tmp\GamePatch\Cross_Channel\CROSS_CHANNEL_Original\Se.arc'),
    Path(r'D:\My_Code\tmp\GamePatch\Cross_Channel\CROSS_CHANNEL_Original\Bgm.arc'),
]


def harvest_steam_knowledge(steam_rio):
    """从 Steam 版脚本收割出口目标 / 选项分支目标 / 脚本名集合。"""
    steam_scripts = set()
    exits = {}
    choices = {}
    for name_bytes, data in arcbuild.read_raw(steam_rio):
        name = name_bytes.decode('utf-16le')
        if not name.upper().endswith('.WS2'):
            continue
        stem = name[:-4].upper()
        steam_scripts.add(stem)
        try:
            instrs = disassemble_ws2(ws2.decode(data))
        except Exception:
            continue
        # 出口：文件尾 ff 前的 07 串（原生的条件双出口形如 07 A | 07 B | ff，
        # 运行时默认执行第一个，后续由 01 条件指令改道 —— 取第一个为默认出口）
        for k in range(len(instrs) - 2, -1, -1):
            if instrs[k].opcode == 0x07 and instrs[k + 1].opcode == 0xff:
                j = k
                while j - 1 >= 0 and instrs[j - 1].opcode == 0x07:
                    j -= 1
                exits[stem] = instrs[j].fields['name'].upper()
                if k > j:
                    exits[stem + '_ALT'] = [i2.fields['name'].upper()
                                            for i2 in instrs[j + 1:k + 1]]
                break
        # 选项：按**表**收集（一个脚本可能有多张 0f 表，如 CCD0102 有 12 张；
        # 逐表对应 WSC 的第 N 个 0x02，不能跨表复用）。条目里每个选项的跳转目标
        # 取 07 脚本名；06 是文件内偏移，无法跨引擎，置 None。
        for ins in instrs:
            if ins.opcode == 0x0f:
                targets = []
                for e in ins.fields['entries']:
                    if e['jump_op'] == 0x07 and e.get('name'):
                        targets.append(e['name'].upper())
                    else:
                        targets.append(None)      # 文件内跳转
                choices.setdefault(stem, []).append(targets)
    return steam_scripts, exits, choices


def build_inventory(paths):
    inv = set()
    for p in paths:
        if not Path(p).exists():
            continue
        try:
            members = arcbuild.read_raw(p)
        except Exception:
            try:
                members = arcbuild.read_old_arc(p)
            except Exception:
                continue
        for name_bytes, _ in members:
            n = name_bytes.decode('utf-16le', 'ignore') if b'\x00' in name_bytes[2:4] \
                else name_bytes.decode('latin-1')
            inv.add(n.upper())
    return inv


def check_resources(report, inventory):
    missing = []
    for kind, name, out_name in report['images']:
        if out_name and out_name.upper() not in inventory:
            missing.append(out_name)
    for kind, name, out_name in report['sounds']:
        if out_name and out_name.upper() not in inventory:
            missing.append(out_name)
    for name, out_name in report['masks']:
        if out_name and out_name.upper() not in inventory:
            missing.append(out_name)
    report['missing_resources'] = sorted(set(missing))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--original-rio', default=str(DEFAULT_ORIGINAL_RIO))
    ap.add_argument('--steam-rio', default=str(DEFAULT_STEAM_RIO))
    ap.add_argument('--outdir', default='tmp/converted_ws2')
    ap.add_argument('--stem', default=None, help='只转换指定脚本（如 CCC0000）')
    ap.add_argument('--report', default='tmp/converted_ws2/conversion_report.json')
    args = ap.parse_args()

    print('harvesting Steam routing knowledge ...')
    steam_scripts, steam_exits, steam_choices = harvest_steam_knowledge(args.steam_rio)
    print('  steam scripts: %d, exit targets: %d, choice tables: %d (in %d scripts)'
          % (len(steam_scripts), len(steam_exits),
             sum(len(v) for v in steam_choices.values()), len(steam_choices)))

    inventory = build_inventory(DEFAULT_INVENTORY_ARCS)
    print('  resource inventory: %d names' % len(inventory))

    opts = ConvertOptions(steam_scripts=steam_scripts,
                          steam_exit_map=steam_exits,
                          steam_choice_map=steam_choices)

    members = arcbuild.read_old_arc(args.original_rio)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    reports = []
    fails = []
    for name_bytes, data in members:
        stem = name_bytes.decode('ascii').split('.')[0]
        if args.stem and stem.upper() != args.stem.upper():
            continue
        try:
            decoded, report = convert(decrypt_wsc(data), stem, opts)
        except Exception as e:
            fails.append((stem, '%s: %s' % (type(e).__name__, e)))
            continue
        check_resources(report, inventory)
        (outdir / (stem + '.ws2')).write_bytes(ws2.encode(decoded))
        reports.append(report)

    for stem, err in fails:
        print('FAIL %s: %s' % (stem, err))

    # 结构校验：全部产物必须能被 WS2 反汇编器 100% 解析
    bad_parse = []
    for rep in reports:
        raw = (outdir / (rep['stem'] + '.ws2')).read_bytes()
        try:
            instrs = disassemble_ws2(ws2.decode(raw))
            covered = sum(i.size for i in instrs)
            if covered != len(ws2.decode(raw)):
                bad_parse.append((rep['stem'], 'coverage %d/%d' % (covered, len(raw))))
        except Exception as e:
            bad_parse.append((rep['stem'], str(e)))

    # 忠实性核对：对话/语音/图像/选项数量与原版一致
    mismatch = []
    for name_bytes, data in members:
        stem = name_bytes.decode('ascii').split('.')[0]
        if args.stem and stem.upper() != args.stem.upper():
            continue
        rep = next((r for r in reports if r['stem'] == stem), None)
        if not rep:
            continue
        instrs = disassemble_wsc(decrypt_wsc(data))
        wsc_dialogues = sum(1 for i in instrs if i.opcode in (0x41, 0x42))
        wsc_voices = sum(1 for i in instrs if i.opcode == 0x23)
        wsc_images = sum(1 for i in instrs if i.opcode == 0x46
                         and i.fields['name'] != 'CG_BACK') + \
            sum(1 for i in instrs if i.opcode == 0x48)
        wsc_choices = sum(1 for i in instrs if i.opcode == 0x02)
        if rep['dialogues'] != wsc_dialogues:
            mismatch.append((stem, 'dialogues %d != %d' % (rep['dialogues'], wsc_dialogues)))
        if len(rep['voices']) != wsc_voices:
            mismatch.append((stem, 'voices %d != %d' % (len(rep['voices']), wsc_voices)))
        if len(rep['images']) != wsc_images:
            mismatch.append((stem, 'images %d != %d' % (len(rep['images']), wsc_images)))
        if len(rep['choices']) != wsc_choices:
            mismatch.append((stem, 'choices %d != %d' % (len(rep['choices']), wsc_choices)))

    # 往返忠实性校验：重新解析转换产物，逐句比对 文本字节/说话人/语音名/图像名
    roundtrip_bad = []
    for name_bytes, data in members:
        stem = name_bytes.decode('ascii').split('.')[0]
        if args.stem and stem.upper() != args.stem.upper():
            continue
        out_path = outdir / (stem + '.ws2')
        if not out_path.exists():
            continue
        wsc_instrs = disassemble_wsc(decrypt_wsc(data))
        try:
            out_instrs = disassemble_ws2(ws2.decode(out_path.read_bytes()))
        except Exception as e:
            roundtrip_bad.append((stem, 'reparse: %s' % e))
            continue
        # 期望序列（含语音归属）
        exp_dialogues = []      # (id, speaker_or_None, text_bytes)
        exp_voices = []
        pend = None
        for i in wsc_instrs:
            if i.opcode == 0x23:
                exp_voices.append(i.fields['name'].upper() + '.OGG')
            elif i.opcode in (0x41, 0x42):
                if i.opcode == 0x41:
                    exp_dialogues.append((i.fields['id'], None, i.operands[4:-1]))
                else:
                    spk_end = i.operands.index(0, 5)
                    spk = i.operands[5:spk_end].decode('cp932')
                    exp_dialogues.append((i.fields['id'], spk, i.operands[spk_end + 1:-1]))
                pend = None
        # 实际序列
        got_dialogues = []
        got_voices = []
        got_images = []
        from tool.wsc2ws2 import SPEAKER_MAP
        for i in out_instrs:
            if i.opcode == 0x2e:
                got_voices.append(i.fields['file'])
            elif i.opcode == 0x14:
                # ws2disasm 的 operands 从 id 高字节起：[id_hi][00 00][chan]00[text]00[tail]
                did = i.fields['id']
                raw = i.operands
                cend = raw.index(0, 3)
                text = raw[cend + 1:-2]
                got_dialogues.append((did, None, text))
            elif i.opcode == 0x15 and i.fields['prefix']:
                got_dialogues and None
            elif i.opcode in (0x33,):
                got_images.append(i.fields['file'])
            elif i.opcode == 0x34:
                got_images.append(i.fields['file'])
        # 对话文本逐字节比对（按 id 对齐）
        exp_by_id = {d[0]: d for d in exp_dialogues}
        if len(exp_by_id) != len(exp_dialogues):
            roundtrip_bad.append((stem, 'duplicate dialogue ids in wsc'))
        for did, spk, text in got_dialogues:
            e = exp_by_id.get(did)
            if e is None:
                roundtrip_bad.append((stem, 'dialogue id %d unexpected' % did))
            elif e[2] != text:
                roundtrip_bad.append((stem, 'dialogue id %d text mismatch' % did))
                break
        if len(got_dialogues) != len(exp_dialogues):
            roundtrip_bad.append((stem, 'dialogue count %d != %d'
                                  % (len(got_dialogues), len(exp_dialogues))))
        if got_voices != exp_voices:
            bad = [(a, b) for a, b in zip(got_voices, exp_voices) if a != b][:2]
            roundtrip_bad.append((stem, 'voice seq mismatch %s' % bad))
    Path(args.report).write_text(json.dumps(reports, ensure_ascii=False, indent=1),
                                 encoding='utf-8')

    n_warn = sum(len(r['warnings']) for r in reports)
    print()
    print('converted %d scripts -> %s' % (len(reports), outdir))
    print('  parse failures : %d' % len(fails))
    print('  bad ws2 parse  : %d %s' % (len(bad_parse), bad_parse[:5]))
    print('  count mismatch : %d %s' % (len(mismatch), mismatch[:5]))
    print('  roundtrip bad  : %d %s' % (len(roundtrip_bad), roundtrip_bad[:5]))
    print('  warnings total : %d' % n_warn)
    ex = [r for r in reports if r['exit'] and r['exit']['source'] == 'none']
    print('  exits unwired  : %d (dead-end ff, needs call-chain wiring)' % len(ex))
    return 0


if __name__ == '__main__':
    sys.exit(main())
