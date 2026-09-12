# CROSS†CHANNEL Steam 版内容还原与汉化补丁项目

## 项目目的

为 Steam 版游戏《CROSS†CHANNEL》制作内容还原与汉化补丁，恢复被删除的原版场景内容，并将英文文本汉化为简体中文。

**核心目标**：
1. 保留 Steam 成就系统
2. 恢复完整 H 场景内容
3. 零破坏性修改（不替换 Steam 原有资源）
4. 汉化英文文本为简体中文

## 项目背景

### 技术挑战

1. **同名冲突**：Steam 版和原版的同名资源内容不同，layer_id 是纯位置量，不能直接替换
2. **接缝错位**：Steam 删 H 场景时会在宿主脚本里留下整场**删节版**，直接追加还原内容会造成顺序倒置 + 局部重复
3. **资源缺失**：Steam 删除 H 场景时一并删除了对应的 CG、语音等资源
4. **语言障碍**：Steam 版只有英文，需要汉化全部文本
5. **字体支持**：原版字体不支持汉字显示，需要替换字体文件
6. **脚本格式差异**：原版 WSC 与 Steam WS2 指令集不同，必须逐指令转换

### 解决方案

- **资源隔离**：同名冲突一律改名（CG → `EVCC9XXX`，立绘 → `TCxx0nnn`→`TCxx1nnn`），禁止覆盖 Steam 原文件
- **就地插入**：把源 WSC 的删除区间转换后插进调用脚本，不引入新脚本、不增加跳转
- **资源补全**：从原版补入 Steam 缺失的资源（语音可直接复用 OGG）
- **文本汉化**：lng 文件按池槽位替换文本，人名走 `NameTable.txt`
- **字体替换**：复用支持汉字的字体文件
- **参考但仍需验证**：Res 303 的方案可作参考，但其产物未经 Steam 版验证

## 工程约定

### 0. 内容忠实性原则（最高优先级）

**绝对禁止修改游戏原始内容和演出**

本项目的目标是**还原**被删除的内容，而不是**创作**或**修改**内容。任何改变游戏演出的行为都是严格禁止的。

**禁止的行为**：
- ❌ 添加原版不存在的 BGM 指令
- ❌ 移除原版存在的 BGM 停止指令
- ❌ 修改场景的演出时序（如提前/延后触发事件）
- ❌ 修改对白内容（汉化除外，汉化也必须忠实原文）
- ❌ 修改 CG 显示逻辑
- ❌ 修改跳转逻辑（调用链整合除外）
- ❌ 任何"我觉得应该这样"的主观修改

**允许的行为**：
- ✅ 修复技术性 bug（如文件名白名单问题）
- ✅ 调用链整合（在 Steam 场景间插入 H 场景）
- ✅ 资源命名调整（为了技术兼容性）
- ✅ 忠实的文本汉化

**原则**：
1. **原版数据是权威信源** - 场景内容、资源引用、演出时序一律以**解密后的原版 WSC**为准；Res 303 的产物只作参考（其还原脚本未经 Steam 版验证，且存在格式错误）
1. **缺少的信息不应猜测补全** - 如果无法从原版确认某个设计，保持现状并如实记录
2. **演出问题应报告而非修改** - 如果发现演出异常（如 BGM 消失），记录问题但不擅自修改，除非能从原版游戏确认正确行为

### 1. 文档同步要求

**任何大型修改都必须同步到文档中**

- 修改文件格式处理 → 更新 `doc/file-formats.md`
- 新增资源命名规则 → 更新 `doc/pna-resources.md`
- 修改调用链结构 → 更新 `doc/call-chain.md`
- 修改汉化方案 → 更新 `doc/localization.md`
- 更新还原目标 → 更新 `doc/restoration-targets.md`
- 修改技术方案 → 更新 `doc/technical-solutions.md`
- 发现引擎新机制 → 更新 `doc/engine-mechanics.md`
- 发现新问题/解决方案 → 更新 `doc/lessons-learned.md`
- 修改验收标准 → 更新 `doc/acceptance-criteria.md`

