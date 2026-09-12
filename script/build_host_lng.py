"""重建被就地插入改写的宿主脚本的配套 lng。

lng 是**位置对应**的：第 N 条 lng ↔ 脚本里第 N 个 `14`。插入段的对话插进宿主中间，
`keep` 之后的全部位置都变了，所以整个 lng 必须按新序列重建：

    新 lng = [前缀: 沿用宿主原 lng 的前 keep 条]
           + [插入段: 源 CCS 的中文 + 与该句源文本相同的尾部控制符]

约定（由 `CNR0005_en.lng` 与 Res303 产物逐字节比对实证）：
  * 取 `tool/lng.parse_ccs`，`CCS 行号 = 源 WSC 对话 id + 1`
  * 用 `tool/lng.strip_speaker_wrap` 去掉 `[说话人]"文本"` 的外层包裹
  * 尾部控制符照抄源文本的（`%K`、`%K%P`、`%N`、`%P`），不是一律 `%K%P`
  * 编码走 `tool/lng.encode_lng`（UTF-16LE 后整体 XOR 0x2C）

`CCD5001B` 是截头而非插入，它的新 lng = 原 lng 的第 `from_dialogue` 条起。

用法（项目根目录）：
    python script/build_host_lng.py --check
    python script/build_host_lng.py --write
"""
import argparse
import io
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tool import arcbuild, lng, ws2, ws2disasm, wsc  # noqa: E402

sys.path.insert(0, str(ROOT / 'script'))
# 先导入：splice_restoration 在导入时会自己包一层 sys.stdout，先包后包会互相关掉底层 buffer
from splice_restoration import SCENES, TAIL_HOSTS  # noqa: E402

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

CCS_DIR = ROOT / '..' / 'cross-channel_chinese-localization_project' / 'Scripts' / '20150412'
WSC_DIR = ROOT / 'tmp' / 'corpus' / 'wsc'
RIO = ROOT / 'asset' / 'Rio.arc'
BACKUP = ROOT / 'asset' / 'Rio.arc.before_host_lng'

CTRL_TAIL = re.compile(r'(?:%[A-Za-z])+$')
CTRL_ONLY = re.compile(r'^(?:%[A-Za-z])+$')


def split_ctrl(text):
    """把源文本拆成 (正文, 尾部控制符)。`%N` 这种整句控制符返回 ('', '%N')。"""
    t = text.strip()
    if CTRL_ONLY.match(t):
        return '', t
    m = CTRL_TAIL.search(t)
    return (t[:m.start()], m.group(0)) if m else (t, '')


def source_dialogues(stem):
    raw = (WSC_DIR / (stem + '.WSC')).read_bytes()
    return [i for i in wsc.disassemble(raw) if i.opcode in (0x41, 0x42)]


def build():
    members = {n.decode('utf-16-le').upper(): v for n, v in arcbuild.read_raw(RIO)}
    out, rows = {}, []

    def n_dialogues(name):
        return sum(1 for i in ws2disasm.disassemble(ws2.decode(members[name]))
                   if i.opcode == 0x14)

    for sc in SCENES:
        host, stem = sc['host'], sc['src']
        keep = sc['keep']
        lo_eff = sc['lo'] + sc['head_overlap']
        old = lng.parse_lng(members[host.upper()[:-4] + '.LNG'])
        # 幂等守卫：lng 条数必须等于该脚本的 `14` 条数。已经相等 = 已是固定点，跳过。
        # 没有这道守卫，「截头」这类操作二次运行会再截一次（CCD5001B 352 → 132）。
        if len(old) == n_dialogues(host.upper()):
            rows.append((host, len(old), len(old), keep, '已是固定点'))
            continue
        if len(old) < keep:
            raise SystemExit('%s 原 lng %d 条 < keep %d' % (host, len(old), keep))
        new = list(old[:keep])

        wdl = source_dialogues(stem)
        ccs = lng.parse_ccs(CCS_DIR / (stem + '.CCS'))
        for k in range(lo_eff, sc['hi'] + 1):
            src = wdl[k]
            ctrl = split_ctrl(src.fields.get('text', ''))[1]
            entry = ccs.get(src.fields['id'] + 1)
            body = lng.strip_speaker_wrap(entry) if entry else src.fields.get('text', '')
            body = split_ctrl(body)[0]
            new.append(body + ctrl)

        data = lng.encode_lng(new)
        if lng.parse_lng(data) != new:
            raise SystemExit('%s 新 lng 回读不一致' % host)
        out[host.upper()[:-4] + '.LNG'] = data
        rows.append((host, len(old), len(new), keep, len(new) - keep))

    for th in TAIL_HOSTS:
        host, k = th['host'], th['from_dialogue']
        old = lng.parse_lng(members[host.upper()[:-4] + '.LNG'])
        if len(old) == n_dialogues(host.upper()):
            rows.append((host, len(old), len(old), '-', '已是固定点'))
            continue
        new = list(old[k:])
        if len(new) != n_dialogues(host.upper()):
            raise SystemExit('%s 截头后 lng %d 条 != 脚本对话 %d 条'
                             % (host, len(new), n_dialogues(host.upper())))
        data = lng.encode_lng(new)
        if lng.parse_lng(data) != new:
            raise SystemExit('%s 新 lng 回读不一致' % host)
        out[host.upper()[:-4] + '.LNG'] = data
        rows.append((host, len(old), len(new), '-', len(new) - k))

    return members, out, rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--check', action='store_true')
    ap.add_argument('--write', action='store_true')
    args = ap.parse_args()
    members, out, rows = build()
    print('%-20s %-9s %-9s %-7s %s' % ('宿主', '原 lng', '新 lng', 'keep', '来源'))
    for host, a, b, keep, n in rows:
        if n == '已是固定点':
            print('%-20s %-9d %-9d %-7s 已是固定点' % (host, a, b, keep))
        elif keep == '-':
            print('%-20s %-9d %-9d %-7s 截头保留 %d' % (host, a, b, keep, n))
        else:
            print('%-20s %-9d %-9d %-7s 前缀沿用 %s + 插入段 %d'
                  % (host, a, b, keep, keep, n))
    if not args.write:
        print('\n（--check 模式，未写入）')
        return 0

    if BACKUP.exists():
        print('\n[备份] 已存在 %s' % BACKUP)
    else:
        arcbuild.write_arc(arcbuild.read_raw(RIO), BACKUP)
        print('\n[备份] asset/Rio.arc -> %s' % BACKUP)
    members.update(out)
    order = [n for n, _ in arcbuild.read_raw(RIO)]
    merged = [(nb, out.get(nb.decode('utf-16-le').upper(), members[nb.decode('utf-16-le').upper()]))
              for nb in order]
    arcbuild.write_arc(merged, RIO)
    back = {n.decode('utf-16-le').upper(): v for n, v in arcbuild.read_raw(RIO)}
    for k, v in out.items():
        if back[k] != v:
            raise SystemExit('[失败] 回读不一致：%s' % k)
    count, size, _ = arcbuild.verify(RIO)
    print('[写入] %d 个 lng；回读校验通过（%d 成员，%d 字节）' % (len(out), count, size))
    return 0


if __name__ == '__main__':
    sys.exit(main())
