# 汉化方案

## 概述

CROSS†CHANNEL Steam 版仅提供英文文本，需要实现简体中文汉化。本方案基于 Res 303 补丁的技术实现，复用已有的翻译成果。

## 核心技术

### LNG 文件机制

LNG（Language）文件是 AdvHD 引擎的文本替换机制，用于实现多语言支持。

#### 工作原理

1. **一一对应**：每个含文本的 `.ws2` 脚本对应一个同名的 `.lng` 文件
   - `CC0TOU_en.ws2` ↔ `CC0TOU_en.lng`
2. **运行时替换**：引擎播放脚本时，把**按出现顺序数到的第 N 条文本型指令**
   的文本替换为 lng 的第 N 条（见下文"位置对应"）
3. **存储位置**：lng 文件与 ws2 脚本同在 Rio.arc 中
4. **覆盖完整性（已核对）**：`asset/Rio.arc` 的 363 个 `.ws2` 配 **328 个 `.lng`**，
   每个 `.lng` 都有对应 ws2；35 个无 lng 的 ws2 **全部是无文本的系统/动画脚本**
   （`AN_*`、`*_ANIME_ERASE`、`EVRET`、`LAYER_ORDER`、`mainmenu`/`title`/`start` 等，
   实测 `0x14` 计数均为 0）；每个含文本的 ws2 都有配套 lng
5. **就地插入后宿主 lng 必须重建（2026-09-11）**：插入段的对话插进宿主中间，`keep` 之后的
   全部位置都变了，lng（位置对应）随之整体重建 —— 前缀沿用宿主原 lng 的前 `keep` 条，
   插入段取源 CCS（`CCS 行号 = 源 WSC 对话 id + 1`，`tool/lng.strip_speaker_wrap` 去引号，
   `tool/lng.encode_lng` 编码），**尾部控制符照抄源文本**（`%K`/`%K%P`/`%N`/`%P`，不是一律
   `%K%P`）。工具 `script/build_host_lng.py`；验收条件：lng 条数 == 该脚本的 `14` 条数。
   注意这 12 个宿主都**没有 `0f` 选项表**，所以「池位置」就等于「`14` 出现序」；
   若有选项表，lng 条数还要加上选项条目数（见上文「位置对应」）。

#### LNG 文件格式（`tool/lng.py`）

```
[Header]
  uint32  count                 // 条目数
  uint16  length[count]         // 每条的字节长（含结尾 NUL 对 00 00）
[Payload]
  count 条字符串背靠背，无对齐填充
```

- **单条字符串 = UTF-16LE 编码，每字节再 XOR 0x2C**。容器本身**不做 rot6**。
- 终端 `%K`/`%P`/`%N` 等控制标记以普通文本形式随条目携带（如 `终于说出话来。%K%P`）。
- 工具：`tool/lng.py` 提供 `parse_lng` / `encode_lng` / `parse_ccs` / `strip_speaker_wrap`，
  全量可解析、往返编码一致。

#### 匹配机制：位置对应（对还原管线至关重要）

- lng 中**没有 id 字段**。引擎按播放顺序把第 N 条文本替换为第 N 条目。
- **严格等式：`lng 条数 == `14` 条数 + Σ(0f 的 count)`**。`14` 与 `0f` 的每个条目**各占一个
  槽位**（一张 `0f` 表的 `count` 个选项条目各占一槽，`strid` 即槽位序），不是整张表占一槽；
  占位行（`%N`/`%P`）同样消耗条目。
  **全量核对已通过** —— `script/final_verification.py` 的 `check_lng_pairing()` 对 328 个
  配对逐一验证条款等式，无一例外。
- **lng 与 ws2 的池位可能整体错开**：Res303 的 lng 是按**汉化组 CCS 的行序**抄的，
  而 CCS 会把一句英文拆成两行中文。`script/realign_lng_to_ws2.py` 按「中文几行就几行」
  给 ws2 插入 `15`+`14` 来校正。详见 [lessons-learned.md](lessons-learned.md) §21。
