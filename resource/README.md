# `resource/` —— **能改变产物的输入**与内化数据

流水线只**读表**，不各自硬编码一份映射。本目录就是那些表的唯一来源。

**两条操作规则**：

1. **进本目录的东西要么是"仓库内无法再生的输入"，要么是"内化数据"** —— 凡是能从仓库里已有的东西
   现算出来的（归档成员、缓存），一律**不落副本**、改由消费方直读源（见 [lessons-learned](../doc/lessons-learned.md)「派生缓存」）。
2. **改了本目录的表 ⇒ 必须重跑一轮**（表是写盘的唯一依据，`asset/` 会立刻与它不一致）。
   **既没有"输入指纹"、也没有出处元信息**：`METADATA.json` **只放归档条目**（多一个 `_` 前缀的键，
   安装器的安装后校验就会把它当归档名、报一条假的「文件不存在」）。逐文件哈希**刻意不钉** ——
   钉不住的输入太多（`backup/`、仓库外的原版归档、ffmpeg 版本），只会给出假保证；
   产物完整性由逐归档 `checksum` + 回读校验负责。

## 索引

| 文件 | 角色 | 消费方 |
|---|---|---|
| `text_map.json` | **文本映射表**（写盘的唯一依据） | `apply_text_map` / `verify_text_map*` / `check_reset_state` |
| `text_map_vc.json` | 逐格**覆盖**：名字框 / 删借用语音 | `apply_text_map` |
| `text_map_stage.json` | 演出覆盖层（洞 2，手工 30 格） | `apply_text_map` / `build_original_stage` / `tool.writer` |
| `text_map_stage_verdicts.jsonl` | 上表的**来源台账**（30 行，写盘不读） | （无消费者，纯溯源） |
| `scene_slices.json` | 还原场景的**插入范围**（11 场景 + 1 截头宿主） | `splice_restoration` / `build_cg_map` / `check_reset_state` |
| `cg_map.json` | 事件 CG 的**改名**映射（36 改名 / 37 全量） | `rename_map` / `build_restored_cgs` / `build_cg_map` / `renumber_evcc9xxx` |
| `speaker_map.json` | 角色名称 + 语音通道 + 立绘前缀（**唯一来源**，90 条） | `build_nametable` / `wsc2ws2` / `speaker` / `tool.writer` / `final_verification` |
| `original_stage.json` | 洞 1：插入行的**随行画面演出**（派生，237 脚本） | `apply_text_map` / `build_original_stage` |
| `original_audio.json` | 逐行**原版演出属性**（语音 / SE，237 脚本） | `apply_text_map` / `build_original_stage` |
| `voice_plan.json` | **补挂语音**计划（27 脚本；键 = 表的 `k`） | `apply_text_map` / `import_missing_voices` |
| `position_overrides.json` | 立绘 x 覆盖表（33 条；不符合 `steam_x()` 的关系） | `wsc2ws2` |
| `portrait_position_overrides.json` | 场景级立绘位置覆盖（手工，1 条） | `wsc2ws2` |
| `original_scope.json` | 原版内容覆盖的**剧本范围**（边界定义） | （无代码消费者） |
| `graphic_overrides/` | **图片汉化**替换清单 + 图（3 张） | `apply_graphic_overrides` |
| `corpus/wsc/` | **原版 WSC 语料**（324 个） | 见下「corpus」 |
| `carried_lng/` | 表外**汉化 lng**（35 条，从上游内化） | `build_patch.carry_carried_lng` |
| `carried_soundlevel.json` | 补入录音的**音量包络**（967 条） | `build_patch.carry_carried_soundlevel` |
| `reused_archives/` | **完全复用**的上游归档（字体 / Lua 界面） | `build_patch.bootstrap_copy` |
| `ws2_operand_formats.json` | WS2 全部 164 个 opcode 的格式串（参考表） | （无代码消费者） |
| `wsc_handlers.json` | 原版 WSC 的 256 项 opcode→handler（参考表） | （无代码消费者） |
| `icon.ico` | 安装器图标 | `script/pack.sh` |

> **除 `README.md` 与 `icon.ico` 外，本目录每一项都是能改变产物的输入** —— 改任何一项都要重跑一轮。
> 标「无代码消费者」的四项是**结论记录**（边界 / 格式表 / 溯源台账），列出只为让本清单完整。

---

## 文本

### `text_map.json` —— **写盘的唯一依据**

