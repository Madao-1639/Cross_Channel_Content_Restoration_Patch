# 技术方案

本文档记录 CROSS†CHANNEL 内容还原与汉化补丁的**现行**技术方案。逐项判据与踩坑记录见
[lessons-learned.md](lessons-learned.md)，格式细节见 [file-formats.md](file-formats.md)。

## 总体形状

```
Steam 原档 (backup/)  ──┬─ 未改动  → 安装器保留玩家原文件，不打包
                        └─ 有改动  → asset/（完整文件，测试用）
                                        └─ generate_payload.py → payload/（增量，发布用）
```

| 归档 | 处理 |
|---|---|
| `Rio.arc` | 12 个宿主脚本就地插入原版内容；补入 328 个 `.lng` 与 `NameTable.txt` |
| `Chip2.arc` | 补入 37 个 `EVCC9XXX.PNG`（还原 CG） |
| `Voice.arc` | 补入 186 条原版语音 |
| `Fonts.arc` | 整体复用 Res 303 的中文字体 |
| `Script.arc` | 整体复用 Res 303 的 Lua 系统界面（Steam 原版无此归档） |
| `Graphic.arc` / `Chip1.arc` / `SysGraphic.arc` / `SysVoice.arc` | **不改动，不打包**（安装器保留玩家原文件） |

---

## 1. 资源：同名冲突隔离与归档

原版与 Steam 版存在同名但内容不同的资源，**禁止覆盖 Steam 原文件**，必须重命名隔离。

- **事件 CG**：原版基号 → `EVCC9XXX`（按内容哈希反查，变体字母保留）→ `Chip2.arc`。
  引擎对 Chip2 实施 `EVCC` 前缀白名单，`CN_` 前缀会被静默忽略。
- **立绘**：`TC{角色}0{nnn}{变体}` → `TC{角色}1{nnn}{变体}`（两版被重编过）。
- **背景 / 蒙版 / 语音**：原名（Steam 侧未重编）。
- 实现：`tool/rename_map.py` —— 规则解不掉的条目**单独列出，不许静默跳过**。

完整规则与决策流程见 [pna-resources.md](pna-resources.md)。

## 2. 调用链：就地插入（不是穿插新脚本）

**目标**：让 Steam 版运行完整的原版剧情，同时保留 Steam 的成就与原有出口。

**方案**：把源 WSC 的删除区间转换后**就地插入调用脚本**，不引入还原脚本、不增加跳转、
不删除 Steam 内容：

```
宿主脚本 = [前段：Steam 原生，源 0 .. 接缝]
         + [插入段：源 WSC 的删除区间转换后插入]
         + [出口尾段：Steam 原档「末句对话之后」的字节，含原出口]
```

12 个宿主，出口与 Steam 原档**逐一致**；`CCC0000_en` 的 `01 mode=0x85` 条件双出口是唯一
带文件内偏移的出口，已按新布局重算。判据、逐场景参数与合并规则见
[call-chain.md](call-chain.md)「就地插入接线」；`script/splice_restoration.py` 是实施脚本，
`script/audit/audit_inline.py` 是回归守卫。

> **为什么不是「宿主截断 + 追加还原脚本」**：Steam 删 H 场景时会把该场景压成删节版留在
> 宿主里，而宿主是**按序跑完整场**的 —— 追加式会造成顺序倒置 + 局部重复；
> 且截断宿主会一并丢掉宿主承担的演出（立绘/BGM/SE）。详见
> [call-chain.md](call-chain.md)「就地插入接线」。

**资源引用**：插入段引入的原版资源名必须先跑重命名映射（见上）。
扩覆盖范围新增的语音由 `script/import_missing_voices.py` 从原版 `Voice.arc` 补入。
⚠️ 补入时**一律重采样到 48000 Hz**（`resample_ogg()`，ffmpeg `-ar 48000 -c:a libvorbis`）——
原版有一批 44100 录音，而 Steam 语料 99.86% 是 48000；偏离语料形态会走宿主几乎不走的解码路径
（一次概率性崩溃即由此，见 [engine-mechanics.md](engine-mechanics.md)「语音采样率」与
[lessons-learned.md](lessons-learned.md) 「概率性崩溃：导入的 44100 语音」）。仅 `--bootstrap` 从零重建会触发本步；
常规复跑因「只增不改」为 no-op。

