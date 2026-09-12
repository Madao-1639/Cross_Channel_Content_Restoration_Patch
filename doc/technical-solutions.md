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
- 实现：`script/build_rename_map.py` —— 规则解不掉的条目**单独列出，不许静默跳过**。

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
`script/audit_inline.py` 是回归守卫。

> **为什么不是「宿主截断 + 追加还原脚本」**：Steam 删 H 场景时会把该场景压成删节版留在
> 宿主里，而宿主是**按序跑完整场**的 —— 追加式会造成顺序倒置 + 局部重复；
> 且截断宿主会一并丢掉宿主承担的演出（立绘/BGM/SE）。详见
> [lessons-learned.md](lessons-learned.md) §18。

**资源引用**：插入段引入的原版资源名必须先跑重命名映射（见上）。
扩覆盖范围新增的语音由 `script/import_missing_voices.py` 从原版 `Voice.arc` 补入。

## 3. 文本汉化：lng 位置对应

- **机制**：lng 里没有 id，引擎按播放序把**第 N 条文本**替换为 lng 第 N 条目。
  严格等式 `lng 条数 == 14 条数 + Σ(0f 的 count)`（选项条目各占一槽）。
- **来源**：主线复用汉化组官方译文（CCS，`CCS 序号 = 原版 WSC 对话 id + 1`），
  Steam 新增的 7 条角色线与后日谈来自 Res303 的机翻。
- **宿主 lng 重建**：就地插入后宿主的位置全部变了，lng 必须整体重建 ——
  前缀沿用宿主原 lng 的前 `keep` 条，插入段按上述关系从 CCS 逐句生成，
  **尾部控制符照抄源文本**（`%K` / `%K%P` / `%N` / `%P`，不是一律 `%K%P`）。
  实现：`script/build_host_lng.py`。
- **池位错位修复**：`script/realign_lng_to_ws2.py` —— 当 CCS 把一句英文拆成两行中文时，
  按「中文几行就几行」给 ws2 插入 `15`+`14`（不是把多行中文并成一条 lng）。
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
| `tool/lng.py` | lng 编解码 + CCS 解析/去说话人包裹 |
| `tool/luac53.py` / `tool/luadis53.py` | Lua 5.3 字节码解析 / 反汇编 |
| `tool/install.py` | 安装器（PyInstaller 入口，`merge_arc` 按 METADATA 重组归档） |

### 流程脚本（`script/`）

```
convert_wsc.py          splice_restoration.py     build_host_lng.py
realign_lng_to_ws2.py   renumber_evcc9xxx.py      build_rename_map.py
import_missing_voices.py fix_nametable_prefix.py  fix_lng_alignment.py
build_patch.py          generate_payload.py       final_verification.py
pack.sh
```

回归类：`audit_inline.py`（就地插入）、`audit_coverage.py` / `audit_missing_content.py`
（覆盖与缺口）、`audit_choices.py` / `audit_lng_semantics.py`（lng 槽位与语义）、
`verify_ws2_conventions.py`（转换器约定）。

转换器的规则、全量验证结果与实现陷阱见
[wsc_to_ws2_conversion.md](wsc_to_ws2_conversion.md)。

## 打包与发布

1. **`script/build_patch.py`** —— 按上述方案产出 `asset/` 下的完整文件（测试阶段直接用
   `asset/` 覆盖游戏目录即可，无需关心 `payload/`）。
2. **`script/generate_payload.py`** —— 对比 `asset/` 与 `backup/` 生成增量到 `payload/`：
   逐成员分类为 `keep`/`modified`/`added`/`deleted`，未变化的成员不重复打包；
   生成 `payload/METADATA.json`（`members` **按 asset 自身的成员顺序**排列 ——
   安装器按这张表重排归档，顺序错会导致安装后字节序不同、哈希校验失败）；
   末尾的**回读校验**用 `backup/` + `payload/` + METADATA 重放安装流程，
   核对结果与 `asset/` 逐字节一致。**这一步不能跳过或注释。**
3. **`bash script/pack.sh`** —— PyInstaller 打包 `tool/install.py`，
   产出 `releases/..._Installer_v{VERSION}.exe`。

> `asset/` 或 `backup/` 任一变动后**必须**重新生成 `payload/`。

## 遗留与风险

| 项 | 状态 |
|---|---|
| 实机测试 | `CCC0000` 试点已通过，其余 11 个场景待测；名字框显示、lng 重排处待确认 |
| lng 语义复审 | Res303 自述 `TEXT_QA_PASS: false`；已确认的 6 条错配已修，全量复审待重开 |
| `ev` 槽特性 | stem 长度限制、前缀容忍度未实测（Steam 语料中未见 `0x34` 用 `ev` 槽） |
| 原版资源补入 | **不能**从原版二进制直接提取（引擎不兼容，见 [lessons-learned.md](lessons-learned.md) §11）；语音是唯一的例外（OGG 通用） |
| `asset/*.arc` 在 Git LFS 下 | 已多次因 `.git/lfs/tmp` 堆积写满磁盘，根治（移出 LFS）待执行 |
