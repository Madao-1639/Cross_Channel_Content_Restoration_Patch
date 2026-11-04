# -*- coding: utf-8 -*-
"""把子集 TTF 的 name 表恢复为**思源本名**（撤销历史上的「伪装成原版族名」）。

为什么用本名（而不是伪装成 `FOT-マティス Pro`）：官方 AdvHD 语言包的做法是**语言包字体用独立名字**
（IMHHW 的 `zh-CN/Fonts.arc` = `NotoSansCJKsc-Regular.PTF`，其 `LegacyGame_utf8.lua` 的
`g_fonts2.name` = `Noto Sans CJK SC`）。本补丁同理：`g_fonts`/`g_fonts2` 请求
`Source Han Sans SC Medium/Bold`，字体内部族名报同样的名字，三者自洽：

    Lua 的 name（GDI 选面） == PTF 内部 id1 族名

PTF **成员名**仍沿用原版 `FOT-MatissePro-M/B.PTF` —— 那是 Lua 的 `file` 字段要查的 `Fonts.arc` 成员。
（伪装族名的老方案会让「我们的 Lua + 原版字体」混层时互相抢同一个族名，已废弃。）

需要 fontTools（EDA 环境）：
    mamba run -n EDA python script/fix_font_names.py
"""
import os
import sys

from fontTools.ttLib import TTFont

sys.stdout.reconfigure(encoding='utf-8')

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUB_DIR = os.path.join(_ROOT, 'resource', 'fonts', 'sourcehan_subset')

# 每个子集的「本名」：id1 族名（GDI 选面用）/ id6 PostScript 名 / id16 印刷族名 / id17 字重
FACES = {
    'SourceHanSansSC-Medium.ttf': {
        'family': 'Source Han Sans SC Medium', 'sub': 'Regular',
        'ps': 'SourceHanSansSC-Medium', 'typo_family': 'Source Han Sans SC', 'typo_sub': 'Medium',
    },
    'SourceHanSansSC-Bold.ttf': {
        'family': 'Source Han Sans SC Bold', 'sub': 'Regular',
        'ps': 'SourceHanSansSC-Bold', 'typo_family': 'Source Han Sans SC', 'typo_sub': 'Bold',
    },
}

COMMON = {
    0: 'Copyright 2014-2021 Adobe (http://www.adobe.com/), with Reserved Font Name "Source".',
    7: 'Source is a trademark of Adobe Systems Incorporated.',
    8: 'Adobe',
    9: 'Adobe',
    11: 'https://github.com/adobe-fonts/source-han-sans',
    12: 'https://github.com/adobe-fonts/source-han-sans',
    13: 'This Font Software is licensed under the SIL Open Font License, Version 1.1.',
    14: 'https://scripts.sil.org/OFL',
}


def face_names(spec):
    out = dict(COMMON)
    out[1] = spec['family']
    out[2] = spec['sub']
    out[3] = 'Subset of %s for the CROSS†CHANNEL Chinese patch' % spec['ps']
    out[4] = spec['family']
    out[5] = 'Version (subset)'
    out[6] = spec['ps']
    out[16] = spec['typo_family']
    out[17] = spec['typo_sub']
    out[20] = spec['ps']
    return out


def fix(path, spec):
    f = TTFont(path)
    want = face_names(spec)
    kept = 0
    for r in f['name'].names:
        nid = r.nameID
        if nid in want:
            r.string = want[nid]
            kept += 1
        else:
            # 原版 Fontworks 的版权/商标/版本/URL 记录一律清空（不能留别人的元数据）
            r.string = ''
    # 空字符串会让某些解析器出错：直接删掉这些记录
    f['name'].names = [r for r in f['name'].names if r.string]
    f.save(path)
    return kept, len(f['name'].names)


def main():
    for fn, spec in FACES.items():
        path = os.path.join(SUB_DIR, fn)
        if not os.path.exists(path):
            print('[skip] %s 不存在' % path)
            continue
        kept, total = fix(path, spec)
        print('%-32s 改写 %2d 条，保留 %2d 条 name 记录 → %s' % (fn, kept, total, spec['family']))
    return 0


if __name__ == '__main__':
    sys.exit(main())
