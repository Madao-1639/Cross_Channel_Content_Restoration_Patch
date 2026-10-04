"""补丁构建流水线（`asset/` 的唯一入口）。

**当前形状**：源 WSC 的删除区间**就地插入** 12 个 Steam 宿主脚本（见 doc/call-chain.md），
不引入独立还原脚本；基线为 **`backup/`（Steam 原版）**。阶段划分
（**只看表的闸在写盘之前，比对产物的闸在写盘之后**；`check_reset_state` 在任何写盘之前）；
**顺序与阶段由 `STEPS` 一张表给出**（下面这份是同一件事的散文版，两者不要各改一处）：

    ---- 引导（仅 `--bootstrap`）----
    bootstrap_from_backup     以 backup/ 起底 + 显式带入：Fonts/Script（内化：resource/reused_archives/）、
                              35 个表外 lng、967 条 .soundlevel 侧车、补入的原版语音、37 张原版 CG
                              （build_restored_cgs → build_cg_map → renumber_evcc9xxx 核对）
    ---- 写盘之前 ----
    verify_text_map_structure **表自身**自洽（格数 / 覆盖 / 行序 / span）—— 只看表，不自洽就不写盘
    check_reset_state         建成态即拒跑（重跑会再插一遍）
    snapshot_assets           素材按轮快照（带时间戳的回滚点；内容未变则跳过）
    ---- 写盘 ----
    apply_graphic_overrides  汉化图替换 Graphic.arc 的整屏图（同名替换，零破坏性）
    splice_restoration       把源 WSC 的删除区间就地插入 12 个宿主
    remove_cnr_scripts       移除已无引用的 CNR### 还原脚本（backup 基线下为 no-op）
    build_original_stage     插入行随行的原版画面演出 -> resource/original_stage.json
    apply_text_map           按 resource/text_map.json **一次产出结构 + lng**（含名字框、语音、演出）
    import_missing_voices    补入扩覆盖范围所需的原版语音（**采样率对齐 48000**）—— 须在 apply_text_map 之后
    audit_choices            选项（`0f`）池位 / 条数 / 槽位内容三项检查 —— 须在产出 lng 之后
    build_nametable          从 resource/speaker_map.json 生成 NameTable.txt
    apply_sysgraphic         UI 汉化：resource/SysGraphic/ 的图层换进 SysGraphic.arc
                             （同名 pna 图层替换，尺寸可不同；**写盘的最后一步**）
    ---- 写盘之后 ----
    verify_text_map           **表 ↔ 产物**对账（占位数 / 插入段 / lng 条数 / 删格 / 行消耗）
    final_verification        全量验收

⚠️ **`METADATA.json` 只放归档条目**（`{归档名: {checksum, members}}`）、**不带任何 `_` 前缀元信息键**：
安装器的安装后校验把 METADATA 的**每一个键**当归档名去游戏目录核哈希 ⇒ 多一个键就多一条假的
「文件不存在」FAIL。也**不生成"输入指纹"** —— 钉不住的输入（`backup/`、仓库外的原版归档、
ffmpeg/libvorbis 版本）太多，一张"全输入"清单只会给出假保证；产物完整性由逐归档 `checksum`
+ 回读校验负责。

用法（项目根目录）：

    python script/build_patch.py                 # 跑全流程（**写盘前先跑表侧闸**；末步不幂等，复跑须先复位素材）
    python script/build_patch.py --bootstrap     # 从零：先按 backup/ 起底（见上「引导」）
    python script/build_patch.py --check         # 只跑三道闸，不写任何东西

⚠️ **`--bootstrap` 会按 `backup/`（Steam 原版）覆盖 `asset/` 的 5 份归档**，再显式带入
Fonts/Script、35 个表外 lng、37 张原版 CG；之后必须再跑后续步骤才能得到产物。
"""
import argparse
import atexit
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tool import arcbuild  # noqa: E402

