"""Generate the incremental payload for CROSS†CHANNEL from asset/ vs backup/.

Fixed path convention:
  - asset/    = fully patched game files (dev/test baseline)
  - backup/   = Steam original baseline (used to compute the delta)
  - payload/  = output incremental payload consumed by tool/install.py

For each archive, **every asset member** is classified against the backup
version (added / modified / keep), and the resulting list is written to
METADATA.json **in the asset file's own member order**; backup-only
members are appended as `deleted` (the installer skips those, so their
position does not matter). The installer reconstructs the archive as
(player's original file) + (payload patch archive) + (metadata).

Ordering by the **asset** file — rather than by the Steam original with
additions appended — is what makes the replay byte-exact: `merge_arc`
re-orders members according to this list. `Voice.arc` is the reason it
matters: its Steam original is already sorted by name, and
`import_missing_voices.py` inserts new voices at their *sorted* position,
so the asset order is NOT "original order + appended additions". Ordering
METADATA by the original made the installer emit a different member order
than the asset and fail its own checksum verification.

Idempotent: safe to re-run; payload/ is cleared and rebuilt each time.
"""
import hashlib
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tool import arcbuild  # noqa: E402

ASSET_DIR = Path('asset')
BACKUP_DIR = Path('backup')
PAYLOAD_DIR = Path('payload')
VERIFY_TMP_DIR = Path('tmp') / 'verify_merge'

# Only archives that actually differ from the Steam baseline are shipped.
# Chip1.arc / SysVoice.arc are untouched and intentionally excluded.
ARCHIVES = [
    'Rio.arc',
    'Graphic.arc',
    'Chip2.arc',
    'Voice.arc',
    'Fonts.arc',
    'Script.arc',
    'SysGraphic.arc',
]


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def patch_name_for(asset_name):
    return asset_name[:-4] + '_patch.arc'


def build_archive_delta(asset_path, backup_path):
    """Classify every member and build the added/modified delta.

    Returns (delta_members, members_with_type, stats).
    members_with_type follows the **asset file's own member order**
    (backup-only members appended as `deleted`), so that the installer's
    replay reproduces the asset byte-for-byte.
    """
    asset_members = arcbuild.read_raw(asset_path)
    asset_map = {n.decode('utf-16le'): (n, d) for n, d in asset_members}
    asset_order = [n.decode('utf-16le') for n, _ in asset_members]

    if backup_path.exists():
        backup_members = arcbuild.read_raw(backup_path)
    else:
        backup_members = []
    backup_map = {n.decode('utf-16le'): d for n, d in backup_members}
    backup_order = [n.decode('utf-16le') for n, _ in backup_members]

    members_with_type = []
    seen = set()
    for name in asset_order:                 # ← 按 asset 的实际顺序
        seen.add(name)
        if name not in backup_map:
            members_with_type.append({'name': name, 'type': 'added'})
        elif sha256(asset_map[name][1]) != sha256(backup_map[name]):
            members_with_type.append({'name': name, 'type': 'modified'})
        else:
            members_with_type.append({'name': name, 'type': 'keep'})
    for name in backup_order:                # 仅原档有的 → deleted（merge 时跳过，位置无关）
        if name not in seen:
            members_with_type.append({'name': name, 'type': 'deleted'})

    delta = [asset_map[m['name']] for m in members_with_type
             if m['type'] in ('added', 'modified')]

    stats = {t: sum(1 for m in members_with_type if m['type'] == t)
             for t in ('keep', 'added', 'modified', 'deleted')}

    return delta, members_with_type, stats


