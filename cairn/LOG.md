# Project Cairn 日志

本文件按倒序记录实质性进展 —— 最新条目在本行正下方。每条保持简短(摘要+指针),
结论沉淀进 `cairn/<topic>.md`。

## 2026-08-23 · M6:浴盆曲线与统计眼(Canvas 原生绘图)

- 不引入图表库:要画的形状只有两种,通用库会带来版本匹配风险换一堆用不到的功能。
- **顺带修掉一个 API 缺陷**:`series(stat_eye)` 声明的 `zmin=-12/zmax=0` 两端都不真 ——
  实测 max ≈ −2.7(色带顶部 1/3 用不到)、33% 的格子在 −18 钳位底(低于声称下限)。
  改为返回数据实际范围 + 显式 `floor`;floor 格子渲染成背景,因为它们的含义是
  "没解出概率",不是"概率很小"。宿主测试钉住。
- 两个渲染决定:纵轴按数据自适应(舒适链路只跨 5 个数量级);眼图走一张缩放位图而非
  32768 次 `drawRect`,位图按 run handle 缓存(用 `z` 做 key 的深比较比绘制还贵)。
- **第一次 CI 红的原因不是我猜的那个**:我先假设是"取了跑不了的 touchstone 预设",
  但 XML dump 显示 `InvalidTestClassError: Method ...() should be void` ——
  `= runBlocking { ... }` 以 `release(...)` 收尾,推断返回类型不是 `Unit`,
  **整个类在校验阶段被拒**,一个测试都没跑。计数脚本报 "1 tests, 1 failed"
  (实际有 2 个)才让这件事可见。全部改为 `runBlocking<Unit>`。
- 那个 touchstone 假设**是个潜伏问题**(类跑起来就会栽),一并修了:
  "挑第一个预设"这个错我写了两遍,抽成 `TestPresets.runnable()` 一处。
