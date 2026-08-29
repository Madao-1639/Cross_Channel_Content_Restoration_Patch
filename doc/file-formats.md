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

**`0x07` - 场景跳转 (Jump)**

格式: `\x07<target>\x00\xff<flag><padding>`

- `<target>`: 目标场景名 (如 "CCC0001_EN")
- `<flag>`: 跳转标志
  - `\x08`: 标准场景跳转 (最常见)
  - `\x00`: **错误标志** (会导致崩溃)
- `<padding>`: 7字节零填充

示例:
```
\x07CCA0002_EN\x00\xff\x08\x00\x00\x00\x00\x00\x00\x00
```

**重要**: 跳转指令前必须是 `\x15\x00\x00` (清除对话框)，**不能**是 `\x16\x00\x00` (图层命令块)

**`0x04` - 调用脚本 (Call)**

格式: `\x04<function_name>\x00`

常见函数:
- `LAYER_ORDER`: 设置图层顺序

#### 对话系统指令

**`0x14` - 对话块 (Dialogue)**

格式: `\x14<id>\x00\x00\x00<content>`

- `<id>`: u8, 对话序号 (**必须从0开始连续递增**)
- `<content>`: 对话内容
  - 真实对话: `char\x00<text>%K\x00\x00`
  - 空白占位: `\x00%N\x00\x00`
  - 暂停标记: `\x00%P\x00\x00`

对话文本标记:
- `%K`: 对话结束，等待玩家点击
- `%P`: 清除文本框 (常与 `%K` 组合: `%K%P`)
- `%N`: 空白占位 (立即跳过)
- `%L<name>\x00`: 设置说话角色名
- `\d...\d`: 延迟显示效果

**`0x15\x00\x00` - 清除对话框**

作用: 清除屏幕文本框，准备下一场景状态

出现位置: 总是在对话结束标记 (`%K`, `%N`, `%P`) 之后

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

**完整背景切换块模板** (110字节):
```
\x16\x00\x00                              # 图层命令块起始
\x64\x00                                  # 淡入淡出参数
\x33bg01\x00<FILENAME.PNG>\x00\x01\x01    # 加载背景
\x04LAYER_ORDER\x00                       # 调用LAYER_ORDER
\x09\x00\x0a\x00\x00\x00\x80\x3f         # 设置变量10=1.0
\x09\x00\x0b\x00\x00\x00\x80\x3f         # 设置变量11=1.0
\x09\x00\x0c\x00\x00\x00\x80\x3f         # 设置变量12=1.0
\x09\x00\x0d\x00\x00\x00\x80\x3f         # 设置变量13=1.0
Fbg01\x00<32字节效果参数>
```

**`0x66` - 特效遮罩**

格式: `\x66...`

说明: 缺失时容忍 (仅画面瑕疵)

#### 音频控制指令

**`0x1e` - BGM加载播放**

格式: `\x1e<slot>\x00<filename>\x00<params>\xff\xff`

- `<slot>`: 音乐槽位 (如 "bgm01")
- `<filename>`: BGM文件名 (如 "BGM015.OGG")
- `\xff\xff`: BGM块结束标记

**`0x2e\x28` - 语音播放**

格式: `\x2e\x28char<character>\x00<filename>\x00<15字节固定参数>`

示例:
```
\x2e\x28charYOU\x00YOU025A5000.OGG\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x0a\x00\x00e\x00\x00\x00\x00\x00\x00\x00\x00
```

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

#### 对话ID连续性规则

**规则**: 所有 `\x14` 指令的ID必须从0开始连续递增，不能跳号

**违反后果**: 对话跳过、场景卡死、回溯系统异常

#### 跳转指令位置规则

**正确**:
```
<对话>%K\x00\x00
\x15\x00\x00                    # 清除对话框
\x07<TARGET>\x00\xff\x08...     # 直接跳转
```

**错误** (Res303 CNR bug):
```
\x15\x00\x00
\x16\x00\x00                    # 错误! 引擎误解为图层命令
\x07<TARGET>\x00\xff\x00...     # 错误标志字节
```

### CNR场景跳转Bug案例

Res303的全部12个CNR场景在跳转指令处使用了错误格式:

错误格式:
```
\x15\x00\x00\x16\x00\x00\x07CCC0001_EN\x00\xff\x00...
```

正确格式:
```
\x15\x00\x00\x07CCC0001_EN\x00\xff\x08...
```

修复方法:
```python
# 删除错误的 \x16\x00\x00 前缀，修正标志字节
错误: rb'\x16\x00\x00\x07([A-Z0-9_]+)\x00\xff\x00'
正确: rb'\x07\1\x00\xff\x08\x00\x00\x00\x00\x00\x00\x00'
```