### 2. 临时文件管理

**生成的临时文件存放在 `tmp/` 目录下，任务结束后清除**

临时文件包括但不限于：
- 解码后的 WS2 脚本
- 提取的资源文件
- 中间处理结果
- 调试输出文件
- 一次性使用的工具脚本

**清理原则**：
- 任务完成后立即清理 `tmp/` 目录
- 重要的中间结果应移动到 `releases/` 或其他持久化目录
- 不将临时文件提交到版本控制

**脚本存放规则**：
- **长期复用的工具**：放入 `tool/`（如 arcbuild.py、ws2.py）
- **项目流程脚本**：放入 `script/`（如 build_patch.py、final_verification.py）
- **一次性/临时脚本**：放入 `tmp/`（任务结束后删除）

### 3. 测试与发布流程

**测试阶段**：
- 只关注 `asset/` 目录下的文件
- 测试时直接将 `asset/` 下的文件覆盖到游戏目录
- **无需关心 `payload/` 中的增量内容**（payload 是给用户用的）

**发布阶段**：
- 从 `asset/` 生成增量补丁到 `payload/`
- 制作安装器打包 `payload/` 内容

**原则**：开发和测试使用完整文件（asset/），发布时才制作增量包（payload/）

### 3. 库和脚本组织

**tool/ 是 Python 包**（通过 `__init__.py`），提供通用库：

```
tool/
├── __init__.py      （包标记）
├── arcbuild.py      （Arc 文件读写）
├── ws2.py           （WS2 脚本编解码）
├── ws2disasm.py     （WS2 反汇编）
├── wsc.py           （原版 WSC 反汇编）
├── wsc2ws2.py       （WSC→WS2 转换，含切片模式）
├── lng.py           （LNG 编解码 + CCS 解析）
├── luac53.py        （Lua 5.3 字节码解析）
├── luadis53.py      （Lua 5.3 反汇编）
└── install.py       （安装器入口）
```

脚本通过 `from tool import arcbuild, ws2` 等方式导入，所有脚本使用相对于项目根的路径。从项目根执行：

```bash
python script/build_patch.py            # 按方案产出 asset/（完整文件）
python script/generate_payload.py       # 生成增量补丁到 payload/（含回读校验）
python script/final_verification.py     # 全量验收
python script/audit_inline.py           # 就地插入回归守卫
python script/verify_ws2_conventions.py # 转换器约定回归
```

各脚本的职责见 `doc/technical-solutions.md`「工具链」。

### 4. 脚本开发规范

**幂等性**：
- 所有工具脚本必须支持重复运行，临时文件除外
- 运行前检查"已满足则跳过"
- 不因重复运行产生错误结果

**回读校验**：
- 改写资源引用后必须回读验证
- 修改偏移指针后必须验证指向正确
- 生成归档后必须验证成员完整性

**编码规范**：
- 资源名使用 Shift-JIS 编码
- 控制台输出使用 ASCII 或配置 UTF-8
- 使用 NUL 前缀的精确替换模式

**编码处理注意事项**：

*Shift-JIS 与中文字符*：
- **脚本内容**（资源名、对话）使用 **Shift-JIS** 编码
- **中文字符不能用 Shift-JIS 编码**，会抛出 `UnicodeEncodeError`
- 搜索/匹配脚本内容时：
  - 日文文本：`text.encode('shift_jis')`
  - 中文文本：**不要尝试编码**，直接在已解码的 UTF-8 字符串中搜索

*常见错误示例*：
```python
# ❌ 错误：尝试用 Shift-JIS 编码中文
search_text = '来露娜，已经是个大人了哦'  # 中文
search_bytes = search_text.encode('shift_jis')  # UnicodeEncodeError!

# ✅ 正确：中文是翻译后的，不在脚本原文中
# 应该搜索日文原文或关键词
search_text = 'ころな'  # 日文
search_bytes = search_text.encode('shift_jis')  # OK
```

