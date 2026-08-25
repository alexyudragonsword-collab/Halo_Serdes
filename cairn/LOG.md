# Project Cairn 日志

本文件按倒序记录实质性进展 —— 最新条目在本行正下方。每条保持简短(摘要+指针),
结论沉淀进 `cairn/<topic>.md`。

## 2026-08-25 · Chaquopy 工程复审 + Cython 编译版宿主侧验证

- **复审(照 `python-android-apk` skill 的坑表逐条对)**:大部分没踩到 —— `--no-index`、
  多行 `run:` 折行、管道退出码、陈旧 sdist(本项目结构上免疫:走 `setSrcDirs` 而非
  sdist/pysrc)、`install("../..")`、缺 loading 状态、skip 冒充 pass,全部不适用或已避开。
  **两条成立**:(1) `targetSdk = 35` 却没有 `enableEdgeToEdge`/WindowInsets 处理,
  完全靠 Material3 `Scaffold`/`TopAppBar`/`NavigationBar` 的默认值 —— **未经验证**,
  因为模拟器是 API 34;(2) APK 里打进了 `halo_serdes_gui`(680 K 的 Dash 桌面 UI 源码),
  手机永远不会 import 它 —— `setSrcDirs(listOf("src/main/python", "../../src"))` 是整棵 `src/`。
- **编译版:宿主侧验收通过。** 53 个模块编 46 个,删掉 `.py` 后
  **356 passed, 1 skipped —— 与解释版基线逐项一致**;12 个 `.so` 里 grep docstring 0 命中。
  排除的 7 个各有具体原因(1 个 Cython 自身崩溃 + 6 个 numba 层),见
  `cairn/android-compiled-variant.md`。
- **两处结构性不匹配**:skill 脚本假设「一发行版一包」,本仓库一个发行版三个包 ——
  两次调用产出同名 wheel,得合并并**重算 `RECORD`**;组装时无条件丢 `.c`,
  连带丢掉当 package data 发布的手写 `io/ami_c/halo_fir_ami.c`。
- **两次控制组都是必需的,不是仪式**:不先断言 `__file__` 是 `.so`,"全绿"可能只证明
  编译版根本没装上;不先在未编译模块的 `.pyc` 里 grep 命中,`.so` 里的 0 命中什么也不说明。
- **未决:要让编译版进 CI 就得把 skill 的 414 行脚本 vendor 进这个公开仓库**
  (无 license 头),这个决定不由我做。另外**交叉编译从未跑过** —— 本机无 NDK,
  且本次验证在 3.11、Chaquopy 目标是 3.10。

## 2026-08-24 · 新增铁律 #6:桌面与 Android 必须同时验证

- 规则本身一句话:**动了共用层(`src/halo_serdes/`、`src/halo_serdes_app/`)就两端都要跑通,
  任一端未验证即未完成。** 写进 `AGENTS.md` 铁律清单(第 6 条,文件到 65 行预算上限)。
- **为什么值得成为铁律,而不是"记得多跑一次"**:两端跑的不是同一套东西。共用的只有
  Python 源码,运行它的环境有**五处系统性差异**,每一处都在本项目真实咬过人 ——
  依赖版本(Chaquopy 给 scipy 1.8.1)、子模块加载语义(`scipy.interpolate` 那个 bug)、
  依赖是否存在(手机上没有 matplotlib/numba/galois)、文件系统语义(Chaquopy 的
  importer 不是文件系统)、生命周期(`connectedAndroidTest` 跑完卸载 APK)。
  论证与"完整验证"的具体含义见 `cairn/architecture-invariants.md` 第 6 条。
- **两端谁也替代不了谁**:前三条差异意味着桌面 `pytest` 全绿不构成手机能跑的证据;
  后两条意味着手机跑通也不构成桌面打包正确的证据(桌面走 PyInstaller/Nuitka)。
- **`wheel-versions` job 就是这条铁律的执行者**,它已经兑现过一次 ——
  `scipy.interpolate` 那个 bug 是它抓到的,而当时桌面套件全绿。
- 判据写清了:不是"我跑过了",是日志最后一行的
  `instrumented totals: N tests, 0 failures` —— 绿勾本身不够。

## 2026-08-24 · 界面进 CI:32 项仪器化测试 + 6 张截图

