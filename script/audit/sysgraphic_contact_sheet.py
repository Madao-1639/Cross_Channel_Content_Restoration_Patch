"""UI 汉化素材的接触表：把 `resource/SysGraphic/<pna>/L*.png` 排成带图层号的大图，供人工扫一遍。

为什么需要它：素材是逐张 PNG，**内容对不对机器判不了**（缺字、裁切、错字都只在像素里）。
已出现过两类只能靠眼睛抓的缺陷：① 两张说明文字图装反（`sys_config_P2#002`/`#006`）；
② 标签右侧被裁掉半个字（`sys_config_P2#080` 等 4 张只剩「全」）。本脚本把它们排成一张表，
逐张标出 `L<图层下标>`，评审扫一眼即可；只读，不改任何文件。

需要装有 Pillow 的环境（本项目用 `EDA`）。用法（项目根目录）：
    python script/audit/sysgraphic_contact_sheet.py                      # 全部子目录 → tmp/ui_sheets/
    python script/audit/sysgraphic_contact_sheet.py sys_config_P2        # 只出一张
    python script/audit/sysgraphic_contact_sheet.py sys_config_P2 --scale 1
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

RES = ROOT / 'resource' / 'SysGraphic'
OUT = ROOT / 'tmp' / 'ui_sheets'
COLS = 3
PAD = 8
LABEL_H = 14
LABEL_FONT = 'C:/Windows/Fonts/consola.ttf'


def build(sub, scale):
    files = sorted(sub.glob('L*.png'), key=lambda p: int(p.stem[1:]))
    if not files:
        return None
    font = ImageFont.truetype(LABEL_FONT, 11)
    tiles = []
    for f in files:
        im = Image.open(f).convert('RGBA')
        bg = Image.new('RGBA', im.size, (245, 245, 248, 255))
        bg.alpha_composite(im)
        pic = bg.convert('RGB')
        if scale != 1:
            pic = pic.resize((im.width * scale, im.height * scale), Image.LANCZOS)
        tiles.append((f.stem, pic))
    col_w = max(t[1].width for t in tiles) + PAD
    row_h = max(t[1].height for t in tiles) + PAD + LABEL_H
    rows = (len(tiles) + COLS - 1) // COLS
    sheet = Image.new('RGB', (col_w * COLS, row_h * rows), (255, 255, 255))
    dr = ImageDraw.Draw(sheet)
    for i, (name, pic) in enumerate(tiles):
        cx, cy = (i % COLS) * col_w, (i // COLS) * row_h
        dr.text((cx + 2, cy + 1), name, fill=(180, 0, 0), font=font)
        sheet.paste(pic, (cx + 2, cy + LABEL_H))
    return sheet, len(tiles)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('names', nargs='*', help='子目录名（缺省 = 全部）')
    ap.add_argument('--scale', type=int, default=2, help='放大倍数（默认 2）')
    args = ap.parse_args(argv)
    OUT.mkdir(parents=True, exist_ok=True)
    subs = [RES / n for n in args.names] if args.names else sorted(p for p in RES.iterdir() if p.is_dir())
    for sub in subs:
        if not sub.is_dir():
            raise SystemExit('[ERR] 不存在的素材子目录：%s' % sub)
        built = build(sub, args.scale)
        if built is None:
            print('[跳过] %s：没有替换图' % sub.name)
            continue
        sheet, n = built
        out = OUT / ('sheet_%s.png' % sub.name)
        sheet.save(out)
        print('[接触表] %-16s %3d 张 -> %s (%dx%d)'
              % (sub.name, n, out.relative_to(ROOT), sheet.width, sheet.height))
    return 0


if __name__ == '__main__':
    sys.exit(main())