*解码方法*：
```python
# 从脚本中提取文本（已是 bytes）
decoded = ws2.decode(raw)  # bytes

# 尝试解码为字符串
try:
    text = decoded.decode('shift_jis')  # 脚本内容
except UnicodeDecodeError:
    text = decoded.decode('shift_jis', errors='ignore')  # 容错模式

# 资源名提取（Shift-JIS）
resource_name = b'D\x82\xb1\x82\xeb\x82\xc8_01L'  # bytes
resource_str = resource_name.decode('shift_jis')  # 'Dころな_01L'
```

*搜索脚本内容的正确方法*：
```python
# 1. 搜索日文关键词（编码为 Shift-JIS bytes）
keyword = 'ころな'.encode('shift_jis')
if keyword in decoded:
    print('Found')

# 2. 搜索资源引用（带 NUL 上下文）
resource = 'Dころな_01L.PNA'.encode('shift_jis')
pattern = b'\x00' + resource + b'\x00'
if pattern in decoded:
    print('Found resource reference')

# 3. 搜索中文内容（在中文化文件中，不在脚本中）
# 脚本中不包含中文，中文是外部翻译文件
# 应该去 zh-CN/Rio.arc 的 lng 文件中搜索
```

*资源名大小写*：
- Arc 文件内的资源名**保留原始大小写**
- 搜索时使用 `.lower()` 进行不区分大小写匹配
- 示例：`EFBG00_01.PNG` vs `efbg00_01.png`（实际文件是 `.PNG`）

### 4. 命名规范

**脚本命名**：
- `*_en.ws2`：Steam 版英文脚本。**本补丁不新增脚本名** —— 原版内容**就地插入**这 363 个脚本中的 12 个，不引入还原脚本、不增加跳转（见 `doc/call-chain.md`）

**PNA 资源命名**：

*立绘（角色标识+分类+编号+变体）*：
- 格式：`T{角色代码}{分类}{编号}{变体}.pna`
- 角色代码：CMM（見里）、CYM（美希）、CSK（霧）、CKT（冬子）、CST（友貴）、CHY（曜子）、CCN（七香）、CSH（桜庭）、CDY（遊紗）、CSY（新川）等
- 示例：`TCKT1002B.pna`（冬子的立绘 B 变体）

*事件 CG*：
- 格式：`EVCC{编号}{变体}.PNG`，位于 `Chip2.arc`
- 补丁新增的还原 CG **一律落在 `EVCC9XXX` 段**（引擎对 Chip2 实施 `EVCC` 前缀白名单，`CN_` 前缀会被静默忽略），编号按「基号 + 脚本引用顺序」分配
- 对照用 `python script/renumber_evcc9xxx.py --check`

*背景*：`BGCC*.png`，位于 `Chip1.arc`（本补丁不改动）

详见 `doc/pna-resources.md`。

**章节前缀**：
- `CC0-CC6`：主要角色线（TOU/MIS/KIR/MIK/YOU/TOM/SAK）
- `CCA`：A 章节场景
- `CCB`：B 章节场景

### 5. 验收流程

每次重大修改后必须执行：

1. 运行 `final_verification.py` 全量验证
2. **流水线复跑必须 0 处改动** —— `build_patch.py` 与其各步（含 `realign_lng_to_ws2.py`）都是幂等的；复跑报出改动即说明某步破坏了自己的输入
3. 检查调用链完整性
4. 验证资源配对正确性
5. 验证 Arc 文件规范化（无 null padding）
6. 测试关键场景功能
7. 更新文档

**Arc 文件规范化检查**：
- `arcbuild.verify()` 会检测表末尾的 null padding 并拒绝
- 若发现 padding，运行 `arcbuild.normalize_arc_padding(path)` 清理
- 规范化后哈希稳定，安装器校验通过
- 详见 `doc/lessons-learned.md` §6

