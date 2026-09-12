# CROSS†CHANNEL 内容还原与汉化补丁

为 Steam 版[《CROSS†CHANNEL: Steam Edition》](https://store.steampowered.com/app/812560/)还原被移除的剧情内容，并将英文文本汉化为简体中文，同时完整保留 Steam 成就系统。

本补丁仅供已购买正版游戏的用户使用，不包含任何游戏本体文件。

> **当前版本：v0.3.0** — 内容还原与汉化已落地，等待全量实机验证。

![Banner](https://shared.steamstatic.com/store_item_assets/steam/apps/812560/library_hero.jpg?t=1573643366)

---

## 安装方式

提供两种方式，任选其一即可，**不要重复安装**。

### 方式一：安装器（推荐）

从 [Releases](../../releases) 下载 `CROSS_CHANNEL_Content_Restoration_Patch_Installer_vX.X.X.exe`，双击运行。

安装器会依次执行以下步骤：

1. 定位游戏目录（Windows 下会自动检测游戏路径，未找到时可手动输入路径）
2. 列出将被修改的文件，等待确认
3. 就地合并资源并覆盖脚本与文本

安装完成后即可删除安装器。

非 Windows 环境请使用 `tool/install.py`。需要 `arcbuild.py` 与 `payload/` 目录与脚本位于同级。

### 方式二：完整文件覆盖

适合安装器无法运行、或希望自行核对文件的情况。

下载 [`asset/`](./asset) 目录下的全部文件，按原目录结构覆盖到游戏根目录：

```
<游戏目录>/
├── Rio.arc          ← asset/Rio.arc
├── Chip2.arc        ← asset/Chip2.arc
├── Voice.arc        ← asset/Voice.arc
├── Fonts.arc        ← asset/Fonts.arc
└── Script.arc       ← asset/Script.arc     （Steam 原版无此文件，为新增）
```

游戏目录一般位于 `<Steam 库>\steamapps\common\CROSS†CHANNEL Steam Edition`。

`Graphic.arc`、`Chip1.arc`、`SysGraphic.arc`、`SysVoice.arc` **无需下载** —— 本补丁不改动它们，安装器会保留玩家原有文件。

### 两种方式的区别

|  | 安装器 | 完整覆盖 |
|---|---|---|
| 下载体积 | 约 145 MB | 约 940 MB |
| 工作方式 | 读取游戏原文件，就地合并增量 | 直接替换为成品归档 |
| 路径处理 | 自动定位 | 手动放置 |
| 适用场景 | 一般情况 | 安装器不可用、或需自行核对 |

安装器体积更小是因为它只携带增量部分，合并时需要读取游戏现有文件；完整覆盖不依赖原文件，但要下载全部归档。

## 卸载与恢复

补丁**不创建备份**，恢复原版通过 Steam 完成：

1. Steam 库中右键游戏 → 属性
2. 已安装文件 → 验证游戏文件完整性

Steam 会检测出被修改的归档并重新下载，恢复到未安装状态。

## 更新补丁

更新补丁使用以下两种方法：

1. **简单**：先通过 Steam 验证游戏文件完整性恢复游戏目录原始状态（见上方「卸载与恢复」），然后按「安装方式」重新安装新版本
2. **进阶**：检查[资源文件](./asset)的变更情况（对比 commit 记录），按需下载、覆盖有变更的文件

## 会被修改的文件

| 文件 | 处理方式 | 内容 |
|---|---|---|
| `Rio.arc` | 覆盖 | 剧情脚本与文本。364 → 693 个成员：在 12 个脚本的对应位置插入原版剧情，并补入 328 个 `.lng` 中文文本与 `NameTable.txt`（说话人名表） |
| `Chip2.arc` | 合并 | 199 → 236 个成员：37 个还原 CG（`EVCC9000`–`EVCC9018`） |
| `Voice.arc` | 合并 | 30288 → 32416 个成员：2128 条还原语音 |
| `Fonts.arc` | 覆盖 | 2 个字体文件换为含汉字字形的版本（支持简体显示） |
| `Script.arc` | 新增 | 14 个 Lua 脚本，提供图形化系统界面（Steam 原版无此归档） |

以下归档**不做任何改动**，安装器保留玩家原文件：`Graphic.arc`、`Chip1.arc`、`SysGraphic.arc`、`SysVoice.arc`。其中 `Graphic.arc` 与 Steam 原档逐字节相同。

---

## 实现原理

Steam 版（App ID 812560，Build ID 812561）删减了部分剧情内容，且文本只有英文。补丁要同时解决
两件事 —— **还原被删减的剧情**、**把文本汉化**。

### 一、内容还原

还原素材来自原版游戏 —— 但**两版游戏所用引擎不同**：

|  | 原版 | Steam 版 |
|---|---|---|
| 引擎 | WillPlus（`CROSSCHANNEL.exe`） | AdvHD（`AdvHD.exe`） |
| 剧本格式 | `WSC`（每字节 `ror 2`） | `WS2`（每字节 `rol 6`） |
| 归档格式 | 固定 13 字节文件名 | UTF-16LE 变长文件名 |
| 图像 | `ANM` / `MSK` 等分类 | PNA 图层 / PNG |

两边的**指令集完全不同**，字节位置对齐不代表语义相同 —— 原版的剧本与资源**不能直接复用**。
还原的主要工作量在于：**解密原版 `WSC`、逐指令转换成 `WS2`**，再把转换结果接进 Steam
的剧情流。

在转换的基础上，采用**增量补充 + 就地插入**策略：

- **不替换任何 Steam 已有资源** —— Steam 的素材、成就调用、脚本出口原样保留
- **同名冲突资源做命名空间隔离** —— 少数资源在两版中同名但内容不同（引擎的图层索引是纯位置量，直接覆盖会让其他场景错乱），补丁为还原版本另起新名，两套素材并存互不干扰：
  - 事件 CG 一律落在 `EVCC9000`–`EVCC9018` 编号段（引擎对 `Chip2.arc` 实施前缀白名单，只认 `EVCC` 前缀）
  - 立绘按 `TC{角色}0{nnn}` → `TC{角色}1{nnn}` 规则改名
- **把原版剧情就地插回脚本里** —— Steam 删减场景时并没有把内容彻底删掉，而是把它压缩成几句**删节版**留在原来的脚本里。补丁就在这段删节内容的位置上换回原版的完整剧情，前后两端仍然接着 Steam 原有的剧情：

  ```
  脚本 = [Steam 原生前段] + [原版完整剧情] + [Steam 原档尾段（含原有出口）]
  ```

  12 个脚本如此处理，**脚本名不变、跳转关系不变、出口不变**，场景标题、CG 鉴赏 Hook 等开场内容也原样保留。

### 二、汉化：绕开脚本内的窄字节限制

引擎按 **CP932** 解码脚本内的文本，简体字（见、雾、贵、樱、游、纱、师、长、丰…）**无法写进脚本**。所以汉化必须走**宽字符通路**：

| 通路 | 承载 | 机制 |
|---|---|---|
| `.lng` 文件 | 正文与选项 | 没有 id 字段，纯按位置对应 —— 引擎把播放序第 N 条文本替换为 lng 第 N 条（每个选项也各占一条） |
| `NameTable.txt` | 说话人名 | UTF-16LE 的替换表。脚本里写 `%LC<英文名>`，引擎查表换成中文 |
| `Fonts.arc` | 字体 | 原版字体不含汉字字形，换为含汉字字形的字体（17.6 MB × 2） |

## 已知情况

### 还原文本来源

- **主线剧情**：复用民间汉化组的官方译文（[cross-channel_chinese-localization_project](https://github.com/MewX/cross-channel_chinese-localization_project)）
- **Steam 新增内容**（7 条角色线与后日谈）：这部分译文由 GPT-5.6-sol 翻译，**未经人工校对**，风格与用词可能与主线译文存在差异

### Steam 版删减的剧情

Steam 删减场景时会把该场景压成几句**删节版**留在原来的脚本里。补丁在该位置用原版的完整剧情替换掉这段删节内容，同时保留 Steam 原档的结尾部分。原版没有、Steam 后加的零碎台词（如 `CCC0000` 的 `Ideal.` / `理想。`）随删节段一并丢弃。

还原区间引用到的背景、蒙版、立绘取自 Steam 原档（不新增）；事件 CG 与语音来自原版，按上述命名规则补入。

### 文本校验状态

- **池位对齐**：已全量通过 —— 328 个 `.lng` 的条目数均满足 `lng 条数 == 对话数 + 选项条目数`
- **语义校验**：现有 lng 的对齐是在两侧条数相等时按序直接对应、**未做语义检查**，因此条数相等并不能保证内容对应。本项目已逐条复核并修复 6 处语义无关的条目，另按可证的模型重对齐 101 个脚本；**全量语义复审尚未重开**，个别位置仍可能存在错配

### 成就系统

补丁不修改 Steam 的成就调用，也不改动各脚本的出口集合：12 个被插入原版剧情的脚本都保留了 Steam 原档的结尾部分，其中的成就 Hook（`0b` 指令）与场景开场内容（`SCENETITLE*`、鉴赏 Hook）原样保留。

---

## 目录结构

```
├── asset/              重新封装好的游戏文件（Git LFS）
├── backup/             Steam 原版基线（用于计算增量）
├── doc/                技术文档
├── tool/               通用库和工具
│   ├── install.py      安装器逻辑
│   ├── arcbuild.py     Arc 格式读写库
│   ├── ws2.py          WS2 脚本解码
│   ├── ws2disasm.py    WS2 反汇编
│   ├── wsc.py          原版 WSC 反汇编
│   ├── wsc2ws2.py      WSC→WS2 逐指令转换
│   ├── lng.py          本地化文本 (.lng) 编解码
│   └── luac53.py       Lua 5.3 字节码解析
├── script/             构建和开发脚本
│   ├── pack.sh         exe 打包脚本
│   ├── generate_payload.py  生成增量补丁
│   ├── final_verification.py 最终验证工具
│   └── icon.ico        打包用图标
├── payload/            增量补丁产物（构建后生成）
├── releases/           exe 最终产物（构建后生成）
└── VERSION             版本号（唯一来源）
```

## 测试进度

- [x] `CCC0000` 段（内容还原试点）
- [ ] 其余 11 个还原场景
- [ ] 说话人名字框显示
- [ ] 全 CG 解锁
- [ ] 全成就解锁

## 反馈

遇到问题请提交 [Issue](../../issues)，附上：

- 补丁版本与安装方式
- 出现问题的具体场景（角色线 + 大致进度；最好附上存档文件，位置在 `C:\Users\你的用户名\Saved Games\MoeNovel\CROSS†CHANNEL Steam Edition`）
- 游戏弹出的错误日志（如有）
- 问题的具体表现

## 致谢

- 主线中文译文来自 [cross-channel_chinese-localization_project](https://github.com/MewX/cross-channel_chinese-localization_project) 汉化组
- 还原用的原版素材（CG、语音）取自原版游戏，经逐项内容比对后补入
- 工具链与工程经验来自 [A Sky Full of Stars 内容还原补丁](https://github.com/Madao-1639/A_Sky_Full_of_Stars_Content_Restoration_Patch)

## To-Do
- [ ] 全量测试
- [ ] UI 汉化
