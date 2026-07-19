# 开源 SerDes 仿真/设计仓库深度分析:serdespy · DragonPHY2 · PyBERT

> 分析日期:2026-07-19
> 分析对象(均为 GitHub 最新 main/master 浅克隆):
> - **serdespy** — `richard259/serdespy`(多伦多大学 Carusone 组,系统级 SerDes 建模 Python 库)
> - **DragonPHY2** — `StanfordVLSI/dragonphy2`(Stanford 开源 ADC-based SerDes PHY,含流片 RTL)
> - **PyBERT** — `capn-freako/PyBERT`(David Banas,串行链路 BERT 仿真器,PyPI 名 PipBERT)

---

## 0. 总览与横向对比

三个仓库分别代表了 SerDes 研发流程中三个不同层次的开源实践:

| 维度 | serdespy | DragonPHY2 | PyBERT |
|---|---|---|---|
| 定位 | 教学级系统建模库 | 流片级 ADC-based RX PHY(RTL+验证+后端) | 工程级链路 BERT 仿真器(GUI) |
| 抽象层次 | Python 行为级 | SystemVerilog RTL + Python 黄金模型 | Python 行为级 + IBIS-AMI |
| 作者/机构 | U of Toronto(Carusone 组,本科毕设) | Stanford VLSI(AHA/POSH 项目) | David Banas(SI 社区,持续 12 年) |
| 许可证 | MIT | Apache-2.0 | BSD 3-clause |
| 语言/规模 | Python,~3k 行 | SV + Python,大型工程 | Python,~9.4k 行(核心包) |
| 信道建模 | ABCD/RLGC + s4p 混模转换 | s4p→脉冲响应 + 解析信道(Python 侧) | Johnson 解析模型 + 1/2/4/8/12 端口 S 参数级联 |
| 均衡 | TX FIR、CTLE(示例)、ZF-FFE、DFE、LMS | 片上并行 FFE + 信道估计误差 + 滑动检测器(简化 MLSD),片外 Wiener/LMS 自适应 | TX FFE、CTLE、RX FFE、DFE(sign-sign LMS)、Viterbi MLSE |
| CDR | 无(启发式相位对齐) | Mueller-Müller 线性 PD + 二阶 PI 环 + PI 相位插值(RTL) | Alexander bang-bang + 二阶环(行为级) |
| BER 方法 | 蒙特卡洛 + PRBS/PRQS checker | 片上 PRBS BIST + FPGA 实时仿真 | bit-by-bit + 双 Dirac 抖动外推浴盆(至 1e-12);另有 COM |
| FEC | RS KP4/KR4(纯 Python) | 无 | 卷积码 + Viterbi 译码 |
| 抖动建模 | 仅高斯 RJ 注入 | jitter_rms 仿真参数 | 完整 Rj/Pj/ISI/DCD 分解与逐级预算 |
| 测试/CI | 无 | BuildKite 全量回归 + FreePDK45 综合冒烟 | 19 个 pytest 文件 + lint/mypy 门槛 |
| 维护状态 | Alpha,单 commit 快照(2023) | 2021 年后封存 | 活跃(v10.0.0,2026-07) |
| 对自研项目的主要价值 | 算法最小可读实现、教学材料 | 数字均衡 RTL 范式 + 验证方法学 | 信道/S 参数处理、抖动分解、AMI 集成的实战参考 |

**一句话结论**:serdespy 适合作为"算法骨架蓝本"快速理解全链路;PyBERT 是行为级链路仿真(尤其 S 参数处理、抖动分解、IBIS-AMI/COM)最有实战价值的参考实现;DragonPHY2 则是把这些算法落到可流片数字 RTL、并配套"Python 黄金模型 ↔ RTL 数值 lockstep"验证方法学的完整范例。三者恰好覆盖"算法 → 仿真器 → 硅实现"的完整链条。

---

# 第一部分:serdespy 深度分析

## 1. 项目定位与背景

**项目名称**:serdespy — "Library for system-level SerDes modelling and Simulation"(系统级 SerDes 建模与仿真 Python 库)

**作者与出处**:根据 `README.md` 与 `setup.py`,本项目是多伦多大学(University of Toronto, Engineering Science)Richard Barrie 的本科毕业设计项目(ESC499, 2021/2022 学年),导师为 Tony Chan Carusone(著名 SerDes 教材作者、Alphawave 前 CTO)与 Ming Yang,合作者 Katherine Liang。信道建模部分明确注明移植自导师的 MATLAB 代码库 `tchancarusone/Wireline-ChModel-Matlab`(见 `serdespy/chmodel.py:5`)。

**许可证**:MIT License。内嵌的 `reedsolo.py` 来自 Tomer Filiba / Stephen Larroque 的开源 reedsolo 库(MIT,文件头部保留版权声明)。

**依赖**(setup.py `install_requires`):`numpy`、`scipy`、`matplotlib`、`scikit-rf`(Touchstone/S 参数处理)、`samplerate`(重采样)。Python 3.7–3.9。版本号 1.0,已发布 PyPI(`pip install serdespy`)。

**成熟度**:开发状态自评为 **Alpha**(`Development Status :: 3 - Alpha`)。本地 git 历史仅 1 个 commit(2023-05-12,squash 后快照);从代码中大量 `#TODO` 注释、被注释掉的调试代码及个别明显 bug(见下文)判断,这是一个**教学/论文级原型**,而非生产级库。代码总量约 3000 行(其中约 1000 行是第三方 reedsolo),核心自研代码约 2000 行。

## 2. 代码结构:逐文件职责

包入口 `serdespy/__init__.py` 采用 `from .xxx import *` 全量导出 8 个模块。

### 2.1 `chmodel.py`(195 行)— 信道频域建模基础设施

移植自 Carusone 的 MATLAB 信道模型,全部以 **ABCD(级联)矩阵** 为核心表示,矩阵存储为 `(n_freq, 2, 2)` 复数数组:

| 函数 | 签名 | 职责 |
|---|---|---|
| `rlgc(r,l,g,c,d,f)` | 返回 `(f.size,2,2)` | 由单位长度 RLGC 参数生成长度 d 的传输线 ABCD 矩阵:γd = d·√((R+jωL)(G+jωC)),Z₀ = √((R+jωL)/(G+jωC)),A=D=cosh(γd),B=Z₀sinh(γd),C=sinh(γd)/Z₀(第 24–36 行) |
| `impedance(z)` / `admittance(y)` | | 串联阻抗 / 并联导纳二端口 ABCD 矩阵 |
| `shunt_cap(c,w)` / `series_cap(c,w)` | | 并联/串联电容 ABCD 矩阵(接受复频率 w) |
| `series(networks)` | | 多个二端口网络 ABCD 矩阵逐频点连乘级联(第 97–107 行) |
| `sparam(s11,s12,s21,s22,z0,f)` | | S 参数 → ABCD 矩阵标准转换(第 125–145 行) |
| `freq2impulse(H, f)` | 返回 `(h, t)` | 频响 → 冲激响应:将单边频谱做共轭对称扩展 `Hd = [H, conj(flip(H[1:-1]))]` 后取 `real(ifft(Hd))`(第 110–123 行)。要求 f 线性均匀且含 DC 点 |
| `zero_pad(H, f, t_d)` | 返回 `(H_zp, f_zp, h, t)` | 频域补零至 f_max = 1/(2·t_d),从而使 IFFT 后时间步长精确等于期望的 t_d(实现过采样时域波形,第 148–196 行) |

### 2.2 `four_port_to_diff.py`(104 行)— 4 端口 S 参数 → 差分传递函数

唯一函数 `four_port_to_diff(network, port_def, source, load, option=0, t_d=None)`:
1. 接收 skrf `Network` 对象,先做端口重排(第 54–66 行,把 1,3,2,4 排成 1,2,3,4 拓扑);
2. **单端→混合模式(mixed-mode)转换**:M = [[1,-1,0,0],[0,0,1,-1],[1,1,0,0],[0,0,1,1]],Smm = (M·S·Mᵀ)/2(第 70–76 行),取左上 2×2 块即差分-差分 S 参数 SDD;
3. 计入源/负载失配:ΓL=(Zl−Z0)/(Zl+Z0)、ΓS 同理(支持 `np.inf` 开路,第 85–89 行),输入反射 Γin = SDD11 + SDD12·SDD21·ΓL/(1−SDD22·ΓL);
4. 电压传递函数(第 93 行):`H = SDD21·(1+ΓL)(1−ΓS) / [(1−SDD22·ΓL)(1−Γin·ΓS)] / 2`
5. `option=1` 时按 DC 归一化 `H/H[0]`;`t_d` 给定时调用 `zero_pad` 得到指定时间步长的冲激响应,否则直接 `freq2impulse`。返回 `(H, f, h, t)`。

### 2.3 `signal.py`(584 行)— DSP 杂项与均衡核心

- 符号编解码:`grey_encode/grey_decode`(格雷映射 00→0,01→1,11→2,10→3)、`natural_encode/natural_decode`;
- 判决:`pam4_decision(x,l,m,h)`(三阈值切片)、`nrz_decision(x,t)`;
- 波形生成:`pam4_input_BR(data, voltage_levels=[-3,-1,1,3])`、`nrz_input_BR(data, voltage_levels=[-1,1])` 生成波特率采样电压序列;
- 采样判决:`nrz_a2d` / `pam4_a2d(signal, samples_per_symbol, ...)` 每 `samples_per_symbol` 点取一个样本切片判决;
- `channel_coefficients(pulse_response, t, samples_per_symbol, n_precursors, n_postcursors, ...)`(第 288 行):以脉冲响应峰值为主光标,间隔 1 UI 采样得到前/后光标 ISI 系数并作图;
- `forcing_ffe(n_taps_pre, channel_coefficients, target=None)`(第 369 行):**零迫(zero-forcing)FFE 求解器**;
- `shift_signal(signal, samples_per_symbol)`(第 423 行):按能量最大启发式寻找最佳采样相位(眼图中心);
- `lms_equalizer(y, mu, N, w_ffe, FFE_pre, w_dfe, voltage_levels, alpha=None, reference=None, update_rate=1)`(第 459 行):**FFE/DFE 联合 LMS 自适应**,内部用 `_quantize`(最近电平量化,第 581 行)。

### 2.4 `transmitter.py`(270 行)— TX 行为模型

