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
- 无严格长度限制
- 支持 8-10 字节 stem（从 Steam 版脚本统计得出）
- 可以使用 `ORG_` 前缀（推测，需实测验证）

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

## 子图层机制

### A Sky Full of Stars 的实现

A Sky Full of Stars 有复杂的子图层系统：
- `ev01/ev01blink/ev01talk` 三元组
- 引擎根据 PNA 内部的图层结构自动生成子图层
- 用于眨眼、口型动画

### CROSS†CHANNEL 的简化

CROSS†CHANNEL 的 LAYER_ORDER **不包含 blink/talk 子图层**：
- 只有基础槽位（ev, st01-st12, bg01-bg03 等）
- 引擎可能仍根据 PNA 内部结构处理动画，但不显式注册子图层
- 动画机制可能更简化

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

## 引擎版本差异

### A Sky Full of Stars vs CROSS†CHANNEL

虽然两者使用同一引擎，但版本可能不同，导致细微差异：

| 特性 | A Sky Full of Stars | CROSS†CHANNEL |
|------|---------------------|---------------|
| 槽位数量 | 114 个 | 19 个 |
| ev 槽设计 | 双槽（ev01/ev02） | 单槽（ev） |
| ev 槽限制 | 7 字节 stem 硬限制 | 待测试 |
| blink/talk | ✅ 有 | ❌ 无 |
| Script.arc | ❌ 无（Steam 版） | ✅ 有（Res 303） |

**影响**：
- 不能直接套用 A Sky Full of Stars 的所有结论
- 关键机制需要重新实测验证
- 命名策略可能需要调整

## 实测验证清单

为了确保补丁的正确性，以下机制需要实测验证：

- [ ] ev 槽对 PNA stem 长度的限制（是否有 7 字节限制）
- [ ] ev 槽对 `ORG_` 前缀的容忍度（能否使用前缀隔离）
- [ ] st 槽对 `ORG_` 前缀的支持（推测支持，需验证）
- [ ] 资源缺失时的引擎行为（0x33 卡死、0x66 容忍）
- [ ] lng 文件的文本替换机制（何时触发、如何匹配）
- [ ] 字体文件的加载机制（PTF 格式、字符集支持）
- [ ] Script.arc 的加载时机（是否必须、加载顺序）

## 经验总结

### 逆向策略

1. **优先实测**：对于资源注册、显示效果等问题，A/B 测试 > 静态逆向
2. **参考已有方案**：Res 303 已验证可行，优先复用其方案
3. **渐进式验证**：从简单场景开始测试，逐步覆盖复杂情况
4. **文档记录**：每次实测结果都记录到文档，避免重复验证

### 工具选择

- **静态分析**：IDA Pro / Ghidra（了解整体结构）
- **动态调试**：x64dbg / WinDbg（追踪运行时行为）
- **文件分析**：010 Editor / HxD（分析二进制格式）
- **实测验证**：修改游戏文件 + 运行游戏（最直接的验证方式）
