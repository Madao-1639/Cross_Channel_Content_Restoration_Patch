#!/usr/bin/env python3
"""
CROSS†CHANNEL Steam 版内容还原与汉化补丁安装器
CROSS†CHANNEL Steam Edition - Content Restoration & Localization Patch Installer

安装说明：
1. 运行安装器，自动检测游戏路径
2. 或手动选择游戏根目录（包含 AdvHD.exe）
3. 确认安装

Installation:
1. Run the installer to auto-detect game path
2. Or manually select the game root directory (contains AdvHD.exe)
3. Confirm installation
"""

import os
import sys
import shutil
from pathlib import Path
import arcbuild

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass


def get_version():
    packed = Path(__file__).parent / 'VERSION'
    if packed.exists():
        return packed.read_text(encoding='utf-8').strip()
    dev = Path(__file__).parent.parent / 'VERSION'
    return dev.read_text(encoding='utf-8').strip()


CURRENT_VERSION = get_version()
TARGET_APPID = '812560'
GAME_FOLDER_NAME = 'CROSS†CHANNEL Steam Edition'

# 平台判定的**唯一来源**：注册表（Steam 库路径、语言设置）都只在 Windows 有。
# 非 Windows（Linux 下跑本安装器）⇒ 自动检测与"设默认语言"都跳过，语言留给游戏设置界面。
IS_WINDOWS = sys.platform.startswith("win") and os.name == "nt"

if IS_WINDOWS:
    import winreg          # 注册表只在 Windows 存在；非 Windows 不导入（Linux 无此模块）
else:
    winreg = None