`class Transmitter`:
- `__init__(self, data, voltage_levels, frequency)`:frequency 定义为 2×符号率(`self.UI = self.T/2`,第 33–35 行),按 voltage_levels 尺寸(2/4)自动选择 NRZ/PAM4 波特率波形;
- `FIR(tap_weights)`:TX FIR 去加重,`sp.signal.fftconvolve(signal_BR, tap_weights, mode="same")`(第 71 行,主光标应为最后一个元素、权重如 `[-0.1, 1]`);
- `oversample(samples_per_symbol)`:`np.repeat` 零阶保持过采样成理想方波;
- `gaussian_jitter(stdev_div_UI=0.025)`:逐符号高斯抖动——为每个符号生成 ε~N(0, σ·UI),将跳变沿前/后 `round(ε/dt)` 个样本替换为上一符号电平,实现边沿位置抖动(第 110–136 行);
- `tx_bandwidth(freq_bw=None, TF=None)`:TX 驱动器带宽限制,默认单极点低通 H(s)=ω_bw/(s+ω_bw),用 `sp.signal.freqs` 算频响、`freq2impulse` 转冲激响应后卷积(第 138–196 行)。**注意第 186 行 `h, t = sdp.freq2impulse(H,f)` 引用了未导入的 `sdp`,是一个 NameError bug**;
- `resample(samples_per_symbol)`:用 `samplerate` 库零阶保持重采样。

### 2.5 `receiver.py`(258 行)— RX 行为模型

`class Receiver`:
- `__init__(self, signal, samples_per_symbol, f_nyquist, voltage_levels, shift=True, main_cursor=1)`:保存原始信号 `signal_org`(可选 `shift_signal` 对齐眼心),`main_cursor` 用于缩放判决阈值;
- `reset()` / `noise(stdev)`:重置 / 在原始信号上叠加 AWGN;
- `slice_signal()`:降采样为波特率序列 `signal_BR`;
- `FFE(tap_weights, n_taps_pre)`(第 84 行):过采样域 FFE——把抽头权重稀疏放置到间隔 `samples_per_symbol` 的滤波器上做卷积,再按预光标数对齐截断;
- `FFE_BR(tap_weights, n_taps_pre)`(第 108 行):波特率域 FFE(直接 `fftconvolve`,`mode="same"`);
- `nrz_DFE(tap_weights)` / `pam4_DFE(tap_weights)`(第 122/189 行):过采样域 DFE——逐符号在采样点判决,更新判决历史抽头,把反馈量从当前符号后半 UI 到下一符号前半 UI 的窗口中减去(第 152、222 行),模拟 DFE 反馈在眼图上的效果;判决阈值按 `main_cursor` 缩放,如 PAM4:`l,m,h = main_cursor·(v_i+v_{i+1})/2`(第 204–206 行);
- `nrz_DFE_BR` / `pam4_DFE_BR`(第 157/227 行):波特率域 DFE,判决-反馈只作用于下一个样本,`pam4_DFE_BR` 同时记录判决符号流 `self.symbols_out` 供 BER 检测。

### 2.6 `prs.py`(371 行)— 伪随机序列与误码检测

- `prbs7/prbs13/prbs20/prbs22/prbs24/prbs31(seed)`:LFSR 软件实现,多项式与业界标准一致(如 PRBS31:x³¹+x²⁸+1,第 32 行;PRBS13:x¹³+x¹²+x²+x+1,第 166 行),生成完整周期 2ⁿ−1 序列;
- `prqs10(seed)` / `prqs12(seed)`(第 218/247 行):PRQS 四进制序列——取 PRBS20/24,循环移位 (2ⁿ−1)/3 得到第二路,两路逐位格雷编码为 PAM4 符号;
- `prbs_checker(n, prbs, data)`(第 266 行):在参考 PRBS 中滑动搜索 data 前 n 位实现**自同步**,然后逐位比对计数误码,返回 `[error_count, error_idx]`;
- `prqs_checker(n, prqs, data)`(第 320 行):同理,PAM4 符号错误若跨 2 个电平记 2 比特错(第 365–367 行,与格雷映射下相邻电平错 1 bit 一致)。

### 2.7 `eye_diagram.py`(99 行)— 眼图

- `simple_eye(signal, window_len, ntraces, tstep, title, res=600, linewidth=0.15)`:把信号切成 ntraces 段折叠绘制(matplotlib 线迹叠加式眼图,无密度/直方图);
- `rx_jitter_eye(...)`:绘图时对每条迹加高斯水平偏移,模拟 RX 采样抖动的视觉效果。

### 2.8 `rs_code.py`(125 行)+ `reedsolo.py`(987 行,第三方)— FEC

- `RS_KP4()` = `RSCodec(nsym=30, nsize=544, c_exp=10)`,即 **IEEE 802.3 KP4 = RS(544, 514, t=15) over GF(2¹⁰)**;
- `RS_KR4()` = `RSCodec(nsym=14, nsize=528, c_exp=10)`,即 **KR4 = RS(528, 514, t=7)**;
- `bin_seq2int_seq` / `int_seq2bin_seq` / `bin2int` / `int2bin`:比特流 ↔ 10-bit RS 符号打包;
- `rs_encode(bin_seq, encoder, pam4=True)` / `rs_decode(...)`:编码后可选直接格雷映射为 PAM4 符号流(第 95–106 行,用 `order='F'` 交织两比特到一个符号);
- `reedsolo.py`:纯 Python 通用错误+擦除 RS 编解码器(Berlekamp–Massey + Chien/Forney),自动分块支持任意长度消息。

## 3. 核心算法详解

### 3.1 信道建模链路

两条路径:

**(a) 解析 RLGC 路径**(problem_set/HW3.py):由损耗参数(趋肤效应系数 k_r、损耗角 θ₀、RDC)构造频变 R(f)、G(f),用 `rlgc` 生成传输线 ABCD,再用 `series` 与源/负载/寄生(`impedance/admittance/shunt_cap`)级联,最后由 ABCD 求端到端 H(f),`freq2impulse`/`zero_pad` 得 h(t)。

**(b) S 参数路径**(example_channel):Touchstone `.s4p` → skrf → `four_port_to_diff` 做混合模式转换 + 端接失配修正 → 差分 H(f) → 频域补零 IFFT 得到时间步长 = UI/64 的 h(t)。**脉冲响应**通过 `h ⊛ ones(samples_per_symbol)` 得到(1 UI 方波激励,1_channel.py:54),这是全库 ISI 分析的基础。

关键数学:`freq2impulse` 的共轭对称重建保证 h 为实序列;`zero_pad` 通过延展频轴到 1/(2t_d) 控制 IFFT 分辨率——这是"频域建模、时域仿真"衔接的核心技巧。

### 3.2 均衡

- **TX FIR(去加重)**:`Transmitter.FIR`,纯卷积,权重手工选取(如 3_ffe_dfe.py:33 用 `[-0.05, 1]` 压前光标)。
- **CTLE**:库内**没有 CTLE 类**,示例 2_ctle.py:27–32 用 scipy 直接构造两极一零传函 H(s) = k(s+z)/(s+p)²(z=2e10、p=1.7e11、k=p²/z,高频 peaking 型),`sp.signal.freqs` 求频响 → `freq2impulse` 求冲激响应 → 与信号卷积。
- **零迫 FFE**:`forcing_ffe`(signal.py:369–420)。构造 ISI 卷积矩阵 A(以信道系数为对角带的 Toeplitz 矩阵,第 399–400 行),目标向量 c 为单位脉冲(主光标位 1,其余 0),解 **b = A⁻¹c**,再归一化使主光标抽头 = 1。这就是经典 zero-forcing 方程 Σᵢ bᵢ·c_{k−i} = δ_k。
- **DFE(零迫)**:示例中直接取均衡后残余后光标系数作为 DFE 权重(3_ffe_dfe.py:68),即理想 DFE 完全消除后光标 ISI;行为模型见 `Receiver.pam4_DFE_BR`:`v_k = y_k − Σᵢ wᵢ·V(ẑ_{k−i})`,判决即时反馈。
- **LMS 自适应(FFE+DFE 联合)**:`lms_equalizer`(signal.py:459–579)。每符号:
  - FFE 输出 `v_ffe[k] = w_ffe · y[k−post : k+pre+1][::-1]`;
  - DFE 输出 `v_dfe[k] = v_ffe[k] − w_dfe · z[k−D:k][::-1]`;
  - 判决 `z[k] = quantize(v_dfe[k])`;
  - 误差 `e[k] = v_dfe[k] − y_ref`,其中 y_ref 在训练期用 `reference`(**训练序列模式**),之后用判决值(**判决导向模式**);
  - 随机梯度更新:`w_ffe −= μ·e·y_k`,`w_dfe += μ·e·z_k`(第 567–569 行,符号相反因 DFE 是减法支路);
  - 支持 `alpha` 固定前几个 DFE 抽头(联合优化时冻结已知抽头)、`update_rate` 降速更新。
- **MLSE**:README 声称支持,但**代码中完全不存在**(全库 grep 无 MLSE/Viterbi 实现)——README 与实现不符。

### 3.3 PAM4 / NRZ 支持

由 `voltage_levels` 数组长度驱动(2→NRZ,4→PAM4),贯穿 Transmitter/Receiver/判决/眼图/检错。PAM4 默认电平 [-3,-1,1,3],格雷映射;PRQS 生成保证 PAM4 图案的伪随机性;`prqs_checker` 按格雷特性折算比特错误数。HW7.py 还演示了 PAM4 **RLM(电平失配比)** 计算与光调制非线性(sin 压缩)。

### 3.4 FEC

KP4/KR4 参数与以太网标准一致;`rs_encode/rs_decode` 完成 比特→10bit 符号→RS 码字→(可选)PAM4 格雷符号 的完整管线。底层 reedsolo 是纯 Python 实现,GF(2¹⁰) 下编解码为 O(n²),6_FEC.py 专门计时展示 1 Mbit 编解码耗时(慢是已知问题)。

### 3.5 眼图与 BER

- 眼图为**线迹折叠式**(simple_eye),窗口一般取 3 UI,无统计眼/浴盆曲线/眼高眼宽自动测量;
- BER 流程:发送完整 PRQS 周期 → 信道+CTLE+噪声+均衡 → `pam4_DFE_BR` 产生 `symbols_out` → `prqs_checker` 自同步比对 → `BER = errors / (n_symbols×2)`(5_BER_test.py:82–86)。这是**暴力蒙特卡洛**方法,无半解析(如 StatEye/峰值失真分析)BER 外推,可达到的 BER 下限受序列长度(~10⁶ 符号)限制,约 1e-6 量级。

## 4. 典型工作流(以 examples/example_channel 为线索)

端到端 100G PAM4(106.24 Gb/s,奈奎斯特 26.56 GHz)铜缆链路仿真,数据来自 IEEE 802.3ck 公开信道(2 m 铜缆,28.5 dB 插损):

1. **1_channel.py**:读 `.s4p` → `four_port_to_diff`(Zs=Zl=50Ω,t_d=UI/64)→ Bode 图 + 冲激响应裁剪 → `np.save` 存档。中间产物用 .npy 文件在脚本间传递。
2. **2_ctle.py**:构造两极一零 CTLE → 保存 h_ctle → 用 PRQS10 数据对比有/无 CTLE 眼图。
3. **3_ffe_dfe.py**:脉冲响应 → `channel_coefficients` 量取 ISI → 手选 1 抽头 TX FIR(−0.05)→ `forcing_ffe` 求 3 预光标 FFE → 残余后光标作 DFE 权重 → 全链路时域仿真逐级画眼图 → 保存三组权重。
4. **4_xtalk.py**(可选):7 路 FEXT + 8 路 NEXT 的 s4p 分别求响应,各用不同种子的 PRQS 激励,叠加得到串扰波形并保存。
5. **5_BER_test.py**:完整 RX 链路——PRQS 全周期 + TX FIR + 2.5% UI 高斯抖动 + 120 GHz TX 带宽限制 + 信道 + (可选串扰) + CTLE + AWGN(σ=0.05)+ 波特率采样 + FFE_BR + pam4_DFE_BR → `prqs_checker` 报告 BER。
6. **6_FEC.py**:KP4/KR4 编解码 1 Mbit 并计时。

