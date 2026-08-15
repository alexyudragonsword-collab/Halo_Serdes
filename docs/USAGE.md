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
```

---

## 9. 抖动:注入、分解、容限

**注入**(Tx 侧,`TxConfig`):

```yaml
tx:
  rj_ui: 0.005       # 随机抖动 sigma [UI]
  sj_ui: 0.05        # 正弦抖动幅度 [UI]
  sj_freq: 10.0e6    # 正弦抖动频率 [Hz]
  dcd_ui: 0.01       # 占空比失真 [UI]
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
`NativeFirAmi` 默认 `True`,`AmiCModel` 默认 `False`,按需显式指定。

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
```

---

## 14. 性能与内存

| 场景 | 实测 |
|---|---|
| 10⁶ 符号 @106.25 GBd,OSR16,ADC 架构 | ~8 s |
| 10⁶ 符号 @106.25 GBd,OSR32,ADC 架构 | ~12 s,峰值内存 ~0.9 GB |

- **numba 是性能层不是正确性层**:`HALO_NO_JIT=1` 走纯 Python 内核,结果一致但慢
  (CI 两条路径都跑)。首次调用有 JIT 编译开销,基准测试记得先热身。
- **内存**随 `n_symbols × osr` 线性增长(波形全量驻留)。10⁶ 符号 OSR32 约 0.9 GB;
  要跑更长或更高 OSR 就降 `osr`(16 通常足够)或分批跑。
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
