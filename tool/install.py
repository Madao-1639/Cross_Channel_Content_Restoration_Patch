#!/usr/bin/env python3
"""
CROSS†CHANNEL Steam 版内容还原与汉化补丁安装器
CROSS†CHANNEL Steam Edition - Content Restoration & Localization Patch Installer

安装说明：
0. 先选**安装器界面语言**（中文 / English）—— 之后全流程只显示这一种语言
1. 运行安装器，自动检测游戏路径
2. 或手动选择游戏根目录（包含 AdvHD.exe）
3. 确认安装
4. 设定默认语言（按环境建议，可改）

Installation:
0. Choose the **installer language** (Chinese / English); the rest of the run uses only that one
1. Run the installer to auto-detect game path
2. Or manually select the game root directory (contains AdvHD.exe)
3. Confirm installation
4. Set the default in-game language (a suggestion is detected from your environment)
"""

import os
import sys
import shutil
from pathlib import Path
import arcbuild
import language_detect

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
    # `ctypes` **必须在这里顶层导入**：默认语言判定（`language_detect.windows_ui_language`）
    # 用它调 `GetUserDefaultLocaleName`，而该文件是**作为数据文件**随包带的（`--add-data`），
    # PyInstaller 不分析它的导入 ⇒ 只在函数内懒导入的话，单文件 exe 里会炸
    # "No module named 'ctypes'"（v0.6.0 打包实测）。
    import ctypes
else:
    winreg = None
    ctypes = None


def say(zh, en):
    """**单语输出**：按已选的安装器界面语言打印（`en` 缺省时退回中文，仅作容错）。"""
    print(tr(zh, en or zh))


#: 安装器界面语言：`'zh'` / `'en'`。由 `choose_ui_language()` 在**第一步**定下，
#: 之后整个流程只印这一种语言（不再中英并排）。
UI_LANG = 'zh'


def tr(zh, en):
    """按当前界面语言取一条文案。"""
    return zh if UI_LANG == 'zh' else en


def ask(zh, en=None):
    """单语提示符 + 读一行（返回去空白的小写串）。"""
    return input(tr(zh, en or zh)).strip().lower()


def hr(ch='='):
    print(ch * 60)


def clear():
    """清屏。**只在真终端**做（管道/重定向时跳过，避免把日志弄脏）。"""
    if not sys.stdout.isatty():
        return
    print('\033[2J\033[H', end='', flush=True)


def stage(zh, en=None):
    """阶段标题：清屏 + 上下分隔线 + 标题 + 空行 —— 阶段之间因此总是干净的。"""
    if sys.stdout.isatty():
        clear()
    else:
        print()          # 管道/重定向：上一行可能停在提示符上，补个换行免得分隔线粘上
    hr()
    print(tr(zh, en or zh))
    hr()
    print()


def lang_name(lang):
    """语言包目录名（`zh-CN` / `''`）→ 当前界面语言下的显示名。"""
    if lang:
        return tr('中文(简体，中国)', 'Chinese (Simplified)')
    return tr('English（原版英文）', 'English (original)')


#: 归档的显示名（按界面语言取）。
_ARC_LABELS = {
    'Rio.arc': ('Rio.arc（剧情脚本）', 'Rio.arc (story scripts)'),
    'Chip2.arc': ('Chip2.arc（事件 CG）', 'Chip2.arc (event CGs)'),
    'Voice.arc': ('Voice.arc（语音）', 'Voice.arc (voice tracks)'),
}


def arc_label(key):
    zh, en = _ARC_LABELS[key]
    return tr(zh, en)


def banner(zh, en=None):
    """结果横幅：分隔线 + 标题 + 分隔线（**不清屏**，保留前面的结果）。"""
    print()
    hr()
    print(tr(zh, en or zh))
    hr()
    print()


#: `--ui-lang=` 的取值 → 界面语言。
_UI_LANG_CHOICES = {
    '1': 'zh', 'zh': 'zh', 'cn': 'zh', 'chinese': 'zh', 'zh-cn': 'zh',
    '2': 'en', 'en': 'en', 'english': 'en',
}


def _cli_ui_lang():
    """从命令行取 `--ui-lang=zh|en`（无则 `None`）。"""
    for arg in sys.argv[1:]:
        if arg.startswith('--ui-lang='):
            return _UI_LANG_CHOICES.get(arg.split('=', 1)[1].strip().lower())
    return None