problem_set(配套 problem_set.pdf 的 7 次作业)覆盖:NRZ/PAM4 基础与抖动眼图(HW1)、FEC 开销与编码增益(HW2)、RLGC 解析信道建模(HW3)、CTLE + LMS 自适应均衡(HW4,库中 `lms_equalizer` 的唯一调用点)、脉冲响应均衡(HW6)、光链路非线性与 RLM(HW7)——本质是一套**线路链路教学课程**的参考答案。

## 5. 优点与局限

**优点**
- 概念清晰、教学价值高:每个 SerDes 子模块(信道/FIR/CTLE/FFE/DFE/FEC/PRBS/眼图)都有最小可读实现,公式与教材(Carusone 系)一一对应;
- 覆盖面完整:从 S 参数到 BER 到 FEC 的全链路都能跑通,且用真实 IEEE 802.3ck 公开信道;
- 零迫 FFE、LMS 联合自适应、混合模式转换等核心算法实现正确且紧凑;
- MIT 许可,依赖轻量。

**局限与具体问题**
- **无任何测试**(无 tests/ 目录)、无 CI、无 API 文档(仅 docstring,且多处 TODO);
- **README 与实现不符**:宣称的 MLSE 不存在;CTLE 只存在于示例脚本而非库 API;
- **明显 bug**:
  - `transmitter.py:186` `sdp.freq2impulse` 引用未定义名 `sdp`(调用 `tx_bandwidth` 会 NameError);
  - `transmitter.py:113` 及 `eye_diagram.py:82` `epsilon.clip(UI)` 结果未赋值,截断无效;
  - `signal.py:403` `if target == None` 对数组 target 会抛异常;`signal.py:452` 注释说"least loss"实际取 `np.max`;
  - `four_port_to_diff.py:82-89`:混模 SDD 矩阵参考阻抗为 2·z0=100Ω(差分),但反射系数 ΓS/ΓL 却用单端 z0=50Ω 计算——"100Ω 端接 100Ω 差分信道"在该实现下并非无反射,物理上等效引入虚假失配(本仓库 Phase 0 移植时经数值对照确认,见 tests/test_touchstone.py);
  - `forcing_ffe` 先按 Σ|b| 归一化又按主抽头归一化(signal.py:414–418),前一步冗余;
- **性能**:大量逐样本 Python for 循环(DFE、PRBS 生成、a2d、PRQS 编码),PRBS31 全周期(2³¹−1)在纯 Python 下不可行;reedsolo 纯 Python O(n²);无向量化/JIT;
- **建模深度**:无统计眼/半解析 BER,BER 下限受蒙特卡洛长度限制;抖动模型只有高斯 RJ(无 DJ/SJ/DCD);无 CDR/时钟恢复模型(采样相位靠 `shift_signal` 启发式);无量化/AGC/ADC 非理想性;Receiver 的过采样 DFE 反馈窗口(半 UI 错位)只是可视化近似;
- **工程性**:脚本间靠 .npy 文件和硬编码路径耦合;`from x import *` 污染命名空间;单 commit 无演进历史。

## 6. 对自研 SerDes 建模项目的可借鉴之处

1. **架构分层值得直接套用**:频域信道层(ABCD/S 参数 → H(f))、波形层(TX/RX 行为类)、算法层(FFE/DFE 求解与自适应)、验证层(PRBS/PRQS + checker + 眼图 + BER)四层解耦,`.npy`/文件传递可升级为统一的 Simulation Context 对象。
2. **可直接移植的算法件**:
   - `four_port_to_diff` 的混合模式转换 + ΓS/ΓL 端接修正公式(four_port_to_diff.py:70–95)是 s4p→差分信道的标准做法;
   - `zero_pad` + `freq2impulse` 的"频域补零控制时域步长"技巧(chmodel.py:110–196);
   - `forcing_ffe` 的 Toeplitz 矩阵零迫解法(signal.py:397–411),可扩展为 MMSE(A→AᵀA+λI);
   - `lms_equalizer` 的训练序列/判决导向双模式 + `alpha` 抽头冻结 + `update_rate` 设计,接口设计对硬件自适应环路建模很实用;
   - KP4/KR4 参数化(rs_code.py:6–11)与比特↔GF(2¹⁰) 符号打包管线。
3. **应吸取的教训/需补强方向**:核心数值循环必须向量化或用 numba/C 扩展(DFE 判决反馈是天然串行,可用 numba);增加统计眼/峰值失真分析以突破蒙特卡洛 BER 下限;抖动模型需扩展 DJ/SJ 分解;补 CDR 相位环模型替代 `shift_signal` 启发式;CTLE 应做成参数化库类(极零点/增益档位表);从第一天起配 pytest 与 README-代码一致性检查。
4. **教学资产**:problem_set.pdf + HW 脚本是一套现成的线路链路训练材料,适合新成员 ramp-up。

---

# 第二部分:Stanford DragonPHY2 深度分析

> 说明:RTL 实际组织在 `vlog/chip_src/`(流片 RTL)、`vlog/chip_stubs/`(宏单元 stub)、`vlog/cpu_models/`(仿真行为模型)、`vlog/fpga_models/`(FPGA 仿真模型)、`vlog/pack/`(SV package)、`vlog/tb/`(测试台工具)六大视图下。

## 1. 项目定位与背景

**DragonPHY 是什么。** DragonPHY 是 Stanford VLSI 组 "Open Source PHY" 项目的第二代设计(README.md 第一句;第一代是 ButterPHY——`synthesis/scripts/butterphy_top/`、`pnr/scripts/butterphy_top_run_all.tcl` 中仍残留其脚本)。它是一个 **ADC-based(数字均衡型)高速 SerDes 接收机 + 简单发射机 + MDLL 时钟生成** 的完整链路 PHY:模拟前端用 16 路时间交织的 8-bit "随机(stochastic)ADC" 对输入采样量化,之后全部均衡、判决、时钟恢复都在数字域完成(FFE + 判决 + 信道估计误差反馈 + 滑动检测器/MLSD + Mueller-Müller CDR)。

**目标工艺与流片背景。** 物理实现流程通过 mflowgen 支持两个工艺(`designs/dragonphy_top/construct.py`:环境变量 `DRAGONPHY_PROCESS` 选 `TSMC16` 或 `FREEPDK45`)。TSMC16(16nm FFC)是真实流片目标——`dragonphy/views.py::get_deps_asic()` 中显式引用 TSMC16 宏单元名(SRAM `TS1N16FFCLLSBLVTC1024X144M4SW`、标准单元 `BUFTD4BWP16P90` 等);FreePDK45/Nangate 则作为开源工艺回归通道(BuildKite CI 中用 `DRAGONPHY_PROCESS=FREEPDK45` 跑 DC 综合)。顶层 `dragonphy_top.sv` 有 `output wire logic clk_cgra` 端口和 JTAG 使能的时钟门控,说明该 PHY 是作为 Stanford AHA(Agile Hardware)项目 Garnet/CGRA SoC 的片上高速接口子系统集成流片的(DARPA POSH/AHA 背景)。

**许可证。** Apache-2.0。`setup.py` 中版本号 `0.1.3`,开发状态标注为 `Development Status :: 2 - Pre-Alpha`。

