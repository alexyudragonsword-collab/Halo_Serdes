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

**更正(2026-10-03,TX FFE 起始均衡修好后重跑)**:示例 18 的发端有一个前标(n_pre = 1),旧的起始均衡同样看错了脉冲,
上表整体偏低约 3.4 dB。修后:理想 31.90、8 bit 31.87(−0.03)、7 bit 31.90(+0.01)、6 bit 32.50(+0.60,扫描噪声)、
7 bit + c = 0.1 31.60(−0.30)、7 bit + c = 0.2 30.28(−1.62)、理想 + c = 0.2 30.68(−1.22)。TX SNDR 表不变(发端的数)。
判断不变:7 bit 不是瓶颈,压缩是。

**再更正(2026-10-03,`pd_input` 默认 `auto` 后重跑;示例 18 的收端是 PAM4 ADC,MM-CDR 从读 ADC 原始样本改为读均衡后样本)**:
理想 31.82、8 bit 32.45(+0.63,扫描噪声)、7 bit 31.57(−0.25)、6 bit 31.54(−0.28)、7 bit + c = 0.1 31.79(−0.03)、
7 bit + c = 0.2 29.43(−2.40)、理想 + c = 0.2 29.81(−2.02)。DAC 位数的差仍在 ±0.6 dB 的扫描噪声里;c = 0.2 的代价从
1.2–1.7 dB 变成 2.0–2.4 dB(CDR 不再锁偏后其余损伤都小了,压缩占比更大)。判断不变,并更强:7 bit 不是瓶颈,压缩是。

**三更(2026-10-03,统计引擎一致性修复后重跑;时域接收噪声改为限带,插值采样不再把它稀释到约 0.8σ)**:理想 31.90、
8 bit 31.87(−0.03)、7 bit 31.85(−0.04)、6 bit 31.55(−0.35)、7 bit + c = 0.1 31.81(−0.09)、7 bit + c = 0.2 30.41(−1.49)、
理想 + c = 0.2 30.10(−1.80)。上一次 c = 0.2 的 2.0–2.4 dB 一部分是扫描噪声(那次 8 bit 比理想还多 0.63 dB),本次扫描噪声收窄到
≤ 0.35 dB,c = 0.2 的代价回到 1.5–1.8 dB。判断不变:7 bit 不是瓶颈,压缩是。

**判断**:在示例 18 的 224G LR 链路上(RX 噪声 1.5 mV rms、ADC ENOB 6.5),**7 bit DAC 不是瓶颈**:TX SNDR 43.6 dB,reach −0.11 dB,在扫描分辨率内。**瓶颈是驱动器压缩**:c = 0.2(R_LM 0.87、TX SNDR 24.4 dB)让 reach 掉 1.6–1.7 dB,此时 7 bit 与理想 DAC 没有差别;c = 0.1(R_LM 0.94、SNDR 30.7 dB)掉 0.3 dB。口径:接收端判决电平不跟随驱动器曲线(自适应参考电平可能追回一部分,未测)、接收端起始均衡看不见 TX FFE(§5,本例 TX 主抽头 1.0,影响小);这是本模型的数,不是器件规格。

## 5. 既有缺口:接收端的起始均衡看不见 TX FFE

静态引擎从**不含 TX FIR** 的信道脉冲解 RX FFE、取相位、算 DFE 和电平尺度,而它均衡的波形含 TX FIR;时域引擎两种架构的起始
MMSE FFE、DFE 种子、电平尺度与符号延时同样不含 TX FIR,靠自适应追回。统计引擎反而对(FIR 卷进 h)。实测(静态,32 GBd,
0.25 m,TX (−0.08, 0.78, −0.14)):NRZ 判决 SNR 27.7 → 11.2 dB,PAM4 BER 0 → 5.3e-2(统计 1.2e-11)。预置的 TX FIR 都很温和
(主抽头 1.0),影响小;DSP TX 让发端 FFE 担均衡时这条就要命。**没在本 PR 修**:修法改动 6 个带 TX FIR 预置的逐位指纹,
与"阶段 1 新开关默认关闭、关着时逐字节不变"的约定冲突;进 ROADMAP P1 1c,单独一个 PR。
示例 35 用示例 18 的发端(主抽头 1.0),受影响很小。

