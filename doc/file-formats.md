# 游戏文件格式规范

## Arc 归档格式

Arc 是 AdvHD 引擎使用的自定义归档格式，用于打包游戏资源。

### 文件结构

```
[Header 8 bytes] [Table N bytes] [Data ...]
```

### Header（8 字节）

```c
struct Header {
    uint32_t count;       // 成员数量
    uint32_t table_size;  // 表大小（字节）
};
```

### Table Entry（每个成员）

```c
struct Entry {
    uint32_t size;        // 数据大小
    uint32_t offset;      // 相对偏移（相对于 data_start = 8 + table_size）
    wchar_t name[];       // UTF-16LE 编码的文件名
    uint16_t terminator;  // 0x0000（UTF-16 NUL）
};
```

### 关键特性

1. **文件名编码**：UTF-16LE，以 `\x00\x00` 终止
2. **偏移量**：相对于数据段起始位置（`8 + table_size`），不是绝对偏移
3. **数据段**：紧随表结构之后，按表中顺序连续存储

### 读写注意事项

- **保持字节精确性**：日文文件名必须字节精确保留，不能通过文件系统往返（会损坏编码）
- **偏移计算**：写入时使用相对偏移，读取时加上 `data_start` 得到绝对位置
- **幂等性**：多次读写同一文件应产生完全相同的字节序列（相同 SHA256）

### Arc 文件规范化

某些 Arc 文件可能在表末尾和数据段之间存在 null padding（`\x00\x00`），这会导致：
- 每次读写产生不同的 SHA256
- 安装器哈希校验失败

**解决方案**：使用 `arcbuild.normalize_arc_padding()` 去除 padding，统一为标准格式。

## WS2 脚本格式

### 文件概述

场景脚本文件，包含对话、跳转指令、CG 显示等。使用 rot6 混淆（每字节循环左移 6 位）。

### 混淆算法

```python
# 解码（rotate left 6）
def decode(raw):
    return bytes(((c << 6) | (c >> 2)) & 0xff for c in raw)

# 编码（rotate right 6 == rotate left 2）
def encode(data):
    return bytes(((c << 2) | (c >> 6)) & 0xff for c in data)
```

### 基础数据类型

#### 字符串 (Null-terminated String)
```
格式: <ASCII字符序列>\x00
示例: "CCA0001_EN\x00" → 43 43 41 30 30 30 31 5f 45 4e 00
```

#### 浮点数 (32-bit Float, Little-Endian)
```
示例: 1.0f → 00 00 80 3f
      1.5f → 00 00 c0 3f
```

### 指令集详解

#### 场景控制指令

**`0x07` - 场景转移 (Jump)**

格式: `\x07<target>\x00` —— **`0x07` 本身没有参数尾**，其后跟的是**独立的 `0xff` 出口指令**。

`0xff <u32 a> <u32 b>` 才是"脚本结束/交出控制权"，与 `07` 组合成 WS2 的出口惯用形：

```
15 00 00 | 07 <target> 00 | ff <u32 a> <u32 b>
```

- `<target>`: 目标脚本名（ASCII 大写、不带扩展名，如 `CCC0001_EN`）
- `ff.a`: 引擎内部参数，取 **`8`**（实测可玩值；`0` 会崩溃）
- `ff.b`: `128` / `192` 与"含选项"强相关，其余为 0

示例:
```
\x15\x00\x00\x07CCA0002_EN\x00\xff\x08\x00\x00\x00\x00\x00\x00\x00
```

**重要**: 出口前必须是 `\x15\x00\x00`（清框），**不能**是 `\x16\x00\x00`（图层命令块起始）——
`16` 会被引擎当作图层命令块的参数读入，脚本随即解析失败。

**`0x04` - 引擎子调用 (Call)**

格式: `\x04<function_name>\x00`

常见函数:
- `LAYER_ORDER`: 刷新图层顺序

**注意**：`0x04` **不是**场景切换指令（原版走全局脚本号路由，Steam 走 `07`+`ff`）；全语料
19,675 条 `04` 里 18,965 条是 `LAYER_ORDER`。

#### 对话系统指令

**`0x14` - 对话块 (Dialogue)**

格式: `\x14<id:u16>\x00\x00<char>\x00<text>\x00\x00`

