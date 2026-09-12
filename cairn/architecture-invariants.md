---
type: project_topic
status: active
summary: "Halo_Serdes 的六条架构不变量与代码/文档约定 —— 破坏其中任何一条都会让某类结论失去可信度"
tags: [architecture, invariants, conventions, serdes]
contains: [architecture-invariant, code-convention, doc-convention, validation-tradeoff]
created: "2026-08-18"
updated: "2026-08-24"
related: [engineering-pitfalls.md, knowledge-inventory.md]
authoring_mode: ai_generated
---
# 架构不变量与约定

## 当前结论

### 1. 配置是唯一真相源

一切由 frozen dataclass `LinkConfig` 决定:引擎、GUI 表单、COM、RTL 参数导出全部读它,
**没有全局状态**。新增参数的完整动作是三步:加字段 + 在 `__post_init__` 里校验 +
在 `config_bridge.SECTIONS` 里加一行(GUI 表单会自动生成)。

**校验的取舍:只拦真正的非法值。** 已知两处**故意不拦**:

- `kind="touchstone"` 而 `file=None` —— `LinkConfig()` 默认就是它,若在此拦截会让
  默认构造函数失效(GUI 的 "Library defaults" 预设就是它);加载器 `ChannelModel.from_config`
  自会精确报错。
- 单抽头 FIR 的 `fir_n_pre` —— 引擎在 `len(fir_taps) > 1` 时才应用 FIR,单抽头整个跳过,
  此时 `n_pre` 无意义。

这两处都是**被测试打回来过的**:第一版校验规则比引擎本身更严格,误伤了 `LinkConfig()`
与 `test_ami.py`。规则应当匹配引擎的实际语义,而不是理想语义。

### 2. 双 RX 架构必须公平可比

mixed-signal 与 ADC-DSP **共享** Tx、信道、分析层、统计引擎骨架;差异只允许存在于
`engine/timedomain.py` 的两个组装函数与各自专属模块内。

这是整个框架能回答"两种架构谁更适合这条信道"的前提 —— 一旦某个改动只惠及一侧,
所有架构对比结论都失去意义。新增能力时要先想清楚它属于共享层还是专属层。

### 3. 双引擎交叉校验是核心纪律

纯 LTI + AWGN 下,统计引擎与时域 MC 必须在 **2× 内**吻合(实测基线 1.03×)。

加了非 LTI 环节(TI-ADC 失配、MLSD、CDR 残余抖动)后偏差会变大 —— 这些在统计引擎里
是**近似**,`engine/statistical.py` 顶部逐条列了假设清单。**新增近似必须往那儿补一条**,
否则下一个人无法判断偏差是 bug 还是已知近似。

### 4. numba 是性能层,不是正确性层

热核写成纯 Python 函数,再用 `numba.njit` 包一层;`ImportError` 或 `HALO_NO_JIT=1`
时退回纯 Python。两条路径结果必须一致(CI 双路径都跑)。定点路径禁 `fastmath`。

### 5. 波形与符号是两个域,不能隐式互转

过采样连续域(`Waveform`)与波特率符号域(`SymbolStream`)之间只能经显式采样器。
隐式互转会引入 0.03 UI 量级的伪抖动而不报错。

**这条曾经只是一句话。** 2026-09 审计发现:没有采样器模块;`SymbolStream` 定义了
但**全仓库零次构造**;11 个调用点各自就地用 `[::osr]` 切片或 `np.repeat` 跨域。
**没有任何东西执行的不变量就是一条注释。**

现在的执行方式:

| | |
|---|---|
| 唯一允许跨域的地方 | `src/halo_serdes/core/sampler.py` |
| 波形 → 符号 | `sample_baud(wave, osr, phase) -> SymbolStream`(带 UI 与相位的类型化边界);`baud_samples(y, osr, phase)`(结果不是符号序列时用,如脉冲响应的 baud 间隔 cursor) |
| 符号 → 波形(零阶保持) | `hold(values, osr)` |
| 波特率滤波器系数 → 采样网格 | `upsampled_taps(taps, osr, length=None)` |
| 执行者 | `tests/test_domain_boundary.py` 扫描 `src/halo_serdes`,断言 `[...::osr]` 与 `np.repeat(..., osr)` **只出现在 sampler.py 里** |

**判据诚实声明**:那条扫描匹配的是**惯用写法**,不是所有可能的隐式转换 ——
把 `osr` 别名成另一个名字仍能绕过。它买到的是「按最自然的方式写这个 bug 会让构建变红」,
绕过必须是刻意的。测试文件里自带一条"种一个违例、要求扫描能看见"的自检,
因为**匹配不到任何东西的正则能通过任何建立在它之上的断言** —— 本项目已经出过一次
瞎断言(`assertDrew` 走 `Color.value.toInt()`)。

**这次改造不允许改变任何数值**:9 个预设 × 4 条引擎路径共 469 个标量与数组哈希,
重构前后逐位一致。

### 6. 桌面与 Android 必须同时验证

