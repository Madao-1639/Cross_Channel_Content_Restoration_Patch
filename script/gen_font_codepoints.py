# -*- coding: utf-8 -*-
"""全**离线**码点清单生成（给字体子集化做输入）。

## 「部署语料**会**被**引擎渲染**的**全部汉字输入**」的**扫描完备口径**

本补丁的字体要覆盖**一切会显示给玩家**的 Chinese 字符，扫描源 = **所有**真正
进入渲染层的**汉字输入**（共 **7** 个独立源，按流水线分工 chained）：

  1. **`resource/text_map*.json` 的 `zh` 字段**（剧本中文准据；`apply_text_map.py` 的唯一输入）
  2. **上游 CCS `*.CCS`（MewX 中文化项目）只取 `>1` 中文列**（text_map 的
     `op:ccs`+`row` 指向 CCS 行的**源文本**，pipeline 写盘时**实际读到的内容**）
  3. **`resource/carried_lng/*.lng`**（35 个接手 lng，`build_patch.py carry_carried_lng()` 白名单直接带入 Rio.arc）
  4. **`resource/speaker_map.json`**（`build_nametable.py` 的唯一来源：`zh`/`ja`/`en`/`channel`/`pna_prefix`/
     `_note` 全字列——名字框实际渲染文本 + voice/PNA 路径名）
  5. **`resource/other_texts/Script_lua_strings.txt`**（Script.arc 全部 14 个 Lua 常量，系统 UI）
  7. **`resource/typo_overrides.json`**（**替换后的目标字**也在字体里会显示，纳入码点）

**{第 2 源和第 5 源之间的 choice}**： 日文列（`>0`）和「非渲染的 Rio ws2 路径池」
**不 scan**—— 日文列不进游戏、ws2 paths 不是显示层。

**结构性兜底**（**不依赖以上**任何扫描）：
  * GB2312 全表 6,763 汉字 + 常用符号
  * ASCII 可打印 + CJK 标点 + 全角 + 假名 + 通用标点/字母数字/符号块

## 输出
`resource/fonts/sourcehan_subset/codepoints.txt`（每行一个 hex codepoint），后续
`script/subset_sourcehan.py` 读它做裁剪，`script/build_fonts_arc.py` 打包 PTF。
"""
import json
import os
import pathlib
import re
import sys

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tool import lng as lng_mod
import importlib.util as _util

_spec = _util.spec_from_file_location('lc53p', 'tool/luac53.py')
m53 = _util.module_from_spec(_spec)
_spec.loader.exec_module(m53)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CCS_DIR = (
    os.path.join(ROOT, 'resource', 'corpus', 'ccs'))
TEXT_DIR = os.path.join(ROOT, 'resource', 'other_texts')
SUBSET_DIR = os.path.join(ROOT, 'resource', 'fonts', 'sourcehan_subset')

codepoints = set()

# 1) text_map 系列（剧本中文准据）
for _fn in ('text_map.json', 'text_map_stage.json'):
    _p = os.path.join(ROOT, 'resource', _fn)
    if not os.path.exists(_p):
        continue
    with open(_p, encoding='utf-8') as fh:
        tm = json.load(fh)

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if (k == 'zh' or k == 'names') and isinstance(v, str):
                    codepoints.update(ord(ch) for ch in v)
                else:
                    walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(tm)
print('after text_map:', len(codepoints))

# 2) 上游 CCS（只取 >1 中文列）
for _fn in sorted(os.listdir(str(CCS_DIR))):
    if not _fn.upper().endswith('.CCS'):
        continue
    try:
        _content = (CCS_DIR / _fn).read_text(encoding='utf-16le')
    except Exception:
        continue
    for _line in _content.splitlines():
        _s = _line.strip()
        _m = re.match(r'^>1.([0-9]+).(.*)$', _s)
        if not _m:
            continue
        for _ch in _m.group(2):
            if _ch and not _ch.isspace():
                codepoints.add(ord(_ch))
print('after CCS corpus (zh only):', len(codepoints))

# 3) carried_lng
_LNG_DIR = os.path.join(ROOT, 'resource', 'carried_lng')
if os.path.isdir(_LNG_DIR):
    for _fn in sorted(os.listdir(_LNG_DIR)):
        if not _fn.lower().endswith('.lng'):
            continue
        _p = os.path.join(_LNG_DIR, _fn)
        try:
            with open(_p, 'rb') as fh:
                raw = fh.read()
            for text in lng_mod.parse_lng(raw):
                codepoints.update(ord(ch) for ch in text if not ch.isspace())
        except Exception:
            pass
print('after carried_lng:', len(codepoints))

# 4) speaker_map.json（全部 list/str 内容，含 ja 原版名）
_DERIVED_FILES = ('resource/speaker_map.json',)
for _fn in _DERIVED_FILES:
    _p = os.path.join(ROOT, _fn)
    if not os.path.exists(_p):
        continue
    with open(_p, encoding='utf-8') as fh:
        obj = json.load(fh)

    def walk_all(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k == '_说明' or '说明' in k:
                    continue   # 免掉说明文字（通常与渲染无关）
                walk_all(v)
        elif isinstance(o, list):
            for v in o:
                walk_all(v)
        elif isinstance(o, str):
            if o:
                codepoints.update(ord(ch) for ch in o if not ch.isspace())
    walk_all(obj)
print('after speaker_map + typo_overrides:', len(codepoints))

# 5) other_texts 离线 dump
for _fn in ('Script_lua_strings.txt',):
    _p = os.path.join(TEXT_DIR, _fn)
    if not os.path.exists(_p):
        continue
    with open(_p, encoding='utf-8') as fh:
        codepoints.update(ord(ch) for ch in fh.read() if not ch.isspace())
print('after dumps:', len(codepoints))

# 6) GB2312
for hi in range(0xA1, 0xF8):
    for lo in range(0xA1, 0xFF):
        try:
            codepoints.add(ord(bytes([hi, lo]).decode('gb2312')))
        except UnicodeDecodeError:
            pass
print('after GB2312:', len(codepoints))

# 7) ASCII / CJK 标点 / 全角 / 假名 / 通用标点
codepoints.update(range(0x20, 0x7F))
codepoints.update(range(0x3000, 0x3040))
codepoints.update(range(0x3040, 0x3100))
codepoints.update(range(0xFF00, 0xFFF0))
codepoints.update(range(0x2000, 0x2070))
codepoints.update(range(0x2100, 0x2900))
codepoints.add(0x2026)
codepoints.add(0x2014)
codepoints.add(0x201C)
codepoints.add(0x201D)
print('final:', len(codepoints))

os.makedirs(SUBSET_DIR, exist_ok=True)
_out = os.path.join(SUBSET_DIR, 'codepoints.txt')
NEWLINE = chr(10)
with open(_out, 'w', encoding='utf-8', newline=NEWLINE) as fh:
    for cp in sorted(codepoints):
        fh.write('%04X' % cp)
        fh.write(NEWLINE)
print('written', _out)
