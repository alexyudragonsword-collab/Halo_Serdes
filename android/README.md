# Android — Chaquopy + Compose

Kotlin 原生界面 + 嵌入式 CPython 计算核。**手机与桌面共用同一份 `src/`**,
不存在第二套仿真逻辑(项目铁律 #1 的延伸)。

```
Compose UI  ──►  HaloApi (信封解析)  ──►  HaloPython (单线程 dispatcher)
                                              │
                                              ▼
                              halo_serdes_app.api.call(method, json) -> json
                                              │
                                              ▼
                                    halo_serdes(计算核)
```

**当前进度:M9。** 选预设 → 改任意参数 → 看派生量与包络告警 → 跑统计引擎读 BER、
浴盆曲线与统计眼 → **跑时域引擎(三档质量、前台服务、可取消)** →
**导入自己的 Touchstone 文件** → **在第二个标签页跑七种扫描**。

### 图表是画出来的,没有引入图表库

要画的形状就两种(对数十倍频程的浴盆、密度图),而一个通用库会带来它自己的版本匹配风险,
换来一堆用不到的功能。两个决定值得记:

- **纵轴按数据自适应,不写死 1e-30…1。** 舒适链路的浴盆可能只跨 5 个数量级,
  硬撑到 30 个会把曲线压成顶边一条线。
- **眼图用一张缩放的位图,不是逐格 `drawRect`。** 256×128 是 32768 个格子,
  每帧发这么多绘制调用正是图表卡顿的原因。位图按 run handle 缓存 ——
  用 `z` 做 remember key 意味着每次重组都要深比较 32768 个 double,比绘制还贵。

### 顺带修掉的一个 API 缺陷

`series(stat_eye)` 原本声明 `zmin=-12, zmax=0`,**两端都不是真的**:典型眼图最大值约 −2.7
(色带顶部三分之一永远用不到),而约三分之一的格子压在 −18 的钳位底(低于声称的下限)。
按声明的范围上色会画出一张发灰的图,而且暗示钳位格子里有测到的值 —— 恰恰相反,
它们的含义是**这个网格上没有解出概率**。

现在返回数据**实际**占据的范围,外加显式的 `floor`;渲染时 floor 格子画成背景色而不是
色带最暗端。宿主 `test_eye_heatmap_range_describes_the_data_it_ships` 钉住这条。

### 表单不是手写的

12 个分组、78 个字段全部由 `config_bridge.SECTIONS` 生成 —— 和桌面 Dash 版读的是同一份
规格。**给配置层加一个字段,手机上就会出现,Kotlin 一行都不用改。** 这是这一层存在的理由。

一个必须守住的不对称:**bool 要以 JSON 布尔过界,不能当文本发。** Python 侧每个数值
kind 都接受字符串(`float(value)`),这正是"表单一律按文本编辑"能成立的原因;但
`bool("false")` 是 `True` —— 开关若当文本发会**静默取反**,配出一个没人要的配置且毫无迹象。
`toFormValues` 因此保持 bool 的类型,设备侧 `boolFieldsStayBooleanThroughTheValueMap`
与宿主侧 `test_bool_fields_must_not_be_sent_as_text` 两头钉住。

校验是**去抖**的(300 ms):半个数字("1e"、"-")还不算用户犯的错,逐键闪红只会训练人无视
标记。配置非法时 `derive` 只回 `valid` 与 `field_errors`,没有 `derived`/`envelope`/`channel`,
所以界面保留上一次的派生量而不是在编辑中途清空面板。

下面「M0」几节是**历史记录**,保留是因为那些结论(版本约束、打包陷阱)至今仍然管用。

---

### 长跑不能靠"希望系统不杀它"

时域引擎是这个进程里几分钟的计算。切到后台而没有前台服务,进程随时可被回收,
**几分钟的结果无声消失,屏幕上不会留下任何痕迹** —— 这正是 M7 要防的那一件事,
通知栏那条通知是 Android 收的过路费,顺带成了熄屏后唯一能看见的进度。

- **`specialUse` 而不是 `dataSync`。** 没有任何东西在同步;`dataSync` 带着一份
  为网络传输准备的每日运行时预算,这份计算会以一个不实的名义把它花掉。
  Android 没有"长时间本地计算"这个类别,`specialUse` 就是为这种情况留的口子。
- **`START_NOT_STICKY`。** run 是本进程里的一个 Python 线程,进程没了活也没了;
  被系统重启的 service 只会去播报一个已经不存在的任务。
- **进度条是不确定态,卡片和通知都是。** 接收机内核是一次调用,它跑完整个符号
  循环 —— 要从里面报进度就得把那个循环切块、并把 CDR 与 DFE 状态跨接缝传递,
  而铁律 #3 / #4 正建立在那段代码上。任何百分比都是编的。
- **取消只在阶段边界生效,界面照实说。** `poll` 返回 `cancel_pending`,
  卡片显示"stopping…"并说明内核不能中途打断,而不是给一个看着能立刻停的按钮。

### 零错误不是 BER = 0

`result` 把 `n_errors` / `n_checked` 摆在 BER 旁边,并给一个 `ber_is_upper_bound`。
20 000 符号下一条舒适链路会跑出**零个错误**,引擎如实报 `ber == 0.0` —— 读起来是
"完美",意思却是"低于这次运行看得见的下限"。界面因此显示 `BER < 5e-5` 而不是
`0.000e+00`。**本项目已经在四个错误上算出过一个错误结论**(见
`cairn/engineering-pitfalls.md` 的测量类),这一栏就是为那件事留的。

同样地,`n_symbols` 与 `n_requested` 两个都报:引擎会丢掉热身与尾部不完整符号,
所以"快速档"实测的是两万里的约一万四,只显示档位就是夸大了这次运行。

### 导入 Touchstone 是"确认"而不是"直接采用"

决定一个文件能不能回答当前这个问题的,不是插损,是**文件自己的频率跨度**:
实测 `.s4p` 经常止步于远低于它被指向的那个 Nyquist(仓库里 peters 那组到 15 GHz
就没了),越过之后信道模型会保守外插 —— 然后照样给出曲线和 BER,屏幕上没有任何
东西说这个数来自外插而不是数据。`extrapolated` 就是那句话,卡片把它标红。

SAF 的 `Uri` 不是文件:`content://…` 由别的 app 的 provider 经框架解析,而 Python
手里只有 `open()`。所以字节必须先拷进本 app 的私有目录 —— 这不是绕路,是让那个
路径对解释器有意义的唯一办法。拷贝保留扩展名(skrf 从 `.sNp` 读端口数),
并有 64 MB 上限:仓库里最大的信道文件是 2.6 MB,超过这个数更可能是选错了文件。

### 扫描页里没有任何一个 study 的名字

列表、标题、说明、面板规格全部由 `schema` 送来 —— 也就是
`studies.STUDY_LABELS` 与 `studies.STUDY_PLOTS`,和数据的产地放在一起。
往 `studies.py` 加一个扫描,手机上就带着**正确的坐标轴**出现,Kotlin 零改动。
这跟表单与 `SECTIONS` 是同一笔交易,也是"一个计算核撑两个 UI"能成立的原因。

哪个 key 是 x 轴、哪些轴取对数,都不该由客户端猜 —— 两个客户端会猜出两种答案。
没有规格的 study 直接列数字,不去替它编一套坐标轴;规格点名了一个 study 没返回的
key,就把那个 key 报出来,而不是画一个空框。

## 跨界契约

界面永远不碰 `ok` / `error.message` / `PyException`,只见 `ApiResult`:

| 规则 | 为什么 |
|---|---|
| **入参出参都是 JSON 字符串** | Chaquopy 免费转 `str <-> String`,`dict <-> Map` 要手工遍历 `PyObject` |
| **numpy 不过界** | 结果留在 Python 侧 registry,Kotlin 只拿 `handle`;数组按需经 `series` 抽取后再取 |
| **错误是数据不是异常** | Python 异常穿过 JNI 会变成不可读的 `PyException`,界面既显示不了有用信息、还可能带走进程 |
| **一个解释器一个 GIL → 一条线程** | `HaloPython.dispatcher` 是单线程,顺带保证 registry 不会被两个调用交错 |

`LinkViewModel` 在存新 handle 前先 `release` 旧的,所以进程侧最多只留一份结果的
numpy 数组;`onCleared` 里用 `HaloPython.post` 而不是协程 —— 那时
`viewModelScope` 已取消,协程根本不会跑。

### 哪些预设在手机上能跑(M8 之后:全部)

M8 之前 APK 只带 `configs/*.yaml`,不带 `data/channels/*.s4p` —— 没有读它的东西,
那 4.4 MB 就是纯负担。M8 装了 scikit-rf,信道文件也就一起打包了,九个预设现在
**都能跑**。

但这条契约保留,而且是**写成蕴含式**保留的:只要有配置指向一个这台设备上没有的
文件(导入的文件被删、"Library defaults" 本身就无文件),就必须**在按 Run 之前**
说清楚,否则用户看到的是"预设坏了"而不是"文件不在"。`derive` 因此返回
`channel: {ok, message}`:

- `valid` 仍然是 `true` —— 配置本身没问题,只是数据不在这台设备上;
- 界面据此显示一张说明卡片并**禁用 Run**;
- `LinkViewModel` 启动时选的是**第一个能跑的**预设,而不是第一个预设 ——
  否则 app 一打开就是个按不动的按钮。

M4 第一版的仪器化测试就栽在这:它取"第一个非 Library defaults 的预设",正好是
touchstone 的那个,于是 `run_stat` 失败、测试红。**app 的行为是对的,是测试要求了
做不到的事。** 现在 `unreachableChannelsAreFlaggedBeforeRunning` 把这条契约钉住:
被标记为不可用的预设,`run_stat` 必须真的失败(写成蕴含式,所以将来若打包了
`.s4p`,这条依然成立)。

---

## 为什么值得先做这一步

Chaquopy 的 **SciPy wheel 长期只到 Python 3.10**
([chaquo/chaquopy#1237](https://github.com/chaquo/chaquopy/issues/1237) 至今开放)。
而 `src/halo_serdes` 用到 `scipy.special` / `scipy.optimize` / `scipy.stats`,
没有 scipy 就没有 `metrics` / `jitter` / `fec` —— 整条路线不成立。

**关键在于:这个问题由构建本身回答。** Chaquopy 在**构建期**解析并下载 wheel,
所以 `assemble` job 能不能变绿,就是答案 —— 不需要手上有手机。

---

## 三个层次的验证

| 层次 | 由谁验 | 能证明什么 | 证不了什么 |
|---|---|---|---|
| **构建** (`assemble`) | CI | wheel 存在、Python 源码能打包、APK 体积 | 能不能跑 |
| **模拟器** (`emulator`) | CI | 解释器启动、numpy/scipy 真的加载并计算、门面可从 Kotlin 调用、**与桌面数值一致** | 模拟器是 x86_64,证不了 ARM 浮点与 16 KB page |
| **真机** | 你 | 16 KB page 机型能否加载、真实性能、APK 安装体积 | — |

---

## golden 值的机制

`halo_probe.py` 在设备上重算一遍统计引擎 + COM + FEC,与 **同一个 commit 在 CI 宿主上
生成的** `probe_golden.json` 比对(`rtol=1e-9`)。

这样设计的原因:**写死的常量会腐化**。引擎正常演进时常量就过期了,于是要么误报、
要么被人调松直到失去意义。用同一次运行生成的值比对,差异就只可能来自**平台**
(libm、FMA 合并、OpenBLAS)—— 这正是要查的东西。

比对基准特意选在 **BER ≈ 1.3e-6** 而不是预设自带的 BER = 0:零在任何平台上都相等,
证明不了任何事。

`probe_golden.json` **不入库**(见 `.gitignore`),由 `tools/gen_probe_golden.py` 每次构建生成。
本地构建时若忘了生成,设备测试会明确报 "no golden bundled",而不是静默通过。

---

## 本地怎么跑

Python 部分不需要 Android 环境:

```bash
# 生成 golden 并跑一遍探针逻辑
python android/tools/gen_probe_golden.py
python android/app/src/main/python/halo_probe.py        # 完整 JSON 报告
```

构建 APK 需要 Android SDK:

```bash
cd android
./gradlew assembleDebug                 # 产物在 app/build/outputs/apk/
./gradlew connectedDebugAndroidTest     # 需要已连接的设备或模拟器
```

---

## M0 的 pip 清单为什么这么短

只装 **numpy + scipy + PyYAML**。

- **不装 matplotlib** —— 核心库从不 import 它(只有 `examples/` 用)。
- **不装 numba** —— Android 无 wheel,且铁律 #4 规定纯 Python 内核才是正确性基准。
- **不装 scikit-rf(M0–M7)** —— 它把 `pandas` 声明为硬依赖(尽管 Touchstone
  这条路上从不 import)。M0 的问题是 **scipy**,现在加进来只会让构建可能挂在一个
  与本次提问无关的包上。探针已验证:skrf 缺失时它优雅报告 MISSING,计算与 golden
  比对照常通过。
- **M8 起装 scikit-rf,连依赖一起装。** 它把 `pandas` 声明为硬依赖而
  Touchstone 这条路上从不 import —— 宿主测试
  `test_touchstone_path_works_without_pandas` 屏蔽 pandas 后跑完整条读取链路
  (解析 → 混合模式变换 → 建模 → 插损),这一点是钉住的。
  但**不用 `--no-deps` 去省掉它**:`install("--no-deps", "...")` 不是 Chaquopy
  接受的写法(`Invalid pip install format`),而它的 `options()` 是**全局**的 ——
  那会连 numpy/scipy 的 `chaquopy-openblas` 一起剥掉,为省一个包去砸这个移植
  赖以成立的东西。多背一个 pandas 是更便宜的那一边。

## 版本旋钮都在 `gradle.properties`

```properties
chaquopyVersion=16.1.0
pythonVersion=3.10      # ← 由 SciPy wheel 供给决定,不是偏好
agpVersion=8.7.3
kotlinVersion=2.0.21
```

**这些版本本身就是实验对象。** 若 Chaquopy 新版已支持更高的 Python,把 `pythonVersion`
调高让 CI 告诉你结果 —— wheel 缺失会以清晰的 pip 错误让构建失败,这正是想要的答案。

### 已经得到的答案

| 问题 | 答案 | 证据 |
|---|---|---|
| Py3.10 有 SciPy wheel 吗 | **有** | `assemble` 变绿 |
| APK 多大 | **74.4 MB**(2 个 ABI,已压缩) | CI 产物,远低于估的 ~160 MB |
| 解释器能在真机启动吗 | **能** | 真机跑出了 Python traceback |
| numpy/scipy 在真机加载并计算吗 | **能** | `erfc`/`erfcinv`/`binom`/`curve_fit` 全过 |
| 门面能从 Kotlin 调用吗 | **能** | `jsonFacadeIsReachableFromKotlin` 通过 |
| 计算核与桌面数值一致吗 | **x86_64 上一致** | 模拟器 5/5 通过,golden `rtol=1e-9` 无失配 |
| **ARM 上一致吗** | **一致** | 真机 `python 3.10.15 on aarch64` → `[golden MATCH]` |
| 真机性能如何 | **百毫秒量级** | 探针(统计引擎+COM+FEC)164 ms;下方有保留 |
| 16 KB page 机型能装吗 | **仍未知** | 见下 |

M0 的判定(2026-08-23):`assemble` / `wheel-versions` / `emulator` 三个 job 全绿,
仪器化 `Starting 5 tests` → `Finished 5 tests` 零失败;真机(aarch64)独立复现
`golden MATCH`。**Chaquopy 路线成立。**

`golden MATCH` 出现在 **aarch64** 上是这里最难由 CI 替代的一项 —— 不同 libm、不同 FMA
合并策略、OpenBLAS 的 ARM 内核都可能让 PDF 卷积那条路径在末几位漂开,而它没有。

**两点保留,不要写成已证明:**

- **164 ms 不能直接跟 CI 宿主的 79 ms 比。** 宿主那次是全新进程(确定冷),真机那次是不是
  首次按键无从判断;若是第二次以后,引擎模块与 numpy 已 import 完,164 ms 是热数据。
  要可比的数字得杀进程后**只按一次**。无论冷热,"百毫秒量级"这个量级结论成立,
  M4 的即时交互前提因此有效。
- **16 KB page 只能说"这台设备可以"**,不能说"16 KB page 机型可以" —— 从运行结果无法
  判断该机的页大小,而多数在用设备仍是 4 KB。要证明得找一台确知 16 KB page 的机器。

### 第二次运行的结果:presets 没进 APK(已修)

真机与模拟器给出同一个错误:

```
ValueError: channel.kind is 'touchstone' but channel.file is unset
```

**这不是配置错误,是打包错误。** `configs/*.yaml` 在仓库根,不在 Python 源码树里,
所以 Chaquopy 的 `srcDirs` 根本没带上它们;`config_bridge` 找不到 `configs/`,
`preset_names()` 退化成只剩合成的 "Library defaults",而它的默认信道正是
**touchstone 且无文件** —— 于是缺资源的问题伪装成了引擎的参数问题。

修法有三处,缺一不可:

1. **Gradle** `stageHaloAssets` 把 `configs/` 拷进 Android assets
   (**不能**走 Chaquopy 的 python `srcDirs`:那个 importer 从归档里取模块,
   普通 `Path()` 查一个非 Python 数据文件是查不到的)。
2. **Kotlin** `HaloPython.start()` 把 assets 解到私有目录,并在 `Python.start()`
   **之前**导出 `HALO_SERDES_DATA_DIR` —— `config_bridge` 在 import 期就把
   `CONFIGS_DIR` 定死了,晚一步就没用。
3. **Python** `load_preset()` 现在对找不到的预设**直接抛异常**。原来那个静默回退到
   `LinkConfig()` 才是让这个错误跑到三层之外才现形的元凶。

回归防线:`presetsSurvivedThePackaging`(仪器化,查 preset 数量并打印
`configs_dir`)+ `test_host_can_relocate_the_data_dir`(宿主侧,钉住环境变量契约)。

`data/channels/*.s4p`(4.4 MB)在 M0–M7 **故意不打包**:读 Touchstone 要 scikit-rf,
而那时不装它。M8 装了之后一并打包 —— 也因此解压改成按包的 `lastUpdateTime` 打戳,
每次启动重拷 4.4 MB 是白付的延迟(36 KB 的 YAML 时代无所谓)。

### 第二个发现:Chaquopy 给的版本比 pyproject 要求的老

构建日志里的实际解析结果:

```
numpy-1.26.2   scipy-1.8.1        ← pyproject 声明的是 scipy>=1.11
ERROR: scipy 1.8.1 has requirement numpy<1.25.0,>=1.17.3,
       but you'll have numpy 1.26.2 which is incompatible
```

**scipy 1.8.1 是 2022 年的**,而且 pip 自己就报了这对组合不兼容。真机上它确实**加载成功
并算出了 erfc**,但"能加载"不等于"算得一样"。因此新增 `wheel-versions` job:在宿主上把
numpy/scipy 强行降到手机拿到的这两个版本,跑一遍手机会跑的计算路径 —— 比模拟器便宜得多,
且失败时能指名原因,而不是等到 golden 比对时变成一个数字对不上。

**结果:版本这一维是干净的。** 该 job 全绿,只剩一条 numpy 版本区间的 `UserWarning`
(pip 报的不兼容只在元数据层面,ABI 实际是兼容的)。跨版本数值对比:

| | numpy 2.4.6 / scipy 1.17.1 | numpy 1.26.2 / scipy 1.8.1 |
|---|---|---|
| BER | 1.320132765231606e-06 | 1.3201327652316062e-06 |
| COM | 3.349834252410304 dB | 3.349834252410304 dB |
| post-FEC KP4 | 3.8061821319882705e-15 | 3.8061821319882705e-15 |

BER 差 **1 ULP**(相对 1.6e-16),COM 与 FEC 逐位相同。所以 golden 比对的 `rtol=1e-9`
虽然名义上跨"版本 + 平台"两个变量,**版本这一维实测只贡献 1 ULP**,留了约 7 个数量级余量 ——
之后若报不一致,可以干净地归因给平台(libm、FMA 合并、OpenBLAS)。

### 首次 CI 运行的结果(已修)

第一次跑就暴露了一个 Kotlin DSL 问题,也正是先做 M0 的价值:

```
e: app/build.gradle.kts:30: Unresolved reference: python
   sourceSets["main"].python { srcDirs(...) }
```

Chaquopy 的 `python` 源集是**动态注册的扩展**,Kotlin DSL 里 `android { sourceSets[...] }`
拿不到它。正确写法是放进 `chaquopy {}` 并用 `setSrcDirs(listOf(...))`(它**替换**默认值,
所以 `src/main/python` 要显式列上)。

**同时这次失败也带来了好消息**:错误发生在构建脚本编译阶段,说明
**AGP 8.7.3 / Kotlin 2.0.21 / Chaquopy 16.1.0 三个插件都已成功解析下载** ——
版本组合本身是通的。

---

## M0 期间有意保持简陋(已解除)

M0 那版没有 Compose、没有 Material 3,只有一个 Activity、一个按钮、一个 TextView ——
它的价值在于**快速给出明确的是/否**,任何与被测风险无关的东西(尤其是会引入版本匹配
风险的 UI 框架)都会稀释它。

M0 判定通过后 M4 才引入 Compose,这个顺序是刻意的:**先证明栈能跑,再堆界面**。
Kotlin 2.0 起 Compose 编译器随 Kotlin 本体发布,所以
`org.jetbrains.kotlin.plugin.compose` 的版本必须与 `kotlinVersion` 完全一致 ——
再没有单独的 composeCompiler 旋钮可以配错。

---

## 仪器化测试的日志约定

`emulator` job 的 `script:` **必须保持成一条无引号的命令**:
`reactivecircus/android-emulator-runner` 是用 `sh -c "<script>"` 执行它的,
内嵌的双引号会把外层包裹闭合,命令当场畸形。踩过一次 —— 改成多行带引号的块之后,
连续两次都在 ~90 秒挂掉且**没有任何测试报告**,而对那段文本单独跑 `sh -n` 是通过的
(它作为 shell 合法,只是没能完整送达)。逻辑因此放进 `tools/run_instrumented.sh`。

那个脚本额外做两件 Gradle 不给的事:

1. **失败时把每个测试的 XML 打进日志。** Gradle 只报 HTML 报告路径,报告在 artifact 里;
   artifact 打不开时,红色构建从日志无法定位。
2. **成功时报出到底跑了几个。** `connectedDebugAndroidTest` 在**一个测试都没跑**的情况下
   照样成功 —— 一个意思是"套件根本没执行"的绿勾比红叉更糟,所以零测试在这里按失败处理。

## 结构

```
android/
├── gradle.properties                     版本旋钮(实验对象)
├── settings.gradle.kts                   插件版本在此解析(plugins 块只接受常量)
├── app/build.gradle.kts                  Chaquopy 的 pip 清单 + ABI 选择 + configs/ 资产暂存
├── app/src/main/python/halo_probe.py     探针:版本/scipy 入口/计算核/golden 比对
├── app/src/main/java/.../HaloPython.kt   与 Python 的唯一接触面 + 单线程 dispatcher
├── app/src/main/java/.../api/HaloApi.kt  信封 -> ApiResult,跨界契约都在这
├── app/src/main/java/.../ui/FormModel.kt      SECTIONS -> 表单模型 + 值编解码
├── app/src/main/java/.../ui/FormFields.kt     按 kind 渲染的控件
├── app/src/main/java/.../ui/LinkViewModel.kt  状态、去抖校验、handle 生命周期
├── app/src/main/java/.../ui/LinkScreen.kt     Compose 界面
├── app/src/main/java/.../ui/charts/Charts.kt  Canvas 绘图(对数线图 + 密度图)
├── app/src/main/java/.../MainActivity.kt setContent 一行
├── app/src/androidTest/.../PythonStackTest.kt  M0 的判定(解释器与数值)
├── app/src/androidTest/.../LinkFacadeTest.kt   M4 的判定(界面走的那条路)
├── app/src/androidTest/.../FormContractTest.kt M5 的判定(表单与 schema 不许漂移)
├── app/src/androidTest/.../ChartDataTest.kt    M6 的判定(图表数据有限且范围自洽)
├── tools/run_instrumented.sh             跑仪器化测试 + 在日志里报出到底跑了几个
└── tools/gen_probe_golden.py             在 CI 宿主上生成 golden
```

`app/build.gradle.kts` 的 `srcDirs` 直接指向仓库的 `../../src` —— **Python 源码不复制一份**。
桌面版与手机版必须共用同一个计算核,这是项目铁律 #1 的延伸。
