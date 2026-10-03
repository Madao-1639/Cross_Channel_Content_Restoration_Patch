"""把就地插入扩出来的覆盖范围所需、而补丁里还没有的语音从原版 Voice.arc 补入。

背景：Res303 的还原脚本只覆盖了场景的一部分，就地插入把每段补到「交接点」之后，
新覆盖的那几百句所引用的语音 `asset/Voice.arc` 里没有（共 186 条，如
`KRI061C3027.OGG`、`MKI179D4001.OGG`），但**原版 Voice.arc 里都有**。

要补哪些：直接调用 `tool/rename_map` 的 `build()`，取它的「找不到出处」清单
（它已经把所有插入区间的引用与 asset+backup 全量核对过）。

关于 `.soundlevel`：原版 Voice.arc 只有 `.OGG`，Steam 侧才带 `.soundlevel`（ASCII 的
逐段音量包络）。本项目已有先例：`YOU035A5000`..`YOU042A5000` 这 8 条就是无 soundlevel
补入的，而 CNR0005 实机验证通过、它们正常播放 —— 所以**只补 OGG 即可**，不凭空造包络
（`CLAUDE.md`「缺少的信息不应猜测补全」）。`.soundlevel` 是**按秒**的包络（30 样本/秒），
与采样率无关，故重采样（见下）不影响它。

关于**采样率**：Steam 语料（`backup/Voice.arc` 的 **15,144** 条 OGG）**全部是 48000 Hz**。
本项目导入的原版录音里有一批是 **44100** —— 已知崩溃（`0xc0000005` / `0xc0000374`，崩点在工作
线程的音频解码路径）所在的那段 H 场景播的正是 44100 那批。故本步**每次运行都全档扫描**：
asset/Voice.arc 里**任何 != 48000 的 OGG 一律重采样到 48000**（对齐语料；**名称形态不限** ——
原版命名有带尾字母的 12 字符形式，按正则筛会漏）。
（此项为**技术性兼容处理**，非内容改动；重采样用 ffmpeg，路径见 `FFMPEG`。）

用法（项目根目录）：
    python script/import_missing_voices.py --check
    python script/import_missing_voices.py --write
"""
import argparse
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tool import arcbuild, rename_map as RN, ws2, ws2disasm  # noqa: E402

if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

ORIG_VOICE = ROOT / '..' / 'CROSS_CHANNEL_Original' / 'Voice.arc'
ASSET_VOICE = ROOT / 'asset' / 'Voice.arc'
BACKUP = ROOT / 'asset' / 'Voice.arc.before_import'
ASSET_RIO = ROOT / 'asset' / 'Rio.arc'
BACKUP_RIO = ROOT / 'backup' / 'Rio.arc'
BACKUP_VOICE = ROOT / 'backup' / 'Voice.arc'
UNRESOLVED_RE = re.compile(r'^(.+?)（缺 (.+?)）$')

# 目标采样率（对齐 Steam 语料）与 ffmpeg 位置（用环境变量 `CC_FFMPEG`，或 PATH 上的 `ffmpeg`）。
TARGET_RATE = 48000
FFMPEG = os.environ.get('CC_FFMPEG') or shutil.which('ffmpeg') or 'ffmpeg'


def ogg_rate(d):
    """OGG 的采样率（Vorbis 标识头 `\\x01vorbis` 之后：版本(4) 声道(1) 采样率(4 LE)）。"""
    p = d.find(b'\x01vorbis')
    if p < 0 or p + 16 > len(d):
        return None
    return int.from_bytes(d[p + 12:p + 16], 'little')


def resample_ogg(data, target=TARGET_RATE):
    """把一条 OGG 重采样到 `target` Hz（libvorbis）。ffmpeg 缺失即中止，**绝不静默导入原采样率**。"""
    if not (os.path.exists(FFMPEG) or shutil.which(FFMPEG)):
        raise SystemExit('需要重采样，但找不到 ffmpeg（%s）—— 设 `CC_FFMPEG` 或装上 ffmpeg。'
                         % FFMPEG)
    tmp = Path(tempfile.mkdtemp(prefix='resample_'))
    fi, fo = tmp / 'i.ogg', tmp / 'o.ogg'
    fi.write_bytes(data)
    r = subprocess.run([FFMPEG, '-y', '-loglevel', 'error', '-i', str(fi),
                        # `+bitexact`：Ogg muxer 的 **stream serial 默认随机** ⇒ 同一份音频重编码
                        # 每次字节都不同（音频一致，只是容器号）。加此标志固定 serial ⇒ 可复现。
                        '-fflags', '+bitexact', '-flags', '+bitexact',
                        '-ar', str(target), '-c:a', 'libvorbis', '-q:a', '6',
                        '-map_metadata', '-1', str(fo)], capture_output=True)
    if r.returncode != 0 or not fo.exists():
        raise SystemExit('ffmpeg 重采样失败：%s' % r.stderr.decode('utf-8', 'replace')[:200])
    nd = fo.read_bytes()
    shutil.rmtree(tmp, ignore_errors=True)
    if ogg_rate(nd) != target:
        raise SystemExit('重采样后仍不是 %d Hz' % target)
    return nd