**293 个脚本 / Σ`n_steam` 41,923 格 / Σ`n_final` 42,834 格**（91 个脚本的格数变了）。

```jsonc
"CCC3027_EN": {
  "ccs": "CCC3027", "n_steam": 808, "n_final": 808,
  "items": [
    {"k": [0, 83], "op": "ccs", "row0": 1, "src": "verdict"},           // 连续段：行号 = row0 + (k - k0)
    {"k": 143, "op": "ccs", "row": 127, "span": [0, 18], "src": "s3"},  // 切分：取该行的第 0..18 字
    {"k": 18, "op": "text", "zh": "抬头望向天空。%K%P", "src": "newtrans"},
    {"k": [657, 669], "op": "ctrl", "zh": "%N"},
    {"at_k": 83, "op": "insert", "n": 118, "src_rows": [86, 203], "scene": "CCC3027"} ]
}
```

- **一个脚本一份**，以 **Steam 槽位序**为准，`items` **依次处理**即得最终脚本；
- `k` = **Steam 原始槽位**（`14` 一格、**每个 `0f` 条目也各占一格**），`n_final` = 最终占位数；
- 六种 `op`（当前用量）：`ccs` 2,020（取民汉官方中文）/ `text` 569（新译或沿用）/
  `ctrl` 516（控制格）/ `opt` 96（选项）/ `insert` 1,095 格（还原插入，配 `scene_slices.json`）/
  `drop` 185 格 / `split` 1（一格拆多格）；
- `span` 给的是**字符下标**而不是拆好的文本 —— 评审一眼能看出这格**是复用原行的某一段**而非新译；
  `op=text` 才是「没有现成中文可用」；
- **`split`**：`{"k":…, "op":"split", "row":…, "at":[切点…], "src":…}` = 源行 `row` 按**字符位**切成
  `len(at)+1` 格（第 1 片替换输入格 `k` 本身，其余各片作**插入格**紧随其后 —— 无独立语音/演出，
  是同一句的续页；尾标记按源行补）。用于「一句话放不进一个文本框」的排版拆格 —— 让表**产出**该结构，
  而不依赖基线里"拆格已完成"的旧状态。已在 `CCA0002_EN` k=193（CCS 行 194 切在 24 字）。
  ⚠️ 改这类"减少一个输入格"的表编辑时，**其后所有项的 `k`（含 `at_k`）要整体 −1**，`n_steam` 相应改。
- `src` 记录该结论来自哪一层判定（`pin`/`anchor`/`verdict`/`s3`/`branch`/`zhfill`/`newtrans`…），供溯源，写盘不需要；
- `tail`（可选）= **人工追加的尾部标记**，写盘时直接接在该格文本末尾。用于**排版**：Steam 骨架那种
  「连续一屏不清框」的排版（日记/笔记本整页），**中文比英文长**会造成一屏装不下、后面几句整段显示不出来
  —— 在该格尾补一个 `%P` 就把它断成两页。**不改槽位、不改格数**。已用：`CCA0012_EN` k=26、
  `CCC0007_EN` k=75（姊妹场同一段日记）。

**判定链已不存在，本表是准据**：生成它的那套覆盖层（`textfix_*`）与中间数据**均已清理**，
**不可重跑**。**要改某一格就改本表**，然后跑 `apply_text_map.py`。

**三道校对**（写完盘后）：`verify_text_map_structure.py`（只看表：格数 / 覆盖 / 行序 / span）→
`verify_text_map.py`（表 ↔ 产物）→ `final_verification.py`（全量）。
另有一条**结构不变量**断言（对话格必带尾部控制符、选项格必不带，两版语料零例外）——
机制与实测见 [file-formats.md](../doc/file-formats.md)「对话文本标记」。

### `text_map_vc.json` —— 逐格**覆盖**

`{脚本: {"vc": {槽位: 名字框}, "del2e": [语音文件名]}}`。按台词来源**自动推导**的名字框
（旁白清空、说话人经 `speaker_map` 映射）由 `apply_text_map` 自己算；本表只放**人工否决**：
写裸名（→ `%LC <名>`）、写空串（清空名字框）、或列要删的借用语音。

### `text_map_stage.json` / `text_map_stage_verdicts.jsonl` —— 演出覆盖（洞 2）

`text_map_stage.json` = `{脚本: {槽位k: 原版源行}}`（`_说明` 键为自述）—— 登记「**台词取自原版、
但 Steam 骨架没把说话人的立绘摆在屏上**」的格，共 **30 格 / 14 个脚本**。

