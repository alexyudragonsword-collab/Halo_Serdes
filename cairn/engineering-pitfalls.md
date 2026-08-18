---
type: project_topic
status: active
summary: "开发中真实踩过、并付出返工代价的坑 —— 每条都带触发条件与判别方法"
tags: [pitfalls, measurement, mlsd, benchmarking, serdes]
contains: [pitfall, measurement-trap, api-trap, statistical-significance]
created: "2026-08-18"
updated: "2026-08-18"
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