ASSET = ROOT / 'asset'
BACKUP = ROOT / 'backup'
REUSED = ROOT / 'resource' / 'reused_archives'   # 完全复用的上游归档（汉字字体 / Lua 界面）
LOCK = ROOT / '.build.lock'


def bootstrap_backup(fname):
    """从 `backup/`（Steam 原版）铺一份归档进 `asset/`；内容相同则跳过。

    ⚠️ 用 `arcbuild.write_arc`（写临时文件 + `os.replace`）落盘，**不用 `shutil.copy2`** ——
    本机对已存在的大归档做 `O_TRUNC` 会偶发 `OSError: [Errno 22]`（见 `write_arc` 说明）。"""
    src = BACKUP / fname
    target = ASSET / fname
    if not src.exists():
        raise SystemExit('[bootstrap] backup/%s 不存在' % fname)
    if target.exists() and arcbuild.same_file(src, target):
        print('[bootstrap] %s 与 backup 逐字节相同，跳过' % fname)
        return
    arcbuild.write_arc(arcbuild.read_raw(src), target)
    arcbuild.verify(target)
    print('[bootstrap] %s copied from backup' % fname)


def carry_carried_lng():
    """把 `resource/carried_lng/`（**本项目处理集之外、但要保留的汉化 lng**）显式带回 `asset/Rio.arc`。

    内化数据由 `script/internalize/extract_carried_lng.py` 从 Res303 一次提取；基线改从 `backup` 起底后，
    这些 lng 不再由 Res303 直供。幂等：内容相同则跳过。"""
    man = json.loads((ROOT / 'resource' / 'carried_lng' / 'manifest.json').read_text(encoding='utf-8'))
    want = man['members']
    members = list(arcbuild.read_raw(ASSET / 'Rio.arc'))
    idx = {n.decode('utf-16-le'): i for i, (n, _) in enumerate(members)}
    added = replaced = 0
    for name in sorted(want):
        d = (ROOT / 'resource' / 'carried_lng' / name).read_bytes()
        if name in idx:
            if members[idx[name]][1] == d:
                continue
            members[idx[name]] = (members[idx[name]][0], d)
            replaced += 1
        else:
            members.append((name.encode('utf-16-le'), d))
            added += 1
    print('[bootstrap] 表外汉化 lng %d 条：新增 %d / 替换 %d' % (len(want), added, replaced))
    if added or replaced:
        arcbuild.write_arc(members, ASSET / 'Rio.arc')
        arcbuild.verify(ASSET / 'Rio.arc')


def carry_carried_soundlevel():
    """把 `resource/carried_soundlevel.json` 的音量包络显式带回 `asset/Voice.arc`。

    ⚠️ **`backup/Voice.arc` 里一条 `.soundlevel` 都没有**（实测 15,144 成员全无侧车），而产物
    需要 16,111 条 —— 多出的 **967** 条只在 Res303 的 Voice.arc 里。起底时不显式带过就会**静默丢失**。
    `.soundlevel` 是按秒的 ASCII 包络（30 样本/秒，与采样率无关）。幂等：内容相同则跳过。"""
    d = json.loads((ROOT / 'resource' / 'carried_soundlevel.json').read_text(encoding='utf-8'))
    want = {k: v for k, v in d.items() if not k.startswith('_')}
    members = list(arcbuild.read_raw(ASSET / 'Voice.arc'))
    idx = {n.decode('utf-16-le'): i for i, (n, _) in enumerate(members)}
    added = replaced = 0
    for stem in sorted(want):
        name = stem + '.soundlevel'
        data = want[stem].encode('ascii')
        if name in idx:
            if members[idx[name]][1] == data:
                continue
            members[idx[name]] = (members[idx[name]][0], data)
            replaced += 1
        else:
            members.append((name.encode('utf-16-le'), data))
            added += 1
    print('[bootstrap] 侧车 .soundlevel %d 条：新增 %d / 替换 %d' % (len(want), added, replaced))
    if added or replaced:
        arcbuild.write_arc(members, ASSET / 'Voice.arc')
        arcbuild.verify(ASSET / 'Voice.arc')