`text_map_stage_verdicts.jsonl` = 它的**来源台账**（30 行，逐行与那 30 条一一对应，实测 0 处不符）：

| 字段 | 含义 |
|---|---|
| `stem` / `k` / `row` | 脚本 / 槽位 `k` / 原版源行号 —— 即上表的键值对 |
| `verdict` | `skeleton-issue`（21）／`skeleton-issue-empty`（9，清层句柄修正后该格变空屏的变体） |
| `why` | 裁定理由 |
| `source` | 判定来源：`agent_c`（8）／`r30-corrected-addition`（13）／`r30-corrected-empty`（9） |
| `orig_chars` / `prod_chars` / `speaker_code` | 原版 / 产物在该行**在屏的角色**与该行说话人 —— 仅 22 行带 |
| `fin` | 该脚本的最终槽位数（定位用） |

⚠️ 台账**不参与写盘**（没有任何代码读它）；
要改演出请改 `text_map_stage.json`。

### `original_stage.json` / `original_audio.json` —— 插入行的随行演出

`text_map.json` 的 `insert` 原先只发 `15+14(+2e)`，**原版随行的画面与声音演出没带** ⇒
「有台词、画面却是空屏或别人」。两张表补上：

- **`original_stage.json`**（画面）—— `{CCS名: {原版行号: 十六进制字节}}`。对每个插入行的
  **随行区间**（上一句结束 → 本句开始）跑 `tool.wsc2ws2.convert_range` —— **与就地插入同一套转换器**，
  所以坐标适配（`steam_x()`）、PNA/CG 改名、`39` 帧号形态全部一致；只留**演出**
  （画面 `33/34/37/39/66`、音 `1e/1f/0b/28`、等待 `11/12`），**剔掉**文本/语音/选项/跳转。
  生成：`script/build_original_stage.py`（写盘阶段，`apply_text_map` 之前）。
- **`original_audio.json`**（声音）—— 原版**每行**的语音/SE 属性。正文取自原版的格，若该行
  **原版没有配音**，就删掉格上的 `2e`（Steam 把原版的旁白改写成台词并配了音，正文换回原版后
  那声音就成了别人的）。⚠️ **只做行级演出属性**；立绘/BGM/计时器/跳转等**跟随 Steam**。
  生成：`script/gen/build_original_audio.py`。

### 洞 2 的区间算法与洞 1 不同

原版在这些行**没有再发 `48`**（那张立绘从更早某行起一直挂在屏上直到本行都没撤，如 `CCC4014` 的雾），
所以补的是「**该说话人最后一次出图的那一条 `48` 本身**」—— 把原版当时在屏的那张立绘重新摆上。
**只取那一条 `48`、不取「它到本行」的整段**：整段里夹着原版十几行的等待与别的演出，照搬会把它们
全挤到本格之前、打乱节奏。

> 三个共同点：① 只保留**演出**，文本/语音/选项/跳转由写盘器另行处理；
> ② 立绘槽位判定（`portrait_slots`）必须用**整脚本**的指令流（`ConvertOptions.slot_context`）——
> 只看切片那几条会把「同框第二张」判成主槽；
> 目标资源不存在的**不发射**（`emit_portrait_block` 的防御），绝不产出悬空引用。
> ③ **立绘槽位要与骨架对齐**（否则同一角色两个槽同屏 = 重叠）：
>   - **沿用骨架的槽**，但**只在骨架对该角色只用唯一槽时**才沿用（`family_slots`；多槽/骨架没有
>     该角色时**不猜**——「最常用」之类是无根据的启发式）；
>   - 其余情况由写盘器 `fix_injected_slots()` 在**流式重建**时补「**换槽前先清另一个槽**」
>     （同族此刻还绑在别的槽上就先发 `37 <那个槽>`）。实测 `CCA0015` 見里被骨架摆在 `st07`
>     而注入落 `st03` ⇒ 两个見里同屏；用这两条后撞车从 19 处降到 **0**。
>   - 清槽必须**跨块**：注入块摆的立绘可能一直挂到**后面某个骨架 `34` 才改绑同族**（此时才撞车）
>     —— 所以重建时**不只处理注入块内部的 `34`，骨架自己的 `34` 也要清「注入来源」的同族旧槽**
>     （`bound` + `inj` 两个状态：只有**注入块摆的**槽才会被清）。实测 `CCA0030` 見里：洞 2 注入落
>     `st05`、骨架随后改绑 `st03`、`st05` 那张一直没撤 ⇒ 两个見里同屏（这正是「从某句起立绘不再
>     消失、且不切换」的成因）。
>     ⚠️ **只清注入来源**：骨架原生的同族双槽（`backup` 全库 6 处，如 `CCA0002` 的 `st01+st03`）
>     是原生行为，**一律不动**。