修复后每个脚本减少3字节，全部12个场景可正常进入和跳转。

### 字节码速查表

| Opcode | 功能 | 参数格式 |
|--------|------|----------|
| 0x04 | 调用函数 | `<name>\x00` |
| 0x07 | 场景跳转 | `<target>\x00\xff<flag><pad>` |
| 0x09 | 设置变量 | `\x00<var_id><value>` |
| 0x0b | 设置变量(短) | `<var><val>` |
| 0x11 | 启动计时器 | `<name>\x00\x00<time>` |
| 0x12 | 等待计时器 | `<name>\x00\x00\x00` |
| 0x14 | 对话块 | `<id>\x00\x00\x00<content>` |
| 0x15 | 清除对话框 | `\x00\x00` |
| 0x16 | 图层命令块 | `\x00\x00<cmds>` |
| 0x1e | BGM播放 | `<slot>\x00<file>\x00<params>\xff\xff` |
| 0x2e | 语音播放 | `\x28char<name>\x00<file>\x00<params>` |
| 0x33 | 加载槽位 | `<slot>\x00<file>\x00<flags>` |
| 0x34 | 显示PNA | `<slot>\x00<file>\x00\x01\x01` |
| 0x64 | 淡入淡出参数 | `\x00` |
| 0x66 | 特效遮罩 | `...` |

### 命名规则

CROSS†CHANNEL Steam 版脚本命名格式：

```
{PREFIX}{编号}{分支}_{语言}.ws2
```

**示例**：
- `CC0TOU_en.ws2` - CC0 章节，TOU（透）线，英文版
- `CCA0001_en.ws2` - CCA 章节，第 0001 场景，英文版
- `CCB1014D_en.ws2` - CCB 章节，第 1014 场景，D 分支，英文版

**章节前缀**：
- `CC0-CC6`：主要角色线（TOU/MIS/KIR/MIK/YOU/TOM/SAK）
- `CCA`：A 章节场景
- `CCB`：B 章节场景

**语言后缀**：
- `_en`：英文版（Steam 原版全部脚本）
- 无后缀：原版日文脚本（待还原的 H 场景脚本）

### 跳转指令

**两种跳转**：
- `0x04 <SCRIPT_NAME>\x00` → call（调用子流程，可返回）
- `0x07 <SCRIPT_NAME>\x00` → jump（直接跳转，不返回）

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
- 带日文的资源名必须用 `name.encode('shift_jis')` 处理

### 改写规则

改写资源引用必须遵循以下原则：

1. **显示指令上下文精确替换**：使用 NUL 前缀的精确模式
   ```python
   # ❌ 错误：会命中已改名的尾巴，导致双前缀
   dec.replace('TCCT1000', 'ORG_TCCT1000')
   
   # ✅ 正确：精确匹配显示指令格式
   dec.replace(b'\x00' + old_name, b'\x00' + new_name)
   ```

2. **幂等性**：脚本必须可重复运行，每次运行前检查"已满足则跳过"

3. **回读校验**：改写后必须回读验证，确认修改成功

4. **控制台输出**：Windows 控制台默认 GBK/cp936，避免使用非 ASCII 字符
   - 推荐：`[OK]`/`[!]` 等 ASCII 标记
   - 或在脚本开头加：`sys.stdout.reconfigure(encoding='utf-8')`

## LNG 文本文件格式

LNG 文件是 AdvHD 引擎用于文本汉化的格式，每个 `.ws2` 脚本对应一个同名的 `.lng` 文件。

### 基本机制

- **一一对应**：`CC0TOU_en.ws2` ↔ `CC0TOU_en.lng`
- **存储位置**：与 .ws2 同在 Rio.arc 中
- **编码方式**：同样使用 rot6 混淆
- **内容替换**：引擎运行时，lng 中的文本替换 ws2 中的对应文本

### 格式特征

从 Res 303 提取的样本：
```python
# 原始字节（混淆）
b'\xf3\x01\x00\x00\x18\x00\x1e\x00 \x00...'

# rot6 解码后
b'\xfc@\x00\x00\x06\x00\x87\x00\x08\x00...'
```

**注意**：lng 文件格式细节需进一步逆向分析，当前已知：
- 使用 rot6 混淆（与 ws2 相同）
- 包含文本偏移表和替换文本
- 文件头部有计数字段

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

**注意**：Steam 原版不包含 Script.arc，这是 Res 303 的扩展功能。
