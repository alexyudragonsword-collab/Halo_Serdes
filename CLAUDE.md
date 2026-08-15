# CLAUDE.md — 给在这个仓库里工作的 Claude Code 会话

行为级高速 SerDes 仿真框架。这份文件记录**在别处不明显、但踩了会返工**的约定与不变量。
能力全景见 [`README.md`](README.md),用法见 [`docs/USAGE.md`](docs/USAGE.md)。

---

## 常用命令

```bash
pip install -e ".[gui,fec,jit,test]"    # 完整开发环境

pytest -q                               # 全套(约 2 分钟)
HALO_NO_JIT=1 pytest -q                 # 纯 Python 内核路径(CI 两条都跑)
pytest tests/test_com.py -q             # 单文件
ruff check src/ tests/ examples/        # lint 门禁(CI 会跑)

bash rtl/run_lockstep.sh                # SV vs Python 黄金模型逐位比对(需 iverilog)
python -m halo_serdes_gui               # 启动 GUI
```

**基准测试务必先热身 JIT**,并且**不要在计时区间里开 `tracemalloc`** —— 它会让
测量值虚高好几倍(这个坑踩过一次,把 11.6 s 测成了 30.7 s)。

---

## 架构不变量(改动时不要破坏)

**1. 配置是唯一真相源。** 一切由 frozen dataclass `LinkConfig` 决定:引擎、GUI 表单、
COM、RTL 参数导出全部读它,**没有全局状态**。新增参数就加字段 + 在 `__post_init__` 里
校验 + 在 `config_bridge.SECTIONS` 里加一行,GUI 表单会自动生成。

校验的取舍:**只拦真正的非法值**。已知两处故意不拦 ——
`kind="touchstone"` 而 `file=None`(`LinkConfig()` 默认就是它,加载器自会精确报错)、
单抽头 FIR 的 `fir_n_pre`(引擎整个跳过它)。过严会误伤,这两处都是被测试打回来过的。

**2. 双 RX 架构必须公平可比。** mixed-signal 与 ADC-DSP **共享** Tx、信道、分析层、
统计引擎骨架;差异只允许存在于 `engine/timedomain.py` 的两个组装函数与各自专属模块内。
新增能力要想清楚放在共享层还是专属层。

**3. 双引擎交叉校验是核心纪律。** 纯 LTI + AWGN 下,统计引擎与时域 MC 必须在 **2× 内**
吻合。加了非 LTI 环节(TI 失配、MLSD、CDR 残余抖动)后偏差会变大 —— 这些在统计引擎里
是**近似**,`engine/statistical.py` 顶部逐条列了假设清单,新增近似要往那儿补。

**4. numba 是性能层,不是正确性层。** 热核写成纯 Python 函数,再用 `numba.njit` 包一层,
`ImportError` 或 `HALO_NO_JIT=1` 时退回纯 Python。两条路径结果必须一致(CI 双路径跑)。
定点路径禁 `fastmath`。

**5. 波形与符号是两个域,不能隐式互转。** 过采样连续域(`Waveform`)与波特率符号域
(`SymbolStream`)之间只能经显式采样器(Farrow 分数相位插值)。

---

## 容易踩的坑

**`SimResult.ber` 是 `BerResult` 不是 float。** 要数值用 `res.ber.ber`;`res.ser` 才是
float。统计引擎的 `stat.ber` 是 float。

**MLSD 只在有残余 ISI 时有用。** FFE/DFE 把眼睁开后(残余 ~1e-3)它无事可做甚至略添噪。
测 MLSD 增益要用**欠均衡**配置(如 mixed-signal + `dfe.n_taps=0`),否则测到的是噪声。

**MLSE 增益的基线要说清。** `mlse_gain_over_dfe_db` 给的是"相对**理想 DFE**"的渐近界;
而实测常常是"相对**无记忆判决器**"。两者数值可以差一个数量级,并列展示必须标注基线
(例 30 里就修过一次这个标注错误)。

**不要断言 MLSD 增益随 trellis memory 单调。** 残余光标是从脉冲响应估的、并非与实际
收到的信号匹配,深 memory 会引入模型误差;实测 m=1..4 的差异落在 Poisson 噪声内。
测试应钉住"slicer→MLSD"这个扛事的增益,而不是相邻档的顺序。

**错误数太少的结论不可信。** SER 5e-5 × 74000 符号 ≈ 4 个错误 —— 这种数字上的
"1.33× 增益"是噪声。断言增益前先确认错误数(几百以上)。

**mixed-signal 包络告警是有意的。** 超过标定边界会 warn(见 README「架构包络约定」),
探索性跑法允许,但别为了消除告警去改阈值。

**GUI 只做桥接和渲染。** 不允许在 `halo_serdes_gui/` 里新增任何仿真/信号处理逻辑 ——
需要新算法就加到核心库,GUI 调用它。图形只从库的数据产出函数取数。

**`has_getwave` 决定引擎走哪条流。** `True` → GetWave(时域块),`False` → Init
(LTI 冲激变换)。两者结果不同是正常的。`NativeFirAmi` 默认 `True`、`AmiCModel` 默认
`False` —— 对比两个 AMI 模型时必须让这个标志一致,否则会把流的差异误当成 bug。

---

## 代码与文档约定

- **代码注释、图表文字、docstring 一律英文**;`docs/*.md`、`README.md`、`CHANGELOG.md`
  是中文(或中英双语),这是有意为之,不要"统一"。
- **注释解释「为什么」,不解释「是什么」。** 尤其是物理/数值上的取舍与已知近似。
- 新算法模块配闭式解单测(AWGN 解析 BER、退化情形、恒等式),不要只测 smoke。
- 新增示例脚本按编号排(`examples/NN_name.py`),它同时是文档;
  `tests/test_examples_api.py` 会检查它能编译且 import 的符号都存在。
- 文档里引用的数字(测试数、示例数、标签页数)由 `tests/test_docs_fresh.py` 守着,
  改了代码规模记得同步 README。

**提交尾注**(全部 62 个提交都遵循):

```
Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_...
```

模型标识符不要写进提交信息、PR、代码注释或任何入库产物。

---

## 已知限制(不是 bug,别去"修")

- **内存**:波形全量驻留,10⁶ 符号 @OSR32 峰值约 0.9 GB。`fft_filter` 内部已分块
  (overlap-save),但波形数组本身没做窗口化。要跑更长就降 OSR 或分批。
- **定点只覆盖数据通路**:时域引擎不读 `numeric.mode`,定点走 `fixed_datapath` 独立重放;
  CDR 与 MLSD 没有定点路径,RTL lockstep 因此只覆盖 FFE+DFE+slicer。
- **片上校准未实现**:只建模 ADC 失配,`calibrated=True` 是行为级归零而非真实算法。
- **Duobinary / PR 整形未建模**:1+D 预编码 ≠ PR 整形(前者是映射,后者要在发端有意
  引入受控 ISI)。
- 真实 RTL 三视图、FPGA AMS、物理实现流 —— 超出行为级框架的设计边界,见
  `docs/COMPARISON.md`。
