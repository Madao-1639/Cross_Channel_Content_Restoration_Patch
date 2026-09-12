# 还原目标

本项目要在**保留 Steam 成就系统**的前提下，恢复 Steam 版删掉的原创内容，并把英文文本
汉化为简体中文。方案实现见 [technical-solutions.md](technical-solutions.md)。

## 资源维度

### 1. 事件 CG（PNG）

| 资源类型 | 数量 | 命名 | 存储位置 |
|---|---:|---|---|
| H 场景事件 CG | 36 | `EVCC9XXX[变体]` | `Chip2.arc` |
| 并入的原版系统图（原 `SGCC0020`） | 1 | `EVCC9004` | `Chip2.arc` |
| Steam 原有 CG / 背景 / 立绘 | 不改动 | 原名 | Chip2 / Chip1 / Graphic |

37 个文件 → **19 个编号 `EVCC9000`–`EVCC9018`**（同一张 CG 的差分共用一个编号）。
命名规则与依据见 [pna-resources.md](pna-resources.md)；
对照用 `python script/renumber_evcc9xxx.py --check`。

**关键约束**：引擎对 `Chip2.arc` 的 CG 实施前缀白名单，只加载 `EVCC` 开头的文件，
`CN_` 前缀会被**静默忽略**（不报错、不加载）。`9XXX` 编号段未被 Steam 原版占用，零冲突。

### 2. 语音

| 项 | 数量 | 来源 |
|---|---:|---|
| H 场景语音（`*.OGG` + `*.soundlevel`） | 随 `Voice.arc` 整体复用 Res303 | Res303 |
| 扩覆盖范围所需的原版语音 | **186 条 OGG** | `../CROSS_CHANNEL_Original/Voice.arc`，由 `script/import_missing_voices.py` 补入 |

原版语音**不带** `.soundlevel`（Steam 侧才带逐段音量包络）。无包络的 OGG 照样播 ——
只补 OGG，不凭空造包络。

### 3. 背景与特效

无新增 —— 还原区间引用的背景/蒙版在 Steam 原档中已存在。

## 调用链维度

**目标**：让 Steam 版跑完整的原版剧情，同时保留 Steam 原档的出口与成就钩子。

**做法**：把源 WSC 的删除区间转换后**就地插入调用脚本** ——
`宿主 = [Steam 原生前段] + [原版插入段] + [Steam 原档出口尾段]`。
12 个宿主，不引入新脚本名、不增加跳转、不删除 Steam 内容，出口与 Steam 原档逐一致。

- 判据与逐场景参数见 [call-chain.md](call-chain.md)「就地插入接线」；
- 回归守卫 `script/audit_inline.py`：与源 CCS 对位后**恰好一次、无缺失、无重复、无倒序**；
- `CCC0000` 试点已**实机验证通过**。

> 早期版本（Res303 的「宿主截断 + 追加 `CNR###` 还原脚本」）已废弃：宿主会把整场**删节版**
> 按序播完，追加式还原会造成顺序倒置 + 局部重复，且截断宿主会丢掉宿主承担的演出。
> 详见 [lessons-learned.md](lessons-learned.md) §18。

**原版内容分散在多个脚本里**（宿主 / 后继 / 姊妹场景），所以不能只看某一个脚本的覆盖率
就断言「没还原完」；这也是把插入段定到**交接点**（下一个承载脚本的源起点 − 1）而不是
「某个还原脚本的覆盖终点」的原因。

## 汉化维度

| 项 | 内容 |
|---|---|
| 正文/选项 | `.lng`，**位置对应**（引擎按播放序第 N 条替换）；共 328 个 |
| 说话人名 | `Rio.arc` 的 `NameTable.txt`（UTF-16LE，不受脚本内 CP932 限制） |
| 字体 | 复用 Res303 的 `Fonts.arc`（18.5 MB × 2，含汉字字形） |
| 系统界面 | 复用 Res303 的 `Script.arc`（14 个 Lua 成员）；`SysGraphic.arc` **不做** |

**译文来源**：主线复用
[CROSS†CHANNEL 中文化项目](https://github.com/MewX/cross-channel_chinese-localization_project)
的官方译文（经汉化组 CCS 逐行对位，`CCS 序号 = 原版 WSC 对话 id + 1`）；
Steam 新增的 7 条角色线与后日谈来自 Res303 的机翻。

**质量状态**：Res303 自述 `TEXT_QA_PASS: false`（其对齐流水线在条数相等时按序直接 zip）。
本项目已独立复核并修复其自述的 6 条错配，另按模型重对齐 101 个脚本的 lng；
全量语义复审待重开。详见 [localization.md](localization.md)。

## 不做什么

- ❌ 不替换 Steam 原有资源（同名冲突一律改名隔离）
- ❌ 不合并 PNA（layer_id 是纯位置量，两版语义不通用）
- ❌ 不修改成就系统
- ❌ 不重新翻译（复用已有译文）
- ❌ 不做系统界面图片汉化（`SysGraphic.arc` 沿用 Steam 原版）

## 最终效果

安装补丁后，玩家在 Steam 购买的游戏基础上得到：

1. **内容还原**：所有路线的完整 H 场景恢复，对应 CG 与语音正常显示/播放；
2. **汉化**：全部剧情文本显示为简体中文，角色名正确；
3. **兼容性**：Steam 成就、界面、存档等原有功能不受影响。

## 剩余工作

- ⏳ **实机测试**：`CCC0000` 已通过，其余 11 个场景待测；名字框显示（`NameTable.txt`
  前缀修正后）与 lng 重排处（插入 `15`+`14` 的那 1 处）的演出待确认
- ⏳ **全量 lng 语义复审重开**（Res303 自述的 6 条已修，其余待复核）
- ⏳ **发布产物重新生成**：`payload/` 与 `releases/` 须在 `asset/` 定稿后重新生成
- ⏳ **`asset/*.arc` 移出 Git LFS**（已多次因 `.git/lfs/tmp` 堆积写满磁盘）
