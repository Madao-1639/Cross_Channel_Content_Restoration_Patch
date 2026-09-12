"""删除 Chip2.arc 中无引用的 MOS 冒充 PNG 成员（evcc0002.png / evcc0002b.png）。

背景
----
这两份成员是**原版 WillPlus 引擎的 MOS 图**被改扩展名塞进 Chip2.arc 的：
  `evcc0002.png`  65,578 B  = `CROSS_CHANNEL_Original/Chip.arc` 的 `EVCC0002.MOS`（逐字节相同）
  `evcc0002b.png` 64,269 B  = 同上 `EVCC0002B.MOS`
两者文件头都是 `WIPF`（WillPlus 图像格式），不是 PNG（PNG 头为 `89 50 4E 47`），
在 AdvHD 引擎里解不开；且**没有任何 ws2 引用它们**。属误加，白占 ~130 KB。

对应的原版 CG（`EVCC0002.PNG` / `EVCC0002B.PNG`）早已作为 `EVCC9XXX` 补入，
不需要这两个 MOS。

用法（项目根目录）：
    python script/remove_unreferenced_mos.py            # 检查并删除
    python script/remove_unreferenced_mos.py --check    # 只检查，不写入
"""
import hashlib
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tool import arcbuild, ws2  # noqa: E402

if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

CHIP2 = Path('asset/Chip2.arc')
RIO = Path('asset/Rio.arc')
BACKUP = Path('asset/Chip2.arc.before_mos_cleanup')
TARGETS = ['evcc0002.png', 'evcc0002b.png']
PNG_SIG = b'\x89PNG\r\n\x1a\n'


def main():
    check_only = '--check' in sys.argv
    members = {n.decode('utf-16-le'): v for n, v in arcbuild.read_raw(CHIP2)}
    present = [t for t in TARGETS if t in members]
    if not present:
        print('[跳过] 目标成员已不存在')
        return 0
    if len(present) != len(TARGETS):
        raise SystemExit('[中止] 目标成员不完整：%s' % present)

    rio = {n.decode('utf-16-le'): v for n, v in arcbuild.read_raw(RIO)}
    referenced = set()
    for name, data in rio.items():
        if name.endswith('.ws2'):
            for ref in ws2.extract_png_refs(ws2.decode(data)):
                referenced.add(ref.upper())

    ok = True
    for t in TARGETS:
        blob = members[t]
        print('  %-16s %8d B  sha=%s  引用数=%d  是PNG=%s'
              % (t, len(blob), hashlib.sha256(blob).hexdigest()[:12],
                 sum(1 for r in referenced if r == t.upper()), blob[:8] == PNG_SIG))
        if t.upper() in referenced:
            ok = False
            print('    [FAIL] 仍被脚本引用，不能删除')
        if blob[:8] == PNG_SIG:
            ok = False
            print('    [FAIL] 它确实是 PNG，不是误加的 MOS，需人工判断')
    if not ok:
        raise SystemExit('[中止] 检查未通过，未做任何写入')

    if check_only:
        print('[--check] 仅检查，未写入')
        return 0

    if BACKUP.exists():
        print('[备份] 已存在 %s（保留原样）' % BACKUP)
    else:
        arcbuild.write_arc(arcbuild.read_raw(CHIP2), BACKUP)
        print('[备份] %s -> %s' % (CHIP2, BACKUP))

    arcbuild.write_arc([(n, v) for n, v in arcbuild.read_raw(CHIP2)
                        if n.decode('utf-16-le') not in TARGETS], CHIP2)

    back = {n.decode('utf-16-le') for n, _ in arcbuild.read_raw(CHIP2)}
    leftover = [t for t in TARGETS if t in back]
    if leftover:
        raise SystemExit('[失败] 回读仍存在：%s' % leftover)
    count, size, _ = arcbuild.verify(CHIP2)
    print('[回读] %s %d 成员 %d 字节 verify OK' % (CHIP2, count, size))
    print('[完成] 已删除 %s' % '、'.join(TARGETS))
    return 0


if __name__ == '__main__':
    sys.exit(main())
