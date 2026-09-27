# -*- coding: utf-8 -*-
"""按 `resource/text_map.json` 落盘：**结构（insert / drop）与 lng 一次产出**。

取代 `insert_deleted_dialogues.py`（结构）与 `realign_lng_to_ws2.py` 的 lng 生成职责 ——
后者按「源 CCS 对齐」生成 lng，而 **表才是准据**（表的 `n_final` 就是最终槽位布局）。
两趟各自生成会静默不一致，所以这里一次产出。

原理：
  1. **原始槽位 `k`** = asset 的 ws2 里 `14` 与 `0f` 条目按指令序编号（`14` 一格、每个选项条目一格）；
  2. 表的 `items` 给出「每一格显示什么」+ 还原插入（`at_k`/`src_rows`）+ 删格（`drop`）；
  3. **结构**：在 `at_k` 之后插 `15`+`14`（复用 `insert_deleted_dialogues.rebuild`，含文件内跳转回写）、
     把 `drop` 的 `14` 去掉；
  4. **lng**：按**最终槽位序**逐个取文本（`ccs` 取源 CCS 该行 / `text`·`ctrl`·`opt` 直接给 /
     插入格取源 CCS 该行）。

不变式（写盘后逐脚本断言）：`lng 条数 == 14 条数 + Σ(0f 条目数)`。
用法：`python script/apply_text_map.py [--write]`
"""
import argparse
import collections
import importlib.util
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tool import arcbuild, lng as lngmod, ws2, ws2disasm          # noqa: E402
from tool.lng import parse_ccs_both                            # noqa: E402

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

TM = json.loads((ROOT / 'resource' / 'text_map.json').read_text(encoding='utf-8'))['scripts']
# 「覆盖」类处置的名字框同步 / 借用语音删除（见 resource/text_map_vc.json 的说明）
VCF = ROOT / 'resource' / 'text_map_vc.json'
VC = json.loads(VCF.read_text(encoding='utf-8')) if VCF.exists() else {}
# 原版**每行的演出属性**（语音 / SE）—— 只含行级演出，立绘/BGM/计时器等跟随 Steam。
# 生成：`script/build_original_audio.py`；说明见该文件。
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
# 补挂语音（「原版该行有配音、产物却没挂」）—— 由 `script/build_voice_plan.py` 生成
# （时长指纹找候选 + **台词核对**定案；键 = 表的 `k` = `rebuild` 的 `si`）。
VPF = ROOT / 'resource' / 'voice_plan.json'
VP = json.loads(VPF.read_text(encoding='utf-8')) if VPF.exists() else {}
CCS_DIR = ROOT.parent / 'cross-channel_chinese-localization_project' / 'Scripts' / '20150412'
RIO = ROOT / 'asset' / 'Rio.arc'
BACKUP = ROOT / 'asset' / 'Rio.arc.before_apply_text_map'
# 尾部控制符 = `%K`/`%P`/`%N` 的任意组合。**仅作结构断言用**（见 `check_tails`）：
# 它不决定该补什么尾巴，只断言"对话格必须有尾、选项格必须没有"。
TAIL = re.compile(r'(?:%[A-Za-z])+$')


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


IDD = _load('idd', ROOT / 'script' / 'insert_deleted_dialogues.py')


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


def expand_script(sc):
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
    src = IDD.src_lines(sc['ccs'])                 # 源 WSC 的日文正文（`14` 的 text 用它，哨兵据此核对）
    svc = IDD.src_voices(sc['ccs'])                # 源 WSC 的原版录音名（插入行照原生布局挂 `2e`）
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
                    (IDD.spk_lc(_jp.get(n, '')), ja, zh_n, svc.get(n, '')))
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
            nb = IDD.spk_lc(_jp.get(row, ''))
            base = lngmod.normalize_zh(zh.get(row, ''), en_of_cur.get(k0, {}).get('ws2_text', ''))
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
        en = en_of_cur.get(ks[0], {}).get('ws2_text', '')
        for k in range(ks[0], ks[1] + 1):
            e = en_of_cur.get(k, {}).get('ws2_text', '')
            if op == 'ccs':
                row = it['row'] if 'row' in it else it['row0'] + (k - ks[0])
                t = lngmod.normalize_zh(zh.get(row, ''), e)
                if 'span' in it:
                    sp = it['span']
                    t = lngmod.slice_span(t, sp[0], sp[1], spk.get(row, False))
                t = lngmod.fix_tail(t, e)
                # 名字框随源行（`_jp` 是 CCS 日文列 = 带 `[話者]`；无 `[話者]` ⇒ 清框）
                names[k] = IDD.spk_lc(_jp.get(row, ''))
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


