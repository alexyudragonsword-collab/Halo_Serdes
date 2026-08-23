---
type: project_topic
status: active
summary: "开发中真实踩过、并付出返工代价的坑 —— 每条都带触发条件与判别方法"
tags: [pitfalls, measurement, mlsd, benchmarking, packaging, android, serdes]
contains: [pitfall, measurement-trap, api-trap, packaging-trap, statistical-significance]
created: "2026-08-18"
updated: "2026-08-23"
related: [architecture-invariants.md]
authoring_mode: ai_generated
---
# 工程陷阱

> 这里每一条都是**已经踩过**的,不是预防性清单。已知限制(不是 bug、不要去"修")
> 在文末;它们的改进计划在根目录 `ROADMAP.md`。

## 当前结论

### 测量类

**基准测试不要在计时区间里开 `tracemalloc`。** 它会让测量值虚高好几倍 —— 这个坑把
10⁶ 符号 @OSR32 的 11.6 s 测成了 30.7 s,并且据此写进了对话与文档,后来才更正。
另外 numba **必须先热身** 再计时,否则首次调用把 JIT 编译时间算进去。

**错误数太少的结论不可信。** SER 5e-5 × 74000 符号 ≈ **4 个错误** —— 这种数字上算出的
"1.33× 增益"是噪声。断言任何增益前先确认错误数(几百以上),并用 Poisson σ ≈ √N
判断差异是否显著。实测例:MLSD 的 m=1 与 m=4 相差 65 个错误,合并 σ≈53,**不到 1.2σ**,
不构成"更深 memory 更好"的证据。

### API 类

**`SimResult.ber` 是 `BerResult` 不是 float。** 要数值用 `res.ber.ber`;`res.ser` 才是
float。统计引擎的 `stat.ber` 是 float。格式化时直接 `f"{res.ber:.2e}"` 会抛
`TypeError: unsupported format string passed to BerResult.__format__`。

**`has_getwave` 决定引擎走哪条流。** `True` → GetWave(时域块),`False` → Init
(LTI 冲激变换)。**两者结果不同是正常的**。`NativeFirAmi` 默认 `True`、`AmiCModel`
默认 `False` —— 对比两个 AMI 模型时必须让这个标志一致,否则会把"走了不同的流"
误当成模型差异。踩过一次:一度以为 2.62 dB 的差异是 bug,实际只是流不同;
对齐后两者逐位一致。

### 打包 / 跨平台类

**静默回退会把"资源没打包"伪装成"参数不对"。** `load_preset()` 曾经在预设文件找不到时
悄悄返回 `LinkConfig()`,而它的默认信道是 **touchstone 且无文件** —— 于是 Android 上
`configs/` 没进 APK 这件事,现形时的样子是引擎抛
`channel.kind is 'touchstone' but channel.file is unset`,离真正的原因隔了三层。
真机与模拟器都被这条误导过。**规则:找不到资源就在找不到的那一层抛,不要回退到默认值** ——
尤其当默认值本身是不完整的。已改为抛 `KeyError`/`FileNotFoundError` 并在消息里报出
搜索过的目录。

**Chaquopy 的 importer 不是文件系统。** 它从归档里按需取模块,所以
`Path(__file__).with_name("data.json")` 这类写法在设备上**不成立**;`__file__` 看着是
真路径(`/data/data/<pkg>/files/chaquopy/AssetFinder/app/...`),但相邻的非 Python 文件
不一定被解出来。非 Python 数据要么走 Android assets + 首启解压 + 环境变量交接,
要么用 `__loader__.get_data()`。项目里两条路都用了(`configs/` 走前者,
`probe_golden.json` 走后者的回退)。

**打包省掉的资源要在"用之前"报,不是"用的时候"报。** APK 有意不带
`data/channels/*.s4p`,于是 9 个预设里 touchstone 的两个在手机上跑不了 ——
第一版 M4 仪器化测试取"第一个预设"正好取中它,测试红了,而 **app 的行为是对的**。
教训有两条:(1) 写测试时"第一个/任意一个"这种取法,要先确认它在目标环境里成立;
(2) 产品侧该给出**运行前**的可用性信号 —— 现在 `derive` 返回 `channel: {ok, message}`,
`valid` 保持 `true`(配置没问题,是数据不在),界面据此禁用 Run 并说明原因。

