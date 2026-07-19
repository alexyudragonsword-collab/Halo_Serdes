# Halo_Serdes

系统行为级高速 SerDes 仿真框架:NRZ(≤32 Gb/s)与 PAM4(≤224 Gb/s,106.25/112 GBd),
支持传统 mixed-signal 与 ADC-based 两种接收机架构的建模与对比。

设计蓝本来自对三个开源项目的深度调研(见 `docs/serdes_opensource_repos_analysis.md`):
serdespy(算法骨架)、PyBERT(S 参数/抖动/行为模型工程实践)、Stanford DragonPHY2
(ADC-DSP 架构硬件真值与验证方法学)。

## 安装

```bash
pip install -e .[jit,test]
```

## 快速开始

```bash
# Phase 0: 信道分析(Bode / 冲激 / 脉冲响应 + ISI 光标)
python examples/00_channel.py                       # 802.3ck Whisper 背板 (DC-40 GHz)
python examples/00_channel.py path/to/channel.s4p
pytest tests/ -q                                    # 全部单元测试
```

## 当前状态(Phase 0 完成)

- `config/`:YAML → frozen dataclass 的参数单源配置(严格键校验、dotted-path 覆盖、schema 版本迁移);
- `core/`:`Waveform`/`SymbolStream`/`ResponseSet`(h/s/p/H 四响应)双域信号表示;
  PRBS7-31、PRBS13Q/31Q、PRQS10 生成与自同步误码检测(numba 加速,`HALO_NO_JIT=1` 回退);
- `channel/`:Touchstone(1/2/4/8/12 端口)导入、混模转换(端口错序自动检测)、
  保守外推插值、广义 S 参数端接(独立 Γ 公式交叉验证)、频域补零精确控制时域步长、
  解析 RLGC/ABCD 信道;
- `tests/`:37 项闭式解/恒等式/黄金数据对照测试(含 serdespy 参考数据,期间数值确认了
  serdespy 端接参考阻抗与端口置换两处实现瑕疵,详见分析文档)。

## 路线图

| 阶段 | 内容 | 状态 |
|---|---|---|
| 0 | 骨架 + 配置 + core + 信道层 | ✅ |
| 1 | NRZ 32G 最小链路(ZF/MMSE FFE + 理想 DFE + MC BER) | ✅ |
| 2 | 时域引擎 + mixed-signal 架构(自适应 DFE + BB-CDR + 抖动注入) | ✅ |
| 3 | StatEye 统计引擎 + 双引擎交叉校验(MC 比值 1.03×) | ✅ |
| 4 | ADC-based 架构(TI-ADC + 数字 DSP + MM-CDR),10⁶ 符号@106.25GBd 7s | ✅ |
| 5 | MLSD(Viterbi/滑动检测器)+ RS-FEC(KP4/KR4)+ 抖动分解 + 双架构对比 | ✅ |
| 6 | 定点双模式(bit-true)+ RTL 黄金模型出口(lockstep 向量) | ✅ |

全部 6 个阶段完成:87 项测试(闭式解/黄金数据/数值等价/双引擎交叉校验),
8 个示例脚本(`examples/00`–`08`)。关键达标指标:
- 统计引擎 vs 蒙特卡洛交叉校验比值 **1.03×**(要求 <2×);
- 10⁶ 符号 @106.25 GBd/OSR32 时域全链路 **7 s**(要求 ≤2 min);
- 双架构对比复现业界结论:mixed-signal 在 ~-15 dB Nyquist 损耗后崩溃,
  ADC/DSP 架构到 -25 dB 仍保持 ~1e-5 SER;
- 定点数据通路 ≥7 bit 权重与浮点 bit-true 一致,DragonPHY2 流片位宽(10b)
  为默认 Q 格式;RTL lockstep 向量包由 `examples/08` 生成。

信道数据:`data/channels/` 内含 TEC Whisper 42.8"(802.3ck COM 参考,DC-40 GHz)与
IEEE 802.3 peters_01_0605 系列(≤15 GHz,适用于 ≤16G 速率)。

## 架构包络约定

- **产品级 mixed-signal 上限 = 16 GBd NRZ**(`MS_PRODUCT_MAX_BAUD`):
  CTLE + DFE(tap-1 unrolled)+ BB-CDR、无 RX FFE 的分工,已在 -18.7 dB
  信道上验证(DFE 后内眼 105 mV,BER 0)。`LinkConfig()` 默认即该锚点;
  规范配置 `configs/nrz_16g_ms.yaml`;时域引擎对超限的 mixed_signal
  运行发 UserWarning(允许探索,但结果在支持包络之外)。
- **16 GBd 以上**:走 `rx.arch: adc_dsp`(TI-ADC + 数字 FFE/DFE/MLSD +
  MM-CDR),或用 `run_static_link` 做纯链路预算评估(其 FFE 为链路总
  线性均衡预算,非 RX 电路)。