**更正(2026-10-03,已修,分支 fix/rx-init-tx-ffe)**:上面"没在本 PR 修"那段已经过时。`TxPipeline.receiver_view(h)` 把 TX FFE
的符号响应卷进接收端做脉冲分析用的 h,并返回 `n_pre × osr` 的超前量(波形里的 FIR 已对齐到主抽头,卷积后的峰值晚 n_pre UI);
静态引擎、时域两种架构、`reconstruct.front_end_waveform` 都用它找峰值、取游标,再把超前量从位置里扣掉。单抽头时返回原数组
与 0,逐字节不变。结果:

| 配置 | 修前 | 修后 |
|---|---|---|
| 静态 NRZ,TX (−0.08, 0.78, −0.14),SNR | 11.2 dB | 26.2 dB |
| 静态 PAM4,同上,BER(统计 7e-14) | 5.3e-2 | 0 |
| ADC 时域,TX (−0.1, 0.75, −0.15),BER / SNR | 0.10 / 9.5 dB | 4.6e-5 / 16.9 dB |
| 静态 vs 统计,强 TX FFE,三损耗点 ± 5 bit DAC | 测不了 | 0.83–1.17 |

指纹:475 值里 131 个变了,全在 6 个带 TX FIR 的预置的静态 / 时域引擎上;统计引擎、COM、单抽头预置逐位不变。静态引擎全面
变好(如 NRZ 28G SNR 14.9 → 28.3 dB)。**4 个 ADC 预置的时域 BER 反而高了 1.1–1.6 倍**,查下来与本修复无关:这些预置的
MM-CDR 读均衡前的 ADC 采样,锁在偏离峰值 0.1–0.3 UI 的地方并来回摆(两种起点下相位轨迹逐位相同),误码几乎全出在偏得
最远的时段;相位在峰值附近的时段新起点好 3–10 倍,改成 `pd_input: ffe` 后新起点的 BER 比旧起点低 18–30 倍。进 ROADMAP P1 1c(新)。

**后续(2026-10-03,分支 fix/adc-presets-pd-ffe)**:4 个 224G ADC 预置改成 `pd_input: ffe`。预置原注释说"fully-equalized
input has no gradient"(均衡后的样本没有 MM 梯度)—— **被推翻**:FFE 的确把 h(±1) 压向 0,但 CDR 照样跟踪。SJ 0.1 UI @ 0.5 MHz
下 `ffe` 的恢复相位峰峰动了 0.27–0.40 UI,跟踪误差 rms 0.026–0.062 UI,都比 `adc` 的 0.058–0.152 UI 小;无 SJ 时 `ffe` 相位
不动(0.003–0.005 UI)是因为它本来就在峰值上,不是因为不动。在 0.2 UI @ 1 MHz 以上两种输入都失锁,环路带宽本身是另一个问题。

**库默认值(2026-10-03,分支 feat/pd-input-auto):`CdrConfig.pd_input` 改为 `auto`**,由 `LinkConfig.mm_pd_input`
按调制解析:PAM4 → `ffe`,NRZ → `adc`;显式写 `adc` / `ffe` 照旧。依据是 48 点扫描(`adc_dsp`,8 bit,FFE 3/10,MM kp 6/8,
NRZ 26.5625 GBd / PAM4 53.125 GBd × 0.1 / 0.25 / 0.4 m × FFE lms/none × DFE 0/1,各跑无 SJ 与 SJ 0.1 UI @ 0.5 MHz):

| | `ffe` 相对 `adc` |
|---|---|
| PAM4 0.25 m | SNR 17.9 → 27.4 dB,BER 3.6e-4 → 5.1e-6;kp 8 时 1.9e-3 → 5.1e-6 |
| PAM4 0.1 m,kp 8 + SJ | `adc` 失锁(BER 0.15,跟踪误差 0.44 UI),`ffe` 2.2e-2 / 0.18 UI |
| PAM4 0.4 m | SNR +0.7–1.0 dB;其余持平 |
| NRZ 0.1 m | **`ffe` 相位游走 0.41–0.44 UI**(`adc` ≤ 0.01),SNR −1 dB;kp 8 + SJ 跟踪误差 0.18 vs 0.07 UI |
| NRZ 0.25 m | SNR +1.9 dB,但 kp 6 + SJ 跟踪误差 0.13 vs 0.05 UI |
| NRZ 0.4 m | SNR −0.7 dB;SJ 下 BER < 3.4e-6 → 3.7e-5–1.2e-4 |

