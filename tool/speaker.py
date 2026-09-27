"""角色名称映射表（`resource/speaker_map.json`）的读取层。

**这是角色名的唯一来源**：名字框显示文本（`Rio.arc` 的 `NameTable.txt`）、转换器的
说话人映射、语音通道、立绘前缀全部从这里取。原先这些映射散在
`tool/wsc2ws2.py` 的 `SPEAKER_MAP` / `VOICE_CHANNEL`、`script/rename_speakers.py`（已删）的
`SPEAKER_ZH`、`tool/writer.py` 的 `SPK2LC` 与
`doc/localization.md` 的两张表里，彼此已经漂移（35 处中文值不一致）。

表的形状与判据见 `resource/README.md`。要点：

    en            Steam ws2 脚本里的 `%LC` 标记（= 键）。**单值映射** —— 原版多个
                  说话人标记合并到同一个 `%LC` 时只能显示一个中文，合并处记在 note 里。
    zh            名字框显示文本
    ja            原版 WSC 的 0x42 说话人标记（**列表**，因为 `太一` 与 `俺` 都是 `%LCTaichi`）
    voice_prefix  原版语音文件名前缀（大写）
    channel       Steam `2e` 指令的通道名
    pna_prefix    立绘文件名前四字符

表里还保留了 4 条 `%LC` 键在 Steam 脚本里**从未出现**的记录（`Masamune`/`Yutaka`/
`Woman`/`Kiri/Taichi`）—— 它们只服务转换器（原版 WSC → ws2 的说话人映射），删掉会让
转换器对原版语料里那些说话人失去映射。所以核对方向是**单向**的：
**脚本里出现的每个 `%LC` 键都必须能在表里查到**（`missing_keys()` 就是查这个）。

用法：

    from tool import speaker
    speaker.ja_to_en()        # {'太一': 'Taichi', '俺': 'Taichi', ...}
    speaker.en_to_zh()        # {'Touko': '冬子', ...}（NameTable.txt 的内容）
    speaker.zh_to_en()        # 中文/日文字形 → en，供从 CCS `[说话人]` 前缀反查
    speaker.voice_channel()   # {'FYU': 'charTOU', ...}
"""
import json
from pathlib import Path

# 表的位置：仓库根的 resource/ 下。允许调用方覆盖（测试或换表）。
MAP_PATH = Path(__file__).resolve().parent.parent / 'resource' / 'speaker_map.json'

_CACHE = {}


def load(path=None):
    """读表，返回记录列表（按出现频次降序）。缺表即中止 —— 本表不自带兜底。"""
    key = str(path or MAP_PATH)
    if key not in _CACHE:
        p = Path(key)
        if not p.exists():
            raise SystemExit('缺少 %s —— 角色名称映射表（见 resource/README.md）' % p)
        _CACHE[key] = json.loads(p.read_text(encoding='utf-8'))['speakers']
    return _CACHE[key]


def by_en(path=None):
    return {r['en']: r for r in load(path)}


def en_to_zh(path=None):
    """`%LC` 英文名 -> 名字框显示文本。写 `NameTable.txt` 用的就是这一份。"""
    return {r['en']: r['zh'] for r in load(path)}


def ja_to_en(path=None):
    """原版 0x42 说话人标记 -> `%LC` 英文名（转换器用的那条）。"""
    out = {}
    for r in load(path):
        for ja in r['ja']:
            out.setdefault(ja, r['en'])
    return out


def zh_to_en(path=None):
    """中文/日文字形 -> `%LC` 英文名。

    键同时收 `zh` 与 `ja` 两种字形：汉化组 CCS 的 `[说话人]` 前缀用的是**繁体/日文字形**
    （`見里`/`霧`/`遊紗`），而显示文本是简体（`见里`/`雾`/`游纱`），查表时两种都要认。
    """
    out = {}
    for r in load(path):
        out.setdefault(r['zh'], r['en'])
        for ja in r['ja']:
            out.setdefault(ja, r['en'])
    return out


def voice_channel(path=None):
    """原版语音名前缀（大写）-> Steam 通道名。表里没有的就是查不到（调用方自行回退）。

    `voice_prefix` 允许是**列表**：一个说话人可能有多个前缀 ——
    原版引擎按「配音来源」分槽，同一槽的多个前缀在 Steam 侧共用一个通道
    （如 `声` 的 `GKA/GKB/GKC`，见 `resource/speaker_map.json`）。
    """
    out = {}
    for r in load(path):
        ch = r.get('channel')
        vp = r.get('voice_prefix')
        if not ch or not vp:
            continue
        for p in (vp if isinstance(vp, list) else [vp]):
            out[p] = ch
    return out


def pna_prefix(path=None):
    """`%LC` 英文名 -> 立绘 PNA 前四字符。"""
    return {r['en']: r['pna_prefix'] for r in load(path) if r.get('pna_prefix')}


def missing_keys(keys, path=None):
    """返回 `keys` 里**查不到映射**的那些（单向完备性核对）。

    核对方向见模块 docstring：表允许有多余记录（转换器需要的原版标记），
    但不允许脚本里出现的键查不到 —— 那会让名字框回落成显示英文原名。
    """
    known = set(en_to_zh(path))
    return sorted(set(keys) - known)
