# Android — M0 可行性验证工程

这不是 app,是一次**去风险实验**。它只回答一个问题:

> Chaquopy 能不能在这个 Python 版本上装出可用的 numpy/scipy,
> 并让 `halo_serdes` 的计算核在 Android 上**算出与桌面一致的数字**?

答案由 CI 给出 —— 见 [`.github/workflows/android.yml`](../.github/workflows/android.yml)。
完整方案见仓库根的 `ROADMAP.md` 与规划文件。

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

## 有意保持简陋

没有 Compose、没有 Material 3、没有导航 —— 只有一个 Activity、一个按钮、一个 TextView。

M0 的价值在于**快速给出明确的是/否**,任何与被测风险无关的东西(尤其是会引入版本
匹配风险的 UI 框架)都会稀释它。真正的 Compose 界面属于后续里程碑。

---

## 结构

```
android/
├── gradle.properties                     版本旋钮(实验对象)
├── settings.gradle.kts                   插件版本在此解析(plugins 块只接受常量)
├── app/build.gradle.kts                  Chaquopy 的 pip 清单 + ABI 选择
├── app/src/main/python/halo_probe.py     探针:版本/scipy 入口/计算核/golden 比对
├── app/src/main/java/.../HaloPython.kt   与 Python 的唯一接触面
├── app/src/main/java/.../MainActivity.kt 一个按钮
├── app/src/androidTest/.../PythonStackTest.kt   M0 的判定,写成自动化测试
└── tools/gen_probe_golden.py             在 CI 宿主上生成 golden
```

`app/build.gradle.kts` 的 `srcDirs` 直接指向仓库的 `../../src` —— **Python 源码不复制一份**。
桌面版与手机版必须共用同一个计算核,这是项目铁律 #1 的延伸。