**环境变量交接必须在 `Python.start()` 之前。** `config_bridge` 在 import 期就把
`CONFIGS_DIR` 定死,晚一步设 `HALO_SERDES_DATA_DIR` 完全没用。

**GitHub Action 的 `script` 输入不是 shell 脚本。** `reactivecircus/android-emulator-runner`
是用 `sh -c "<script>"` 执行它的,所以内嵌的双引号会闭合外层包裹,命令当场畸形。
把一条无引号命令改成多行带引号的块之后,连续两次在 ~90 秒挂掉且**没有任何测试报告**。
更值得记的是**误诊的方式**:我对那段文本单独跑 `sh -n`,通过了,于是排除了这个嫌疑 ——
语法检查验证的是"到达之后能不能解析",而故障发生在"到达"这一步。
**规则:凡是要传进第三方 action 的脚本,放进仓库里的 `.sh` 文件,输入端只留一条无引号命令。**

**`connectedDebugAndroidTest` 跑零个测试也算成功。** 一个意思是"套件根本没执行"的绿勾
比红叉更糟。`android/tools/run_instrumented.sh` 因此解析结果 XML,总数为零就退出非零。

**Kotlin 表达式体的 `@Test` 必须显式声明返回 `Unit`。** `fun t() = runBlocking { ... }`
的推断返回类型是**最后一个表达式的类型**;若它不是 `Unit`(例如以
`HaloApi.release(...)` 收尾),JUnit 会以 "Method t() should be void" **在校验阶段
拒绝整个测试类** —— 类里所有测试一个都不跑,只留一条 `initializationError`。
后果是**测试数悄悄变少而不是变红**:那次 `ChartDataTest` 报的是 "1 tests, 1 failed",
而它其实有 2 个测试。**统一写 `runBlocking<Unit> { ... }`** —— 但**只改 `@Test` 方法体**:
一次全文替换会连带打到需要返回值的局部/辅助函数,把它们也变成 `Unit`,
下一轮就是编译错误。(实际发生过:替换脚本报"6 处"而那个类只有 5 个 `@Test`,
证据就在输出里没被读。)
(能看见这件事,靠的是 `run_instrumented.sh` 会打印每个类的测试数;否则只会看到
一条含糊的失败。)

**`org.json` 的 `optString` 把显式 null 变成字符串 `"null"`。** 不是 `""`,
所以 `optString(k).ifBlank { null }` 这个看着很稳的写法在**键存在且值为 null** 时
返回四个字符的 `"null"`。Python 侧是**故意**发显式 null 的(非有限浮点、被取消的
run 没有 handle、错误没有对应字段),于是:取消后的 job 拿到一个叫 `"null"` 的
handle,而每一条 `field: None` 的错误都会去把一个名叫 `"null"` 的输入框标红。
**规则:凡是可空的字符串字段都走 `stringOrNull(key)`(先 `isNull` 再 `optString`)。**
这条是仪器化测试逼出来的 —— 我的断言 `optString("handle").isBlank()` 红了,
而**产品代码里是同一个 bug**,测试红得对。

**AGP 的仪器化结果 XML 每台设备只有一个,根 `<testsuite>` 聚合所有类。** 按根节点的
`name` 归类会把整轮测试算到某一个类头上(实测:9 个测试全被标成 `LinkFacadeTest`)。
要分类明细就遍历 `<testcase>` 的 `classname`。总数对但分类错,比不给分类更有害。

