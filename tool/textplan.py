# -*- coding: utf-8 -*-
"""把 `resource/text_map.json` 展开成**写盘计划**：每一格显示什么 + 结构增删。

**库**：被 `script/apply_text_map.py`（写盘器）与 `script/gen/build_voice_plan.py`（补挂语音计划）
调用。原与写盘器同处一个文件 —— 抽出来是为了让写盘器只剩「装配 + 落盘 + 回读校验」，
也让「规划」能被别的手工工具复用而不必 import 一个流水线步骤。

## 槽位编号（全项目同一口径）

`k` = 池槽位：`14` 一格、**`0f` 的每个条目也各占一格**。只数 `14` 会让所有含选项的脚本
整体错位 —— 选项之后的每一格都取到别格的英文，尾标记随之错配。

## 每格英文从哪来

`en_of_from(ins)` **直接从输入归档的指令流取**，不读任何派生缓存。旧实现读
`tmp/textqa/slots/*.jsonl`：缓存路径失效后会**静默返回空**，`fix_tail(zh, '')` 便什么都不补
⇒ 整份产物的 `%K`/`%P` 静默丢失（引擎不再等待点击、一屏堆满对话），而当时所有闸照过。
"""
import json
import re
from pathlib import Path

from tool import lng as lngmod, writer
from tool.lng import parse_ccs_both

ROOT = Path(__file__).resolve().parent.parent

TM = json.loads((ROOT / 'resource' / 'text_map.json').read_text(encoding='utf-8'))['scripts']
# 「覆盖」类处置的名字框同步 / 借用语音删除（见 resource/text_map_vc.json 的说明）
VCF = ROOT / 'resource' / 'text_map_vc.json'
VC = json.loads(VCF.read_text(encoding='utf-8')) if VCF.exists() else {}
# 原版**每行的演出属性**（语音 / SE）—— 只含行级演出，立绘/BGM/计时器等跟随 Steam。
# 生成：`script/gen/build_original_audio.py`；说明见该文件。
OAF = ROOT / 'resource' / 'original_audio.json'
OA = json.loads(OAF.read_text(encoding='utf-8')) if OAF.exists() else {}
# 插入行**随行的原版画面演出**（立绘 / 背景 / CG）—— 由 `script/build_original_stage.py`
# 用**与就地插入同一套转换器**转出（坐标适配 + 资源改名一致）；见该文件头。
# 用途：文本级插入原先只带名字框/正文/语音，原版随行的演出没带 ⇒「有台词、画面却是空屏或别人」。
OSF = ROOT / 'resource' / 'original_stage.json'
OS = json.loads(OSF.read_text(encoding='utf-8')) if OSF.exists() else {}
# 「按格补演出」的**覆盖层**（洞 2）= `{脚本: {槽位k: 原版源行}}`：台词取自原版、但 Steam 骨架
# 没把说话人立绘摆上屏的格。见 resource/text_map_stage.json 的说明。
_STG = ROOT / 'resource' / 'text_map_stage.json'
STAGEMAP = json.loads(_STG.read_text(encoding='utf-8')) if _STG.exists() else {}
# 补挂语音（「原版该行有配音、产物却没挂」）—— 由 `script/gen/build_voice_plan.py` 生成
# （时长指纹找候选 + **台词核对**定案；键 = 表的 `k` = `rebuild` 的 `si`）。
VPF = ROOT / 'resource' / 'voice_plan.json'
VP = json.loads(VPF.read_text(encoding='utf-8')) if VPF.exists() else {}
CCS_DIR = ROOT.parent / 'cross-channel_chinese-localization_project' / 'Scripts' / '20150412'


# 尾部控制符 = `%K`/`%P`/`%N` 的任意组合。**仅作结构断言用**（见 `check_tails`）：
# 它不决定该补什么尾巴，只断言"对话格必须有尾、选项格必须没有"。
TAIL = re.compile(r'(?:%[A-Za-z])+$')

def en_of_from(ins):
    """**每格（池槽位）的英文** —— 直接从**输入归档的指令流**取。

    ⚠️ **槽位编号必须与写盘器同口径**：`14` 一格、**每个 `0f` 条目也各占一格**
    （与写盘器 `build_lng` 的槽位序一致）。只数 `14` 会让**所有含选项的
    脚本整体错位** —— 选项之后的每一格都取到别格的英文（实测 `CCA0006_EN`：选项格取到
    相邻的 `%P`、末尾三格取不到），尾标记随之错配。

    ⚠️ 旧实现读 `tmp/textqa/slots/<stem>.jsonl`（**派生缓存**）。那份缓存被清掉后，`en_of`
    静默返回空，于是 `fix_tail(zh, '')` 补不上尾标记 ⇒ **整份产物的 `%K`/`%P` 静默丢失**
    （引擎不等待点击），而所有闸都照过。缓存只是 `asset/Rio.arc` 的派生，直接读归档即可，
    且不再有"缓存缺失"这条隐藏前提。
    """
    out = {}
    k = -1
    for x in ins:
        if x.opcode == 0x14:
            k += 1
            out[k] = {'k': k, 'ws2_text': x.fields.get('text', '')}
        elif x.opcode == 0x0f:
            for e in x.fields['entries']:
                k += 1
                out[k] = {'k': k, 'ws2_text': e['text']}
    return out