## 3. 文本汉化：lng 位置对应

- **机制**：lng 里没有 id，引擎按播放序把**第 N 条文本**替换为 lng 第 N 条目。
  严格等式 `lng 条数 == 14 条数 + Σ(0f 的 count)`（选项条目各占一槽）。
- **来源**：主线复用汉化组官方译文（CCS，`CCS 序号 = 原版 WSC 对话 id + 1`），
  Steam 新增的 7 条角色线与后日谈来自 Res303 的机翻。
- **宿主 lng 重建**：就地插入后宿主的位置全部变了，lng 必须整体重建 ——
  **现由 `script/apply_text_map.py` 按 `resource/text_map.json` 一次产出**（原
  `script/build_host_lng.py` 已废弃：只写 `.LNG`、输出必被覆写）。
  尾部控制符照抄源文本（`%K` / `%K%P` / `%N` / `%P`，不是一律 `%K%P`）。
- **池位错位修复**：由 `script/apply_text_map.py` 按表**一次产出**（表里同一行的拆分记为
  `span: [from, to)`——同一格取该行中文的字符片段，**不新增槽位**）。
  早期的 `script/realign_lng_to_ws2.py`（DP 对齐、会给 ws2 插 `15`+`14`）已废弃：
  实测它对 `apply_text_map` 的输入逐字节不改任何脚本，且其 lng 必被覆写，贡献为 0。
- **人名不走 lng**：由 `Rio.arc` 的 `NameTable.txt`（UTF-16LE）替换。

详见 [localization.md](localization.md)。

## 4. 字体支持

复用 Res 303 的 `Fonts.arc`（`FOT-MatissePro-B/M.PTF`，各 18.5 MB，含汉字字形；
Steam 原版只有 7.0–7.6 MB）。`CharSet = GB2312_CHARSET` 是简体显示的关键。

## 5. 系统界面

- **复用 Res 303 的 `Script.arc`**（14 个成员：13 个 Lua 字节码 + `LegacyGame.inc`），
  提供图形化菜单/设置/画廊。Steam 原版不含此归档。
- **`SysGraphic.arc` 不做**：暂不处理图片汉化，沿用 Steam 原版（不打包）。

---

## 工具链

### 基线、复跑与回滚（机制）

**基线 = `backup/`（Steam 原版）**。`build_patch.py --bootstrap` 按它铺 5 份归档，再**显式带入**少数几项：

| 带入项 | 来源 |
|---|---|
| `Fonts.arc` / `Script.arc` | **复用 Res303**（汉字字体、Lua 系统界面）—— 唯二保留的 Res303 依赖 |
| 35 个表外汉化 lng | 内化数据 `resource/carried_lng/` |
| 967 条 `.soundlevel` 侧车（补入录音的包络） | 内化数据 `resource/carried_soundlevel.json`（`backup` 只带自己那 15,144 条的包络；这 967 条只存在于 Res303，不显式带过即静默丢失） |
| 补入的原版录音 | `CROSS_CHANNEL_Original/Voice.arc`（并统一重采样 48000） |
| 37 张还原 CG | `build_restored_cgs.py`：原版 `Chip.arc` 的 `EVCC####.PNG` 放大到 1280×960（只需目标一致，不必字节级） |

其余全部由后续步骤从 `resource/text_map.json` + 源 WSC（`resource/corpus/wsc`）生成。
**验收**：从 `backup` 起底**跑两次**全链，五份归档**逐字节一致**（`final_verification` 全过）。

**`build_patch.py` 不复位素材**，而 `apply_text_map.py` **不幂等**（按表在**输入**上再插一遍，在已建成的
产物上跑会把插入再算一遍）。所以「**复跑 0 处改动**」＝先回到**复位点**再复跑：`check_reset_state.py`
在**任何写盘之前**拦下"已建成"的输入（判据：有插入/删格的**在表非宿主**脚本，格数 `== n_final`
⇒ 是建成态；宿主由 `splice` 产生、串接前本就是 Steam 格数，不参与判定）。
**复位 = 跑一次 `--bootstrap`**（从 `backup` 起底、全链重建；最省事也最可靠，且自带验收）。
它跑完会把"串接前态"写成 `asset/Rio.arc.before_inline_splice` —— `splice` 每轮按**内容**刷新它，
那是给**后续非 bootstrap 的复跑**用的复位点（它不存在时，用 `--bootstrap` 重建一次即可）。

