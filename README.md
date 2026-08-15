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
# 1) 跑一条完整链路(PAM4 224G,ADC-based 接收机)
python examples/01_nrz_link.py                      # NRZ 最小链路
python examples/05_adc_link.py                      # ADC-DSP 架构 + 逐 lane 诊断

# 2) 信道分析(Bode / 冲激 / 脉冲响应 + ISI 光标)
python examples/00_channel.py                       # 802.3ck Whisper 背板 (DC-40 GHz)
python examples/00_channel.py path/to/channel.s4p   # 换成自己的 s4p

pytest -q                                           # 全部单元测试
```

三行 Python 即可跑通一条链路:

```python
from halo_serdes.config.loader import load_config
from halo_serdes.engine import run_time_link

cfg = load_config("configs/pam4_224g_adc.yaml")
print(run_time_link(cfg).summary())     # BER / SER / slicer SNR
```

完整用法(选引擎、扫参数、加串扰、接 AMI/COM、定点)见
[`docs/USAGE.md`](docs/USAGE.md)。

## 图形界面 GUI

一个专业的 Plotly Dash 工作台,把 31 个示例脚本的全部分析能力变成交互式操作
(单次链路、双引擎交叉校验、眼图/浴盆、CTLE、自适应/CDR 动态、ADC 逐 lane、抖动预算、
reach 扫描、FEC、串扰、AMI/COM、定点),共 16 个能力标签页。界面不新增任何仿真逻辑,
只驱动现有引擎并渲染结果。详见 [`docs/GUI.md`](docs/GUI.md);
16 个标签页的图文导览见 [`docs/GUI_tour.md`](docs/GUI_tour.md)。

```bash
pip install -e ".[gui,jit,fec]"
halo-serdes-gui                                     # 或 python -m halo_serdes_gui
# 浏览器打开 http://127.0.0.1:8050/
```

## 能力总览

| 层 | 模块 | 能力 |
|---|---|---|
| 配置 | `config/` | YAML → frozen dataclass 参数单源;严格键校验、dotted-path 覆盖、字段级合法性校验 |
| 信号 | `core/` | `Waveform`/`SymbolStream`/`ResponseSet`(h/s/p/H 四响应);PRBS7-31、PRBS13Q/31Q、PRQS10 + 自同步检错;定点 QFormat |
| 信道 | `channel/` | Touchstone(1/2/4/8/12 端口)导入、混模转换、保守外推、广义端接、解析 RLGC;FEXT/NEXT 串扰与多 lane 侵略者组 + ICN |
| 发端 | `tx/` | FIR 预加重、驱动器带宽、RJ/SJ/DCD 抖动注入(Farrow 边沿) |
| 前端 | `afe/` | CTLE、VGA、求和节点有限带宽;时间交织 ADC(offset/gain/skew 失配、ENOB) |
| 均衡 | `dsp/` | ZF/MMSE FFE、自适应 DFE(LMS/sign-sign)、Viterbi MLSE 与 sliding-detector MLSD、定点数据通路 |
| 时钟 | `cdr/` | bang-bang 与 Mueller-Müller CDR(二阶环、环路延迟、相位钳位) |
| 引擎 | `engine/` | 时域 MC、StatEye 统计(PDF 卷积外推 1e-15)、静态快评、Tx-FIR 背channel 训练 |
| 分析 | `analysis/` | 眼图/浴盆、三层抖动分解、JTOL、IEEE 802.3 COM(93A/178A)、波形重建 |
| 编码 | `fec/` | RS-KP4/KR4 + pre/post-FEC 换算、级联内码模型 |
| 接口 | `io/` | IBIS-AMI(真实 C ABI 执行编译模型 / pyibisami 厂商模型)、COM 适配器 |
| 界面 | `halo_serdes_gui/` | 16 标签页 Plotly Dash 工作台 + Windows 桌面打包 |
| 硬件 | `rtl/` | FFE+DFE+slicer 的 SystemVerilog 独立实现,与 Python 黄金模型 bit-exact lockstep |

**规模**:核心库 ~5.3k 行 / GUI ~2.6k 行 / 测试 ~2.9k 行 / 示例 ~3.4k 行;
**283 项测试**(闭式解、黄金数据、数值等价、双引擎交叉校验、RTL lockstep)、
**31 个编号示例**(`examples/00`–`30`)。

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
| G | 16 标签页 Plotly Dash GUI + Windows 桌面打包(PyInstaller / Nuitka) | ✅ |
| + | 抖动预算、IBIS-AMI/COM 接缝、时域 FEXT/NEXT 串扰 | ✅ |
| ++ | 忠实 IEEE 802.3 COM(93A/178A)、多 lane 串扰 + ICN、MLSD/1+D 预编码接入引擎、<br>IBIS-AMI 真实 C ABI 执行、JTOL、RTL bit-exact lockstep、CI(测试/lint/lockstep) | ✅ |

六个阶段之后又完成三项增量(抖动预算、IBIS-AMI/COM、时域串扰)、16 标签页 GUI,
以及第二梯队:忠实 802.3 COM、多 lane 串扰 + MLSD/预编码接线、IBIS-AMI 真执行、
JTOL、RTL lockstep、Windows 打包。

关键达标指标:
- 统计引擎 vs 蒙特卡洛交叉校验比值 **1.03×**(要求 <2×);
- 10⁶ 符号 @106.25 GBd 时域全链路 **OSR16 8.1 s / OSR32 11.6 s**(要求 ≤2 min;
  本机实测,峰值内存约 0.9 GB);
- 双架构对比复现业界结论:mixed-signal 在 ~-15 dB Nyquist 损耗后崩溃,
  ADC/DSP 架构到 -25 dB 仍保持 ~1e-5 SER;
- 定点数据通路 ≥7 bit 权重与浮点 bit-true 一致,DragonPHY2 流片位宽(10b)
  为默认 Q 格式;`rtl/` 的 SystemVerilog 实现与黄金模型逐位一致(`bash rtl/run_lockstep.sh`)。

信道数据:`data/channels/` 内含 TEC Whisper 42.8"(802.3ck COM 参考,DC-40 GHz)与
IEEE 802.3 peters_01_0605 系列(≤15 GHz,适用于 ≤16G 速率)。

## 文档索引

| 文档 | 内容 |
|---|---|
| [`docs/USAGE.md`](docs/USAGE.md) | **使用指南**:按任务组织(跑链路、扫参数、串扰、COM、AMI、定点、导出) |
| [`docs/GUI.md`](docs/GUI.md) | GUI 安装/启动/各标签页用法、桌面版打包 |
| [`docs/GUI_tour.md`](docs/GUI_tour.md) | GUI 图文导览(实拍截图) |
| [`docs/SUMMARY.md`](docs/SUMMARY.md) | 工程总结:三次架构探索与结论 |
| [`docs/COMPARISON.md`](docs/COMPARISON.md) | 与 serdespy / PyBERT / DragonPHY2 的能力对比 |
| [`docs/serdes_opensource_repos_analysis.md`](docs/serdes_opensource_repos_analysis.md) | 三个参考库的深度调研(设计蓝本) |
| [`rtl/README.md`](rtl/README.md) | 黄金模型 → RTL 的 bit-exact lockstep |
| [`packaging/README.md`](packaging/README.md) | Windows 桌面 exe 打包(PyInstaller / Nuitka) |

## 架构包络约定

**产品级 mixed-signal 包络**(CTLE + DFE(tap-1 unrolled)+ BB-CDR、
无 RX FFE),三层结构,由 Whisper 参考背板上的极限扫描标定
(`examples/12`,闭眼损耗 NRZ ~-30 dB / PAM4 ~-20.5 dB,差值即
PAM4 的 9.5 dB 电平代价):

| 层级 | NRZ | PAM4 | 引擎行为 |
|---|---|---|---|
| 默认锚点 | 16 Gb/s(`LinkConfig()`,`nrz_16g_ms.yaml`) | 32 Gb/s(`pam4_32g_ms.yaml`) | 静默 |
| 舒适区上限 | ≤ 24 Gb/s | ≤ 32 Gb/s | 静默(参考信道上眼高 ≥ ~25% 电平间距) |
| 绝对极限 | ≤ 30 Gb/s | ≤ 36 Gb/s | **marginal 告警**(数 mV 眼,FEC 工况,逐信道验证) |
| 硬顶 | 30 GBd 符号率(不分调制) | 同左 | **exceeds 告警**(未建模的电路墙:CTLE 增益带宽/判决器孔径/时钟路径) |

超过绝对极限或硬顶 → `exceeds` 告警并建议 `rx.arch: adc_dsp`
(TI-ADC + 数字 FFE/DFE/MLSD + MM-CDR);纯信道可行性评估用
`run_static_link`(其 FFE 为链路总线性均衡预算,非 RX 电路)。
