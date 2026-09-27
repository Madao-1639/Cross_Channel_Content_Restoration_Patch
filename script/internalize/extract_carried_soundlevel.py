# -*- coding: utf-8 -*-
"""把「随补入的原版语音一起发布、但只在 Res303 里有」的 `.soundlevel` 音量包络内化到仓库。

为什么：基线改从 `backup/`（Steam 原版）起底，而 backup **只带自己那 15,144 条语音的包络**
（实测 `backup/Voice.arc` 15,144 个 OGG + 15,144 条 `.soundlevel`，两侧 stem 逐一相等 ——
即 Steam 对**每条**语音都配了包络）。补入的原版录音**没有**包络，其中 **967 条**在 Res303 的
Voice.arc 里存在 ⇒ `asset/Voice.arc` 有 16,111 条 = 15,144 + 967。若起底时不显式带过，
这 967 条会**静默丢失**（`import_missing_voices.py` 只补 OGG、从不补包络），而验收此前不比对侧车。
余下 585 条补入语音没有包络 —— 本来就没有，**不凭空造**。

`.soundlevel` = **按秒**的 ASCII 音量包络（30 样本/秒，与采样率无关）。

产出：`resource/carried_soundlevel.json` —— `{stem: "0.00,0.00,..."}`（成员名 = `<stem>.soundlevel`）。
用法（项目根目录）：
    python script/internalize/extract_carried_soundlevel.py            # 预演
    python script/internalize/extract_carried_soundlevel.py --write    # 落盘
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tool import arcbuild  # noqa: E402

if hasattr(sys.stdout, 'buffer'):
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

RES303_VOICE = ROOT.parent / 'CROSS_CHANNEL_Steam_CN_Restored_v3.0.3' / 'Voice.arc'
ASSET_VOICE = ROOT / 'asset' / 'Voice.arc'
OUT = ROOT / 'resource' / 'carried_soundlevel.json'
BACKUP_VOICE = ROOT / 'backup' / 'Voice.arc'


def members(path):
    return {n.decode('utf-16-le'): d for n, d in arcbuild.read_raw(path)}


def wanted():
    """Res303 有、而 **backup 没有**的 `.soundlevel`（= 只能由 Res303 提供的那些）。"""
    r = members(RES303_VOICE)
    b = {k.upper() for k in members(BACKUP_VOICE)}
    out = {}
    for k, v in r.items():
        if not k.lower().endswith('.soundlevel'):
            continue
        if k.upper() in b:
            continue
        out[k[:-len('.soundlevel')]] = v.decode('ascii')
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--write', action='store_true')
    args = ap.parse_args()

    w = wanted()
    print('Res303 有、backup 没有的 .soundlevel：%d 条' % len(w))
    # 与当前 asset 交叉核对（应当一致）
    a = {k[:-len('.soundlevel')]: v for k, v in members(ASSET_VOICE).items()
         if k.lower().endswith('.soundlevel')}
    miss = [k for k in w if k not in a]
    diff = [k for k in w if k in a and a[k].decode('ascii') != w[k]]
    print('  与当前 asset/Voice.arc 交叉核对：缺 %d 条、内容不同 %d 条' % (len(miss), len(diff)))
    if miss or diff:
        print('  ⚠ 不一致（asset 缺 %s；不同 %s）' % (miss[:3], diff[:3]))
    if not args.write:
        print('（未指定 --write，未写入）')
        return 0
    OUT.write_text(json.dumps(
        {'_说明': '随补入的原版语音一起发布的 .soundlevel 音量包络（backup 里没有，只 Res303 有）。'
                  '基线从 backup 起底时由 build_patch 显式带回 asset/Voice.arc（成员名 = key + ".soundlevel"）。',
         **w}, ensure_ascii=False, indent=0) + '\n', encoding='utf-8')
    print('写出 %s（%d 条）' % (OUT.relative_to(ROOT), len(w)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
