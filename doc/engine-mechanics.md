# 引擎机制与逆向发现

本节记录对 AdvHD.exe 引擎的逆向发现，以及 CROSS†CHANNEL 特有的引擎机制。

## AdvHD 引擎概述

AdvHD.exe 是 MoeNovel 的 Amaneko 系视觉小说引擎，用于多款游戏：
- A Sky Full of Stars
- CROSS†CHANNEL Steam Edition
- 其他 MoeNovel 发行的游戏

### 引擎特性

- **32 位 PE 可执行文件**
- **Imagebase**：0x400000
- **代码段**：约 2 MB .text
- **脚本系统**：WS2 二进制脚本（rot6 混淆）
- **资源系统**：自定义 Arc 归档格式
- **脚本绑定**：Lua 5.3（`lua5.3.dll`）
- **表情驱动**：E-mote 系统（`emotedriver.dll`）
- **文本编码**：内建 BIG5 / GB2312 / SHIFTJIS / UTF-8 四种代码页（见「文本编码路径」）
- **语言包机制**：`AdvHDLang.dll` + 语言子目录（官方简中为 `zh-CN/`）

### 代码段加密（CROSS†CHANNEL 特有）

**CROSS†CHANNEL 的 `AdvHD.exe` 的 `.text` 段是加密的**（熵 8.00，开头即随机字节），
静态反汇编不可行；`.rdata` 仍是明文，所以导入表与字符串常量可读。

同引擎的其他 exe 是明文，可直接反汇编：

| exe | 引擎版本（PDB 路径） | `.text` 熵 | 可反汇编 |
|---|---|---|---|
| CROSS†CHANNEL | 2.0 | 8.00（加密） | ❌ |
| A Sky Full of Stars | 2.01 | 6.59 | ✅ |
| If My Heart Had Wings（羽翼汉化版） | 1.7 cn | 6.49 | ✅ |

**因此本项目的引擎行为结论以 A Sky Full of Stars 的 exe 为准** —— 两者版本只差 0.01，
文本路径一致（`LegacyGame.inc` 的 include 清单、名字表机制、命令标记表都相同）。

## LAYER_ORDER：图层槽注册表

### 作用

定义游戏可用的全部"子图层"键的注册表。引擎据此知道有哪些图层槽可用。

### CROSS†CHANNEL 的 LAYER_ORDER

Steam 版与 Res 303 的 LAYER_ORDER 内容完全相同：

```
0x3f 0x13 ev\x00 bg01\x00 bg02\x00 st01\x00 st02\x00 st03\x00 st04\x00 st05\x00 
st06\x00 st07\x00 st08\x00 st09\x00 st10\x00 st11\x00 st12\x00 movie\x00 
text01\x00 text02\x00 text03\x00 \x05\xff\x00\x00\x00\x00\x00\x00\x00\x00
```

**可用槽位（19 个）**：
- `ev`：事件 CG 槽（单槽，与 A Sky Full of Stars 的 ev01/ev02 双槽不同）
- `bg01-bg03`：背景槽（3 个）
- `st01-st12`：立绘/静止帧槽（12 个）
- `movie`：视频槽
- `text01-text03`：文本槽（3 个）

### 与 A Sky Full of Stars 的对比

| 特性 | A Sky Full of Stars | CROSS†CHANNEL |
|------|---------------------|---------------|
| 事件 CG 槽 | `ev01`, `ev02`（双槽） | `ev`（单槽） |
| 立绘槽 | `st01-st30`（30 个） | `st01-st12`（12 个） |
| blink/talk 子图层 | ✅ 有（ev01blink, ev01talk 等） | ❌ 无 |
| 总槽位数 | 114 键 | 19 键 |

### 文件格式

LAYER_ORDER 是一个 `.ws2` 文件，但**不是带 opcode 的可执行脚本**，而是**纯键名数据表**：

```
0x3f <count u8> <key0>\x00 <key1>\x00 ... <keyN>\x00 0x05 0xff 00...
  │              └────── 键名列表 ──────┘              └── 终止符
  └= 键数量（0x13 = 19）
```

## 槽位命名限制

### ev 槽（事件 CG）

**已知信息**：
- CROSS†CHANNEL 只有单个 `ev` 槽
- Steam 版脚本中未发现 0x34 指令使用 `ev` 槽（可能事件 CG 以其他方式显示）
- Stem 长度限制待实测