def wanted():
    """待补语音清单 ＝ ① 重命名表核对的「找不到出处」清单（**被还原区间**里的原版录音）
                        ＋ ② `resource/voice_plan.json` 点名的原版录音（**换挂**用）。

    ⚠️ ② 是 2026-09-25 补的：原先只认 ①，于是"**保留槽被重新绑到原版行**、要把 Steam 的净化版
    短音换回原版录音"这类需求（如 `CCB2019` k123／k137）**永远补不进来** —— 而 `apply_text_map`
    会照 `voice_plan` 挂上那条 `2e`，产物里就多一条指向**不存在文件**的悬空引用。
    （现有 56 条 `voice_plan` 条目已全部同时存在于 `asset/Voice.arc` 与原版归档，故纳入 ② 不会
    引入"原版也没有"的中止。）
    """
    _rename, unresolved, _unchanged, _cg, _conf = RN.build()
    missing = []
    for u in unresolved:
        m = UNRESOLVED_RE.match(u)
        if m and m.group(2).endswith('.OGG'):
            missing.append(m.group(2))
    vp_path = ROOT / 'resource' / 'voice_plan.json'
    if vp_path.exists():
        for stem, byk in json.loads(vp_path.read_text(encoding='utf-8')).items():
            if stem.startswith('_'):
                continue
            for v in byk.values():
                f = (v or {}).get('f')
                if f:
                    missing.append(f if f.upper().endswith('.OGG') else f + '.OGG')

    # ③ **闭环保底**：产物脚本（`asset/Rio.arc`）里**实际挂着的 `2e`** 都必须能在档 ——
    #    前两个来源只覆盖「重建引用」（改名表 / voice_plan），**骨架自带的引用不在其中**
    #    （如 `CCD4003A` 沿用了 Steam 的 `2e`；旧流程靠 Res303 的 Voice 提供，backup 起底后就缺）。
    #    ⚠️ 排除「Steam 原版本就缺」的死引用（backup 的脚本引用了、`backup/Voice.arc` 却没有）——
    #       那是原生既存问题，不该触发"原版缺资源即中止"。
    def referenced(rio_path):
        out = set()
        if not rio_path.exists():
            return out
        for nb, d in arcbuild.read_raw(rio_path):
            if not nb.decode('utf-16-le').upper().endswith('.WS2'):
                continue
            for x in ws2disasm.disassemble(ws2.decode(d)):
                if x.opcode == 0x2e and x.fields.get('file'):
                    out.add(x.fields['file'].upper())
        return out

    dead = referenced(BACKUP_RIO) - {n.decode('utf-16-le').upper()
                                     for n, _ in arcbuild.read_raw(BACKUP_VOICE)}
    missing += [n for n in sorted(referenced(ASSET_RIO) - dead)]
    return sorted(set(missing))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--check', action='store_true')
    ap.add_argument('--write', action='store_true')
    args = ap.parse_args()

    need = wanted()
    asset = list(arcbuild.read_raw(ASSET_VOICE))
    have = {n.decode('utf-16-le').upper() for n, _ in asset}
    print('待补语音 %d 条；asset/Voice.arc 现有 %d 个成员（%.1f MB）'
          % (len(need), len(asset), sum(len(d) for _, d in asset) / 1e6))

    got, absent = [], []
    if need:
        print('读取原版 %s ...' % ORIG_VOICE)
        orig = {n.decode('shift_jis', 'replace').upper(): d
                for n, d in arcbuild.read_old_arc(ORIG_VOICE)}
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

    # ① 新补入的 → 采样率对齐
    rates = {n: ogg_rate(d) for n, d in got}
    n_new = sum(1 for v in rates.values() if v is not None and v != TARGET_RATE)
    if n_new:
        print('  新补入里 %d 条 != %d Hz，重采样（例：%s）'
              % (n_new, TARGET_RATE, [n for n, v in rates.items() if v != TARGET_RATE][:3]))
        got = [(n, resample_ogg(d) if (rates[n] is not None and rates[n] != TARGET_RATE) else d)
               for n, d in got]

    # ② **全档扫描**：任何 != TARGET_RATE 的 OGG 一律重采样 —— **不只新补入的**。
    #    此前只管新增 ⇒ 已在档的旧数据永不复查，于是 21 条 44100 长期残留在产物里。
    #    名称形态不限（原版命名有带尾字母的 12 字符形式，按正则筛会漏）。
    fixed = []
    for i, (nb, d) in enumerate(asset):
        nm = nb.decode('utf-16-le')
        if nm.upper().endswith('.OGG') and ogg_rate(d) not in (None, TARGET_RATE):
            fixed.append(nm)
            asset[i] = (nb, resample_ogg(d))
    print('  全档扫描：残留 != %d Hz 的 OGG %d 条%s'
          % (TARGET_RATE, len(fixed), ('（例：%s）' % fixed[:3]) if fixed else ''))
    print('  补入后总大小 +%.1f MB' % (sum(len(d) for _, d in got) / 1e6))

    # 幂等：无新补入、且全档无残留 ⇒ **不重写 Voice.arc**（`CLAUDE.md` 幂等性要求）。
    if not got and not fixed:
        print('  无需补入、无采样率残留 ⇒ 不改动 Voice.arc')
        return 0

    if not args.write:
        print('\n（未指定 --write，未写入）')
        return 0

    if not (BACKUP.exists() and arcbuild.same_file(BACKUP, ASSET_VOICE)):
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
