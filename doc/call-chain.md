# 调用链组织

## 脚本命名规范

### CROSS†CHANNEL 脚本命名

**格式**：`{PREFIX}{编号}{分支}_{语言}.ws2`

**示例**：
- `CC0TOU_en.ws2` - CC0 章节，TOU（透）线，英文版
- `CCA0001_en.ws2` - CCA 章节，第 0001 场景，英文版
- `CCB1014D_en.ws2` - CCB 章节，第 1014 场景，D 分支，英文版

### 章节前缀

| 前缀 | 说明 |
|------|------|
| `CC0-CC6` | 主要角色线（TOU/MIS/KIR/MIK/YOU/TOM/SAK）|
| `CCA` | A 章节场景 |
| `CCB` | B 章节场景 |
| `AN` | 特殊场景（动画、过场） |
| `BG*_ANIME` | 背景动画 |

### 后缀含义

| 后缀 | 说明 | 数量 |
|------|------|------|
| `_en` | 英文版（Steam 原版） | 364 个 |
| 无后缀 | 日文版原版脚本（待还原） | 待确定 |
| `_cn` | 中文版（可能的命名） | 可选 |

### 分支标识

场景编号后可能带分支标识符：
- `CCA0005A_en.ws2` / `CCA0005B_en.ws2` - 同一场景的不同分支
- `CCB1014A_en.ws2` / `CCB1014B_en.ws2` / `CCB1014C_en.ws2` / `CCB1014D_en.ws2` - 四分支场景

## 脚本类型分层

### 当前 Steam 版（364 个脚本）

**全部脚本统一命名**：
- 全部带 `_en` 后缀
- 无 H 场景标识（已删除）
- 无资源版本区分

### 补丁后的分层策略

借鉴 A Sky Full of Stars 的命名空间隔离方案：

| 脚本类型 | 后缀 | 配对资源 | 用途 |
|---------|------|----------|------|
| `*_en.ws2` | Steam 英文版 | Steam 裸名资源 | Steam 版剧情（保留） |
| `*_h.ws2` | 原版 H 场景 | `ORG_*` 前缀资源 | 原版 H 场景（新增） |
| `*_he.ws2` | Steam 过审 H | Steam 裸名资源 | Steam 改写版 H（如存在） |

**注意**：后缀命名规范需根据实际情况调整，以上仅为参考方案。

## 穿插式调用链

### 基本原理

CROSS†CHANNEL 的 H 场景被 Steam 删除，但前后文保留。需要在 Steam 段落间插入原版 H 段落，形成完整剧情。

### 穿插模式

```
Steam段落A → 原版H段落 → Steam段落B
   ↓              ↓              ↓
 *_en.ws2     *_h.ws2       *_en.ws2
   ↓              ↓              ↓
Steam资源    ORG_*资源    Steam资源
```

### 跳转改写

**入口改写**（Steam → 原版）：
```python
# 原始：Steam段落A → Steam段落B（跳过H）
\x07CCA0010_EN\x00

# 修改：Steam段落A → 原版H段落
\x07CCA0010_H\x00
```

**出口改写**（原版 → Steam）：
```python
# 原始：原版H段落 → 下一个原版场景（已不存在）
\x07CCA0011\x00

# 修改：原版H段落 → Steam段落B（汇合）
\x07CCA0011_EN\x00
```

## 调用链完整性

### 检查策略

1. **待插入原版脚本必须可达**：插入位置的前一节点必须指向原版脚本
2. **插入部分结束后正常汇合**：原版脚本结尾必须指向汇合的 Steam 脚本
3. **无悬空引用**：所有跳转目标脚本必须存在

### 检查方法

```python
# 检查跳转目标存在性
for caller, opcode, target in call_chain:
    pattern = opcode.encode() + target.encode('ascii') + b'\x00'
    if pattern not in ws2.decode(caller_data):
        print(f'FAIL: {caller} 缺少到 {target} 的跳转')
    
    if target + '.WS2' not in rio_arc:
        print(f'FAIL: 目标脚本 {target}.ws2 不存在')
```