**维护状态。** 可见的最后一次提交为 **2021-06-10**(合并 ZCU106 FPGA 板支持的 PR #114)。该仓库自 2021 年中之后基本停止活跃开发,属于**已完成流片使命、随论文/项目结题而封存**的学术开源项目。依赖链锁死在 2020-2021 年的版本(`svreal==0.2.7`、`msdsl==0.3.6`、`anasymod==0.3.6`、`fault==3.0.36`、`magma-lang==2.1.17` 等,Python>=3.7),今天直接 `pip install -e .` 大概率需要做依赖考古。

## 2. 整体架构

### 2.1 顶层层次(`vlog/chip_src/top/dragonphy_top.sv`,module `dragonphy_top`)

```
dragonphy_top
├── input_buffer ×4          (ibuf_async / ibuf_main / ibuf_mdll_ref / ibuf_mdll_mon,差分转单端时钟输入)
├── analog_core   iacore     (vlog/chip_src/analog_core/analog_core.sv —— 模拟前端:16路TI-ADC + PI)
├── tx_top        itx        (vlog/chip_src/tx_16t1/tx_top.sv —— 16:1 串化发射机)
├── digital_core  idcore     (vlog/chip_src/digital_core/digital_core.sv —— 全部数字后端)
├── mdll_r1_top   imdll      (vlog/chip_src/mdll_r1/ —— 注入锁定 MDLL 时钟生成)
└── mdll_inv      minv_i
```

顶层信号面很有代表性:模拟输入用宏 `` `pwl_t ``/`` `real_t ``/`` `voltage_t ``(定义在 `inc/{cpu,fpga,asic}/iotype.sv`,CPU 视图下映射到 mLingua 的 PWL 分段线性实数类型),JTAG 走 SystemVerilog interface,模拟/数字之间用一组 debug interface(`acore_debug_intf`、`tx_debug_intf`、`mdll_r1_debug_intf`)传递配置与观测信号。

关键跨模块信号:
- `adcout [Nti-1:0]`(16 路 8-bit)+ `adcout_sign`、2 路 replica(`Nti_rep=2`):ADC → digital_core;
- `pi_ctl_cdr [Nout-1:0]`(4 相 × 9-bit,`Npi=9`)+ `ctl_valid`:CDR → analog_core 的相位插值器;
- `clk_adc`:ADC 重定时时钟,同时是整个数字核的主时钟(1/16 速率并行时钟域)。

全局常量集中在 `vlog/pack/const_pack.sv`:`Nti=16`(交织路数/并行度)、`Nadc=8`(ADC 位宽)、`Nout=4`(PI 输出相位数)、`Npi=9`(PI 控制位)、`Nprbs=32` 等。

### 2.2 模拟前端(行为模型 + 宏 stub 双视图)

`vlog/chip_src/analog_core/` 下是**可仿真的行为级模拟前端**(ASIC 实现时整个 `analog_core` 作为硬宏被 `views.py::get_deps_asic` 的 `skip` 集合排除;`vlog/chip_stubs/analog_core` 提供 LVS/综合 stub):

- `stochastic_adc_PR.sv`:核心 ADC slice——**V2T(电压-时间转换)+ 随机 TDC** 结构:`V2T.sv`/`V2T_clock_gen.sv` 做采样与电压转脉宽,`TDC_delay_chain_PR.sv`+`snh.sv` + 255 个触发器采样延迟链,`wallace_adder.sv` 把 255 位温度计码求和成 8-bit 码,`arbiter.sv`、`PFD.sv` 输出符号位(相位先后判决),后端需要 `adc_unfolding` 配合;
- `phase_interpolator.sv` / `phase_blender.sv` / `PI_delay_chain.sv` / `PI_local_encoder.sv`:CDR 控制的 4 相 PI(9-bit 控制:高位选 mux 相区 + `Nblender=4` 位相位混合);
- `adc_retimer.sv`、`input_divider.sv`、`sync_divider.sv`:时钟分配与 ADC 数据重定时;
- 模拟基元在 `vlog/cpu_models/analog_core/` 有 mLingua PWL 版本,在 `vlog/fpga_models/analog_core/` 有 msdsl 生成的可综合定点版本。

MDLL(`vlog/chip_src/mdll_r1/`)是外部合作者提供的注入锁定倍频 DLL,含 BB-PD 数字环路、DAC 追踪环、频率校准,所有控制/观测都经 `mdll_r1_debug_intf` 挂到 JTAG。

### 2.3 数字后端(`digital_core.sv`)

`digital_core` 是"胶水 + BIST + 调试"层,数据通路本体在 `datapath_core`:

```
digital_core
├── ti_adc_reorder            (ADC 通道重排序)
├── avg_pulse_gen + adc_unfolding ×16(+2 replica)   (PFD 偏移校准/码展开)
├── mm_cdr  iMM_CDR           (Mueller-Müller CDR)
├── weight_manager ×2         (FFE 权重 / 信道估计系数的 JTAG 写入管理)
├── datapath_core datapath_i  (FFE→判决→信道滤波→误差→滑动检测器)
├── oneshot_multimemory ×2    (ADC 原始码 / FFE 输出的单次 SRAM 抓取)
├── prbs_generator_syn + prbs_checker      (BIST)
├── error_tracker             (误差事件记录)
├── histogram_* 系列          (ADC/FFE 输出直方图统计)
├── output_buffer             (16 选 1 内部时钟/信号观测输出)
├── jtag jtag_i               (JusTAG/Genesis2 生成的寄存器文件,挂 12 个 debug interface)
└── tx_data_gen               (TX PRBS/pattern 数据源)
```

**时钟域**:数字核几乎全部运行在 `clk_adc`(串行速率/16 的并行时钟);TX 数据发生器在 `clk_tx`;JTAG TCK 为独立测试域(md/ 中的寄存器表明确标注每个字段属于 "System/Test" 时钟域)。CDR 的 PI 控制码经 `ctl_valid` 握手(复位后等 32 拍,`digital_core.sv` 253-262 行)进入模拟域。

值得注意:`vlog/chip_src/digital_core/dsp_backend.sv` 是**上一代 DSP 后端**(FFE + 全 MLSD 判决链),当前 `digital_core` 已改用 `datapath_core`(FFE + 滑动检测器方案),`dsp_backend` 保留未删;`digital_core.sv` 中 `checked_bits`(MLSD 输出)信号仍被 PRBS mux 和 SRAM 抓取引用但**已无驱动源**(86 行仅声明),这是切换方案留下的悬空遗迹——分析/复用此代码时需要注意。

## 3. 数字信号处理链路详解

### 3.1 ADC 码展开与校准(`vlog/chip_src/calib_pfd_offset/adc_unfolding.sv`)

stochastic ADC 输出 8-bit 幅度 + 1-bit 符号(PFD 判定)。`adc_unfolding` 按符号把码"展开"为有符号数,并做 **PFD 偏移自校准**:`avg_pulse_gen.sv` 每 2^Ndiv 周期产生一次更新脉冲,`calib_sample_avg.sv`/`calib_hist_measure.sv` 对码值做滑动平均和直方图(中心 bin/边 bin 统计 + 死区 DZ),迭代估计每个交织通道的 PFD 偏移 `pfd_offset`,也可从 JTAG 强制外部偏移。每路独立校准,消除 16 路 TI 通道的失配。另有 `calib_v2t_gain/calib_v2t_gain.sv` 做 V2T 增益校准。

### 3.2 均衡与判决主链(`vlog/chip_src/datapath_core/datapath_core.sv`)

`datapath_core` 是一条**16 路并行、参数化流水深度**的 DSP 流水线,其结构完全由 `config/system.yml` 驱动(FFE 长度 10、权重 10-bit、输入码 8-bit、输出 10-bit、信道估计深度 30、误差 9-bit、检测序列长 3;流水深度 `ffe_pipeline_depth=1`、`chan_pipeline_depth=1`、`sld_dtct_out_pipeline_depth=2` 等)。链路如下:

1. **FFE**(`vlog/chip_src/fir/comb_ffe.sv`):纯组合的 16 通道并行 FIR。每通道 `ffeDepth=10` 个 tap:`product = weights[ii][gi] * flat_codes[idx-ii+gi]`,求和后 `estimated_bits[gi] = result >>> shift_index[gi]`(每通道独立的移位定标 `ffe_shift`,由 Python 端 `FFEHelper.calculate_shift()` 算好经 JTAG 下发)。每个 tap 有独立 `disable_product` 屏蔽位。输入来自 `signed_buffer`/`signed_flatten_buffer_slice`(`vlog/chip_src/flat_buffer/`)构成的多拍历史缓冲——这套 "buffer + flatten + slice" 泛型组件是整个并行 DSP 对齐跨通道边界样本的基础设施。
2. **判决**(`vlog/chip_src/dig_comp/comb_comp.sv`):对 FFE 输出与每通道阈值 `thresh` 比较出 `cmp_out`,经 `bit_aligner.sv` 按 JTAG 配置的 `align_pos` 做比特相位对齐得到 `sliced_bits`。
3. **信道滤波/码重构**(`vlog/chip_src/channel_filter/channel_filter.sv`):用 JTAG 下发的 30 深度信道估计 `channel_est`(经第二个 `weight_manager` 写入)对已判决的 ±1 比特序列做卷积:`est_code += bitstream ? +channel[jj] : -channel[jj]`,再移位定标,重构出"如果判决正确,ADC 应看到的码" `est_codes`。
4. **误差生成**(datapath_core 277-283 行):`est_error = est_code - adc_code`(两者经流水线深度精确对齐,localparam 段落 25-56 行全部在算对齐深度)。该误差流既输出给 `error_tracker` 抓取,也是自适应和错误检测的原料。
5. **滑动检测器 / 单发错误纠正**(`vlog/chip_src/sliding_detector/sliding_detector.sv`):本设计最有特色的模块——一个**基于误差事件注入的后验 MMSE 检测器**(简化 MLSD)。原理:若第 k 位判决错了,残差流中会出现极性为 `∓2h` 的误差事件。RTL 对每个通道并行构造 4 种假设(无错/前一位错/本位错/相邻两位都错),把对应的"反误差向量" `error = bitstream ? -2*channel : +2*channel` 注入残差流,计算长度 `seq_length=3` 窗口内的平方误差和 `mse_err[4][16]`,取 argmin 输出 2-bit `mmse_err_pos`(即 `sd_flags`)。这是把 MLSD 的序列搜索退化为"错误事件检测"的低成本实现。
6. **完整 MLSD 链(备用路径,`dsp_backend.sv`)**:`comb_partial_code_gen.sv` → `comb_potential_codes_gen.sv` → `comb_mlsd_decision.sv` + `comb_eucl_dist.sv`(对 2^nbit 个候选算欧氏距离取最小)→ `predict_bits`。当前流片版未实例化,但 RTL 与 Python 黄金模型(`dragonphy/mlsd.py`,含 `perform_mlsd_single`/`calculate_norm`)完整保留。

**自适应策略**:片上**没有**硬件 LMS 更新引擎——FFE 权重与信道估计均在片外(Python)用 `dragonphy/adaptation.py::Wiener` 类计算(`find_weights_pulse` 实现归一化 LMS,支持 `blind=True` 的判决引导盲自适应),量化后经 JTAG → `weight_manager.sv`(带指令/数据/exec 握手的小型写口,md/wme_intf.md 定义寄存器)烧入。`error_tracker` + SRAM dump + `histogram` 给片外自适应闭环提供观测数据。这是学术芯片常见的 "hardware datapath + software adaptation" 折衷。

### 3.3 CDR(`vlog/chip_src/mm_cdr/`)

- **相位检测**:`mm_pd.sv` 实现**并行化 Mueller-Müller PD**:对 16 路 ADC 码,取符号 `ak = sign(din[k])`,相位误差项 `pd_net[k] = din[k]*(ak[k+1]-ak[k-1])`(标准 MM 公式 `e = x[n]·(â[n+1]−â[n−1])` 的滑窗形式),16 项求和后 `>>> log2(16)` 平均,加 JTAG 可编程 `pd_offset`(用于锁定相位微调,实现 baud-rate CDR 的采样点偏置)。注意它**不是** bang-bang PD,而是保幅度的线性 MM-PD。
- **环路滤波**:`mm_cdr.sv` 是二阶数字 PI 环:比例路径 `phase_error << Kp`、积分(频率)路径 `freq_est += phase_error << Ki`,增益均为 JTAG 可编程移位;另有独特的**三阶斜坡估计器**(`ramp_est_pls/neg`,由外部 `ramp_clock` 调制,`Kr` 增益)用于跟踪三角波频率调制(SSC-like 实验);相位更新可限幅(`cdr_clamp_amt`,修复 issue #20 的过冲问题)。`phase_est >> phase_est_shift(20)` 截位后直接作为 9-bit PI 码 `pi_ctl` 送 4 个 PI。
- **输入选择**:`digital_core.sv` 240-246 行,CDR 输入可在原始 ADC 展开码与 FFE 输出(取高 8 位)之间用 `cdbg_intf_i.sel_inp_mux` 切换——即支持均衡前/均衡后定时恢复对比实验。
- **CDR 环内其余环节**(PI 码缩放 `scale_value`、偏置 `ext_pi_ctl_offset`、旁路 `en_bypass_pi_ctl`)在 `digital_core.sv` 282-289 行,便于开环/闭环调试。
- Python 侧对应模型:`dragonphy/cdr.py::Cdr`(`cal_mm_timing_error` 与 RTL 公式一致的 numpy 版,`__main__` 里有完整的 CDR 收敛行为仿真)。

### 3.4 BIST 与可观测性

- `prbs_generator_syn.sv`/`prbs_checker.sv`:32-bit LFSR,方程 `eqn`、初值、错误注入 `inj_err`、极性 "chicken bits" 全部 JTAG 可编程;checker 输入可在 ADC 判决/FFE 判决/MLSD 判决/内部环回四路间 mux(`sel_prbs_mux`),输出 64-bit 误码与总比特计数——BER 测量完全片上化。
- `error_tracker.sv`:当 PRBS flag 报错时把 `est_errors`/`sliced_bits`/`sd_flags` 快照进 SRAM,支持错误事件的事后取证。
- `histogram.sv` 系列:64-bit 计数的码值直方图,可测 ADC ENOB/眼图统计。
- 双 `oneshot_multimemory`(4 tile SRAM):一次性抓 16+2 路 ADC 原始码与 FFE/MLSD 输出,JTAG 读回——Python 侧 `dragonphy/analysis/histogram.py`、`enob.py` 做离线分析。

## 4. 建模与验证方法学

这是本仓库对自研项目最有参考价值的部分:**整个芯片的构建被组织成一个 Python 驱动的"视图化"构建系统**。

### 4.1 构建图与多视图文件解析

- `make.py`(`python make.py --view {cpu|fpga|asic}`)用 `dragonphy/graph/graph.py::BuildGraph` 声明构建依赖:config(YAML)→ Python 生成器 → 生成物落在 `build/` 下。
- `dragonphy/views.py::get_deps()` 是核心:用 **svinst**(SystemVerilog 解析器)静态解析每个模块实例化了哪些子模块,然后按**视图优先级序**在目录树中逐模块解析实现文件——
  - CPU 仿真:`view_order=['dw_tap','mlingua','pack','tb','cpu_models','chip_src']`;
  - FPGA 仿真:`['fpga_models','pack','chip_src']` + svreal/msdsl 头文件,`no_descend={'chan_core','tx_core',...}`(msdsl 生成的黑盒);
  - ASIC:`['pack','chip_src']`,`skip={'analog_core','mdll_r1_top',...}`(硬宏),并按工艺 `override`(tsmc16/freepdk45 的 SRAM、tri-buf 等)。

  这实现了同一模块名在"行为模型/可综合 RTL/工艺 stub"之间的自动替换——一套顶层,三种视图,无需维护 filelist。

### 4.2 参数化 RTL 生成

- `dragonphy/adapt_fir.py::AdaptFir` 读 `config/system.yml`,用 `dragonphy/packager.py::Packager` 生成 6 个 SV package(`constant_gpack`、`ffe_gpack`、`cmp_gpack`、`channel_gpack`、`error_gpack`、`detector_gpack`)——RTL 中所有位宽/深度都引用这些 package,**位宽探索只改一个 YAML**;同时生成参考信道脉冲响应 `chan.npy`。
- `dragonphy/jtag.py::JTAG`:把 `md/*.md` 中的 **Markdown 寄存器表**(如 `md/sm_ffe_intf.md`,列:名称/符号/packed 维度/unpacked 维度/时钟域/方向/复位值)喂给 **JusTAG** + **Genesis2**(Perl 代码生成器)自动生成整个 JTAG TAP、寄存器文件、`jtag_reg_pack.sv` 和验证用 `JTAGDriver`。**文档即真源**(docs-as-source)的寄存器管理方式。

### 4.3 模拟行为建模(AMS 验证栈)

三层模拟建模并存:
- **CPU 事件驱动仿真**:DaVE/mLingua 的 **PWL 分段线性实数**建模(`inc/cpu/iotype.sv` 把 `` `pwl_t `` 映射到 mLingua `pwl` 类型;regress.sh 克隆 `StanfordVLSI/DaVE` 的 `pwl_cos` 分支),`vlog/cpu_models/` 下的 V2T、snh、phase_blender 等都是 PWL 模型,可在 Xcelium 上和数字 RTL 联仿真;
- **FPGA 仿真**:**msdsl/svreal**(定点合成实数)+ **anasymod**(`dragonphy/anasymod.py` 生成其项目 YAML)——把信道(基于 `dragonphy/channel.py` 的 s4p→脉冲响应,`sparams.py::s4p_to_impulse`)、ADC、振荡器、时钟延迟编译成 Vivado 可综合模型,在 ZC702/ZCU106 Zynq 板上以 5 MHz 仿真时钟做**全链路 PRBS 闭环仿真**(`tests/fpga_system_tests/emu/`,UART/VIO 控制,固件 `main.c`);
- **Python 黄金模型**:`Channel/DelayChannel`(多种解析信道:exponent/arctan/skineffect + S 参数)、`Quantizer`、`Fir`、`Wiener`、`MLSD`、`Cdr`——算法级探索与 RTL 比对基准。

### 4.4 测试组织与 CI

- `tests/cpu_block_tests/`(约 20 个块级)、`tests/cpu_system_tests/`(约 25 个系统级:loopback_sram、mm_cdr、ffe_simple、jtag_hello、mdll、tx_loopback...)、`tests/fpga_block_tests/` + `tests/fpga_system_tests/{emu,emu_macro}`、`tests/other_tests/`(s 参数、ENOB 等纯 Python)。
- 典型测试(`tests/cpu_system_tests/ffe_simple/test_ffe_simple.py`):`get_deps_cpu_sim()` 解析依赖 → `DragonTester`(anasymod 封装,缺省 Xcelium)跑 SV 测试台 → 测试台把 ADC/FFE 输出写成 txt(`vlog/tb/` 里的 `*_recorder.sv`)→ numpy 逐点断言 RTL 输出 = 卷积参考。**RTL 数值级 lockstep 校验**贯穿始终。
- `conftest.py` 提供丰富的 pytest 选项(`--simulator_name`、`--ffe_length`、`--jitter_rms`、`--noise_rms`、`--chan_tau` 等),同一测试可扫参数。
- CI:BuildKite 双流水线——`test`(Xcelium 全量回归,约 55 分钟)+ `test_mflowgen`(FreePDK45 DC 综合冒烟);codecov 统计 Python 覆盖率。CI 依赖 Stanford 内部 CAD license 服务器,外部无法直接复现。

## 5. 物理实现流程

- **主流程:mflowgen**(Cornell BRG 的步骤图式流程管理)。`designs/dragonphy_top/construct.py` 组装完整后端图:`synopsys-dc-synthesis` → `cadence-innovus-init/cts/...` → `mentor-calibre-lvs/fill/gdsmerge`,外加自定义步骤:`mc-gen-sram`/`openram-gen-sram`(TSMC MC 或 OpenRAM 双通道 SRAM 生成)、`qtm`(为 analog_core/PI/input_divider 等硬宏建 Quick Timing Model)、`inject_dont_touch`、`custom-power/route/geom`、`prelvs_fix` 等;工艺经 `DRAGONPHY_PROCESS` 一键切换 TSMC16/FreePDK45。子块(`analog_core`、`phase_interpolator`、`input_buffer`、`mdll_r1`、`termination` 等)各有独立设计目录。
- **遗留流程**:`synthesis/scripts/`(DC 模板、formality、功耗报告)与 `pnr/scripts/`(Innovus 分步 tcl:init → place → prects → ccopt → route → filler/SVIA → chip_out)是 ButterPHY 时代的手工脚本,保留作参考。
- `mflowgen.sh` 展示最小可复现路径:装 Genesis2 + mflowgen → `python make.py --view asic` → `mflowgen run --design designs/dragonphy_top && make synopsys-dc-synthesis`。

## 6. 优点、局限与可借鉴之处

### 优点

1. **数字域 SerDes RX 的完整参考实现**:从 TI-ADC 校准(unfolding/PFD offset)→ 并行 FFE → 判决 → 信道估计误差重构 → 错误事件检测(简化 MLSD)→ MM-CDR → PI,链路环环齐全且全部开源,还带 PRBS/直方图/误差抓取全套片上 DFT。
2. **"一套 RTL、三种视图"的方法学**:svinst 自动依赖解析 + 视图优先级替换(views.py),同一 testbench 可跑 CPU 事件仿真、FPGA 实时仿真、ASIC 网表,验证复用率极高。
3. **配置驱动的位宽/深度参数化**:YAML → SV package 生成(Packager),配合泛型 buffer/flatten 组件,把 16 路并行 DSP 的流水对齐问题工程化(完全 localparam 化,可静态检查)。
4. **寄存器即文档**:md 表格 → JusTAG/Genesis2 自动生成 JTAG 寄存器文件 + 驱动,消灭了寄存器表/RTL/驱动三方不一致。
5. **Python 黄金模型与 RTL 数值 lockstep**:Wiener/MLSD/Cdr/Channel 与 RTL 逐点比对,且 FPGA 仿真能在真实时间尺度跑 BER,这是 ADC-based SerDes 验证的最难点,项目给出了可行的开源方案(anasymod/msdsl/svreal)。
6. CI 化的回归 + 开源工艺(FreePDK45)综合冒烟,保证 RTL 始终"可综合、可复现"。

### 局限

1. **自适应不在片上**:FFE/信道估计的 LMS 收敛在 Python 完成、JTAG 下发;没有硬件 DFE,也没有片上实时自适应引擎——作为产品级 SerDes 还缺一大块。
2. **代码熵较高**:`dsp_backend.sv` 与 `datapath_core.sv` 新旧两代并存、`checked_bits` 悬空、`digital_core` 中残留 Vivado bug workaround 注释、`adaptation.py::find_weights_error` 引用未定义的 `error_norm`(死代码)等——切换设计方案时的清理不彻底。
3. **依赖锁死且生态脆弱**:Genesis2(Perl)、DaVE 特定分支、justag/svinst/anasymod 固定旧版本、Python 3.7、内部 CAD 环境;2021 年后无人维护,直接复活成本不低。
4. CDR 是纯行为可综合环路 + 行为 PI,缺少抖动容限/环路带宽的系统化 sign-off 流程;MM-PD 为全并行 16 项求和,面积/时序在更高速率下的可扩展性未验证。
5. 文档主要是寄存器表,架构级文档缺失(需要读代码反推)。

### 对自研 SerDes 项目的借鉴建议

1. **DSP RTL 侧**:直接借鉴的三件套——(a) `comb_ffe` 的"扁平历史缓冲 + 每通道移位定标 + per-tap disable"并行 FFE 模式;(b) `channel_filter`+误差重构+`sliding_detector` 的**错误事件检测**思路(以 4 假设 MMSE 排序替代全 MLSD,硬件代价极低,适合作为 FFE 后的 BER 增强级);(c) `mm_pd`/`mm_cdr` 的并行 baud-rate CDR 写法(含 clamp、可编程移位增益、均衡前后输入 mux、ramp/SSC 跟踪支路)。
2. **验证框架侧**:最值得照抄的是 **views.py 的视图化依赖解析**(现代可用 pyslang/verible 替代 svinst)与 **YAML→SV package 的参数单源化**;以及"RTL 输出 recorder → numpy 黄金模型逐点断言"的测试范式和 conftest 参数扫描。
3. **寄存器管理**:md 表驱动生成 JTAG/CSR 的思路可平移到 SystemRDL/自研脚本,关键是保持"单一真源 + 自动生成读写驱动"。
4. **AMS 建模**:若需要 FPGA 级 BER 仿真,msdsl/svreal/anasymod 栈今天仍可用(注意版本考古);若只做算法验证,可用其 Channel/Quantizer/Wiener 类作为轻量 Python 基线。
5. **引以为戒**:片上自适应缺失、双代 datapath 并存的悬空信号,提示自研项目要为"算法方案迭代"预留干净的模块替换边界,并把自适应引擎(哪怕慢速 FSM 版)纳入片上规划。

---

# 第三部分:PyBERT 深度分析

## 1. 项目定位与背景

### 1.1 定位

PyBERT 是一个带 GUI 的**串行链路误码率测试仪(BERT)仿真器**,定位于"探索串行通信链路设计概念"的教育/工程工具,同时(经多年演进)已具备相当专业的能力:S 参数信道导入与级联、IBIS-AMI 模型(Init/GetWave 双模式)、Tx FFE / Rx CTLE / Rx FFE / DFE / CDR 行为级建模、抖动分解、MMSE/穷举 EQ 协同优化、Viterbi MLSE/FEC 译码、COM(Channel Operating Margin)计算、多 lane S 参数与 FEXT 串扰等。

### 1.2 作者与历史

- 原作者 **David Banas**,首版日期 2014-06-17(见 `src/pybert/pybert.py`、`models/dfe.py`、`models/cdr.py` 文件头);主要合作者 **David Patterson**(负责 Python2→3 迁移与现代构建体系)。README 致谢中还有 Peter Pupalaikis(FFT/S 参数理论)、Yuri Shlepnev(Simbeor 作者)等业内知名人士,可见其在 SI 社区的渊源。
- 代码内的历史脉络清晰可考:`utility/__init__.py` 注明血统 `pybert_cntrl.py => utility.py => utility/ 子包`(2024 年重构);`models/bert.py` 是 2014 年从 `pybert.py` 拆出的控制器;Viterbi(2025-06)、FEC(2025-08)是最新增量。
- **当前版本 v10.0.0**,提交日期 **2026-07-01**——项目至今活跃维护(PyPI 包名为 **PipBERT**,因 "PyBERT" 名字被抢注)。
- **许可证**:3-clause BSD。

### 1.3 依赖栈(`pyproject.toml`)

| 类别 | 依赖 | 用途 |
|---|---|---|
| GUI | `traitsui==8.0.0`、`pyface==8.0.0`、`pyside6==6.10.2` | Enthought Traits/TraitsUI 声明式 GUI,Qt 后端 |
| 绘图 | `chaco==6.1.1`、`enable==6.1.0` | Chaco 交互式科学绘图(眼图热力图等) |
| 数值 | `numpy==1.26.4`、`scipy==1.14.0` | FFT、滤波器设计、插值、curve_fit |
| RF | `scikit-rf==1.10.0` | Touchstone 读取、Network 级联(`**`)、重归一化 |
| 自研配套 | `pyibis-ami>=8.1`(PyAMI)、`pychopmarg>=3.1.2`(PyChOpMarg) | IBIS-AMI 与 COM,同为 Banas 作品 |
| 其他 | `click`(CLI)、`pyyaml`(配置)、`parsec`(HSPICE CSDF 解析)、`pandas`+`xlrd`(COM 配置表格) |

Python 支持 3.10–3.12。构建采用 `make` + `uv`(`[tool.uv.workspace]` 把 `PyAMI/`、`PyChOpMarg/` 声明为 uv workspace 成员并 editable 安装)。

### 1.4 子模块方式

`.gitmodules` 注册了三个 git submodule:`PyAMI`、`PyChOpMarg`、enthought/chaco。**同一作者的三件套 PyBERT + PyAMI(pyibisami)+ PyChOpMarg 构成完整生态**,开发态用 submodule+workspace 联调,发布态用 PyPI 版本约束。

## 2. 代码结构(src/pybert,共约 9350 行)

### 2.1 架构总述

架构上是典型的 **Traits 单体模型**:`PyBERT`(`pybert.py`,1663 行)是一个 `HasTraits` 类,**同时充当数据模型、仿真控制器和 GUI 模型**。GUI 与内核的"分离"方式是:

- 视图完全在 `gui/view.py`(970 行)中以 TraitsUI `View/Group/Item` 声明,不含算法;
- 仿真管线是**自由函数** `my_run_simulation(self, ...)`(`models/bert.py`),把 PyBERT 实例当作参数袋/结果袋传入;
- 无 GUI 用法:`PyBERT(run_simulation=False, gui=False)`(tests、CLI `sim` 子命令、`misc/PyBERTasLibrary.ipynb` 都这么用);
- 后台线程(`threads/`)保证 GUI 响应性。

### 2.2 逐文件职责

**顶层**

| 文件 | 关键内容 |
|---|---|
| `pybert.py` | `PyBERT(HasTraits)`:全部独立变量 traits(bit_rate、nbits、nspui、pattern(PRBS-7~31 的 LFSR 抽头映射)、mod_type(NRZ/Duo-binary/PAM-4)、inter_sel(native/single/multiple)、Johnson 信道参数、Tx/Rx 原生参数与 IBIS-AMI 文件 traits、DFE/CDR 参数、20 抽头 Rx FFE/DFE tuner 列表、COM 开关等);`Property+@cached_property` 派生量;`calc_chnl_h()`(信道冲激响应);`simulate()`;配置/结果存取;所有 `_<trait>_changed` 观察器 |
| `configuration.py` | `PyBertCfg`:显式白名单式快照配置,YAML(推荐)或 pickle 序列化;`load_from_file()` 用 `setattr` 回灌,并对旧字段名做迁移 |
| `results.py` | `PyBertData`:pickle 保存 `plotdata` 中 23 个响应波形,重载时以 `*_ref` 名义作为参考曲线叠加到图上 |
| `cli.py` | click 入口:`pybert`(GUI)与 `pybert sim <config.yaml>`(无头运行,输出 `.pybert_data`) |
| `common.py` | 类型别名 `Rvec/Cvec/Rmat/Cmat`、常量 |

**models/**

| 文件 | 关键内容 |
|---|---|
| `models/bert.py`(1433 行) | `my_run_simulation()`:完整仿真管线;`compute_com()`;`update_results()`(全部 plotdata 更新、浴盆曲线、眼图、Viterbi trellis 可视化);`update_eyes()` |
| `models/dfe.py` | `DFE` 类:`run_dfe()`(逐样本 DFE+CDR 主循环)、`step()`(sign-sign LMS 自适应)、`decide()`(三种调制的判决)、AGC;`LfilterSS`(可单步的 IIR,用于非理想求和节点) |
| `models/cdr.py` | `CDR` 类:Bang-bang(Alexander)鉴相 + 比例/积分环路,`adapt(samples)`;锁定检测带迟滞 |
| `models/viterbi.py` | 抽象 `ViterbiDecoder(ABC, Generic[S, X])` + 具体 `ViterbiDecoder_ISI`(以脉冲响应样本构造各状态期望电压,做 MLSE 均衡) |
| `models/fec.py` | `FEC_Encoder`(rate-1/2 卷积码,G0=1+D+D²+D³, G1=1+D²+D³)与 `FEC_Decoder`(复用 ViterbiDecoder,硬判决汉明度量) |
| `models/com.py` | `calc_com()`:PyChOpMarg 封装 |
| `models/tx_tap.py` | `TxTapTuner(HasTraits)`:name/pos/enabled/min/max/step/value,贯穿 Tx FFE、Rx FFE、DFE 三处的通用"抽头调谐器"行对象 |

**utility/**

| 文件 | 关键函数 |
|---|---|
| `utility/sigproc.py` | `make_ctle()`(CTLE 频响合成)、`calc_resps()`(冲激→阶跃/脉冲/频响)、`trim_impulse()`(按一阶导数能量 99.9% 截取)、`calc_eye()`(眼图热力图)、`pulse_center()`("Hula Hoop" 算法,SiSoft DesignCon 2016)、`add_ffe_dfe()`/`get_dfe_weights()`(PRZF 理想 DFE 权重)、`import_time()`、`resize_zero_pad()` |
| `utility/jitter.py` | `find_crossing_times()/find_crossings()`(线性插值过零)、`calc_jitter()`(核心抖动分解) |
| `utility/sparam.py` | `import_channel()`、`import_freq()`(1/2/4/8/12 端口)、`import_fext()`(多 lane FEXT 子网提取)、`sdd_21()`/`se2mm()`(单端→混模,含 1↔3 端口错序自动检测)、`interp_s2p()`(带外推约束的安全插值:S11/S22 幅度 cap 到 1,S21 越界填 0)、`H_2_s2p()`(时域响应→2 端口网络) |
| `utility/channel.py` | `calc_gamma()`(Howard Johnson《High Speed Signal Propagation》§3.1 金属传输模型:趋肤 Rac=R0√(2jω/ω0)、损耗角正切引起的复电容)、`calc_gamma_RLGC()`、`calc_G()`(考虑源/负载 RC 端接与二次反射 1/(1−R1R2H²) 的加载传函) |
| `utility/ibisami.py` | `run_ami_model()`(AMI 模型统一驱动) |
| `utility/math.py` | `lfsr_bits()`(PRBS 生成器)、`make_bathtub()`(PDF→CDF 折叠+高斯外推)、`gaus_pdf()`、`safe_log10()`、`all_combs()` |

**gui/**:`view.py`(TraitsUI 布局)、`plot.py`(675 行,Chaco 容器)、`handler.py`(Run/Stop/存取配置与结果/优化启停/EQ 的 Reset/Use 按钮)、`help.py`。

**threads/**:`stoppable.py`、`sim.py`(`RunSimThread`)、`optimization.py`(`OptThread` + `coopt()`)。

**solvers/**:`solver.py` 定义抽象 `Solver.solve()`(走线几何→S 参数),`solvers/simbeor/` 是 Simbeor SDK 的桥接——预留的"2.5D 场求解器插槽"。

**parsers/hspice.py**:用 parsec 组合子写的 HSPICE CSDF 波形文法解析器。

## 3. 仿真内核详解

### 3.1 信道建模(`PyBERT.calc_chnl_h()`,pybert.py:1318)

三种互连来源(`inter_sel`):

1. **native**:Howard Johnson UTP 解析模型。`calc_gamma(R0,w0,Rdc,Z0,v0,Theta0,w)` 给出 γ(ω) 与 Zc(ω),`H = exp(−l_ch·γ)`;再用 H、Zc 构造一个"完美匹配"2 端口 `rf.Network`(S11=S22=0, S21=S12=H, z0=Zc),`renormalize(Rs)` 到驱动阻抗。
2. **single**:`import_channel()` 读单个文件——Touchstone(1/2/4/8/12 端口;4 端口默认单端经 `sdd_21()` 取 DD 象限;8/12 端口按 `(+A,+B,−A,−B)` 每 4 口一个 lane,由 `lane_sel` 选取)或两列时域波形(阶跃/冲激自动判别,再经 `H_2_s2p()` 升为 2 端口网络,精度受限——docstring 明确警告)。
3. **multiple**:多个文件各自导入后用 scikit-rf 的 `**` 级联算子拼接(封装+过孔+走线的复合互连)。

之后统一处理:

- 若 Tx/Rx 选择 IBIS,则从 IBIS 模型取 `zout/zin`、`ccomp` 覆盖 Rs/Cs/RL/Cp;若 AMI 带 `Ts4file`,`add_ondie_s()` 把片上 4 端口 S 参数(`sdd_21` 转差分后 `extrapolate_to_dc().windowed()`)级联进信道。
- **端接建模的关键手法**:把复数阻抗 Zs=Rs/(1+jωRsCs)、ZL=RL/(1+jωRLCp) 直接写入网络端口参考阻抗再 `renormalize`(广义 S 参数),然后 `chnl_H = s21 * sqrt(z0[:,1]/z0[:,0])` 归一化为电压传函——比手写反射级数干净得多。
- `irfft` 得冲激响应(可选 `raised_cosine()` 加窗抑制 Gibbs),三次样条插值到系统时间栅格 `t`(`t` 与 `f` 栅格独立,靠插值桥接,幅度乘 `t[1]/t_irfft[1]` 保持 V/sample 归一),`trim_impulse()` 按一阶导数能量保留 99.9% 截短(最短 20 UI、最长 100 UI,或用户指定)。
- **FEXT**:`include_fext` 时,`import_fext()` 对 8/12 端口文件的每条 aggressor lane 提取 FEXT 2 端口网络,同样端接归一后得 `fext_h` 列表。

### 3.2 管线主干(`my_run_simulation()`,models/bert.py:79)

信号流:PRBS(`lfsr_bits`)→ 符号映射(NRZ ±1 / duobinary XOR 预编码 / PAM-4 Gray)→ 过采样 `x = repeat(symbols, nspui)` → `chnl_out = convolve(x, chnl_h)`。

噪声注入(bert.py:235–257):周期性噪声建模为方波经 2 阶 Butterworth 高通(fc=1 MHz,模拟容性耦合)+ 高斯白噪;FEXT 为各 aggressor(假定与 victim 同 PRBS,相干最坏情况)信号卷积 `fext_h` 后叠加进 noise。

**Tx/Rx 四种组合显式展开**(bert.py:259 起,因为 GetWave 模式下 Tx/Rx 不可交换):Tx GetWave × Rx {GetWave, Init, native},以及 Tx {Init, native} × Rx {GetWave, Init, native}。native Tx FFE 的冲激响应是符号间隔冲激串(每抽头后 nspui−1 个零),主抽头自动取 `1 − Σ|w_i|`(峰值功率约束)。

各级都同时维护 `*_h/_s/_p/_H`(冲激/阶跃/脉冲/频响,`calc_resps()`:s=cumsum(h),p=s−shift(s,nspui),H=补零 rfft 后插值)与级联输出版本 `*_out_*`,供 Responses 页四联图使用。

### 3.3 均衡器建模与自适应

- **CTLE**(`make_ctle()`,sigproc.py:311):一零两极模型——极点取信道自然带宽 `rx_bw` 与峰化频率 `peak_freq`,零点 `z = p1/10^(peak_mag/20)` 控制峰化量,经 `invres()` 合成有理传函、`freqs()` 采样、峰值归一。也可从文件导入 CTLE 响应。
- **Rx FFE**(bert.py:472–494):与 Tx FFE 同构的符号间隔 FIR(默认 15 抽头 5 前 9 后),仅在非 AMI Rx 时启用;仿真中权重是静态的(由优化器或用户设定),**运行时不自适应**。
- **DFE**(`DFE.run_dfe()`,dfe.py:266):逐样本循环。求和节点 `sum_out = x − filter_out`(可选经 `LfilterSS` 2 阶低通模拟有限带宽 `sum_bw`);在 CDR 给出的边界/数据时刻采样;判决后 `error = sum_out − decision*decision_scaler`,**sign-sign LMS**:`correction += tap_value * error * gain`,累积 `n_ave` 个时钟后平均更新一次,且**仅在 CDR 锁定后**累积误差;支持每抽头 min/max 限幅。AGC 用两级各 100 样本滑动平均调整 `decision_scaler`(PAM-4/duobinary 乘 1.5)并同步更新判决阈值。即使用户关闭 DFE 也会构造"dummy DFE"(0 抽头),因为 CDR 与电平→bit 判决封装在 DFE 内。
- **PAM-4 判决**(`DFE.decide()`):阈值 ±(2/3)·scaler 与 0,Gray 译码输出比特对。

### 3.4 CDR 模型(`CDR.adapt()`,models/cdr.py)

Alexander(bang-bang)鉴相:3 个采样(上个数据点、边界点、当前数据点)取 `sign`;无跳变→0 修正,早钟→+Δt,晚钟→−Δt;积分支路 `integral += α·proportional`;`ui = nom_ui + integral + proportional`(即 UI 周期调整,而非显式相位插值器)。锁定判据:最近 `n_lock_ave`(500)次比例修正的均值和积分修正方差都小于 `rel_lock_tol·Δt`,再经长度 `lock_sustain` 的 80%/20% 迟滞投票翻转 locked 状态。这是 2 阶数字 PLL 的最小行为模型。

### 3.5 抖动分解(`calc_jitter()`,utility/jitter.py:212)——本仓库最有特色的算法之一

输入理想/实际过零序列(`find_crossings()` 按调制类型选阈值;PAM-4 只用 0 阈值避免同沿多次理想过零),流程:

1. **TIE 组装**:对每个理想过零在 ±UI/2 窗内找实际过零(多个取均值);**漏翻转**(眼闭合时)填充 ±3UI/4 占位并跳过下一理想沿——这些样本最终落入直方图首尾特殊 bin。
2. **数据相关/无关分离(时域平均法)**:把上升/下降沿 TIE 各自 reshape 成 `(num_patterns, xings_per_pattern/2)` 按 PRBS 图样周期做**图样平均**,平均后的轨迹消除了非相关分量:`ISI = max(ptp(rising_ave), ptp(falling_ave))`(cap 到 1 UI),`DCD = |mean(rising_ave) − mean(falling_ave)|`;整体 TIE 减去图样平均得数据无关抖动 `tie_ind`。
3. **Pj/Rj 谱分离**:对 `tie_ind`(匀采样后 FFT),用滑动均值+`rel_thresh`(默认 3σ)滑动标准差构造自适应阈值,谱线高于阈值判为周期分量→ifft 后 `pj = ptp(tie_per)`;其余为随机分量→`rj = std(tie_rnd)`。这是**谱阈值法**的 Rj/Pj 分解。
4. **双 Dirac 拟合**:对数据无关抖动直方图(自制 `my_hist`:[−UI/2,+UI/2] 之外扫入首尾 bin)平滑后找左右峰,`pjDD = 峰间距`;对左右尾部(半高以下)分别 `curve_fit(gaus_pdf)`(以 ps 为单位避免数值问题),`rjDD = (σ_pos+σ_neg)/2`,同时返回 μ_pos/μ_neg 供浴盆外推。

管线对 **chnl / tx(rx_in)/ ctle / dfe 四个观测点分别做全套分解**,`_get_jitter_info` 生成"各均衡环节对 ISI/DCD/Pj/Rj 的抑制量(dB)"HTML 表——这个"逐级抖动预算"视角对链路调试非常实用。

### 3.6 眼图与 BER

- **眼图**(`calc_eye()`,sigproc.py:362):二维计数热力图;DFE 后的眼用 **CDR 恢复的 clock_times 折叠**(每次以时钟为中心切 2 UI),前级信号则用平均过零+固定 UI 折叠。
- **BER**:**bit-by-bit 直接计数**——`calc_ber()`(bert.py:580)将恢复比特与参考比特做互相关对齐(自动求 bit 延迟)后逐位比较。
- **浴盆曲线 / BER 外推**(`make_bathtub()`):抖动 PDF→PMF→CDF,中点归一后对右半折叠;**统计外推**:PDF 为零处用双 Dirac 拟合得到的高斯替换后再积分,从而把 ~1e4 bit 的仿真外推到 1e-12 量级(`MIN_BATHTUB_VAL = 1e-12`)。闭眼情形有专门的首尾 bin 处理避免中心伪尖峰。
- 注意:PyBERT **没有** StatEye 式的全统计(LTI 假设下 PDF 卷积)引擎——统计外推只作用于抖动浴盆;真正的统计域指标由 COM(PyChOpMarg)提供互补。
- **Viterbi/FEC**(bert.py:638–707):可选在 DFE 后接 `ViterbiDecoder_ISI`(以 DFE 输出脉冲响应的 UI 间隔采样构造期望观测,高斯噪声概率插值表做 emission,MLSE 消残余 ISI)或 `FEC_Decoder`(卷积码 Viterbi 硬判决),分别统计第二套 BER;trellis 及最优路径在 Results/Viterbi 页可视化。

### 3.7 EQ 优化(`threads/optimization.py::coopt()`)

Tx FFE × CTLE 峰化 × Rx FFE(× 理想 DFE)协同优化,两种模式:

- **MMSE 模式**(默认):外层穷举 CTLE 峰化与 Tx 抽头组合(`mk_tap_weight_combs()` 递归生成,>1e6 组合时拒绝);内层调用 **PyChOpMarg 的 `optimize.mmse(NoiseCalc, ...)`** 直接解出带限幅的 Rx FFE + DFE 权重与 FOM(SNR)。
- **穷举模式**:Rx FFE 也穷举,DFE 权重按 **PRZF**(`get_dfe_weights()`:取 cursor 后 n 个 UI 采样/主光标幅度,再限幅),FOM = 主光标幅度 / 残余 ISI 绝对值和。
- 结果回写 `*_tap_tuners`,GUI "Use" 按钮再拷入实际仿真参数——"tuner 与实际参数分离 + Reset/Use"是不错的交互设计。

## 4. IBIS-AMI 与 COM 支持

### 4.1 PyAMI(pyibisami)的角色

PyBERT 通过四个入口消费 pyibisami:

- `IBISModel(ibis_file_name, ...)`:解析 .ibs,提供 `zout/zin/ccomp/dll_file/ami_file`;
- `AMIParamConfigurator(ami_text)`:解析 .ami 参数树,`fetch_param_val(["Reserved_Parameters", "GetWave_Exists"|"Init_Returns_Impulse"|"Ts4file"|"Ignore_Bits"])` 决定运行模式,提供参数配置 GUI;
- `AMIModel(dll_fname)`:ctypes 加载 .dll/.so;
- `ami_parse`:解析 GetWave 返回的 AMI 参数字符串(取 DFE tap 时间演化、`cdr_locked`、`cdr_ui` 等)。

**运行流程**(`run_ami_model()`,utility/ibisami.py:26):

1. 校验模式与模型能力(GetWave_Exists / Init_Returns_Impulse);
2. `AMIModelInitializer(input_ami_params)`,设 `sample_interval`(必须先设)、`channel_response = chnl_h / ts`(**V/sample → V/s 的单位换算**,PyBERT 内部用 V/sample,AMI 规范用 V/s)、`bit_time`;**FEXT 扩展**:把 thru + 各 aggressor 冲激响应拼成多行矩阵传给 AMI_Init(设置 `row_size`/`num_aggressors`),让 AMI 模型可做串扰消除——与时域直接叠加的物理串扰互补而非重复计算;
3. `model.initialize(...)` 即调 **AMI_Init()**;收集参数与消息(容错处理 DLL 返回空指针);
4. `model.get_responses(bits_per_call=40)` 返回模型自身冲激响应与级联输出响应;
5. Init-only 模式:输出 = `convolve(x, out_h)`(LTI 假设);GetWave 模式:`model.getWave(x, bits_per_call)` 逐块调 **AMI_GetWave()**,返回波形、**clock_times** 与逐块输出参数。
6. Rx GetWave + `rx_use_clocks` 且拿到有效 clock_times 时,PyBERT 跳过自身 CDR,直接用 AMI 时钟(+UI/2 边沿对齐)采样判决(bert.py:537–563),仅用 dummy DFE 做电平→bit;`Ignore_Bits` 用作锁定门限。

### 4.2 PyChOpMarg 的角色

两处使用:

- **COM 计算**(`models/com.py::calc_com`):`COM(com_params, {"THRU": [...], "FEXT": [...], "NEXT": [...]})` 后调用 `com()` 返回 dB 值。参数默认 `IEEE_8023dj`,或读 IEEE 官方 COM 配置 xls;在仿真主流程末尾运行(可能耗时数分钟),失败置哨兵 −999.0。
- **MMSE 优化器复用**:`pychopmarg.optimize.mmse` + `pychopmarg.noise.NoiseCalc`。

这样 PyBERT 同时给出 time-domain BERT 视角与 **IEEE 规范统计视角(COM)** 的双重评估。

## 5. 典型工作流与配置体系

- **GUI 流**:`pybert` → 建 `PyBERT()`(构造即跑一次初始仿真)→ `configure_traits(view=traits_view)`;Run 按钮起 `RunSimThread`;Optimizer 页调 tuner → Tune → Use;File 菜单存取 YAML 配置与 `.pybert_data` 结果(旧结果作参考曲线对比叠画)。
- **无头/批处理流**:`pybert sim config.yaml [-r out]`;或作为库:`PyBERT(run_simulation=False, gui=False)` + 设属性 + `simulate(update_plots=False)`。
- **配置体系**:`PyBertCfg` 白名单字段 + YAML dump(可读、可 diff、可脚本生成扫参);向后兼容旧字段名映射;测试覆盖往返一致性。
- **测试**:19 个 pytest 文件(约 2000 行),参数化 fixture 对整条 `simulate()` 冒烟;覆盖信道导入、复合信道、多 lane S 参数、IBIS-AMI 三种 Rx 模式(带示例模型)、jitter、优化器、COM、FEC/Viterbi、CLI。配 pytest/pylint(fail-under 9.0)/mypy/ruff。
- **文档**:Sphinx + GUI 内嵌 Help + GitHub wiki;`misc/` 有 DFE 建模、抖动分解、库用法等推导性 notebook——算法出处可追溯性很好。

## 6. 优点与局限

**优点**

1. **算法出处清晰、工程正确性讲究**:Johnson 传输线模型、广义 S 参数端接、`interp_s2p` 的保守外推、V/sample vs V/s 单位纪律、`trim_impulse` 能量准则、抖动分解的图样平均+谱阈值+双 Dirac 三层方法,都有注释/notebook/文献支撑。
2. **覆盖面完整**:从场求解器插槽(Simbeor)→ S 参数 → AMI → EQ → CDR → MLSE/FEC → 抖动/BER/COM,一个仓库贯通整个链路仿真栈,且 NRZ/duobinary/PAM-4 三调制。
3. 工程化程度较高:类型注解 + mypy、lint 门槛、较全的测试、YAML 配置迁移、线程可中断。
4. GUI/内核虽同类但可无头运行,可当库使用。

**局限**

1. **架构耦合**:`PyBERT` 是 God-object,`my_run_simulation(self)` 靠动态 `setattr` 在实例上堆几十个结果属性,模块间契约靠命名约定;`utility/__init__` 的 `import *` 使命名空间扁平模糊。
2. **性能**:核心是 Python for 循环——`DFE.run_dfe()` 逐样本(nbits×nspui 次迭代)、`calc_eye()` 双重循环、Viterbi 纯 Python;numpy 精确钉死;单线程无向量化 JIT。默认 15k bits 尚可,百万 bit 级会很慢。
3. **模型深度**:CDR 无显式相位噪声/传递函数参数化;Rx FFE 运行时不自适应;无显式全统计(StatEye)BER 引擎,1e-12 结论依赖双 Dirac 外推假设;时域信道导入保真度受限;FEXT 假设 aggressor 与 victim 同图样(最坏情况而非统计)。
4. GUI 栈(Traits/Chaco)较老,依赖版本全部精确锁定,环境脆弱。
5. 抖动分解对 pattern 长度/eye_bits 有硬性要求,配置不当会直接抛 ValueError。

**适用场景**:教学、SerDes 架构探索、AMI 模型冒烟验证、通道快速评估与 COM 复核;不适合作为高吞吐量产签核仿真器直接使用。

## 7. 对自研 SerDes 链路仿真项目的可借鉴之处

1. **管线组织与"响应四件套"**:每级维护 `h/s/p/H` 及累积 `out_*` 版本(`calc_resps()` 统一派生),配合逐级观测点(chnl/tx/ctle/ffe/dfe)的抖动分解与"逐级抖动抑制表",是非常好的调试/报告范式,值得照搬。
2. **端接与 S 参数处理手法**:用复数端口阻抗 renormalize + `s21·sqrt(z0_L/z0_S)` 得电压传函,避免手推反射级数;`interp_s2p()` 的保守外推规则;`se2mm/sdd_21` 的端口错序自动检测;多 lane 端口约定与 `import_fext()` 的子网提取——这些是自研工具最容易出错的地方,PyBERT 给了经过实战的参考实现。
3. **抖动分解算法**(`calc_jitter`)可直接移植:图样平均分离 DDJ(得 ISI/DCD)→ 谱阈值分离 Pj/Rj → 双 Dirac 拟合 → 用拟合高斯外推浴盆。漏沿填充 ±3UI/4 进边界 bin 的闭眼处理细节也很有价值。
4. **DFE/CDR 行为模型模式**:CDR 封装在 DFE 内、以 clock_times 驱动一切下游;sign-sign LMS + n_ave 平均 + 锁定门控 + 每抽头限幅;bang-bang 3 采样鉴相 + 迟滞锁定检测——作为行为级基线简洁完备。
5. **IBIS-AMI 集成协议**:Tx/Rx × Init/GetWave 四组合的显式展开、V/s↔V/sample 换算、Ignore_Bits/clock_times 的使用、FEXT 多行矩阵传入 Init——自研工具接 AMI 时这些细节全是坑,PyBERT+PyAMI 是现成的对照答案。
6. **优化器分层**:线性 EQ 用 MMSE 解析解(借 PyChOpMarg)+ DFE 用 PRZF,外层只穷举低维离散量;tuner 对象与实际参数分离,Reset/Use 双向拷贝的交互模式。
7. **生态拆分策略**:把 AMI 驱动(PyAMI)与规范合规计算(PyChOpMarg/COM)拆成独立包、以 submodule+uv workspace 联调、PyPI 发布解耦——自研项目同样建议把"标准合规内核"与"仿真器外壳"分库。
8. **反面教训**:仿真内核不要以 GUI 模型类为数据总线(应定义显式的 SimConfig/SimResult 数据类);热循环(DFE、眼图)应尽早向量化或 numba/C 化;配置序列化从第一天就用带版本迁移的白名单 YAML。

---

# 第四部分:三仓库综合对比与对本项目的建议

## 1. 三者互补关系

- **算法学习路径**:serdespy(最小实现,看懂概念)→ PyBERT(工程实现,看懂细节与坑)→ DragonPHY2(RTL 实现,看懂硬件化)。
- **信道建模**:三者殊途同归——都以"S 参数/解析模型 → 差分电压传函 H(f) → IFFT → 冲激/脉冲响应"为主干。serdespy 的 `four_port_to_diff` 最易读;PyBERT 的广义 S 参数 renormalize 手法最严谨(且处理了外推、端口错序、多 lane 等实战问题);DragonPHY2 的 `sparams.py::s4p_to_impulse` 服务于 FPGA 综合的定点化。
- **均衡与判决**:serdespy 有 ZF-FFE 闭式解与 LMS 教学实现;PyBERT 有 MMSE(经 PyChOpMarg)与 sign-sign LMS DFE + MLSE;DragonPHY2 给出并行硬件 FFE、误差重构与低成本"错误事件检测"(简化 MLSD)的 RTL 答案,并示范了"片外自适应 + 片上数据通路"的分工。
- **BER 方法论**:蒙特卡洛(serdespy)→ 抖动外推浴盆 + COM(PyBERT)→ 片上 BIST + FPGA 实时仿真(DragonPHY2),三种手段分别对应算法探索、行为签核、硅前/硅后验证阶段,一个完整的自研项目最终三者都需要。

## 2. 对 Halo_Serdes 的具体建议

**若目标是 Python 链路仿真器**:
1. 以 PyBERT 的管线分段(chnl/tx/ctle/ffe/dfe 各带 h/s/p/H 四响应)+ serdespy 的清爽分层为骨架,但用显式 `SimConfig`/`SimResult` 数据类替代 God-object;
2. 直接移植/改写:PyBERT `utility/sparam.py`(S 参数处理)、`utility/jitter.py`(抖动分解)、`make_bathtub`;serdespy `forcing_ffe`(扩展成 MMSE)、KP4/KR4 FEC 管线;
3. 热循环(DFE/CDR/眼图/Viterbi)从第一天用 numba 或向量化;
4. 增加三家都缺的**全统计引擎**(LTI 假设下逐 UI PDF 卷积,StatEye 式),与时域蒙特卡洛互为校验;
5. IBIS-AMI 支持直接依赖 pyibisami,COM 直接依赖 pychopmarg,不重复造轮子。

**若目标是数字 RTL/硬件化**:
1. 参考 DragonPHY2 的 YAML→SV package 参数单源化、buffer/flatten 并行对齐组件、comb_ffe/mm_cdr 模块划分;
2. 建立"Python 黄金模型 ↔ RTL 逐点断言"的测试范式(每个 RTL 模块对应一个 numpy 参考函数);
3. 寄存器/JTAG 采用"单一真源自动生成"(SystemRDL 或 md 表驱动);
4. 与 DragonPHY2 不同,建议把自适应引擎(至少慢速 FSM 版 LMS)纳入片上规划;
5. 方案迭代时保持干净的模块替换边界,避免 dsp_backend/datapath_core 双代并存、信号悬空的技术债。

## 附:仓库获取方式

```bash
git clone https://github.com/richard259/serdespy.git
git clone https://github.com/StanfordVLSI/dragonphy2.git
git clone https://github.com/capn-freako/PyBERT.git
```
