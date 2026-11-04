# -*- coding: utf-8 -*-
"""首次安装时的**默认语言判定**（Steam 客户端语言 / Windows 界面语言，任一为中文即中文）。

为什么要在安装器侧判定：引擎只认注册表 `HKCU\\Software\\WillPlus\\AdvHD\\LanguageSettings`
（值名 = 游戏目录经系统 ANSI 码页转换后的串，值 = 语言包目录名如 `zh-CN`，**空串 = 英文**），
它**看不到 Steam**；而且注册表值不存在时，`WinMain` 的 LABEL_13 分支会在"恰好一个语言包目录"
的情况下**无视语言环境直接启用语言包**（`dword_58F028` 置为 `<游戏目录>\\zh-CN`）。
⇒ "默认语言跟随玩家环境"只能由安装器决定：按本模块的判定写 `zh-CN`，否则**显式写空串**
（空串让引擎停在原版英文，也顺带挡住上面那条自动启用）。

判定信号（**任一为中文即默认中文**）：

| 信号 | 位置 |
|---|---|
| Steam 客户端界面语言 | `HKCU\\Software\\Valve\\Steam` 的 `Language` |
| Windows 界面语言 | `GetUserDefaultLocaleNameW()` |

**为什么不看 Steam 为本作选的语言**（`appmanifest_812560.acf` 的 `UserConfig.language`）：
Steam 版本作在 Steam 上只声明了英文 ⇒ 无论客户端/系统是什么语言，Steam 都给该作写 `english`
（本机实测：客户端 `schinese` + 系统 `zh-CN`，appmanifest 仍是 `english`）。采信它等于所有人默认英文。

映射：`schinese`/`tchinese`/`zh*` → `zh-CN`（本补丁只提供简体译文，繁中玩家也读得懂，已确认如此），
其余语言 → `''`（英文）。
"""

import os
import sys

LANG_DIR = 'zh-CN'
LANG_EN = ''

IS_WINDOWS = sys.platform.startswith("win") and os.name == "nt"

#: 语言标记（小写）→ 本项目语言包目录名。其余语言 = 英文（语言包不生效）。
STEAM_LANGUAGES = {
    'schinese': LANG_DIR,
    'tchinese': LANG_DIR,     # 只提供简体译文；繁中玩家默认简体比英文合用
}


def normalize(token):
    """语言标记 → 语言包目录名（`'zh-CN'` 或 `''`）。无法识别一律按英文。"""
    if not token:
        return LANG_EN
    t = str(token).strip().lower()
    if not t:
        return LANG_EN
    if t in STEAM_LANGUAGES:
        return LANG_DIR
    if t.startswith('zh'):            # zh-CN / zh-TW / zh-Hans-CN / zh-Hant …
        return LANG_DIR
    return LANG_EN


def steam_client_language():
    """Steam 客户端界面语言（`HKCU\\Software\\Valve\\Steam` 的 `Language`）。→ (值, 出处)"""
    if not IS_WINDOWS:
        return None, '非 Windows，无数值'
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Valve\Steam') as k:
            v, _ = winreg.QueryValueEx(k, 'Language')
        return (v or None), 'HKCU\\Software\\Valve\\Steam\\Language'
    except Exception as e:                                  # noqa: BLE001 - 读不到就回退
        return None, '读不到（%s）' % e


def windows_ui_language():
    """Windows 界面语言（BCP-47 名，如 `zh-CN`）。→ (值, 出处)

    两条路：`GetUserDefaultLocaleName()` 优先；取不到（冻结成单文件 exe 后实测曾返回 0）
    就退回 `GetUserDefaultUILanguage()` + `GetLocaleInfoW(LOCALE_SNAME)` —— **不要只留一条**，
    否则安装器在打包形态下会静默退化成只看 Steam 语言。
    """
    if not IS_WINDOWS:
        return None, '非 Windows，无数值'
    import ctypes

    reason = None
    try:
        k = ctypes.WinDLL('kernel32', use_last_error=True)
        k.GetUserDefaultLocaleName.argtypes = [ctypes.c_wchar_p, ctypes.c_int]
        k.GetUserDefaultLocaleName.restype = ctypes.c_int
        buf = ctypes.create_unicode_buffer(85)          # LOCALE_NAME_MAX_LENGTH
        if k.GetUserDefaultLocaleName(buf, 85):
            return buf.value, 'GetUserDefaultLocaleName()'
        reason = 'GetUserDefaultLocaleName 返回 0（err %s）' % ctypes.get_last_error()
    except Exception as e:                              # noqa: BLE001
        reason = e

    try:                                                # 兜底：UI LCID → LOCALE_SNAME
        k = ctypes.WinDLL('kernel32', use_last_error=True)
        buf = ctypes.create_unicode_buffer(85)
        if k.GetLocaleInfoW(k.GetUserDefaultUILanguage(), 0x5C, buf, 85):
            return buf.value, 'GetUserDefaultUILanguage()+GetLocaleInfoW(LOCALE_SNAME)'
    except Exception as e:                              # noqa: BLE001
        reason = '%s / %s' % (reason, e)
    return None, '读不到（%s）' % reason


#: 判定信号的**标识**（文案不进这里：显示名由调用方按安装器界面语言取，见 `probe_label`）。
PROBE_LABELS = {
    'steam_client': ('Steam 客户端语言', 'Steam client language'),
    'windows_ui': ('Windows 界面语言', 'Windows UI language'),
}


def probe_label(key, lang='zh'):
    """信号标识 → 显示名（`lang='zh'|"en"`）。"""
    zh, en = PROBE_LABELS[key]
    return zh if lang == 'zh' else en


def detect_default_language():
    """→ `(语言包目录名或 '' , [(信号标识, 原始值, 出处, 该信号的判定), …])`（"任一为中文即中文"）。"""
    probes = (
        ('steam_client', steam_client_language),
        ('windows_ui', windows_ui_language),
    )
    seen, chosen = [], LANG_EN
    for key, fn in probes:
        raw, where = fn()
        lang = normalize(raw)
        seen.append((key, raw, where, lang))
        if lang:
            chosen = LANG_DIR
    return chosen, seen


if __name__ == '__main__':
    lang, seen = detect_default_language()
    for key, raw, where, verdict in seen:
        print('  %-24s %-12r %-45s → %s'
              % (probe_label(key), raw, where, verdict or 'en'))
    print('=> 默认语言 / default language: %s' % (lang or 'English'))