**规则**:凡是改动了共用层(`src/halo_serdes/` 或 `src/halo_serdes_app/`),
桌面与 Android **两端都跑通才算完成**。只验一端就宣布完成,是把一半的交付物
建立在没有检查过的假设上。

**为什么这条要单独立成铁律,而不是"记得多跑一次测试"**:两端跑的不是同一套东西。
共用的只有 Python 源码,**运行它的环境有五处系统性差异**,每一处都已经在本项目
真实咬过人:

| 差异 | 咬过的具体形式 |
|---|---|
| **依赖版本不同** | Chaquopy 解析到 numpy 1.26.2 + **scipy 1.8.1**,而 `pyproject.toml` 要 `scipy>=1.11`;pip 自己都报这对不兼容 |
| **子模块加载语义不同** | skrf 只 `import scipy` 就用 `scipy.interpolate` —— 现代 SciPy 惰性加载把它盖住,**手机上的 1.8.1 不会**,Touchstone 这条路能不能跑取决于 import 顺序 |
| **依赖是否存在不同** | 手机上没有 matplotlib / numba / galois;M0–M7 连 scikit-rf 都没有 |
| **文件系统语义不同** | Chaquopy 的 importer 不是文件系统,`Path(__file__).with_name(...)` 在设备上不成立;`configs/` 与 `data/channels/` 要走 assets + 解压 |
| **生命周期不同** | `connectedAndroidTest` 跑完会**卸载两个 APK**,测试写出的文件随之消失 |

前三条意味着:**桌面 `pytest` 全绿,不构成手机能跑的证据**。第四、五条意味着:
**手机上跑通,也不构成桌面打包正确的证据**(桌面走 PyInstaller/Nuitka,另一套打包)。
两端谁也替代不了谁。

**"完整验证"的具体含义**:

- **桌面**:`pytest -q` 全绿 + `ruff check src tests` 干净。动了 `halo_serdes_gui/`
  或它 re-export 的东西,还要确认 GUI 能起来。
- **Android**:CI 三个 job 全绿 —— `assemble`(能不能打包)、`wheel-versions`
  (**在手机的 wheel 版本上跑一遍手机的计算路径**)、`emulator`(仪器化套件,
  脚本在零测试或任一失败时退出非零)。
- **判据不是"我跑过了",是"日志里那行数字"**:`instrumented totals: N tests, 0 failures`。
  绿勾本身不够 —— `connectedDebugAndroidTest` 跑零个测试也算成功。

**`wheel-versions` 这个 job 就是这条铁律的执行者。** 它在宿主上降到手机的
numpy/scipy 版本跑同一条计算路径,把"版本"这一维从"平台差异"里摘出来。
它已经兑现过一次:`scipy.interpolate` 那个 bug 是它抓到的,而桌面套件全绿。

## 实践指南

### 代码与文档约定

- **代码注释、图表文字、docstring 一律英文**;`docs/*.md`、`README.md`、`CHANGELOG.md`、
  `ROADMAP.md`、`cairn/` 是中文。**这是有意为之,不要"统一"。**
- **注释解释「为什么」,不解释「是什么」** —— 尤其是物理/数值上的取舍与已知近似。
- 新算法模块配闭式解单测(AWGN 解析 BER、退化情形、恒等式),不要只测 smoke。
- 新增示例脚本按编号排(`examples/NN_name.py`),它同时是文档;
  `tests/test_examples_api.py` 检查它能编译且 import 的符号都存在。
- 文档里引用的数字(测试数、示例数、标签页数)由 `tests/test_docs_fresh.py` 守着。

### 常用命令

```bash
pip install -e ".[gui,fec,jit,test]"    # 完整开发环境
pytest -q                               # 全套(约 2 分钟)
HALO_NO_JIT=1 pytest -q                 # 纯 Python 内核路径(CI 两条都跑)
ruff check src/ tests/ examples/        # lint 门禁(CI 会跑)
bash rtl/run_lockstep.sh                # SV vs Python 黄金模型逐位比对(需 iverilog)
python -m halo_serdes_gui               # 启动 GUI
```

Android 那一半(铁律 #6)**没有本地等价物** —— 这个沙箱没有 Android SDK,
`dl.google.com` 也不通,所以 Kotlin 能不能编译、仪器化测试过不过,**只有 CI 能回答**。
推送后去看 `android` workflow 的三个 job,判据是最后一步打印的那行:

```
instrumented totals: N tests, 0 failures, 0 errors, 0 skipped
```

绿勾本身不是判据:`connectedDebugAndroidTest` 跑零个测试也算成功,
所以 `android/tools/run_instrumented.sh` 会在总数为零时退出非零。

### 提交尾注

```
Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_...
```

模型标识符不要写进提交信息、PR、代码注释或任何入库产物。

## 教训

**校验规则不能比被校验的代码更理想化。** 第一版配置校验按"理论上正确"来写
(`fir_n_pre` 必须是合法索引),结果误伤了引擎明确跳过的退化情形。测试是唯一能
发现这类过度严格的手段 —— 加校验后必须跑全套,失败要先判断是"抓到真 bug"还是
"规则过严"。