def parse_vdf(vdf_path):
    """
    解析 Steam libraryfolders.vdf 文件
    VDF 格式类似 JSON，使用递归下降解析

    Returns:
        list: [(library_path, {appid: size, ...}), ...]
    """
    try:
        with open(vdf_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception:
        return []

    def tokenize(text):
        tokens = []
        i = 0
        while i < len(text):
            if text[i].isspace():
                i += 1
                continue
            if text[i] == '"':
                i += 1
                start = i
                while i < len(text) and text[i] != '"':
                    if text[i] == '\\' and i + 1 < len(text):
                        i += 2
                    else:
                        i += 1
                tokens.append(('STRING', text[start:i]))
                i += 1
            elif text[i] == '{':
                tokens.append(('LBRACE', '{'))
                i += 1
            elif text[i] == '}':
                tokens.append(('RBRACE', '}'))
                i += 1
            elif text[i:i + 2] == '//':
                while i < len(text) and text[i] != '\n':
                    i += 1
            else:
                i += 1
        return tokens

    def parse_dict(tokens, pos):
        result = {}
        while pos < len(tokens):
            token_type, token_value = tokens[pos]

            if token_type == 'RBRACE':
                return result, pos + 1
            elif token_type == 'STRING':
                key = token_value
                pos += 1

                if pos >= len(tokens):
                    break

                next_type, next_value = tokens[pos]

                if next_type == 'LBRACE':
                    pos += 1
                    value, pos = parse_dict(tokens, pos)
                    result[key] = value
                elif next_type == 'STRING':
                    result[key] = next_value
                    pos += 1
                else:
                    pos += 1
            else:
                pos += 1

        return result, pos

    tokens = tokenize(content)
    data, _ = parse_dict(tokens, 0)

    libraries = []
    if 'libraryfolders' not in data:
        return []

    libraryfolders = data['libraryfolders']
    for key, value in libraryfolders.items():
        if not isinstance(value, dict):
            continue
        if 'path' not in value or 'apps' not in value:
            continue

        library_path = value['path'].replace('\\\\', '\\')
        apps = value['apps'] if isinstance(value['apps'], dict) else {}
        libraries.append((library_path, apps))

    return libraries


def _steam_roots():
    """Steam 安装根的候选列表：Windows 走注册表，Linux 走常见目录。"""
    roots = []
    if IS_WINDOWS:
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam")
            steam_path, _ = winreg.QueryValueEx(key, "SteamPath")
            winreg.CloseKey(key)
            roots.append(Path(steam_path))
        except Exception as e:  # noqa: BLE001 - 拿不到就走 Linux 候选/手动选择
            print(f"读取 Steam 安装路径失败: {e}")
    if not IS_WINDOWS:
        home = Path.home()
        for rel in (".steam/steam", ".steam/root", ".local/share/Steam"):
            roots.append(home / rel)
    return roots


def find_steam_game_path():
    """自动检测 Steam 游戏路径（Windows 走注册表；Linux 扫常见 Steam 根目录）。

    游戏本体是 Windows 版，但 `steamapps/common/<游戏>` 在 Linux 上也是普通目录，
    因此路径检测与文件替换照常可用 —— 只有注册表相关的步骤（默认语言）要跳过。

    Returns:
        Path or None: 游戏路径，如果找不到则返回 None
    """
    for steam_path in _steam_roots():
        libraryfolders_vdf = steam_path / "steamapps" / "libraryfolders.vdf"
        if not libraryfolders_vdf.exists():
            continue
        try:
            libraries = parse_vdf(libraryfolders_vdf)
        except Exception as e:  # noqa: BLE001
            print(f"解析 {libraryfolders_vdf} 失败: {e}")
            continue
        for library_path, apps in libraries:
            if TARGET_APPID not in apps:
                continue
            game_path = Path(library_path) / "steamapps" / "common" / GAME_FOLDER_NAME
            if (game_path / "AdvHD.exe").exists():
                return game_path
    return None


LANG_KEY = r"Software\WillPlus\AdvHD\LanguageSettings"


def language_value_name(game_path):
    """引擎写这条注册表时用的**值名** = 游戏目录，且经**系统 ANSI 代码页**转换（编不出的字符变 `?`）。

    **仅 Windows 使用**（`set_default_language()` 内部调用；非 Windows 不会走到这里）。
    `CROSS†CHANNEL Steam Edition` 的 `†`(U+2020)：CP932 其实能编（0x8181），但系统 ACP 是 GBK(936)
    ⇒ 落到注册表就成了 `CROSS?CHANNEL Steam Edition`（本机实测）。安装器必须复刻同一规则，
    否则写出的值名和引擎读的对不上、等于没设。
    """
    s = str(game_path)
    for enc in ("mbcs", "cp932", "utf-8"):
        try:
            return s.encode(enc, errors="replace").decode(enc)
        except (LookupError, UnicodeError):
            continue
    return s


def set_default_language(game_path, lang="zh-CN"):
    r"""把「默认语言」写成语言包名（`zh-CN`），使游戏不选语言也按中文启动。

    引擎启动时读 `HKCU\Software\WillPlus\AdvHD\LanguageSettings`（值名 = 游戏目录），
    值为空 ⇒ English（语言包整个不生效）。这就是"语言包已就位却仍是英文"的根因。

    **仅 Windows**：注册表是 Windows 专有机制；Linux（Wine/Proton 或无 winreg 的解释器）直接跳过，
    让玩家在游戏设置界面自己选语言。
    """
    if not IS_WINDOWS or winreg is None:
        print("  [跳过] 非 Windows 平台：注册表不可用，默认语言需在游戏设置界面选择")
        return False

    name = language_value_name(game_path)
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, LANG_KEY, 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, name, 0, winreg.REG_SZ, lang)
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, LANG_KEY) as k:
        got, _ = winreg.QueryValueEx(k, name)
    print(f"  [OK] 默认语言 = {got!r}（值名 {name!r}）")
    print("       改回英文：启动游戏，在设置界面把「语言」改回 English")
    return got == lang


def _ask_default_language(game_path):
    """询问并写入默认语言（仅 Windows）。"""
    print("是否把游戏默认语言设为「中文(简体，中国)」？")
    print("Set Chinese (Simplified) as the default in-game language?")
    answer = input("  设为默认中文? (Y/n): ").strip().lower()
    if answer in ("n", "no"):
        print("  [跳过] 未改注册表；启动游戏后在设置界面手动在 Language Pack 选「中文(简体，中国)」即可")
    else:
        try:
            set_default_language(game_path)
        except Exception as e:  # noqa: BLE001 - 提示即止，不该让安装失败
            print(f"  [警告] 写注册表失败：{e}")
            print("         启动游戏后在设置界面手动在 Language Pack 选「中文(简体，中国)」")
    print()


