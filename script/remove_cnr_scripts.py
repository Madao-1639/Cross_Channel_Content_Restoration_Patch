"""清理 CNR### 还原脚本（接线改为「就地插入」后已无引用）。

背景：2026-09-11 定案把原版内容**就地插入** 12 个宿主脚本，不再引入独立还原脚本。
11 个 `CNR0001`–`CNR00011` 的 ws2 + lng 全部失去引用（`CNR0105` 更早在上一轮移除）。

本脚本只做移除，不改动别的成员；幂等（已无 CNR 成员则跳过）。

用法（项目根目录）：
    python script/remove_cnr_scripts.py --check
    python script/remove_cnr_scripts.py --write
"""
import argparse
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tool import arcbuild, ws2, ws2disasm  # noqa: E402

if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

RIO = ROOT / 'asset' / 'Rio.arc'
BACKUP = ROOT / 'asset' / 'Rio.arc.before_cnr_cleanup'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--check', action='store_true')
    ap.add_argument('--write', action='store_true')
    args = ap.parse_args()

    members = list(arcbuild.read_raw(RIO))
    cnr = [(n, d) for n, d in members if n.decode('utf-16-le').upper().startswith('CNR')]
    if not cnr:
        print('已无 CNR 成员，无需清理')
        return 0
    print('待移除 %d 个成员：%s' % (len(cnr), [n.decode('utf-16-le') for n, _ in cnr]))

    # 移除前必须确认无引用 —— 否则会留下悬空跳转
    refs = {}
    for n, d in members:
        nm = n.decode('utf-16-le')
        if not nm.upper().endswith('.WS2'):
            continue
        try:
            ins = ws2disasm.disassemble(ws2.decode(d))
        except Exception:
            continue
        for i in ins:
            tgt = None
            if i.opcode == 0x07:
                tgt = i.fields['name']
            elif i.opcode == 0x0f:
                for e in i.fields.get('entries', []):
                    t = e.get('name')
                    if t and t.upper().startswith('CNR'):
                        refs.setdefault(t.upper(), []).append(nm)
                continue
            if tgt and tgt.upper().startswith('CNR'):
                refs.setdefault(tgt.upper(), []).append(nm)
    if refs:
        raise SystemExit('[中止] 仍有引用，不能移除：%s' % refs)
    print('[核对] 无任何脚本引用 CNR，可安全移除')

    if not args.write:
        print('\n（--check 模式，未写入）')
        return 0

    if BACKUP.exists():
        print('[备份] 已存在 %s' % BACKUP)
    else:
        arcbuild.write_arc(members, BACKUP)
        print('[备份] asset/Rio.arc -> %s' % BACKUP)

    keep = [(n, d) for n, d in members if not n.decode('utf-16-le').upper().startswith('CNR')]
    arcbuild.write_arc(keep, RIO)
    back = [n.decode('utf-16-le') for n, _ in arcbuild.read_raw(RIO)]
    if any(x.upper().startswith('CNR') for x in back):
        raise SystemExit('[失败] 回读仍有 CNR 成员')
    count, size, _ = arcbuild.verify(RIO)
    print('[写入] %d -> %d 成员；回读校验通过（%d 成员，%d 字节，无 padding）'
          % (len(members), len(keep), count, size))
    return 0


if __name__ == '__main__':
    sys.exit(main())