- `<id>`: **u16**（不是 u8），是**字符串池序号**——与选项表 `0f` 的 strid 同池，按文件出现顺序
  填充，选项文本也占槽位，所以 id 会**跳过**选项占用的号（原版 CCC0000 是 0..363 连续；CCA0006
  是 0..203 / 204,205,206 归选项 / 207..）。详见 [wsc_to_ws2_conversion.md](wsc_to_ws2_conversion.md) §3.1
- `<char>`: 固定为 `char`（叙述也是 `char`，靠 `15` 的空前缀区分说话人）
- `<text>`: CP932 正文，`%K%P` 收尾

对话文本标记:
- `%K`: 对话结束，等待玩家点击
- `%P`: 清除文本框 (常与 `%K` 组合: `%K%P`)
- `%N`: 空白占位 (立即跳过)
- `%L<name>\x00`: 设置说话角色名（在 `0x15` 的前缀里，不在正文）
- `\d...\d`: 延迟显示效果

**`0x15` - 清除对话框 / 设置说话人**

格式: `\x15<prefix>\x00\x00`，`<prefix>` 为空或 `%LC<说话人英文名>`

作用: 清框并（如带前缀）设置名字框。**每条 `0x14` 前发一条**（原生 `15→14` 54,176 次是主流
惯用形）；`\x16\x00\x00` 是图层命令块起始，**不能**拿来当"清框"。

#### 图层/背景控制指令

**`0x16\x00\x00` - 图层命令块起始**

标记图层操作命令块的开始，必须跟随具体操作指令 (如背景切换)

**`0x33` - 硬加载槽位 (Load Slot)**

格式: `\x33<slot>\x00<filename>\x00<flags>`

- `<slot>`: 槽位名 (如 "bg01", "st12")
- `<filename>`: 资源文件名 (如 "BLACK.PNG")
- `<flags>`: 2字节标志 (常见: `\x01\x01`)

示例:
```
\x33bg01\x00BLACK.PNG\x00\x01\x01
```

**`0x34` - 显示 PNA 图层**

格式: `\x34<slot>\x00<FILE.PNA>\x00\x01\x01`

- `<slot>`: **一个完整的 NUL 串**，立绘是 `st01`..`st12`（首字节 `0x73` 就是 `s`，不是独立的
  tag 前缀——照文档写成 `\x34\x73` + `"st01"` 会产出非法的 `sst01`）。另有 `\x40` 开头的
  `@ANIMATION_SETKEY` 形态
- `<FILE.PNA>`: 立绘资源名

**背景切换块**：完整字节模板（含 `65` 渐变指令，首操作数恒 `0x00`）见
[wsc_to_ws2_conversion.md](wsc_to_ws2_conversion.md) §3.3，此处不再重复；要点是
`15 00 00` → `16 00 00 64 00 37 2a 00` → `33` 载图 → `04 LAYER_ORDER` → `09`×4 透明度 →
`46` 无补间 → `65 00 00 00 <f32 秒> 00000000 0200` → `16 01 00`。

**`0x66` - 特效遮罩**

格式: `\x66<EFMSK_##.PNG>\x00 65 64 00 00 <f32 1.0> 00000000 02 00`

说明: 缺失时容忍 (仅画面瑕疵)。注意这条尾巴里的 `65 64 00 00` 是**蒙版自己的**参数，
不要把它当成背景块 `65` 指令的写法（背景块是 `65 00 00 00`）。

#### 音频控制指令

**`0x1e` - BGM加载播放**

格式: `\x1e<slot>\x00<filename>\x00<17字节参数>`

- `<slot>`: 音乐槽位 (如 "bgm01")
- `<filename>`: BGM文件名 (如 "BGM015.OGG")
- 17 字节参数 = `f32 音量 @0 · f32 0 @4 · u16 0xffff @8 · u16 0x000a @10 · u8 1 @12 · f32 0 @13`。
  **`@10` 的 `0x0a` 是参数，不是一条新指令**——按 10 字节读会错位

**`0x2e\x28` - 语音播放**

格式: `\x2e\x28<char通道>\x00<filename>\x00<22字节固定参数>`