**更正上一段的"被推翻"**:均衡后的样本没有 MM 梯度这一担心对 PAM4 不成立,**对轻 ISI 的 NRZ 成立** —— FFE 在一大片相位上都
把 h(±1) 压到 0,MM 在那片区域里随机游走。PAM4 用 `adc` 时锁在未均衡脉冲 h(−1) = h(+1) 的点上,离均衡后的最优点多远
取决于信道和 CTLE(0.25 m 上 CTLE 3 dB 差 3.4 dB SNR、6 dB 差 9.5 dB)。两条都写进 `tests/test_mm_pd_input.py`。
指纹:475 值里只有 LPO 预置(PAM4,没写 `pd_input`)的时域块变了,9 → 11 个误码、SNR +0.03 dB,统计上持平。

另一个既有缺口:统计引擎从未建模 `tx.bw`(驱动器单极点)。阶段 0 保持原样(逐字节),记在 ROADMAP P3 #8。

## 6. 阶段 2/3 的接缝(阶段 2 见 §7,阶段 3 见 §8)

- 发端 PR:`TxPipeline.pr_filter`(FFE 与 DAC 之前,恒等占位)。PR 开启后 `dac_fullscale` 的默认值要按 PR 后的峰值重算。
- 收端 PR:不碰发端;`PrConfig` 只放一份在 `LinkConfig`,发收两端都读。
- 802.3dj 的 TX SNDR / R_LM 规格值(量级 30+ dB / ≥ 0.95)在出口代理后核不到,示例与文档都标 "approximate, unsourced",不画成限值。

## 7. 收端 1 + aD 整形(阶段 2,2026-10-03)

**接口**:`PrConfig(target=(1.0,) | (1.0, a), at="rx")`,a ∈ [0, 1];`at="tx"` 抛 NotImplementedError 指向 ROADMAP P3 #8 阶段 3;
mixed-signal + PR 在 `LinkConfig` 构造时拒绝(没有数字 FFE 可整形);静态引擎、`fixed_datapath` 拒绝 PR。`target=(1.0,)`
与不设逐位相同(指纹 488 + 44 个值)。

**接线**(`engine/timedomain.py`、`cdr/adc_kernel.py`、`dsp/ffe.py`):
- 起始 FFE:`mmse_ffe(..., target=)`(ZF 版对 [1, a] 残余 < 1%);LMS 期望值 levels[x_s] + a·levels[x_{s−1}];
- 逐符号判决(给 LMS、CDR、DFE 用):减 a × 上一判决再切;a = 1 + 预编码时切 2N − 1 个合成电平,用户符号 = q mod N(不传播),
  另有一条截断的线路符号链给 DFE / 残差 / LMS,合成电平到两端时自动重同步;
- DFE 从受控光标之后起;MLSD 光标 [1, a_实测, r…](a 取最终 FFE 的均衡光标);
- MM-CDR 读"样本 − a × 上一判决"。原方案的 `pd_offset` 补偿不行:a ≥ 0.75 时环路走 0.4–4 UI。修后锁定点与 delta 目标差
  ≤ 0.0011 UI(a 0.25–1、含预编码,示例 18 信道 −33 dB)。

**统计引擎**:受控光标移出 ISI PDF;有序列检测器时对交替误差事件(长度 1–12)做 union bound,噪声自相关取自 FFE 抽头
(`pr_error_events`)。只用最小距离时比时域乐观 2.5–20 倍(PR 下 FFE 输出噪声 ρ1 ≈ −0.25、ρ2 ≈ −0.31;a ≈ 1 时各长度事件距离相同)。
与时域比(`enob=None`,36.4 dB 信道):a = 0.5 1.47–1.52×、a = 1 预编码 1.00–1.35×(BER 2e-5…3e-4);BER 1e-2 以上
union bound 偏悲观(a = 0.5 3.2×)。统计引擎仍不建 ADC 量化噪声(ROADMAP 4b)。

**示例 36**(示例 18 的 21 抽头 LMS FFE + MM-CDR + memory-2 Viterbi,ENOB 6.5,1.5 mV,40 万符号):

| 损耗 @ 56 GHz | a = 0(对照) | 0.25 | 0.5 | 0.75 | 1 |
|---|---|---|---|---|---|
| 28.8 dB | 3.8e-6 | 0 错 | 0 错 | 0 错 | 0 错 |
| 33.3 dB | 1.7e-3 | 1.2e-4 | 7.6e-6 | **2.5e-6** | 2.5e-6 |
| 37.9 dB | 2.5e-2 | 1.0e-2 | 2.8e-3 | **1.1e-3** | 1.3e-3 |