def bootstrap_from_backup():
    """以 `backup/`（Steam 原版）为基线起底，**显式带入**少数几项：

      * `Fonts.arc` / `Script.arc` —— 完全复用（内化在 `resource/reused_archives/`；汉字字体、Lua 界面）；
      * **35 个处理集之外的汉化 lng** —— 内化在 `resource/carried_lng/`；
      * **37 张还原 CG** —— 由 `build_restored_cgs.py` 从**原版** `Chip.arc` 放大（LANCZOS 1280×960）改名补入。

    其余一律来自 `backup`，且后续步骤（`splice_restoration` → `apply_text_map`）会重生成 293 个在表脚本。
    """
    for fname in ('Rio.arc', 'Graphic.arc', 'Chip2.arc', 'Voice.arc', 'SysGraphic.arc'):
        bootstrap_backup(fname)
    bootstrap_copy('Fonts.arc', 2)                 # 完全复用（内化副本）
    bootstrap_copy('Script.arc', 14)               # 完全复用（内化副本）
    carry_carried_lng()
    carry_carried_soundlevel()
    # ⚠️ **语音必须在 `splice_restoration` 之前补全**：splice 的改名核对要求被引用的语音已在档，
    #    而 backup 里没有它们。流水线里本步排在 splice 之后（当时靠 Res303 的 Voice 已含），
    #    换 backup 起底后必须提前到这里。（流水线里那一步此后为 no-op。）
    run('import_missing_voices.py', '--write')
    run('build_restored_cgs.py', '--write')        # 37 张原版 CG -> asset/Chip2.arc
    run('build_cg_map.py')                         # 用新 Chip2 重新核对改名映射（幂等）
    run('renumber_evcc9xxx.py')                    # 核对 CG 引用全部可解析


def bootstrap_copy(fname, expect_count):
    """把一份**完全复用**的归档（`resource/reused_archives/`）铺进 `asset/`。

    这两份（`Fonts.arc` 汉字字体、`Script.arc` Lua 系统界面）原样采用、不涉裁定。原先从外部上游
    目录取 ⇒ 构建期依赖它；现读仓库内的内化副本 ⇒ **构建期不再需要任何上游知识**。
    ⚠️ 落盘走 `arcbuild.write_arc`（大归档上 `shutil.copy2` 会撞 `OSError 22`）。"""
    src_path = REUSED / fname
    target = ASSET / fname
    if not src_path.exists():
        raise SystemExit('[中止] 缺少 %s（先跑 script/internalize/extract_reused_archives.py）' % src_path)
    if target.exists() and arcbuild.same_file(src_path, target):
        print('[bootstrap] %s 与内化副本逐字节相同，跳过' % fname)
    else:
        arcbuild.write_arc(arcbuild.read_raw(src_path), target)
    arcbuild.verify(target, expect_count=expect_count)
    print('[bootstrap] %s copied from reused_archives' % fname)


def _pid_alive(pid):
    """该 pid 是否仍活着。Windows 上用 `tasklist` —— 绝不能用 `os.kill(pid, 0)`：后者在 Windows
    会把信号当终止请求，**真把进程杀掉**。"""
    try:
        r = subprocess.run(['tasklist', '/FI', 'PID eq %d' % pid],
                           capture_output=True, text=True)
        return str(pid) in (r.stdout or '')
    except Exception:
        return False