## 范围与资源

### `scene_slices.json` —— 还原场景的插入范围

```jsonc
{ "order": ["CCA0025C", ...],                    // 剧情顺序（= scenes 的先后）
  "scenes": [{"host": "CCC3027_en.ws2", "src": "CCC3027",
              "lo": 85, "hi": 808,                // 源 WSC 对话序号（含端点）
              "head_overlap": 0, "keep": 84}],    // 宿主开头重合块 / 前段保留对话数
  "tail_hosts": [{"host": "CCD5001B_en.ws2", ...}] }   // 截掉与插入段重合头部的宿主
```

**11 个插入场景 + 1 个截头宿主 = 12 个就地插入宿主**。
消费方：`splice_restoration`（就地插入）、`build_cg_map`（推导 CG 编号顺序）、`check_reset_state`。

### `original_scope.json` —— 原版内容覆盖的剧本范围

```jsonc
{ "out_of_scope": [
    {"script": "CC0TOU_EN", "n_slots": 499, "block": "角色后日谈（見里）"},
    {"script": "CC_30A_EN", "n_slots": 937, "block": "追加剧本链"} ],
  "story_blocks": { "共同线": [...], "个人线": {...}, "终章": [...], "追加剧本": [...] } }
```

**边界以「脚本」为粒度**：**未列出的脚本一律在原版范围内**；列出的这些在原版语料里没有对应剧本文件。

**为什么不能按「格」划**（写在 `criterion` 里）：试过「从第一次出现连续不再匹配处划界」，数据上不成立
—— 702 个「原版没有」的格只分布在 78/293 个脚本里且高度散布（227 段连续段里 126 段只有 1 格），
而 289/293 个脚本是「一路匹配到最后一格」，根本没有尾部不匹配段。按那条判据切会误把 11,085 个已匹配格
判成范围外。那些格是 Steam 的**局部新写/改写**；Steam 真正的**整段删改**表现为「源剧本的行没人取」，
不产生这类格 —— 两类现象形状不同。

### `cg_map.json` —— 事件 CG 的**改名**映射

```jsonc
{ "renamed": {
    "EVCC0017B.PNG": {
      "patch": "EVCC9006B.PNG",                  // 本补丁用的名字
      "refs": [["CCC3027", 227], ["CCC3027", 1180]]   // [宿主, 源对话序号]，列表（CG 会复用）
    } },
  "all": { "EVCC0017B.PNG": "EVCC9006B.PNG", ... } }    // 原版名 → 本补丁名，全 37 条
```

- **`renamed` 只记录改了名的**（36 条）；同名且内容一致的（7 条）不入表 —— 不用查表也知道用原名。
- **`all`**（37 条）是 `script/build_restored_cgs.py` 的输入 —— 该步**从原版 `Chip.arc` 取
  `EVCC####.PNG`**（800×600）**LANCZOS 放大到 1280×960** 后按 `all` 改名写入 `asset/Chip2.arc` ⇒
  还原 CG **不依赖上游补丁**（只需"目标 CG 一致"，不要求字节级）。
- **识别方式 = 像素，不是 sha**：上游出货的图是原版 LANCZOS 放大并重编码过的，同一张画的 sha256
  必然不同。同名图片**至今没有任何一例被确认是内容审查**；「相似度 > 60% 即为审查」的判据是反的
  （高相似恰恰说明是同一张图）。可行判据三条并用：① 同名；② **保宽高比 + 最优对齐后的分块局部差异**；
  ③ 脚本层证据（原版该场景引用了它、且 Steam 同名文件画面不同）。
- **不记录指令号**：位置取自**原版 WSC**（`0x46` 图像 / `0x48` 压进立绘槽），而消费方处理的是
  **转换后的 ws2**（同一引用在那里是 `0x33`）。记原版的会误导，记 ws2 的又与「在源里定位」对不上
  —— 只记「哪个宿主、哪条对话」，两者都能唯一定位。