### 6. 备份策略

**重要修改前必须备份**：
- Arc 归档修改前备份（如 `Rio.arc.before_xxx`）
- 记录备份时间和修改原因
- 验证备份文件完整性

### 7. 版本控制

**不提交的文件**：
- `tmp/` 目录下的所有文件
- 备份文件（*.before_*）
- 临时输出文件
- 大型二进制文件（使用 Git LFS 或外部存储）

**必须提交的文件**：
- 所有 Python 脚本
- 文档文件（`doc/` 下所有文件）
- 配置文件
- SHA256SUMS

### 8. 调试规范

**日志输出**：
- 使用结构化日志（时间戳 + 级别 + 消息）
- 关键操作前后记录状态
- 错误时输出完整上下文

**错误处理**：
- 捕获并记录所有异常
- 提供清晰的错误信息
- 建议可能的解决方案

### 9. 性能要求

- 不添加冗余资源、脚本
- 生成的 payload 尽可能小（增量模式）

### 10. 安全要求

- 不修改 Steam 原版文件（仅读取用于对比）
- 验证所有输入文件的完整性
- 使用 SHA256 校验关键文件
- 谨慎处理用户路径输入

## 技术栈

- **语言**：Python（使用 mamba/conda 管理，优先使用 mamba）
- **编码**：Shift-JIS（脚本内资源名与文本）、UTF-16LE + XOR 0x2C（lng）、UTF-8（文档）
- **归档格式**：自定义 Arc 格式（UTF-16LE 文件名）
- **脚本格式**：WS2 二进制格式（rot6 混淆）
- **文本格式**：LNG 文件（文本替换机制）
- **资源格式**：PNA（图层）、PNG（背景）、OGG（语音）
- **引擎**：AdvHD.exe（MoeNovel Amaneko 系）
- **脚本扩展**：Lua 5.3（系统界面）

## 参考文档

- [doc/file-formats.md](doc/file-formats.md) - 游戏文件格式规范（Arc/WS2/LNG/Script.arc）
- [doc/pna-resources.md](doc/pna-resources.md) - PNA 资源机制与命名规则
- [doc/localization.md](doc/localization.md) - 汉化方案（lng 文件、字体、系统界面）
- [doc/call-chain.md](doc/call-chain.md) - 调用链组织与就地插入接线
- [doc/restoration-targets.md](doc/restoration-targets.md) - 还原目标与剩余工作
- [doc/technical-solutions.md](doc/technical-solutions.md) - 技术方案与工具链
- [doc/engine-mechanics.md](doc/engine-mechanics.md) - 引擎机制与逆向发现
- [doc/lessons-learned.md](doc/lessons-learned.md) - 问题记录与经验教训
- [doc/acceptance-criteria.md](doc/acceptance-criteria.md) - 验收标准、回归与实机测试清单
- [doc/wsc_to_ws2_conversion.md](doc/wsc_to_ws2_conversion.md) - WSC→WS2 指令集转换规则（含切片模式）

## 与 A Sky Full of Stars 项目的关系

本项目基于 A Sky Full of Stars 内容还原补丁项目的技术经验启动，但两个项目是独立的：

**共同点**：
- 同一引擎（AdvHD.exe）
- 同一资源格式（Arc/WS2/PNA）
- 同一核心目标（保留成就、还原 H 场景、零破坏性）
- 共享工具代码（arcbuild.py、ws2.py）

**差异点**：
- CROSS†CHANNEL 需要汉化（Steam 版只有英文）
- 槽位设计不同（单 ev 槽 vs ev01/ev02 双槽）
- 资源命名规则不同
- 字体处理需求（需支持汉字显示）
- 系统界面汉化（Lua 脚本 + 图形化界面）
- 分支结构更复杂

本项目文档独立编写，不依赖 A Sky Full of Stars 项目的上下文。