def cur_voice(ins, drops):
    """槽位序（与 `rebuild` 的 `si` 同口径）→ 该格紧邻其前的 `2e` 文件名。

    `2e` 与 `15` 同一套归属：属于其**后**出现的那一格。
    """
    out, pend, si = {}, None, 0
    for i in ins:
        if i.opcode == 0x2e:
            pend = i.fields.get('file')
        elif i.opcode == 0x14:
            if si not in drops:
                out[si] = pend
            pend, si = None, si + 1
        elif i.opcode == 0x0f:
            for _ in i.fields['entries']:
                if si not in drops:
                    out[si] = pend
                pend, si = None, si + 1
    return out

def expand_script(sc, en_of):
    """→ (texts{槽位: 文本}, inserts{at_k: [...]}, drops{槽位}, names{槽位: 名字框},
         rows{槽位: 源行})

    **`names`：名字框随台词来源**（2026-09-20）—— 「台词与角色名一致」不再靠人工补表：
      - `op=ccs` ⇒ 名字框 = **该源行 `[話者]`**（经 `speaker_map` → `%LC<英文名>`）；
        源行是**旁白**（无 `[話者]`）⇒ **清空**（`''`）；
        源行是复合/集体（`霧・太一`/`二人`/`三人`）⇒ 映射表原样给**集体名**（`Both`/`All 3`…）。
      - `op=text` / `ctrl` / `opt`（自译、Steam 自有）⇒ **不写**，保持 Steam 的名字框。
      - `op=insert` ⇒ 由 `rebuild` 按源行说话人另发 `15`（`plan` 里已带），此处不重复。
    键与 `texts` 同一空间（表的 `k` = 写盘器 `rebuild` 的 `si`）。
    `rows` 供上层对照「原版这一行的音频属性」（`resource/original_audio.json`）。
    """
    _jp, _zh = parse_ccs_both(CCS_DIR / (sc['ccs'] + '.CCS'))
    src = writer.src_lines(sc['ccs'])                 # 源 WSC 的日文正文（`14` 的 text 用它，哨兵据此核对）
    svc = writer.src_voices(sc['ccs'])                # 源 WSC 的原版录音名（插入行照原生布局挂 `2e`）
    zh = {n: lngmod.strip_speaker_only(v) for n, v in _zh.items()}
    spk = {n: lngmod.has_speaker_prefix(v) for n, v in _zh.items()}   # span 的标定基准
    texts, inserts, drops, names, rows, stages = {}, {}, set(), {}, {}, {}
    for it in sc['items']:
        op = it['op']
        if op == 'insert':
            a, b = it['src_rows']
            # ⚠️ 2026-09-19 修：插入格的 lng **必须补尾**（`%K`/`%P`/`%N`）—— 源 CCS 的中文列
            # **不带**控制符，直接回填会让引擎不等待点击，一屏堆 10–24 句（第 8 轮报告 ① 的
            # 「211/211 裸尾」）。尾部取自**源 WSC 日文**（它带 `%K%P`），与 `op=ccs` 同一套归一化。
            for n in range(a, b + 1):
                ja = src.get(n, _jp.get(n, ''))
                zh_n = lngmod.normalize_zh(lngmod.fix_tail(zh.get(n, ''), ja), ja)
                inserts.setdefault(it['at_k'], []).append(
                    (writer.spk_lc(_jp.get(n, '')), ja, zh_n, svc.get(n, '')))
                # 该行随行的原版画面演出（可能为空 —— 原版这行本就没有）
                stages.setdefault(it['at_k'], []).append(
                    bytes.fromhex(OS.get(sc['ccs'], {}).get(str(n), '')))
            continue
        if op == 'split':
            # **一格拆多格**（2026-09-27 新增）：源行 `row` 的正文按 `at` 的**字符位**切成
            # `len(at)+1` 格。第 1 片替换输入格 `k` 本身；其余各片作为「插入格」紧随其后
            # —— 与 `insert` 同一套机关，故**无独立语音、无独立随行演出**（是同一句的续页）。
            # 尾标记按源行补（`fix_tail` 同一口径）⇒ 各片都带 `%K%P`。
            # 用途：把「一句话放不进一个文本框」的排版拆格**变成表能表达的结构**，
            #      从而不再依赖基线里"拆格已完成"的旧状态。
            k0, row = it['k'], it['row']
            ja = src.get(row, _jp.get(row, ''))
            nb = writer.spk_lc(_jp.get(row, ''))
            base = lngmod.normalize_zh(zh.get(row, ''), en_of.get(k0, {}).get('ws2_text', ''))
            prev, pieces = 0, []
            for c in list(it['at']) + [len(base) + 1]:
                pieces.append(lngmod.fix_tail(
                    lngmod.slice_span(base, prev, c, spk.get(row, False)), ja))
                prev = c
            texts[k0] = pieces[0]
            names[k0] = nb
            rows[k0] = row
            for seg in pieces[1:]:
                inserts.setdefault(k0, []).append((nb, ja, seg, ''))
            continue
        if op == 'drop':
            drops.add(it['k'])
            continue
        ks = it['k'] if isinstance(it['k'], list) else [it['k'], it['k']]
        en = en_of.get(ks[0], {}).get('ws2_text', '')
        for k in range(ks[0], ks[1] + 1):
            e = en_of.get(k, {}).get('ws2_text', '')
            if op == 'ccs':
                row = it['row'] if 'row' in it else it['row0'] + (k - ks[0])
                t = lngmod.normalize_zh(zh.get(row, ''), e)
                if 'span' in it:
                    sp = it['span']
                    t = lngmod.slice_span(t, sp[0], sp[1], spk.get(row, False))
                t = lngmod.fix_tail(t, e)
                # 名字框随源行（`_jp` 是 CCS 日文列 = 带 `[話者]`；无 `[話者]` ⇒ 清框）
                names[k] = writer.spk_lc(_jp.get(row, ''))
                rows[k] = row
            else:                              # text / ctrl / opt
                t = lngmod.normalize_zh(lngmod.fix_tail(it.get('zh'), e), e)
            if it.get('tail'):
                # **人工追加的尾部标记**（排版用，如补一次换页 `%P`）——
                # 中文比英文长时，Steam 骨架那种「连续一屏不清框」的排版会装不下，
                # 后面几句整段显示不出来；在该格尾补 `%P` 就把它断成两页。不改槽位、不改格数。
                t += it['tail']
            texts[k] = t
    return texts, inserts, drops, names, rows, stages

