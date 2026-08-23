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

### 关键指令

| 操作码 | 格式 | 说明 |
|--------|------|------|
| `0x34` | `\x34 <slot>\x00 <FILE.PNA>\x00 \x01 \x01` | 显示 PNA 图层资源 |
| `0x04` | `\x04 <SCRIPT_NAME>\x00` | 调用其他脚本（call，可返回） |
| `0x07` | `\x07 <SCRIPT_NAME>\x00` | 跳转到其他脚本（jump，不返回） |
| `0x33` | `\x33 <slot>\x00 <FILE.PNG>\x00 \x01 \x01` | 硬载入资源（背景），缺失时会卡死 |
| `0x66` | `\x66 ...` | 特效遮罩，缺失时容忍（仅画面瑕疵） |
| `0x0b` | `\x0b <u16>\x01` | 设置变量（用于 gallery id 等） |

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