**与 A Sky Full of Stars 的差异**：
- A Sky Full of Stars 的 `ev01/ev02` 槽有 **7 字节 stem 硬限制**
- CROSS†CHANNEL 的 `ev` 槽是否有类似限制需要实测验证

### st 槽（立绘）

**已确认**：
- 无严格长度限制，支持 8-10 字节 stem（从 Steam 版脚本统计得出）

**使用统计**：
- `st03`：5840 次引用（使用最频繁）
- `st05`：1132 次引用
- `st07`：377 次引用
- 其他槽位使用较少

### bg 槽（背景）

**已知信息**：
- 有 3 个背景槽（bg01-bg03）
- 主要用于显示背景图片（PNG 格式）
- 命名限制待测试

## PNA 显示机制

### 0x34 显示指令

```
\x34 <slot>\x00 <STEM>.PNA\x00 \x01 \x01
```

**槽位名**：
- 变长字段（2-12 字节）
- 以 NUL（`\x00`）终止
- 示例：`st03`（4 字节）、`ev`（2 字节）

**文件名**：
- 不含扩展名的 stem
- 以 `.PNA` 为扩展名
- NUL 终止

### 图层 ID 引用

引擎在显示 PNA 时，脚本中引用的 layer_id 是 **u16 小端序整数**，表示该 PNA 文件中的图层索引。

**计算规则**：
```
layer_id = layer_count - 1 - index
```

**图层 ID 越界**：
- 如果请求的 layer_id ≥ layer_count，引擎会崩溃（错误码 0x00000039）

## 资源载入机制

### 0x33 硬载入（背景）

```
\x33 <slot>\x00 <FILE.PNG>\x00 \x01 \x01
```

**行为**：
- 硬载入资源，必须存在
- 如果资源缺失，游戏会**卡死（无响应）**，不弹错误码
- 用于背景图片的载入

### 0x66 特效遮罩

```
\x66 ...
```

**行为**：
- 用于特效遮罩
- 如果资源缺失，引擎**容忍（静默跳过）**，仅画面瑕疵
- 不会导致崩溃或卡死

## 场景出口与条件双出口（0x01 mode=0x85）

### 指令形态

```
01 <mode:u8> <var-id:u16> <value:f32> <a:u32> <b:u32>        # mode != 0 时固定 16 字节
```

`mode == 0x85`（十进制 133，本项目与 Res303 报告简称 **条件双出口**）是一处**条件跳转**：

- **条件成立 → 跳到文件内偏移 `b`**；
- **条件不成立 → 顺序执行下一条指令**。

（比较运算本身未实证，此处按"跳到 b / 否则顺序执行"的可观测行为描述。）

### 两种排布（Steam 原生 363 个脚本中共 6 处）

| 排布 | 形态 | 出现 |
|------|------|------|
| 双出口 | `01 85 …` / `07 A` / `07 B` / `ff`，`b` 指向 `07 B` | CCC0000（b=15464→`CCC0002_EN`）、CCC0007（b=20713→`CCC0008B_EN`）、CC_23（b=36→`CC_26A_EN`） |
| 跳过 transfer | `01 85 …` / `07 X` / `16 …`，`b` 指向 `07 X` **之后**的 `16` | CCC3001、CCD0201、CCD3099 |

即：条件是"走 transfer 出去"还是"跳过它、留在本文件继续"。`07` 是**终止型**跳转（没有调用/返回），
所以同一脚本要给出两条出路时，必须用这种条件跳转在同一文件里承载两个目标。

### 变量语义

- var-id 与 value 都随剧本而变：CCC0000 用 **var 50 = 3.0**，CCC0007 也是 **var 50 但 = 4.0**，CCD0201/CCD3099 用 **var 52 = 20.0**，CC_23 用 **var 53 = 4.0**。
- 同一变量槽在不同剧本取不同值，读起来像"这条分支是否已经走过"的**进度标记**，而不是通用布尔开关。

### 还原注意

`b` 是**文件内绝对偏移**——凡是移动/重排该脚本的字节，都必须重算这个字段
（rot6 编码后再写入）。实例：`CCC0000_en` 就地插入原版内容后，`b` 需按新布局重算
（现为 25194，指向 `07 CCC0002_EN` 的偏移），由 `script/splice_restoration.py` 处理并回读校验。

## 与 A Sky Full of Stars 的差异汇总

两者同引擎但版本不同，**不能直接套用 ASF 的全部结论**：

