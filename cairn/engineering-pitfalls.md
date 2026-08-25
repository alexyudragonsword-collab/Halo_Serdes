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

**"编译后测试全绿"必须先证明跑的是编译版。** 把 wheel 装进 venv 再 `pytest`,如果编译版
没装上、或者被 editable 安装的 `.pth` 抢先解析,套件照样全绿 —— 它只是又跑了一遍源码树。
断言前先 `assert halo_serdes.__file__.endswith('.so')`。同理,`.so` 里 grep 不到 docstring
这件事,只有在**先于一个未编译模块的 `.pyc` 里 grep 到同类短语**之后才有意义。
两次都是控制组:少了它,得到的是一个没有信息量的绿。

**"一个发行版一个包"是打包工具的普遍假设,本仓库不满足。** `halo-serdes` 这一个发行版下
有三个可导入包。任何按包产出 wheel 的工具都会给出**同名同 dist-info** 的多个 wheel,
装第二个会卸掉第一个;合并时还必须**重算 `RECORD`** —— 后解开的那份覆盖了前一份、
只列一半文件,而 pip 装的时候会校验它。

**丢弃中间产物的规则要按来源判,不能按后缀判。** 打 wheel 时无条件跳过 `.c` 是为了丢掉
Cython 生成的中间 C,却连带丢掉了 `io/ami_c/halo_fir_ami.c` —— 本项目当 package data
发布的**手写** C。现象离原因很远:wheel 正常产出、脚本自检通过,只有 `test_ami_c.py`
报 `RuntimeError: reference ...`。判据应为「只丢**旁边有同名 `.so`** 的 `.c`」。

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

**"该项不适用就跳过"的测试会把整项漏掉。** 检查 `STUDY_PLOTS` 的规格与数据是否对得上时,
我对返回 `note`(该 study 不接受这个配置)的项写了 `continue` —— 而 `fixedpoint`
**对每一个不带 ADC run 的配置都返回 note**,于是它是唯一一个从没被检查过的,
规格里写的 `bits`/`ser` 与它实际返回的 `wl`/`mismatch` 完全对不上。
症状会是:在它真正能跑的那些配置上,面板画出来是空的。
**规则:跳过分支要么把被跳过的项报出来,要么单独给它配一个能跑的输入。**
现在两条都做了 —— 断言"至少有一项被真正检查到",外加一个专门用 ADC 预设跑
`fixedpoint` 的测试。

**`Color.value.toInt()` 对任何 sRGB 颜色都是同一个数。** Compose 的 `Color` 是
`ULong` 上的 inline class,ARGB 打包在**高 32 位**、色彩空间 id 在低位 ——
`value.toInt()` 取的正是恒为 0 的那一半。用它做"这张图画了几种颜色"的判据,
**任何图像都报 1 种颜色**。我加这条断言正是为了抓"图表画白了",而它自己是瞎的:
浴盆图画得好好的,断言照样红。**用 `toArgb()`。**
(这次红得对 —— 它没有假装通过。)

**`adb exec-out` 的退出码是本地 adb 的,不是远端命令的。** 远端命令失败它照样返回 0,
所以 `adb exec-out ... > file || rm file` 这个守卫**永远不会触发**。
实际后果:`run-as` 打了一句 `run-as: unknown package: com.halo.serdes.probe`,
我那个 `for f in $(... ls ...)` 循环把这四个词当成了四个文件名建出来,
其中 `package:` 带冒号被 upload-artifact 拒绝 —— **32 个测试全过的一次运行因此变红**。
规则两条:(1) 从命令输出取文件名时**按模式过滤**(只收 `*.png`),别让错误文本变成文件名;
(2) 取回的文件**验魔数**,空文件或错误文本比没有文件更糟 —— 它看起来像证据。

**证据类产物不该当闸门。** 那次红的是截图上传,不是测试。截图是给人看的旁证,
让它决定构建成败,等于把它的角色倒过来。已改为 `continue-on-error` + `if-no-files-found: ignore`。

**`connectedAndroidTest` 跑完会把两个 APK 都卸载掉。** 测试写出来的文件随之消失,
所以**测试结束后再去设备上取文件,取到的一定是空**。我为此换过两条路径
(内部存储 + `run-as`、外部 files 目录 + `adb pull`),两条都失败,而失败原因跟路径
毫无关系 —— 诊断打出来是 `run-as: unknown package` 和 `No such file or directory`,
两句话说的是同一件事:**包已经不在了**。
**规则:仪器化测试要留下文件,必须在运行期间就把它交出去 —— 用 `TestStorage`**
(`useTestStorageService=true` + `androidx.test.services`),AGP 会在 app 还装着的时候
把它们抽到 `build/outputs/connected_android_test_additional_output/`。

> 我在这上面换了两次路径都没先问"文件还在不在",这是**沿着现象找原因、没有先确认前提**。
> 两条独立路径给出同一类错误时,该怀疑的是它们的共同前提,而不是各自的实现。

**诊断信息放在日志里读不到的位置,等于没有。** 脚本原本在失败时 dump 整份结果 XML,
而那东西在日志里几千行深、尾部全是模拟器拆机输出 —— 我连取好几个窗口都落在它旁边,
白白花掉几轮。改成在这一步**最后**打印一段紧凑的 `class#method + 断言消息`,
一次就读到了。规则同上一条的精神:**产出证据的位置,要按"谁来读、怎么读"来选。**

> 这条后来还不够:**"这一步的最后"仍然不是"这个 job 的最后"**。测试步骤之后还有
> 约 120 行模拟器拆机、Gradle 缓存写入和 git 清理,而从外部读 job 日志只能读尾部 ——
> 我又有四次取窗口落在计数旁边。最终解法是脚本把这几行同时写进
> `ci-summary.txt`,workflow **最后一步**再 `cat` 一次。重复打印几行的代价是零,
> 换掉的是一整类问题。

**Compose 测试的自动同步等的是"时钟空闲",而不定进度条永远不空闲。**
`assertIsDisplayed` / `performClick` / `performScrollTo` 在动手之前都会先同步一次,
而 `CircularProgressIndicator`(不定态)走的是 `rememberInfiniteTransition` ——
只要它在屏幕上,时钟就不会空闲,那一行**会一直阻塞到整个 job 超时**,
而不是给你一条能读的失败信息。本项目每一次调 Python 都会先亮一下 spinner,
所以这不是边角情况,是主路径。
**规则:所有不定进度条统一打 `TestTags.BUSY`,交互前先 `waitUntil { 没有 BUSY 节点 }`。**
`waitUntil` 是轮询条件、不要求空闲的,这也是这道闸门能写出来的原因。
(同理:单线程 Python dispatcher 不是 IdlingResource,启动路径也必须显式等,
不能指望 `waitForIdle`。)

**`androidTestImplementation` 不继承 `implementation`,BOM 要单独写一遍。**
`implementation(platform(compose-bom))` 管不到测试配置,于是
`androidTestImplementation("androidx.compose.ui:ui-test-junit4")` 解析不到版本、构建直接失败。
`debugImplementation` 反而**是**继承的,不用重复。

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
