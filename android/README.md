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

**当前进度:M4。** 选预设 → 看派生量与包络告警 → 跑统计引擎 → 读 BER。
参数逐项可编辑的表单(由 `SECTIONS` 自动生成)是 M5;图表是 M6。

下面「M0」几节是**历史记录**,保留是因为那些结论(版本约束、打包陷阱)至今仍然管用。

---

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

### 哪些预设在手机上能跑

APK 带了 `configs/*.yaml`,但**没带** `data/channels/*.s4p`(4.4 MB,而且读它要
scikit-rf,M0 起就没装)。所以 9 个预设里用 touchstone 信道的两个
(`nrz_16g_ms` / `nrz_32g`)在手机上**跑不了**。

这本身是有意的取舍,但它必须**在按 Run 之前**就说清楚,否则用户看到的是"预设坏了"
而不是"文件不在"。因此 `derive` 现在多返回一个 `channel: {ok, message}`:

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
- **不装 scikit-rf** —— 它把 `pandas` 声明为硬依赖(尽管运行时从不 import)。
  M0 的问题是 **scipy**,Touchstone 导入是后续里程碑的事;现在加进来只会让构建
  可能挂在一个与本次提问无关的包上。探针已验证:skrf 缺失时它优雅报告 MISSING,
  计算与 golden 比对照常通过。

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

`data/channels/*.s4p`(4.4 MB)**故意不打包**:读 Touchstone 要 scikit-rf,而 M0 不装它。

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

## 结构

```
android/
├── gradle.properties                     版本旋钮(实验对象)
├── settings.gradle.kts                   插件版本在此解析(plugins 块只接受常量)
├── app/build.gradle.kts                  Chaquopy 的 pip 清单 + ABI 选择 + configs/ 资产暂存
├── app/src/main/python/halo_probe.py     探针:版本/scipy 入口/计算核/golden 比对
├── app/src/main/java/.../HaloPython.kt   与 Python 的唯一接触面 + 单线程 dispatcher
├── app/src/main/java/.../api/HaloApi.kt  信封 -> ApiResult,跨界契约都在这
├── app/src/main/java/.../ui/LinkViewModel.kt  状态与 handle 生命周期
├── app/src/main/java/.../ui/LinkScreen.kt     Compose 界面
├── app/src/main/java/.../MainActivity.kt setContent 一行
├── app/src/androidTest/.../PythonStackTest.kt  M0 的判定(解释器与数值)
├── app/src/androidTest/.../LinkFacadeTest.kt   M4 的判定(界面走的那条路)
└── tools/gen_probe_golden.py             在 CI 宿主上生成 golden
```

`app/build.gradle.kts` 的 `srcDirs` 直接指向仓库的 `../../src` —— **Python 源码不复制一份**。
桌面版与手机版必须共用同一个计算核,这是项目铁律 #1 的延伸。