def acquire_lock():
    """并发保护：同一时刻只允许一个 `build_patch.py`。

    **为什么需要**：每个实例都会**复位并重写 `asset/`**，并发时两边互相写坏（实测发作：一边把
    `Rio` 拉回串接前、另一边刚把 `Chip2` 复位而 CG 尚未补回 ⇒ 假报"CG 改名目标缺失"）。
    锁文件记 `pid` + 起始时间；**进程已死 ⇒ 视为陈旧锁，警告后接管**；仍活 ⇒ 拒跑。
    """
    if LOCK.exists():
        try:
            info = json.loads(LOCK.read_text(encoding='utf-8'))
            pid = int(info.get('pid', -1))
        except Exception:
            info, pid = {}, None
        if pid and _pid_alive(pid):
            raise SystemExit('[中止] 已有 build_patch 在跑（pid %d，起于 %s）—— 并发会让两边互相写坏 '
                             'asset/。等它结束；若确认它已死，删掉 %s 再跑。'
                             % (pid, info.get('since'), LOCK.name))
        print('[警告] 发现陈旧锁 %s（pid %s 已不在）—— 接管' % (LOCK.name, pid))
    LOCK.write_text(json.dumps({'pid': os.getpid(),
                                'since': time.strftime('%Y-%m-%d %H:%M:%S')}),
                    encoding='utf-8')


def release_lock():
    """只删**自己**的锁（接管别人的陈旧锁后，不要把它删成别的进程的）。"""
    if not LOCK.exists():
        return
    try:
        if int(json.loads(LOCK.read_text(encoding='utf-8')).get('pid', -1)) == os.getpid():
            LOCK.unlink()
    except Exception:
        pass


def run(script, *args):
    cmd = [sys.executable, str(ROOT / 'script' / script)] + list(args)
    # 父进程 print 会被缓冲、子进程直写 fd ⇒ 日志交错（曾据此误判"某步没有输出"）。故先 flush。
    sys.stdout.flush()
    print('\n===== %s %s =====' % (script, ' '.join(args)), flush=True)
    r = subprocess.run(cmd, cwd=str(ROOT))
    if r.returncode != 0:
        raise SystemExit('[中止] %s 失败（退出码 %d）' % (script, r.returncode))


def snapshot_assets():
    """写盘**之前**给素材留一份按轮带时间戳的快照（回滚点）。

    ⚠️ 2026-09-25 第 28 轮验收指出：此前"可回滚的那份"停在六天前 —— 因为旧规则是
    "备份存在就不刷新"，于是一次都没更新过。现改为：**每次都按内容判断**——
    与最近一份快照逐字节相同就跳过（不产生冗余），**只要内容变了就新写一份带时间戳的**。
    `Voice.arc`/`Chip2.arc` 很大（数百 MB），所以"相同就跳过"是必要的；关键在判据是**内容**不是**存在**。
    """
    import hashlib
    import shutil
    from datetime import datetime
    stamp = datetime.now().strftime('%Y%m%d_%H%M')
    for fname in ('Rio.arc', 'Voice.arc', 'Chip2.arc', 'Graphic.arc', 'SysGraphic.arc'):
        src = ASSET / fname
        if not src.exists():
            continue
        cur = hashlib.sha256(src.read_bytes()).hexdigest()
        prevs = sorted(ASSET.glob(fname + '.before_round_*'),
                       key=lambda p: p.stat().st_mtime, reverse=True)
        if prevs:
            old = hashlib.sha256(prevs[0].read_bytes()).hexdigest()
            if old == cur:
                print('[快照] %s 与最近一份（%s）逐字节相同，跳过' % (fname, prevs[0].name))
                continue
        dst = ASSET / ('%s.before_round_%s' % (fname, stamp))
        shutil.copy2(src, dst)
        print('[快照] %s → %s（sha256 %s）' % (fname, dst.name, cur[:16]))


# ---------------------------------------------------------------------------
# 流水线（**一张表**；阶段分组一眼可见，顺序即数据）
#
# `when`：`A` = 总是跑；`NB` = 只有**非** `--bootstrap` 才跑 —— 复位守卫在刚起底的基线上会误判
# （那时宿主还没串接，格数 != `n_steam`）。
# ---------------------------------------------------------------------------
A, NB = 'always', 'not_bootstrap'
PHASE_PRE, PHASE_WRITE, PHASE_POST = '写盘之前', '写盘', '写盘之后'

