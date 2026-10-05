# 待办与路线图 / Roadmap

已完成的演进见 [`CHANGELOG.md`](CHANGELOG.md);README 里的「路线图」表是**回顾性**的
(记录已交付的阶段)。这份文件相反,只记**尚未做的**。

每条都标注了**证据**(在哪测到的、哪个文件),便于接手的人直接开工而不必重新调查。
优先级按「是否影响结论的可信度」排序,不是按工作量。

> 约定:`[P1]` 会让某类结论不可信或明显误导;`[P2]` 影响能力上限或使用体验;
> `[P3]` 可选扩展;`[边界]` 有意不做,仅记录范围。

---

## P1 — 声称与实现的落差

### 1. 定点只覆盖数据通路,不覆盖引擎与时钟恢复

**现状**:`engine/timedomain.py` **完全不读 `numeric.mode`** —— 定点走
`dsp/fixed_datapath.py` 的独立重放路径,只实现 FFE + DFE + slicer
(`_ffe_dfe_fixed_py`)。`cdr/` 下没有任何 `QFormat` 引用,`dsp/mlsd.py` 也没有定点路径。
`rtl/ffe_dfe_datapath.sv` 因此只能覆盖同样三级。

**为什么要紧**:Phase 6 的目标是"作为将来 RTL 的黄金模型"。CDR 环路增益是移位量
(`kp_shift`/`ki_shift`),本就是按硬件语义设计的,却没有 bit-true 路径去验证;
MLSD 的定点化(度量位宽、路径度量归一化)是真实 RTL 里最容易出错的地方之一。
现在的 lockstep 给人"黄金模型→RTL 已闭环"的印象,实际只闭了三分之一。

**怎么做**:先给 `mm_cdr` 加 int64 路径(PD 输出、环路累加器、相位插值索引),
配 `dump_vectors` 出口与一个 SV 对照模块;MLSD 同理(优先 sliding-detector,
它的定点化比 Viterbi 简单得多)。每一步都按现有范式:纯 Python 核 + numba 包装 +
独立整数参考仲裁。

**验收**:`rtl/run_lockstep.sh` 覆盖 FFE+DFE+slicer+CDR;`HALO_NO_JIT=1` 与 JIT
两条路径一致;定点与浮点在字长 → ∞ 时收敛。

---

### 2. 波形全量驻留,长跑受内存限制

**现状**:实测 10⁶ 符号 @106.25 GBd/OSR32 峰值 RSS ≈ **0.9 GB**
(tracemalloc 追踪到的 Python 峰值 786 MB)。`engine/lti.py::fft_filter` 内部已做
overlap-save 分块,**但波形数组本身没有窗口化** —— `tx_wave`、`tx_y`、`rx_y`
(以及 `collect_jitter` 时的 `ch_only`)同时全量存活。

**为什么要紧**:原计划写的是"观测点按需保留窗口"。现在跑 2×10⁶ 符号或 OSR=64
会到多 GB,而**深 LR 的低 BER 结论恰恰需要更长的码流**(1e-6 量级要 ~10⁷ 符号)。

**已做的一半(2026-10-05,原第 3 条)**:两个接收机内核已可按 `sim.chunk_symbols` 分块续跑
(`MsRxRun` / `AdcRxRun`,循环状态全在参数里,分块与整段逐位一致,两条 JIT 路径与全部预设指纹都验过),
进度与取消因此在块间生效。**剩下的就是本条**:内核仍读整条 `rx_y`,波形本身没有窗口化。

**怎么做**:把时域引擎改成按 `chunk_symbols` 流式推进 —— 每块只保留
(前一块尾部 + 当前块)的波形,观测点(眼图、抖动、q 直方)按需累积统计量而非留全量。
难点在跨块的状态连续性:CDR 相位与 DFE/FFE 抽头已由内核接续,剩下 Farrow 插值与 FFE
回看所需的波形尾巴、overlap-save 的块边界、以及观测点的累积。

**验收**:同一配置下分块与全量结果**逐位一致**(这是硬要求,参照 `fft_filter`
的验证方式);10⁶ 符号峰值内存降到数百 MB;新增一个长跑(≥5×10⁶ 符号)冒烟测试。

---