- **为什么改名不能在 `renumber` 里全局做**：同一个原版名在不同脚本里含义不同 —— `SGCC0020.PNG`
  在 `CCB2101_en.ws2` 的**插入段**指原版系统图（该用 `EVCC9004.PNG`），而该脚本的**宿主前缀**
  （`0x33 bg02 SGCC0020.PNG`）、`CCD2002B_en.ws2`、画廊页 `CG_PAGE.ws2` 都指 Steam 的同名图
  （必须保持原名）。全局替换会把后者改坏。所以改名只在 `splice_restoration` 生成插入段那一趟落到字节上。

生成：`script/build_cg_map.py`　核对：`script/renumber_evcc9xxx.py`

### `position_overrides.json` / `portrait_position_overrides.json` —— 立绘位置覆盖

两张都由 `tool/wsc2ws2.py` 消费，**优先级高于**它的启发式 `steam_x()`：

- `position_overrides.json`（33 条）= 逐条记录**不符合 `steam_x()`** 的 `(源脚本, 源行, 角色) -> Steam x`；
- `portrait_position_overrides.json`（1 条）= **手工**维护的场景级覆盖，修正 `steam_x()` 处理不了的
  （如还原场景里 `x==0` 是贴左缘），**优先级更高**。

⚠️ `steam_x()` 只是**单调启发式、未获真值验证**（94.7% 那条是**自指一致率**，配对错就无意义）；
精确复现须按**对白内容**对齐两侧帧，**尚未做**。详见 [wsc_to_ws2_conversion.md](../doc/wsc_to_ws2_conversion.md)「位置映射」。
⚠️ 生成这两张表的标定脚本已随判定链清理而**删除** ⇒ 它们是**已定稿的输入**；要重标定须先重建锚点。

### `graphic_overrides/` —— **图片汉化**的替换清单

```jsonc
{ "Graphic.arc": ["efcca0030.png", "SGCC0003.png", "sgcc0011.png"] }
```

每项登记「哪个归档换掉哪些成员」；替换图 = **同目录**下的同名文件，路径不重复写。
素材来源与加工口径（内容对齐、阴影边透明）见 [localization.md](../doc/localization.md)「图片汉化」。
消费方：`script/apply_graphic_overrides.py`（写盘开头，素材快照之后）。

## 名称与语料

### `speaker_map.json` —— 角色名称映射（唯一来源）

```jsonc
{ "speakers": [
    { "en": "Touko",                    // Steam ws2 里的 `%LC` 标记（= 键）
      "zh": "冬子",                      // 名字框显示文本
      "ja": "冬子",                      // 原版 WSC 的 0x42 说话人字段（无源角色为 null）
      "voice_prefix": "FYU",            // 原版语音文件名前缀（**可为列表**）
      "channel": "charTOU",             // Steam `2e` 指令的通道名
      "pna_prefix": "TCKT",             // 立绘文件名前四字符
      "confidence": "low",              // 仅低置信度条目带（日文名系推断，待实机校对）
      "note": "…" } ] }                 // 仅判定过的条目带（判定留痕）
```

- **完备性核对是单向的：脚本里出现的每个 `%LC` 键都必须能在表里查到**。表**允许有多余记录** ——
  几条是转换器需要的**原版说话人标记**，其 `%LC` 键在 Steam 脚本里从未出现；删掉会让转换器对原版
  语料里那些说话人失去映射。核对接口：`tool/speaker.missing_keys()`。
- `zh` 是 `NameTable.txt` 的**唯一来源**；其余列供转换器与审计查表。
- **`voice_prefix`/`channel` 的判据是「原版引擎自己的分桶」，不是 Steam 的习惯**：原版处理 `0x23`
  时先按文件名前 3 字母查一张**硬编码别名表**，返回**配音来源槽号 1..12**（13 = 非角色），再按槽查
  每角色音量表。**槽按「配音来源」分、不按角色** —— 实测槽 12 = `MMN`+`MYK`（ママン/おばちゃん/
  **みゆき/少女** 同一人）、槽 11 = `GKA/GKB/GKC/HRA/RKA/MSM`（声/腹の虫/老カラデ家/政宗）。
  所以同槽的多个前缀在 Steam 侧**共用该槽唯一存在的通道**。见 [engine-mechanics.md](../doc/engine-mechanics.md)「语音前缀 → 配音来源」。
- ⚠️ **同一个人在两版可能拼法不同**：原版 `HRA`（腹の虫）在 Steam 侧被改名成 `HAR_0001..0006.OGG`。
  本列**记原版拼法**（`WSC→WS2` 用）；Steam 侧自己的拼法由 `tool/writer.py` 直接从 `backup` 取。