STEPS = [
    # 阶段,          目标,                         参数,         when, 为什么排在这儿
    (PHASE_PRE, 'verify_text_map_structure.py', (), A),
    #    ↑ **只看表**的闸必须先跑：表自己不自洽就不该拿去写盘（且它不读产物）
    (PHASE_PRE, 'check_reset_state.py', (), NB),
    #    ↑ 建成态即拒跑 —— 在已插入的 asset 上重跑会再插一遍，把产物写坏
    (PHASE_PRE, snapshot_assets, (), A),
    #    ↑ 素材按轮快照（回滚点）；此刻 asset/ 还是上一轮的成品
    (PHASE_WRITE, 'apply_graphic_overrides.py', ('--write',), A),
    (PHASE_WRITE, 'splice_restoration.py', ('--write',), A),
    (PHASE_WRITE, 'remove_cnr_scripts.py', ('--write',), A),
    (PHASE_WRITE, 'build_original_stage.py', (), A),
    #    ↑ 插入行随行演出 → resource/original_stage.json；**必须在 apply_text_map 之前**（后者读它）
    (PHASE_WRITE, 'apply_text_map.py', ('--write',), A),
    #    ↑ 按表**一次产出结构 + lng**（两趟各自生成会静默不一致）
    (PHASE_WRITE, 'import_missing_voices.py', ('--write',), A),
    #    ↑ **必须在 apply_text_map 之后**：还原行的 `2e` 是它发的，排在前面会漏 303 条、
    #      12 处悬空引用（`CCD4003A` 等）
    (PHASE_WRITE, 'audit_choices.py', ('--write',), A),
    #    ↑ **必须在产出 lng 之后**：它核的是 `.LNG` 的池槽位/条数/内容
    (PHASE_WRITE, 'build_nametable.py', ('--write',), A),
    (PHASE_WRITE, 'apply_sysgraphic.py', ('--write',), A),
    #    ↑ **写盘的最后一步**（UI 汉化）：resource/SysGraphic/ 的图层换进 SysGraphic.arc。
    #      不依赖脚本/lng 产物，排末尾；其后两道闸仍复核全量产物（含本步）
    (PHASE_POST, 'verify_text_map.py', (), A),
    #    ↑ 表 ↔ 产物对账
    (PHASE_POST, 'final_verification.py', (), A),
]

# `--check`：只跑三道闸（表自身 → 表↔产物 → 全量），不写任何东西
CHECK_STEPS = {'verify_text_map_structure.py', 'verify_text_map.py', 'final_verification.py'}


def run_steps(only=None, skip_not_bootstrap=False):
    """按 `STEPS` 跑。`only` 非空则只跑这些目标；`skip_not_bootstrap` 跳过 `NB` 行。"""
    phase = None
    for ph, target, args, when in STEPS:
        if only is not None and target not in only:
            continue
        if when == NB and skip_not_bootstrap:
            continue
        if ph != phase:
            phase = ph
            print('\n---- %s ----' % ph, flush=True)
        if isinstance(target, str):
            run(target, *args)
        else:
            target()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--bootstrap', action='store_true',
                    help='从零按 backup/ 起底（会覆盖 asset/，慎用）')
    ap.add_argument('--check', action='store_true', help='只跑验收，不写任何东西')
    args = ap.parse_args()
    ASSET.mkdir(exist_ok=True)
    # **并发保护**：`--check` 也要 —— 它只读 asset/，而并发写会让它读到瞬时态、报出假缺陷
    # （评审 2026-09-27 遇到过）。`atexit` 负责释放（含异常退出）；硬杀留下的锁下次会被判陈旧并接管。
    acquire_lock()
    atexit.register(release_lock)

    if args.check:
        print('[仅验收] 三道闸，不写任何东西')
        run_steps(only=CHECK_STEPS, skip_not_bootstrap=True)
        print('\n[DONE] 仅验收')
        return 0

    if args.bootstrap:
        print('!! --bootstrap：asset/ 将按 **backup（Steam 原版）** 基线起底')
        bootstrap_from_backup()
    run_steps(skip_not_bootstrap=args.bootstrap)
    print('\n[DONE] 流水线完成')
    return 0


if __name__ == '__main__':
    sys.exit(main())