**scikit-rf 的 `Network.interpolate` 依赖别人先 import 过 `scipy.interpolate`。**
`skrf/network.py` 只做了 `import scipy`,却去用 `scipy.interpolate.interp1d`。
于是它**在进程里碰巧有人导过那个子模块时能跑**,否则抛
`AttributeError: module 'scipy' has no attribute 'interpolate'`。现代 SciPy 惰性
加载子包把这个 bug 完全盖住,**而 Chaquopy 给手机装的 SciPy 1.8.1 不会** ——
设备上 Touchstone 这条路能不能跑通取决于 import 顺序。`touchstone.py` 现在显式
`import scipy.interpolate`。**规则:第三方库靠"别人导过"才成立的引用,要在自己这一层
补上显式 import**;测试断言的是 `sys.modules` 里有它,这样与 SciPy 版本无关。
(是 `wheel-versions` job 抓到的 —— 它存在的理由就是这个。)

**Chaquopy 的 `pip` 块没有"只给某个包加 pip 选项"这回事。**
`install("--no-deps", "scikit-rf")` 直接被拒(`Invalid pip install format`,
只有 `install("-r", "file")` 是合法的双参形式),而 `options("--no-deps")` 是
**全局**的 —— 会连 numpy/scipy 赖以工作的 `chaquopy-openblas` 一起剥掉。
想为一个包省掉一条无用的硬依赖,代价是砸掉整个移植的地基;老实装上更便宜。

**Chaquopy 装的不是 `pyproject.toml` 要的版本。** 实际解析到 numpy 1.26.2 + **scipy 1.8.1**
(声明的是 `scipy>=1.11`),pip 自己都报这对不兼容。它能加载 ≠ 算得一样。
`android` workflow 的 `wheel-versions` job 就是为此存在:在宿主上降到手机的版本跑一遍。
做 golden 比对时记住它跨的是**版本 + 平台**两个变量,别一上来就归因给平台。

### MLSD 类

**MLSD 只在有残余 ISI 时有用。** FFE/DFE 把眼睁开后(残余 ~1e-3)它无事可做、
甚至因为在近零残余上跑网格而略微添噪。测 MLSD 增益要用**欠均衡**配置
(如 mixed-signal + `dfe.n_taps=0`),否则测到的是噪声。

**MLSE 增益的基线必须说清。** `mlse_gain_over_dfe_db` 给的是"相对**理想 DFE**"的
渐近界;而实测常常是"相对**无记忆判决器**"。两者可以差一个数量级 —— 同一组残余光标下
前者 0.12 dB、后者 1.29–2.17×。并列展示必须标注基线(示例 30 里修过一次这个标注错误)。

**不要断言 MLSD 增益随 trellis memory 单调。** 残余光标是从脉冲响应估的、并非与实际
收到的信号匹配,深 memory 会把 0.03 量级的微小光标(模型误差占比大)纳入网格。
测试应钉住"slicer→MLSD"这个扛事的增益,而不是相邻档的顺序。

### 其他

**mixed-signal 包络告警是有意的。** 超过标定边界会 warn(见 `README.md` 的
「架构包络约定」),探索性跑法允许,但**不要为了消除告警去改阈值**。

**GUI 只做桥接和渲染。** 不允许在 `halo_serdes_gui/` 里新增任何仿真/信号处理逻辑 ——
需要新算法就加到核心库,GUI 调用它。图形只从库的数据产出函数取数。

## 已知限制(不是 bug,别去"修")

> 改进计划见根目录 `ROADMAP.md`;这里只说清现状边界。

- **内存**:波形全量驻留,10⁶ 符号 @OSR32 峰值约 0.9 GB。`fft_filter` 内部已分块
  (overlap-save),但波形数组本身没做窗口化。要跑更长就降 OSR 或分批。
- **定点只覆盖数据通路**:时域引擎不读 `numeric.mode`,定点走 `fixed_datapath` 独立重放;
  CDR 与 MLSD 没有定点路径,RTL lockstep 因此只覆盖 FFE+DFE+slicer。
- **片上校准未实现**:只建模 ADC 失配,`calibrated=True` 是行为级归零而非真实算法。
- **Duobinary / PR 整形未建模**:1+D 预编码 ≠ PR 整形(前者是映射,后者要在发端有意
  引入受控 ISI)。
- 真实 RTL 三视图、FPGA AMS、物理实现流 —— 超出行为级框架的设计边界,见
  `docs/COMPARISON.md`。
