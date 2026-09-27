# -*- coding: utf-8 -*-
"""写盘器：按 `resource/text_map.json` **一次产出结构 + lng**。

**规划与落盘分家**：本文件只剩「装配（计划 + 名字框 + 语音 + 演出）→ 调 `tool.writer.rebuild`
→ 按最终槽位序取 lng → 结构断言 → 落盘 + 回读校验」；
「表怎么展开成计划」在 `tool/textplan.py`。

取代了结构写盘器原先的独立入口与 lng 生成的那一族（后者按「源 CCS 对齐」生成 lng，
而**表才是准据** —— 表的 `n_final` 就是最终槽位布局）；两趟各自生成会静默不一致，
所以这里一次产出。

不变式（逐脚本断言，不符即中止、不写盘）：
  - `lng 条数 == ws2 占位数 == 表的 n_final`
  - 表对每个 Steam 槽**都**有处置（漏一格即中止，不再静默产出空白格或沿用英文）
  - 对话格必带尾部控制符、选项格必不带（见 `textplan.check_tails`）

用法：`python script/apply_text_map.py [--write] [--rio <副本>]`
"""
import argparse
import collections
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tool import arcbuild, lng as lngmod, textplan as TP, writer, ws2, ws2disasm  # noqa: E402

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

RIO = ROOT / 'asset' / 'Rio.arc'
BACKUP = ROOT / 'asset' / 'Rio.arc.before_apply_text_map'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--write', action='store_true')
    ap.add_argument('--rio', default=str(RIO), help='要改的归档（默认 asset/Rio.arc；试跑时可指向副本）')
    args = ap.parse_args()
    rio = Path(args.rio)
    members = {n.decode('utf-16-le').upper(): d for n, d in arcbuild.read_raw(rio)}
    newdata, nscript, nins, ndrop = {}, 0, 0, 0
    n_skipped = []
    for stem, sc in sorted(TP.TM.items()):
        wk, lk = stem + '.WS2', stem + '.LNG'
        # `.LNG` **由本步生成**，不要求预存 —— `backup/`（Steam 原版）一条 lng 都没有，
        # 旧判据 `lk not in members` 会把 293 个脚本**全部跳过**（产物里 Rio 只剩带入的 35 条 lng）。
        if wk not in members:
            n_skipped.append(stem)
            continue
        raw2d = ws2.decode(members[wk])               # rebuild 要**解码后**的字节（含 rot6 还原）
        ins = ws2disasm.disassemble(raw2d)
        en_of = TP.en_of_from(ins)                    # 每格英文**从归档直接读**（见 textplan）
        texts, inserts, drops, names, rows, stages = TP.expand_script(sc, en_of)
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
        _vc = TP.VC.get(stem, {})
        # 名字框：**先按台词来源自动推导**（`names`），**再用人工 vc 覆盖**（同名以人工为准）。
        # vc 可写裸名（`Misato` → `%LC Misato`）；**写空串表示「清空名字框」**（旁白/独白格）。
        vcs = dict(names)
        for k, v in (_vc.get('vc') or {}).items():
            vcs[int(k)] = ('' if v == '' else (v if v.startswith(writer.LC) else writer.LC + v))
        del2e = set(_vc.get('del2e') or [])
        # 补挂语音：`voice_plan.json` 给的那些格（原版该行有配音、产物没挂、非切分）
        plan2e = {int(k): v['f'] for k, v in (TP.VP.get(stem) or {}).items() if v.get('f')}
        # **演出属性随来源**：正文取自原版的格，若该行**原版没有配音**，就删掉格上的 `2e`
        # （Steam 把原版的旁白改写成了台词并配了音；正文换回原版后，那声音就成了别人的）。
        # ⚠️ 只做**行级演出属性**；立绘/BGM/计时器/跳转等场景与状态属性**跟随 Steam**（既有方针）。
        # ⚠️ 切分：一行拆成多格时，各格共用同一源行 ⇒ 判定相同；若 Steam 只在其中一格挂了 `2e`，
        #    只有那一格会被删（`del2e` 按文件名删，实测 0 处同名共用，不会误伤别的格）。
        _ov = TP.OA.get(sc['ccs'], {})
        _cv = TP.cur_voice(ins, frozenset())        # 全部格的 `2e`（含将被删的格）
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
        new2 = writer.rebuild(raw2d, ins, plan, drops, vcs, del2e, plan2e, stages,
                           {int(k): bytes.fromhex(TP.OS.get(sc['ccs'], {}).get(str(row), ''))
                            for k, row in (TP.STAGEMAP.get(stem) or {}).items()})
        newL, kinds = TP.build_lng(ins, plan, drops, texts)
        TP.check_tails(stem, newL, kinds)               # 结构断言：对话格必须有尾、选项格必须没有
        back = ws2disasm.disassemble(ws2.decode(ws2.encode(new2)))
        n14 = sum(1 for i in back if i.opcode == 0x14)
        n0f = sum(len(i.fields['entries']) for i in back if i.opcode == 0x0f)
        if n14 + n0f != len(newL):
            raise SystemExit('[不符] %s：ws2 占位 %d != lng %d' % (stem, n14 + n0f, len(newL)))
        if len(newL) != sc['n_final']:
            raise SystemExit(
                '[不符] %s：lng %d != 表 n_final %d\n'
                '  最常见成因：**在已建成的 asset 上跑** —— 表里的插入格会被再算一遍。\n'
                '  本步必须从串接前基线跑（`asset/Rio.arc.before_inline_splice`；或直接跑 build_patch.py）。'
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

