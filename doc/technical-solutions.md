# 技术方案

本文档记录 CROSS†CHANNEL 内容还原与汉化补丁的技术方案。

## 当前状态

项目处于**分析阶段完成**，技术方案已明确。以下内容为基于 Res 303 分析的实施方案。

## 核心技术方案

### 1. 资源命名空间隔离

**目标**：解决原版和 Steam 版同名资源内容不同的问题。

**分析结果（已完成 ✅）**：
- Res 303 使用 `CN_` 前缀命名所有新增资源
- **零资源冲突**：没有同名但内容不同的资源
- **无需 ORG_ 前缀**：可直接使用 CN_* 命名

**方案**：
1. **直接复用 Res 303 的命名**：
   - 新增 H 场景 CG：`CN_EVCC*.PNG`（36 个）+ `CN_SGCC0020.PNG`（1 个）
   - 无需改写资源引用
   - 无需添加 ORG_ 前缀

2. **遵守资源分类规则（含 1 个例外）**：
   - ⚠️ Res 303 将全部 37 个 CN_* 资源放入 Graphic.arc（违反分类规则）
   - ✅ 本项目将 36 个 CN_EVCC*.PNG 放入 **Chip2.arc**
   - ⚠️ 例外：`CN_SGCC0020.PNG` 放入 **Graphic.arc**（Steam 原版 Graphic.arc 本身混有 92 个 SGCC* 系统图，此资源延续该命名族，归入 Graphic.arc 与既有惯例一致）

**状态**：✅ 已实施（`script/build_patch.py`，全量验证通过）

### 2. 穿插式调用链组织

**目标**：在 Steam 版段落间插入原版 H 场景脚本，形成完整剧情。

**分析结果（已完成 ✅）**：
- **新增脚本**：12 个 H 场景（CNR*.ws2）
- **修改脚本**：11 个入口点（Steam 脚本跳转被重定向）
- **调用模式**：完美穿插式（入口 → H 场景 → 原目标）

**方案**：
1. **直接复用 Res 303 的脚本**：
   - 从 Res 303 Rio.arc 提取 12 个 CNR*.ws2
   - 从 Res 303 Rio.arc 提取 11 个修改的入口脚本
   - 无需改写跳转（Res 303 已处理）

2. **完整调用链映射**（已验证）：
   - CCA0025C_en → CNR0001_EN → CCA0029_EN
   - CCB1014C_en → CNR0002_EN → CCB0020_EN
   - CCB2013_en → CNR0003_EN → CCB2014_EN
   - CCB2101_en → CNR0004_EN → CCB2019_EN
   - CCC0000_en → CNR0005_EN, CNR0105_EN → CCC0001_EN, CCC0002_EN
   - CCC3027_en → CNR0006_EN → CCC3028_EN
   - CCC4022_en → CNR0007_EN → CCC4023_EN
   - CCD0022A_en → CNR0008_EN → CCD0000_EN
   - CCD1001B_en → CNR00009_EN → CCD1001C_EN
   - CCD4003A_en → CNR00010_EN → CCD0023A_EN
   - CCD5001A_en → CNR00011_EN → CCD5001B_EN

**状态**：✅ 方案已确定（直接复用 Res 303）

### 3. 文本汉化（LNG 文件）

**目标**：将英文剧情文本替换为简体中文。

**分析结果（已完成 ✅）**：
- **LNG 文件**：340 个（覆盖 90.7% 脚本）
- **格式**：rot6 混淆 + UTF-16LE 编码
- **翻译来源**：
  - 主线剧情：CROSS†CHANNEL 中文化项目官方译文
  - Steam 新增内容：GPT-5.6-sol 机器翻译

**方案**：
1. **直接复用 Res 303 的 lng 文件**：
   - 从 Res 303 Rio.arc 提取 340 个 .lng 文件
   - 放入新补丁的 Rio.arc

2. **lng 文件格式**（已分析）：
   - 使用 rot6 混淆（与 ws2 相同）
   - 包含 UTF-16LE 编码的中文文本
   - 头部结构：`0x000040fc`（偏移表/计数）

**状态**：✅ 方案已确定（直接复用 Res 303）

### 4. 字体支持