- **说话人名（`0x15 %LC<Name>`）不经 lng 替换** —— 条目数核对证明 lng 只覆盖 `0x14` 与
  选项文本。人名走**另一条通路**：引擎拿 `%LC<英文名>` 去查 `Rio.arc` 里的
  `NameTable.txt`（UTF-16LE）。详见下文「角色名称映射」。
- **对还原管线的影响**：位置对应意味着 ws2 的对话流一旦增删，既有 lng 全部错位。
  就地插入后宿主的位置全部改变，配套 lng 必须**整体重建**（见下文实施步骤 5）。

#### 汉化实施步骤

1. ✅ **格式逆向与编码确认**：UTF-16LE 后整体 XOR 0x2C（`tool/lng.py`）
2. ✅ **工具开发**：`parse_lng` / `encode_lng` / `parse_ccs` / `strip_speaker_wrap`
3. ✅ **池位对齐**：328 个配对全部满足 `lng 条数 == 14 条数 + Σ(0f 的 count)`；
   Res303 遗留的漏行 / 错位已由 `script/fix_lng_alignment.py` 与
   `script/realign_lng_to_ws2.py` 修正
4. ✅ **宿主 lng 重建**：就地插入后宿主 lng 整体重建 —— 前缀沿用宿主原 lng 的前 `keep` 条，
   插入段按「CCS 序号 = 对话 id + 1」逐句生成，**尾部控制符照抄源文本**
   （`%K` / `%K%P` / `%N` / `%P`，不是一律 `%K%P`）。实现 `script/build_host_lng.py`，
   规则见 [wsc_to_ws2_conversion.md](wsc_to_ws2_conversion.md) §3.1
5. ⏳ **语义抽查**：抽样核对 lng 条目与场景对白（Res303 自述的 6 条错配已修复）

### 翻译来源

Res 303 的翻译来自两个来源：

