# 待办与路线图 / Roadmap

已完成的演进见 [`CHANGELOG.md`](CHANGELOG.md);README 里的「路线图」表是**回顾性**的
(记录已交付的阶段)。这份文件相反,只记**尚未做的**。

每条都标注了**证据**(在哪测到的、哪个文件),便于接手的人直接开工而不必重新调查。
优先级按「是否影响结论的可信度」排序,不是按工作量。

> 约定:`[P1]` 会让某类结论不可信或明显误导;`[P2]` 影响能力上限或使用体验;
> `[P3]` 可选扩展;`[边界]` 有意不做,仅记录范围。

---

## P1 — 声称与实现的落差

### 1. 定点 Viterbi 与定点 PR

**现状**(2026-10-06 起):`numeric.mode: fixed` 覆盖 ADC 接收机的 FFE + DFE + slicer + 训练 + 整数 LMS + MM CDR 闭环
(`dsp/fixed_loop.py`)与 sliding-detector MLSD(`dsp/fixed_mlsd.py`),三段 SV 逐位对照(`rtl/run_lockstep.sh`)。
**还没有**:`rx.mlsd.kind: viterbi` 在定点下仍是浮点;PR 目标(含 a 的 LMS)在定点下直接报错。

**为什么要紧**:Viterbi 的路径度量要归一化(或模运算比较),位宽不够时比较翻转 —— 典型 RTL 坑;
PR 是本项目 224G 结论的主力路径,没有 bit-true 版本,那部分结论就没有 RTL 黄金模型。

**怎么做**:Viterbi 先做 2 状态(NRZ / memory 1)的整数分支度量 + 减最小值归一化,再推广;
PR 在 `_digital_step` 里加受控光标减法(与浮点核 pr_mode 1 对齐),SV 同步。

**验收**:`HALO_NO_JIT=1` 与 JIT 一致;字长 → ∞ 时与浮点收敛;lockstep 覆盖。

---

## P2 — 一致性与交付

### 2. 流式模式(`sim.stream`)尚未覆盖的路径
**现状**:流式(2026-10-05,原 P1 #2)只接了电链路:Tx(含 DAC、驱动曲线、`tx.bw`)→ 信道 + CTLE + VGA → 噪声 → 两种接收机。
IBIS-AMI、光拓扑(两段/三段 + 电平相关噪声)、串扰注入、`collect_jitter` 在流式下直接报错(`engine/timedomain.py::_check_streamable`)。
**为什么要紧**:光链路的深 BER 恰是长码流的主要用户;现在只能走默认引擎,受全量波形内存限制。
**怎么做**:光路的 `fft_filter` 段换 `stream.Fir`;`OpticalNoise.inject` 与大信号曲线是逐样本的,先确认它们的随机抽取能按块读;
串扰同理(每个攻击者一条 `Fir`)。AMI GetWave 是黑盒、按整段调用,可能只能保留"不支持"。`collect_jitter` 需要改成累积统计量。
**验收**:每条新路径都有"流式 vs 默认统计一致 + 流式分块逐位一致"两条测试(`tests/test_stream.py` 的范式)。

---

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
