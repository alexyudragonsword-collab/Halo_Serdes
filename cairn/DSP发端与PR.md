---
type: project_topic
status: active
summary: "DSP 发端(阶段 0 + 1):TxPipeline 收拢七处拼装点、DAC 量化与 INL 的闭式与等效噪声折算、驱动器与 E/O 共用压缩参数 c、接收端起始均衡看不见 TX FFE 的既有缺口;PR 整形(阶段 2/3)的接缝"
tags: [tx, dac, driver, compression, sndr, rlm, partial-response, statistical-engine, invariant-3, invariant-5]
contains: [convention, approximation, closed-form, pitfall, known-gap]
created: "2026-10-02"
updated: "2026-10-02"
related: [architecture-invariants.md, engineering-pitfalls.md, 光互联建模.md]
authoring_mode: ai_generated
---
# DSP 发端与 PR 整形

> 计划:`https://claude.ai/code/artifact/99289727-ebdc-4725-b2b4-1e69763d7180`(四阶段:0 收拢 TX、1 DSP TX、
> 2 收端 PR、3 发端 PR 与三方同台)。本文件记阶段 0 + 1 的当前真相;阶段 2/3 开工时在这里续写。

## 1. 一条流水线(阶段 0)

`tx/pipeline.py::TxPipeline`:

| 域 | 级 | 状态 |
|---|---|---|
| 符号(每 UI 一个数) | 电平 `symbols_to_voltages` → **PR 占位** → FFE `tx_fir` → **DAC** | PR 恒等;顺序写死:PR 在 FFE 与 DAC 之前,发端 PR 的电平数由 DAC 承担 |
| 跨域 | ZOH:`rng=None` 用 `hold`(理想沿),否则 `edge_jitter_seq` + `jittered_zoh` | 唯一切换点(铁律 5);理想沿不能用"零偏移的分数填充"代替,边界样本舍入不同 |
| 波形 | **驱动器压缩** → 驱动器单极点 `tx.bw` | Hammerstein |
| 统计引擎 | `equivalent_symbol_response()` = 上采样 FIR(单抽头时 None) | DAC 不在里面,走 `dac_sigma_q` |

**阶段 0 找到并替换的拼装点(main @ 112ce94 行号)**:

| 文件 | 函数 | 行 |
|---|---|---|
| `engine/timedomain.py` | `run_time_link` | 232–235 |
| `engine/timedomain.py` | `_run_adc_link` | 390–393 |
| `engine/static_link.py` | `run_static_link`(经 `tx.build_tx_waveform`) | 118 |
| `engine/statistical.py` | `run_statistical`(FIR 卷进 h) | 165–166 |
| `engine/optical_stage.py` | `transmitter_power` | 147–150 |
| `analysis/reconstruct.py` | `front_end_waveform` | 53–56 |
| `analysis/cdr_tracking.py` | `tx_edge_offsets_s` | 36–39 |

旧 API `tx.build_tx_waveform` 与 `jitter.build_jittered_tx` 保留,委托给流水线。**不进流水线**:`analysis/com.py::_apply_tx_fir`
与 `io/ami.py::NativeCom.compute` 的 TX FIR(802.3 参考发端,docstring 写明 "no DAC / driver model");`engine/backchannel.py`
的 `upsampled_taps(taps)` 是训练时的试探脉冲,不是发端;`channel/model.py:136` 只用 Σtaps 定光功率尺度。

验证:475 值引擎指纹 + 44 值 TX 路径指纹(白噪 / 剖面时钟 × 两种 RX、重建、CDR 追踪边沿、光发射功率 c = 0 / 0.3、串联)
与 main 逐位相同;jit 550 / nojit 548 passed,与基线同数;`tests/test_tx_pipeline.py` 把"流水线 = 手拼"逐字节钉住(含 rng 后续状态)。

## 2. DAC(`tx/dac.py::TxDac`)

- **约定**:`dac_fs` 是**峰峰值**(与 `tx.swing`、`adc.fullscale` 一致),h = fs/2,LSB = fs/2^N,码 c → −h + (c + ½)LSB(中升)。
  默认 fs = swing·Σ|taps|,即 FFE 输出的峰峰值,按构造不削峰。|x| > h(1 + 1e-9) 计削峰 —— 容差是因为 backchannel 训到
  恰好填满满量程时,舍入会让一部分值超出 1e-16。
- **INL**:2^N − 1 个单元,低 N−m 位二进制成组、高 m 位 2^m − 1 个温度计元件,每单元 1 + e、e ~ N(0, σ_unit)。端点修正后
  Var(INL_c) = σ²c(1 − c/M),**与分段无关**(分段决定 DNL / 单调性:全温度计单调,纯二进制在 σ 0.3 时会非单调,测试钉住);
  码平均 E[INL²] = σ²(M − 1)/6(测试 60 种子 ±20%,逐码方差剖面 ±25%)。