| 特性 | A Sky Full of Stars | CROSS†CHANNEL |
|------|---------------------|---------------|
| 槽位数量 | 114 个 | 19 个 |
| 事件 CG 槽 | 双槽（`ev01`/`ev02`） | 单槽（`ev`） |
| ev 槽限制 | 7 字节 stem 硬限制 | **待实测** |
| blink/talk 子图层 | ✅ 有（`ev01blink`/`ev01talk` 三元组，用于眨眼、口型） | ❌ LAYER_ORDER 不包含（引擎可能仍按 PNA 内部结构处理动画，但不显式注册子图层） |
| `Script.arc` | Steam 版无 | Res 303 借入 |
| `.text` 段 | 明文，可反汇编 | 加密（结论以外推为准） |

## E-mote 表情系统

### emotedriver.dll

AdvHD 引擎集成了 E-mote 表情驱动系统，用于实现立绘的动态表情。

**相关符号**（从 AdvHD.exe 导出表）：
- `CEmoteLayer`
- `face_talk`
- `DISABLETALK`
- `LUA_LAYER_PNA` / `LUA_LAYER_STD`

**功能**：
- 驱动立绘的眨眼、口型动画
- 与 PNA 图层结构配合工作
- 可能通过 Lua 脚本控制

## Lua 脚本绑定

### lua5.3.dll

AdvHD 引擎使用 Lua 5.3 作为脚本系统的扩展。

**用途**：
- 系统界面逻辑（菜单、设置、画廊）
- 游戏主逻辑（变量管理、存档系统）
- UI 组件（按钮、滚动条、进度条）

**Lua API**（从逆向发现）：
- `insertPNARefLayer` - 把 PNA 注册为子图层（对应 0x34 指令）
- `initSubLayer` / `initPNALayoutMode`
- `setSubLayerParam` / `setSubLayerVisible` / `setSubLayerEffect`
- `startSubLayerEffect` / `cancelSubLayerEffect`
- `removeSubLayer`
- `getSubLayerParam`

**注意**：这些函数名存在于二进制中，但**运行时按名/哈希动态绑定**，无法通过静态分析直接定位到函数体。

### Lua 5.3 字节码格式（引擎特有）

`Script.arc` 里的 `*.lua` 成员是 **Lua 5.3 编译产物，不是混淆** ——
文件头就是标准签名 `\x1bLuaS`，`LUAC_INT`/`LUAC_NUM` 校验值也都对。

之前"解不出"是因为拿 utf-8 / utf-16le / gbk / cp932 去解**字节码**（而非源码文本）。
`tool/ws2.py` 的 rot6 也不适用。

与上游 Lua 5.3 有**两处差异**，都是穷举验证出的唯一解：

| 项 | 上游 5.3 | 本引擎 |
|---|---|---|
| 字符串长度字段 `LoadSize` | `sizeof(size_t)` = 4 字节 | **1 字节** |
| opcode 枚举 | 47 个 | 在**索引 9 处多一个**，其后全部后移 1 位 |

**字符串长度 1 字节**的判定依据：把 (主 chunk 前置字节数, 字符串长度宽度, 计数宽度,
行号宽度) 的全部组合跑一遍，只有 `(1, 1, 4, 4)` 能让 `Script.arc` 的 13 个成员
**逐个精确消耗到文件末尾**，且提取出的字符串全部可读。13 个文件同时自洽也反证了
没有长度 ≥ 256 的字符串。

**多出的 opcode**：该码（索引 9）在整个 `Script.arc` —— 753 个 proto、约 13 万条指令 ——
里**从未出现**，身份未知，也不影响反汇编。`op=10` 是 `SETTABLE`、`op=11` 是 `NEWTABLE`
（上游是 9/10），可由 `g_fonts = {name=..., file=..., CharSet=...}` 的构造代码实证。

工具：`tool/luac53.py`（解析）+ `tool/luadis53.py`（反汇编，753 个 proto 零问题）。
用法：

```bash
python tool/luadis53.py LegacyGame_utf8.lua --all
```

### 活动 Lua 与死代码

`LegacyGame.inc` 的 include 清单决定了实际加载哪些 Lua：

```
include "ArcFileName"        include "menu_base"      include "menu_gallery"
include "LegacyGame_utf8"    include "menu_config"    include "ui_button"
include "ui_language"        include "ui_scrollbar"
```

**`LegacyGame.lua`（147 KB）不在清单里 —— 它是死代码。** 且这不是 Res 303 的改动：
A Sky Full of Stars 原版的 `LegacyGame.inc` **同样 include `LegacyGame_utf8`**。
那份 CP932 的大文件是引擎/发行方遗留，UTF-8 才是引擎的标准路径。