reach(post-KP4 1e-15 ⇔ pre-FEC 2.19e-4):对照 31.6 dB;a = 0.25 / 0.5 / 0.75 / 1 → 33.9 / 35.5 / **36.3** / 36.2 dB(+2.2 / +3.9 / **+4.7** / +4.6)。
a = 1 预编码 8.9e-6 vs 不预编码 2.5e-6(33.3 dB):Viterbi 的一个错误解码后变两个,预编码的价值只在逐符号判决不传播。
33.3 dB 处 a ≥ 0.75 的 2.5e-6 是 2 个比特错,种子 3 / 4 / 5 给 2 / 0 / 2,是计数统计,不是误码底。

**判断**:在本模型的 224G LR 链路上,收端 1 + 0.75D + Viterbi 比 delta 目标 + Viterbi 多约 4.7 dB reach —— 这正是示例 18
里 MLSD 加不了 reach 的另一半:delta 目标的 FFE 把噪声放大花掉了,整形后由网格收回。口径:a 由配置给定(不自适应)、
memory-2、单 lane、无串扰;与示例 19 / 21 的 FEC / ADC 杠杆是否可叠加未量。

## 8. 发端 1 + aD(阶段 3,2026-10-03)

**接口**:`PrConfig(target=(1.0, a), at="tx")`。`TxPipeline.pr_filter` 在电平之后、FFE / DAC 之前做 (x_k + a·x_{k−1}) / (1 + a);
`equivalent_symbol_response` 把它并进去(两个引擎的脉冲分析都看得见),收端用与 §7 相同的 1 + aD 检测。**峰值归一**是有意的:
发端的约束是 DAC / 驱动器的满量程,合成电平多出来的那份从电平间距里出(a = 1 时间距减半)。不设 `at="tx"` 时逐位同 main(488 + 44 值)。

**主光标定位(坑)**:1 + aD 脉冲有两个可比的光标(a = 1 时等高),取 argmax 会落在 a·x_{k−1} 那个上,收端整体错一个 UI。
时域引擎改为在不含 PR 的脉冲上找峰(`receiver_view(shaping=False)`,lead 与起点不变);统计引擎在收端 FFE 之后的脉冲上,
PR 开启且前一个 UI 的样本 ≥ 峰值一半时取前一个。

**示例 37**(示例 18 的接收端,memory-2 Viterbi,40 万符号):

| 配置 | reach | 比无 PR | 预测 无 PR − 20·log10(1 + a) |
|---|---|---|---|
| 无 PR(delta + Viterbi) | 31.6 dB | — | |
| 收端 a = 0.75 | **36.3 dB** | **+4.7** | |
| 发端 a = 0.25 | 29.7 dB | −1.9 | 29.7 |
| 发端 a = 0.5 | 28.1 dB | −3.6 | 28.1 |
| 发端 a = 0.75 | 26.9 dB | −4.7 | 26.8 |
| 发端 a = 1 | 26.9 dB | −4.7 | 25.6 |

30.3 dB 处 slicer SNR:无 PR 18.8、收端 23.1、发端 16.2–17.2 dB。

**为什么**:线性链路、噪声在收端。收端 FFE 要把"信道 ⊛ 发端 PR"均衡到 [1, a],等价于把信道均衡到 delta —— 与无 PR 时同一个
均衡器、同样的噪声放大;发端 PR 只把 a 交给 Viterbi,而 Viterbi 在 delta 目标下本就无事可做(示例 18)。所以发端 PR = 无 PR 减去
峰值代价。收端 PR 的收益恰恰来自"FFE 少反演一点",这在发端做不到。理想 ADC 下把发端摆幅放大 (1 + a) 倍(不限峰值)时,
发端 PR 的 slicer SNR 回到收端 PR 的 1.4–2.5 dB 内(`test_tx_pr_is_rx_pr_moved_plus_its_peak_cost`);在 ENOB 6.5 的 ADC 上这个
对照做不干净 —— 摆幅放大要求 ADC 满量程同比放大,ENOB 噪声跟着放大。

**判断**:在本模型的 224G 电链路上,发端 PR 不值得做;收端 PR(§7)是 PR 的正确位置。发端 PR 可能占优的条件本模型没有:
噪声 / 串扰在发端带宽限制之前进入、或发端器件本身带宽受限且噪声在其后(光调制器 duobinary 的场景)。口径:a 固定、memory-2、
无 DAC 量化(加 DAC 后发端 PR 的间距更小,只会更差)。

**统计引擎缺口**:发端 a = 0.5 时比时域悲观 2.5–2.9×(FFE 输出 ρ1 ≈ −0.67,长度 2–6 的交替事件距离 1.10–1.20 几乎相同,union bound
重复计嵌套事件);发端 a = 1 预编码 1.2–1.5×。ROADMAP P3 #8。

