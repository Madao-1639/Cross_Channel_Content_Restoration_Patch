"""两层产物布局：**根目录层**（与语言无关）＋ **语言包层**（`zh-CN/`，只放中文相关）。

## 为什么分两层

AdvHD ≥1.9 有官方语言包机制：游戏目录下以 locale 命名的文件夹（`zh-CN/`）里放归档，
引擎**按成员**优先读包内版本（实测：只含 1 个成员的 `Rio.arc` 也能覆盖），其余成员仍读根目录。
于是把产物切成两层：

    asset/                     根目录层（**游戏目录形态**：复制 asset/* 到游戏目录即完整安装）
      Rio.arc                  ws2 等与语言无关的成员（**不含 .lng / NameTable.txt**）
      Chip2.arc / Voice.arc     含补入的还原 CG 与语音
      zh-CN/                   语言包层（安装后位于 <游戏目录>/zh-CN/）
        Rio.arc                 328 条 .lng + NameTable.txt
        SysGraphic.arc          汉化 UI 图层（只放改动的 pna）
        Fonts.arc               汉字字体 PTF
        Script.arc              中文 Lua 系统界面
        Graphic.arc             中文整屏图
        AdvHDLang.dll           语言包识别标记

## 判定口径

* **语言成员**：`.lng` 与 `NameTable.txt`（换语言只动这两类）；
* 根目录层**不得**出现语言成员（否则英文玩家/未来英化包会看到中文）；
* 语言包层只放"该归档里与语言相关且确有差异"的成员，不整包复制；
* 验收用 `effective()`（**语言包按成员覆盖根目录**）得到"玩家实际读到的视图"。

用法：

    from tool import layers
    layers.base_arc('Rio.arc')        # asset/Rio.arc
    layers.pack_arc('Rio.arc')        # asset/zh-CN/Rio.arc
    layers.read_effective('Rio.arc')  # 合并视图 [(name_bytes, data)]，用于验收/审计
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = ROOT / 'asset'
PACK = BASE / 'zh-CN'

LANG_SUFFIXES = ('.LNG',)
LANG_NAMES = ('NAMETABLE.TXT',)


def is_language_member(name_bytes):
    up = name_bytes.decode('utf-16-le').upper()
    return up.endswith(LANG_SUFFIXES) or up in LANG_NAMES


def base_arc(name):
    return BASE / name


def pack_arc(name):
    return PACK / name


def read_layer(path):
    """读一层归档；文件不存在返回 []。"""
    from tool import arcbuild
    path = Path(path)
    if not path.exists():
        return []
    return arcbuild.read_raw(path)


def write_layer(path, members, expect_count=None):
    """写一层归档（`arcbuild.write_arc` 自带"内容相同则跳过"与原子替换），写完 verify。"""
    from tool import arcbuild
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    arcbuild.write_arc(members, path)
    arcbuild.verify(path, expect_count=expect_count)
    return path


def effective(name):
    """**语言包按成员覆盖根目录**后的成员表（顺序：根目录在前，包内新增追加在后）。"""
    out = list(read_layer(base_arc(name)))
    pack = read_layer(pack_arc(name))
    if not pack:
        return out
    idx = {nb: i for i, (nb, _) in enumerate(out)}
    for nb, data in pack:
        i = idx.get(nb)
        if i is None:
            idx[nb] = len(out)
            out.append((nb, data))
        else:
            out[i] = (nb, data)
    return out


def effective_dict(name):
    return {nb.decode('utf-16-le').upper(): d for nb, d in effective(name)}