## 名字替换表（NameTable.txt）★

引擎内建**说话人名替换机制**：脚本里的 `%L?<名>` 标记会被拿去查 `Rio.arc` 里的
`NameTable.txt`，命中则替换成表右列的内容。

**这张表是 UTF-16LE** —— 因此**不受脚本内窄字节 CP932 解码的限制**，
简体中文可以经这条路显示。**这是角色名汉化的正解**，无需改动 ws2 字节。

### 格式

制表符分隔，**左右两侧都带完整标记前缀**，CRLF 换行，无 BOM：

```text
%LCTaichi	%LC太一
%LCMisato	%LC见里
```

（示例为修正后的 CC 表；ASF 用的是 `%LF`，格式相同。）

### 关键规则：表键必须等于脚本里的标记

对照 A Sky Full of Stars 的官方简中包可以确证：

| 游戏 | 脚本里的标记 | 表键前缀 | 表键 vs 脚本名 | 结果 |
|---|---|---|---|---|
| ASF | `%LF`（26,130 处） | `%LF`（140 行） | 一一对应，**"只在表里" 0 个** | ✅ 生效 |
| CC | `%LC`（27,132 处） | ~~`%LR`~~ → `%LC`（73 行） | 一一对应，**"只在表里" 0 个** | 修正前 ❌ / 修正后待实测 |

`%LC` / `%LF` / `%LR` **都是引擎的合法命令标记** —— 三者在 `AdvHD.exe` 的命令
标记表里并列存在：

```text
%LC  %C   %TS  %TE  %AS  %AE  %WS  %WE  %LR  %LL  %K   %P   %p   %N   %O
%FE  %FS  %LF  %E   %V   %W   %T   %XS  %XE  %X   ...
```

字母的含义未定论（`%TS/%TE`、`%AS/%AE`、`%WS/%WE` 成对，像是成对的内联控制码）。
**但每个游戏用哪个字母是脚本生成时固定的，与语言切换无关**（ASF 的英文版和
简中版都用 `%LF`）。所以表键必须跟着脚本走。

### 本项目的修正

Res 303 补丁在 `Rio.arc` 里放了 `NameTable.txt`（73 行），**内容与脚本的 73 个
`%LC` 名字一一对应、零偏差**，中文也是简体 —— 表本身是对的，**只是前缀写成了
`%LR`**，与脚本的 `%LC` 对不上，引擎查不到，名字框就一直显示英文。

修正：`script/fix_nametable_prefix.py`（把行首与制表符后的 `%LR` 改成 `%LC`，
备份 + 回读校验 + 幂等）。

### 语言包目录

引擎支持语言包：`AdvHDLang.dll`（语言桩，内含设置界面的本地化字符串 + 语言列表）
加语言子目录。官方简中的结构与 CC 的对应关系：

| | A Sky Full of Stars | CROSS†CHANNEL |
|---|---|---|
| 语言目录 | `zh-CN/` | 无（Res 303 借 `Script.arc` 提供 Lua） |
| 目录内容 | `AdvHDLang.dll`、`Fonts.arc`、`Rio.arc`、`Script.arc`、`SysGraphic.arc` | — |
| 中文剧本载体 | `zh-CN/Rio.arc`：298 个 `.lng` + `NameTable.txt` | `Rio.arc` 内的 `.lng` + `NameTable.txt` |

**ASF 的中文包里没有 ws2** —— 剧本仍用英文 ws2，中文全部经 `.lng`（正文/选项）
与 `NameTable.txt`（人名）注入。这对本项目是可直接复用的范式。

`ui_language.lua` 是语言开关，官方简中版与 Res 303 的**实现完全相同**：

```lua
function getLangPatchFlag()  PatchFlag = true;  return PatchFlag  end
function setInitLang()       g_altLanguage = true                 end
```

## 文本编码路径

`AdvHD.exe` 内建 **BIG5 / GB2312 / SHIFTJIS / UTF-8** 四种代码页，并提供 Lua 侧转换绑定：

- `MyConv__lua_utf8tosjis` / `MyConv__lua_sjistoutf8` / `MyConv__lua_utf8substring`
- `lua_utf8split`

**Lua 字符串层是 UTF-8**：`LegacyGame_utf8.lua` 里的字体名 `FOT-マティス Pro M`
就是 UTF-8 编码（且直接传给引擎 API，未做转换）。