def deploy_language_pack(pack_src, pack_dst):
    """把 payload/zh-CN/ 原样铺到 <游戏目录>/zh-CN/（语言包，新增文件夹，不碰原版文件）。

    语言包内的归档**只装中文相关成员**，引擎按成员优先读它、其余从根目录读 ⇒
    卸载＝删这个文件夹 + Steam 校验还原根目录归档。
    """
    import hashlib
    import json

    print(f"部署语言包 {pack_src} -> {pack_dst} ...")
    pack_dst.mkdir(parents=True, exist_ok=True)
    manifest_path = pack_src / "PACK_MANIFEST.json"
    manifest = {}
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    count = 0
    for src in sorted(p for p in pack_src.iterdir() if p.is_file()):
        if src.name == "PACK_MANIFEST.json":
            continue
        dst = pack_dst / src.name
        data = src.read_bytes()
        if dst.exists() and dst.read_bytes() == data:
            print(f"  [跳过] {src.name}（已是目标内容）")
        else:
            dst.write_bytes(data)
            print(f"  [写入] {src.name}  {len(data):,} bytes")
        count += 1
        want = (manifest.get(src.name) or {}).get("sha256")
        got = hashlib.sha256(dst.read_bytes()).hexdigest()
        if want and got != want:
            raise RuntimeError(f"语言包成员 {src.name} 哈希不匹配")
    print(f"  语言包部署完成：{count} 个文件")
    print()


def merge_arc(game_arc_path, patch_arc_path, output_path, metadata_path=None, asset_name=None):
    """
    合并游戏原有的 arc 文件和补丁 arc 文件（资源级替换，按元数据指定的顺序）

    Args:
        game_arc_path: 游戏原有的 arc 文件路径
        patch_arc_path: 补丁 arc 文件路径
        output_path: 输出文件路径
        metadata_path: 元数据文件路径（JSON，包含成员顺序和变化信息）
        asset_name: asset 文件名（如 'Chip2.arc'，用于查找元数据中对应的条目）
    """
    import json

    # Script.arc 等文件是补丁新增的整档，Steam 原版不存在该文件
    if Path(game_arc_path).exists():
        game_members = {nb.decode('utf-16le'): (nb, d)
                        for nb, d in arcbuild.read_raw(str(game_arc_path))}
    else:
        game_members = {}

    patch_members = {nb.decode('utf-16le'): (nb, d)
                     for nb, d in arcbuild.read_raw(str(patch_arc_path))}

    metadata = {}
    if metadata_path and Path(metadata_path).exists() and asset_name:
        try:
            with open(metadata_path, 'r', encoding='utf-8') as f:
                all_metadata = json.load(f)
            metadata = all_metadata.get(asset_name, {})
        except Exception as e:
            print(f"    警告：无法读取元数据 {metadata_path}: {e}")

    merged = []
    target_members = metadata.get('members', [])
    deleted_count = sum(1 for m in target_members if isinstance(m, dict) and m.get('type') == 'deleted')

    if target_members:
        # 按元数据中的目标顺序重组
        # target_members 中的每个元素是 {"name": "...", "type": "keep|added|modified|deleted"}
        missing_members = []
        for member_info in target_members:
            if isinstance(member_info, dict):
                name = member_info.get('name')
                member_type = member_info.get('type', 'keep')
            else:
                # 兼容旧格式（直接是字符串）
                name = member_info
                member_type = 'keep'

            if not name:
                continue

            if member_type == 'deleted':
                # 已删除：不写入输出
                continue

            if member_type in ('added', 'modified'):
                # 新增或修改：优先用补丁版本，找不到则降级用原版
                if name in patch_members:
                    patch_name_bytes, patch_data = patch_members[name]
                    merged.append((patch_name_bytes, patch_data))
                elif name in game_members:
                    name_bytes, data = game_members[name]
                    merged.append((name_bytes, data))
                    missing_members.append((name, member_type, 'patch'))
                else:
                    missing_members.append((name, member_type, 'both'))
            else:  # 'keep'
                # 保持原版：优先用原版，找不到则用补丁版本
                if name in game_members:
                    name_bytes, data = game_members[name]
                    merged.append((name_bytes, data))
                elif name in patch_members:
                    patch_name_bytes, patch_data = patch_members[name]
                    merged.append((patch_name_bytes, patch_data))
                    missing_members.append((name, member_type, 'game'))
                else:
                    missing_members.append((name, member_type, 'both'))

        if missing_members:
            print(f"    警告：{len(missing_members)} 个成员未能在预期位置找到")
            for name, mtype, missing_from in missing_members:
                print(f"      - {name} (type={mtype}): {missing_from} 中不存在")
    else:
        # 降级方案：如果没有元数据，按游戏原顺序处理
        for name_bytes, data in arcbuild.read_raw(str(game_arc_path)):
            name = name_bytes.decode('utf-16le')
            if name in patch_members:
                patch_name_bytes, patch_data = patch_members[name]
                merged.append((patch_name_bytes, patch_data))
            else:
                merged.append((name_bytes, data))

        for name, (name_bytes, data) in patch_members.items():
            if name not in game_members:
                merged.append((name_bytes, data))

    arcbuild.write_arc(merged, str(output_path))

    return len(game_members), len(patch_members), deleted_count, len(merged)


