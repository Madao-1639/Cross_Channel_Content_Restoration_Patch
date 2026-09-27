# -*- coding: utf-8 -*-
"""生成 `resource/cg_map.json` —— 事件 CG 的**改名**映射表。

只记录**改了名**的那些。同名且内容一致的（7 个）默认用相同名字，不入表 ——
消费方查不到即保持原名。表里因此只有「原版名 → 本补丁的 EVCC9XXX 名」。

**识别方式：像素，不是 sha。** Res303 出货的图是把原版 LANCZOS 放大到 1280×960
并重编码过的，同一张画的 sha256 必然不同 —— 早期据此误判出 5 个「同名冲突」，
实测**全部是同名同内容**（降到 200×150 灰度后平均绝对差 0.14–0.22）。
放大方法的确定性已用对照验证：原版 `EVCC0017B.PNG` 按 LANCZOS 放大到 1280×960
与 Res303 的 `CN_EVCC0017B.PNG` **逐像素完全相同（最大差 0）**。

表内容：
  ```jsonc
  {
    "_说明": "...",
    "renamed": {
      "EVCC0017B.PNG": {
        "patch": "EVCC9006B.PNG",
        "refs": [["CCC3027", 227], ["CCC3027", 1180]]   // [宿主, 源对话序号]，列表（CG 会复用）
      }
    }
  }
  ```

**不记录指令号**：`refs` 里的位置取自**原版 WSC**（`0x46` 图像 / `0x48` 压进立绘槽），
而消费方处理的是**转换后的 ws2**（那里同一个引用是 `0x33`）。记原版的指令号会误导，
记 ws2 的又与「在源里定位」对不上 —— 所以只记「哪个宿主、哪条对话」，
两者都能唯一定位，且不依赖指令集版本。

还原范围（剧情顺序与切片区间）不在本表里，见 `resource/scene_slices.json`。

用法（项目根目录）：
    python script/build_cg_map.py            # 生成/更新 resource/cg_map.json
    python script/build_cg_map.py --check    # 只校验现有表与事实一致
"""
import argparse
import glob
import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image                                          # noqa: E402
from tool import arcbuild, wsc                                 # noqa: E402
from tool.wsc2ws2 import decrypt_wsc                           # noqa: E402

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

ORIG = ROOT.parent / 'CROSS_CHANNEL_Original'
RES = ROOT / 'resource'
TOL = 2.0                      # 200x150 灰度下的平均绝对差阈值
SIG = (200, 150)
CG_OPS = (0x46, 0x48)          # 原版 WSC 里引用图像 / 把图压进立绘槽的两个指令


def read_arc(path, old=False):
    rd = arcbuild.read_old_arc if old else arcbuild.read_raw
    out = {}
    for n, v in rd(path):
        if isinstance(n, str):
            k = n.upper()
        else:
            k = (n.decode('shift_jis', 'replace') if old else n.decode('utf-16-le')).upper()
        out[k] = v
    return out


def load_slices():
    p = RES / 'scene_slices.json'
    if not p.exists():
        raise SystemExit('缺少 %s' % p)
    d = json.loads(p.read_text(encoding='utf-8'))
    return [(s['src'], s['lo'], s['hi']) for s in d['scenes']], d['order']


def sig(b):
    return Image.open(io.BytesIO(b)).convert('L').resize(SIG, Image.LANCZOS).tobytes()


def dist(a, b):
    return sum(abs(x - y) for x, y in zip(a, b)) / (SIG[0] * SIG[1])


def build():
    scenes, order = load_slices()
    orig = {}
    for p in sorted(glob.glob(str(ORIG / 'Chip*.arc'))):
        try:
            for k, v in read_arc(p, old=True).items():
                if k.startswith(('EVCC', 'SGCC')) and k.endswith('.PNG'):
                    orig.setdefault(k, v)
        except Exception:
            continue
    patch = {k: v for k, v in read_arc(ROOT / 'asset' / 'Chip2.arc').items()
             if k.startswith('EVCC9')}
    osig = {k: sig(v) for k, v in orig.items()}
    p2o, worst = {}, 0.0
    for n, v in sorted(patch.items()):
        s = sig(v)
        best = min(osig.items(), key=lambda kv: dist(s, kv[1]))
        d = dist(s, best[1])
        if d > TOL:
            raise SystemExit('[中止] %s 找不到原版对应（最近 %s，差 %.2f）' % (n, best[0], d))
        p2o[n] = best[0]
        worst = max(worst, d)
    o2p = {o: p for p, o in p2o.items()}

    # 引用位置：剧情顺序 × 源 WSC 切片内。只记「哪个宿主、哪条对话」。
    rio = read_arc(ORIG / 'Rio.arc', old=True)
    refs = {}
    for stem, lo, hi in scenes:
        ins = wsc.disassemble(decrypt_wsc(rio[stem.upper() + '.WSC']))
        cid = -1
        for i in ins:
            if i.opcode in (0x41, 0x42) and i.fields.get('id') is not None:
                cid = i.fields['id']
            if i.opcode not in CG_OPS:
                continue
            nm = (i.fields.get('name') or '').upper()
            if not nm.startswith(('EVCC', 'SGCC')) or not (lo <= cid <= hi):
                continue
            f = nm + '.PNG'
            if [stem, cid] not in refs.setdefault(f, []):
                refs[f].append([stem, cid])

    renamed = {}
    for f in sorted(refs):
        tgt = o2p.get(f)
        if tgt is None:
            # 本补丁没给副本 —— 且必须确认 Steam 侧确有同名（否则引用解析不了）
            steam = read_arc(ROOT / 'backup' / 'Chip2.arc')
            if f not in steam and f != 'SGCC0020.PNG':
                raise SystemExit('[中止] %s 既无补丁副本、Steam 也无同名' % f)
            continue                      # 同名 → 不入表
        renamed[f] = {'patch': tgt, 'refs': refs[f]}
    unreferenced = sorted(set(p2o.values()) - set(refs))
    return renamed, order, p2o, worst, unreferenced, o2p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--check', action='store_true')
    args = ap.parse_args()
    renamed, order, p2o, worst, unref, o2p = build()
    print('像素识别：%d 个补丁 CG 全部找到原版对应，最大平均差 %.3f（阈值 %.1f）'
          % (len(p2o), worst, TOL))
    if unref:
        print('！有补丁副本但切片里没引用到：%s' % unref)
    print('改名映射 %d 条（其余引用同名，不入表）；完整映射 %d 条' % (len(renamed), len(o2p)))
    out = {'_说明': '事件 CG 的改名映射：原版名 -> 本补丁 EVCC9XXX 名。'
                    '同名且内容一致的不入表（默认用相同名字）。识别方式=像素。'
                    'refs = [宿主, 源对话序号]，列表。还原范围见 scene_slices.json。'
                    '`all` = **完整**映射（原版名 -> 本补丁名，含未改名/未引用者），'
                    '供 `script/build_restored_cgs.py` 从原版重建补丁 CG。',
           'renamed': renamed,
           'all': o2p}
    RES.mkdir(exist_ok=True)
    p = RES / 'cg_map.json'
    if args.check:
        if not p.exists():
            raise SystemExit('[中止] %s 不存在' % p)
        old = json.loads(p.read_text(encoding='utf-8'))
        if old.get('renamed') != renamed or old.get('all') != o2p:
            raise SystemExit('[--check] 与现有表不一致（renamed 或 all）')
        print('[--check] 与现有表一致')
        return 0
    p.write_text(json.dumps(out, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    print('已写出 %s' % p)


if __name__ == '__main__':
    sys.exit(main())