**但脚本内窄字节文本按 CP932 解码**。对 A Sky Full of Stars 的引擎（明文，可反汇编）
统计 `MultiByteToWideChar` 的调用点参数：

| 代码页 | 出现次数 |
|---|---|
| **CP932（Shift-JIS）** | **40** |
| 参数在寄存器中（未能静态解析） | 9 |
| UTF-8 (65001) | 2 |
| CP_ACP | 2 |

反方向的 `WideCharToMultiByte` 则以 UTF-8 为主（4 处）—— 那是**输出**方向，
用于把宽字符交给 Lua / Steam API。这与 `MyConv__lua_utf8tosjis` 的存在完全吻合：
**引擎内部用 CP932，Lua 层用 UTF-8，需要跨层时显式转换。**

**结论**：`%LC` 这类**脚本内窄字节**受 CP932 限制，简体专用字（见/雾/贵/樱/游/纱/
师/长/丰/咪 等）写不进去。**所以文本汉化必须走宽字符通路** ——
正文/选项走 `.lng`（UTF-16LE），人名走 `NameTable.txt`（UTF-16LE）。
这也是官方简中的做法。

## 逆向难点

### 符号信息缺失

- AdvHD.exe 是**无符号 C++ 二进制**
- 函数名以 RTTI 形式存在（`.?AVCPNALayer@@` 等）
- Lua 函数注册表是运行时动态绑定的
- 无法经字符串 xref 静态定位到函数体

### 代码高度优化

- 编译器优化导致代码难以理解
- 内联函数、循环展开、指令重排
- 需要结合动态调试和静态分析

### 实测优于逆向

对于资源注册失败、显示错误等问题，**实机 A/B 测试比纯静态逆向更高效**：
- 快速定位问题（是资源名、长度、格式还是其他）
- 验证假设（如 ev 槽对前缀的容忍度）
- 获得确定性结论

### 但逆向在有明文样本时也别放弃

「实测优先」不等于「不逆向」。本项目的两个关键结论都是静态分析拿到的：

1. **文本编码路径** —— 统计 `MultiByteToWideChar` 调用点的代码页参数，
   得出「脚本内窄字节 = CP932」。这比逐个试编码快得多，且是确定性的。
2. **名字表机制** —— 从 ASF 官方简中包对照出「表键必须等于脚本标记」的规则。

**前提是找到一份未加密的同引擎 exe**。CC 自己的 `.text` 加密，但 A Sky Full of Stars
（版本只差 0.01）是明文，结论可直接外推。

## 实测验证清单

- [ ] **`NameTable.txt` 前缀改为 `%LC` 后，名字框是否显示简体中文**（当前最高优先级；
      见「名字替换表」）
- [ ] `NameTable.txt` 放在主 `Rio.arc` 是否足够（ASF 是放在 `zh-CN/Rio.arc`，CC 无语言目录）
- [ ] ev 槽对 PNA stem 长度的限制（是否有 7 字节限制）
- [ ] 资源缺失时的引擎行为（`0x33` 卡死、`0x66` 容忍）
- [ ] `Script.arc` 的加载时机（是否必须、加载顺序）

**已确认**：

- [x] lng 文件的文本替换机制 —— 只覆盖 `0x14` 正文与 `0f` 选项，按**池槽位序**定位；
      见 [lessons-learned.md](lessons-learned.md) §20
- [x] 字体文件的加载机制 —— `CLegacyFontInfo:create` 以 `file`/`name`/`CharSet` 从
      `Fonts.arc` 取 PTF；`CharSet = GB2312_CHARSET` 是简体显示的关键（Res 303 已设）
- [x] 脚本内窄字节文本的编码 —— CP932（见「文本编码路径」）
- [x] 角色名替换机制 —— `NameTable.txt`（见「名字替换表」）

## 经验总结

1. **优先实测**：资源注册、显示效果一类问题，A/B 测试 > 静态逆向（快，且是确定性结论）
2. **但也别放弃逆向**：有明文样本时静态分析能给出实测难以枚举的结论（见上文）
3. **先找同引擎的官方样本**：「实测优先」之前先问这个机制在同引擎的其他游戏里长什么样
4. **复用而不要盲信**：Res 303 的方案可作参考，但其产物未经 Steam 版验证（见
   [lessons-learned.md](lessons-learned.md) §5）
5. **工具选择**：静态分析 IDA Pro / Ghidra；动态调试 x64dbg / WinDbg；
   文件分析 010 Editor / HxD；最终验证 —— 修改游戏文件 + 运行游戏