- **`UiRenderTest` 用 `createAndroidComposeRule` 起真 Activity、跑真 Python**,
  五个测试断言:开屏落在**能跑的**预设、内置信道芯片与 facade 列表一致、时域三档带
  耗时估计、Run 后浴盆图与统计眼**真的画了东西**(像素级)、Sweeps 列出通告的每个 study。
  **结果:32 tests, 0 failures**(run #29),截图 6 张作为 artifact。
- **两类断言强度不同,分开写在类注释里**:结构用可见文本匹配(顺带把文案纳入测试);
  像素用 `assertDrew` 数颜色种类。**故意不做 golden image** —— 那种东西在模拟器字体
  和 GPU 一变就红,随之而来的"再基线一次"会让它彻底失去意义。
- **必须绕开的坑:不定进度条让 Compose 永不空闲。** 自动同步等的是时钟空闲,
  而 `CircularProgressIndicator` 是无限动画 —— 交互会**阻塞到 job 超时**而不是失败。
  所有不定进度条打 `TestTags.BUSY`,交互前 `waitUntil { 没有 BUSY }`。
- **这一轮红了四次,每一次的价值都不一样**:
  (1) `Color.value.toInt()` 对任何 sRGB 都是同一个数 —— 我加来抓"图表画白"的断言**自己是瞎的**;
  (2) `adb exec-out` 返回本地退出码,`run-as` 的错误文本被我的循环变成了四个文件名,
  其中带冒号的那个让**32 个测试全过的一次运行变红**;
  (3) 截图丢失的真因是 **`connectedAndroidTest` 跑完会卸载两个 APK** ——
  我为此换过两条取回路径,而**位置从来就不是问题**;改用 `TestStorage`。
- **花掉最多轮次的不是这些,是"读不到日志"**:诊断信息在日志几千行深、尾部全是拆机输出,
  我反复取窗口都落在旁边。两步才修对:先挪到**步骤最后**(不够,步骤末尾不是 job 末尾),
  再让脚本写 `ci-summary.txt`、workflow **最后一步** `cat` 一次。
- 五条坑均已沉淀进 `cairn/engineering-pitfalls.md`,含一条推理层面的:
  **两条独立路径给出同一类错误时,该怀疑的是共同前提,不是各自实现。**

## 2026-08-23 · M7–M9:时域长跑 / Touchstone 导入 / 通用扫描页

- **M7**:三档质量 + 前台服务 + 取消。服务类型选 `specialUse` 而非 `dataSync` ——
  没有东西在同步,而 `dataSync` 的每日预算是给网络传输的;`START_NOT_STICKY`,
  因为 run 是本进程的 Python 线程,重启的 service 只会播报一个不存在的任务。
- **新增门面方法 `result`**:`poll` 只给 handle,此前读不出跑完的结果。它把
  `n_errors`/`n_checked` 摆在 BER 旁边并给 `ber_is_upper_bound` —— 两万符号下舒适
  链路是**零错误**,`ber == 0.0` 读起来像"完美"、意思是"低于这次能看见的下限"。
  这一栏是为 pitfalls 里"四个错误上算增益"那条留的。
- **M8 的硬约束在开工前就问掉了**:先在宿主上屏蔽 pandas 跑完整条 Touchstone
  读取链路,证明 skrf 不需要它,再动 UI。装了 skrf,`.s4p` 也一并打包,九个预设
  现在都能跑;解压改成按 `lastUpdateTime` 打戳(4.4 MB 不能每次启动重拷)。
- **M9 的扫描页里没有任何一个 study 的名字**:列表/标题/说明/面板规格全部来自
  `schema`(`STUDY_LABELS` + `STUDY_PLOTS`)。与表单和 `SECTIONS` 是同一笔交易。
- **两次红都抓到了真问题,不是测试的毛病**:
  (1) `optString` 把显式 null 变成字符串 `"null"` —— 取消后的 job 拿到一个叫
  `"null"` 的 handle,`field: None` 的错误去标红一个叫 `"null"` 的输入框。
  **我最初的测试断言里是同一个 bug**,红得对。
  (2) `wheel-versions` 抓到 skrf 的 `Network.interpolate` 只 `import scipy` 就用
  `scipy.interpolate.interp1d` —— 现代 SciPy 惰性加载盖住了它,**手机上的 1.8.1
  不会**,设备上能不能跑通取决于 import 顺序。这正是那个 job 存在的理由。
- **`--no-deps` 这条路不通**:Chaquopy 不接受 `install("--no-deps", ...)`,
  而它的 `options()` 是全局的 —— 会连 numpy/scipy 的 openblas 一起剥掉。改为
  正常安装(多背一个 pandas),宿主那条"不需要 pandas"的测试保留,因为它说明
  这个依赖只是浪费、不是承重。
- 坑均已沉淀进 `cairn/engineering-pitfalls.md`;结构与理由见 `android/README.md`。

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