- 22 字节尾 = `00×10 0a 00 00 65 00×8`，全语料恒定
- 通道名是角色名（如 `charYOU`）

示例:
```
\x2e\x28charYOU\x00YOU025A5000.OGG\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x0a\x00\x00e\x00\x00\x00\x00\x00\x00\x00\x00
```

**`0x28` - 音效 (SE)**

格式: `\x28<slot>\x00<filename>\x00<22字节参数>`

- 槽名如 `se01`；槽名为 `char*` 时即"语音"的另一种写法（原生另有 14,824 条走 `2e 28`）
- 22 字节尾与 `0x2e` 同源，布局见 [wsc_to_ws2_conversion.md](wsc_to_ws2_conversion.md) §3.6

#### 时序控制指令

**`0x11` - 启动计时器**

格式: `\x11<timer_name>\x00\x00<time_value>`

**`0x12` - 等待计时器**

格式: `\x12<timer_name>\x00\x00\x00`

典型用法 (定时延迟):
```
\x11timer01\x00\x00<time>  # 启动计时器
\x12timer01\x00\x00\x00    # 等待计时器结束
```

#### 变量控制指令

**`0x09\x00` - 设置变量**

格式: `\x09\x00<var_id><value>`

- `<var_id>`: u16, 变量ID
- `<value>`: u32, 变量值 (通常是浮点数)

示例:
```
\x09\x00\x0a\x00\x00\x00\x80\x3f  # 设置变量10为1.0
```

**`0x0b` - 设置变量(短形式)**

格式: `\x0b<u16>\x01`

用途: gallery id 等

### 重要规则

#### 对话 id 的取值

**规则**：`\x14` 的 id **等于该 14 在字符串池里的槽位序** —— 从 0 起随文件出现顺序递增，
**会跳过选项文本占用的号**（选项条目的 strid 与 id 同池）。

**违反后果**：文本与说话人错位、选项串位、回溯系统异常。
完整池模型见 [lessons-learned.md](lessons-learned.md) §8。

#### 出口惯用形

```
<对话>%K\x00\x00
\x15\x00\x00                    # 清除对话框
\x07<TARGET>\x00                # 场景转移（终止型，无参数尾）
\xff <u32 a> <u32 b>            # 独立的出口指令，a 取 8
```

### 字节码速查表

| Opcode | 功能 | 参数格式 |
|--------|------|----------|
| 0x04 | 引擎子调用 | `<name>\x00`（绝大多数是 `LAYER_ORDER`） |
| 0x07 | 场景转移 | `<target>\x00`（无参数尾；其后跟独立的 `0xff` 出口指令） |
| 0x09 | 设置变量 | `\x00<var_id:u16><f32>` |
| 0x0b | 设置变量(短) | `<u16 id><u8 01>` |
| 0x11 | 启动计时器 | `<name>\x00\x00<f32 秒>` |
| 0x12 | 等待计时器 | `<name>\x00\x00\x00` |
| 0x14 | 对话块 | `<id:u16>\x00\x00<char>\x00<text>\x00\x00` |
| 0x15 | 清框 / 设说话人 | `<prefix>\x00\x00`（前缀空或 `%LC<名>`） |
| 0x16 | 图层命令块 | `<u16 a>\x00<payload>`（`a=0` 关对话框层、`a=1` 重开） |
| 0x1e | BGM 播放 | `<slot>\x00<file>\x00<17B 参数>` |
| 0x28 | 音效 (SE) | `<slot>\x00<file>\x00<22B 参数>` |
| 0x2e | 语音播放 | `\x28<通道>\x00<file>\x00<22B 参数>` |
| 0x33 | 加载槽位 | `<slot>\x00<file>\x00<flags>` |
| 0x34 | 显示 PNA | `<slot>\x00<file>\x00\x01\x01`（槽名是**一个**整串，如 `st01`） |
| 0x65 | 渐变 | `\x00\x00\x00<f32 秒>00000000<u16 mode>`（首操作数恒 `0x00`） |
| 0x66 | 特效遮罩 | `<名>.PNG\x00 65 64 00 00 <f32>00000000 02 00` |
| 0xff | 脚本结束 / 出口 | `<u32 a><u32 b>`（`a=8` 可玩；`b` 见 `07` 节） |