def choose_ui_language():
    """启动**第一步**：选安装器界面语言。

    这是整个安装器里**唯一双语并排**的一屏 —— 此时还不知道玩家看哪种语言。
    选完（或 `--ui-lang=` 给定）之后，全部输出只使用它。
    默认值取自环境（`tool/language_detect.py`）：中文环境默认 1，否则默认 2。
    """
    global UI_LANG
    forced = _cli_ui_lang()
    if forced:
        UI_LANG = forced
        return

    detected, _seen = language_detect.detect_default_language()
    default = 'zh' if detected else 'en'

    clear()
    hr()
    print('CROSS†CHANNEL 内容还原与汉化补丁 %s' % CURRENT_VERSION)
    print('CROSS†CHANNEL Steam Edition - Content Restoration Patch')
    hr()
    print()
    print('  安装器语言 / Installer language')
    print()
    print('    1) 中文')
    print('    2) English')
    print()
    answer = input('  1/2 (Enter = %d): ' % (1 if default == 'zh' else 2)).strip().lower()
    UI_LANG = _UI_LANG_CHOICES.get(answer, default)
    print()


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
            say(f"读取 Steam 安装路径失败: {e}",
                f"Failed to read the Steam install path: {e} (will ask you instead)")
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
            say(f"解析 {libraryfolders_vdf.name} 失败: {e}",
                f"Failed to parse {libraryfolders_vdf.name}: {e} (skipped)")
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
    值为**空串** ⇒ English（语言包整个不生效）；值**不存在** ⇒ 引擎在"恰好一个语言包目录"时
    会无视语言环境直接启用它（见 `tool/language_detect.py` 的说明）⇒ 英文必须**显式写空串**。
    `lang=''` 就是那条空串。

    **仅 Windows**：注册表是 Windows 专有机制；Linux（Wine/Proton 或无 winreg 的解释器）直接跳过，
    让玩家在游戏设置界面自己选语言。
    """
    if not IS_WINDOWS or winreg is None:
        say("  [跳过] 非 Windows 平台：注册表不可用，默认语言需在游戏内设置界面选择",
            "  [skip] Not Windows: no registry — pick the language in the in-game settings dialog")
        return False

    name = language_value_name(game_path)
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, LANG_KEY, 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, name, 0, winreg.REG_SZ, lang)
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, LANG_KEY) as k:
        got, _ = winreg.QueryValueEx(k, name)
    say("  [OK] 默认语言 = %s" % lang_name(got),
        "  [OK] default language = %s" % lang_name(got))
    say("       注册表 %s · 值名 %r" % (LANG_KEY, name),
        "       registry %s · value name %r" % (LANG_KEY, name))
    say("       改语言：启动游戏，在设置界面的「Language Pack」里选",
        "       To switch: run the game and pick it in the start-up settings dialog")
    return got == lang


def get_default_language(game_path):
    """注册表里**当前**的「所选语言」。

    → `None` = 值不存在（首次安装 / 从没选过）；`''` = 玩家（或上一次安装）明确选了 English；
    `'zh-CN'` = 中文。

    **仅 Windows**（非 Windows 一律 `None`）。
    """
    if not IS_WINDOWS or winreg is None:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, LANG_KEY) as k:
            value, _ = winreg.QueryValueEx(k, language_value_name(game_path))
        return value
    except OSError:
        return None


#: `--lang=` 的取值 → 语言包目录名（`auto` 交给环境判定）。
_CLI_LANG = {
    'zh': 'zh-CN', 'zh-cn': 'zh-CN', 'cn': 'zh-CN', 'chinese': 'zh-CN', 'schinese': 'zh-CN',
    'en': '', 'english': '', '': '',
}


def _cli_lang():
    """从命令行取 `--lang=zh-CN|en|auto`（无则 `None`）。"""
    for arg in sys.argv[1:]:
        if arg.startswith('--lang='):
            raw = arg.split('=', 1)[1].strip().lower()
            if raw in ('auto', ''):
                return None
            return _CLI_LANG.get(raw, None)
    return None


def _ask_default_language(game_path, interactive=True):
    """首次安装的默认语言：按玩家环境判定 → 询问 → 写注册表（仅 Windows）。

    规则：
      * `--lang=zh-CN|en` 显式给出 ⇒ 无条件按它写（脚本/静默安装用）；
      * 注册表里**已有非空值**（`zh-CN`）⇒ 玩家选过中文，原样保留，不覆盖；
      * 值为**空串或不存在** ⇒ 由玩家选择：先按环境给建议，交互式询问（回车 = 采纳建议），
        非交互（EOF / `interactive=False`）直接用建议。
    环境判定只看 Steam 客户端语言 + Windows 界面语言，任一为中文即中文
    —— 为什么不信 `appmanifest`、本作为何要显式写空串，见 `tool/language_detect.py`。
    """
    choice = _cli_lang()
    if choice is not None:
        say("  [命令行] --lang 指定 → %s" % lang_name(choice),
            "  [cli] --lang given → %s" % lang_name(choice))
        _write_default_language(game_path, choice)
        return

    existing = get_default_language(game_path)
    if existing:
        say("  [保留] 注册表已有默认语言 = %s（不改写）" % lang_name(existing),
            "  [keep] the registry already sets the default language = %s (left untouched)" % lang_name(existing))
        say("       要改写：--lang=zh-CN | --lang=en",
            "       To override: --lang=zh-CN | --lang=en")
        print()
        return

    detected, seen = language_detect.detect_default_language()
    say("  判定依据：", "  Signals:")
    for key, raw, where, verdict in seen:
        print("    %s: %r → %s" % (language_detect.probe_label(key, UI_LANG), raw,
                                   lang_name(verdict) if verdict else lang_name('')))
    print()

    if interactive:
        say("  建议默认：%s" % lang_name(detected),
            "  Suggested default: %s" % lang_name(detected))
        print()
        say("  要哪种默认语言？", "  Which default language?")
        print("    1) %s" % lang_name('zh-CN'))
        print("    2) %s" % lang_name(''))
        print()
        default_no = 1 if detected else 2
        answer = ask("  1/2 (回车 = %d): " % default_no, "  1/2 (Enter = %d): " % default_no)
        print()
        if answer in ('1', 'zh', 'cn', 'chinese'):
            choice = 'zh-CN'
        elif answer in ('2', 'en', 'english'):
            choice = ''
        else:
            choice = detected
    else:
        choice = detected

    _write_default_language(game_path, choice)


def _write_default_language(game_path, lang):
    """写注册表，失败只提示（不该让安装失败）。"""
    try:
        set_default_language(game_path, lang)
    except Exception as e:  # noqa: BLE001
        say("  [警告] 写注册表失败: %s" % e,
            "  [warn] registry write failed: %s" % e)
        say("         启动游戏后在设置界面选语言",
            "         Pick the language in the in-game settings dialog")
    print()


def deploy_language_pack(pack_src, pack_dst):
    """把 payload/zh-CN/ 原样铺到 <游戏目录>/zh-CN/（语言包，新增文件夹，不碰原版文件）。

    语言包内的归档**只装中文相关成员**，引擎按成员优先读它、其余从根目录读 ⇒
    卸载＝删这个文件夹 + Steam 校验还原根目录归档。
    """
    import hashlib
    import json

    say("部署语言包 %s -> %s ..." % (pack_src.name, pack_dst),
        "Deploying the language pack %s -> %s ..." % (pack_src.name, pack_dst))
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
            say("  [跳过] %s（已是目标内容）" % src.name,
                "  [skip] %s (already up to date)" % src.name)
        else:
            dst.write_bytes(data)
            say("  [写入] %s  %s bytes" % (src.name, format(len(data), ',')),
                "  [write] %s  %s bytes" % (src.name, format(len(data), ',')))
        count += 1
        want = (manifest.get(src.name) or {}).get("sha256")
        got = hashlib.sha256(dst.read_bytes()).hexdigest()
        if want and got != want:
            raise RuntimeError(tr("语言包成员 %s 哈希不匹配" % src.name,
                                 "language pack member %s: hash mismatch" % src.name))
    say("  语言包部署完成：%d 个文件" % count,
        "  Language pack deployed: %d file(s)" % count)
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
            say("    警告：无法读取元数据 %s: %s" % (metadata_path, e),
                "    Warning: cannot read metadata %s: %s (member order falls back)" % (metadata_path, e))

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
            say("    警告：%d 个成员未能在预期位置找到" % len(missing_members),
                "    Warning: %d member(s) not found where expected" % len(missing_members))
            for name, mtype, missing_from in missing_members:
                say("      - %s (type=%s): %s 中不存在" % (name, mtype, missing_from),
                    "      - %s (type=%s): missing from %s" % (name, mtype, missing_from))
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
    选择游戏目录（阶段一）

    Returns:
        Path or None: 游戏路径
    """
    stage('选择游戏目录', 'Choose the game folder')

    say("正在检测游戏路径...", "Detecting the game folder ...")
    print()

    auto_path = find_steam_game_path()

    if auto_path:
        say("检测到游戏路径：", "Detected game folder:")
        print("  %s" % auto_path)
        print()
        if ask("使用此路径？(Y/n): ", "Use this path? (Y/n): ") not in ('n', 'no'):
            return auto_path
        print()
    else:
        say("未能自动检测到游戏路径", "Could not auto-detect the game folder")
        print()

    say("请输入游戏根目录路径（包含 AdvHD.exe 的目录）：",
        "Please enter the game root directory path (the folder containing AdvHD.exe):")
    print()
    say("提示：", "Hints:")
    say("  1. 直接输入路径，例如", "  1. Type the full path, e.g.")
    print("     E:\\SteamLibrary\\steamapps\\common\\CROSS†CHANNEL Steam Edition")
    say("  2. 输入 '.' 使用安装器所在目录", "  2. Type '.' to use the installer's own folder")
    say("  3. 留空取消安装", "  3. Leave it empty to cancel")
    print()

    user_input = input(tr("路径: ", "Path: ")).strip()

    if not user_input:
        return None

    if user_input == '.':
        return Path.cwd()

    user_input = user_input.strip('"').strip("'")

    game_path = Path(user_input)

    if not game_path.exists():
        say("错误：路径不存在：%s" % game_path,
            "Error: path does not exist: %s" % game_path)
        return None

    if not (game_path / "AdvHD.exe").exists():
        say("错误：该目录下未找到 AdvHD.exe",
            "Error: AdvHD.exe not found in this directory")
        return None

    return game_path