**目标**：替换字体文件以支持汉字显示。

**分析结果（已完成 ✅）**：
- **Steam 原版**：FOT-MatissePro-B/M（7.0-7.6 MB）
- **Res 303**：FOT-MatissePro-B/M（18.5 MB）
- **增幅**：2.4-2.6 倍（扩展汉字字符集）

**方案**：
1. **直接复用 Res 303 的 Fonts.arc**：
   - 复制 Res 303 的 Fonts.arc
   - 替换 Steam 原版

**状态**：✅ 方案已确定（直接复用 Res 303）

### 5. 系统界面汉化

**目标**：汉化菜单、设置、画廊等系统界面。

**分析结果（已完成 ✅）**：
- **Script.arc**：14 个 Lua 脚本（图形化菜单系统）
- **SysGraphic.arc**：30 个成员（系统界面图片）

**方案**：
1. **直接复用 Res 303 的 Script.arc**：
   - 复制 Res 303 的 Script.arc
   - 包含图形化菜单和多语言支持

2. **复用 Res 303 的 SysGraphic.arc**：
   - 对比后决定是否复用（待确认差异）

**状态**：✅ 方案已确定（优先级中等）

## 工具脚本

### 已有工具（从 A Sky Full of Stars 迁移）✅

1. **arcbuild.py**：Arc 归档读写工具
   - `read_raw(path)`：读取 Arc 文件为 [(name_bytes, data)]
   - `write_arc(members, output_path)`：写入 Arc 文件
   - `verify(path)`：验证 Arc 文件完整性
   - `normalize_arc_padding(path)`：去除 null padding

2. **ws2.py**：WS2 脚本编解码工具
   - `decode(raw)`：解码 ws2 脚本（rot6）
   - `encode(data)`：编码 ws2 脚本（rot6）
   - `extract_pna_refs(decoded_data)`：提取 PNA 引用
   - `extract_png_refs(decoded_data)`：提取 PNG 引用
   - `extract_calls(decoded_data)`：提取 call 跳转
   - `extract_jumps(decoded_data)`：提取 jump 跳转
   - `extract_all_jumps(decoded_data)`：提取全部跳转

**状态**：✅ 已完成迁移并测试
   - `verify(path)`：验证 Arc 文件完整性
   - `normalize_arc_padding(path)`：去除 null padding

2. **ws2.py**：WS2 脚本编解码工具
   - `decode(raw)`：解码 ws2 脚本（rot6）
   - `encode(data)`：编码 ws2 脚本（rot6）

**状态**：✅ 已验证兼容性，可直接使用

### 待开发工具

1. **资源提取工具**：
   - 从 Res 303 提取指定资源
   - 按分类规则组织资源

2. **Arc 打包工具**：
   - 重新打包 Rio.arc（脚本 + lng）
   - 重新打包 Chip2.arc（事件 CG + H 场景 CG）
   - 重新打包 Voice.arc（语音）

3. **验收测试工具**：
   - 调用链完整性检查
   - 资源配对验证
   - Arc 文件规范化检查
   - **资源分类规则检查**

**状态**：✅ 已开发并验证（`script/build_patch.py`、`script/final_verification.py`，39 项检查全部通过）

## 实施步骤

### ~~阶段 1：基础设施~~（✅ 已完成）

1. ✅ 建立文档体系
2. ✅ 迁移工具脚本
3. ✅ 验证 Arc/WS2 格式兼容性

### ~~阶段 2：资源分析~~（✅ 已完成）

1. ✅ Res 303 资源分析
2. ✅ 资源冲突识别（零冲突）
3. ✅ 资源映射表建立（无需，直接复用）

### ~~阶段 3：调用链分析~~（✅ 已完成）

1. ✅ Steam 版脚本分析
2. ✅ H 场景位置识别（12 个 CNR*.ws2）
3. ✅ 调用链映射表建立（11 个入口点）

### ~~阶段 4：内容还原实施~~（✅ 已完成）

1. ✅ **复用 Res 303 脚本**（`script/build_patch.py: build_rio()`）
   - 从 Res 303 Rio.arc 提取 12 个 H 场景脚本（CNR*.ws2）+ 11 个修改的入口脚本
   - 入口脚本以 res303 版本（跳转已重定向）覆盖 backup 版本，其余 353 个 backup 脚本原样保留