def build_lng(ins, plan, drops, texts):
    """按**最终槽位序**逐个取文本 → `(newL, kinds)`。

    `kinds[i]` 与 `newL[i]` 等长，取值 `'dlg'`（`14` 格）或 `'opt'`（`0f` 条目）——
    供 `check_tails` 做结构断言。**种类必须照抄写盘器的发射顺序**：`plan` 里的插入格
    全部是**对话格**（`split` 的续页与 `insert` 的还原行都是），即使它紧跟在一个选项格之后。
    """
    newL, kinds, si = [], [], 0

    def push(t, kind):
        newL.append(t)
        kinds.append(kind)

    for _p, _j, zt, *_v in plan.get(-1, []):      # 「补在脚本最前」的那几行，排在最前
        push(zt, 'dlg')
    for i in ins:
        if i.opcode == 0x14:
            if si not in drops:
                push(texts.get(si, ''), 'dlg')
            for _p, _j, zt, *_v in plan.get(si, []):
                push(zt, 'dlg')
            si += 1
        elif i.opcode == 0x0f:
            for _ in i.fields['entries']:
                if si not in drops:
                    push(texts.get(si, ''), 'opt')
                for _p, _j, zt, *_v in plan.get(si, []):
                    push(zt, 'dlg')
                si += 1
    return newL, kinds

def check_tails(stem, newL, kinds):
    """**结构断言**：对话格必须带尾部控制符，选项格必须不带（不符即中止，不写盘）。

    这条是全库**唯一**能独立证伪「尾标记整体丢失」的判据，且不依赖任何外部参照：
    两版语料在这条上都**零例外** —— Steam 原档 51,452 个对话格无一带尾缺失、
    127 个选项条目无一带尾；源 WSC 41,629 行也无一缺尾。
    ⚠️ 它必须存在的原因（2026-09-27 事故）：每格英文取自**派生缓存**，缓存路径失效后
    `fix_tail(zh, '')` 静默什么都不补 ⇒ 39,113 个对话格裸尾、产物静默走样（引擎不再等待
    点击，一屏堆满对话），而当时流水线**所有闸照过**。
    """
    bare = [i for i, (t, kd) in enumerate(zip(newL, kinds))
            if kd == 'dlg' and not TAIL.search(t)]
    tailed = [i for i, (t, kd) in enumerate(zip(newL, kinds))
              if kd == 'opt' and TAIL.search(t)]
    if bare or tailed:
        raise SystemExit(
            '[中止] %s：尾部控制符不成立 —— 对话格缺尾 %d 处 %s；选项格带尾 %d 处 %s\n'
            '  对话格必须带 `%%K`/`%%P`/`%%N`（否则引擎不等待点击，一屏堆多句）；选项格必须不带。\n'
            '  最常见成因：**该格英文取不到** —— `fix_tail(zh, \'\')` 便什么都不补。'
            % (stem, len(bare), bare[:10], len(tailed), tailed[:10]))