- **失配用独立随机流** `default_rng([seed, 0x7DAC])`,不吃链路的 rng:失配是芯片属性,开 DAC 不能挪动后面的边沿 / 噪声 / ADC 抽样
  (测试:剖面时钟下有无 DAC 的边沿逐字节相同)。
- **SQNR 闭式**:6.02N + 1.76 假设误差在一个 LSB 内均匀。正弦大部分时间在峰值附近,所以**单一幅度**的结果取决于峰值落在顶码的
  哪个位置:理想 5 bit 恰好满幅读 31.48 dB,低 ¼ LSB 读 32.07(闭式 31.86),N = 5..8 恰好满幅都偏低 0.13–0.38 dB。
  `sqnr_of_sine` 对 16 个峰值依次走过顶码一个 LSB 的幅度取误差功率平均(对满幅信号功率),这正是闭式描述的条件:
  N = 3..10 与闭式差 ≤ 0.09 dB。量化器本身用均匀输入核过(误差功率 / (LSB²/12) = 1.000 ± 0.002)。

### 统计引擎的等效噪声

σ_q² = LSB²/12 + E[INL²]·LSB²,作为**每 UI 白噪**加在 DAC 输出,经 DAC → 判决器的符号响应(信道 × CTLE × VGA × RX FFE,
**不含** TX FFE,因为误差在 FFE 之后)的全部游标平方和到判决器:σ_rx = σ_q √Σp_k²,与 RX 噪声功率相加。
`dac_noise_at_slicer` 与"有无 DAC 两条波形之差在判决时刻的 RMS"对得上 15% 以内(5 / 7 bit,FFE 开)。

近似的边界:误差其实是 DAC 输入的确定函数。FFE 把输入摊到很多码上时像白噪;**没有 TX FFE 时 PAM4 四个电平落在四个固定码上**,
"噪声"其实是固定的电平偏移,统计引擎把它平均成高斯。实测这种最不利情形下铁律 3 仍过(下表),但比值随位数下降
(4 bit 0.73–0.81,6 bit 0.94–0.95)—— 位数更少时偏移更像确定性误差。

**铁律 3(只开 DAC 量化,静态引擎 vs 统计引擎,32 GBd PAM4,无 TX FFE,RX FFE 2+6、DFE 2)**:

| 损耗 @16 GHz | 噪声 | 4 bit 比值 | 5 bit | 6 bit | 4 bit 时 DAC 把 BER 抬了 |
|---|---|---|---|---|---|
| 6.4 dB | 0.030 | 0.81 | 0.89 | 0.95 | 2.73e-2 → 3.65e-2 |
| 10.7 dB | 0.020 | 0.77 | 0.87 | 0.94 | 1.99e-2 → 2.81e-2 |
| 15.0 dB | 0.012 | 0.73 | 0.85 | 0.94 | 9.48e-3 → 1.52e-2 |

为什么不带 TX FFE 测:见 §5,静态引擎看不见 TX FFE,带 FFE 的 BER 对照不可信;σ_q 的折算本身用上面的波形差 RMS 单独钉住。

## 3. 驱动器(`tx/driver.py`)与 E/O 共用压缩参数

- `StaticCurve` 从 `optical/eo.py` 提到 `core/static_curve.py`(`tx/` 与 `optical/` 互不 import,`core` 两边都能用),`optical/eo.py`
  重导出,光侧行为不变(TX 路径指纹含 c = 0.3 的光发射功率,逐位相同)。`rlm()` 一起搬。
- `drv_nl = "curve"` 用 **rollover** 形(凹,压顶电平),满量程 = DAC 满量程的一半(默认 swing·Σ|taps|/2),所以同 c、同归一化输入
  时驱动器与 VCSEL 曲线**逐位相同**(测试)。它是单端 / 一侧余量不够的驱动器;对称的差分级用 `tanh`(按单音 1 dB 压缩点定尺度,
  比值 A_1dB / V 由二分法解出,测得增益 −1.00 ± 0.02 dB)或 `cubic`(c₃ = 4/(3 OIP3²),输入钳在增益峰 1/√(3c₃);HD3 闭式
  c₃A³/4 ÷ (A − ¾c₃A³),实测差 < 1e-13 dB)。
- **Hammerstein**:曲线作用在 ZOH 后的波形上、单极点之前(测试逐字节钉住顺序)。光拓扑下驱动器先压、E/O(Wiener)再压。
- PAM4 测得 R_LM 随 c 单调降:理想 DAC、无带宽时 c = 0 / 0.1 / 0.2 / 0.3 → 1.000 / 0.940 / 0.874 / 0.801(示例 35)。
- 统计引擎不建驱动器非线性,`drv_nl != "none"` 时 warning(铁律 3 范围外)。接收端判决电平**不**跟随驱动器曲线(光侧阶段 3
  让 `_levels` 跟随 E/O 曲线;驱动器这里没做,压缩的代价因此全额落在固定电平的接收端上 —— 示例 35 的 reach 是这个口径)。