## 资源配对保证

### 配对规则

- `*_h.ws2` ↔ `ORG_*` 前缀资源（或其他命名空间隔离方案）
- `*_en.ws2` ↔ Steam 裸名资源
- `*_he.ws2` ↔ Steam 裸名资源

### 检查脚本是否错误引用资源

```python
if script.endswith('_h.ws2'):
    for pna_ref in extract_pna_refs(script):
        if not pna_ref.startswith('ORG_'):
            print(f'ERROR: {script} 引用非 ORG_ 资源 {pna_ref}')
```

## 分支处理

### 多分支场景

CROSS†CHANNEL 有复杂的分支结构，同一场景可能有多个分支：

```
CCA0005_EN
  ├─> CCA0005A_EN
  ├─> CCA0005B_EN
  └─> (其他分支)
```

### H 场景分支

需要分析每个分支是否包含 H 内容：
- 如果某分支包含 H 场景 → 插入原版 H 脚本
- 如果某分支无 H 场景 → 保持 Steam 版调用链

### 分支映射

建立 Steam 版分支 ↔ 原版分支的映射表：

| Steam 版 | 原版 H 场景 | 说明 |
|---------|------------|------|
| `CCA0005A_EN` | `CCA0005A_H` | A 分支包含 H |
| `CCA0005B_EN` | 无 | B 分支无 H |

## 调用链验证清单

### 基本检查

- [ ] 所有跳转目标脚本存在
- [ ] 调用链连续且可达
- [ ] 无悬空引用
- [ ] 无循环引用（除非剧情需要）

### 资源配对检查

- [ ] `*_h.ws2` ↔ `ORG_*` 资源
- [ ] `*_en.ws2` ↔ Steam 裸名资源
- [ ] 无冗余资源（所有 ORG_* 资源被至少 1 个脚本引用）

### 后缀一致性检查

- [ ] Steam 脚本只跳转到 Steam 脚本或原版 H 脚本（入口）
- [ ] 原版 H 脚本只跳转到 Steam 脚本（出口）
- [ ] 后缀传递正确

## 新增脚本管理

### 脚本来源

1. **原版完整游戏**（日文版）
   - 需要获取原版游戏的 Rio.arc
   - 提取 H 场景脚本
   - 改名并修改跳转

2. **其他补丁**
   - 如果其他作者已完成 H 场景还原
   - 可以参考其调用链组织方式
   - 注意版权和授权问题

### 脚本清单

**待建立**：从原版或其他补丁中识别需要新增的脚本。

## 技术难点

### 分支爆炸

CROSS†CHANNEL 的分支数量可能很多，需要：
1. 建立完整的分支树
2. 标识每个分支的 H 场景位置
3. 逐一处理每个分支的调用链

### 跳转目标识别

从 Steam 版脚本中识别被删除的 H 场景位置：
1. 寻找场景编号的跳跃（如 CCA0005 → CCA0010，中间缺失）
2. 分析剧情逻辑的断点
3. 对比原版脚本列表

### 资源同名冲突

判断哪些资源需要命名空间隔离：
1. 提取 Steam 版和原版的资源列表
2. 找出同名但内容不同的资源（SHA256 比对）
3. 为冲突资源建立命名映射

## 工具需求

### 调用链分析工具

```python
def analyze_call_chain(rio_arc):
    """分析全部脚本的调用关系，生成调用链图"""
    pass

def find_missing_scenes(call_chain):
    """识别场景编号跳跃，标识可能的 H 场景位置"""
    pass

def verify_call_chain(call_chain, rio_arc):
    """验证调用链完整性"""
    pass
```

### 脚本改写工具

```python
def rewrite_jump(script_data, old_target, new_target):
    """改写脚本中的跳转目标"""
    pass

def add_suffix(script_data, suffix):
    """为脚本中所有跳转目标添加后缀"""
    pass
```

## 参考资料

- Res 303 补丁的调用链组织方式
- A Sky Full of Stars 项目的穿插式调用链经验
- 原版游戏的脚本列表（如可获取）