**并发保护（`.build.lock`）**：每个实例都会**复位并重写 `asset/`**，两个同时跑必然互相写坏
（实测：一边把 `Rio` 拉回串接前、另一边刚复位 `Chip2` 而 CG 未补回 ⇒ 假报"CG 改名目标缺失"）。
`build_patch.py` 起手取锁（记 `pid` + 时间，`atexit` 释放；**`--check` 也取** —— 它只读 `asset/`，
并发写会让它读到瞬时态、报假缺陷）。同锁的进程**仍活 ⇒ 拒跑**；**进程已死 ⇒ 判陈旧、警告后接管**。
⚠️ 判活必须用 `tasklist`：`os.kill(pid, 0)` 在 Windows 会**真把进程杀掉**。

**闸的次序固定为「只看表 → 写盘 → 表↔产物 → 全量验收」**：

- **写盘之前**跑 `verify_text_map_structure.py`（**只看表**：格数自洽 / 覆盖无空档 / 行序单调 / span 可定位）。
  表自己不自洽就**不该拿去写盘** —— 此前三道检查全排在写盘之后，报警时产物已被覆盖。
- **写盘器自身的硬拦截**：`apply_text_map.py` 逐槽核「该槽有没有处置」，**漏一格即中止**，
  不再静默产出（旧行为：lng 给它写一条**空条目** ⇒ 屏幕上一行空白；或照旧沿用 Steam 英文）。
- **写盘器的第二道硬拦截（`check_tails`）**：逐脚本断言「**对话格必带**尾部控制符、**选项格必不带**」。
  该判据在两版语料上零例外、且不需要任何外部参照（不变量与实测见
  [file-formats.md](file-formats.md)「对话文本标记」）；它与 `final_verification.py` 里的同名检查
  是同一判据的两道接线（写盘期逐脚本中止 / 产物级全库复核）。
  ⚠️ 每格英文**从输入归档的指令流直读**（`en_of_from`），**不读任何派生缓存** ——
  缓存路径失效过一次，读空的后果是 `fix_tail(zh, '')` 什么都不补、整份产物尾标记静默丢失，
  而当时其余闸照过；且槽位编号必须与写盘器同口径（`14` 一格、**每个 `0f` 条目也各占一格**）。
- **写盘之后**跑 `verify_text_map.py`（表 ↔ 产物对账）→ `final_verification.py`（全量验收）。
  前者七条断言里**只有第 4 条（插入段位置与正文）依赖产物布局**，所以槽位序对不上时**只跳过第 4 条**、
  其余照跑（产物过期时它们是唯一还验得动的检查）—— 早先整段 `continue` 会把它们连坐跳过。

**素材快照按轮带时间戳**：写盘前调 `snapshot_assets()` —— 与 `asset/<归档>.before_round_<时间戳>`
里**最近一份逐字节比较**，相同则跳过（`Voice.arc`/`Chip2.arc` 数百 MB，不比内容就会把磁盘堆爆），
**内容一变就新写一份**（旧规则是"存在就不刷新"，结果那份停在六天前）。
⚠️ 快照记的是「**运行开始前那一刻的状态**」；若按上面的规矩**先复位 `asset/Rio.arc` 再跑**，
这份快照记的就是复位后的基线、而非上一轮的产物 —— 此时上一轮产物的回滚点要**自己先留**
（如 `Rio.arc.before_rebuild_<日期>`）。

**⚠️ 做"一轮改了什么"的全量对账时，基线必须是「那一轮的起点」那份表快照**（＝上一轮收工时、开工前的表），
**不能拿中途备份**：形如 `text_map.json.before_<某脚本>_<时间>` 的备份是**该脚本写入前**的状态，
已经含了该轮早先的改动 —— 拿它 diff 会**少算**（实测：用中途备份只看到 9 个脚本，换成真正的起点快照才见 22 个）。
⇒ 每轮开工第一件事：留一份 `text_map.json.before_round_<轮次>`。