def select_game_directory():
    """
    选择游戏目录

    Returns:
        Path or None: 游戏路径
    """
    print("=" * 60)
    print(f"CROSS†CHANNEL 内容还原与汉化补丁 {CURRENT_VERSION}")
    print("CROSS†CHANNEL Steam Edition - Content Restoration Patch")
    print("=" * 60)
    print()

    print("正在检测游戏路径...")
    print("Detecting game path...")
    print()

    auto_path = find_steam_game_path()

    if auto_path:
        print(f"检测到游戏路径：")
        print(f"  {auto_path}")
        print()
        use_auto = input("使用此路径? (Y/n): ").strip().lower()
        if use_auto not in ['n', 'no']:
            return auto_path
        print()
    else:
        print("未能自动检测到游戏路径")
        print()

    print("请输入游戏根目录路径（包含 AdvHD.exe 的目录）：")
    print("Please enter the game root directory path (contains AdvHD.exe):")
    print()
    print("提示：")
    print("  1. 直接输入路径（如 E:\\SteamLibrary\\steamapps\\common\\CROSS†CHANNEL Steam Edition）")
    print("  2. 输入 '.' 使用安装器所在目录")
    print("  3. 留空取消安装")
    print()

    user_input = input("路径 / Path: ").strip()

    if not user_input:
        return None

    if user_input == '.':
        return Path.cwd()

    user_input = user_input.strip('"').strip("'")

    game_path = Path(user_input)

    if not game_path.exists():
        print(f"错误：路径不存在 / Path does not exist: {game_path}")
        return None

    if not (game_path / "AdvHD.exe").exists():
        print(f"错误：该目录下未找到 AdvHD.exe")
        print(f"Error: AdvHD.exe not found in this directory")
        return None

    return game_path