en_of_cur = {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--write', action='store_true')
    ap.add_argument('--rio', default=str(RIO), help='要改的归档（默认 asset/Rio.arc；试跑时可指向副本）')
    args = ap.parse_args()
    rio = Path(args.rio)
    members = {n.decode('utf-16-le').upper(): d for n, d in arcbuild.read_raw(rio)}
    newdata, nscript, nins, ndrop = {}, 0, 0, 0
    n_skipped = []
    for stem, sc in sorted(TM.items()):
        wk, lk = stem + '.WS2', stem + '.LNG'
        # `.LNG` **由本步生成**，不要求预存 —— `backup/`（Steam 原版）一条 lng 都没有，
        # 旧判据 `lk not in members` 会把 293 个脚本**全部跳过**（产物里 Rio 只剩带入的 35 条 lng）。
        if wk not in members:
            n_skipped.append(stem)
            continue
        global en_of_cur
        raw2d = ws2.decode(members[wk])               # rebuild 要**解码后**的字节（含 rot6 还原）
        ins = ws2disasm.disassemble(raw2d)
        en_of_cur = en_of_from(ins)                   # 每格英文**从归档直接读**（见 en_of_from）
        texts, inserts, drops, names, rows, stages = expand_script(sc)
        # **"漏一格"的硬拦截**（2026-09-25 第 28 轮验收点名）：表若对某个 Steam 槽**没有任何处置**
        # （既不在 `texts`、也不是 `drop`），写盘会**静默**产出 —— `build_lng` 给它写一条**空条目**
        # （屏幕上是一行空白），`rebuild` 则照旧沿用 Steam 英文；两处都不报错。
        # 这里直接中止，不静默产出。
        _missing = sorted(set(range(sc['n_steam'])) - set(texts) - drops)
        if _missing:
            raise SystemExit('[中止] %s：表对槽 %s 没有任何处置（漏一格）—— '
                             '写盘会静默变空白或沿用 Steam 英文'
                             % (stem, _missing[:20]))
        plan = {at: seg for at, seg in inserts.items()}
        _vc = VC.get(stem, {})
        # 名字框：**先按台词来源自动推导**（`names`），**再用人工 vc 覆盖**（同名以人工为准）。
        # vc 可写裸名（`Misato` → `%LC Misato`）；**写空串表示「清空名字框」**（旁白/独白格）。
        vcs = dict(names)
        for k, v in (_vc.get('vc') or {}).items():
            vcs[int(k)] = ('' if v == '' else (v if v.startswith(IDD.LC) else IDD.LC + v))
        del2e = set(_vc.get('del2e') or [])
        # 补挂语音：`voice_plan.json` 给的那些格（原版该行有配音、产物没挂、非切分）
        plan2e = {int(k): v['f'] for k, v in (VP.get(stem) or {}).items() if v.get('f')}
        # **演出属性随来源**：正文取自原版的格，若该行**原版没有配音**，就删掉格上的 `2e`
        # （Steam 把原版的旁白改写成了台词并配了音；正文换回原版后，那声音就成了别人的）。
        # ⚠️ 只做**行级演出属性**；立绘/BGM/计时器/跳转等场景与状态属性**跟随 Steam**（既有方针）。
        # ⚠️ 切分：一行拆成多格时，各格共用同一源行 ⇒ 判定相同；若 Steam 只在其中一格挂了 `2e`，
        #    只有那一格会被删（`del2e` 按文件名删，实测 0 处同名共用，不会误伤别的格）。
        _ov = OA.get(sc['ccs'], {})
        _cv = cur_voice(ins, frozenset())        # 全部格的 `2e`（含将被删的格）
        # ① 删格：正文被移除，它挂的 `2e` 也不该留 —— `rebuild` 只去掉 `14`，
        #    语音会"漂"到下一格去（实测 9 处、0 处同名共用）。
        for k in drops:
            if _cv.get(k):
                del2e.add(_cv[k])
        # ② 正文取自原版、而该行**原版没有配音** ⇒ 删掉格上的 `2e`。
        # ⚠️ **切分格除外**：Steam 把原语音切成多段、每段配一句英文，我们只是把中文按同样的
        #    语义点切开去替换 —— 于是**每段中文与其对应的 Steam 切分语音是一一对应的**，
        #    那种格的 `2e` 就是对的，不许动（实测误删 9 处，如 `CCA0016` k=281/282 共用源行 264、
        #    各挂 `MIS_0245`/`MIS_0246`）。
        if _ov:
            _rc = collections.Counter(rows.values())
            _split = {k for k, r in rows.items() if _rc[r] > 1}
            _voiced = set(_ov.get('voice') or [])
            for k, r in rows.items():
                if k in _split:
                    continue
                if r not in _voiced and _cv.get(k):
                    del2e.add(_cv[k])
        new2 = IDD.rebuild(raw2d, ins, plan, drops, vcs, del2e, plan2e, stages,
                           {int(k): bytes.fromhex(OS.get(sc['ccs'], {}).get(str(row), ''))
                            for k, row in (STAGEMAP.get(stem) or {}).items()})
        newL, kinds = build_lng(ins, plan, drops, texts)
        check_tails(stem, newL, kinds)               # 结构断言：对话格必须有尾、选项格必须没有
        back = ws2disasm.disassemble(ws2.decode(ws2.encode(new2)))
        n14 = sum(1 for i in back if i.opcode == 0x14)
        n0f = sum(len(i.fields['entries']) for i in back if i.opcode == 0x0f)
        if n14 + n0f != len(newL):
            raise SystemExit('[不符] %s：ws2 占位 %d != lng %d' % (stem, n14 + n0f, len(newL)))
        if len(newL) != sc['n_final']:
            raise SystemExit(
                '[不符] %s：lng %d != 表 n_final %d\n'
                '  最常见成因：**在已建成的 asset 上跑** —— 表里的插入格会被再算一遍。\n'
                '  本步必须从 `asset/Rio.arc.before_insert_deleted` 基线跑（或直接跑 build_patch.py）。'
                % (stem, len(newL), sc['n_final']))
        newdata[wk] = ws2.encode(new2)
        newdata[lk] = lngmod.encode_lng(newL)
        nins += sum(len(v) for v in inserts.values())
        ndrop += len(drops)
        nscript += 1
    print('脚本 %d 个；插入 %d 格、删格 %d 格；lng 与 n_final 逐脚本相符' % (nscript, nins, ndrop))
    if not args.write:
        print('（未写入；加 --write 才改 %s）' % rio)
        return 0
    bck = rio.with_name(rio.name + ".before_apply_text_map")
    if not (bck.exists() and arcbuild.same_file(bck, rio)):
        arcbuild.write_arc(arcbuild.read_raw(rio), bck)
        print("[备份] %s" % bck)
    members.update(newdata)
    order = [n for n, _ in arcbuild.read_raw(rio)]
    # **新增成员必须追加**：`order` 只含 Rio 里已有的成员，而 `backup/`（Steam 原版）**一条 `.LNG`
    # 都没有** ⇒ 首次在 backup 基线上运行时，293 个重生成的 lng 若不追加就**全丢**（只剩带入的 35 条）。
    have = {nb.decode('utf-16-le').upper() for nb in order}
    extra = sorted(k for k in newdata if k not in have)
    real = {nb.decode('utf-16-le').upper(): nb.decode('utf-16-le') for nb in order}

    def name_for(k):
        """新增成员的**真实名**必须沿用既有写法的**大小写**（如 `CCA0002_en.lng`，不是 `…_EN.LNG`）——
        否则交付层的增量会变成"293 个成员被改名"。取同名 `.WS2` 的实际名、换扩展名。"""
        if not k.upper().endswith('.LNG'):
            return k
        w = real.get(k[:-4] + '.WS2')
        return (w[:-4] + '.lng') if w else k

    merged = [(nb, members[nb.decode('utf-16-le').upper()]) for nb in order]
    merged += [(name_for(k).encode('utf-16-le'), newdata[k]) for k in extra]
    arcbuild.write_arc(merged, rio)
    backmap = {n.decode("utf-16-le").upper(): v for n, v in arcbuild.read_raw(rio)}
    for k, v in newdata.items():
        if backmap.get(k) != v:
            raise SystemExit('[失败] 回读不一致：%s' % k)
    cnt, size, _ = arcbuild.verify(rio)
    print('[写入] 回读校验通过（%d 个成员，%d 字节）' % (cnt, size))
    return 0


if __name__ == '__main__':
    sys.exit(main())