def generate():
    print('=' * 84)
    print('通用 Payload 生成器')
    print('=' * 84)
    print(f'Asset 目录:   {ASSET_DIR}')
    print(f'Backup 目录:  {BACKUP_DIR}')
    print(f'Payload 目录: {PAYLOAD_DIR}')
    print()

    if not ASSET_DIR.exists():
        print(f'错误: Asset 目录不存在: {ASSET_DIR}')
        return None

    if PAYLOAD_DIR.exists():
        shutil.rmtree(PAYLOAD_DIR)
    PAYLOAD_DIR.mkdir(parents=True, exist_ok=True)

    metadata = {}
    total_size = 0

    print('生成 payload:')
    print()

    for asset_name in ARCHIVES:
        asset_path = ASSET_DIR / asset_name
        backup_path = BACKUP_DIR / asset_name
        output_name = patch_name_for(asset_name)
        output_path = PAYLOAD_DIR / output_name

        if not asset_path.exists():
            print(f'  [SKIP] {asset_name}: 不存在于 asset/')
            continue

        if not backup_path.exists():
            print(f'  [INFO] {asset_name}: backup/ 中不存在，视为全新归档')

        delta, members_with_type, stats = build_archive_delta(asset_path, backup_path)

        if not delta:
            print(f'  [SKIP] {output_name}: 无新增/修改成员')
            continue

        arcbuild.write_arc(delta, output_path)
        size = output_path.stat().st_size
        total_size += size

        print(f'  [OK] {output_name}: {size:,} bytes')
        print(f'      keep={stats["keep"]} 新增={stats["added"]} '
              f'修改={stats["modified"]} 删除={stats["deleted"]}')

        metadata[asset_name] = {
            'checksum': sha256(asset_path.read_bytes()),
            'members': members_with_type,
        }

    metadata_file = PAYLOAD_DIR / 'METADATA.json'
    metadata_file.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')

    print()
    print('=' * 84)
    print(f'Payload 总大小: {total_size:,} bytes ({total_size / 1024 / 1024:.1f} MB)')
    print(f'元数据已保存至: {metadata_file}')

    return metadata


def verify(metadata):
    """回读校验：用 backup + payload + metadata 重放安装流程，核对结果与 asset 字节一致。

    这是唯一能抓住「元数据成员顺序与 asset 不一致」的检查 —— 顺序错了，
    重放出来的归档成员内容都对、但字节序不同，安装器的 checksum 校验必然失败。
    """
    print()
    print('=' * 84)
    print('回读校验（模拟安装流程）')
    print('=' * 84)

    # tool/install.py 是 PyInstaller 的入口，里面写的是裸 `import arcbuild`
    # （打包后 arcbuild.py 与 exe 同目录）。在项目内 import 它，需要把 tool/
    # 一并放进 sys.path，否则 ModuleNotFoundError。
    tool_dir = str(Path(__file__).parent.parent / 'tool')
    if tool_dir not in sys.path:
        sys.path.insert(0, tool_dir)
    from tool.install import merge_arc  # noqa: E402

    if VERIFY_TMP_DIR.exists():
        shutil.rmtree(VERIFY_TMP_DIR)
    VERIFY_TMP_DIR.mkdir(parents=True, exist_ok=True)

    metadata_path = PAYLOAD_DIR / 'METADATA.json'
    ok = True

    try:
        for asset_name, info in metadata.items():
            backup_path = BACKUP_DIR / asset_name
            patch_path = PAYLOAD_DIR / patch_name_for(asset_name)
            out_path = VERIFY_TMP_DIR / asset_name

            merge_arc(backup_path, patch_path, out_path, metadata_path, asset_name)

            actual = sha256(out_path.read_bytes())
            expected = info['checksum']
            if actual == expected:
                print(f'  [OK] {asset_name}: 重放结果与 asset/ 一致')
            else:
                ok = False
                print(f'  [FAIL] {asset_name}: 重放结果不一致')
                print(f'      期望: {expected}')
                print(f'      实际: {actual}')
            out_path.unlink(missing_ok=True)
    finally:
        shutil.rmtree(VERIFY_TMP_DIR, ignore_errors=True)

    if ok:
        print()
        print('[OK] 所有归档回读校验通过')
    else:
        print()
        print('[FAIL] 回读校验失败，请检查上方输出')

    return ok


def main():
    metadata = generate()
    if metadata is None:
        return 1
    if not metadata:
        print('没有生成任何 payload，跳过校验')
        return 0
    if not verify(metadata):
        return 1
    print()
    print('下一步: bash script/pack.sh')
    return 0


if __name__ == '__main__':
    main()