def install():
    """执行安装"""

    game_path = select_game_directory()

    if not game_path:
        print("安装已取消")
        print("Installation cancelled")
        return False

    print()
    print("=" * 60)
    print(f"游戏目录: {game_path}")
    print("=" * 60)
    print()

    if getattr(sys, 'frozen', False):
        base_dir = Path(sys._MEIPASS)
    else:
        base_dir = Path(__file__).parent.parent
    payload_dir = base_dir / "payload"

    if not payload_dir.exists():
        print(f"错误：找不到 payload 目录")
        print(f"Error: payload directory not found")
        print(f"预期位置：{payload_dir}")
        return False

    print("检测到补丁文件")
    print()

    # 定义需要处理的文件（Chip1.arc、SysVoice.arc 本次无变化，不处理）
    # 根目录层：与语言无关的归档（脚本/CG/语音）。中文文本与汉化 UI 在语言包 zh-CN/ 里。
    files_to_process = [
        {
            "name": "Rio.arc (脚本)",
            "game_path": game_path / "Rio.arc",
            "patch_path": payload_dir / "Rio_patch.arc",
            "output_path": game_path / "Rio.arc",
        },
        {
            "name": "Chip2.arc (事件 CG)",
            "game_path": game_path / "Chip2.arc",
            "patch_path": payload_dir / "Chip2_patch.arc",
            "output_path": game_path / "Chip2.arc",
        },
        {
            "name": "Voice.arc (语音资源)",
            "game_path": game_path / "Voice.arc",
            "patch_path": payload_dir / "Voice_patch.arc",
            "output_path": game_path / "Voice.arc",
        },
    ]
    pack_src = payload_dir / "zh-CN"

    print("检查补丁文件...")
    files_to_process = [f for f in files_to_process if f["patch_path"].exists()]

    for file_info in files_to_process:
        if not file_info["patch_path"].exists():
            print(f"错误：缺失补丁文件 {file_info['patch_path']}")
            print(f"Error: Missing patch file {file_info['patch_path']}")
            return False

        # Script.arc 是补丁新增的整档，Steam 原版没有此文件是正常情况
        if not file_info["game_path"].exists() and file_info["game_path"].name != "Script.arc":
            print(f"错误：游戏文件不存在 {file_info['game_path']}")
            print(f"Error: Game file not found {file_info['game_path']}")
            return False

    print("所有文件检查通过")
    print()

    print("即将安装内容还原与汉化补丁，这将修改以下文件：")
    print("About to install the content restoration patch. The following files will be modified:")
    for file_info in files_to_process:
        print(f"  - {file_info['name']}")
    if pack_src.exists():
        print("  - zh-CN/（语言包：中文文本 / 汉化 UI / 字体；新增文件夹）")
    print()
    print("建议：安装前通过 Steam 验证游戏完整性以创建备份")
    print("Recommendation: Verify game integrity via Steam before installation to create a backup")
    print()

    confirm = input("确认安装? (Y/n): ").strip().lower()
    if confirm in ['n', 'no']:
        print("安装已取消")
        print("Installation cancelled")
        return False

    print()
    print("开始安装...")
    print()

    try:
        metadata_path = payload_dir / "METADATA.json"
        for file_info in files_to_process:
            print(f"处理 {file_info['name']}...")

            asset_name = file_info["output_path"].name
            game_count, patch_count, deleted_count, merged_count = merge_arc(
                file_info["game_path"],
                file_info["patch_path"],
                file_info["output_path"],
                metadata_path,
                asset_name
            )
            print(f"  原文件成员: {game_count}")
            print(f"  补丁成员: {patch_count}")
            print(f"  删除成员: {deleted_count}")
            print(f"  合并后成员: {merged_count}")

            print(f"{file_info['name']} 完成")
            print()

        if pack_src.exists():
            deploy_language_pack(pack_src, game_path / "zh-CN")

            # 语言包不会被自动采用：引擎读注册表里的「所选语言」，缺省/空 = English。
            if not IS_WINDOWS:
                print("  [跳过] 非 Windows 平台：无法写注册表")
                print("         启动游戏后在设置界面手动选「中文(简体，中国)」即可")
                print()
            else:
                _ask_default_language(game_path)

        print("验证安装文件...")
        import hashlib
        import json

        if metadata_path.exists():
            try:
                with open(metadata_path, 'r', encoding='utf-8') as f:
                    metadata = json.load(f)

                all_verified = True
                for asset_name, asset_info in metadata.items():
                    expected_hash = asset_info.get('checksum')
                    installed_path = game_path / asset_name

                    if not installed_path.exists():
                        print(f"  [FAIL] {asset_name}: 文件不存在")
                        all_verified = False
                        continue

                    file_hash = hashlib.sha256(installed_path.read_bytes()).hexdigest()
                    if file_hash == expected_hash:
                        print(f"  [OK] {asset_name}")
                    else:
                        print(f"  [FAIL] {asset_name}: 哈希不匹配")
                        print(f"    预期: {expected_hash}")
                        print(f"    实际: {file_hash}")
                        all_verified = False

                if not all_verified:
                    print()
                    print("=" * 60)
                    print("验证失败：部分文件不正确")
                    print("Verification failed: some files are incorrect")
                    print("=" * 60)
                    print()
                    print("请按以下步骤恢复后重新安装：")
                    print("Please follow these steps to recover and reinstall:")
                    print()
                    print("1. 打开 Steam，找到《CROSS†CHANNEL Steam Edition》")
                    print("   Open Steam and find 'CROSS†CHANNEL Steam Edition'")
                    print()
                    print("2. 右键点击游戏 -> 属性 -> 已安装文件 -> 校验游戏文件完整性")
                    print("   Right-click game -> Properties -> Installed Files -> Verify integrity")
                    print()
                    print("3. 等待 Steam 完成验证和修复")
                    print("   Wait for Steam to complete verification")
                    print()
                    print("4. 重新运行本安装程序")
                    print("   Run this installer again")
                    return False

            except Exception as e:
                print(f"  警告：无法验证文件: {e}")

        print()
        print("=" * 60)
        print("安装完成！")
        print("Installation completed!")
        print("=" * 60)
        print()
        print("现在可以启动游戏体验还原内容")
        print("You can now launch the game to experience the restored content")
        print()

        return True

    except Exception as e:
        print()
        print("=" * 60)
        print(f"安装失败：{e}")
        print(f"Installation failed: {e}")
        print("=" * 60)
        print()
        print("请通过 Steam 验证游戏完整性恢复原始文件")
        print("Please verify game integrity via Steam to restore original files")
        return False


if __name__ == "__main__":
    try:
        success = install()
        input("\n按任意键退出...")
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n\n安装已取消")
        print("Installation cancelled")
        input("\n按任意键退出...")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n未预期的错误：{e}")
        print(f"Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        input("\n按任意键退出...")
        sys.exit(1)