- 眼图**按需拉取**而非随每次 Run 一起返回:缩减后仍有约 4k 个数,而多数时候按 Run 只为看 BER。
- **结果(run #14 全绿)**:`ChartDataTest 2 / FormContractTest 5 / LinkFacadeTest 4 /
  PythonStackTest 5`,`instrumented totals: 16 tests, 0 failures`。
  ChartDataTest 从"1 tests, 1 failed"(幽灵)变成"2 tests, 0 failed"。
- 这一轮红了两次,**两次都是我引入的**,第二次还是修第一次时引入的。教训不在 Kotlin,
  在于**批量文本替换要核对影响面** —— 脚本已经把"6 处替换 vs 5 个 @Test"打出来了,我跳过了。

## 2026-08-23 · M5:参数表单由 SECTIONS 自动生成

- 12 个分组、78 个字段全部从 `config_bridge.SECTIONS` 渲染,与桌面 Dash 读同一份规格 ——
  **给配置层加字段,手机上自动出现,Kotlin 零改动**。
- **踩到一个真陷阱并钉住**:Python 侧数值 kind 都接受字符串(所以表单可以一律按文本编辑),
  但 `bool("false")` 是 `True` —— 开关当文本发会**静默取反**。`toFormValues` 保持 bool 类型;
  宿主 `test_bool_fields_must_not_be_sent_as_text` + 设备
  `boolFieldsStayBooleanThroughTheValueMap` 两头验证。
- 校验去抖 300 ms(半个数字不算错);配置非法时 `derive` 只回 `valid` + `field_errors`,
  界面保留上一次派生量而不是清空。
- 新增 `FormContractTest`:schema 声明的 kind 必须都有控件、预设经表单值映射往返仍合法、
  改 symbol_rate 派生量真的变、非法值按 path 报错。
- **结果(run #11 全绿)**:`FormContractTest 5 / LinkFacadeTest 4 / PythonStackTest 5`,
  `instrumented totals: 14 tests, 0 failures`。上一轮修的分类归属这次输出正确。

## 2026-08-23 · M4:Compose 界面接上共用计算核

- 分层:`Compose UI → HaloApi(信封解析)→ HaloPython(单线程 dispatcher)→ api.call`。
  信封只拆一次;`suspend` 强制切到解释器线程(一个解释器一个 GIL,顺带防 registry 交错)。
- CI 回答了唯一的未知项:**Kotlin 编译通过**(Compose 插件版本跟随 `kotlinVersion`,
  Kotlin 2.0 起没有独立的 composeCompiler 旋钮)。APK 产物 74.4 → 80.6 MB,Compose 约 +6.2 MB。
- **第一版仪器化测试红了,原因是测试自己的 bug**:它取"第一个非 Library defaults 预设",
  而那是 touchstone 的 `nrz_16g_ms`,`.s4p` 有意没打进 APK → `run_stat` 失败。app 行为正确。
- 由此补了一个**产品侧**缺陷:`derive` 新增 `channel: {ok, message}`(`valid` 仍为 `true`),
  界面显示说明卡片并禁用 Run,启动时选**第一个能跑的**预设。契约由
  `unreachableChannelsAreFlaggedBeforeRunning`(设备)与两个宿主测试钉住。
- **最终结果(run #9)**:三个 job 全绿,`instrumented totals: 9 tests, 0 failures,
  0 errors, 0 skipped`(5 个 PythonStackTest + 4 个 LinkFacadeTest)。
- 路上踩了两个 CI 坑,都已沉淀进 `engineering-pitfalls.md`:
  (1) emulator action 的 `script` 走 `sh -c "<script>"`,内嵌双引号会把它截断 ——
  而我用 `sh -n` 检查语法通过,因此误判成"不是我的改动";
  (2) `connectedDebugAndroidTest` 跑零个测试也算成功,现已在脚本里按失败处理。

## 2026-08-23 · M0 判定:通过(run #3 三个 job 全绿)

- `assemble` / `wheel-versions` / `emulator` 全绿;仪器化测试
  `Starting 5 tests` → `Finished 5 tests`,零失败,含 `rtol=1e-9` 的 golden 比对。
  **Chaquopy 路线成立。**
- **`wheel-versions` 消掉了版本这一维**:numpy 1.26.2 + scipy 1.8.1(手机拿到的版本)
  下,BER 与现代版本只差 **1 ULP**(相对 1.6e-16),COM 与 post-FEC 逐位相同。
  pip 报的不兼容只在元数据层面。今后 golden 若失配,可干净归因给平台。
- **真机(aarch64)随后独立复现 `golden MATCH`**:`python 3.10.15 on aarch64`、
  numpy 1.26.2 / scipy 1.8.1、BER 1.3201e-06、COM 3.35 dB、164 ms。
  ARM 的 libm / FMA 合并 / OpenBLAS ARM 内核都没让 PDF 卷积在 1e-9 内漂开 ——
  这是 CI 最替代不了的一项。`skrf MISSING` 与 `numba absent: True` 均为预期。
- **两点保留**:164 ms 不可直接对比 CI 宿主的 79 ms(宿主确定冷启,真机是否首次按键
  未知,可能是热数据);16 KB page 只证明了**这台设备**可以,该机页大小无从判断。
- 上一条(下方)记的打包 bug 至此确认修复。判定表见 `android/README.md`。

## 2026-08-23 · M0 在真机跑通到了"presets 没打包"这一层

- **好消息(M0 的主要风险已排除)**:`assemble` job 绿 → Chaquopy 的 Py3.10 **有 SciPy
  wheel**;APK 74.4 MB(2 ABI,压缩后),远低于估的 ~160 MB;真机与模拟器上解释器启动、
  numpy/scipy 加载并算出 `erfc`、Kotlin 侧门面可调用(`jsonFacadeIsReachableFromKotlin` 过)。
- **失败点是打包不是算法**:`configs/*.yaml` 在仓库根、不在 Python 源码树,
  Chaquopy `srcDirs` 没带上 → `preset_names()` 退化 → `load_preset` 静默回退到
  `LinkConfig()`(默认 touchstone 无文件)→ 引擎抛 `channel.file is unset`。
  真机与 CI 模拟器给出同一个错误。
- 三处修复:Gradle `stageHaloAssets` 把 `configs/` 拷进 assets;`HaloPython.start()`
  解压并在 `Python.start()` **之前**导出 `HALO_SERDES_DATA_DIR`;`load_preset()` 改为
  **抛异常而非回退**。新增 `presetsSurvivedThePackaging`(仪器化)与
  `test_host_can_relocate_the_data_dir`(宿主)两道防线。
- **新发现待验**:Chaquopy 实际给的是 numpy 1.26.2 + **scipy 1.8.1**(pyproject 声明
  `scipy>=1.11`),pip 自报这对不兼容。新增 `wheel-versions` job 在宿主上降到这两个版本
  跑手机的计算路径 —— golden 比对现在跨"版本 + 平台"两个变量。
- 坑已沉淀进 `cairn/engineering-pitfalls.md` 新增的「打包 / 跨平台类」;
  经过与结构见 `android/README.md`。

## 2026-08-18 · Project Cairn 初始化(retrofit 进成熟项目)

- 在已有 62 个提交、297 项测试的项目上追加初始化 Cairn。
- 历史迁移模式:`inventory_only` —— 未改写任何历史文档,盘点见
  `cairn/knowledge-inventory.md`。
- **旧 `CLAUDE.md`(119 行)拆分迁移,内容零丢失**:铁律清单 → `AGENTS.md`;
  架构不变量与代码/文档约定 → `cairn/architecture-invariants.md`;
  踩过的坑与已知限制 → `cairn/engineering-pitfalls.md`;
  `CLAUDE.md` 按规范改为单行 `@AGENTS.md`。
- 决策:**不新建 `cairn/ROADMAP.md`** —— 根目录已有权威的 `ROADMAP.md`,
  再建一份会造成两个真相源;`AGENTS.md` 阅读顺序直接指向它。理由见盘点文档。
- 与模板的两处**有意偏离**(均已记录理由):不新建 `cairn/ROADMAP.md`(见上);
  `AGENTS.md` 预算从 ≤60 调为 ≤65 行,为 5 条项目铁律在必读文件里留位置 ——
  留一条文件自身违反的规则比调整预算更糟。
- 配置:`git_policy: track`(内容是技术结论,无敏感信息)、
  provider `none`(暂缓对接)、`language: zh`。详见 `.cairn/config.yaml`。