2. ✅ **复用 Res 303 资源（调整存储位置）**（`build_graphic()` / `build_chip2()`）
   - 36 个 CN_EVCC*.PNG → **Chip2.arc**（遵守分类规则）
   - 1 个 CN_SGCC0020.PNG → **Graphic.arc**（延续既有 SGCC 系统图族命名惯例，例外情形）
   - Voice.arc / Fonts.arc / Script.arc / SysGraphic.arc 整体复用 res303 版本（内容已含全部新增语音/字体/系统界面）

3. ✅ **复用 Res 303 汉化**
   - 340 个 lng 文件随 `build_rio()` 一并提取，放入 Rio.arc
   - Fonts.arc / Script.arc 直接复制 res303 版本

### ~~阶段 5：打包与验证~~（✅ 已完成）

1. ✅ **重新打包 Arc 文件**（产出至 `asset/`）
   - Rio.arc：717 个成员（364 保留 + 353 新增）
   - Chip2.arc：235 个成员（199 + 36 CN_EVCC*）
   - Graphic.arc：716 个成员（715 + CN_SGCC0020.PNG）
   - Voice.arc / Fonts.arc / Script.arc / SysGraphic.arc：直接复制 res303 版本
   - Chip1.arc / SysVoice.arc：本次无变化，不生成，安装器保留玩家 Steam 原文件

2. ✅ **完整性验证**（`script/final_verification.py`，39 项检查全部通过）
   - Arc 文件规范化检查（无 null padding）
   - SHA256 校验稳定性（重复读写哈希不变）
   - 调用链完整性检查（11 入口 + 12 CNR 出口全部核对）
   - 资源配对验证（CNR 脚本引用的全部 PNG 均在 Graphic.arc+Chip2.arc 中找到）
   - 资源分类规则检查（Chip2.arc 仅含 EVCC*/CN_EVCC*；Graphic.arc 不含 CN_EVCC*）

### 阶段 6：测试与发布（⏳ 待开始）

1. **实机测试**
   - 测试所有 12 个 H 场景
   - 验证入口和出口
   - 测试文本显示
   - 测试语音播放

2. **打包发布**
   - 生成安装器
   - 编写用户文档
   - 发布补丁

## 技术难点与对策

### 难点 1：分支复杂度

**问题**：CROSS†CHANNEL 的分支数量可能很多。

**对策**：
- 开发自动化工具分析调用链
- 分阶段处理（先主线，后分支）
- 建立分支映射表

### 难点 2：资源冲突识别

**问题**：同名但内容不同的资源数量未知。

**对策**：
- 使用 SHA256 批量比对
- 开发自动化工具生成映射表
- 优先处理高频使用的资源

### 难点 3：LNG 格式逆向

**问题**：lng 文件格式未公开文档。

**对策**：
- 优先复用 Res 303 的 lng 文件
- 如需修改，再进行逆向分析
- 参考 Res 303 的样本文件

### 难点 4：原版资源获取

**问题**：可能无法获取原版游戏。

**对策**：
- 完全依赖 Res 303 的资源
- 验证 Res 303 资源的完整性和正确性
- 考虑授权和引用方式

## 风险管理

### 技术风险

| 风险 | 影响 | 概率 | 对策 |
|------|------|------|------|
| LNG 格式复杂 | 延期 | 中 | 优先复用 Res 303 |
| 资源冲突多 | 工作量大 | 中 | 自动化工具 |
| 分支调用链复杂 | 延期 | 高 | 分阶段处理 |
| 原版资源缺失 | 阻塞 | 低 | 依赖 Res 303 |

### 工程风险

| 风险 | 影响 | 概率 | 对策 |
|------|------|------|------|
| 工具开发延期 | 延期 | 中 | 复用已有工具 |
| 测试不充分 | 质量问题 | 中 | 建立测试清单 |
| 文档不完整 | 维护困难 | 低 | 严格执行文档更新 |

## 参考资料

- A Sky Full of Stars 内容还原补丁项目的技术方案
- Res 303 补丁的实现方式
- AdvHD 引擎的逆向分析笔记
