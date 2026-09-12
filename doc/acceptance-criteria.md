# 验收标准

## 功能验收

### 调用链

- [ ] 所有跳转目标脚本存在、无悬空引用
- [ ] 12 个宿主的出口集合 == Steam 原档（`script/final_verification.py` 的
      `check_call_chain()` / `check_inline_splice()`）
- [ ] **就地插入落位正确**：与源 CCS 对位后**恰好一次、无缺失、无重复、无倒序**
      （回归守卫 `script/audit_inline.py`）
- [ ] 条件双出口（`CCC0000_en` 的 `01 mode=0x85`）的 `b` 字段按新布局重算，
      且指向正确的 `07`
- [ ] 宿主「第一句对话之前」的场景开场件仍在（`SCENETITLE*`、`SGCC*`、鉴赏钩子）
- [ ] `asset/Rio.arc` 的脚本数与 Steam 原档一致（363 个 `*_en.ws2`，无遗留还原脚本）

### 资源

#### 事件 CG / 背景 / 立绘

- [x] 37 个还原 CG（19 个编号 `EVCC9000`–`EVCC9018`）齐备，差分共用编号
- [x] **任何归档都不含 `CN_` 前缀成员**（引擎只加载 `EVCC` 前缀的 CG）
- [x] 编号按脚本引用顺序分配（`script/renumber_evcc9xxx.py --check` 可复核）
- [x] `Chip2.arc` 每个成员都带 PNG 签名（曾混入 MOS 冒充 PNG 的无引用成员）
- [x] `Graphic.arc` 与 Steam 原档逐字节相同 ⇒ `asset/` 不生成它
- [ ] 还原区间引用的背景 / 蒙版在 `Chip1.arc` 中存在（缺失的 `0x33` 会卡死引擎）

#### 语音

- [ ] 还原区间引用的全部语音存在于 `Voice.arc`（含补入的 186 条原版语音）
- [ ] 格式为 OGG；`.soundlevel` 缺失可接受（引擎容忍无包络的 OGG）

### 汉化

- [ ] 每个含文本的 ws2 都有配套 lng；无 lng 的 ws2 全部是无文本的系统/动画脚本
      （`0x14` 计数为 0）
- [ ] `lng 条数 == 14 条数 + Σ(0f 的 count)`（`check_lng_pairing()`）
- [ ] lng 格式正确：UTF-16LE 后整体 XOR 0x2C（**不是** rot6）
- [ ] 说话人名字框显示简体中文（`NameTable.txt` 前缀与脚本的 `%LC` 一致）

### 字体与系统界面

- [ ] `Fonts.arc` 含支持汉字的字体（约 18.5 MB × 2）
- [ ] `Script.arc` 的 14 个成员齐备，菜单/设置/画廊功能正常

## 质量标准

### Arc 文件

- [ ] 所有 Arc 通过 `arcbuild.verify()`（无 null padding）
- [ ] 哈希稳定：重新读写一遍后字节相同（`check_idempotency()`）

### 编码

- [ ] 资源名使用 Shift-JIS；lng 文本使用 UTF-16LE XOR 0x2C
- [ ] 无乱码或编码错误

### 打包（安装器）

- [ ] `python script/generate_payload.py` 末尾的**回读校验全部 `[OK]`** ——
      用 `backup/` + `payload/` + `METADATA.json` 重放安装流程，结果须与 `asset/`
      **字节一致**。**只比成员集合不够**：成员内容全对但顺序错，安装器的 checksum
      照样失败（见 [lessons-learned.md](lessons-learned.md) §24）
- [ ] `asset/` 或 `backup/` 任一变动后**必须**重新生成 `payload/`
      （METADATA 的 checksum 与成员顺序都取自当时的 `asset/`）

## 回归测试清单

每次修改后必须重新执行：

1. **全量验收**
   - [ ] `python script/final_verification.py`
   - [ ] **流水线复跑 0 处改动** —— `build_patch.py` 及各步（含
         `realign_lng_to_ws2.py`）都是幂等的；复跑报出改动即说明某步破坏了自己的输入

2. **调用链**
   - [ ] `python script/audit_inline.py` —— 合并播放序与出口一致性
   - [ ] （按需，较慢）`python script/audit_missing_content.py` —— 确认原版场景的每一段
         内容都能归属到某个脚本，没有真正掉内容

3. **转换器约定**（改 `tool/ws2disasm.py` / `tool/wsc2ws2.py` 后必跑）
   - [ ] `python script/verify_ws2_conventions.py`（7 项：`28`/`1e` 尾段结构、发射模板、
         `0x34` 槽名、`65` 首操作数、`15` 连跑、转换后的对话文本/id、选项 strid）
   - [ ] `python script/convert_wsc.py --outdir tmp/converted_ws2_new`，确认
         `parse failures` / `count mismatch` / `roundtrip bad` 全为 0
   - [ ] 新增/改动字节模板时按**全语料逐字节模态**核对，不要只看覆盖率
         （见 [lessons-learned.md](lessons-learned.md) §2）

4. **lng**
   - [ ] `python script/audit_choices.py` —— 池位连续性、条数等式、选项槽位形态
   - [ ] `python script/audit_lng_semantics.py` —— 语义复审 triage

5. **打包**
   - [ ] `python script/generate_payload.py` —— 回读校验全 `[OK]`

## 实机测试清单

- [ ] 至少一位测试者完整通关一条角色线
- [ ] **12 个还原场景**全部触发、CG 与语音正常、结束后正常续接（`CCC0000` 已通过）
- [ ] **名字框显示简体中文**（`NameTable.txt` 前缀修正后）
- [ ] **lng 重排处的演出**（`CCA0002_en` 插入 `15`+`14` 的那一处）
- [ ] 接缝处无重复台词、无缺失、无倒序；立绘 / BGM / SE / 蒙版正常；场景标题卡仍在
- [ ] 剧情文本显示为简体中文，无乱码
- [ ] Steam 成就、保存/读取、菜单与系统功能不受影响

## 发布标准

### 必要文件

- [ ] `asset/` 下全部归档（`Rio.arc` / `Chip2.arc` / `Voice.arc` / `Fonts.arc` / `Script.arc`）
- [ ] `payload/` 增量 + `METADATA.json`（含回读校验通过）
- [ ] 安装器可执行文件（`releases/`）
- [ ] `SHA256SUMS` 校验文件
- [ ] 用户文档：安装 / 使用 / 卸载说明，常见问题，版权与授权说明
- [ ] 版本号与更新日志（`VERSION`）、已知问题列表、兼容性说明

### 发布前

- [ ] 全部验收标准检查通过
- [ ] 无已知的严重 bug
- [ ] 文档完整且准确
- [ ] 安装包在干净环境测试通过