def install():
    """执行安装（阶段：选目录 → 确认文件 → 安装 → 默认语言 → 校验 → 完成）"""

    game_path = select_game_directory()

    if not game_path:
        print()
        say("安装已取消", "Installation cancelled")
        return False

    if getattr(sys, 'frozen', False):
        base_dir = Path(sys._MEIPASS)
    else:
        base_dir = Path(__file__).parent.parent
    payload_dir = base_dir / "payload"

    stage('确认要修改的文件', 'Files that will be modified')
    print("  %s%s" % (tr("游戏目录：", "Game folder: "), game_path))
    print()

    if not payload_dir.exists():
        say("错误：找不到 payload 目录", "Error: payload directory not found")
        print("  %s%s" % (tr("预期位置：", "expected at: "), payload_dir))
        return False

    # 定义需要处理的文件（Chip1.arc、SysVoice.arc 本次无变化，不处理）
    # 根目录层：与语言无关的归档（脚本/CG/语音）。中文文本与汉化 UI 在语言包 zh-CN/ 里。
    files_to_process = [
        {
            "name": arc_label('Rio.arc'),
            "game_path": game_path / "Rio.arc",
            "patch_path": payload_dir / "Rio_patch.arc",
            "output_path": game_path / "Rio.arc",
        },
        {
            "name": arc_label('Chip2.arc'),
            "game_path": game_path / "Chip2.arc",
            "patch_path": payload_dir / "Chip2_patch.arc",
            "output_path": game_path / "Chip2.arc",
        },
        {
            "name": arc_label('Voice.arc'),
            "game_path": game_path / "Voice.arc",
            "patch_path": payload_dir / "Voice_patch.arc",
            "output_path": game_path / "Voice.arc",
        },
    ]
    pack_src = payload_dir / "zh-CN"

    say("检查补丁文件...", "Checking patch files ...")
    files_to_process = [f for f in files_to_process if f["patch_path"].exists()]

    for file_info in files_to_process:
        if not file_info["patch_path"].exists():
            say("错误：缺失补丁文件 %s" % file_info['patch_path'],
                "Error: missing patch file %s" % file_info['patch_path'])
            return False

        # Script.arc 是补丁新增的整档，Steam 原版没有此文件是正常情况
        if not file_info["game_path"].exists() and file_info["game_path"].name != "Script.arc":
            say("错误：游戏文件不存在 %s" % file_info['game_path'],
                "Error: game file not found %s" % file_info['game_path'])
            return False

    say("所有文件检查通过", "All files present")
    print()

    say("即将安装内容还原与汉化补丁，这将修改以下文件：",
        "About to install the content restoration patch. The following files will be modified:")
    for file_info in files_to_process:
        print("  - %s" % file_info['name'])
    if pack_src.exists():
        say("  - zh-CN/（语言包：中文文本 / 汉化 UI / 字体；新增文件夹）",
            "  - zh-CN/ (language pack: Chinese text / localized UI / fonts; new folder)")
    print()
    say("建议：安装前先通过 Steam 校验游戏文件，以便需要时恢复",
        "Recommendation: verify the game files via Steam first, so you have a way back")
    print()

    if ask("确认安装？(Y/n): ", "Install now? (Y/n): ") in ('n', 'no'):
        print()
        say("安装已取消", "Installation cancelled")
        return False

    stage('安装中', 'Installing')

    try:
        metadata_path = payload_dir / "METADATA.json"
        for file_info in files_to_process:
            say("处理 %s ..." % file_info['name'], "Processing %s ..." % file_info['name'])

            asset_name = file_info["output_path"].name
            game_count, patch_count, deleted_count, merged_count = merge_arc(
                file_info["game_path"],
                file_info["patch_path"],
                file_info["output_path"],
                metadata_path,
                asset_name
            )
            say("  原文件成员：%s" % game_count, "  original members: %s" % game_count)
            say("  补丁成员：%s" % patch_count, "  patch members: %s" % patch_count)
            say("  删除成员：%s" % deleted_count, "  deleted: %s" % deleted_count)
            say("  合并后成员：%s" % merged_count, "  merged: %s" % merged_count)
            say("  完成", "  done")
            print()

        if pack_src.exists():
            deploy_language_pack(pack_src, game_path / "zh-CN")

            # 语言包不会被自动采用：引擎读注册表里的「所选语言」（值名 = 游戏目录）。
            # 值不存在时引擎还会无视环境直接启用唯一那个语言包 ⇒ 英文必须显式写空串，见 tool/language_detect.py。
            stage('默认语言', 'Default in-game language')
            if not IS_WINDOWS:
                say("  [跳过] 非 Windows 平台：无法写注册表",
                    "  [skip] Not Windows: no registry — pick the language in the in-game settings dialog")
                print()
            else:
                _ask_default_language(game_path)

        stage('校验安装文件', 'Verifying installed files')
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
                        say("  [FAIL] %s: 文件不存在" % asset_name,
                            "  [FAIL] %s: file not found" % asset_name)
                        all_verified = False
                        continue

                    file_hash = hashlib.sha256(installed_path.read_bytes()).hexdigest()
                    if file_hash == expected_hash:
                        say("  [OK] %s" % asset_name, "  [OK] %s" % asset_name)
                    else:
                        say("  [FAIL] %s: 哈希不匹配" % asset_name,
                            "  [FAIL] %s: checksum mismatch" % asset_name)
                        say("    预期 %s" % expected_hash, "    expected %s" % expected_hash)
                        say("    实际 %s" % file_hash, "    actual   %s" % file_hash)
                        all_verified = False

                if not all_verified:
                    banner('校验失败：部分文件不正确',
                           'Verification failed: some files are incorrect')
                    say("请按以下步骤恢复后重新安装：",
                        "Please follow these steps to recover, then reinstall:")
                    print()
                    say("  1. 打开 Steam，找到《CROSS†CHANNEL Steam Edition》",
                        "  1. Open Steam and find 'CROSS†CHANNEL Steam Edition'")
                    say("  2. 右键点击游戏 -> 属性 -> 已安装文件 -> 校验游戏文件完整性",
                        "  2. Right-click the game -> Properties -> Installed Files -> Verify integrity")
                    say("  3. 等待 Steam 完成校验与修复",
                        "  3. Wait for Steam to finish verifying and repairing the files")
                    say("  4. 重新运行本安装程序",
                        "  4. Run this installer again")
                    print()
                    return False

            except Exception as e:
                say("  警告：无法校验文件: %s" % e,
                    "  Warning: cannot verify the file: %s" % e)

        banner('安装完成', 'Installation completed')
        say("现在可以启动游戏体验还原内容。",
            "You can now launch the game to experience the restored content.")
        print()

        return True

    except Exception as e:
        banner('安装失败', 'Installation failed')
        say("  错误：%s" % e, "  Error: %s" % e)
        print()
        say("请通过 Steam 校验游戏文件完整性以恢复原始文件。",
            "Please verify the game files via Steam to restore the originals.")
        return False


if __name__ == "__main__":
    try:
        choose_ui_language()
        success = install()
        input(tr("\n按任意键退出...", "\nPress Enter to exit..."))
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print()
        say("安装已取消", "Installation cancelled")
        input(tr("\n按任意键退出...", "\nPress Enter to exit..."))
        sys.exit(1)
    except Exception as e:
        print()
        say("未预期的错误：%s" % e, "Unexpected error: %s" % e)
        import traceback
        traceback.print_exc()
        input(tr("\n按任意键退出...", "\nPress Enter to exit..."))
        sys.exit(1)