### 复用（从 A Sky Full of Stars 项目）

- `tool/arcbuild.py` —— Arc 读写。`read_raw` / `write_arc`（原子写入）/ `verify` /
  `normalize_arc_padding` / `read_old_arc`（原版老式归档）
- `tool/ws2.py` —— WS2 的 rot6 编解码；`extract_pna_refs` / `extract_png_refs` /
  `extract_calls` / `extract_jumps`

### 本项目新增

| 工具 | 用途 |
|---|---|
| `tool/wsc.py` | 原版 WSC 反汇编（324/324 文件 100% 覆盖） |
| `tool/ws2disasm.py` | WS2 反汇编（363 个原生脚本 100% 覆盖） |
| `tool/wsc2ws2.py` | WSC→WS2 逐指令转换（含 `convert_range` 切片模式） |
| `tool/lng.py` | lng 编解码 + CCS 解析（`parse_ccs` / `parse_ccs_both`）+ **文本体例规则**（`fix_tail` / `normalize_zh`）+ 去说话人包裹 |
| `tool/writer.py` | **结构写盘器**：还原插入 / 删格 / 名字框同步 / 借用语音删除 → ws2 字节（原 `insert_deleted_dialogues.py`） |
| `tool/textplan.py` | **表 → 写盘计划**：逐格处置展开、尾标记、名字框、随行演出（原在写盘器里） |
| `tool/rename_map.py` | 资源**改名表**（原版名 → 本补丁名；原 `build_rename_map.py`） |
| `tool/archprobe.py` | 归档探针：运行时可用资源名集合 / PNA 层数（原在 `splice_restoration` 里） |
| `tool/pna.py` | PNA 分层图像只读解析：图层表 + 内嵌 PNG 切分（见 `engine-mechanics.md`「PNA 二进制布局」） |
| `tool/luac53.py` / `tool/luadis53.py` | Lua 5.3 字节码解析 / 反汇编 |
| `tool/install.py` | 安装器（PyInstaller 入口，`merge_arc` 按 METADATA 重组归档） |

### 流程脚本（`script/`）—— 按角色分

**入口**：`build_patch.py`（流水线总控；`--bootstrap` 从 `backup/` 起底）、`generate_payload.py`（增量包）、
`pack.sh`（PyInstaller 打包安装器）。

**流水线步骤**（顺序与阶段划分见 `build_patch.py` 的文档头）：
`verify_text_map_structure` · `check_reset_state` · `apply_graphic_overrides` ·
`splice_restoration` · `remove_cnr_scripts` · `build_original_stage` · `apply_text_map` ·
`import_missing_voices` · `audit_choices` · `build_nametable` · `verify_text_map` · `final_verification`；
**引导步**（仅 `--bootstrap`）：`build_cg_map` · `build_restored_cgs` · `renumber_evcc9xxx`。

**目录分组**（`script/` 根只放入口与流水线步骤）：

- `script/internalize/` —— 把**上游产物搬进仓库**，各跑一次；跑完构建不再需要外部目录：
  `extract_carried_lng` · `extract_carried_soundlevel` · `extract_reused_archives`
- `script/gen/` —— **手工生成 `resource/` 的输入表**（产出的就是流水线输入）：
  `build_original_audio` · `build_voice_plan`
- `script/audit/` —— **只读检查与分析**（不改 `asset/`、不改 `resource/`）：
  `audit_inline` · `audit_lng_semantics` · `audit_duplicate_text` ·
  `verify_ws2_conventions` · `convert_wsc`

**库全在 `tool/`**：`tool/writer.py`（结构写盘器）、`tool/rename_map.py`（改名表）、
`tool/archprobe.py`（归档资源 / PNA 层数探针）、`tool/textplan.py`（表 → 写盘计划）。
⚠️ **流水线步骤不再互相 import** —— 原先库放在 `script/` 里，要靠 `sys.path.insert` 与
`importlib` 绕路加载（那正是尾标记事故的温床）。

