---
type: project_topic
status: active
summary: "Halo_Serdes 的五条架构不变量与代码/文档约定 —— 破坏其中任何一条都会让某类结论失去可信度"
tags: [architecture, invariants, conventions, serdes]
contains: [architecture-invariant, code-convention, doc-convention, validation-tradeoff]
created: "2026-08-18"
updated: "2026-08-18"
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

过采样连续域(`Waveform`)与波特率符号域(`SymbolStream`)之间只能经显式采样器
(Farrow 分数相位插值)。隐式互转会引入 0.03 UI 量级的伪抖动而不报错。

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