定长尾段（`0x1e` = 17B、`0x28`/`0x2e` = 22B）的逐字节布局见
[wsc_to_ws2_conversion.md](wsc_to_ws2_conversion.md) §3.6——**按整条指令读**，少读 1 字节都会让
后续指令整体错位。

### 命名规则

脚本命名格式 `{PREFIX}{编号}{分支}_{语言}.ws2`，`_en` = Steam 原版脚本。
章节前缀与分支标识见 [call-chain.md](call-chain.md)「脚本命名规范」。

### 跳转指令

**两种"转移"**：
- `0x07 <SCRIPT_NAME>\x00` → WS2 的场景转移。**终止型**，没有调用/返回概念，后面跟独立的 `0xff` 出口指令
- `0x04 <FUNCTION>\x00` → 引擎子调用（`LAYER_ORDER` 等），**不是**场景切换

> 原版（WSC）的 `07`/`09` **兼有** call-and-return 语义（`EVRET`/`RestBGM`/`CG_WAIT` 之后仍有活代码），
> 而 WS2 没有可返回的调用指令——中程转移无法一一对应，见
> [wsc_to_ws2_conversion.md](wsc_to_ws2_conversion.md) §3.4。

**引用规则**：
- 脚本名使用 ASCII 大写，无扩展名
- 示例：`\x07CCA0005A_EN\x00`
- 后缀 `_EN` 等必须显式写入

### 跳转指令提取注意事项

❌ **错误（字符串匹配）**：
```python
if b'CCA0005A_EN' in data:  # 可能是注释或数据
```

✅ **正确（指令匹配）**：
```python
if b'\x07CCA0005A_EN\x00' in data:  # 精确匹配 jump 指令
```

## 资源引用编码规则

### 字符编码

- 资源名使用 **Shift-JIS** 编码（不是 UTF-8）
- 带日文的资源名必须用 `name.encode('shift_jis')` 处理；标准正则 `[A-Za-z0-9_]+`
  匹配不到含日文的名字，审计时极易漏掉

### 改写规则

1. **显示指令上下文精确替换**：使用 NUL 前缀的精确模式
   ```python
   # ❌ 错误：会命中已改名文件的尾巴，导致双前缀
   dec.replace('TCCT1000', 'TCCT1001')

   # ✅ 正确：精确匹配显示指令格式
   dec.replace(b'\x00' + old_name, b'\x00' + new_name)
   ```
2. **幂等性**：脚本必须可重复运行，每次运行前检查「已满足则跳过」
3. **回读校验**：改写后必须回读验证，确认修改成功
4. **控制台输出**：Windows 控制台默认 GBK/cp936，避免使用非 ASCII 字符
   - 推荐：`[OK]`/`[!]` 等 ASCII 标记
   - 或在脚本开头加：`sys.stdout.reconfigure(encoding='utf-8')`
     （**只能包一层** —— 多个模块各自 `io.TextIOWrapper(sys.stdout.buffer, ...)`
     会互相关掉底层 buffer，报 `I/O operation on closed file`）

## LNG 文本文件格式

LNG 是 AdvHD 引擎的文本替换机制，每个含文本的 `.ws2` 对应一个同名的 `.lng`，两者同在
`Rio.arc` 中。工具 `tool/lng.py`（`parse_lng` / `encode_lng`）。

```
[Header]
  uint32  count                 // 条目数
  uint16  length[count]         // 每条的字节长（含结尾 NUL 对 00 00）
[Payload]
  count 条字符串背靠背，无对齐填充
```

- **单条字符串 = UTF-16LE 编码，每字节再 XOR 0x2C**。容器本身**不做 rot6**。
- 终端 `%K`/`%P`/`%N` 等控制标记以普通文本形式随条目携带（如 `终于说出话来。%K%P`）。
- **按位置替换**：引擎把播放序第 N 条文本替换为 lng 第 N 条目。完整的池槽位模型
  （`lng 条数 == 14 条数 + Σ(0f 的 count)`）见 [lessons-learned.md](lessons-learned.md) §20。
- 说话人名**不经 lng**，走 `NameTable.txt`（见下）。

## Script.arc（Lua 脚本）

Res 303 新增的归档，包含游戏系统界面的 Lua 脚本。