**`audit_duplicate_text.py` 是"检测报告"，不是闸**：列**同一脚本内**出现完全相同显示文本的格
（长度 <8 的短句/纯符号句一律略过，跨脚本不比），并标注每格的出处。
⚠️ **它只检测、不判错，也不参与流水线的通过/失败** —— 同文本重复**是正常现象**：
短句与符号句（`……`／`“……”`）原版本就反复出现，姊妹场同对白更是既定设计，
切分片重合也正常。只有"**至少一条是自撰格**"的那些组值得人看一眼（在现表上实测：
90 组同文本，其中 1 组含自撰格 —— 经查是 Steam 自己连着两槽同样的笑声，正常）。

**`apply_text_map.py` = 落盘接线的入口**：按 `resource/text_map.json` **一次产出结构 + lng** ——
它是**表的唯一消费者**，取代了结构写盘的独立入口与 lng 生成的那一族。
以下脚本**只写 `.LNG`**、处理集全部落在表的 293 个范围内 ⇒ 输出必被 `apply_text_map` 覆写，
已从流水线摘除并**删除**：
`realign_lng_to_ws2.py`（DP 对齐）、`build_host_lng.py`（宿主 lng）、`fix_lng_alignment.py`（模型对齐）、
`fix_nametable_prefix.py`、`rename_speakers.py`、**判定链的 39 个 `textfix_*.py`**，
以及判定链的**下游**：`build_text_map.py`（表生成器）、`speaker_map_sheet.py`（对照卡）、
`build_position_overrides.py` / `calibrate_positions.py`（立绘位置标定，锚点输入已清）。
判定链的**中间层（`tmp/textfix/`）也随之清理** ⇒ 整条链**不可再跑**，且它本就**顺序敏感、表已成准据**
—— **要改某一格，改表**。
（`build_text_map.py` 里仍被复用的两条文本体例规则 `fix_tail` / `normalize_zh` 已移入 `tool/lng.py`。）
- 原理：原始槽位 `k` = ws2 里 `14` 与 `0f` 条目按指令序编号；表的 `items` 给出每格显示什么 +
  `insert`（`at_k`/`src_rows`）+ `drop`；结构先落（复用 `tool.writer.rebuild`，含跳转回写），
  lng 按**最终槽位序**逐个取文本。
- **指令排布照抄 Steam**：相邻的 `15`（`SetDisplayName`）**一律原样保留**（不得折叠），
  `drop` **删整格**（`14` + 它的设名 `15` + 清框 `15`，带「不改动任何存活格名字框」的守卫）
  —— 口径与守卫见 [file-formats.md](file-formats.md)「0x15」与 [wsc_to_ws2_conversion.md](wsc_to_ws2_conversion.md) [file-formats.md](file-formats.md)「字符串池」.11。
- **随行画面演出一起补**（演出专项评审的洞 1/洞 2）：`insert` 行补该行的**随行区间**、
  覆盖层登记的格补「**该说话人最后一次出图 → 本行**」，两者都用同一个转换器转出（坐标与改名一致）。
  表与判据见 [resource/README.md](../resource/README.md)「original_stage.json / text_map_stage.json」，
  生成器 `script/build_original_stage.py`（写盘阶段，`apply_text_map` 之前）。
- 逐脚本断言 `lng 条数 == 14 条数 + Σ(0f 条目数)`，并与表的 `n_final` 相等。
- 校验：`script/verify_text_map.py`（哨兵**七条**）＋ `script/final_verification.py`（全量验收）＋ 逐格内容比对；产出与表逐脚本相等。
- 试跑可加 `--rio <副本路径>`（不动 `asset/`）。

**结构写盘器（`tool/writer.py`）的三处硬要求**：

- **必须回写文件内跳转**。它在指定占位之后插 `15`+`14`，会平移其后的所有字节；而
  `06 <u32>`（无条件跳转）、`01 mode=0x85` 的 `b`、**选项条目里的 `06 <u32>`** 记的都是
  **文件内绝对偏移** —— 不回写就会让选项 / 双出口**跳错**（实测 `CCB0007` 的 3 个选项、
  `CCB2007` 的 2 个都是 `06`）。做法与 `splice_restoration.rebase_offsets` 同源：
  先记「旧指令偏移 → 新偏移」，再把每个跳转目标按该映射改写（目标不是指令边界时告警、不回写）。