- **组合名分隔符统一用全角斜杠 `／`**（如 `冬子／见里／美希／友贵`）；原版日文侧用 `･`/`・`，保留在 `ja` 列不动。
- `en` 是**单值映射**：原版 `太一` 与 `俺` 都写成 `%LCTaichi`，只能显示一个中文。
- 中文列取值的判据分三层：**汉化组 CCS 的 `[...]` 前缀**（机械配对）、**台词自证**、其余交逐条语义判定。

手工维护（本表即唯一来源）。消费方：`build_nametable`（生成 `NameTable.txt`）/ `wsc2ws2` /
`tool.writer` / `speaker` / `audit_lng_semantics` / `final_verification`。

### `corpus/wsc/` —— 源语料（324 个原版 WSC）

**本流水线最深的外部输入**：就地插入、插入行随行演出、原版语音计划、改名表、审计全都从它读。
源头是**仓库外**的原版归档 ⇒ **仓库内无法再生**，所以必须持久化（在版本控制里 ⇒ 换台机器可完整重建）。

> **Steam 侧语料不落副本**：`backup/Rio.arc` 已在仓库里，凡需要原生 WS2 的地方
> （`verify_ws2_conventions` 的原生语料统计等）**直读归档**，不另存一份 `corpus/ws2`。

### `carried_lng/` 与 `carried_soundlevel.json` —— **显式带入**的内化数据

基线改从 `backup/`（Steam 原版）起底后，有两样东西**只在 Res303 里有**，必须显式带过（否则静默丢）：

- **`carried_lng/`** = **本项目处理集之外**（脚本不在 `text_map.json` 的 293 个内）但**要保留**的
  35 个汉化 `.lng`（+ `manifest.json` 记名与哈希）。由 `script/internalize/extract_carried_lng.py` 一次性内化。
- **`carried_soundlevel.json`** = **967 条 `.soundlevel`** 音量包络（按秒 ASCII，与采样率无关）——
  产物需要 16,111 条 = `backup` 的 **15,144**（Steam 对**每条**语音都配了包络，实测 OGG 与侧车的
  stem 逐一相等）**＋ 967 条（补入的原版录音）**，后者只存在于上游补丁的 `Voice.arc`，不显式带过即
  静默丢失（`import_missing_voices.py` 只补 OGG）。余下 585 条补入语音没有包络 —— 本来就没有，
  **不凭空造**（引擎容忍缺包络）。由 `script/internalize/extract_carried_soundlevel.py` 提取。

消费方：`build_patch` 的 `carry_carried_lng()` / `carry_carried_soundlevel()`（仅 `--bootstrap` 时）。
`final_verification` 有产物级断言「侧车条数 ≥ backup + 内化清单」。

### `reused_archives/` —— **完全复用**的上游归档

`Fonts.arc`（汉字字体）+ `Script.arc`（Lua 系统界面）—— 上游汉化产出，**原样采用、不涉裁定**。
由 `script/internalize/extract_reused_archives.py` 一次性内化（+ `manifest.json` 记哈希）。
`build_patch` 的 `bootstrap_copy()` 据此带入 `asset/` ⇒ **构建期不再依赖任何外部上游目录**。
（`SysGraphic.arc` 属另一类：其 UI 汉化图**不采用**，取 `backup/` 的 Steam 原版。）

## 参考表（无代码消费者）

- `ws2_operand_formats.json` —— WS2 全部 **164 个 opcode** 的格式串（`tool/ws2disasm.py` 的对照表）。
  见 [engine-mechanics.md](../doc/engine-mechanics.md)。
- `wsc_handlers.json` —— 原版 WSC 的 **256 项** opcode→handler 表。见 [wsc_to_ws2_conversion.md](../doc/wsc_to_ws2_conversion.md)。
- （**不再保留上游补丁的语料**：它对流水线**零引用** —— 真正要的文本已内化为 `carried_lng/`。）

## 待办

- **`voice_map.json`** —— 语音的完整对照表：Steam 文件名 ↔ 原版录音 ↔ 时长指纹 ↔ 所属槽位。
  目前只有**逐格补挂计划** `voice_plan.json`（⚠️ 它被 `build_voice_plan.py` **整体重写**，
  手工补的条目重跑生成器会丢，见 [lessons-learned](../doc/lessons-learned.md)）。