### 成员列表（14 个）

| 文件名 | 用途 |
|--------|------|
| `ArcFileName.lua` | Arc 文件名配置 |
| `GameInfo.lua` | 游戏信息 |
| `LegacyGame.inc` / `LegacyGame.lua` / `LegacyGame_utf8.lua` | 游戏主逻辑 |
| `VariableSize.lua` | 变量大小配置 |
| `VersionInfo.lua` | 版本信息 |
| `menu_base.lua` | 菜单基础框架 |
| `menu_config.lua` | 设置菜单 |
| `menu_gallery.lua` | 画廊菜单 |
| `ui_GaugeBar.lua` / `ui_button.lua` / `ui_language.lua` / `ui_scrollbar.lua` | UI 组件 |

### 作用

- 实现图形化系统界面（菜单、设置、画廊等）
- 支持多语言切换
- 替代原版的纯文本系统界面

**注意**：Steam 版 CROSS†CHANNEL 不含此归档（本项目由 Res 303 借入）；
同引擎的 A Sky Full of Stars 原版即带有 `Script.arc`，其官方简中语言包另有
`zh-CN/Script.arc`（只含一个 `ui_language.lua`）覆盖。

### Lua 5.3 字节码格式

成员 `*.lua` 是 **Lua 5.3 编译产物，不是混淆**（先前"解不出"是因为拿文本编码去解字节码）：

| 字段 | 值 |
|------|-----|
| 签名 | `\x1bLuaS`（`\x1bLua` + 版本 `0x53`） |
| 格式字节 | `0x00` |
| `LUAC_DATA` | `19 93 0d 0a 1a 0a` |
| 字长声明 | `int=4, size_t=4, Instruction=4, lua_Integer=8, lua_Number=8` |
| `LUAC_INT` | `0x5678`（8 字节） |
| `LUAC_NUM` | `370.5`（double，8 字节） |

**与上游 Lua 5.3 的两处差异**（穷举验证的唯一解，详见
[engine-mechanics.md](engine-mechanics.md)）：

1. **字符串长度字段是 1 字节**（上游为 `sizeof(size_t)` = 4 字节）；
2. **opcode 枚举在索引 9 处多一个**，其后全部后移 1 位 ——
   `op=10` 是 `SETTABLE`、`op=11` 是 `NEWTABLE`（上游为 9/10）。

`LegacyGame.inc` 是**纯文本**的 include 清单（CRLF，每行 `include "xxx"`），
决定实际加载哪些成员。**`LegacyGame.lua` 不在清单里，是死代码**
（ASF 原版同样如此，非 Res 303 的改动）。

工具：`tool/luac53.py`（解析）/ `tool/luadis53.py`（反汇编，`--all` 可全量）。

## NameTable.txt（说话人名替换表）

引擎的说话人名替换表，放在 `Rio.arc` 里。脚本中的 `%L?<名>` 标记会被拿去查它。
**这是角色名汉化的通路** —— 表是 UTF-16LE，不受脚本内窄字节 CP932 的限制。

### 格式

- **编码**：UTF-16LE，**无 BOM**
- **换行**：CRLF
- **分隔**：制表符；**左右两列都带完整标记前缀**

```text
%LCTaichi	%LC太一
%LCMisato	%LC见里
```

### 关键规则

**表键必须等于脚本里的标记。** 每个游戏用哪个字母由脚本生成时固定
（CROSS†CHANNEL 用 `%LC`，A Sky Full of Stars 用 `%LF`），**与语言切换无关**
（ASF 的英文版与简中版都用 `%LF`）。

`%LC` / `%LF` / `%LR` 都是 `AdvHD.exe` 命令标记表里的合法标记：

```text
%LC  %C   %TS  %TE  %AS  %AE  %WS  %WE  %LR  %LL  %K   %P   %p   %N   %O
%FE  %FS  %LF  %E   %V   %W   %T   %XS  %XE  %X   ...
```

### 存放位置

ASF 官方简中把此表放在 **`zh-CN/Rio.arc`**（语言目录）；CROSS†CHANNEL 无语言目录，
本项目放在**主 `Rio.arc`**（待实机验证是否足够）。

详见 [engine-mechanics.md](engine-mechanics.md)「名字替换表」。