1. **主线剧情**：复用 [CROSS†CHANNEL 中文化项目](https://github.com/MewX/cross-channel_chinese-localization_project)（MewX 等）的官方译文
   - 质量较高，人工翻译
   - 对照文本位于 `../cross-channel_chinese-localization_project/Scripts/20150412/`：
     - **格式**：293 个 `.CCS` 文件（`CCA0001.CCS`、`CCC0000.CCS` 等，与原版 WSC 脚本同名对应；`TXT/` 子目录另有 293 个同名 `.TXT` 为纯中文译文，**GBK** 编码，内容与 `.CCS` 的 `>1●` 中文行一致）`.CCS` 为 UTF-16LE + CRLF 纯文本，日中逐行对照：
       ```
       >0○0001○最古の記憶は。          ← 0 行：日文原文
       >1●0001●最久远的记忆，          ← 1 行：中文译文
       ```
     - **编号关系（已全语料验证）**：CCS/TXT 的 `NNNN` = **原版 WSC 对话 id + 1**。41,629 句中 41,628 句逐字匹配、顺序完全一致（唯一差异是 CCA0002 一行 staff-roll 的空白），详见 [wsc_to_ws2_conversion.md](wsc_to_ws2_conversion.md) §2。
     - **用途**：lng 文本校对信源；转换器产物的配套 lng 可按 `CCS 序号 = 对话 id + 1` 直接从 CCS 的 `>1●` 行逐句生成
2. **Steam 版新增内容**：7 条角色线与后日谈由 Res 303 作者用 GPT-5.6-sol 翻译
   - 机器翻译，质量待评估
   - 可能需要人工校对

### 翻译策略

1. **直接复用**：完整复用 Res 303 的 lng（328 个配对，池位对齐已全量校验通过）
2. **质量评估**：后续评估机翻部分的质量，标记需要改进的条目
3. **增量更新**：可根据用户反馈逐步改进翻译

### 质量状态：Res303 的对齐审计不通过，本项目已重开复审

Res 303 的自述审计判定 `TEXT_QA_PASS = false`，理由是旧的对齐/复核队列存在系统性缺陷：
**等数量文件按序复用**（两侧句数相等时直接按顺序 zip，不进入复核队列）、
**相似度阈值免审**。其报告列出 6 条人工确认的语义错配，本项目独立复核后**全部属实**：

| 位置 | ws2 英文 | lng 中文 | 判定 |
|------|---------|---------|------|
| `CCA0025C_en.ws2` #16 | `That's because...I thought you looked lonely by\nyourself...` | `“……啊……啊啊啊……”` | 语义无关 |
| `CCA0025C_en.ws2` #37 | `I changed my mind and gave everything up.` | `呀，唔呜……痛、痛……要掉、要掉了！` | 语义无关 |
| `CCB2013_en.ws2` #60 | `Wait, maybe it was because I tried to visualize it...` | `我想要尽情地做爱！` | 语义无关 |
| `CCB2101_en.ws2` #21 | `Like a cactus.` | `在冬子的舌技够不着的那个地方，括约肌紧紧地收缩了。` | 语义无关 |
| `CCD0022A_en.ws2` #93 | `Had they noticed it?` | `其实，是我命令曜子需如实禀明高潮的次数。` | 语义无关 |
| `CCD5001A_en.ws2` #448 | `She stood there.` | `我也是，她也是。` | 语义无关 |

这 5 个脚本正是就地插入时重建过 lng 的宿主，**6 条错配已随之修复**。
其余按「等数量 + 子序列 ⇒ 逐条恒等」的模型重对齐了 101 个脚本的 lng
（模型与边界见 [lessons-learned.md](lessons-learned.md) §23）。

**要求**：

- 低相似度分数只是**复审优先级的信号，不等于错译**；反过来，等句数与高相似度也**不能**
  批准映射。语义对位必须回到可靠锚点：`CCS 序号 ↔ 对话 id` 的位置对应，
  以及 [wsc_to_ws2_conversion.md](wsc_to_ws2_conversion.md) 的
  **全区间偏移扫描 + 全量比对**方法。
- 池位对齐（条数与连续性）已全量通过；**语义抽查仍待做**。

## 字体处理

### 问题描述

Steam 原版字体不含汉字字形，需要替换为支持中文的字体。

### 字体文件（已核对）

**Steam 原版 Fonts.arc**：
- `FOT-MatissePro-B.PTF`（粗体）：7.6 MB（7.28 MiB）
- `FOT-MatissePro-M.PTF`（中等）：7.0 MB（6.71 MiB）

**Res 303 Fonts.arc**：
- `FOT-MatissePro-B.PTF`（粗体）：18.5 MB（17.60 MiB）
- `FOT-MatissePro-M.PTF`（中等）：18.5 MB（17.60 MiB）

**文件大小增加**：约 2.4–2.6 倍，即扩展了字符集以覆盖汉字。

### PTF 格式（已部分识别）

PTF **不是纯自定义黑盒**：对文件主体做逐字节 XOR 0x2C 后，偏移 5 处出现
OpenType/CFF 的 **`OTTO`** 签名，其后 `numTables=15`、`searchRange=0x80` 与
OpenType 头部字段吻合，表目录区域可见 `BASE`/`CFF `/`GPOS`/`GSUB`/`VORG`/
`cmap`/`head` 等标准表标签。即 **PTF ≈ 5 字节自定义前缀 + XOR 0x2C 混淆的
OpenType 字体**；封装细节（前缀语义、表目录未完全对齐）未逆向完毕。

**处理方式**：
- 直接复用 Res 303 的字体文件，无需自制
- 如需自制中文字体，可按上述封装思路打包 OpenType 后实测

### 实施步骤

1. **字体文件替换**：用 Res 303 的 Fonts.arc 替换 Steam 原版
2. **显示测试**：测试中文字符显示效果

## 系统界面汉化

### Script.arc（Lua 脚本）

Res 303 新增了 Script.arc（Steam 原版不包含），实现图形化系统界面。

#### 文件列表（14 个）

| 文件名 | 用途 | 汉化相关性 |
|--------|------|-----------|
| `menu_config.lua` | 设置菜单 | ✅ 包含界面文本 |
| `menu_gallery.lua` | 画廊菜单 | ✅ 包含界面文本 |
| `menu_base.lua` | 菜单基础框架 | ✅ 包含界面文本 |
| `ui_language.lua` | 语言设置 | ✅ 多语言支持 |
| `ui_button.lua` / `ui_GaugeBar.lua` / `ui_scrollbar.lua` | UI 组件 | 可能包含文本 |
| `GameInfo.lua` / `VersionInfo.lua` | 游戏信息 | 可能包含文本 |
| `LegacyGame.lua` / `LegacyGame_utf8.lua` | 游戏主逻辑 | 可能包含文本 |
| `ArcFileName.lua` / `VariableSize.lua` / `LegacyGame.inc` | 配置文件 | 不包含文本 |

#### 实施步骤

1. **直接复用**：完整复用 Res 303 的 Script.arc
2. **文本提取**：从 Lua 脚本中提取界面文本（如需改进翻译）
3. **功能测试**：测试菜单、设置、画廊等功能是否正常

### SysGraphic.arc（系统界面图片）—— 本补丁**不做**

**决定（2026-09-11）**：系统界面汉化涉及图片处理，现阶段**暂不处理**，
**沿用 Steam 原版 SysGraphic.arc** —— 本补丁不再分发该归档（安装器保留玩家原文件），
`script/final_verification.py` 已把它列入 `OPTIONAL`。

以下为当初的核对结果，留作后续若要处理时的参考：

**已核对**：Steam 原版与 Res 303 的 SysGraphic.arc 成员名集合完全一致（30/30），
其中 **19 个成员内容被替换**——即 Res 303 通过同名换图完成系统界面图形化汉化：

`SYS_AUTO` `SYS_GalleryBGM` `SYS_GalleryBase` `SYS_GalleryCg` `SYS_GalleryReplay`
`SYS_MSW_N` `SYS_SKIP` `Sys_BackLog` `Sys_Dialog` `Sys_Msw` `Sys_Title`
`sys_config_P1/P2/P3/P4/P4TH/P6/P7` `sys_config_base`

其余 11 个成员逐字节相同。

**实施步骤**：
1. **直接复用**：整体替换为 Res 303 的 SysGraphic.arc
2. **显示测试**：系统界面显示效果

## 角色名称映射

### 三语对照（已从三方数据实证）

- **日文名**取自原版 WSC 的 0x42 说话人字段（游戏自带数据）；
- **中文名**取自汉化组 CCS 的 `>1●` 行 `[...]` 前缀（与 WSC 说话人逐句配对， 33 个名字全量提取，频次与 WSC 侧完全一致）；
- **英文名**取自 Steam ws2 的 `%LC` 字段；
- **语音前缀**取自原版语音文件名前三字母（与随后说话人逐句配对，10,693 条）；
- **通道**取自 Steam `2e` 语音指令的 charXXX（与随后 `%LC` 配对）；
- **立绘 PNA 前缀**取自原版 0x48 立绘名前四字符（与同剧本语音前缀共现验证，全部 10 个主前缀以显著优势收敛）。

| %LC（Steam） | 日文 | 中文（汉化组） | 语音前缀 | Steam 通道 | 立绘 PNA |
|---|---|---|---|---|---|
| Taichi | 太一 | 太一 | （主角无专用语音） | — | — |
| Misato | 見里 | 见里 | msa | charMIS | TCMM |
| Miki | 美希 | 美希 | mki | charMIK | TCYM |
| Kiri | 霧 | 雾 | kri | charKIR | TCSK |
| Touko | 冬子 | 冬子 | fyu | charTOU | TCKT |
| Tomoki | 友貴 | 友贵 | yki | charTOM | TCST |
| Youko | 曜子 | 曜子 | you | charYOU | TCHY |
| Nanaca | 七香 | 七香 | nnk | charNNK | TCCN |
| Sakuraba | 桜庭 | 樱庭 | sku | charSAK | TCSH |
| Yusa | 遊紗 | 游纱 | ysa | charYUS | TCDY |
| Shinkawa | 新川 | 新川 | shi | charSIN | TCSY |
| Miyuki | みゆき | 美幸 | myk | charMIY | — |
| Matron | ママン | 岳母 | mmn | charOBA | — |
| Lunch Lady | おばちゃん | 阿姨 | （同 mmn） | charOBA | — |

### 次要说话人（33 名中的其余，同源提取）

| %LC | 日文 | 中文 |
|---|---|---|
| Voice | 声 | 一个声音 |
| Taichi | 俺 | 我 |
| Both / All 3 / All 4 | 二人 / 三人 / 冬子･見里･美希･友貴 | 二人 / 三人 / 冬子･见里･美希･友贵 |
| Stomach | 腹の虫 | 肚子里的虫 |
| Hayasugi | 曜子先生 | 曜子老师 |
| Dead Tomoki | 友貴の死体 | 友贵的尸体 |
| Club President | 部長 | 社长 |
| Kiri/Taichi | 霧・太一 | 雾・太一 |
| Karade Master | 老カラデ家 | 老控手道大师 |
| Masamune / Yutaka / Juuzou | 政宗 / 豊 / 重蔵 | 政宗 / 丰 / 重藏 |
| Woman | 女 | 女声 |
| Girl / Boy | 少女 / 少年 | 少女 / 少年 |
| ??? / ?? | ＊＊ / ＊ | ＊＊ / ＊ |

完整转换器侧的日文→英文映射见 `tool/wsc2ws2.py` 的 `SPEAKER_MAP`
（其中 Matron/Lunch Lady/Hayasugi 等英文名为低置信度推断，待实机校对；
中文名以上表为准，无置信度问题）。

**语音通道的权威规则**（原生语料实证，`char + 语音名前 3 字母`）：

Steam 侧**自己的**语音名（`MIK_0686.OGG` 等）满足 `通道 = char + 前三字母`
（`MIK→charMIK` 2818 次、`MIS→charMIS` 2810 次、`KIR→charKIR` 2417 次…… 全语料零反例）。
但**原版**语音名前缀是**日文名首字母**（`MSA`=見里、`FYU`=冬子、`YKI`=友貴、`MMN`=ママン），
与 Steam 通道名（`charMIS`/`charTOU`/`charTOM`/`charOBA`）不是同一个字母序列 —— **不能套规则，
必须查表**。转换器侧的实现见 `tool/wsc2ws2.py` 的 `VOICE_CHANNEL`。

> ⚠️ Res303 给全部还原脚本一律写 `charCN`，而**原生语料里没有这个通道名**。
> 现资产按角色查表（`script/splice_restoration.py`），用到的通道全部在原生语料里真实存在。

### 角色名的实现方式：`NameTable.txt`

**结论：角色名走引擎的名字替换表，不改 ws2 字节。**
表是 **UTF-16LE**，任意简体字都能写 —— 完全绕开脚本内窄字节的 CP932 限制。

机制、格式与判定依据见 [engine-mechanics.md](engine-mechanics.md)「名字替换表」与
[lessons-learned.md](lessons-learned.md) §9。要点：

- 脚本里 `15 %LC<英文名> 00 00` 的 `%LC` 是**引擎命令标记**；引擎拿 `%LC<英文名>`
  去查 `Rio.arc` 里的 `NameTable.txt`，命中就替换成表右列。
- **表键必须与脚本标记一致** —— ASF 官方简中即如此（脚本 `%LF` ↔ 表 `%LF`）。
- 本项目资产里那张表承自 Res 303，73 行**内容是对的**（与脚本 73 个 `%LC` 名字
  一一对应、零偏差，中文也是简体），**但前缀写成了 `%LR`**，与脚本的 `%LC` 对不上，
  引擎查不到 ⇒ 名字框一直显示英文。
  **已修正**：`script/fix_nametable_prefix.py`（146 处，备份 + 回读校验 + 幂等）。

**为什么不能让 `%LC` 直接写中文**：引擎对**脚本内窄字节**按 **CP932** 解码
（见 [engine-mechanics.md](engine-mechanics.md)「文本编码路径」），
简体专用字（见/雾/贵/樱/游/纱/师/长/丰/咪…）不在 CP932 里，写不进去。
所以文本汉化必须走宽字符通路：正文/选项走 `.lng`，人名走 `NameTable.txt`。

字体侧无碍：Res303 替换的 `Fonts.arc` 配 `CharSet = GB2312_CHARSET` 已能渲染简体 ✓

### 映射表的构成（73 行，落地形式为 `Rio.arc` 的 `NameTable.txt`）

除上面两张主表外，还有三批补充，来源各不相同：

| 批次 | 例子 | 依据 |
|---|---|---|
| **机械派生** | `(Taichi)`→太一、`Taichi's Voice`→太一的声音、`Miki/Kiri`→美希・霧、`Dead Misato`→見里の死体、`Taichi (Delusional)`→太一（妄想） | 由基表按括号/斜杠/后缀组合 |
| **从台词中文反推** | `Poko`→波可（"那只狗就改叫波可"）、`Akira`→阿基拉（"谢谢你，阿基拉"）、`Michiru`→美知留、`Fukuhara`→福原、`Tomokichi`→友吉、`Misako`→美佐子、`Usamimi`→兔耳 | 这些角色只在 **Steam 独有剧情**里出现（无源 WSC 可配日文名），但**台词本身点名了中文写法** —— 取同名英文台词对应的 lng 中文 |
| **泛称/非人名** | `Dr. Oldman`→老医生、`Vet`→兽医、`Student`→学生、`Male Student A`→男学生A、`Female Students`→女学生们、`Anatomy Model`→解剖模型、`Announcement`→广播、`All`→全体、`All together`→大家、`Scream`→尖叫、`Attacker`→袭击者、`Recording`→录音、`Shark`→鲨鱼 | 语义直译 |

`Dr. Oldman` 的语境：台词里他自称**老夫**、被误认成女性、名字是「剖腹产薰」——
映射取泛称**老医生**（对应英文 `Dr. Oldman`），不提前剧透真名。

**已无「表外名字」**：`Mimi`(78) / `Mimimi`(17) 的台词中文是「咪咪」/「咪咪咪」，
已按简体写入。

### 实施步骤

1. ✅ **映射表建立**（三方数据实证，见上文两表）
2. ⏳ **一致性检查**：对照汉化组 CCS 与 Res 303 lng，确保人名在全部文本中一致
3. ✅ **资源名映射**：语音前缀 / 通道 / PNA 前缀三套代码体系已核实，可直接用于资源定位
   （`tool/wsc2ws2.py` 的 `VOICE_CHANNEL`）
4. ✅ **名字落地**：经 `NameTable.txt` 注入（前缀已修正），**待实机验证显示效果**

## 与脚本转换管线的衔接

就地插入宿主时，汉化需同步处理：

1. **宿主 lng 整体重建**：`0x14` 与选项条目各占一个池槽位，位置对应 ⇒ 插入后其后所有条目
   全部错位，必须重建（`script/build_host_lng.py`）。
2. **文本来源**：转换器逐句保留 WSC 对话 id，按「CCS 序号 = id + 1」从汉化组 `.CCS` 的
   `>1●` 行取中文；**尾部控制符照抄源文本**（`%K` / `%K%P` / `%N` / `%P`）。
3. **人名**：ws2 里的 `%LC<英文名>` **保持原样**；中文由 `Rio.arc` 的 `NameTable.txt` 替换。
4. **字体**：复用 Res303 的 `Fonts.arc`（已含汉字字形）。

## UI 汉化优先级

项目专注于游戏流程，UI 汉化优先级较低：

### 高优先级（必须）

- ✅ 剧情文本汉化（lng 文件）与池位对齐
- ✅ 字体支持（Fonts.arc）
- ✅ 角色名汉化（`NameTable.txt`）

### 中优先级（建议）

- ⏳ 语义抽查：抽样核对 lng 条目与场景对白
- ⏳ 菜单文本润色

### 不做（现阶段）

- ❌ 系统界面图片汉化（`SysGraphic.arc`）—— 涉及图片处理，沿用 Steam 原版
- ❌ 帮助文档汉化

> 验收标准见 [acceptance-criteria.md](acceptance-criteria.md)「汉化」一节，此处不再重复。