## 4. 示例 35 的读数

TX SNDR(示例 18 的发端:112 GBd PAM4,FFE (−0.06, 1, −0.12),同边沿理想发端作参考):

| DAC | c = 0 | 0.1 | 0.2 | 0.3 | 正弦闭式 |
|---|---|---|---|---|---|
| 5 bit | 30.97 | 27.94 | 23.57 | 20.05 | 31.86 |
| 6 bit | 40.20 | 30.45 | 24.33 | 20.38 | 37.88 |
| 7 bit | 43.57 | 30.73 | 24.42 | 20.44 | 43.90 |
| 8 bit | 48.87 | 30.91 | 24.48 | 20.47 | 49.92 |
| 理想 | ∞ | 31.00 | 24.51 | 20.49 | — |

- c = 0 时不是每 bit 正好 6 dB(5→6 +9.2、6→7 +3.4、7→8 +5.3):PAM4 × 3 抽头只有 64 个不同的 DAC 输入,误差是这 64 个值的
  确定性舍入,不是均匀分布;平均斜率 6.0 dB/bit 与闭式一致。
- c ≥ 0.1 时 SNDR 由压缩决定,位数几乎不再起作用(c = 0.1:5 bit 27.9,8 bit 30.9,理想 31.0)。

Reach(示例 18 的信道扫描,FFE + memory-2 MLSD,post-KP4 1e-15 ⇔ MLSD 后 pre-FEC ≤ 2.19e-4,400k 符号):

| 发端 | reach [dB @56 GHz] | 相对理想 |
|---|---|---|
| 理想 DAC | 28.48 | — |
| 8 bit | 28.46 | −0.02 |
| 7 bit | 28.37 | −0.11 |
| 6 bit | 28.71 | +0.23 |
| 7 bit + c = 0.1 | 28.19 | −0.29 |
| 7 bit + c = 0.2 | 26.78 | −1.70 |
| 理想 DAC + c = 0.2 | 26.88 | −1.60 |

分辨率:6 bit 比理想**多** 0.23 dB 说明这条扫描的逐次差异在 ±0.25 dB 量级(量化改变了 LMS / CDR 的收敛轨迹,
不是 6 bit 更好),所以 6 / 7 / 8 bit 与理想之间的差都在噪声里。

**判断**:在示例 18 的 224G LR 链路上(RX 噪声 1.5 mV rms、ADC ENOB 6.5),**7 bit DAC 不是瓶颈**:TX SNDR 43.6 dB,reach −0.11 dB,在扫描分辨率内。**瓶颈是驱动器压缩**:c = 0.2(R_LM 0.87、TX SNDR 24.4 dB)让 reach 掉 1.6–1.7 dB,此时 7 bit 与理想 DAC 没有差别;c = 0.1(R_LM 0.94、SNDR 30.7 dB)掉 0.3 dB。口径:接收端判决电平不跟随驱动器曲线(自适应参考电平可能追回一部分,未测)、接收端起始均衡看不见 TX FFE(§5,本例 TX 主抽头 1.0,影响小);这是本模型的数,不是器件规格。

## 5. 既有缺口:接收端的起始均衡看不见 TX FFE

静态引擎从**不含 TX FIR** 的信道脉冲解 RX FFE、取相位、算 DFE 和电平尺度,而它均衡的波形含 TX FIR;时域引擎两种架构的起始
MMSE FFE、DFE 种子、电平尺度与符号延时同样不含 TX FIR,靠自适应追回。统计引擎反而对(FIR 卷进 h)。实测(静态,32 GBd,
0.25 m,TX (−0.08, 0.78, −0.14)):NRZ 判决 SNR 27.7 → 11.2 dB,PAM4 BER 0 → 5.3e-2(统计 1.2e-11)。预置的 TX FIR 都很温和
(主抽头 1.0),影响小;DSP TX 让发端 FFE 担均衡时这条就要命。**没在本 PR 修**:修法改动 6 个带 TX FIR 预置的逐位指纹,
与"阶段 1 新开关默认关闭、关着时逐字节不变"的约定冲突;进 ROADMAP P1 1c,单独一个 PR。
示例 35 用示例 18 的发端(主抽头 1.0),受影响很小。

另一个既有缺口:统计引擎从未建模 `tx.bw`(驱动器单极点)。阶段 0 保持原样(逐字节),记在 ROADMAP P3 #8。

## 6. 阶段 2/3 的接缝(未做)

- 发端 PR:`TxPipeline.pr_filter`(FFE 与 DAC 之前,恒等占位)。PR 开启后 `dac_fullscale` 的默认值要按 PR 后的峰值重算。
- 收端 PR:不碰发端;`PrConfig` 只放一份在 `LinkConfig`,发收两端都读。
- 802.3dj 的 TX SNDR / R_LM 规格值(量级 30+ dB / ≥ 0.95)在出口代理后核不到,示例与文档都标 "approximate, unsourced",不画成限值。