- **插入点也允许落在 `0f` 条目之后**（=「选项块之后」）。例：`CCB0007` 的 `[engine-mechanics.md](engine-mechanics.md)「0x33 硬载入」7` 在**原版**是
  `[engine-mechanics.md](engine-mechanics.md)「0x33 硬载入」6→[engine-mechanics.md](engine-mechanics.md)「0x33 硬载入」7→[engine-mechanics.md](engine-mechanics.md)「0x33 硬载入」8` **连续**（原版该脚本**零选项**，`0f` 计数 = 0），Steam 把这段换成了 3 个「看内裤」选项
  —— 故 `[engine-mechanics.md](engine-mechanics.md)「0x33 硬载入」6`+`[engine-mechanics.md](engine-mechanics.md)「0x33 硬载入」7` 是「是否要看」的**前置铺垫**、选项在其后，`[engine-mechanics.md](engine-mechanics.md)「0x33 硬载入」7` 的语义位置在**选项块之前**
  （`after_k=51`，= 取 `[engine-mechanics.md](engine-mechanics.md)「0x33 硬载入」6` 的那格）。
- **指令排布照抄 Steam**：相邻的 `15` **不折叠**（旧实现"后者覆盖前者"会把原生清框压掉），
  `drop` **删整格**（`14` + 设名 `15` + 清框 `15`，带名字框守卫）。理由与守卫见
  [wsc_to_ws2_conversion.md](wsc_to_ws2_conversion.md) [file-formats.md](file-formats.md)「字符串池」.11。

回归类：`script/audit/audit_inline.py`（就地插入）、`audit_lng_semantics.py`（lng 语义）、
`verify_ws2_conventions.py`（转换器约定）；`audit_choices.py` 是**流水线步骤**（选项池位检查）。

转换器的规则、全量验证结果与实现陷阱见
[wsc_to_ws2_conversion.md](wsc_to_ws2_conversion.md)。

## 打包与发布

1. **`script/build_patch.py`** —— 按上述方案产出 `asset/` 下的完整文件（测试阶段直接用
   `asset/` 覆盖游戏目录即可，无需关心 `payload/`）。
2. **`script/generate_payload.py`** —— 对比 `asset/` 与 `backup/` 生成增量到 `payload/`：
   逐成员分类为 `keep`/`modified`/`added`/`deleted`，未变化的成员不重复打包；
   生成 `payload/METADATA.json`（`members` **按 asset 自身的成员顺序**排列 ——
   安装器按这张表重排归档，顺序错会导致安装后字节序不同、哈希校验失败）；
   ⚠️ **METADATA 只放归档条目**（`{归档名: {checksum, members}}`）、**不带任何 `_` 前缀元信息键** ——
   安装器的安装后校验把它的**每一个键**当归档名去游戏目录核哈希，多一个键就多一条假的「文件不存在」FAIL。
   末尾的**回读校验**用 `backup/` + `payload/` + METADATA 重放安装流程，
   核对结果与 `asset/` 逐字节一致。**这一步不能跳过或注释。**
3. **`bash script/pack.sh`** —— PyInstaller 打包 `tool/install.py`，
   产出 `releases/..._Installer_v{VERSION}.exe`。

> `asset/` 或 `backup/` 任一变动后**必须**重新生成 `payload/`。

## 遗留与风险

> ⚠️ **本表已停止维护** —— 仍开放的项已并入
> [restoration-targets.md](restoration-targets.md) 的「剩余工作」统一维护。下表仅存历史记录。

| 项 | 状态 |
|---|---|
| 实机测试 | `CCC0000` 试点已通过，其余 11 个场景待测；lng 重排处待确认（名字框显示已通过） |
| lng 语义复审 | Res303 自述 `TEXT_QA_PASS: false`；已确认的 6 条错配已修，全量复审待重开 |
| `ev` 槽特性 | stem 长度限制、前缀容忍度未实测（Steam 语料中未见 `0x34` 用 `ev` 槽） |
| 原版资源补入 | **不能**从原版二进制直接提取（引擎不兼容，见 [lessons-learned.md](lessons-learned.md) 「原版与 Steam 是两套引擎」）；语音是唯一的例外（OGG 通用） |
| `asset/*.arc` 在 Git LFS 下 | 已多次因 `.git/lfs/tmp` 堆积写满磁盘，根治（移出 LFS）待执行 |