## P2 — 一致性与交付

## P3 — 能力扩展

### 7. 片上校准回路
现在只**建模失配**:`AdcConfig.calibrated=True` 是行为级把 offset/gain 归零,
不是真实算法。DragonPHY2 的 ADC unfolding 是可移植的参考。做了之后才能回答
"校准残差 vs 性能"这类问题。

### 8. Duobinary / PR 整形
1+D 预编码已实现(`precode` 开关),但**预编码 ≠ PR 整形**:前者是符号映射,
后者要有意引入受控 ISI 并配匹配的检测器。

**阶段 2(收端)与阶段 3(发端)已合入**(2026-10-03,`PrConfig(at="rx"|"tx")`、`docs/USAGE.md` §18、`cairn/DSP发端与PR.md` §7–8)。
示例 18 的信道上:收端 1 + 0.75D 比 delta + Viterbi 多 4.7 dB reach(示例 36);发端 1 + aD 在峰值不变时 ≈ 无 PR − 20·log10(1 + a)
(示例 37),线性链路里发端整形不省收端的噪声放大。

尚未做:
- 更长的目标(四光标起网格 N⁴)。
  (2026-10-04/05 已做:a 自适应 `pr.adapt`、1 + aD + bD² 目标及其 (a, b) 的 LMS 跟踪、统计引擎对逐符号判决(误差传播)的建模。)
- 发端 PR 真正可能占优的场景本模型没有:发端带宽受限且噪声在发端之前 / 之内(如光调制器的 duobinary)、串扰源在发端。

### 9. 多 lane 数据通路
多 lane 目前只在**串扰侧**(`aggressor_bank`/`icn_rms`);链路本身仍是单 lane。
真正的多 lane 系统级(lane 间 skew、共享 CDR/校准、per-lane FEC 交织)是另一个量级
的工作,按需再评估。

### 11. 光互联的余项
阶段 1–3(`cairn/光互联建模.md`)有级联 H(f) + 电平相关噪声 + 重定时串联 + 大信号曲线与 TDECQ。**未做**:
- 802.3dj 200G/λ TDECQ 的 1 抽头 DFE(现在只有 15 抽头 FFE;DFE 版的上限 dB 不同);MMF PMD 的参考接收机带宽
  可能低于 0.5×baud(802.3cd SR 用过 11.2 GHz @ 26.5625 GBd),未核实前按 0.5×baud;
- 统计引擎对大信号曲线只用稳态电平,不建 ISI 被曲线弯折的部分(已发 warning);
- LPO 模块内的"线性"EQ:OIF CEI-112G-LINEAR 允许驱动器 / TIA 各带简单 CTLE,现在按零 EQ 做;
- 功耗。

### 10. 测试与文档的长尾
- GUI docstring 28%(核心库 66%);GUI 行覆盖 60%(核心库 89%)。
- 39 个示例只做 **import 守卫**(`tests/test_examples_api.py`),不执行。
  全量执行太慢(多个用 10⁶ 符号),可考虑加一个"小符号数档"的夜间 CI job。
- `LICENSE` 与 `CONTRIBUTING.md` 尚缺(许可证类型需要由项目所有者决定)。

---

## 边界 — 有意不做

这些超出**行为级框架**的设计范围,记录在此以免被反复提起(详见
[`docs/COMPARISON.md`](docs/COMPARISON.md)):

- 真实 RTL 的完整三视图方法学(model / rtl / fpga 一致性)—— 现有的是一个
  bit-exact 的 FFE+DFE lockstep,证明范式可行,不追求全芯片。
- FPGA AMS 混合仿真、物理实现流(综合 / PnR / DRC)、片上 BIST/DFT/JTAG。
- 晶体管级模拟前端 —— CTLE/VGA 是传函行为模型,不是电路。

---

## 维护约定

- 完成一项就从这里删掉,并在 [`CHANGELOG.md`](CHANGELOG.md) 里记一笔。
- 新发现的问题请连同**证据**(复现方式、测到的数字、涉及文件)一起写进来 ——
  没有证据的条目会在下一次审计里被当作猜测处理。
- 架构不变量与易踩的坑写在 [`CLAUDE.md`](CLAUDE.md),不要写进这里。
