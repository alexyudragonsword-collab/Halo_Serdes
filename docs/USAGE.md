# Halo_Serdes 使用指南 / Usage Guide

按**任务**组织,而不是按模块。每段都是可直接粘贴运行的最小代码。
模块级细节看各模块 docstring;能力全景看 [`../README.md`](../README.md);
交互式操作看 [`GUI.md`](GUI.md)。

> 所有片段假设已安装:`pip install -e ".[gui,fec,jit,test]"`

---

## 目录

1. [配置:一切的起点](#1-配置一切的起点)
2. [跑一条链路](#2-跑一条链路)
3. [三个引擎怎么选](#3-三个引擎怎么选)
4. [读结果](#4-读结果)
5. [换信道](#5-换信道)
6. [参数扫描](#6-参数扫描)
7. [串扰](#7-串扰)
8. [COM 与合规评估](#8-com-与合规评估)
9. [抖动:注入、分解、容限](#9-抖动注入分解容限)
10. [MLSD 与 1+D 预编码](#10-mlsd-与-1d-预编码)
11. [FEC 投影](#11-fec-投影)
12. [IBIS-AMI 模型](#12-ibis-ami-模型)
13. [定点与 RTL 出口](#13-定点与-rtl-出口)
14. [性能与内存](#14-性能与内存)
15. [常见问题](#15-常见问题)
16. [光互联:LPO / CPO 作为同一条链](#16-光互联lpo--cpo-作为同一条链)
17. [DSP 发端:DAC 与驱动器压缩](#17-dsp-发端dac-与驱动器压缩)
18. [接收端部分响应(PR)整形](#18-接收端部分响应pr整形)

---

## 1. 配置:一切的起点

框架里**没有全局状态**,一切由一个 frozen dataclass `LinkConfig` 决定 ——
引擎、GUI、COM、RTL 参数导出全部读它。

```python
from halo_serdes.config import LinkConfig
from halo_serdes.config.loader import load_config, dump_config, apply_overrides

cfg = load_config("configs/pam4_224g_adc.yaml")     # 从 YAML
cfg = LinkConfig()                                   # 或库默认(16 GBd NRZ)
```

改配置用 `dataclasses.replace`(frozen,不能就地改)或 dotted-path 覆盖:

```python
import dataclasses as dc
cfg2 = dc.replace(cfg, sim=dc.replace(cfg.sim, n_symbols=200_000))

# 等价、更简洁的写法:
cfg2 = apply_overrides(cfg, {"sim.n_symbols": 200_000,
                             "rx.ffe.n_post": 12,
                             "rx.cdr.kp_shift": 6})
dump_config(cfg2, "my_run.yaml")                     # 存回 YAML
```

**非法值会当场报错**,而不是在引擎深处炸掉:

```python
LinkConfig(osr=-4)      # ValueError: osr must be >= 1 sample/UI, got -4
FfeConfig(adapt="lsm")  # ValueError: ffe.adapt must be one of ['lms','none','wiener']
```

内置配置在 `configs/`:`nrz_16g_ms`、`nrz_32g`、`pam4_32g_ms`、
`pam4_224g_adc`(106.25 GBd 主锚点)、`pam4_224g_112g_adc`(112 GBd 压力档)、
`pam4_deep_lr_adc`、`pam4_112g_adc_mismatch`(TI 失配)、`nrz_28g_analytic`。

---

## 2. 跑一条链路

```python
from halo_serdes.engine import run_time_link

res = run_time_link(cfg)
print(res.summary())
# n=195000  BER=1.763e-03 (7/3970)  SER=2.015e-03  slicer SNR=19.1 dB
```

复用信道模型可以省掉重复的 S 参数处理(扫描时必做):

```python
from halo_serdes.channel import ChannelModel
ch = ChannelModel.from_config(cfg)          # 一次
for c in variants:
    run_time_link(c, channel=ch)            # 复用
```

---

## 3. 三个引擎怎么选

| 引擎 | 调用 | 速度 | 给你什么 | 什么时候用 |
|---|---|---|---|---|
| **时域 MC** | `run_time_link` | 慢 | 逐符号动态:CDR 牵引、自适应轨迹、眼图、真实 BER | 需要动态行为、或验证统计引擎 |
| **统计 StatEye** | `run_statistical` | 快 | BER(φ) 浴盆、统计眼、可外推到 1e-15 | 扫描、低 BER 尾部、合规评估 |
| **静态快评** | `run_static_link` | 最快 | 固定均衡下的 BER/SNR | 信道可行性粗筛 |

```python
from halo_serdes.engine.statistical import run_statistical
from halo_serdes.engine import run_static_link

stat = run_statistical(cfg, channel=ch)
print(stat.ber, stat.best_phi)          # 最佳采样相位处的 BER
```

**双引擎交叉校验**是本框架的核心纪律 —— 纯 LTI + AWGN 下两者应在 2× 内:

```python
t = run_time_link(cfg, channel=ch)
s = run_statistical(cfg, channel=ch)
print(f"stat/time = {s.ber / t.ber.ber:.2f}x")     # 期望 <2
```

时域引擎的 MC BER 是 `res.ber.ber`(`BerResult` 带置信信息),
统计引擎的是 `stat.ber`(浮点)。

---

## 4. 读结果

`SimResult` 的主字段:

```python
res.ber            # BerResult: .ber, .n_errors, .n_checked, .error_idx
res.ser            # 符号错误率 (float)
res.slicer_snr_db  # 判决器输入 SNR —— ADC 架构的主指标
res.n_symbols      # 计入统计的符号数(已扣除 warmup)
res.ffe_taps       # 收敛后的 FFE 抽头 (ADC 架构)
res.dfe_taps       # 收敛后的 DFE 抽头
res.eye_data       # 折叠眼迹线 (collect_eye=True 时)
res.y_slicer       # 判决器输入采样
res.extras         # 诊断字典,见下
```

`extras` 里按架构提供:

| 键 | 架构 | 内容 |
|---|---|---|
| `phase_track` | 两者 | CDR 恢复相位序列 |
| `levels` / `main_cursor` | 两者 | 判决电平、主光标 |
| `settle` / `train_end` / `warmup` | 两者 | 启动状态机分界 |
| `ser_slicer` / `mlsd_resid` | 两者 | MLSD 前的原始基线、残余光标 |
| `cycle_slips` / `slip_at` | 两者 | 计分时找到的整 UI 周跳次数与位置(计分后判决序号);BER 按段重对齐计,不读成 0.5 |
| `w_dfe_hist` / `n_ave` | mixed-signal | DFE 抽头收敛轨迹 |
| `pd_hist` | mixed-signal | 鉴相器输出 |
| `lane_ser` / `adc` / `q_hist_head` | ADC | 逐 lane SER、ADC 模型、码字直方 |
| `jitter_budget` | 两者 | 逐级抖动预算(`collect_jitter=True`) |

---

## 5. 换信道

**Touchstone(实测/仿真 S 参数)**:

```yaml
channel:
  kind: touchstone
  file: data/channels/whisper42p8in_thru.s4p
  lane: 0            # 8/12 端口文件里选 lane
  renumber: true     # 自动检测 1<->3 端口错序
  zs_diff: 100.0     # 差分端接
  zl_diff: 100.0
```

**解析 RLGC**(参数化扫描用,没有文件依赖):

```yaml
channel:
  kind: analytic
  length_m: 0.20
  rdc: 5.0
  r_skin: 2.0e-3     # R(f) = rdc + r_skin*sqrt(f)
  loss_tangent: 0.012
  n_freq: 8192
```

看信道本身:

```python
ch = ChannelModel.from_config(cfg)
print(f"Nyquist 插损 {ch.loss_at(cfg.f_nyquist):.1f} dB")
il = ch.insertion_loss_db()        # 全频段
h  = ch.impulse(cfg.dt)            # 冲激响应
p  = ch.pulse(cfg.dt, cfg.osr)     # 脉冲响应(取 ISI 光标用)
```

---

## 6. 参数扫描

扫描的通用模式:**复用 channel、用统计引擎、只改要扫的字段**。

```python
import numpy as np, dataclasses as dc
from halo_serdes.engine.statistical import run_statistical

bers = []
for peak_db in np.arange(0, 12, 1.0):
    c = apply_overrides(cfg, {"rx.ctle.peak_db": float(peak_db)})
    bers.append(run_statistical(c, channel=ch).ber)
```

扫信道长度(reach 阶梯)时信道会变,必须重建:

```python
for L in np.linspace(0.1, 0.4, 10):
    c = apply_overrides(cfg, {"channel.length_m": float(L)})
    cm = ChannelModel.from_config(c)                 # 长度变了,要重建
    print(cm.loss_at(c.f_nyquist), run_statistical(c, channel=cm).ber)
```

参考示例:`12`(包络扫描)、`18`–`21`(深 LR 杠杆)、`08`(定点位宽墙)。

---

## 7. 串扰

一个 `XtalkAggressor` 对象**同时**驱动时域和统计两个引擎,保证一致:

```python
from halo_serdes.channel import synthetic_aggressor, aggressor_bank, icn_rms

fext = synthetic_aggressor("fext", -28.0, cfg.ui, cfg.dt, modulation="pam4", seed=1)
next_ = synthetic_aggressor("next", -30.0, cfg.ui, cfg.dt, modulation="pam4", seed=2)

t = run_time_link(cfg, channel=ch, xtalk=[fext, next_])           # 时域:注入波形
s = run_statistical(cfg, channel=ch,
                    xtalk_pulses=[a.pulse(cfg.osr) for a in (fext, next_)])
```

**多 lane 环境**(N 个 FEXT + M 个 NEXT,逐 lane 独立数据与耦合抖动):

```python
bank = aggressor_bank(4, 4, -30.0, cfg.ui, cfg.dt, modulation="pam4", base_seed=1)
print(f"ICN = {icn_rms(bank, cfg.osr, modulation='pam4')*1e3:.1f} mV rms")   # ~sqrt(N)
```

从多端口 Touchstone 提取真实耦合:

```python
from halo_serdes.channel import import_xtalk
agg = import_xtalk(ntwk, victim_out=1, aggressor_in=0, dt=cfg.dt, f_max=40e9)
```

---

## 8. COM 与合规评估

两个实现,同一个 `ComAdapter.compute` 接缝:

```python
from halo_serdes.io import Com93a, NativeCom

# 忠实 IEEE 802.3 Clause 93A/178A:EQ 网格按 FOM 优化、DFE 抽头由光标经 b_max 导出、
# A_ni 从卷积后的干扰+噪声 PDF 在目标 DER 处读取
r = Com93a().compute(ch, cfg, xtalk_pulses=[a.pulse(cfg.osr) for a in bank])
print(r.summary())          # COM=..dB (A_s, A_ni; σ_ISI σ_XT σ_N σ_J)
print(r.detail["ctle_peak_db"], r.detail["sample_phase"])   # 优化结果

# 透明的行为级 RSS 图(快、易读,非标准)
NativeCom().compute(ch, cfg)
```

调参数用 `ComParams`:

```python
from halo_serdes.analysis.com import ComParams, compute_com
compute_com(ch, cfg, params=ComParams(target_der=1e-5, n_dfe=2, b_max=0.85,
                                      ctle_peak_grid_db=(0, 3, 6, 9, 12)))
compute_com(ch, cfg, params=ComParams(rx_ffe=(4, 10)))   # 指定 Rx FFE;rx_ffe=None 关掉
```

- **参考接收机按架构**:mixed-signal 是 93A 的 CTLE + DFE;ADC 是 178A 式 CTLE + 波特间隔 Rx FFE + DFE,
  FFE 大小默认取 `rx.ffe`(`ComParams.rx_ffe="auto"`)。每个相位上 FFE 抽头按 MMSE 解:DFE 覆盖的后光标不算误差,
  接收噪声与串扰经抽头放大一并计入(否则无噪声时 FFE 会迫零)。`r.detail["rx_ffe_taps"]` 给出所选抽头。
  Rx FFE 的抽头上限(178A 限前光标)不建。
- **噪声 σ_N**:`rx.noise_rms`,ADC 架构再按功率加上量化噪声(与统计引擎同一式,`adc_noise_sigma`)。
- 2026-10-05 前 ADC 架构的 COM 也按 93A 接收机算、且不含量化噪声:各 ADC 预设读 −6…−9 dB(链路实际能跑);
  现在 4.6–19.8 dB。mixed-signal 不变。

---

## 9. 抖动:注入、分解、容限

**注入**(Tx 侧,`TxConfig`):

```yaml
tx:
  clock:             # 发送时钟(见 docs/clock_profile.md)
    kind: white      # white | profile
    rj_ui: 0.005     # 随机抖动 sigma [UI]
    sj_ui: 0.05      # 正弦抖动幅度 [UI]
    sj_freq: 10.0e6  # 正弦抖动频率 [Hz]
    dcd_ui: 0.01     # 占空比失真 [UI]
```

**PLL 相噪剖面时钟**(`kind: profile`):用 pll_simulator 导出的 L(f) + 杂散表合成
*有色*的逐沿时间偏移,代替"一个 `rj_ui` 数字"。格式与单位约定见
[`docs/clock_profile.md`](clock_profile.md);随库附带 `data/clock_profiles/` 下七个
JSSC 基准 PLL 的剖面。

```yaml
tx:
  clock:
    kind: profile
    file: data/clock_profiles/bench_wu19_spll_frac_52m_6p253g.yaml
    # f0_hz: 6.253e9     # 可选:覆盖文件里的载波(只改相位→秒的换算,不改曲线)
    # sj_ui: 0.02        # 三项白噪抖动仍可叠加在剖面之上(例如做 JTOL)
```

```python
from halo_serdes.tx.clock import ClockProfile
prof = ClockProfile.load("data/clock_profiles/bench_wu19_spll_frac_52m_6p253g.yaml")
prof.rms_jitter_s(1e5, prof.f0_hz / 2)       # 剖面连续部分在某频带上的 RMS 抖动 [s]
```

> 旧写法 `tx.rj_ui` 等四个字段仍能加载(loader 自动迁到 `tx.clock.*`),
> 但新写的配置请直接放在 `tx.clock` 下。

**剖面时钟经过 CDR**:时域引擎直接跑环路;统计引擎用 `cdr/linear.py` 的线性化环路
算出"环路追不上的剖面功率 + 环路自身噪声"作为采样时刻抖动 σ,代替 `rj_ui` 模糊
(双引擎在 1/f² 时钟下 BER 仍在 2× 内;模型假设清单在 `engine/statistical.py` 顶部)。

```python
from halo_serdes.analysis.cdr_tracking import cdr_tracking_error_s
res = run_time_link(cfg, channel=ch)                 # kind: profile 的配置
err = cdr_tracking_error_s(cfg, res)                 # 恢复时钟 − 发送时钟,逐判决 [s]
st = run_statistical(cfg, channel=ch)
sol = st.extras["clock_loop"]                        # 环路模型:k_pd、带宽、未追踪 σ、自噪声 σ
print(err.std() * 1e15, sol.sigma_ui * cfg.ui * 1e15)   # 实测 vs 模型 [fs]
prof.scaled_to_rms(200e-15, f_lo=cfg.symbol_rate / cfg.sim.n_symbols).save("x_200fs.yaml")
```

剖面在环路带宽内的漂移速率接近 `kp` 时 BB-CDR 会失锁(周期滑移),统计引擎会给出
`slew-limited` 警告 —— 这时线性模型不再描述内核在做什么。`examples/31` 用三个 200 fs
时钟(白噪 / SSPLL / CPPLL)扫 `kp_shift`,出 BER、眼高、残余抖动实测与模型的对照表。

**接收端采样时钟**(`rx.clock`,与 `tx.clock` 同一个 `ClockConfig`;默认理想,全零):
两个内核把它逐符号加到环路相位上再采样,CDR 追踪的是两只时钟之差;统计引擎把两份剖面
经同一条 `|1−H|` 相加功率。`dcd_ui` 对采样时钟无意义,忽略。

```yaml
rx:
  clock:
    kind: profile
    file: data/clock_profiles/bench_dadalt03_cppll_311m_2p488g.yaml
    # rj_ui: 0.003     # 或只给白噪采样抖动
```

**不经文件的活桥**(桌面,需 `pip install "halo-serdes[pll]"`,手机构建不含):

```python
from halo_serdes.io.pll_bridge import profile_from_preset, profile_from_analysis
prof = profile_from_preset("bench_wu19_spll_frac_52m_6p253g")   # 函数内 import pllsim
prof = profile_from_analysis(pll.analyze(f=profile_grid(pll.cfg.fout)))  # 或直接给 AnalysisResult
prof.save("my_pll.yaml")                                          # 之后照常走 file
```

**逐级分解**(需要重复码型,≥4 个周期,如 `prbs7`):

```python
res = run_time_link(cfg, channel=ch, collect_jitter=True)
from halo_serdes.analysis import format_jitter_budget
print(format_jitter_budget(res.extras["jitter_budget"], cfg.ui))
# 逐级(tx / chnl / ctle)给出 ISI / DCD / Pj / Rj 与 TJ@1e-12
```

**抖动容限 JTOL**(二分搜索每个频率下可容忍的 SJ 幅度):

```python
from halo_serdes.analysis import jitter_tolerance, jtol_mask
import numpy as np
freqs = np.logspace(6, 8.5, 12)
j = jitter_tolerance(cfg, freqs, ber_threshold=1e-3, channel=ch)
print(j.summary())              # 低于 CDR 环带宽平坦,高于则 ~20dB/dec 滚降
mask = jtol_mask(freqs)         # 通用合规模板
```

---

## 10. MLSD 与 1+D 预编码

两者都是**配置开关**,引擎端到端处理:

```yaml
rx:
  mlsd:
    kind: viterbi      # none | sliding | viterbi
    memory: 2          # 网格里的残余后光标数(状态数 = n_levels^memory)
    seq_len: 4         # sliding 的平方误差窗
precode: true          # 1/(1+D) mod-N 预编码
```

```python
res = run_time_link(cfg, channel=ch)
print(res.ser, res.extras["ser_slicer"])      # MLSD 后 vs 原始判决器基线
print(res.extras["mlsd_resid"])               # MLSD 工作的残余光标
```

**什么时候有用**:MLSD 只在**有残余 ISI** 时有收益。若 FFE/DFE 已经把眼睁开
(残余 ~1e-3),它无事可做甚至略添噪。它的价值是"少花均衡硬件、用序列检测换回来"。

**预编码的取舍**:截断 DFE 错误突发,代价是孤立符号错误**精确翻倍**
(1+D 解码把一个错误摊到相邻两个符号)。突发主导时才划算。

统计引擎按匹配滤波器界折算 MLSD 增益,与时域实测在 2× 判据内一致。
渐近增益闭式解:

```python
from halo_serdes.dsp.mlsd import mlse_gain_over_dfe_db
mlse_gain_over_dfe_db([1.0, 1.0])      # 3.01 dB (1+D duobinary)
```

---

## 11. FEC 投影

```python
from halo_serdes.fec import pre_to_post_fec_ber, concatenated_post_fec_ber

pre_to_post_fec_ber(2.4e-4, "kp4")            # KP4 (544,514,t=15)
pre_to_post_fec_ber(1e-5, "kr4")              # KR4 (528,514,t=7)
concatenated_post_fec_ber(1e-3, 255, 5)       # 级联内码 + KP4 外码
```

真实编解码(需要 `galois`,`pip install -e ".[fec]"`):

```python
from halo_serdes.fec import rs_kp4
rs = rs_kp4(); cw = rs.encode(msg); dec, n_err = rs.decode(cw_with_errors)
```

投影公式只需 scipy;只有真实编解码才需要 galois。

---

## 12. IBIS-AMI 模型

三种后端,同一个 `AmiModel` 接口(Init / GetWave 双流):

```python
from halo_serdes.io import load_ami_model, build_reference_ami

# (a) 原生 FIR 参考模型 —— 零依赖
m = load_ami_model(taps=[-0.1, 0.8, -0.1], n_pre=1, sample_spaced=False)

# (b) 真实编译模型经 IBIS-AMI C ABI 执行 —— 需要 C 编译器
so, ami = build_reference_ami()                       # 现编译随仓库的参考 .c
m = load_ami_model(so_file=so, taps=[-0.1, 0.8, -0.1], n_pre=1)

# (c) 厂商模型 —— 需要 pyibisami
m = load_ami_model(ami_file="vendor.ami", dll_file="vendor.so")

res = run_time_link(cfg, channel=ch, tx_ami=m)        # 或 rx_ami=
```

**注意 `has_getwave`**:`True` 时引擎走 GetWave(时域块)流,`False` 时走
Init(LTI 冲激变换)流 —— 两者结果不同是正常的,不是 bug。
三个模型(`NativeFirAmi`、`AmiCModel`、`IbisAmiModel`)和 `load_ami_model` 都默认 `True`(厂商模型的
`GetWave_Exists` 为假时仍走 Init);对比两个模型时把它们放在同一条流上,改一个就两个一起改
(`has_getwave=False`)。2026-10-05 前 `AmiCModel` 默认 `False`,不显式指定的旧调用现在走 GetWave。

---

## 13. 定点与 RTL 出口

```yaml
numeric:
  mode: fixed
  adc_code:   {wl: 8,  fl: 0}
  ffe_weight: {wl: 10, fl: 8}    # DragonPHY2 流片位宽
  dfe_weight: {wl: 10, fl: 8}
```

定点数据通路重放 + 导出 RTL 比对向量:

```python
from halo_serdes.dsp.fixed_datapath import (
    run_fixed_datapath, dump_vectors, dump_sv_package,
)

# codes 是 ADC 码(整数),权重/电平是浮点;n_pre 为 FFE 前光标数
art = run_fixed_datapath(codes_int, w_ffe_float, w_dfe_float, levels_float,
                         n_pre=cfg.rx.ffe.n_pre, numeric=cfg.numeric,
                         code_fullscale=cfg.rx.adc.fullscale,
                         code_bits=cfg.rx.adc.n_bits)
dump_vectors("vectors/", art)                # codes/权重/判决/输出 的 txt
dump_sv_package("vectors/dims.svh", art)     # 维度与移位量,RTL 与 Python 单源
```

一个可直接运行的完整例子见 `rtl/gen_vectors.py`。

与 SystemVerilog 实现逐位比对:

```bash
sudo apt-get install -y iverilog
bash rtl/run_lockstep.sh          # -> LOCKSTEP PASS  n=1985  errors=0
                                  # -> LOOP LOCKSTEP PASS  n=3000  errors=0
                                  # -> MLSD LOCKSTEP PASS  n=4000  flips=81  errors=0
```

### 带 CDR 的定点闭环(`numeric.mode: fixed`)

`numeric.mode: fixed` 只对 `rx.arch: adc_dsp` 生效。引擎先照常跑浮点,然后从同一个起点、同样的初始(量化)权重和
训练日程,把整个数字后端用 int64 再跑一遍闭环:FFE、DFE、slicer、数据辅助训练、**整数 LMS**、MM 鉴相、环路滤波、
相位寄存器,相位插值码决定采样点(`dsp/fixed_loop.py`)。返回的 BER / SNR / 判决都是定点环路的,
`extras["fixed"]` 里有环路参数(`loop`)和逐符号记录(`record`:ADC 字、PI 码、slicer 值、判决)。

```yaml
numeric:
  mode: fixed
  pi_bits: 7              # 相位插值器每 UI 2^7 档
  phase_frac_bits: 24     # 相位寄存器在 PI 码以下的位数
rx:
  cdr:
    kp_shift: 7           # 环路增益 = 2^-kp_shift × (PD 的 LSB 换算到 UI 的移位)
    ki_shift: 15
```

- 输入字是 `2c + 1`(c 为 ADC 码):中点量化器的电平 (c + ½)·q_step,不丢半个 LSB;
- 增益全是移位:`kp_tot = kp_shift + round(log2(L / x_lsb))`,与浮点环路差在 √2 以内;
  `float_equivalent_gains()` 给出它**精确**对应的浮点增益,字长放宽时两者收敛(`tests/test_fixed_loop.py`);
- `rx.mlsd.kind: sliding` 在定点下跑整数版(`dsp/fixed_mlsd.py`):逐电平反馈表、残差平方右移
  `sq_shift` 后求和并饱和到 `numeric.mlsd_metric_bits`(默认 24)、margin 换算到度量 LSB;
  字长放宽时与浮点检测器判决一致。`viterbi` 在定点下仍是浮点(跑在定点 slicer 值上);
- 整数 LMS:权重累加器比权重细 `numeric.lms_guard_bits`(默认 24)位,权重取其高位;步长是最接近
  `ffe.mu` / `dfe.mu` 的 2 的幂(换算到整数单位后),`float_equivalent_mu()` 给出精确对应的浮点步长。
  保护位太少时小更新被舍入成 0,环路就不再自适应;
- PR 目标(`pr.target`)在定点下同样是整数:1 + aD [+ bD²] 减受控光标再切(a、b 是 `dfe_weight.fl` 小数位的字,
  `pr.adapt: lms` 时整数 LMS 跟踪),预编码 1 + D 走合成电平切片;sliding MLSD 的残差此时取目标的第一光标 a;
- 不建模(直接报错):mixed-signal(DFE / CDR 是模拟的)、`sim.stream`、非 2 的幂 lane 数。

RTL 对照的是"ADC 字之后"的部分:`replay_digital()` 从记录的 ADC 字流重放数字后端,与闭环逐位相同;
`rtl/adc_dsp_loop.sv` 是它的独立 SV 实现,`run_lockstep.sh` 的第二段逐位比对 PI 码、slicer 值与判决。

---

## 14. 性能与内存

| 场景 | 实测 |
|---|---|
| 10⁶ 符号 @106.25 GBd,OSR16,ADC 架构 | ~8 s |
| 10⁶ 符号 @106.25 GBd,OSR32,ADC 架构 | ~12 s,峰值内存 ~0.9 GB |

- **numba 是性能层不是正确性层**:`HALO_NO_JIT=1` 走纯 Python 内核,结果一致但慢
  (CI 两条路径都跑)。首次调用有 JIT 编译开销,基准测试记得先热身。
- **内存**:默认引擎的波形全量驻留,随 `n_symbols × osr` 线性增长。长跑开 **`sim.stream=True`**:
  波形按块生成、接收机读滑动窗口,内存只剩每符号几十字节的记账数组(判决、相位、参考……)。

  | 10⁶ 符号,OSR32(峰值 RSS,含 ~180 MB 解释器 + numba) | 默认 | `sim.stream` |
  |---|---|---|
  | `PAM4 224G ADC (TI mismatch)` 预设 | 1.89 GB,16.1 s | 0.33 GB,4.9 s |
  | `NRZ 28G analytic (COM/xtalk)` 预设 | 1.89 GB,13.6 s | 0.31 GB,4.7 s |

  5×10⁶ 符号 NRZ @OSR16 流式:13 s,tracemalloc 峰值 0.70 GB(一条全长波形就要 0.64 GB)。
  (上表的默认引擎峰值高于早先记的"0.9 GB":那是另一配置下的 tracemalloc / RSS 口径,本表四行同机同口径。)

  流式的代价:默认引擎的接收噪声限带(整段 FFT 砖墙)与发端单极点 `tx.bw`(整段 rFFT)是循环算子,每个输出样本
  依赖全部输入,没法分块。流式里换成等价的 FIR —— 噪声用**同一批白噪抽样**过 Kaiser 窗 sinc(居中、单位能量),
  单极点用统计引擎本来就用的 `TxPipeline.driver_response` —— 其余随机抽取(比较器失调、Rx 时钟、ADC 失配与噪声)
  一个不差。所以流式与默认是**同一个链路实例**,结果统计一致(SNR 差 < 0.1 dB、误码数在 Poisson 内,
  `tests/test_stream.py`),不逐位;流式内部,任意 `chunk_symbols` 逐位一致。
  暂不支持(会直接报错,不静默退回):IBIS-AMI、光拓扑、串扰注入、`collect_jitter`;`collect_eye` 支持(取波形开头)。
- 扫描优先用 `run_statistical`,它不生成波形。

---

## 15. 常见问题

**BER 是 0 / SER 是 0** —— 符号数不够。0 错误只能给出置信上限
(`res.ber` 会体现);要测 1e-6 量级至少要 ~10⁷ 符号,或改用统计引擎外推。

**`mixed_signal arch ... exceeds the envelope` 告警** —— 超出经标定的产品级包络
(见 README「架构包络约定」)。可以继续跑(探索用),但结论不在支持范围内;
高速率请用 `rx.arch: adc_dsp`。

**自适应训飞 / 抽头发散** —— PAM4 高速下初始眼全闭,必须先 CDR 锁定再开自适应。
引擎已内置分阶段启动(`sim.cdr_settle` → `sim.train_symbols` → 判决导向),
训飞时先加大 `cdr_settle`、减小 `mu`。

**统计引擎与时域差得远(>2×)** —— 检查是否引入了统计引擎不建模的非 LTI 环节
(TI 失配、MLSD、CDR 残余抖动)。这些在统计引擎里是**近似**,文档在
`engine/statistical.py` 顶部逐条列出。

**Touchstone 读不进来** —— `channel.kind` 要设 `touchstone` 且 `channel.file` 非空;
8/12 端口文件用 `channel.lane` 选 lane,端口错序交给 `renumber: true`。

**`galois` / `skrf` / `pyibisami` 缺失** —— 都是可选依赖,缺了只影响对应功能
(真实 FEC 编解码 / Touchstone / 厂商 AMI 模型),核心链路不受影响。

---

## 16. 光互联:LPO / CPO 作为同一条链

`LinkConfig.topology` 把信道换成 **电段 A → E/O → 光纤 → O/E → 电段 B**;留 `None` 就是原来的电链路
(逐字节不变)。LPO、CPO、retimed 模块在阶段 1 只差两个电段的损耗,光路三个块共用。

```yaml
topology:
  seg_a: {kind: analytic, length_m: 0.08, rdc: 5.0, r_skin: 2.0e-3, loss_tangent: 0.012}
  optical:
    kind: vcsel_mmf          # none | vcsel_mmf | eml_smf
    f_r_hz: 22.0e9           # VCSEL 弛豫振荡频率;eml_smf 时是单极点 3 dB 带宽
    damping_hz: 30.0e9       # VCSEL 阻尼(γ/2π)
    er_db: 4.0               # 消光比 → 与 oma_dbm 一起唯一决定四个电平的光功率
    oma_dbm: -3.0            # OMA_outer(802.3db SR1 下限 −3 dBm)
    rin_db_hz: -140.0
    length_m: 100.0
    modal_bw_mhz_km: 4700.0  # OM4 EMB(vcsel_mmf 必填);EMB/L 是 −3 dBo 点(|H| = 0.5,电 −6 dB)
    # dispersion_ps_nm_km: -1.9   # eml_smf 必填;H = cos θ − α sin θ
    # chirp_alpha: 0.0
    responsivity_a_w: 0.7
    tia_bw_hz: 40.0e9        # PD+TIA 二阶 Butterworth
    tia_noise_pa_sqrthz: 12.0
    tz_ohm: 2000.0           # 只用于报告物理输出摆幅,仿真保持电尺度
  seg_b: {kind: analytic, length_m: 0.08, rdc: 5.0, r_skin: 2.0e-3, loss_tangent: 0.012}
```

```python
cm = ChannelModel.from_config(cfg)      # seg_a × eo × fiber × oe × seg_b,带 cm.optical
cm.loss_at(cfg.f_nyquist)               # 相对"理想级联"的插损(两段理想电信道 = 0 dB,H = 0.25)
cm.optical.noise.sigma_per_level()      # 四个电平各自的 PD 节点噪声 σ(上电平最吵)
t = run_time_link(cfg, channel=cm)      # 两段卷积,光电二极管节点注入随功率变化的噪声
s = run_statistical(cfg, channel=cm, ffe_taps=t.ffe_taps, ffe_pre=cfg.rx.ffe.n_pre)
s.extras["level_sigma"]                 # 统计引擎实际用的每电平判决 σ
```

**尺度约定**:电波形不按光功率重标;`oma_dbm`/`er_db` 只决定噪声。到达光电二极管的直流外电平间距
对应光电流 R·OMA。噪声注入在 PD 电流节点(O/E 之前),所以 TIA 带宽决定噪声带宽,与 `osr` 无关。

**两引擎**:统计引擎每个发送电平一个高斯核,σ 取判决样本沿接收滤波器记忆的光功率二阶矩;
铁律 3 在 ER 3 / 4.5 / 6 dB 三点 2× 内(ADC PAM4 0.88 / 0.71 / 0.63,MS NRZ 0.92 / 0.84 / 0.86,
`tests/test_optical.py`)。剩余差来自每电平噪声其实是高斯尺度混合(σ 随 ISI 图样摆动),见
`cairn/光互联建模.md`。

**重定时(阶段 2)**:`topology.retimer: both` 在模块入口和出口各放一个重定时器,链路变成三段串联
(host TX → 段 A → 重定时 RX;重定时 TX → 光路 → 重定时 RX;重定时 TX → 段 B → host RX),
每段各自判决、下一段把上一段的判决当符号源重发(`symbols=` 入参,经 `tx/builder.py` 回到波形域),
端到端 BER 用 host 的判决对 host 自己的符号流打分;FEC 仍是端到端,模块里不终结。

```yaml
topology:
  retimer: both            # none | both
  retimer_rx: {arch: adc_dsp, ctle: {peak_db: 3.0}, adc: {n_bits: 10, fullscale: 0.6}, ffe: {n_pre: 4, n_post: 12, adapt: lms, mu: 3.0e-5}}
  retimer_tx: {swing: 1.0}
```

```python
from halo_serdes.engine.cascade import run_cascade, run_cascade_statistical
r = run_cascade(cfg, statistical=True)   # 时域三段串联 + 每段统计 BER
r.ber.ber, r.ber_product, r.stat_ber     # 端到端实测 / 1 − ∏(1 − p_i) / 统计引擎同式
[s.result.ber.ber for s in r.segments]   # 每段
run_cascade_statistical(cfg).stat_ber    # 只用统计引擎(ADC 收端用 MMSE 起始 FFE),扫描用
run_time_link(cfg, symbols=my_symbols)   # 任何引擎都能接外部符号流(整数索引,不接波形)
```

注意全刻度:未重定时时 host RX 看到整条链(增益 0.25),重定时后每个 RX 只看一段电线(0.5)或光路(0.25),
`adc.fullscale` 要跟着设(预置 `pam4_100g_lpo_vcsel.yaml` 里 host 0.3、重定时器 0.6)。

**大信号曲线与 TDECQ(阶段 3)**:`topology.optical.li_compression`(0 = 线性)给 E/O 一条静态大信号曲线 ——
VCSEL 是二阶 L-I 带热翻转(压缩顶电平),EML 是指数型 EAM 吸收曲线(压缩底电平);两端外电平钉住,OMA / ER 不变,
内电平移动,`tx.rlm` 成为"驱动器设定",光域 R_LM 由曲线导出。曲线作用在 E/O 小信号输出上(Wiener 顺序),
时域引擎按样本过曲线;接收机按曲线后的电平切片(ADC DSP 自适应参考电平的行为),统计引擎用同样的电平但链路
仍线性,并发 warning —— 曲线开启时不在铁律 3 的 2× 保证内。

```python
from halo_serdes.optical import optical_rlm, static_curve
optical_rlm(cfg.topology.optical)                 # 曲线导出的 802.3 R_LM(120D.3.1.2)

from halo_serdes.engine.optical_stage import transmitter_power
from halo_serdes.analysis.tdecq import tdecq
P, line = transmitter_power(cfg, include_seg_a=False, through_fibre=True)   # TP2 光功率 [W],含激光 RIN
r = tdecq(P, cfg.dt, cfg.symbol_rate, line)                                 # 100G/λ:5 抽头
r = tdecq(P, cfg.dt, cfg.symbol_rate, line, n_taps=15, pre_options=(1, 2, 3),
          f_ref_hz=53.125e9)                                                # 200G/λ(802.3dj,DFE 未建)
r.tdecq_db, r.oma_outer_w, r.er_db, r.rlm, r.ceq, r.taps
```

TDECQ 按 802.3 121.8.5:0.5×baud 四阶 Bessel-Thomson 参考接收机、抽头和为 1 的 T 间隔 FFE、0.45 / 0.55 UI
两个 0.04 UI 宽直方图、阈值 P_ave 与 ±OMA/3、OMA 取 7 个 3 / 6 个 0 连续游程中心 2 UI、目标 SER 4.8e-4
(Q_t = 3.414)、C_eq 噪声增益。理想眼 0 dB;理想发射机过参考接收机约 0.3 dB。GUI「Optical」页与 Android 的
「TDECQ」study 给出配置点与 ER / 激光带宽两条扫描。

**不做的**:功耗、重定时器内的 FEC 终结、802.3dj TDECQ 的 1 抽头 DFE;AMI 模型与 `topology` 互斥。
示例 `examples/32_lpo_vs_cpo.py`(同一光路,电段 4/8/12/16 dB,光纤长度扫到 reach,100 m 上比
CPO 与 LPO 的 OMA 裕度)、`examples/33_three_topologies.py`(LPO / retimed / CPO 同台 + 三杠杆表);
`examples/34_tdecq.py`(VCSEL 100G/λ 与 EML 200G/λ 的 TDECQ 对 ER / 激光带宽 / L-I 压缩,对着 802.3 上限);
GUI「Optical」页与 Android 的「Optical reach」study 用统计级联画同一张 reach 图。


## 17. DSP 发端:DAC 与驱动器压缩

发端是一条流水线(`tx/pipeline.py::TxPipeline`):符号域 **电平 → [PR 占位] → FFE → DAC**,一次显式 ZOH(带时钟边沿偏移),
波形域 **驱动器压缩 → 驱动器单极点**。所有引擎与分析入口都从它取发端波形,新字段默认全关,关着时逐字节等于以前。

```yaml
tx:
  fir_taps: [-0.06, 1.0, -0.12]
  fir_n_pre: 1
  dac_bits: 7              # 空 = 理想发端;每 UI 一个码,中升量化
  dac_fs: null             # 满量程峰峰值 [V];空 = swing × Σ|taps|(FFE 的峰值,不削峰)
  dac_thermo_msbs: 3       # 温度计解码的高位,其余二进制
  dac_unit_sigma: 0.02     # 单元电流失配 σ [LSB] → 静态 INL(端点修正);失配用独立随机流
  drv_nl: curve            # none | curve | tanh | cubic(Hammerstein:先压缩后 tx.bw)
  drv_compression: 0.1     # curve 的 c,与 optical.li_compression 同一定义(两端斜率比)
  # drv_p1db_v: 0.35       # tanh:单音 1 dB 压缩点的输入幅度
  # drv_oip3_v: 1.2        # cubic:输出三阶交调点幅度,y = x − 4/(3 OIP3²) x³
```

```python
from halo_serdes.analysis.tx_metrics import tx_report
r = tx_report(cfg)           # 同边沿、理想 DAC + 线性驱动器作参考
r.sndr_db, r.rlm, r.dac_clipped
```

- **削峰不静默**:`dac_fs` 小于 FFE 峰值时 `dac_clipped` 计数,TX SNDR 随之下降;`train_tx_fir` 在有 `dac_fs` 时把 Σ|taps| 限在 `dac_fs / swing`。
- **统计引擎**把 DAC 折成每 UI 白噪 σ_q² = LSB²/12 + E[INL²],经 DAC→判决器的符号响应到 RX;驱动器压缩只在时域引擎里,统计引擎发 warning。
- **COM 不含 DAC / 驱动器**:`analysis/com.py` 与 `NativeCom` 用 802.3 的参考发端。

示例 `examples/35_dsp_tx_sndr.py`:TX SNDR 对 DAC 位数 × 驱动器压缩(叠 6.02N + 1.76),以及示例 18 的信道扫描上
DAC 6 / 7 / 8 / 理想与 7 bit + 压缩的 reach。

---

## 18. 接收端部分响应(PR)整形

ADC 收端的 FFE 默认把脉冲均衡成 delta(只留主光标)。`pr.target = [1.0, a]` 让它均衡成 1 + aD:
第一个后光标保留为 a·主光标,由序列检测器(Viterbi)解掉,FFE 少反演信道、少放大噪声。

```yaml
rx:
  arch: adc_dsp            # 只有 ADC 收端能整形;mixed_signal + pr 在构造时就报错
  mlsd: {kind: viterbi, memory: 2}
pr:
  target: [1.0, 0.75]      # (1.0,) = delta(默认,逐位等于不设);1 + aD:a ∈ [0, 1];
                           # 1 + aD + bD²:[1.0, a, b],a ∈ [0, 2]、b ∈ [-1, 1];EPR4 即 (1, 2, 1)
  at: rx                   # rx:收端 FFE 整形;tx:发端在 FFE 与 DAC 之前整形(见下)
  adapt: none              # none:用上面的 a;mmse:按起始脉冲解 MMSE 最优 a(三光标时 a、b 一起解;主光标固定为 1);
                           # lms:从 mmse 的目标起,与 FFE 一起 LMS 跟踪 a(三光标时 a、b 都跟)(只限收端)
  mu: 2.0e-4               # adapt: lms 的步长(按平均符号功率归一,无量纲)
precode: false             # a = 1 时可配 1/(1+D):逐符号判决切 2N−1 个合成电平再 mod N,不传播
```

- **一定要配序列检测器**:`mlsd.kind: none` 时只有逐符号判决减 a × 上一判决(相当于理想 DFE 抽头),
  省下的噪声又还回去了;引擎发 warning。
- **接线**:LMS 期望值 = levels[x_s] + a·levels[x_{s−1}];DFE 从受控光标之后起;MM-CDR 读"样本 − a × 上一判决",
  锁定点与 delta 目标同(示例 18 信道上差 ≤ 0.0011 UI)。静态引擎与定点数据通路不支持 PR,直接拒绝。
- **统计引擎**:受控光标移出 ISI PDF;有序列检测器时 BER 是对交替误差事件的 union bound,噪声相关性取自 FFE 抽头
  (只看最小距离会乐观 2.5–20 倍),相邻事件的重叠扣掉(简单求和会重复计)。收端 / 发端 PR 与时域在 1.0–1.8×(BER 7e-5…6e-3)。
  逐符号判决(减 a × 上一判决,错判会传给下一个)按误差马尔可夫链算,报在 `st.extras["ser_slicer"]`(对照时域同名键);
  `mlsd.kind: none` 时它就是 BER。与时域 0.8–1.6×(按理想抽头算曾乐观 2.7–4.1×);它对 LMS / CDR 的影响不建。
- **预编码与 Viterbi**:a = 1 + 预编码时 Viterbi 的一个错误解码后变两个,BER 比不预编码略高;预编码的价值在给 LMS / CDR
  的逐符号判决不传播。

示例 `examples/36_pr_rx_alpha.py`:a ∈ {0, 0.25, 0.5, 0.75, 1} × 三个损耗,以及 reach 扫描(对照 a = 0 + Viterbi)。

**发端 PR(`at: tx`)**:`TxPipeline.pr_filter` 在电平之后、FFE 与 DAC 之前做 (x_k + a·x_{k−1}) / (1 + a)。除以 1 + a 让合成信号的
峰值等于不整形时的峰值 —— DAC 与驱动器的满量程不变,多出来的电平从电平间距里出。收端用同一个 1 + aD 目标检测
(FFE 起始解、LMS、Viterbi 光标与收端 PR 相同);收端的主光标在不含 PR 的脉冲上定位(a = 1 时整形脉冲有两个等高光标)。

线性链路、噪声在收端时,发端整形不改变收端 FFE 要反演的东西(信道,一直到 delta),所以拿不到收端 PR 省下的噪声放大;
峰值受限时还要再付最多 20·log10(1 + a)。示例 `examples/37_pr_tx_vs_rx.py` 三方同台(无 PR / 收端 / 发端):发端 PR 的 reach ≈ 无 PR − 20·log10(1 + a),收端 PR 多 4.7 dB。

