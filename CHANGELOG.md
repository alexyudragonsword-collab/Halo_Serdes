# 更新日志 / Changelog

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，按里程碑而非
逐条提交组织。项目尚未发布正式版本（`pyproject.toml` 中为 `0.0.1`），下列条目按
完成顺序排列。

---

## [未发布] — 串扰 / 漂移 PDF 的卷积按网格中心对齐(ROADMAP P2 #36)

### 修复
- 统计引擎把每个 aggressor 的 ISI PDF(同步 lane、准同步漂移的相位平均、PR 事件投影的两处)与 COM 的 A_ni PDF 用 `np.convolve(mode="same")`
  并进受扰 lane 的 PDF,4096 / 8192 点的偶数网格上每卷一次中心晚一格:耦合为零(−120 dB)的 aggressor 每个让统计 SER 多 0.2–0.6 %,
  8 个多 8 %(NRZ)/ 22 %(PAM4,平信道 + AWGN,SER 1e-6 / 5e-5 处)、COM 少 0.25 dB。五处都换成 `_convolve_centred`(P2 #35 已给 PAM6 的
  新 PDF 用的那个),改后逐位等于没有 aggressor;奇数长度的高斯噪声核不动(`same` 对它们是对的)。

### 变更(数会动)
- 只动带串扰的统计读数与 COM;指纹 980 个值 0 变(预设里没有串扰)。示例 24 统计 BER −40 … −18 dB:6.41e-32 / 3.40e-26 / 2.73e-21 / 6.05e-16 /
  4.07e-8 / 3.90e-3 → 5.42e-32 / 3.09e-26 / 2.61e-21 / 5.54e-16 / 3.94e-8 / 3.89e-3(两个 aggressor,越深的尾部差越多,两引擎 −18 dB 处 0.71×);
  示例 28 的 COM 1 … 12 个 aggressor 各高 0.03–0.06 dB(3.42 → 3.45、−8.50 → −8.45、−10.25 → −10.19 dB);示例 44 统计 / 时域 同步 24 次 0.44–1.15× → 0.44–1.11×、准同步 0.72–0.95× → 0.70–0.93×(统计读数低 1–4 %,时域不变)。
- 测试 +3:平信道上 1 / 8 个耦合为零的 aggressor 与 8 个准同步 aggressor 的统计 SER 逐位等于无串扰(NRZ / PAM4)、COM 同;
  `_convolve_centred` 对 `same` 的中心对照(含奇数核不受影响)。

---

## [未发布] — PAM6 的成对符号:±1 光标的 ISI 按搭档条件分布(ROADMAP P2 #35)

### 修复
- 统计引擎把 PAM6 的符号当独立电平:corner32 的搭档永不把外层配外层,±1 光标的 ISI 与所发符号相关。混合信号 16 GBd 无 DFE 时那两个
  光标就是 ISI 的主体,统计读高 1.45–3.9×(越短越差);喂独立同分布符号时时域 SER 升 1.6–2.8×、正落在统计读数上(0.10 m 1.34e-3 对 1.33e-3)。
  现按 `core.mapping.neighbour_conditional`(搭档在对的第一位置是右邻、第二位置是左邻,各一半)逐电平建 ±1 光标的 ISI,PR 事件投影仍用
  独立符号的 PDF。三格 SER 1.04–1.19×、BER 1.30–1.37×(余下是按符号的比特矩阵,P2 #33);带 DFE 的格与 ADC 收端不动。
- 新 PDF 的卷积按网格中心对齐(`_convolve_centred`):`np.convolve(mode="same")` 在偶数网格上把中心放晚一格,平信道闭式测试上差 4–7 % 的 SER。
  既有的串扰 PDF 卷积仍用 `same`(改会动串扰预设的指纹,另记 P2 #36)。

### 变更(数会动)
- NRZ / PAM4 / PAM8 三引擎逐位不变(指纹 980 个值里只有合成 PAM6 链路的 2 个 1e-16 量级的数动)。
- 登记 #35 时写的"bang-bang 锁定相位对陡浴盆"是猜测,实测两引擎锁定相位一致、冻结时域读数不变,已更正(`cairn/448G调制格式.md` §7)。
- 测试 +3:`neighbour_conditional` 表、`_pair_isi_pdfs` 按占用加权回到独立模型、条件化后统计 SER 比独立模型低 2–3.5×(不跑时域)。
- 示例 00–46 不变:ADC 收端的 FFE 把 ±1 光标压平,示例 46 的 PAM6 统计列一位不动(本地一次用未对齐的中间版本跑出 0.01–0.03 dB 的假漂移、
  一度 accept 进库,CI examples-full 把它抓了回来;已还原)。

---

## [未发布] — 448G/lane 的调制格式比较:PAM6 / PAM8 进字母表,示例 46(feat/448g-formats)

### 新增
- `modulation ∈ {nrz, pam4, pam6, pam8}`:字母表只在 `core/mapping.py` 一处 —— 均匀电平,PAM8 反射 Gray,PAM6 **corner32** 对映射
  (5 bit 装进两个符号,36 对去掉四个角对,字按与网格邻对的汉明和贪心分配,角对解码时向内移一格;`tx.pam6_map`)。`LinkConfig.n_levels` /
  `bits_per_symbol`(PAM6 2.5),码型比特数走 `bits_needed()`,`prbsNq` / `prqs10` 在 pam6 / pam8 上拒绝。
- 比特计数:时域 / 静态引擎按格式精确(`count_bit_errors`:PAM6 按对、对以发端索引奇偶锚定,周跳重对齐后对仍是整的;PAM8 Gray 距离);
  `fec/interleave.bit_errors` 同口径。统计引擎带电平占用(corner32 外层 1/8、内层 3/16)与比特距离矩阵(PAM6 为按搭档与位置平均的 B6):
  ISI PDF、SER 求和、DFE 误码传播、眼图混合、PR 逐符号判决链、发端边沿抖动分箱、bang-bang 跳变类别;`analysis.metrics.pamN_ser_awgn`。
- `analysis/format_compare.py`:FEC 开销下的波特率、半间距罚、赢家 / DAC 位数 / 发端噪声容限三条读数规则。
- 示例 46 `448g_formats`:448 Gb/s 净吞吐、KP4 与 KP4+BCH 下 PAM4 237 / PAM6 190 / PAM8 158 GBd,各自在统计引擎上扫收端(21 抽头 ×
  {delta、1 + aD K = 1、1 + aD + bD² K = 2} + 11 抽头 delta),选出的格上时域验证、三种子方向检查,DAC 位数与发端噪声容限表,三格式浴盆与交叉点。
  结论:三格式都选 1 + aD + bD² K = 2;KP4 时域 reach PAM4 20.44 / PAM6 19.65 / PAM8 19.38 dB @ 56 GHz,PAM4 赢 0.79 dB、省 0.70 pJ/b,3 种子方向一致;
  只看 delta 收端 PAM8 领先,PAM4 靠两光标 PR + 16 状态 DFSE 多 +4.25 dB 翻盘;DAC 位数 6 / 7 / 7;KP4+BCH 下 PAM4 的统计 reach 时域未确认(判决导向环路失锁)。
- 测试 +59(1136 → 1195 passed / 1 skipped):`tests/test_mapping_formats.py`(19)、`tests/test_statistical_formats.py`(18,含 pam6 / pam8 在混合信号与 ADC 收端的铁律 3)、
  `tests/test_format_compare.py`(5),power / jitter / interleave / api_json / ms_envelope / mm_pd_input / cycle_slip 各加参数。
  指纹加两条合成 PAM6 / PAM8 ADC 链路(1 + aD,K = 1,三引擎;静态引擎行跑 delta 目标):860 → 980 个值,原 860 个 bit-identical。
- 新专题 `cairn/448G调制格式.md`;USAGE §1 / §10;SUMMARY §04 小节。

### 修复
- `run_time_link` 在 pam6 / pam8 混合信号"包络未核实"的警告分支里 `return` 了 None(只收集警告的测试没看见);改成 if / else,测试断言有结果。

### 变更(数会动)
- NRZ / PAM4 三引擎逐位不变:指纹原 860 个值 bit-identical(加合成链路前比对);示例 00–45 零漂移。
- 铁律 3 在新格式上:ADC 收端 PAM6 / PAM8 0.96–1.20×;混合信号 PAM6 带 DFE 0.55–0.67×、无 DFE 1.45×(更短链路上到 3.9×,P2 #35)、
  PAM8(需 CTLE)0.53–1.07×;示例 46 选出的格上统计 / 时域 PAM4 0.57–0.91×、PAM6 1.01–1.41×、PAM8 0.81–1.10×。
- 混合信号包络 `ms_envelope()` 只有 NRZ / PAM4;pam6 / pam8 走混合信号只警告"未核实",硬上限 30 GBd 仍生效(P2 #34)。
- ROADMAP:P2 #31 / #32 暂停(用户决定);新登记 P2 #33 / #34 / #35、P3 #12。开工 prompt 里"删 #20 / #37"不适用:仓库没有 #37,
  现有 #20 是已清空的 PAM4 0↔3 计数条目,不动。

---

## [未发布] — PAM4 猎振的数据噪声;门限放在接收机设电平处(ROADMAP P2 #30)

### 修复
- bang-bang 环路模型把 PAM4 偏离锁定点的跨号跳变(±1 ↔ ∓1/3)的检测器输出当白噪声全功率算。那份输出由数据定(大幅到小幅 +1、反之 −1),
  相邻跳变共用一个符号:随机数据上低频密度只有总功率的一半(方差 1/4、滞后 1 协方差 −1/16),PRBS 码型上一周期内正好抵消、只剩码型谱线。
  `cdr.linear.bb_data_noise` 按逐对电平的平均输出算环路看到的部分,`bb_loop_fixed_point(mean_output=, period=)` 在迭代里替换。PAM4 32G 的猎振
  模型 / 时域 1.11–1.23 → 0.97–1.01;NRZ 逐位不变。
- 平滑时的 PAM4 门限改为放在接收机设电平的相位:混合信号时域接收机的电平取在脉冲峰值(bang-bang 锁在其后 2 个样本),ADC 与 `phase_ui`
  读数在读点。PAM4 32G 五个深 BER 点(37–2349 个错)1.09–1.40× → 0.94–1.15×(门限放在锁定点时 0.70–0.95×)。
- 没有 DFE 的 PAM4 链路上平滑时的门限保持没生效(整样本相位按"没有可保持的"跳过,平滑宽于两个样本时整条曲线都没重算)—— 改为有 DFE
  或多电平时都重算。

### 变更(数会动)
- 时域、静态引擎逐位不变;统计引擎只有带平滑的 PAM4 混合信号读数变,NRZ、ADC 逐位不变。
- 指纹:860 个值中 4 个变,都在 PAM4 32G 混合信号预设:统计 BER 2.2e-9 → 3.3e-10、SER、浴盆最低点、平滑 σ 0.031 → 0.026 UI。
- 白时钟 PAM4 32G(≥ 100 个错)1.03–1.13 → 1.04–1.12×,误码少的点 1.01–1.53 → 0.81–1.10×;交叉校验 PAM4 1.04–1.07 → 1.01–1.04×。
- 示例 44 / 45 的 PAM4 32G 列变,其余 44 个不变:44 同步 0.90–1.10 → 0.96–1.06×、准同步 0.72–0.96 → 0.72–0.95×;45 KP4 窗口 0.321 → 0.363 UI、
  minmax / 单 lane 2.94 → 1.83;skew 对回放游走的 lane PAM4 32G 1.05–1.73 → 0.71–0.99×。
- 测试:`tests/test_bb_data_noise.py`(4 项);`test_bb_white_wander` PAM4 猎振界 1.0–1.35 → 0.9–1.15;`test_skew::test_phase_ui_reads_the_bathtub_where_it_is_defined`
  改为 NRZ 上 `phase_ui=0` 等于默认读数、PAM4 上默认读数更高(门限在峰值对锁定点);`test_ffe_tracks_wander` 的混合信号冻结参照改为电平取峰值。

### 更正
- ROADMAP P2 #30 登记时的主因猜测"高斯尾部读高"不对:实测相位分布换进曲线只差 0–15 %,冻结时域对照曲线本身对得上。§28 里"PAM4 偏高是 BB 极限环
  不是白噪"也不对(NRZ 同一模型 1.02–1.07×)。

### 新登记
- ROADMAP P2 #31:混合信号时域接收机的 PAM4 电平不自适应、定在脉冲峰值(比定在锁定点 BER 高 1.3–1.8×)—— 要不要加电平自适应,待定。
- ROADMAP P2 #32:NRZ 16G 自由运行时相位与码型相关,统计读低 0.5–0.7×(同一相位上误码是冻结时的 1.3–1.6×);同链路 kp_shift 8 失锁不报警。

---

## [未发布] — 判决门限跟抽头一起保持;LMS FFE 进环路模型(ROADMAP P2 #29)

### 修复
- 平滑采样相位时 PAM4 的判决门限按每个相位自己的主光标重放,等于每个相位做一次电平适应;时域的电平在起始解定下后不动。现在平滑经过的
  相位,门限停在读点相位的主光标(DFE 抽头已经停在那里,P2 #27 / #28)。冻结时域停在锁定点外对照:示例 31(2.8 mV)早侧 1–2 个样本
  0.70–0.74× → 0.95–1.10×;PAM4 32G 预设(6 mV,时域的 DFE 抽头与电平取锁定点)0.42–0.79× → 1.00–1.19×。NRZ(门限在 0)不变。
- ADC 收端的 LMS FFE 沿定时导数跟走一部分采样偏移,统计引擎把它当固定的。现在作为环路模型里的第二条跟踪路径:每次环路更新跟走剩余偏移的
  γ = n_lanes·mu·λ'(λ' = w'ᵀRw'/w'ᵀw',w' 是时域起始 FFE 解在峰值 ±1 个样本处之差,`statistical._ffe_lms_track`;示例 31 mu 1e-3 时 2.4e-5,
  时域 CDR 误差拟合 3e-5)。检测器读 FFE 输出时并联在环路旁(D 加 γ(z − 1)z^lat,无延迟时等价 kp 加 γ/K),读原始样本时是环路之后的高通
  (z − 1)/(z − 1 + γ)。示例 31 链路 kp_shift 4–9、SSPLL / CPPLL、mu 0 / 1e-3 / 1e-2、12 个种子:0.68–1.61×(mu 1e-2 kp9 此前 ~12×),
  模型 σ / 实测 CDR 误差 0.93–1.01。示例 31 自己的配置 16 个种子 kp 8–9 0.89–1.07×(此前 12 种子 0.69–0.85×)。

### 新增
- `LoopParams.ffe_track` / `ffe_in_loop`、`LoopSolution.ffe_track`;`error_response` / `tracking_response` / `ErrorResponse` 在 `ffe_track` > 0 时走
  通用的多项式与留数(`_polys`,N − D 分开构造;单位圆上按 z − 1 算,z ≈ 1 附近多项式相减会把低频 |E| 算错 1e2–1e3 倍)。`ffe_track` = 0 时逐位不变。
  GUI 抖动面板的 |E| 曲线带上 FFE 那份。

### 变更(数会动)
- 时域、静态引擎逐位不变。统计引擎:PAM4 带平滑的读数(白时钟混合信号、剖面时钟)、LMS FFE 的 ADC 剖面时钟。
  P2 #27 那组白时钟对时域(≥ 100 个错):PAM4 32G 0.99–1.01 → 1.03–1.13×,NRZ 不变,交叉校验 1.00–1.07× 不变。
- 指纹:860 个值中 4 个变:PAM4 32G 混合信号预设的统计 BER / SER(7.6e-11 → 2.2e-9 —— 这么深的 BER 几乎全来自平滑尾部,门限保持在尾部影响最大)、
  合成链路 `adc_dsp/profile` 的统计 BER / SER。
- 示例 31 / 44 / 45 的统计列变,其余 43 个不变:31 统计 BER 高 1.1–1.25×、kp 7–9 的模型 σ 低 2–4 %(说明文字更新);44 PAM4 32G 同步 0.89–1.09 →
  0.90–1.10×、准同步 0.72–0.95 → 0.72–0.96×;45 PAM4 32G KP4 窗口 0.360 → 0.321 UI、minmax / 单 lane 1.73 → 2.94;skew 对回放游走的 lane
  PAM4 32G 0.74–1.04 → 1.05–1.73×(早侧 1.3–1.7×,见新登记),NRZ 不变。
- 测试:`tests/test_ffe_tracks_wander.py`(12 项:闭式对环路逐次仿真 —— 两种接法、延迟 0–2;等价 kp;高通;γ 与 mu 成正比;示例 31 kp9 mu 1e-2
  的铁律 3 与 σ;示例 31 与 PAM4 32G 两条冻结门限对照);`test_skew` 文档串的范围更新。

### 更正
- ROADMAP P2 #29 登记时写的"冻结均衡器时 0.63–0.90×、余下是锁定点外曲线本身 0.6–0.8×"是中间代码上量的,方向也不对:曲线读低是门限重放;
  只保持门限时 kp 8–9 自适应读高 1.07–1.31×,那才是 FFE 跟走的一份。

### 新登记
- ROADMAP P2 #30:平滑相位的分布 —— PAM4 32G 猎振的模型 σ 是实测的 1.19×、实测分布 2.5σ 外比高斯重,深 BER 与 skew 早侧读高 1.3–1.7×;
  示例 31 冻结均衡器读低 0.68–0.88×。

---

## [未发布] — 剖面时钟:运行的 σ、样本之间的曲线、DFE 保持(ROADMAP P2 #28)

### 修复
- 剖面时钟的平滑 σ 是环路的稳态;时域一次运行从静止起步、只有 `sim.n_symbols` 个 UI,欠阻尼环路(示例 31 kp_shift 7–9,误差响应在
  1.1 MHz 附近尖峰 4–16×)在运行里振铃不起来 —— 稳态读 16 种子时域系综 RMS 1.14 / 1.34 / 1.70×。现在按运行算:剖面放在运行的谱线 k/(N UI) 上
  (时域合成器就放在那里)、环路从静止起步、取时域计分的窗口,闭式(`cdr.linear.ErrorResponse.run_mean_sq`,极点展开 + 几何级数;
  `ClockProfile.run_untracked_sigma_s`),对系综 1.00–1.04×。误差在运行中长(kp9 0.36 → 0.90 个样本),读数取运行中 12 个点的高斯读数的平均
  (`LoopSolution.sigma_over_run_ui`)。
- 平滑小于两个样本时,保持抽头的曲线在整样本之间线性插值读高 1.6–3.6×(冻结时域停在 ±0.25…±0.75 个样本对过)。现在在样本之间真算:
  每 1/4(平滑 < 1 个样本)或 1/2 个样本(< 2 个)加算一次相位,脉冲游标三次卷积插值(`_pulse_at`),其余逐相位的量取最近整样本;读数仍对曲线线性。
  没有 DFE 时同样适用。
- 剖面时钟也在平滑时保持 DFE 的抽头(P2 #27 只给白时钟)。
- 结果:示例 31 的链路 kp_shift 4–9、SSPLL / CPPLL、12 个种子:统计 / 时域 0.66–1.40×(此前 0.24–1.42×)。

### 新增
- `LoopSolution.sigma_steady_ui`(环路振铃起来后的 σ)、`sigma_over_run_ui`;稳态超过运行 1.25× 时警告"the CDR loop rings";
  GUI 抖动面板加一行;示例 31 加 steady 列。`cdr.linear.ErrorResponse`(`run_mean_sq` / `run_sq_at`)。

### 变更(数会动)
- 时域、静态引擎逐位不变。统计引擎:剖面时钟全部、白时钟里平滑小于两个样本的(混合信号预设的 BB 游走、ADC 收端 RJ)。
  P2 #27 那组白时钟对时域(≥ 20 个错):NRZ 16G 0.50–0.89 → 0.45–0.89×,PAM4 32G 0.99–1.03 → 0.99–1.01×,NRZ 28G 0.75–1.12 → 0.67–1.08×,
  交叉校验 1.00–1.09 → 1.00–1.07×。
- 指纹:860 个值中 10 个变,都在统计引擎:三个混合信号预设、合成链路 `adc_dsp/profile`(BER 与 σ)与 `mixed_signal/profile` 的 σ。
- 统计单跑:示例 31 剖面时钟 0.24–0.25 → 0.37–0.42 s,混合信号预设 0.33 / 1.33 → 0.44 / 1.74 s(样本之间加算的相位)。
- 示例 31 的实测列改为整个计分窗口(与模型同口径),模型列是运行的 σ,加 steady 列;统计 BER 在 kp_shift 8 / 9 对本种子 1.2–1.6× / 0.8–1.4×。
- 示例 24 / 44 / 45 的统计列随之变:24 在 −18 dB 3.82e-3 → 3.90e-3(时域 5.51e-3);44 同步 0.67–1.15 → 0.44–1.15×(NRZ 28G 7/8 UI 那个浴盆太陡的点
  0.67 → 0.44,P2 #27 之前 0.42)、准同步 0.72–0.96 → 0.72–0.95×;45 PAM4 32G KP4 窗口 0.349 → 0.360 UI、minmax / 单 lane 1.92 → 1.73,
  NRZ 28G 20.56 → 10.05;skew 对回放游走的 lane 0.92–1.31 → 0.67–1.09×。其余 42 个示例不变。
- 测试:`tests/test_profile_run_smear.py`(闭式对线性环路的逐次仿真、阻尼 / 振铃两端、三次插值、示例 31 链路两点的铁律 3);
  `test_mm_loop_model_tracks_the_adc_kernel_on_the_112g_link` 改为六个种子的系综(单种子 0.32–1.02 个样本);
  `test_the_smear_holds_the_dfe_where_it_adapted` 改为同一批相位上比两条曲线。

### 更正
- 示例 31 与 `docs/clock_profile.md` 的"模型 / 实测 0.81–1.07×"是 seed 3 后半程对稳态的偶合;P2 #27 记的"同一剖面放到收端时域 0 误码"
  也是 seed 3(16 个种子收端 155 / 343 个错,σ 比发端大 5 %)。

### 新登记
- ROADMAP P2 #29:时域 LMS FFE 跟上剖面时钟的一部分慢游走(kp_shift 8–9 BER 低 1.6–1.8×),统计引擎不建。

---

## [未发布] — 白时钟上 bang-bang 环路的游走进统计引擎(ROADMAP P2 #27)

### 修复
- 白时钟(`kind: white`)时统计引擎不建 CDR,时域里混合信号的 bang-bang 环路却一直在猎(三个预设 0.025–0.062 UI)。陡浴盆上统计 / 时域
  (≥ 20 个错):NRZ 16G 0.11–0.54×,PAM4 32G 0.48–0.71×,NRZ 28G 0.53–0.98×。三处改动:
  - 白时钟的混合信号也走 `bb_loop_fixed_point`:接收端 RJ 过误差响应,发端 RJ 进检测器的边沿噪声(它让边沿样本随机;到判决器是 P2 #25 的逐沿噪声,
    不进相位轴)。NRZ 模型 / 实测游走 1.02–1.07×。
  - PAM4 的 Alexander 检测器按跳变分类:`engine.statistical._bb_transitions` 列出(概率、过零偏离锁定点、边沿噪声),
    `bb_loop_fixed_point(transitions=)` 的增益 Σ p √(2/π)/s · exp(−d²/2s²)。±1 ↔ ∓1/3 的过零离锁定点 0.14–0.15 UI,只带噪声;
    按 ρ = 0.5 全算时游走读 0.65–0.72×,改后 1.04–1.3×。NRZ 仍是一类,逐位同旧。
  - 白时钟平滑时 DFE 停在读点的抽头:环路比 sign-sign LMS 快得多(时域 DFE 步长 ×1 / ×10 / ×100 / 冻结都是 4.5e-4),平滑覆盖到的相位
    按读点的抽头再算一遍,残余后光标留作 ISI;读数是这条曲线在读点周围的高斯平均(线性插值,`_smeared_at`)。`ber_phi` 仍是每相位
    重新适应的浴盆。剖面时钟不这样做(见下方新登记)。
- 结果:NRZ 16G 0.50–0.89×,PAM4 32G 0.99–1.03×,NRZ 28G 0.75–1.12×;六条交叉校验 1.00–1.09×;PAM4 32G 预设噪声、发端 RJ 0.06–0.09 UI
  0.96–1.19×(此前 0.47–0.67×)。NRZ 16G 余下的是高斯近似(时域实际采样相位的分布比同 σ 的高斯读高 1.15–1.4×)。

### 变更(数会动)
- 时域引擎逐位不变。统计引擎在白时钟的混合信号上变(不带 FFE 时;带 FFE 时它替的是静态引擎,不加游走);ADC 白时钟不变(不建 MM 环路)。
  剖面时钟的读法不变。
- 示例 24 / 44 / 45 的统计列随之变:24 在 −18 dB 3.67e-3 → 3.82e-3(时域 5.51e-3);44 同步 0.42–1.28 → 0.67–1.15×、准同步 0.72–0.91 → 0.72–0.96×;
  45 混合信号两条曲线变陡,PAM4 32G 窗口 0.514 → 0.349 UI、minmax 最差 / 单 lane 0.17 → 1.92,NRZ 28G 1e-12 目标下没有窗口。
- `tests/test_skew.py` 的时域参照改为冻结环路 + 回放一次自由环路(另一种子)的游走:统计 / 时域 0.92–1.31×(对静止的 lane 1.2–48×)。
- 统计引擎单跑 0.10–1.20 s → 0.19–1.65 s(平滑覆盖的相位多算一遍)。
- 指纹:860 个值中 16 个变,都在统计引擎、都是白时钟的混合信号:三个混合信号预设、"NRZ 32G static"(静态引擎用的预设,时域不锁、
  BER 0.49,统计 0.096 → 0.105)与合成链路 `mixed_signal/white` 的 `jitter_sigma_ui`(0 → 0.018 UI)。时域、静态引擎逐位不变;剖面时钟的值不变。
- 测试:`tests/test_bb_white_wander.py`(检测器的跳变类别、模型对时域游走、三条链路 2 种子 × 20 万符号的铁律 3、平滑下 DFE 保持);
  `test_clock_profile_cdr.py` / `test_rx_clock.py` / `test_tx_edge_jitter.py` / `test_crosstalk.py` 的界与文档串按新口径。

### 新登记
- ROADMAP P2 #28:剖面时钟下 DFE 保持的平滑读高 6.6–15×(示例 31),时域均衡器同样不跟、保持抽头的曲线本身对、同一剖面放到收端时域
  0 误码 —— 原因未定,剖面时钟暂按重新适应读。

---

## [未发布] — MM 定时函数在锁定点附近见顶:报拐点距离、按环路游走警告(ROADMAP P2 #26)

### 新增
- `cdr.linear.mm_hold_range_ui`:Mueller-Muller 检测器的定时函数从锁定点往外拉回力一直在长的距离(UI)—— 随机符号、与
  `mm_pd_statistics` 同一组游标 / 锁定点 / 固定 DFE 反馈(抽成 `_mm_cursor_set`,后者逐位不变),接收噪声的期望用 erf 闭式,
  一个样本一个样本往外走到拐点,抛物线求顶点,近的一侧,封顶半个 UI;锁定点处不上升为 0。约 30–50 ms。
- `LoopSolution.hold_range_ui` / `sigma_wander_ui`(检测器自噪声 + 带宽内跟晚的时钟;带宽外的抖动环路不跟,不能把它带过拐点)。
  统计引擎剖面时钟的 MM 分支填这两项,`hold < 8 × wander` 时警告(`_warn_if_mm_cannot_hold`)。GUI 抖动面板加一行。

### 背景
- 指纹的 53 Gb/s 合成链路:均衡把定时函数在锁定点压到零、0.42 个样本(0.026 UI)后见顶,顶点只高出零约 2e-4(低于模型能分辨的量,
  所以报拐点距离而不判零点);随机符号下时域环路漂走,线性模型读 0.035–0.117 UI 不警告。标定(SSPLL 剖面,PRBS7Q–15Q,kp_shift 4–10,
  4 个种子 × 20 万符号):2.8–6.9× 失锁、8.6× 起没有;随机符号 0.1–0.7×。ADC 预设 15–33×(随机符号),示例 31 的 12 个设置 15–37×
  (拿总 σ 比时 1.6–6.7× 全报警,而时域都锁着)。白噪钟不建环路模型,不查。
- 更正:`docs/clock_profile.md` 原说"PRBS13Q 下内核锁住" —— 白时钟下 4/4 锁住,剖面时钟下 kp_shift 6 有 1/4、8 起 3/4 失锁。

### 变更(数不动)
- 指纹 860 个值逐位不变,示例输出不变(示例 31 的环路都在 15× 以上,不报警)。测试:`tests/test_mm_hold_range.py`(三角脉冲解析核对、
  合成链路随机 / PRBS7Q 的警告与时域失锁 / 锁住、112 GBd 预设不报警且锁住)。

---

## [未发布] — 发端白抖动按逐沿噪声进统计引擎(ROADMAP P2 #25)

### 修复
- 统计引擎把发端 `tx.clock.rj_ui` 当采样时刻抖动,在相位轴上抹 BER(φ);时域引擎每个边沿各自移动,经信道是跟着跳变走的噪声。
  0.03–0.09 UI 下统计 / 时域 0.00–0.10×,示例 31 白时钟(200 fs)0.03×。改为 `engine.statistical.tx_edge_jitter`(→ `TxEdgeJitter`):
  按被采符号的前后邻居分箱(复用光噪声的 `pattern_sigma` 通道),该符号自己的两个边沿按阶跃响应精确算(快信道上线性化读低
  29×),其余线性化,逐相位;`noisy()` 里直接卷积(FFT 的舍入会给尾巴垫底)。相位轴只留接收端时钟的 RJ。

### 变更(数会动)
- 时域引擎逐位不变。统计引擎在发端 RJ > 0 时变:224G ADC 两个预设 0.69–1.39×(此前 0.01–0.10×),PAM4 32G 混合信号 0.47–0.67×
  (0.01–0.08×),NRZ 16G 0.38×(0.09 UI);示例 31 白时钟统计 BER 2.4e-7 → 4.5e-6(时域 6 种子 8.2e-6),其模型列改为 n/a。
- `extras['jitter_sigma_ui']` 只剩接收端 RJ(白噪钟发端 RJ 时为 0)。指纹 25 个值变,都在统计引擎(带发端 RJ 的预设与合成链路)。
- 示例 24 / 44 / 45 的统计列随之变(重跑并更新期望输出)。
- 测试:`tests/test_tx_edge_jitter.py`(分箱的边沿、小位移下的功率、两个引擎在重 RJ 下 2× 内);`test_white_kind_has_no_loop_model_and_smears_only_the_receivers_rj`、
  `test_white_receiver_rj_smears_the_statistical_phase_axis` 替换原先"发端与接收端 RJ 功率相加"的断言;电压格的跨度改为与 `phase_ui` 无关。

### 新登记
- ROADMAP P2 #27:白噪钟下混合信号 BB 环路自身的游走(0.025–0.062 UI)不在统计引擎里,是混合信号余下的差距。

---

## [未发布] — MM 环路模型:检测器读 DFE 之后,噪声按低频谱密度(ROADMAP P2 #24)

### 修复
- `cdr.linear.mm_pd_statistics`(统计引擎剖面时钟下 ADC 收端的 Mueller-Muller 环路模型)两处与内核不符:
  - 检测器读的样本里留着 DFE 消掉的后光标;内核读切片器输入、DFE 反馈之后。改为扣掉锁定点的 DFE(与 PR 目标的受控)光标、锁在峰值上
    (`n_feedback`)。112 GBd 链路上增益 0.0195 → 0.032 V/UI,冻结环路实测 0.0276。
  - 检测器噪声当白噪声用了逐符号方差。相邻输出的主光标在块内求和时抵消,慢环路积分的是低频谱密度,两条链路上都是方差的 1/5;
    码型重复时数据那份是重复率整数倍上的谱线(PRBS13Q 在 112 GBd 下从 13.7 MHz 起,在环路之外)。改为接收噪声的白底
    加码型的谱线(`PdNoise`,逐条过 `|H|²`),码型周期取自 `sim.pattern`(PRBS-N;PRBS31 / PRQS10 按随机数据)。
- `self_noise_ui` / `mm_loop_solution` 接受 `PdNoise`;`mm_pd_statistics` 改为卷积、返回 `(k_pd, PdNoise)`。

### 变更(数会动)
- 时域引擎逐位不变。统计引擎在剖面时钟 + ADC 收端下变:示例 31 模型 / 实测 0.97–1.39× → 0.81–1.07×,统计 BER 对时域
  kp_shift 8 处 1.1–1.7× → 0.4–0.5×、9 处 5–8× → 1.2–2.2×;112 GBd 测试实测 / 模型 0.75 / 1.03 / 0.80 → 0.98 / 1.23 / 0.96。
- 指纹 4 个值变,都在 `synthetic / adc_dsp/profile/stat`:采样抖动 σ 0.0202 → 0.0045 UI,BER 1.2e-130 → 0
  (该链路 kp_shift 6 下时域 40 万符号稳态 0.0036 UI + 未跟踪 0.0033 UI)。
- 测试:`test_mm_noise_is_what_a_slow_loop_integrates`(无 ISI 脉冲上 NRZ 2σ²、PAM4 2h0²var|a| + 2σ²,重复码型的谱线);
  112 GBd 测试参数化到 kp_shift 4 / 7 / 9、区间 0.85–1.3。

### 结论(更正)
- ROADMAP 原写"试过在 `mm_pd_statistics` 里扣掉 DFE 消掉的光标,更远,已回退" —— 那次是每个相位都扣;内核的 DFE 抽头适应得比环路慢,
  扣锁定点的固定值才对。
- 新登记 ROADMAP P2 #25(白时钟发端边沿抖动过信道,示例 31 白时钟 4–5 个错 vs 预期 0.1)与 P2 #26(MM 定时函数在锁定点后见顶时
  随机数据失锁,模型不报)。

---

## [未发布] — 统计单跑的 ADC 代用 FFE 就是时域起点(ROADMAP P2 #23)

### 修复
- `engine.cascade.initial_ffe_taps`(统计引擎单跑 ADC 收端的 FFE:示例 45、`analysis.skew`、应用的统计 study 与双引擎面板)是时域起点
  求解的一份拷贝,在不含发端 FFE 的脉冲上解;224G 预设上与时域起点差 7.5 %、读高 21×。起点求解抽成 `timedomain.adc_start`,
  时域引擎、`initial_ffe_taps`、`front_end_pulse` 与 `skew` 的锁定点共用;代用解与时域起点逐位相同,顺带用上 PR 目标与发端噪声协方差。

### 变更(数会动)
- 时域引擎逐位不变(指纹 860 值逐位相同)。统计单跑的 ADC 结果变:lane 冻结在偏移处统计 / 时域 14–25× → 0.9–1.5×。
- 示例 45:ADC lane 锁定点 1.0e-3 → 4.8e-5,KP4 门限下窗口 0 → 0.435 UI(FFE 重解)/ 0.156 UI(沿用锁定点的)。
- 测试:`test_standin_is_the_time_engines_start`(三个预设逐位比对);ADC 铁律 3 用例同时查代用解。

### 结论(更正)
- ROADMAP P2 #23 原写"时域 LMS 把 FFE 从起点挪得很远,代用解只是起点" —— 错:LMS 在 20 万符号里只动 0.2 %;差在代用解漏了发端 FFE。

---

## [未发布] — 静态引擎的 FFE 把后光标留给 DFE(ROADMAP P2 #22)

### 修复
- `run_static_link` 的 FFE 原先按 MMSE 连 DFE 的后光标一起压掉,DFE 几乎不用。改为 MMSE-DFE(`mmse_ffe(free_post=rx.dfe.n_taps)`),
  与 P1 #21 的时域起点同一规则;两种架构一样,正则项不加 ADC 噪声(静态引擎不量化)。

### 变更(数会动)
- NRZ 32G static 在 10 mV 下 2.56e-2 → 1.47e-3;指纹 72 个 `static` 值变(切片器 SNR 六个预设 +0.5…+2.6 dB,三个 −0.1…−0.5 dB)。
- 示例 01 / 03:切片器 SNR 15.1 → 19.3 dB,内眼 6.8 → 16.2 mV;系统框图(`tools/draw_system_diagram.py`)重画。
- 示例 04:MC 5.93e-4 → 4.95e-4,StatEye 对 MC 1.03× → 0.92×(README / SUMMARY / COMPARISON / 铁律 3 的基线数)。
- 测试:`test_ffe_leaves_the_dfe_its_postcursors`。

---

## [未发布] — ADC 收端的 DFE 起作用(ROADMAP P1 #21)

### 修复
- 时域 ADC 收端的 DFE 从不起作用:FFE 从不计 DFE 的 MMSE 起步,联合 LMS 的代价面沿"FFE 留 h1、DFE 消 h1"是平的,DFE 停在 ≈ 0。
  - 起点改为 DFE 感知:`dsp/ffe.mmse_ffe(free_post=)` 不计 DFE 管的后光标,有 DFE 时正则项加 ADC 噪声。没有 DFE 时起点不变。
  - 预编码 1 + D 复合判决例外(它的 DFE 反馈重建的线符号,错一个就一直错;DFE 用上后一条测试链路 246 → 5779 个误码)。
  - `engine.cascade.initial_ffe_taps` 同一规则。
- 统计引擎加 DFE 误码传播(第一抽头,两状态链 s / (1 − p + s),`extras['dfe_ep_factor']`):DFE 用上后理想 DFE 乐观 1.8–5×。

### 变更(数会动)
- 224G 106 GBd 预设 2.27e-4 → 4.0e-6;112 GBd stress、deep-LR DFE8 → 0 误码。指纹 136 个值变(ADC + DFE、带 DFE 收端的新键、
  高 BER 处的误码传播)。
- 示例 05 07 08 15 16 19 20 21 31 39 43 44 45 的输出变;19 / 20 / 21 的扫描加长到 reach 在内,43 的 DFE 剖析点挪到新门限(0.225 m)。
- 测试:`test_mmse_leaves_the_dfe_its_postcursors`、`test_adc_dfe_engages`;按新接收机重标的阈值(统计引擎长度扫描、GUI 噪声、
  skew 的 ADC 用例、串扰漂移恒等式按 `ber / dfe_ep_factor`)。

### 结论(更正)
- 深 LR 阶梯 31 → 33 → 36.3 / 38.5 → 42.8 dB 变为 31 → 36.0 → 40.5 / 44.2 → 47.8 dB:
  - DSP 深度 +1.8 → +4.7 dB,"深 LR 主要 SNR 受限、DSP 深度收益小"不再成立,与收端 PR 的 +5.0 dB 打平(每 dB 0.46 vs 0.48 pJ/b);
  - ADC +5.4 → +8.2 dB,级联 FEC +3.2 → +4.5 dB;两者合起来 +11.8 dB,比之和少 0.9 dB(此前多 1.1 dB)。
- 示例 08 定点字长墙 wl 5 → wl 7;默认 10 bit 不受影响。
- 示例 43:1 抽头 DFE 的误码不再孤立(每事件 1.07 → 3.05),FEC 映射对它的影响与 PR 同向(−1.0…+0.85 dB);收端 PR 对 DFE1 的
  优势 +4.98 → +2.13 dB,预编码 DFE 由亏转赚(+0.49 dB)。
- 双引擎:带 DFE 的 ADC 收端 0.81–1.25×;lane 冻结在偏移处、给定它收敛的 FFE 0.93–1.47×;准同步串扰 0.72–0.86×。

### 余项(记入 ROADMAP)
- P2 #22 静态引擎的 FFE 仍不计 DFE(6–14× 悲观)。
- P2 #23 统计单跑的代用 FFE 离时域收敛点很远(14–25×,示例 45 的 ADC 曲线因此到不了 KP4 门限)。
- P2 #24 DFE 用上后 MM 环路线性模型两端高估 CDR 误差 1.26–1.39×;示例 31 白时钟的发端抖动按采样时刻处理。

---

## [未发布] — 共享时钟的 lane 间静态 skew 预算(ROADMAP P3 #9c 静态部分)

### 新增
- `analysis/skew.py`:一个 CDR 的时钟采 N 条 lane 时各 lane 的 BER。
  - `lane_curve(cfg)`:lane 偏离自己锁定点时的 BER 曲线。每个偏移一次统计引擎,DFE、判决电平(ADC 还有 FFE)在那个相位上重新适应。
  - ADC 收端要给 `ffe_taps`:一组,或"采样相位 → FFE"的函数。
  - `LaneCurve`:读数(对数线性、过半 UI 回绕)、最低点、`window(BER 目标)`、`budget_ui`。
  - `shared_cdr(curves, skews, policy)`:时钟放法 master / average / minmax,给出各 lane 的 BER、最差与平均。
- `run_statistical(phase_ui=)`:在锁定点 + 偏移处采样,相位轴以那里为中心(主光标就是那里采到的)。
  `None` 走原路径;`0` 与原 `ber` 逐位相同。
- `engine.cascade.initial_ffe_taps(at_ui=)`:在偏离脉冲峰值处解 MMSE FFE;`front_end_pulse` 是它解的脉冲。
- 示例 45(`examples/45_shared_cdr_skew.py`):三条链路的曲线与窗口，四条 lane 三种时钟放法。

### 变更
- CI 测试 job 的超时 30 → 45 分钟:nojit 那两条在慢的 runner 上要 25–30 分钟，已经开始被截断。

### 结论
- 不是"读一次运行的浴盆":
  - ADC 的浴盆沿用锁定点的 FFE,偏 1/4 UI 读高 21×;
  - 均衡后的脉冲最大样本可能不是主光标，从浴盆挪着读会认错。
- 对时域(冻结环路停在偏移处，均衡器从那里的游标起步):
  - 混合信号 0.49–1.78×(PAM4 32G、NRZ 28G,−3/8…+1/4 UI);
  - ADC 给定时域 lane 在该偏移收敛的 FFE 时 0.98–1.26×(±3/8 UI);
  - 用 MMSE 起点代用 FFE 时 1.4–4.5×。
- PAM4 32G 混合信号(示例 45):
  - bang-bang 环路锁在浴盆底之后 0.19 UI;
  - 四条 lane 用 minmax 时钟时最差 5.3e-7,是单条 lane 用自己环路时(5.5e-6)的 0.09;
  - average 反而比 master 差(浴盆在晚的一侧陡)。

### 发现(未修，记入 ROADMAP P1 #21)
- 时域 ADC 收端的 DFE 不起作用:FFE 从不计 DFE 的 MMSE 起步,DFE 抽头停在 ≈ 0。
- 从 DFE 感知的起点出发:224G 106 GBd 2.29e-4 → 5.2e-6;带 DFE 的另两个预设误码 5 / 3 → 0。

### 测试
- +10:`tests/test_skew.py`
  - 曲线读数与回绕、窗口、三种放法、输入校验;
  - `phase_ui` 与原浴盆一致;ADC 不给 FFE 报错;ADC 重解 FFE 与沿用的差别;
  - 铁律 3:两条混合信号链路对冻结在偏移处的时域 lane;ADC 给定 lane 收敛的 FFE。

## [未发布] — 串扰进配置;准同步 lane(ROADMAP P3 #9b)

### 新增
- `LinkConfig.crosstalk`(`CrosstalkConfig`):受扰 lane 的串扰环境,铁律 1 的缺口(此前只能 API 入参)。
  - 一组合成 lane:`n_fext` / `n_next`,耦合 `coupling_db` ± `spread_db`,数据由 `seed` 生成。
  - 外加逐个列出的 `aggressors`(`AggressorConfig`):合成的，或用 `file` 从多端口 Touchstone 取耦合。
  - YAML、`apply_overrides`、表单(bank 那七项)都能写。
  - 时域(含流式)、统计、COM 都从它建 aggressor。显式 `xtalk=` / `xtalk_pulses=` 替换配置，两者都给时警告。
- 准同步 lane:`ppm` ≠ 0(`XtalkAggressor.ppm`、`crosstalk.symbol_index`)。
  - 时域按 aggressor 自己的速率保持它的符号，默认引擎与流式引擎同一个函数。
  - 统计引擎对 aggressor 相位求平均(PR 联合界的事件投影同样)。
  - 一次运行扫不过整 UI 时警告。
- `run_statistical(xtalk=...)`:传 aggressor 本身,ppm 才能生效;`xtalk_pulses` 仍按同步 lane 算。
- 示例 44(`examples/44_quasi_sync_xtalk.py`):三条链路，同步 8 个 skew 对准同步 ±100 ppm。
- `aggressor_bank` / `synthetic_aggressor` 加 `skew_s` / `ppm` 参数。

### 变更
- 静态引擎遇到配置串扰时报错(它不注入串扰)。
- 带重定时器的拓扑遇到配置串扰时报错(三条链路，说不清属于哪段)。
- GUI / 应用的串扰与多 lane study 先去掉运行配置里的串扰，再自己扫。
- 没有串扰时数值不变：指纹 846 值逐位相同。

### 结论(示例 44)
- 同步 lane 的时域 BER 在 8 个 skew 上差 8.9–32.2×。
- 准同步 lane 落在各 skew 平均附近：时域 BER 是同步平均的 0.80–0.97。
- 准同步时统计 / 时域 0.79–0.93×。

### 测试
- +18:`tests/test_crosstalk.py`
  - 配置建 bank 与列出的 lane;YAML 往返;校验(6 组)。
  - 配置与 API 逐位相同，两者都给时警告。
  - `symbol_index` 的走速;流式 hold;流式引擎配合准同步配置。
  - 一个 aggressor 的相位平均 = 各 skew 平均。
  - 两组链路准同步铁律 3。
  - 运行太短时警告;静态 / 重定时报错;COM 从配置取。

---

## [未发布] — PAM4 比特误码按 Gray 码不同的比特数计(ROADMAP P2 #20)

### 修复
- `symbol_checker` 原先按 min(|电平差|, 2) 数比特误码(与 serdespy 的 `prqs_checker` 相同),统计引擎的逐符号判决链也照此计。
  Gray 映射下 0(00)与 3(10)只差一个比特 —— 而 1/(1 + D) mod 4 预编码回绕出的误码正是 0 ↔ 3,所以预编码 PAM4 的 BER 高报 25 %
  (示例 43 的 1 + aD 预编码链路:2.51e-4 对 2.01e-4)。两处都改为按 `core.mapping.GRAY_BIT_DISTANCE` 计。
- 不预编码时几乎碰不到 0 ↔ 3:指纹 846 个值逐位不变(没有预编码的预设)。

### 变更(示例输出)
- 示例 40 只动两行预编码,结论不变:
  - 收端 a = 1:需要的 f_r 11.94 → 11.86 GHz(加 host Tx FFE 时 8.54 → 8.49);容忍的发端噪声 35.9 / 36.7 → 36.7 / 37.3 mV。
  - 发端 a = 1:需要的 f_r 17.30 → 17.10 GHz(加 host Tx FFE 时 11.56 → 11.41);容忍的发端噪声 22.1 / 28.7 → 23.0 / 29.3 mV。
- 示例 36:33.3 dB 处预编码 8.88e-6 → 7.61e-6。
- 示例 37 / 38 / 42:只动 BER ≥ 0.07 的点,reach 不变。
- 引用处(SUMMARY、USAGE、`cairn/DSP发端与PR.md`、`cairn/光互联建模.md`、pitfalls)已更新,旧值写在日期说明里。

### 测试
- `tests/test_prbs.py`:每一对电平都与 Gray 码逐位比对。
- `tests/test_pr.py`:预编码判决链每个错符号的比特数对蒙特卡洛(两种预编码)。
- `tests/test_fec_interleave.py`:预编码时域运行里确有 0 ↔ 3,且引擎的计数等于 `bit_errors`;引擎交回参考符号那条由 ≤ 收紧为 ==。
- `tests/test_cascade.py`:端到端计分原先把 0 → 3 断言成 2 bit(钉住了这个错),改为 1,另加 0 → 2 为 2。

---

## [未发布] — 误码按 lane 复用与码字交织映射到 RS 码,成串误码的 KP4 帧错误率(ROADMAP P3 #9a)

### 新增
- `fec/interleave.py`:
  - `bit_errors(decisions, reference)`:按 Gray 比特(MSB 在前)给出误码在线上的比特位置;参考为 −1(周跳留下的空位)的符号不计。
  - `FecLayout(lanes, pcs_lanes, mux_bits, interleave, stagger)`:物理 lane 上的比特怎样到达 (码字, RS 符号)。几种复用 —— 单 lane 连续、
    比特复用、按符号、按符号对 —— 与 1 / 2 / 4 码字交织都由参数表达。预置 `SEQUENTIAL`、`BIT_MUX_2CW`(仿 400GBASE-R)、
    `SYMBOL_PAIR_4CW`(仿 200G/lane);后两个按 802.3 资料的二手摘要建模,不是条文转录。
  - `burst_profile(...)` → `BurstProfile`:误码按间隔分事件,记每个"事件 × 码字"占几个 RS 符号。
    - `frame_error_rate(ber)`:复合泊松尾部(事件率随 BER 缩放、足迹不变)。
    - `threshold()`:FER 等于二项投影在其门限处 FER 的 pre-FEC BER。
    - `resample(rng)`:按事件 bootstrap。
  - `compound_fer(lam, pmf, t)`:按尾部直接求和,1e-13 量级不丢精度。
- 时域引擎与静态链路的 `extras["reference"]`:与 `decisions` 同长的参考符号。只加键,数值不变(指纹原有 819 个值逐位相同,另外 27 个是新键)。
- 示例 43(`examples/43_fec_mapping.py`):示例 42 的 224G 链路,五种收端 × 四种映射,给出等效门限(带 bootstrap)与按门限读的 reach。

### 结论(示例 43)
- **二项投影对 PR 链路偏不偏,取决于映射**:
  - 仿 200G/lane 的符号对复用 + 四码字下偏保守:1 + aD 与 1 + aD + bD² 的 reach 都 +0.58 dB。
  - 单 lane 连续映射下偏乐观 0.30 / 0.70 dB;4:1 比特复用下偏乐观 0.60 / 1.56 dB;8:1 比特复用下偏乐观 2.14 / 2.20 dB。
- DFE 的误码基本孤立,映射对它的影响在 −0.25…+0.02 dB 之间。
- 预编码把 1 + aD 的一串误码变成恰好两个:比特复用 8:1 下 +1.63 dB,符号对复用下 −0.11 dB。
- 更正范围评估里的一条推断:单 lane 下 RS 符号误码率低(0.51 倍)不等于帧错误率低,t = 15 的尾部由每个码字里的簇大小决定
  (`cairn/多lane建模.md` §2 更正、§6;`engineering-pitfalls.md`)。

### 发现(未修,ROADMAP P2 #20)
- `symbol_checker` 把 Gray 下只差一比特的 0 ↔ 3 记成 2 bit,所以预编码 PAM4 的 BER 高 25 %(1 + aD 预编码链路:2.51e-4 对 2.01e-4)。

### 测试
- +25:`tests/test_fec_interleave.py`
  - 映射:每个码字恰好 n 个符号;已知突发落在哪;参数校验。
  - Gray 比特计数与跳过空参考。
  - 引擎交回的参考符号。
  - 复合泊松:单符号事件就是泊松尾部;对四条 lane 逐码字直接计数,t = 2 / 4 / 6 吻合。
  - 等效门限:孤立误码 0.986;独立误码 0.92–1.0;PR 式误码串在三种映射下的方向。
  - bootstrap 可复现;无误码时报错。

---

## [未发布] — 串扰在受扰 lane 的采样相位上读,lane 间 skew 两个引擎同用(ROADMAP P2 #19)

### 修复
- 统计引擎原先按每个 aggressor 自己的峰值取串扰游标,相当于所有 aggressor 同时处在最坏相位;时域引擎则是耦合冲激碰巧对齐到哪就是哪。
  ADC 收端的统计引擎还漏了一步:串扰没有过 FFE(时域在 ADC 之前叠加串扰,所以串扰会跟着信号一起被 FFE 放大)。
  这导致 aggressor 延后 0…7/8 UI 时,统计 / 时域在 1.2–49× 之间变,而铁律 3 在串扰下没有测试把关。
- 改为在受扰 lane 的采样网格上读 aggressor 的全部游标,ADC 收端先过 FFE。各 skew 下的统计 / 时域:
  - 224G ADC:2.9–49× → 1.01–1.08×;
  - PAM4 32G 混合信号:0.79–1.21×;
  - NRZ 28G:0.41–1.17×(0.41 那一点是浴盆在锁定点旁太陡,`cairn/多lane建模.md` §3)。

### 新增
- `XtalkAggressor.skew_s`:aggressor 比受扰 lane 晚发多久(同步 lane 的 skew,≥ 0,取最近的采样点)。
  `launched_coupling` 给时域、流式和统计三处统一使用;`skew_s = 0` 时时域逐位同前。

### 变更(示例输出)
- 示例 24:统计 BER 不再比时域高一截。−18 dB 时统计 1.34e-1 → 3.64e-3(时域 5.51e-3);−22 dB 时统计 4.44e-2 → 2.1e-8(时域 0 误码)。
  docstring 和图标题里"统计是保守上界"的说法一并去掉。示例 26 / 28(COM、ICN)不变;COM 仍按各 aggressor 自己的峰值取,那是它自己的口径。

### 测试
- +3:`tests/test_crosstalk.py`
  - 两组链路(224G ADC 4 个 skew、PAM4 32G 混合信号 3 个 skew),每个 skew 都要求 2× 以内,且两个引擎在同一个 skew 上最差;
  - skew 会延后时域和统计两边看到的耦合,负值报错。

## [未发布] — TDECQ 未定条文:前后抽头差限制与多模光纤仿真滤波器(ROADMAP P3 #11 余项)

### 新增
- `tdecq(pre_post_limits=(lo, hi))`:802.3dj D2.1 后加的参考 FFE 前后抽头差限制,按 lo ≤ (w(1) − w(−1))/w(0) − b(1) ≤ hi 执行。
  起点和搜索中每个越界的试探点都会把 w(1) 与 w(0) 挪到边界上,其余抽头和抽头和不动。`TdecqResult.pre_post` 总是报告这个值。
- `tdecq(fiber_emulation_hz=f_A)`:多模条款的光纤仿真滤波器,是一个 BT4,与 0.5×baud 参考接收机级联,只滤信号。
- 两者数值都没拿到(IEEE 802.3 / OIF 原文被出口代理拦截,只有检索摘要),所以默认都不加;docstring 列了候选值与出处。

### 变更(示例输出)
- 示例 34 末尾新增一节,量这些未定条文会改变什么,原有输出不变。
  - **前后抽头差**:EML 上两种候选限值 (−0.3, 0.15) / (−0.25, 0.25) 处处不起作用,TDECQ 一位不差;收紧到 ±0.05 时,30 / 25 GHz 多 1.11 / 1.95 dB。
  - **DR1 上限**:若降到 3.0 dB,最小带宽 29.83 → 33.72 GHz。
  - **多模**:VCSEL 在 TP2 时 f_A 25.8 GHz 处过 SR1,本模型的 100 m OM4 约当 f_A 25 GHz。
- ROADMAP 原写"MMF 参考接收机带宽可能低于 0.5×baud",更正为:参考接收机仍是 0.5×baud,另级联一个光纤仿真滤波器。

### 测试
- +3:`tests/test_tdecq.py` 覆盖三件事:
  - 前后抽头差会报告,约束生效时被钉在边界上,不生效时结果逐位不变;
  - 约束计入 DFE 的 b(1);
  - 光纤仿真滤波器只滤信号,带宽越窄 TDECQ 越高。

## [未发布] — CPO 相对 LPO 的功耗差(ROADMAP P3 #11 余项)

### 新增
- `TopologyConfig.optics_placement: pluggable | co_packaged`(默认 `pluggable`):光路是前面板笼子里的可插拔模块,还是主机封装基板上的
  共封装光引擎。只有功耗模型读它 —— 引擎只认段 A / B 的损耗;不进表单(应用层不算功耗)。
- `PowerCoefficients.co_packaged_optics_j_per_bit` = 6.9 pJ/b(Broadcom 51.2T CPO 5.5 W / 800G;JLT 2026 交换机级对比的 2024 年 CPO,
  比 LPO 省 23%;区间 5.6–8.0)。`power_report` 对共封装光路按它计价,可插拔仍按 `optics_j_per_bit` 9.0;重定时器照算。

### 变更(示例输出)
- 示例 41 第 4 节:同收端下 CPO 11.15 对 LPO 13.25 pJ/b(原两者同价);新增 100 m OM4 上两条链路的时域 ENOB 阶梯(9 抽头 FFE,
  CTLE 6 / 7.5 / 9 dB 取最好,满量程 0.2):LPO 最低 ENOB 5.0、CPO 3.5,按各自收端计价 LPO 12.57、CPO 9.74 pJ/b,共省 2.82(22%)——
  模块开销 2.10,1.5 bit ADC 0.72。第 5 节加 CPO / LPO 一列与 CPO 光引擎系数两端两个变体:0.67–0.86。原有数字不变。
- 文档里"LPO 与 CPO 在模型里同价"一句(SUMMARY、USAGE、COMPARISON、`cairn/功耗建模.md`)改为上述结论并附更正说明。

### 测试
- +2:`tests/test_power.py` 共封装只换光路总量(收端同价、重定时器照算);`optics_placement` 取值校验。

## [未发布] — Viterbi 的网格在接收机实际采样点拟合(ROADMAP P2 #17)

### 修复
- 时域引擎给序列检测器的光标原取自起始的脉冲模型(脉冲峰值处的游标经最终 FFE 抽头),而环路不停在那里。FFE 只有主抽头时 MM-CDR 锁在
  原始脉冲 h(−1) = h(+1) 处、没有东西把它重新居中:第一后光标模型 −0.086、实际 +0.058,主光标只有电平的 0.92 —— Viterbi 比自己的
  slicer 多约 7 倍误码(示例 42 的链路 0.06 m:8.5e-3 对关 MLSD 的 1.16e-3)。有 DFE 时模型还把 DFE 之后的光标放到了网格的延迟 1、2 上。
- 现在两种收端都用 `_trellis_cursors`:判决器样本对它们携带的线路符号做最小二乘(按 `score` 跨周跳重对齐),前光标与网格外的光标一起
  拟合再丢掉,主光标增益保留;浮点与 bit-true 的 Viterbi / DFSE / sliding 都用它。检测器对信道的了解仍是理想的(此前的脉冲模型同样是
  仿真器自己的知识),与统计引擎的假设一致。对接收机自己判决拟合的版本试过:slicer 很脏时被判决错误带偏(示例 06 增益 2.4× → 1.4×),弃用。
  同一 1 抽头链路 MLSD 0 误码。MLSD 关闭的路径逐位不变(仓库指纹)。

### 变更(示例输出)
- 12 个用 MLSD 的示例全尺寸重跑并更新期望输出,门限附近 reach 动 ≤ 0.1 dB,结论不变:示例 19 / 20 / 21 基线 33.0 → 33.1 dB(FEC +3.3 → +3.2、
  ADC +5.5 → +5.4、DSP 深度 +1.7 → +1.8、全栈 +9.8 → +9.7 dB),示例 38 第三个受控光标 +0.34 → +0.28 dB(38.94 → 38.88,仍低于 0.5 dB 门槛),
  示例 18 的 3 抽头 MLSD 增益 2.4–4.3× → 2.0–4.3×,示例 40 发端 PR a = 1 预编码 17.36 → 17.30 GHz,示例 42 短 FFE 只留目标本身 ≤ 0.09 → ≤ 0.07 dB。
  95 处登记的推导数与引用打印值的文档同步更新(有日期记录的保留旧值)。
- 双引擎重量(统计不变、时域重跑):§14 的 16 个 PR 链路 0.60–1.08×,短 FFE 0.95–1.53×,光链路 PR 0.83–1.44×,0.26 m 降状态三点 0.89–0.92×。

### 测试
- +2:`tests/test_mlsd_reduced.py` 拟合在前光标 / 网格外 ISI / slicer 错误 / 周跳下给出原光标;1 抽头 FFE 上 MLSD 不输给 slicer(旧模型下失败)。

## [未发布] — 光链路上联合界的 SNR 计入光噪声(ROADMAP P2 #18)

### 修复
- 统计引擎 PR 联合界的重叠权重(`pr_error_events(snr=...)`)按 `half_gap / noise_sigma` 抽样;光链路的 `noise_sigma` 只有接收端电噪声
  (示例 40 的链路 0.08 mV),光噪声(1.7–3.2 mV)在逐电平 / 分箱的核里。SNR 高估几十倍,事件看起来互不相交,并集退化成简单求和,
  发端 PR(各长度距离挤在一起)悲观 1.5–3.0×。现在按链路全部噪声抽样:电噪声加光噪声按电平 / 邻符号组合的平均方差(有大信号曲线时
  加它的方差)。示例 40 的链路上:发端 PR a = 0.5 16 / 17 GHz 2.99 / 2.28 → 1.40 / 1.36,a = 1 预编码 1.73 → 1.40,带主机 FFE 1.50 → 1.05;
  收端 PR 1.01–1.41 → 0.83–1.20。电链路逐位不变(仓库指纹);示例都不在光链路 PR 下调统计引擎,输出不变。

### 测试
- +1:`tests/test_pr.py` 光链路发端 PR a = 0.5 的双引擎 2× 判据(旧 SNR 下 2.99× 失败)。

## [未发布] — 统计引擎 PR 联合界:残余 ISI 按事件投影(ROADMAP P2 #16)

### 修复
- 统计引擎在 PR 目标 + 序列检测器下,每个误差事件原来只把噪声按事件距离缩小(σ / g),网格外的残余 ISI 仍按单个样本的大小算 ——
  相对门限等于把 ISI 放大了 g 倍。短 FFE(3–7 抽头)留下的残余大,统计因此悲观 1.8–10.7×(铁律 3 越界)。现在每个事件的 ISI
  是残余光标在它的差 d 上的投影 d · ISI / |d|²(串扰同样投影),即检测器成对条件 d · (n + ISI) > |d|² / 2 的精确形式。
  短 FFE:3 抽头 1 + aD 2.05 / 2.32 → 1.00 / 1.17,5 抽头 6.18 / 10.74 → 0.77 / 1.24,7 抽头 1 + aD + bD² 7.29 → 1.07;
  `cairn/DSP发端与PR.md` §14 的 16 个 PR 链路 0.67–1.55 → 0.58–1.08×(几何平均 1.06 → 0.80)。
- 不变:非 PR 路径逐位相同(仓库指纹),眼图 PDF 与逐符号判决(`ser_slicer`)仍用原 ISI;光链路的两个分箱邻符号不投影。
  示例都不在 PR + 序列检测器下调统计引擎,输出不变。PR 链路的统计调用多约 0.4 s。

### 新增
- `engine.statistical.pr_event_differences`:联合界每个事件对期望样本的差,与 `pr_error_events` 同序(两者共用模式表)。

### 测试
- +3:`tests/test_pr.py` 事件差与联合界事件一一对应;5 / 7 抽头 FFE 配 PR 目标的双引擎 2× 判据(不投影时两格都失败)。

### 发现(未修,记 ROADMAP P2 #18)
- 光链路发端 PR、噪声在调制器之后时统计偏悲观 1.5–3.0×(示例 40 的链路;投影前后相同,原有)。

## [未发布] — 降状态网格在短 FFE 上的代价(示例 42 第二部分)

### 变更
- `examples/42_reduced_state_mlsd.py` 加第二部分:FFE 砍到示例 18 的 3 抽头(1 前 / 1 后),网格里多了要解的残余光标。delta 目标
  memory 3:64 / 16 / 4 状态 reach 30.06 / 30.04 / 29.97 dB;1 + aD memory 2:64 / 4 状态 31.04 / 31.05 dB。只留目标本身的 4 状态
  差 ≤ 0.09 dB,"网格只需要目标的受控光标"在短 FFE 上也成立 —— 这条链路上 3 抽头 FFE 留在网格窗口里的残余只有 0.02–0.06。
  第一部分的输出不变;`examples/derived.yaml` 的 ex42 组改用第一张汇总表独有的表头定位。

### 发现(未修,记 ROADMAP)
- P2 #16:PR 目标配 3–7 抽头 FFE 时统计引擎悲观 1.8–10.7×(铁律 3 不成立;delta 目标 0.88–1.62,11 抽头起 ≤ 1.31,示例都不在此区间)。
- P2 #17:1 抽头 FFE(只有主抽头)下 Viterbi MLSD 比 slicer 多出约 7 倍误码(0.06 m:8.5e-3 对关 MLSD 的 1.16e-3)。

## [未发布] — 降状态检测的定点与 RTL(bit-true DFSE + `rtl/dfse_mlsd.sv`)

### 新增
- `dsp/fixed_viterbi.py`:`_dfse_fixed_py` / `dfse_fixed_kernel`(numba)、`FixedDfse`、`dfse_tables`、`build_fixed_dfse`、
  `run_fixed_dfse`、`dump_dfse_vectors`。期望字 = rnd(状态部分的未舍入表 + 各反馈光标按电平的未舍入份额, c_fl),只舍入一次,
  所以 K = L 时就是全网格的期望字表、决定逐个相同;加比选、饱和、重归一与回溯同全网格内核,尾部寄存器随获胜路径搬移。
- `rtl/dfse_mlsd.sv` / `tb_dfse_mlsd.sv`:独立的 SV 实现;`rtl/gen_vectors.py` 第 5 段(1 + 0.6D + 0.3D² + 0.1D³、K = 1、4 状态、
  反馈 2 个光标、3000 符号),`run_lockstep.sh` 与 CI `rtl-lockstep` 多一段,`tests/test_rtl_lockstep.py` 加一项(含改一个金标即 FAIL)。

### 变更
- `numeric.mode: fixed` 与 `rx.mlsd.state_memory` < 网格长度不再被配置拒绝,引擎定点模式跑 bit-true DFSE。

### 测试
- +13:`tests/test_fixed_viterbi.py` 12 项(K = L 等于定点 Viterbi、输入精确时等于浮点 DFSE、独立参考 4 种移位 / 饱和、JIT = Python、
  引擎定点模式与浮点误码数 3σ 内),`tests/test_rtl_lockstep.py` 1 项。

## [未发布] — 降状态序列检测(DFSE,`rx.mlsd.state_memory`;示例 42)

### 新增
- `rx.mlsd.state_memory` = K:Viterbi 网格跨 L = len(pr.target) − 1 + memory 个光标,状态只记最近 K 个符号(N^K 个),其余
  L − K 个光标用每条幸存路径自己的判决减掉(decision-feedback sequence estimation)。不设或 K = L 是原来的全网格,逐判决相同
  (不设时仍走原内核,已有结果一个不变)。`dsp/mlsd.py` 的 `dfse_mlsd`(numba,`HALO_NO_JIT` 下 Python 同一判决)、
  `post_detect(state_memory=)`、`mlse_min_distance_sq(state_memory=)`。
- 统计引擎:降状态网格里两条路径在最后一个错误之后 K 个符号合并,长 n 的错误事件只计差的前 n + K 个样本(最小距离与 PR 联合界
  `pr_error_events(state_memory=)` 都按此截断);幸存路径反馈出错的传播不计。224G LR 链路上全网格 / 16 / 4 状态统计对时域
  1.09 / 1.10 / 0.99。
- 配置:`state_memory` 只配 Viterbi、≥ 1、不超过网格长度;定点模式下 K < L 被拒(bit-true 检测器与 SV 是全网格表,ROADMAP P3 #12)。
  功耗模型按 N^K 状态加逐状态的反馈加法计价。GUI / Android 表单字段 `rx.mlsd.state_memory`。
- `examples/42_reduced_state_mlsd.py`(示例 38 的链路与种子,全网格三行与示例 38 逐位相同):1 + aD 4 状态 = 64 状态(36.33 dB);
  1 + aD + bD² 16 状态 = 256 状态(38.60 dB),4 状态 37.75 dB;三光标 memory 1 的 64 状态 = 256 状态(38.94 dB)。两光标目标的
  Viterbi 估计 12.73 → 1.02 pJ/b、收端 17.68 → 5.97 pJ/b。网格只需要目标的受控光标,残余交给逐路径反馈不丢 reach。
- 测试 +18(`tests/test_mlsd_reduced.py` 16 项,`test_examples_api` 对示例 42 参数化 2 项)。

## [未发布] — 功耗估计(pJ/bit,估计口径;示例 41)

### 新增
- `analysis/power.py`:按模块给一条链路方向的功耗,只读配置。发端与两种收端共用的前端 / 时钟按每 bit 总量(两种架构同一个系数);
  混合信号的比较器(tap1 展开 × N)与模拟 DFE 抽头;ADC 按 Walden 或 Schreier FOM(`enob` 未设按 `n_bits` 计价并注明);
  数字 FFE / DFE / Viterbi 按运算次数 × Horowitz 45 nm 能耗 × DeepScaleTool 的 45 → 7 nm 能耗比 0.175;Viterbi 状态数
  N^(len(pr.target) − 1 + mlsd.memory) 与引擎网格一致(`trellis_states`);光路一个模块总量,`retimer: both` 加两对收发。
  **系数全是公开数据的估计**(Murmann ADC 调查表、Horowitz、DeepScaleTool、已发表收发器与实测 800G 模块;出处表在
  `cairn/功耗建模.md`),只读比值。数字块只算运算,是下限。
- `examples/41_power_budget.py`:与已发表总量核对(112 Gb/s 收发器 4.23 vs 4.63 pJ/b;224 Gb/s 收端 2.44 vs 1.41;retimed 减 LPO
  8.51 vs 实测 7.4–8.5)、示例 07 的两种收端(1.60 vs 4.81 pJ/b)、224G LR 杠杆(每多一个受控光标 Viterbi ×3.76 / ×3.91,
  第一个 +2.39、第二个 +9.48 pJ/b)、示例 33 的三种拓扑(LPO 13.25、retimed 21.76;LPO 与 CPO 在模型里同价),以及每个比较在
  系数区间两端是否成立。
- 测试 +26(`test_examples_api` 对示例 41 参数化 2 项;`tests/test_power.py` 24 项:Walden / Schreier 复现调查表一行、每 bit ×2 / ×4、FFE 线性、网格状态数、展开 tap1、
  两种架构共用块同价、retimed = LPO + 两对收发)。

### 变更
- 示例 07 的"乘法数 × 位宽"代理(混合信号记 0)换成本模型的收端 pJ/b:混合信号 1.60、ADC 4.81(3.01×)。

### 修正
- `docs/SUMMARY.md` 杠杆表把收端 PR(1 + aD + memory 2)的网格写成"N² 状态",实为 N³ = 64,对照才是 N² = 16;同表代价列补了 pJ/b。
  USAGE 的 `mlsd.memory` 注释改为状态数 = n_levels^(memory + PR 受控光标数)。

## [未发布] — MMSE 起始解计入发端噪声

### 修正
- 收端的起始求解(`pr.adapt: mmse` 的目标、`lms` 的起点、起始 MMSE FFE;时域 ADC 收端与静态引擎)原来只看 FFE 输入处的白噪声
  (收端 + ADC)。`tx.noise_rms` 到 FFE 时经信道着色、与信号同形,这时最优目标是 delta;旧解在示例 40 的 12 / 22 GHz 链路上选
  a = 0.98 / 0.26,`adapt: lms` 从那里几乎不动(0.980 → 0.981)。现在按 σ² × DAC → FFE 逐 UI 响应的自相关(Toeplitz)计入:
  选回 a ≈ 0,容忍的发端噪声 36.7 → 42.8 mV(12 GHz)、45.6 → 48.2 mV(22 GHz),与对照相同;40 mV 时 BER 5.5e-4 → 1.0e-4。
  DAC 量化不计入(带 DAC 的链路起始解照旧);没有 `tx.noise_rms` 时逐位不变(819 值指纹)。
- `mmse_ffe` / `mmse_pr_target` 加可选的 `noise_cov`;`statistical.tx_noise_covariance`。
- 示例 40 重跑:MMSE 行如上;发端 PR 两行的 22 GHz / a = 1 列小移(LMS 起点变了),发端 PR 亏 3.0–5.8 dB。
- 更正 `cairn/DSP发端与PR.md` §16 的"MMSE 仍选 a ≈ 0.38":那是 DAC 量化试验里的值。
- 测试 +2(`tests/test_tx_noise.py`)。

## [未发布] — 光 duobinary:发端噪声 `tx.noise_rms`,带宽受限调制器上 PR 放哪(示例 40)

### 新增
- `tx.noise_rms`:发端自己的噪声,每 UI 一个白噪声加在 DAC 输出(驱动器极点与调制器带宽之前,后面每一级都把它与信号一样整形)。
  独立随机流 `[sim.seed, 0x7E5]`,开它不挪任何别的抽取;0(默认)时逐位不变(引擎指纹 819 值)。`symbol_stage(noise=False)`
  给无噪声重建用(`front_end_waveform(with_noise=False)`)。GUI / Android 表单字段 `tx.noise_rms`。
- `examples/40_pr_optical_duobinary.py`:示例 32 的 VCSEL + OM4,VCSEL 的 f_r 当带宽限制,对照 / 收端 PR(a = 1 预编码、MMSE a)/
  发端 PR(a = 0.5、1 预编码)。噪声在调制器后:需要的 f_r 对照 14.23 GHz、收端 11.94 / 11.99、发端 16.74 / 17.36(加 host Tx FFE
  后 9.31 / 8.55 / 8.34 / 10.99 / 11.53);噪声在发端:12 → 22 GHz 对照的噪声容限 42.7 → 48.2 mV,收端 PR 少 0.5–2.3 dB,
  发端 PR 少 3.0–5.7 dB。两种情形下发端 PR 都不占优;光 duobinary 的收益由收端 PR 拿到。ROADMAP P3 #8 清空。

### 修正
- 统计引擎:发端噪声与 DAC 的 σ_q 合并;序列检测 + PR 目标的联合界,噪声自相关原来只取自 FFE 抽头(噪声在 FFE 输入是白的),
  现在按两份方差加权 —— 收端那份 FFE 抽头的自相关,发端那份 DAC → 判决器逐 UI 响应的自相关(DAC 的 σ_q 也走这条)。
  发端噪声为主时统计 / 时域:收端 PR 0.22–0.48 → 0.52–1.23,发端 a = 0.5 0.83–2.89 → 0.95–1.55。
- `tx_report` 的 TX SNDR:参考发端原来带着同一份发端噪声,做差时抵消;参考改为无噪声,SNDR 现在含发端噪声。
- `tools/example_drift.py`:"示例 38,40 万符号"里的 40 不再算作引用示例 40。

## [未发布] — 推导数登记补全,`example_drift.py candidates`

### 新增
- `tools/example_drift.py candidates [文档…] [--strict]`:列出现行文档里以比较措辞("多 / 差 / 再加 X dB""+X dB""X×""X 倍")
  写出、所引示例不打印、又没被 `examples/derived.yaml` 覆盖的数 —— 未登记推导数的候选,只列不拦;多数是历史值、单独实测或配置,
  要人读。默认按舍入判断"打印过",`--strict` 要求逐位相同(连碰巧与某个打印值同舍入的也列)。不带 "+" 的 "−X dB" 不算比较
  (在这些文档里是损耗或电平)。
- 推导式里 `max` / `min` 可取多个操作数(`max(abs(a - b), abs(c - d))`)。

### 修正
- 补登记 6 个推导数(`examples/derived.yaml` 8 组 76 处 → 9 组 84 处):示例 38 第四光标比三光标 +0.34 dB(ROADMAP、USAGE 的
  "约 0.3 dB")、第二个受控光标的增量 2.3 dB 与 memory 1 / 2 之差 0.03 dB(`cairn/DSP发端与PR.md`);示例 37 发端 PR 与峰值代价预测
  之差 ≤ 0.4 dB、a = 1 时 1.3 dB(新组)。数都还对,此前只是改输出时不会被核对。
- `docs/summary.html` 有三处测试数停在 913(中文一处、英文两处),此前几次只同步了页脚的中文,现统一为 941。
- 测试 940 → 941。

## [未发布] — 统计引擎报 PR 的"环路负载",高 SER 时警告

### 新增
- `run_statistical` 的 `extras['pr_loop_load']`:逐符号判决的 SER × 受控光标能量 Σc_k²(ADC 收端、MM 读均衡后样本、有 PR 目标时),
  ≥ 0.08 时警告统计结果偏乐观。依据:强目标在高 SER 下时域 MM-CDR 读的残差被错判带偏、环路周跳(例 (1, 1.4, 1.0, 0.4)、3 mV:
  时域 BER 0.16 对统计 3e-3);冻结 CDR 后两者 0.71×,冻结 LMS 没用。6 个目标(两到四光标、预编码、c < 0)首次失效在负载 0.09–0.14,
  正常的点最高 0.117(0.08–0.117 会误报)。只用逐符号 SER 分不开(0.03–0.24)。
- 测试 939 → 940。ROADMAP P2 #15 清空(P2 再次清空)。

## [未发布] — 统计引擎的 PR 联合界改为整族事件的并集

### 修正
- `pr_error_events(snr=...)` 的权重。原来每个长度只扣掉与前一个长度的重叠(链式界),手选 c < 0 的四光标目标统计 / 时域
  到 2.09×(铁律 3 越界,ROADMAP P2 #15)。现在一个检测器错误只算一次:竞争集合是全部事件(各长度、交替 / 同号两族、
  ±11 个符号内所有重叠的起点与两种符号),在事件 j 的度量条件与符号可行的前提下重要性抽样,权重 = 支持率 × mean(选中
  事件的误码 / 同时成立的事件数)(at-least-one-event 估计器)。只在接收机采样的相位算一次,按比值用到其他相位;
  每次统计调用多约 0.1 s。删掉不再用的 `_bvn_upper`。
- 16 个 PR 链路(收 / 发端、两到四个光标、预编码)统计 / 时域:0.78–2.09× → **0.67–1.55×**(几何平均 1.45 → 1.05);
  (1, 0.9, 0.3, −0.15) 2.2 mV 2.09 → 1.30×,发端 a = 0.5 1.86 → 1.25×。中等 SNR 下对整族事件直接抽样:并集 / 直接 0.98–0.99,
  简单求和是它的 2.1–4.1 倍。非 PR 路径逐位不变(仓库指纹 819 值);示例输出不变。
- 测试 935 → 939:直接抽样核对三组(交替、两族带负光标、预编码)、孤立事件权重不变,四光标铁律 3 加回 (1, 0.9, 0.3, −0.15);
  原"重叠只降低较长事件"的测试连同 `_bvn_upper` 的精度检查删除。
- ROADMAP P2 #15 第一项清空;第二项(强目标高 SER 下判决导向环路失效)仍在。

## [未发布] — 第三个受控光标:1 + aD + bD² + cD³

### 新增
- `pr.target = (1.0, a, b, c)`(a ∈ [0, 3]、b ∈ [−1, 3]、c ∈ [−1, 1],E2PR4 (1, 3, 3, 1) 在边上)、`PrConfig.gamma`;
  `mmse_pr_target(n_target=4)`。ADC 内核的逐符号判决、MM 残差与 LMS 多减 / 多跟一个受控光标(`pr_gamma`,状态 `fs[6]`);
  `pr.adapt="lms"` 同时跟 a、b、c。定点环(`prs[2]`、`lp` 加 c 的范围)与 `rtl/adc_dsp_loop.sv`(`ab[3]`、`C_LO` / `C_HI`)同步,
  新向量 `rtl/vectors/pr4`(a、b、c 都自适应)逐位一致。Viterbi 与统计引擎的逐符号判决模型本来按光标数通用(343 状态)。
- 示例 38 加两行:四光标 memory 1(与三光标 + memory 2 同 256 状态)**38.94 dB**,memory 2(1024 状态)38.97 dB,三光标 38.60 dB。
  换种子 7 / 8 / 9:+0.18 / +0.31 / +0.48 dB。四个种子都低于事先定的 0.5 dB 门槛;2026-10-09 的基带 MC 折算估 +0.6–0.7 dB,
  实测约其一半(`tools/pr_target_length.py` 的 `MEASURED` 改按 (光标数, memory) 记,并附实测)。可用,不作推荐配置。
- 测试 913 → 935(`test_pr.py` 四光标一组、定点独立参考与宽字长对照、分块逐位、RTL `pr4`、配置校验)。

### 修正
- 统计引擎有 PR 目标时把相位网格放在接收机的锁定点上。原以成形脉冲的最大点为中心,a > 1 时最大点在主光标后最多半个 UI,
  锁定点出网格、折回后读到下一个光标:(1, 1.1, 0.45) 统计 BER 2.7 对时域 6.0e-4,修后 5.8e-4。锁定点原在网格内的用例读同一个
  采样,结果逐位不变(仓库指纹 819 个值、另录的 11 个 PR 用例)。
- 表单标签 `pr.target` / `pr.adapt` / `pr.mu` 不再写"alpha"(目标早已不止一个受控光标)。

### 已知
- 统计 / 时域在手选 c < 0 的四光标目标上 1.75–2.09×(一格越过 2×),强目标在高 SER 下时域判决导向环路失效而统计引擎不建 ——
  ROADMAP P2 #15。MMSE 选的目标(c > 0)0.78–1.73×。

## [未发布] — 示例 06 / 18 的 MLSD 增益全程计数

### 修正
- 示例 18 与 06 在 `res.y_slicer`(引擎只存前 2 万个符号)上离线比较判决器与 Viterbi,示例 18 每个点只有个位数到几十个错误。
  改为引擎的 Viterbi(`rx.mlsd`,memory 2;示例 06 另有 memory 1)在全部符号上计分:判决器的误码取 `extras["ser_slicer"]`,
  MLSD 之后取 `ser` / `ber`,同一组判决,比值就是 MLSD 增益。残余光标仍由 `y_slicer` 拟合(只估三个数)。
- 结论不变,数字变:21 抽头 FFE 下 MLSD 在每个有误码的损耗上 1.0×;零误码只到 −27.3 dB(原记 −28.8 dB,该点 40 万符号里有 6 个错)。
  3 抽头 FFE 下 MLSD 收益 2.4–4.3×(原 1.9–4.4×,−27.3…−33.3 dB),仍不如 21 抽头 FFE 单独用。示例 06:memory 1 / 2 的收益
  2.0 / 2.2× → 2.1 / 2.4×。示例 18 的表改报 SER(MLSD 前后)与 MLSD 后的 BER;docstring 里"PR 目标未建模"的旧说法改指示例 36。
- ROADMAP P2 #14 清空。

## [未发布] — reach 按 pre-FEC 阈值读、扫描在穿越点附近加密

### 新增
- `halo_serdes.fec`:`fec_threshold(code, inner=, target=)`(post-FEC 达到目标时的 pre-FEC BER,KP4 2.19e-4、+ BCH(255,215) 6.86e-3),
  `reach(x, ber, threshold)`(在越线的两个扫描点之间对 log pre-FEC BER 线性插值;下方那个点零误码时不插值,报 "no errors at lo"),
  `refine(points, measure, threshold, tol)`(二分扫描参数直到区间 ≤ tol,新点留在 points 里供下一个阈值复用)。测试 +4。

### 修正
- reach 的读法。此前在扫描点之间对 **post-FEC** BER 取对数插值,并把零误码点换成 1e-9 / 半个计数的替身:级联码的 post-FEC 映射在
  阈值附近极陡,3 dB 步长上插值偏向差的那一端(示例 21 全栈 44.2 dB,pre-FEC 实际在约 42.8 dB 越线);零误码点的替身决定答案
  (示例 21 基线 33.2 dB,示例 20 细网格上同一接收机 32.4 dB)。示例 19 / 20 / 21 / 32 / 33 / 35 / 36 / 37 / 38 与 app 的 reach /
  光链路研究(GUI、Android)改用 `fec.reach` / `fec.refine`(示例 0.4 dB / 10 m,研究 0.25 dB / 5 % 光纤长度)。
- 示例 19 / 20 / 21 / 35 在 `res.y_slicer` 上离线跑 Viterbi 计分,而 `y_slicer` 只存前 2 万个符号:50 万符号的仿真只在 4 万比特上数,
  KP4 阈值附近约 9 个错误。改用引擎的 Viterbi(`rx.mlsd`)在全部符号上计分;示例 20 / 21 标的是 MLSD mem3,旧拟合只用了 3 个光标
  (memory 2),现在按标签用 memory 3。
- 数字变化(全尺寸):同一接收机在示例 19 / 20 / 21 都读 33.0 dB(原 32.4 / 32.4 / 33.2)。DSP 深度杠杆 +0.8 → +1.7 dB、ADC +5.3 → +5.5、
  级联 FEC +3.8 → +3.3、全栈 44.2 → 42.8 dB(+11.0 → +9.8,"比单独之和多 3.5 dB"→ 1.0 dB;仍在 802.3dj LR 带内)。收端 PR
  +4.7 → +5.0 dB、两光标 +7.0 → +7.3、对照 31.6 → 31.3 dB。光链路三拓扑 159 / 256 / 214 → 156 / 255 / 214 m,杠杆之和 +322 → +314 m;
  示例 32 的 12 dB 段 32 → 42 m(30–60 m 的 BER 本就贴着门限)。DAC:6 / 7 / 8 bit 与理想差 ≤ 0.07 dB,c = 0.2 代价 1.2 dB。
  GUI 的 reach 研究在两个预置上变 0.15 / 0.44 dB。
- `tools/pr_target_length.py` 的校准值随示例 38 更新;第四个受控光标的估计从 ≈ +0.5 dB 变为 +0.6–0.7 dB(三个种子),越过事先定的
  0.5 dB 门槛,ROADMAP 把它从"边界"移回 P3 #8、待重新决定。
- 示例 20 打印各 FEC 的 pre-FEC 阈值及其相对 KP4 的倍数(31.3×),文档的"抬高 31 倍"登记进 `examples/derived.yaml`,
  `pr_target_length` 的校准值也登记了(8 组、69 处引用)。

## [未发布] — 文档推导数的漂移检查

### 新增
- `examples/derived.yaml`:文档里由示例输出推导、但没有示例直接打印的数(两个输出之差、各行之和、一列的范围),每条写明算子在
  哪个示例的哪一行、哪段文字引用结果。首批 7 组、57 处引用:示例 12 的 NRZ / PAM4 极限之差,示例 19 / 20 / 21 的 DSP 深度、
  ADC、级联 FEC 杠杆及其和,示例 33 各行四舍五入后之和,示例 34 的 DFE 节省量与压缩代价,示例 32 的 host FFE 增益。
- `tools/example_drift.py`:`compare` 用新输出重算登记的推导数,列出不再成立的引用;新子命令 `derived` 按 `examples/expected/`
  检查每一处(测试套件也跑,所以 `accept` 后漏改的推导引用会让测试失败)。表达式只许四则运算、min / max / abs / sum / log10 与下标。
- 测试 +4(`tests/test_example_drift.py`)。

### 修正
- 示例 12 的结论"NRZ 约 −30 dB、PAM4 约 −20.5 dB 闭眼,差值恰为 PAM4 的 9.5 dB 电平代价"(README、`docs/SUMMARY.md`、
  `docs/summary.html`):示例打印的最后开眼点是 −30.2 / −20.4 dB,差 9.8 dB(扫描步长 1.6 dB)。改为给出两点与差值,
  写"与 20·log10 3 ≈ 9.5 dB 相符"。登记推导数时第一次用输出重算才发现。
- ROADMAP P3 #10 原举的推导数例子"收端 PR 多 4.7 dB"不是推导数:示例 36 / 37 自己打印 +4.71 dB。

## [未发布] — 桌面打包:Nuitka 编译缓存跨运行保存

### 变更
- `build-windows-desktop` 的 `nuitka` / `nuitka-onefile`:用 `actions/cache` 跨运行保存 `NUITKA_CACHE_DIR`(约 92 MB,每次运行一个新 key,
  从本类最新的一份恢复;分支也读 main 的)。一次构建 114 分钟里有 97 分钟是 MSVC 编 3643 个 C 文件,此前每次 0 命中。实测(2026-10-09):
  冷 onefile 87 / standalone 109 分钟,热两轮 40 / 31、42 / 35 分钟,3642–3643 / 3643 命中。上限 120 → 180 分钟,留给冷构建(依赖升级后、缓存被淘汰时)。
- 背景:10-03…08 onefile 在 120 分钟上被取消 7 次;取消不受 `continue-on-error` 管,打 tag 时还会让 `release` 被跳过。
  `packaging/README.md`、`CONTRIBUTING.md` 同步。

## [未发布] — ADC 收端的统计结果按 MM 锁定点读

### 变更
- `run_statistical` 对 ADC 收端不再报一个 UI 内的 BER 最低点,而是按接收机的锁定点报(mixed-signal 早就这样):
  `pd_input: adc` 锁在均衡前脉冲 h(−1) = h(+1) 处(`lock_offset_samples(..., osr)`),`pd_input: ffe` 锁在均衡前脉冲峰。
  依据:把时域环路起点挪开 ±0.5 / ±2 个采样,`ffe` 的五条 PAM4 链路都往原起点回摆,`adc` 的 NRZ 链路锁在预测点(−2.33 vs −2.30)。
  最低点留在 `extras['ber_min_phase']`,`extras['lock_phase_ui']` 现在对 ADC 也有值。电链路预置上两者本来同点(BER 不变);
  示例 32 的 CPO + FFE、c = 0.5 浴盆窄,0.35× → 0.91×;示例 31 kp_shift 9 两行 3.1e-4 / 3.2e-4 → 4.2e-4 / 4.4e-4(统计 / 时域
  1.37 → 1.85×,抖动 1.2 采样时读在环路自己的相位上),正文的"0.3–1.4×"改为"0.3–1.9×"。示例 04 的输出标签改为"at the
  receiver's phase"(mixed-signal 早已按接收机相位报,原标签"at best phase"不准)。指纹:不带 FFE 抽头调用统计引擎的 ADC 条目变了
  (未均衡,任何相位都没有意义)。
- `tools/example_drift.py`:一行的文字变了而数字个数没变时按位置配对,改标签不再把整行的数当成"可能变了"。

### 测试
- `tests/test_mm_pd_input.py`:统计引擎预测的 `adc` 输入锁定偏移与时域环路实际停留处一致(−0.563 vs −0.572 采样);
  `tests/test_optical.py`:窄浴盆上按接收机相位读在 2× 内、按最低点读不行。两条在旧代码上都挂。

## [未发布] — 统计引擎的光噪声核补上发端 FFE

### 修复
- `optical_stage.slicer_sigma_per_level` / `slicer_sigma_binned` 用阶段 1 的冲激自己拼"一个符号到光电二极管的脉冲",没卷发端的
  LTI 响应(`TxPipeline.equivalent_symbol_response`);统计引擎的主脉冲与大信号曲线的图样平均都卷了。开 host Tx FFE 的光链路上,
  每电平噪声底电平低估 16–22 %、顶电平高估 19–22 %,统计 / 时域 BER 偏悲观:线性链路 2.5–3.0×(BER 1e-4 量级,铁律 3 的 2× 被打破),
  示例 32 各点 1.27–2.66×。新的 `_symbol_pulse_at_pd` 统一拼这个脉冲;修后与无噪声波形的精确期望差 0.15 % 内,统计 / 时域 0.85–1.47。
  不开发端 FFE / 驱动器极点的配置逐位不变(804 值指纹逐位同)。这就是 ROADMAP #11 记的"c = 0.5 + 16 dB + 驱动器 CTLE 9 dB 悲观
  2.5–2.8×、原因未查清"。

### 变更
- 示例 32 第 3 问带 host FFE 的 7 行裕度更新,例如 16 dB 段 + FFE 3.92 → 5.52 dB(过 c = 0.2 激光器 1.66 → 4.63)、+ TIA 6 dB
  3.29 → 5.30、+ 驱动器 6 / 9 dB 7.44 / 9.31 → 7.88 / 9.53、CPO + FFE 12.62 → 12.71。方向结论不变;推翻一条:"c = 0.5 时 CPO 上 FFE 的
  增益从 4.03 剩 3.35 dB"(修后 4.17,与线性时的 4.12 相同)。`cairn/光互联建模.md` §6 更正段、`docs/SUMMARY.md`、`docs/USAGE.md` 同步。
  这些文档行由 `tools/example_drift.py` 找出。
- `tools/example_drift.py`:一位有效数字的数不再列出(第一次实战列了 170 行,多数是段落里的单个数字);修后 74 行,去掉更正段与
  4 条无关的"别处"档后,要改的就是表与正文里那几处。
- `tools/fingerprint.py` 多录一项 `optical/stat`(带 Tx FFE 的光链路走统计引擎):此前没有预设或合成链路走到这条路径。

### 测试
- `tests/test_optical.py`:光噪声核对无噪声波形的精确期望(ER 4.5 / 6,1 % 内,旧代码差 22 %);带 host FFE 的线性光链路铁律 3
  (1 / 1.5 – 1.5×,旧代码 3.0×)。三条在旧代码上全挂。

### 已知
- ADC 收端的统计引擎报一个 UI 内的最优相位,时域 MM CDR 在 FFE 自适应下停在初始相位;浴盆窄时差一个采样就是几倍(CPO + FFE、
  c = 0.5:0.35× vs 0.91×)。ROADMAP P2 #12。

## [未发布] — 示例输出入库与文档数字漂移检查

### 新增
- `examples/expected/`:40 个示例的全尺寸 stdout(计时与仓库绝对路径已屏蔽)。
- `tools/example_drift.py`:`compare` 把新的全尺寸输出与期望输出逐行比对,对每个变了的数,列出文档里能舍入成旧值、
  却舍入不成新值的引用(按所在段落 / 小节 / 别处分三档放宽精度要求);`accept` 刷新期望输出。搜 README、ROADMAP、CONTRIBUTING、
  `docs/`、`cairn/` 专题文档与示例的 docstring / 注释,不搜 CHANGELOG 与 LOG。
- CI `examples-full` workflow:每个示例按原尺寸跑,再与期望输出比对(4 核约 10 分钟),输出存为 artifact。在改动库、app 层、示例、
  `configs/`、`data/`、`pyproject.toml` 的 PR 与 main push 上跑,另每周一次。

### 变更
- 示例 29 打印 AMI_Init 与原生实现之差的上界(< 1e-15),不再打印残差本身:残差随编译器与 CPU 变(本地 1.73e-18、CI 3.47e-18),
  是两台机器之间唯一不同的输出。`docs/COMPARISON.md` 同步。

### 测试
- `tests/test_example_drift.py`(10 条):期望输出与示例一一对应且已规范化、计时与路径屏蔽、数字切分、舍入判定、
  同形行的数字配对、示例只取 docstring 与注释、引用识别、三档范围、`compare` / `accept` 的退出码。

## [未发布] — 预加重过激光器曲线(研究)

### 变更
- 示例 32 第 3 问加一列:每个均衡组合再过一颗 L-I 压缩 0.2 的激光器(曲线 + 零光功率下限)。结论:过冲几乎不罚(下限截到 0.5–1 %
  样本只差 ≤ 0.1 dB),电平压缩才罚(0.3–0.4 dB,裕度小时更多);原"无余量"口径对这颗 VCSEL 过悲观(CPO + FFE 10.1 vs 12.2 dB)。
  `cairn/光互联建模.md` §6。库代码未动。

### 已知
- 统计引擎在 c = 0.5 + 16 dB 段 + 驱动器 CTLE 9 dB 时比时域悲观 2.5–2.8×(6 dB 时 1.80,c ≤ 0.2 时 1.06–1.35);ROADMAP #11。

## [未发布] — LPO 模块的驱动器 / TIA CTLE

### 新增
- `OpticalConfig.drv_ctle_db` / `tia_ctle_db`:OIF CEI-112G-LINEAR 允许 LPO 模块驱动器与 TIA 各带的 CTLE(`afe.Ctle`,极点在 Nyquist 与
  2×Nyquist,直流增益 1;0 = 不插入,804 值指纹逐位同前)。驱动器的进 `pre_pd` / 曲线前的 drive / `transmitter_power`(TP2 的光),
  TIA 的进 `post_pd`(在 PD 噪声之后)。`ChannelModel.module_ctle`;GUI / 手机表单两项;校验 [0, 12] dB。
- 示例 32 第 3 问:100 m 处 OMA 裕度对 host Tx FFE、驱动器 CTLE、TIA CTLE 的各种组合,每行给有 / 无激光器过冲余量两个口径。
  结论:PD 噪声之前的均衡(host FFE、驱动器 CTLE)收回裕度,之后的(TIA CTLE)小亏;原"CPO 多 3.1 dB"是两边都不做发端均衡时的比较。

### 测试
- `tests/test_optical.py`:CTLE 的位置(`pre_pd` / `post_pd` 恰好乘上各自的传函,电平功率与噪声尺度不变,0 dB 与不设逐位同)、
  TIA CTLE 抬 PD 噪声而驱动器的不抬、带模块 EQ 的铁律 3(0.98–1.01);`test_config_validation.py` 两个越界值。

## [未发布] — 统计引擎建模大信号 E/O 曲线对 ISI 的弯折

### 变更
- 统计引擎在 `li_compression` > 0 时不再只用曲线的稳态电平:`optical_stage.curve_pattern_offsets` 在配置的码型上(无噪声、
  同一组冲激与曲线调用)算出曲线链路与线性模型之差,按相位与"当前符号 + 两个最近邻"分箱取均值与方差;统计引擎把均值加进
  这些箱的平移、方差并进箱内噪声(光噪声已用同样的分箱)。四条链路(VCSEL / EML ADC、VCSEL MS NRZ)c = 0…0.5 统计 / 时域
  从 0.30–1.16 到 0.86–1.28。原来的 warning 只在显式传 `level_sigma`(绕过分箱)时保留。曲线关闭时逐位不变(804 值指纹)。

### 测试
- `tests/test_optical.py`:EML ADC 与 MS NRZ 在 c = 0.4 的 1.5×(原只用稳态电平时 0.42 / 0.57)、恒等曲线下修正为 0、平移随邻符号变、
  显式 `level_sigma` 发 warning;替换原"统计引擎说自己不在铁律 3 内"的测试。

## [未发布] — 贡献指南与数值指纹工具

### 新增
- `CONTRIBUTING.md`:先读什么、环境、推送前检查(与 CI 相同)、CI 各 job 守什么及何时触发、按改动类型的额外检查、
  两端验证的判据、约定、记录与 PR、许可。README 文档索引改指向它与 `AGENTS.md`(原指 `CLAUDE.md`,它只是一行存根),并加"许可"一节。
- `tools/fingerprint.py`:`record` 把每个预设跑过静态 / 时域 / 统计引擎与 COM,外加预设到不了的合成链路(两种时钟 + Tx FIR + Tx 带宽、
  前端重建、Tx 沿偏移、光链路与 E/O 功率曲线、重定时级联),标量按 `repr`、数组按 SHA-256 记;`compare` 逐值比对,有差异退出 1。
  两条命令都单独列出记成报错的条目。约 1 分钟、804 个值。测试 `tests/test_fingerprint.py`。

### 修正
- ROADMAP #10 原记"`LICENSE` 尚缺"不实:MIT 的 `LICENSE` 自 2026-09-12 就在根目录。
- `cairn/architecture-invariants.md` 实践指南:全套测试"约 2 分钟"→ 约 12 分钟;ruff 路径与 CI 对齐;Android 不止三个 job
  (编译版两个);测试数并不由 `test_docs_fresh.py` 守着。

## [未发布] — 四光标 PR 目标的评估(不实现)

### 新增
- `tools/pr_target_length.py`:在示例 38 的 224G LR 链路上估 PR 目标每多一个光标换多少 reach。基带 Monte Carlo:联合 MMSE 的
  (FFE, 首一目标),库里的 Viterbi 跑 FFE 实际输出的目标 + 残余光标;一个噪声比例拟合到实测的 delta reach,前两档对照实测。
  测试 `tests/test_pr_target_length.py`(目标解与 `mmse_pr_target` 一致、分块 Viterbi 与整段一致、三光标胜 delta)。

### 变更
- ROADMAP #8 余项"更长的目标"移入"边界":第四个光标约 +0.5 dB(MC 原值 +0.65,按它对前两档的高估比例折算),恰在事先定的
  0.5 dB 门槛上;按成本(内核判决、定点数据通路、统计引擎判决模型各多带一个受控光标)暂不做,写了重开条件。
  `cairn/DSP发端与PR.md` §12。库代码未动。

## [未发布] — 系统框图由示例 03 的运行生成

### 新增
- `tools/draw_system_diagram.py`:跑示例 03(框图描述的那条 32G NRZ 链路),从它的配置与结果取图上每个数,
  写 `docs/figures/nrz32_system_diagram.png`、`docs/nrz32_system_diagram.mmd`,并刷新 `docs/summary.html` 里内嵌的副本。
  测试 `tests/test_system_diagram.py`(桩结果,不跑仿真)。

### 修正
- 框图原是手画的("参数为实际仿真值"),停在 2026-08:slicer SNR 14 dB、判决 ±17.3 mV、CDR Kp = 1/32、CTLE "6–9 dB";
  现为 15.1 dB、±17.4 mV、Kp = 1/64(配置里的 kp_shift 6)、CTLE 峰值 6 dB(实际 3.1 dB)。summary.html 的内嵌副本同样过时,一并换掉。

## [未发布] — 修正:sliding 检测器不修正接收机的判决

### 修正
- `post_detect(..., "sliding")` 丢掉调用方的判决,从忽略被建模后光标的最近电平切片起步。单比特翻转检测器修不了这样留下的相邻成对错误:
  - 时域引擎 + PR 目标 1 + 0.5D:sliding "MLSD" 把 SER 从接收机自己的 2.1e-5 拉到 3.3e-2(浮点与定点都如此,定点路径照抄了这个起步);
  - 示例 27:sliding 在所有噪声下都不如 DFE(σ = 0.2 时 1.5e-4 vs 0)。
  现在 `post_detect` 接受 `dec0`(引擎传接收机 / 定点环路自己的判决),不给时从反馈该后光标的判决起步(与 RTL 向量生成器同一做法)。
  修后示例 27 每个 σ 都是 DFE > sliding > Viterbi(σ = 0.27:1.7e-4 > 6.0e-5 > 2.0e-5);PR 1 + 0.5D 下 sliding 不再劣化(= 接收机判决)。
- 三光标 PR 目标配 `rx.mlsd.kind: sliding` 现在报错:检测器(及其定点 / RTL 版)只建模一个后光标,第二个受控光标被当误差
  (修前 / 修后都把 SER 0 拉到 ~2e-2)。用 viterbi。
- 无 PR 时引擎结果不变(切片与接收机判决只在训练段不同,不计分);浮点 / TX 黄金指纹逐位同,RTL lockstep 七段过,示例 30 输出逐字同。
- 示例 27 的 "ideal DFE" 改称 DFE(它反馈自己的判决,有误码传播;解析增益是对无差错 DFE 的)。

## [未发布] — 示例全尺寸复核(ROADMAP P3 #10)

### 修正(文档与示例 docstring 里漂移的数字,40 个示例按原尺寸重跑后逐条对照)
- 级联 FEC 的可容忍 pre-FEC BER 写成 "2.2e-5 → ~7e-3(300 倍)":KP4 在 1e-15 目标下的门限是 2.2e-4,BCH(255,215) 6.9e-3,
  是 **31 倍**(SUMMARY、summary.html、COMPARISON、示例 20)。
- 示例 19 docstring 与图题:31.8 → 32.4 → 38.7 dB、"~6 dB" → 31.6 → 32.4 → 37.7 dB、~5 dB;示例 21 的两个杠杆 +6 / +3.5 → +5 / +4 dB;
  summary.html 的全栈表把 +3.0(FEC)/ +4.5(ADC)标反了。
- 示例 18:MLSD 恢复 "3–5×" → 1.9–4.4×;"残余光标 < 0.002" → ≤ 0.002(实测 0.0022,四处)。
- 示例 15(summary.html):post-KP4 7.5e-50 / 5.8e-44 → —(0 误码);"224G 下 TI 失配全面显现" 与输出(各 lane 0 错)不符,改写。
- 示例 03 内眼 6.5 → 6.8 mV;README 的架构对比(mixed-signal 在 −10 到 −15 dB 间崩、ADC 到 −25 dB 0 误码)与定点墙(≥5 bit);
  示例数 39 → 40;summary.html 的规模数(3,700 行 / 106 项测试 / 22 个脚本)同步为 SUMMARY 的。
- 示例 29:AMI Init 与原生实现差 1.7e-18(机器精度,不是逐位),GetWave 逐位 —— COMPARISON 原把两者说反;示例 23 的 COM 在这条扫描上
  始终高于 3 dB(最低 6.8),docstring 改正。
- 示例 30–35 的 cairn 数:MLSD 实测增益 1.20–1.94×、clock_profile 的 ADC 行 0.95 / 1.22 / 0.92、示例 32 的 reach 表体
  (214 / 159 / 32 m,3.1 dB)、示例 34 的带 DFE 扫描与 R_LM、示例 35 的 DAC / 压缩代价(−0.04 / 1.5–1.8 / 0.09 dB);
  阶梯 pre-FEC 地板改为 0 误码的半计数上限 1.3e-6(旧的 ~1e-5 是已修的尾部无效判决)。
- 示例 37 / 38:COMPARISON 仍写 "PR 整形未建模"、README 的发端流水线仍写 "[PR 占位]";示例 38 的 45.5 dB 一行 0.32 → 0.18。
- `docs/nrz32_system_diagram.mmd` 的 SNR / 判决电平 / 内眼同步;对应 PNG 不是由它生成的,未重画,SUMMARY 在图下注明。

### 新增
- `tools/run_examples.py --save DIR`(保存每个示例的输出,供与文档、与上一次全尺寸运行对照)与 `--jobs N`(并行,结果按示例序输出)。

## [未发布] — GUI 文档与覆盖(ROADMAP P3 #10);ADC 统计结果在 GUI 里没套 FFE

### 修正
- app 层(桌面 GUI 与手机 API 共用)调统计引擎时不传 FFE,ADC 收端的 StatEye 是未均衡信道:106 GBd 预置 BER 0.16 /
  SER 0.32,与时域 2.3e-4 并排显示(Single Run、双引擎页、串扰基线、reach 扫描)。新增 `runner.stat_equaliser`:
  有时域结果用它收敛的 FFE,只跑统计用 MMSE 起始 FFE(与 `engine/cascade.py` 一致)。修后同预置 2×10⁵ 符号:时域 2.29e-4、
  统计 2.40e-4。混合信号收端不变;库层与黄金指纹不变(逐位同)。
- ADC 页"Per-lane mismatch"图:offset 是伏特、skew 是过采样样本,却按 "code" / "UI" 标注直接画 —— 10 mV 读成 0.01、
  0.05 UI 在 osr 16 时读成 0.8。改为 mV 与 UI(`lane_mismatch_fig(adc, osr)`)。

### 新增
- `tests/test_gui_panels.py`:每个标签页在"未运行 / 运行失败 / mixed-signal / ADC / 时钟剖面"记录上渲染,并断言用户该读到的文字
  (门控说明或数据卡片);侧栏回调经 Dash 的 `__wrapped__` 直接调用(预置载入、YAML 导入导出、实时派生值、运行、切页);
  桌面启动器的端口与就绪探测。GUI 行覆盖 61% → 89%。
- GUI 函数 docstring 37% → 100%,app 层 57% → 88%。

## [未发布] — TDECQ 的 802.3dj 参考 DFE(ROADMAP P3 #11)

### 新增
- `analysis/tdecq.tdecq(..., dfe=True, dfe_max=0.3)`:15 抽头 FFE 之后的 1 抽头参考 DFE —— 0 ≤ b ≤ 0.3,FFE 抽头和 = 1 + b,
  OMA / 阈值在 FFE 输入,判决用发送符号,C_eq 只算 FFE;最小二乘起步(b 越界则钳住后重解 FFE),b 参与坐标搜索。
  `TdecqResult.dfe_b`。条文按 dj 意见处理材料的检索摘要转述(原文被代理拦截)。
- GUI / Android 的 TDECQ study 在 ≥ 80 GBd 时带 DFE;示例 34 的 EML 带 DFE 并打印只 FFE 的对照,带宽扫描下延到 25 GHz:
  基线 2.08 → 1.88 dB,最小带宽 34.59 → 29.83 GHz。
- 测试:单后标闭式 10·log10(1 + h)(差 < 0.01 dB)、b 的上下界、200G EML 上 DFE 不劣于只 FFE 且慢激光省得更多。
  `dfe=False` 与改前逐位同(main 对照);浮点黄金指纹逐位同。

## [未发布] — 前台校准、校准字分辨率(ROADMAP P3 #7 余项)

### 新增
- `adc.cal.mode: foreground`(`fg_samples` 默认 4096、`fg_ref` 默认 0.8):上电时每 lane 经自己的量化器与 ENOB 噪声测短接输入(offset)
  与 ±参考(增益),修正冻结、折进量化器前的 offset / gain(近似);单独随机流,链路实例不变;不校 skew;定点下数字校准关闭。
  `extras["adc"]` 新增 `true_offsets` / `true_gains` / `fg`。GUI / Android 表单加该选项与两个字段。
- 示例 39 第三部分:有 ENOB 噪声时前台 1024 次即到理想(26.90 dB);无噪声时停在量化误差 q/√12(0.70 mV,29.21 vs 理想 29.74 dB),
  不如后台(29.55 dB)。
- 分辨率扫描(USAGE §19):`cal_gain_bits` ≥ 9、`cal_frac_bits` ≥ 2 饱和。

### 修正
- 定点 / RTL 的 gain 校准没有钉住公共增益:LMS 只把各 lane 拉齐,各 lane 一起缩放对它不可见,寄存器舍入噪声在这个方向上随机游走 ——
  默认 14 位看不出,9–10 位时公共增益漂到 0.7–0.85、SNR 掉几 dB(扫描曲线不单调才发现)。现在生效增益 = 寄存器 − 平均 + 1
  (`cpm[1]` / SV `cgsum` 记和,平均是移位),`_cal_word_py`、replay、`rtl/adc_dsp_loop.sv` 同一份;`vectors/cal` 重新生成,
  record / replay 的 `cal` 返回生效增益。默认 14 位下定点 SNR 变化 ≤ 0.03 dB;浮点黄金指纹逐位同。

## [未发布] — 定点 / RTL 的 skew 修正(ROADMAP P3 #7 阶段 4)

### 新增
- `dsp/fixed_loop._skew_step_py` / `_trim_py`:每 lane 一个整数延时修正寄存器(相位寄存器单位),由该 lane 在校准后字上的
  MM 鉴相器驱动;PI 码 = `(ph − (cts[l] − Σcts >>> lane_shift)) >>> pi_sh`(生效值零均值精确到 LSB)。闭环与 replay 同一份。
- 定点不再拒绝 `adc.cal.mu_skew`。`rtl/adc_dsp_loop.sv` 同步;`vectors/cal` 加 0.04 UI skew 并比对末态修正寄存器。
- 测试:修正量零均值、确实拉开 PI 码;replay / JIT 测试含 skew;定点与浮点 SNR 差 < 0.4 dB(实测 0.15–0.40)、修正量与抽到的 skew 相关 > 0.9。
  校准在浮点 / 定点 / RTL 三层全部闭环。

## [未发布] — 定点 / RTL 的 ADC 后台校准(offset / gain,ROADMAP P3 #7 阶段 3)

### 新增
- `dsp/fixed_loop._cal_word_py`:ADC 字与 FFE 之间的整数校准 —— offset 寄存器(F 位小数)按 `rnd(d, sh_o)` 更新;
  gain 用硬件形式的 LMS 把本 lane 修正后的功率拉向各 lane 的滑动平均功率(不开方、不除法);移位为负表示该项关闭,
  移位前加半 LSB 舍入。闭环与 replay 都调用它;记录里 `xin` 改为原始 ADC 字(replay 与 RTL 的输入)、`x_cal` 为校准后。
- `NumericConfig.cal_frac_bits` / `cal_gain_bits`;`build_fixed_loop(adc_power=...)` 由浮点跑出的 ADC 输出功率定 gain 步长。
- 定点模式不再拒绝 `adc.cal`(skew 修正除外)。
- `rtl/adc_dsp_loop.sv` 加同一份校准,`run_lockstep.sh` 的环路对照第四段(`vectors/cal`),末态寄存器也比对。
- 测试 `tests/test_fixed_cal.py`:独立参考(含步长 0、关闭、饱和);replay 复现闭环含寄存器;JIT = Python;
  关闭时原始字即校准后字;2⁻⁸ / 2⁻¹⁰ 下定点与浮点 SNR 差 < 0.3 dB(实测 0.13–0.19)。

### 过程中修掉的
- 第一版用"右移 62 位"表示步长 0:负数算术右移 62 位得 −1,寄存器每次转换往下漂,真值冻结也只有 21.6 dB(应为 26.7)。
  改为负移位表示关闭,并统一做舍入移位。

## [未发布] — 修正:`adc.calibrated` 改变了链路的随机实例

### 修正
- `TiAdc` 在 `calibrated=True` 时跳过 offset / gain 的随机抽取,于是之后抽的 skew、ENOB 噪声和 Rx 时钟都换了值 ——
  "理想校准"与"未校准 / 后台校准"比的是两条链路。现在照常抽取再置零,三者是同一实例。
  只影响同时设了 `calibrated` 与非零失配 sigma 的配置(没有预设这样设;浮点黄金指纹逐位同)。
- 由此撤回 #38 里记的观察"有 skew 时 offset / gain 环收敛明显变慢(4×10⁵ 符号差理想 2 dB)":修正后同一链路上
  后台 offset / gain 距理想 0.04 dB(4×10⁵ 符号)/ 0.05 dB(10⁶,示例 39)。那 2 dB 是两组 skew 的差,
  10⁶ 与 4×10⁵ 符号又因 `n_symbols` 改变抽取量而是不同实例,才显得"随运行长度变化"。
- 测试:理想校准与未校准抽到同样的 skew;同一链路上后台 offset / gain 距理想 < 0.3 dB(有 skew)。示例 39 第二部分的理想值 24.31 → 24.17 dB。

## [未发布] — TI-ADC 后台校准,阶段 2:skew(ROADMAP P3 #7)

### 新增
- `AdcCalConfig.mu_skew`(默认 0 = 不校):每 lane 的 Mueller-Muller 鉴相器调本 lane 采样延时,修正量保持零均值(公共相位归 CDR)。
  `extras["adc_cal"]` 多一行 —— 延时修正(样本)。GUI / Android 表单加一个字段。
- 示例 39 第二部分:offset + gain + skew 同时失配,对照只开 offset / gain 与三个都开。
- 测试:修正量收敛到 lane 相对 skew(相关 > 0.95、残差 < 0.35×)、零均值、无周跳;三种失配时三个环一起开距无失配 < 0.5 dB;
  分块 / 流式逐位一致、JIT = Python 都加了 skew。`mu_skew` = 0 时阶段 1 的结果逐位不变。

## [未发布] — TI-ADC 后台校准,阶段 1:offset / gain(ROADMAP P3 #7)

### 新增
- `AdcConfig.cal`(`AdcCalConfig`:`mode` off | background、`mu_offset`、`mu_gain`):ADC 内核里逐 lane 的后台校准 ——
  量化器后数字修正 `(q − ô)·ĝ`,offset 取 lane 滑动均值,增益按 lane 功率对齐到各 lane 平均;增益在每 lane 满 1/mu 次转换前保持 1。
  `extras["adc_cal"]` 给出末态估计。`calibrated`(理想模型)与之互斥;定点模式开校准报错。GUI / Android 表单加三个字段。
- 示例 `39_adc_calibration.py`:步长 2⁻⁸…2⁻¹⁴ 的稳态 SNR、残差、时间常数,对照未校准与理想。
- 测试 `tests/test_adc_cal.py`:真值冻结 = 理想模型;offset 收敛到 lane 均值;稳态 SNR 在小步长下距理想 < 0.8 dB、
  残差随步长缩小;分块 / 流式逐位一致;JIT = Python;关闭时不留状态、互斥与校验。

## [未发布] — 示例冒烟运行(ROADMAP P3 #10 的一项)

### 新增
- `tools/run_examples.py`:逐个在独立解释器里跑 `examples/NN_*.py`(Agg 后端);`--smoke` 只在子进程里把每个
  `SimConfig.n_symbols` 压到 `--cap`(默认 2 万),库与示例本身不动;缺可选后端(pyibisami / pllsim / galois)记为跳过。
- CI `examples` job:每次 push 跑全部 39 个示例(~6 分钟)。
- 测试:运行器的上限覆盖每个 `SimConfig`(含 `dataclasses.replace`)、坏示例让运行失败、缺可选后端是跳过;
  文档里提到的 `examples/NN_*.py` 必须存在。

### 修正
- README 快速开始里的两个示例文件名早已改名(`01_nrz_link.py` → `01_nrz32_minimal.py`,
  `05_adc_link.py` → `05_adc_rx_pam4_224g.py`),照抄会报文件不存在。

## [未发布] — 流式模式覆盖光链路、串扰、AMI Init、抖动分解(原 ROADMAP P2 #2)

### 新增 / 变更
- `sim.stream` 支持光拓扑:两段(相位节点前后各一条 FIR,中间按样本功率注入光电二极管噪声)与带 E/O 大信号曲线的三段;
  光噪声逐样本用默认引擎同一批白噪抽样(拷贝生成器、主生成器跳过),两种引擎是同一个链路实例。
- 串扰攻击者流式:同一种子抽同样的符号(`XtalkAggressor.symbol_volts`),保持后过耦合 FIR。
- Init 流程的 AMI 模型(并进冲激响应)流式;GetWave 流程仍报错,记入 ROADMAP「边界」。
- `collect_jitter` 流式:`analysis.jitter.CrossingCollector` 按块收集过零点,与整段 `edge_crossings` 逐位同;
  `calc_jitter` 拆成 `edge_crossings` + `jitter_from_crossings`(默认引擎结果不变)。
- 测试:每条新路径"流式 vs 默认统计一致 + 流式分块(997 / 1 符号)逐位一致";无接收噪声的光链路判决与默认引擎逐个相同;
  不加 Tx 极点时 Tx 级 TIE 与默认引擎逐位同;开 `collect_jitter` 不改变任何结果。

## [未发布] — 定点 Viterbi MLSD(原 ROADMAP P1 #1 收尾)

### 新增
- `dsp/fixed_viterbi.py`:Viterbi 的整数版 —— 每 (状态, 新符号) 一个期望字(光标字 × 电平,带舍入)、分支度量
  平方右移 `sq_shift`、路径度量饱和到 `numeric.mlsd_metric_bits`、每步减最小度量归一化、加比选顺序与浮点核一致、全程回溯。
- 定点模式下 `rx.mlsd.kind: viterbi` 跑整数版(此前在定点 slicer 值上用浮点),PR 目标下的网格同浮点(头部 [1, a] + 残差)。
- `rtl/viterbi_mlsd.sv` + `tb_viterbi_mlsd.sv`,`run_lockstep.sh` 第六段(PAM4 1 + 0.6D + 0.2D²,16 状态)。
- 测试:精确输入上与浮点 `viterbi_mlsd` 逐符号一致(memory 1、2);字典式独立参考(移位、饱和);
  归一化后窄度量与宽度量判决相同;JIT = Python;引擎接线(delta / PR)可重放、误码与浮点同量级。

### 里程碑
- ROADMAP P1 清空:定点 / RTL 黄金模型覆盖 ADC 接收机的全部数字后端。

## [未发布] — 定点 PR 整形(原 ROADMAP P1 #1 的 PR 部分)

### 新增 / 变更
- `dsp/fixed_loop.py`:数字后端加部分响应,与浮点核 `pr_mode` 一一对应 —— 1 + aD [+ bD²] 减受控光标
  `(a·L[x̂ₛ₋₁] + b·L[x̂ₛ₋₂]) >>> fl` 再切、DFE 从受控光标之后在线符号估计 x̂ 上反馈、a / b 的整数 LMS(带保护位、钳位);
  预编码 1 + D 走合成电平切片与 mod N 判决;PR 下鉴相器读减去光标后的值。
- 定点模式不再拒绝 `pr.target`;结果里的 `pr_alpha` / `pr_target` 是定点环路最终的 a、b。
  定点 sliding MLSD 在 PR 下取 a 为残差、从平铺切片出发(与浮点 `post_detect` 一致)。
- `rtl/adc_dsp_loop.sv` 同步;`run_lockstep.sh` 的环路对照跑三遍(delta、自适应 1 + aD + bD²、预编码 1 + D)。
- 测试:独立整数参考扩到 4 种目标 × 4 组随机参数;宽字长下三种 PR 与浮点 ADC 内核的判决、相位、a 一致;
  默认字长下三种 PR 的误码与浮点同量级。

## [未发布] — 定点训练与整数 LMS(原 ROADMAP P1 #1 的自适应部分)

### 新增 / 变更
- `dsp/fixed_loop.py`:数字后端加数据辅助训练与 FFE / DFE 整数 LMS —— 权重累加器比权重细 `numeric.lms_guard_bits`
  (默认 24)位,步长取最接近 `mu` 的 2 的幂,更新带舍入加法器,累加器饱和到权重范围。
- 定点模式现在从浮点运行的**初始**权重与同一训练日程出发自己训练、自适应(此前是拿浮点训练完的权重冻结);
  结果里的 `ffe_taps` / `dfe_taps` 是定点环路最终的权重。
- `float_equivalent_mu()`:定点步长精确对应的浮点步长;宽字长时与浮点 ADC 内核(训练 + LMS + CDR)收敛到同一组权重
  (差 < 2% 的权重行程)。独立整数参考覆盖训练与 LMS。
- `rtl/adc_dsp_loop.sv` 同步加训练与 LMS(`ref` 是 SV 关键字,输入名为 `ref_sym`),testbench 另比对最终权重;
  向量用较大步长与短启动,窗口覆盖 CDR 收敛、训练、判决导向自适应。

## [未发布] — 定点 sliding-detector MLSD 与 RTL 对照(原 ROADMAP P1 #1 的 MLSD 部分)

### 新增
- `dsp/fixed_mlsd.py`:DragonPHY 式误差事件后检测器的整数版 —— 逐电平反馈表、整数残差、平方右移 `sq_shift`
  后求和并饱和、margin 换算到度量 LSB、两遍;假设翻转时按定义重算受影响的两个残差(不减预算签名,
  舍入后两者不等)。`numeric.mlsd_metric_bits`(默认 24)。
- 定点模式下 `rx.mlsd.kind: sliding` 跑整数检测器(在定点环路的 slicer 字上),`extras["fixed"]["mlsd"]` 记录;
  `viterbi` 仍为浮点。
- `rtl/sliding_mlsd.sv` + `tb_sliding_mlsd.sv`,`run_lockstep.sh` 第三段(向量:强残差后光标的合成流,81 次翻转)。
- `tests/test_fixed_mlsd.py`:纠错增益;浮点运算精确时与浮点检测器逐符号一致;独立参考(非均匀电平、移位、饱和、margin);
  JIT = Python;引擎接线可重放。

## [未发布] — 定点 CDR 闭环与 RTL 对照(原 ROADMAP P1 #1 的 CDR 部分)

### 新增
- `dsp/fixed_loop.py`:ADC 接收机数字后端连同 MM CDR 全部 int64 —— FFE / DFE / slicer、MM 鉴相、移位增益环路滤波、钳位、
  环路时延、相位寄存器 → 相位插值码 → 采样点。`run_fixed_loop` 闭环跑波形,`replay_digital` 只跑 ADC 字之后的部分,二者逐位相同。
- `numeric.mode: fixed` 第一次真正生效(此前时域引擎完全不读它):ADC 架构下浮点训练后用冻结的量化权重再跑定点闭环,
  结果与 `extras["fixed"]` 都是定点环路的。新字段 `numeric.pi_bits`(默认 7)、`numeric.phase_frac_bits`(默认 24)。
- `rtl/adc_dsp_loop.sv` + `tb_adc_dsp_loop.sv`:独立 SV 实现,`run_lockstep.sh` 第二段逐位比对 PI 码、slicer 值、判决
  (向量来自 Rx 时钟抖动 + 钳位 + 时延下的闭环,PI 码确实在动)。
- `tests/test_fixed_loop.py`:重放 = 闭环;独立整数参考(含右移、钳位、时延、两种 PD 输入);JIT = Python;
  宽字长收敛到浮点 ADC 内核(用 `float_equivalent_gains`);默认字长与浮点链路误码同量级;不支持的组合报错。

### 变更
- `numeric.mode: fixed` 配 mixed-signal、PR 目标、`sim.stream` 或非 2 的幂 lane 数现在**报错**(以前静默按浮点跑)。

### 不变
- 浮点路径全部预设指纹逐位同。

## [未发布] — 流式时域引擎 `sim.stream`:长跑内存不再随码长增长(原 ROADMAP P1 #2)

### 新增
- `sim.stream`(默认 False;GUI / Android 表单 "Stream waveform (long runs)"):波形按固定块生成(`engine/stream.py`:
  抖动 ZOH 窗口、驱动曲线、FIR 级、噪声源),两个接收机内核读滑动窗口(`MsRxRun` / `AdcRxRun.set_window`,
  core 新增 `y_off` / `n_total` 与"窗口不够"状态码)。10⁶ 符号 OSR32 峰值 RSS 1.89 GB → 0.33 GB,耗时 16 s → 5 s;
  5×10⁶ 符号 @OSR16 13 s / 0.70 GB(冒烟测试)。
- 流式内部任意 `chunk_symbols` 逐位一致;与默认引擎是同一链路实例(同一批白噪与其他随机抽取),只把两个整段循环 FFT 算子
  (噪声砖墙限带、`tx.bw` 单极点)换成等价 FIR,结果统计一致(SNR 差 < 0.1 dB,误码数在 Poisson 内)。
- 流式暂不支持 IBIS-AMI、光拓扑、串扰、`collect_jitter`(明确报错);见 ROADMAP P2 #2。

### 不变
- 默认路径(`sim.stream=False`)全部预设指纹逐位同(506 + 44 值)。

## [未发布] — 接收机内核分块续跑:时域长跑可报进度、可中途取消(原 ROADMAP P1 #3)

### 新增
- `cdr.kernels.MsRxRun` / `cdr.adc_kernel.AdcRxRun`:内核循环拆成 `[k0, k1)` 的 core,循环状态(相位、积分器、DFE/FFE 抽头、
  LMS 累加、环路延迟队列、PR 的 a/b…)全在参数里;任意分块与整段**逐位一致**。`ms_rx` / `adc_rx` 签名与行为不变。
- 时域引擎按 `sim.chunk_symbols`(默认 65536)分块推进;`run_time_link(progress=)` 每块后回调 `(done, total)`,回调抛异常即中止。
- App 层:`run_link` 的 progress 在引擎内带 fraction;`poll` 新增 `fraction`;`cancel` 在下一块生效(原先要等整个内核调用跑完)。
  Android 运行卡与通知在接收机循环内显示确定进度条,取消提示改为"下一块生效"。
- 预设指纹逐位同(506 + 44 值,默认分块已实际生效:预设 200k–400k 符号)。

## [未发布] — Android edge-to-edge,API 35 模拟器(原 ROADMAP P2 #6b)

### 变更
- `MainActivity` 调 `enableEdgeToEdge()`:所有 API 级别都按 Android 15(targetSdk 35 强制)的方式铺满,API 34 模拟器测的就是 15 上的布局。
- Scaffold 内容加 `consumeWindowInsets(pad).imePadding()`:铺满后键盘不再缩窗口而是作为 inset 到达,表单靠它把焦点框滚到键盘上方。
- 新 instrumented 测试 `theTitleAndTabsClearTheSystemBars`:标题在状态栏下、Tab 标签在导航栏上(窗口坐标)。
- Android CI 解释型模拟器 job 改为矩阵 API 34 / 35;报告与截图 artifact 名带 `-api34` / `-api35`。

## [未发布] — 解释型 APK 不再打包桌面 GUI(原 ROADMAP P2 #6c)

### 变更
- `android/app/build.gradle.kts`:Chaquopy 主源集 `exclude("halo_serdes_gui/**")`。手机侧无任何代码 import 它(读的是 `halo_serdes_app`,
  桌面包只是 re-export);编译型 wheel 本来就不含它。桌面端不受影响(源码树未动)。
- `android/tools/inspect_apk.py --absent`:断言某包完全不在 APK 里;CI 对解释型与编译型 APK 都加 `--absent halo_serdes_gui`。

## [未发布] — 桌面版随版本 tag 发 GitHub Release(原 ROADMAP P2 #5)

### 新增
- `build-windows.yml` 加 `release` job:推 `vX.Y.Z` tag 时,用通过 `--selfcheck` 的产物发 Release —— Nuitka onefile exe(带图标)
  与 PyInstaller 文件夹 zip。tag 与 `pyproject.toml` 版本不符即失败、不发;onefile 构建失败(允许)时只发 zip。
- 触发器加 `branches: ["**"]` + `tags: ["v*"]`(只写 tags 会让分支推送不再触发;tag 推送不受路径过滤)。
- onefile 的 `--product-version` 改从 `pyproject.toml` 读(CI 与 `build_nuitka_onefile.ps1`),原写死 0.0.1。

## [未发布] — COM:ADC 架构用 178A 式 Rx FFE、计入 ADC 量化噪声(原 ROADMAP P2 #6)

### 新增 / 修复
- `ComParams.rx_ffe`(默认 `"auto"`:ADC 架构取 `rx.ffe` 大小,mixed-signal 无;`None` 关掉;或 `(n_pre, n_post)`)。
  每个 (CTLE, Tx, 相位) 候选上 FFE 抽头按 MMSE 解:DFE 覆盖的后光标不算误差、接收噪声与串扰经抽头计入;
  ISI / 串扰 / 噪声 / 抖动都在 FFE 之后取。`detail["rx_ffe"]`、`detail["rx_ffe_taps"]`。
- ADC 架构的 σ_N 加上量化噪声(`adc_noise_sigma`,与统计引擎同式)。
- 预设 COM:mixed-signal 不变;ADC 106 GBd −8.35 → 4.58、112 GBd stress −7.15 → 5.42、deep-LR −8.99 → 4.78、TI mismatch −6.23 → 9.26、
  LPO −9.41 → 19.78 dB。示例 26(IL 16.2 dB,含串扰)−5.40 → −1.96 dB;示例 28 无串扰 2.76 → 15.01、12 个干扰 −10.86 → −10.25 dB。

## [未发布] — AMI 模型的 `has_getwave` 默认值统一(原 ROADMAP P2 #4)

### 变更
- `AmiCModel` 与 `load_ami_model(so_file=...)` 的 `has_getwave` 默认由 `False` 改为 `True`,与 `NativeFirAmi`、`IbisAmiModel` 一致:
  不带参数对比两个模型时走同一条流(此前 Init vs GetWave,曾被当成 2.62 dB 的模型差异)。**不显式指定的旧调用现在走 GetWave。**
- `NativeFirAmi(has_getwave=...)` 构造参数;`load_ami_model(has_getwave=False)` 对三种模型都生效(厂商模型 `GetWave_Exists` 为假时仍走 Init)。
- 仓库内调用都显式指定或只调 Init,结果不变。

## [未发布] — 统计引擎建模 PR 逐符号判决(原 ROADMAP P3 #8 余项)

### 新增 / 修复
- `engine.statistical.pr_symbol_decisions`:受控光标由前面的判决减掉、错判会传播,用最近一或两个判决误差的马尔可夫链算 SER;
  预编码 a = 1 走合成电平(不传播)。`StatResult.extras["ser_slicer"]` 报它(对照时域同名键)。
- `rx.mlsd.kind: none` + PR 时统计 BER 改用它:与时域 0.8–1.6×,原按理想抽头 2.7–4.1× 偏乐观。有 Viterbi 时 BER 不变。
  无 PR 时逐位同前(预设指纹 488 + 44 值)。

## [未发布] — 整 UI 周跳后按段重对齐计分(原 ROADMAP 4c)

### 修复
- `score()` 找整 UI 周跳(`engine.scoring.find_slips`:512 符号窗,当前偏移错 > 1/4 且相对 ±4 内某偏移错不到一半才换,
  边界取两段错数和最小处),BER / SER / SNR 按段对齐计;`extras["cycle_slips"]`、`extras["slip_at"]` 报次数与位置。
  此前一次周跳就让之后全部读成机会水平(BER ≈ 0.5)。没有周跳时计分逐位同前。
- 示例 24 在 −18 dB:时域 BER 0.5 / SNR −3.37 dB → 5.51e-3 / 6.98 dB(训练期内滑了一位;统计 0.134)。
  预设指纹只有 `NRZ 32G static` 的时域行变(该链路本就不通:0.4998 → 0.4933,前 ~3000 符号对在 +4 UI)。
- 注入 ±1 UI 采样相位跳:各预设都报 1 次周跳、位置对、BER 回到不注入时的量级(`tests/test_cycle_slip.py`)。

## [未发布] — 统计引擎计入 ADC 量化噪声(原 ROADMAP 4b)

### 修复
- `rx.arch="adc_dsp"` 时统计引擎把 ADC 噪声 σ = fullscale/√12 · 2^(−ENOB)(无 ENOB 或 ENOB ≥ n_bits 时用 n_bits)与
  `rx.noise_rms` 按功率相加,再走 FFE 噪声放大(`engine.statistical.adc_noise_sigma`)。此前只有时域建了它:ENOB 5 的链路
  统计 0、时域 1.46e-4;修后统计 / 时域 0.91(ENOB 5)、0.94(ENOB 6.5)。
- 变化只在 ADC 架构的统计结果:预设指纹 `noise_sigma` +2.4–3.5 mV、SER 变动 ≤ 0.004;示例 31 统计 BER 由 5.8e-16 量级升到
  9.5e-6(时域 2.8e-5)。mixed-signal 与全部时域结果逐位不变。

## [未发布] — 三光标目标的 LMS 跟踪

### 新增
- `pr.adapt="lms"` 支持 (1.0, a, b):内核同时跟 a、b;`alpha_out` 返回两者。示例 38 加 LMS 行:38.58 dB(MMSE 起始解 38.63 dB)。

## [未发布] — 第二个受控光标:1 + aD + bD²

### 新增
- `pr.target = (1.0, a, b)`(a ∈ [0, 2]、b ∈ [−1, 1]);内核 `pr_beta`、`pr_nt`;`dsp.ffe.mmse_pr_target`(`adapt="mmse"` 联合解 a、b);
  统计引擎同号误差事件族(b < 0);`extras["pr_target"]`。
- 示例 `38_pr_three_cursor.py`:无 PR 31.6 dB、MMSE 1 + aD 36.3 dB、MMSE 1 + aD + bD² **38.6 dB**。

## [未发布] — PR 目标的 a 由收端选(`pr.adapt`)

### 新增
- `PrConfig.adapt: none | mmse | lms`、`PrConfig.mu`;`dsp.ffe.mmse_pr_alpha`(闭式 MMSE 单位主光标目标);ADC 内核对 a 的 LMS
  (步长 0 时逐位同前);`extras["pr_alpha"]` = (起始, 结束);表单 `pr.adapt`、`pr.mu`。
- 示例 36 第 4 部分:收端自选 a 的 reach 36.32 dB,固定 a 最优 36.33 dB;a 从 27 dB 的 0.60 随损耗升到 39 dB 的 0.76。

## [未发布] — PR 统计引擎:相邻误差事件的重叠扣掉

### 修复
- `pr_error_events(snr=)`:L ≥ 2 的事件按 P(A_L 且非 A_{L−1}) / P(A_L) 加权(二元正态,Owen T;Hunter 链式上界)。
  发端 a = 0.5 统计 / 时域 2.5–2.9× → 1.8×,进入不变量 3;收端 / 发端其余情形 1.0–1.6×。

## [未发布] — 发端部分响应整形与三方同台(原 ROADMAP P3 #8 阶段 3)

### 新增
- `pr.at: tx`:`TxPipeline.pr_filter` 在 FFE / DAC 之前做 (x_k + a·x_{k−1}) / (1 + a)(峰值归一),并进等效符号响应;收端沿用 1 + aD 检测。
- 示例 `37_pr_tx_vs_rx.py`:无 PR 31.6 dB、收端 a = 0.75 36.3 dB、发端 a = 0.25 / 0.5 / 0.75 / 1 29.7 / 28.1 / 26.9 / 26.9 dB
  (≈ 无 PR − 20·log10(1 + a))。

### 修复
- 1 + aD 脉冲的主光标定位:时域引擎在不含发端 PR 的脉冲上找峰,统计引擎在 PR 开启时取两个可比光标中靠前的一个
  (a = 1 发端 PR、−30 dB:BER 1.5e-2 → 3.7e-3)。

## [未发布] — 接收端部分响应整形 1 + aD(原 ROADMAP P3 #8 阶段 2)

### 新增
- `PrConfig(target, at)` 与 `LinkConfig.pr`;表单 `pr.target`、`pr.at`(桌面与 Android 共用)。ADC 收端的起始 FFE(`zf_ffe` /
  `mmse_ffe` 的 `target=`)、LMS 期望值、DFE 起点、MLSD 光标、MM-CDR 检测器输入都按目标走;a = 1 + 预编码切合成电平。
- 统计引擎 `pr_error_events`:PR + 序列检测器时对交替误差事件在 FFE 着色噪声下做 union bound。
- 示例 `36_pr_rx_alpha.py`:示例 18 信道上 a = 0.75 的 reach 36.3 dB,对照(delta 目标 + Viterbi)31.6 dB(+4.7 dB)。
- `tests/test_pr.py`,并在 `test_mlsd_fec.py`、`test_mlsd_precode_wiring.py`、`test_config_validation.py` 加用例。

### 变更
- `pr.at: tx`、mixed-signal + PR、静态引擎与定点数据通路遇 PR 都拒绝;`target=(1.0,)` 与此前逐位相同。
- 测试 664 项,示例 37 个。

## [未发布] — 统计引擎与时域引擎的一致性(原 ROADMAP P1 1b、P3 #8 的 tx.bw、P3 #11 的分箱)

### 修复
- ADC 路径不再计入 FFE 没判决的尾部 n_pre 个符号(预分配的 0 被当判决打分):ADC 预置的指纹少 3–11 个"误码",
  示例 15 / 16 由 1.5e-6 / 3.5e-6 与 4 / 5 个误码变为 0。
- `tx.bw` 进统计引擎与接收端脉冲分析:`TxPipeline.driver_response` / `equivalent_symbol_response` / `after_dac_response`;
  0.5×baud 驱动器极点下统计 / 静态比从 0.003× 变为 1.00×(NRZ)/ 1.04×(PAM4)。
- Mixed-signal 统计引擎在 bang-bang 环路实际锁定的相位读 BER(此前报全相位最小值);时域接收噪声改为限带到 [0, baud]
  (`engine/lti.receiver_awgn`),插值采样器不再把逐样本白噪声稀释到约 0.8σ。PAM4 MS 统计 / 时域比 0.19–0.70 → 0.88–0.97。
- 光链路统计引擎按两邻符号分箱电平相关噪声(`optical_stage.slicer_sigma_binned`):ER 6 dB 处 0.65–0.76 → 0.94–0.96。

### 变更
- 示例重跑(23 个有变化):224G 阶梯 18 / 32 / 36–38 / 44 dB;杠杆 DSP 深度 +0.8、ADC +5.3、级联 FEC +3.8 dB;全栈 33.2 / 36.2 /
  37.7 / 44.2 dB;示例 18 3 抽头 MLSD 1.9–4.4×;示例 33 159 / 256 / 214 m;示例 35 c = 0.2 代价 1.5–1.8 dB。
- 测试 637 项;`test_jtol_runs_on_a_profile_clock` 取代原单种子方向断言。

## [未发布] — 示例 18 改为量 MLSD 在什么条件下有用;summary.html 换图(原 ROADMAP P2 6d)

### 变更
- `examples/18_lr_ffe_mlsd_fec.py`:同一组损耗跑两种 FFE —— 21 抽头(原配置,示例 19 / 35 仍引用它)与 3 抽头,各报 FFE-only 与
  MLSD memory-2。21 抽头残余光标 < 0.002,MLSD 处处 1.0×;3 抽头留下 h2 ≈ −0.05…−0.11,MLSD 从 −28.8 dB 起 2.4×、−33.3 dB 处
  5.1×,但 3 抽头 + MLSD(2.3e-3)仍不如 21 抽头单独(1.9e-3)。右图改为 MLSD 增益对损耗。结论:delta 目标的 FFE 下 MLSD 只能
  替代抽头;加 reach 要 1+αD 目标(并入 ROADMAP P3 #8)。
- `docs/summary.html`:§03 两张图(示例 15、16)与全栈图(示例 21)换成修复后重跑的输出;MLSD 段落同步。

## [未发布] — 重跑受接收端修复影响的示例,文档数字更正(原 ROADMAP P1 1d)

### 更正
- 20 个示例在修复前代码(4b6650e,复现了文档里的旧数)与本分支各跑一遍:05、06、07、08、15、16、18–21、24–26、28–33、35。
  06、26、28、29、30 输出不变;其余变了,文档按新数改并在原处注明旧数。
- **示例 18:MLSD 在它的扫描上不再有增益**(FFE-only 到 −28.8 dB 零误码,−30 / −33 dB 处 1.0×)。旧的"−27 dB 处 29×"来自
  MM-CDR 锁偏峰值。224G 阶梯 18 → 28 → 29 → 35 → 41 dB 改为 18 → 32 → 36 / 39 → 44 dB:DSP 深度 +0.6 dB(此前 ~1)、
  级联 FEC +3.6 dB(此前 +6)、更好 ADC +6.3 dB(此前 +6),全栈 A 33.2 → D 44.0 dB(此前 29.3 → 41.4)。
- 示例 15:112G / 224G slicer SNR 22.9 / 18.9 → 31.2 / 26.1 dB,224G BER 7.6e-5 → 3.5e-6。
- 示例 33:LPO / retimed / CPO 143 / 249 / 182 → 157 / 254 / 211 m;杠杆表 97 m 起 +62 / +81 / +132 / +185 → 99 m 起 +81 / +107 /
  +132 / +188 m;"不可叠加"的结论不变。示例 32:4 / 8 dB 段 reach 209 / 153 → 211 / 157 m,OMA 裕度不变。
- 示例 35:理想 DAC reach 31.82 dB;DAC 位数的差仍在扫描噪声里,c = 0.2 压缩的代价变成 2.0–2.4 dB。

### 变更
- 示例 19:`reach()` 在扫描内从未越过 1e-15 时以前打印 `<start`(像是"一开始就不过"),改为 `> 末点 dB`;扫描加到 0.28 m,
  ENOB 7.5 的 reach 量出来是 38.7 dB。
- 示例 31:`pd_input` 由 `adc` 改为 `ffe`,与它注释里说"相同"的 `pam4_224g_112g_adc.yaml` 一致;BER 约低 100×,CDR 误差
  模型 / 实测仍在 0.85–1.18×。
- 示例 18 / 19 / 20 / 21 的 docstring 与图标题里写死的结论按新数改;`docs/summary.html` 文字同步,内嵌图仍是旧快照
  (新 ROADMAP P2 6d)。

## [未发布] — MM-CDR 鉴相器输入默认 `auto`(PAM4 读均衡后样本,NRZ 读 ADC 原始样本)

### 变更
- `CdrConfig.pd_input` 默认值 `adc` → `auto`,新属性 `LinkConfig.mm_pd_input` 按调制解析:PAM4 → `ffe`,NRZ → `adc`;
  显式写 `adc` / `ffe` 的配置不变。表单 `rx.cdr.pd_input` 多一个选项 `auto`(桌面与 Android 同一份广告)。
- 依据:48 点 ADC 链路扫描。PAM4 用 `adc` 会锁在未均衡脉冲的 MM 零点上(0.25 m:SNR 17.9 vs 27.4 dB);NRZ 用 `ffe` 在
  轻 ISI 链路上失去 MM 梯度,相位游走 ~0.4 UI。细节与表见 `cairn/DSP发端与PR.md` §5。
- 受影响的现有配置:没写 `pd_input` 的 PAM4 ADC 配置 —— 预置里只有 `pam4_100g_lpo_vcsel.yaml`(时域 9 → 11 个误码,持平),
  以及自己搭 PAM4 ADC 接收机的示例(重跑结果见下一节)。

## [未发布] — 224G ADC 预置的 MM-CDR 改读均衡后的样本(ROADMAP P1 1c 新)

### 变更
- `pam4_224g_adc.yaml`、`pam4_224g_112g_adc.yaml`、`pam4_112g_adc_mismatch.yaml`、`pam4_deep_lr_adc.yaml`:`cdr.pd_input: adc → ffe`。
  `adc` 输入时 MM 鉴相器锁在偏离脉冲峰值 0.15–0.3 UI 处并来回摆;`ffe` 时贴住峰值,且照样跟踪抖动(SJ 0.1 UI @ 0.5 MHz,
  跟踪误差 rms 0.13 / 0.15 / 0.06 / 0.12 UI → 0.03 / 0.06 / 0.03 / 0.04 UI)。时域 BER:3.1e-3 → 2.3e-4、7.8e-3 → 1.6e-5、
  3.8e-3 → 1.3e-5、6.4e-2 → 3.9e-5。其余预置、静态与统计引擎不变。库默认值仍是 `adc`(见 ROADMAP P1 1d;同日后改为 `auto`,见上一条)。

## [未发布] — 接收端起始均衡看见 TX FFE(ROADMAP P1 1c)

### 修复
- 静态引擎与时域引擎两种架构的脉冲分析(RX FFE 起始解、DFE 种子、判决电平尺度、采样相位、符号延时)以前用的是不含 TX FIR
  的信道脉冲,而它们均衡的波形含 TX FIR。现在经 `TxPipeline.receiver_view(h)` 卷进 TX FFE 并扣掉 n_pre UI 的超前量;
  `reconstruct.front_end_waveform` 的眼图相位同理。单抽头 TX 逐字节不变。
- 强 TX FFE(主抽头 0.78)下:静态 NRZ SNR 11.2 → 26.2 dB,静态 PAM4 BER 5.3e-2 → 0,ADC 时域 BER 0.10 → 4.6e-5;
  静态 vs 统计交叉校验(带 / 不带 5 bit DAC,三损耗点)0.83–1.17,以前测不了。
- 指纹:6 个带 TX FIR 的预置的静态 / 时域值变化(475 值中 131 个);统计、COM、单抽头预置不变。4 个 ADC 预置的时域 BER
  高了 1.1–1.6 倍,根因是它们的 MM-CDR(`pd_input: adc`)锁偏 0.1–0.3 UI,见 ROADMAP P1 1c(新)。

## [未发布] — DSP-based TX(阶段 0 + 阶段 1)

### 变更
- **TX 收成一条 `tx.pipeline.TxPipeline`**:`symbol_stage`(电平 → [PR 占位] → FFE → DAC)、ZOH + 时钟边沿偏移(唯一的域切换)、
  `waveform`(驱动器压缩 → 驱动器单极点)、`equivalent_symbol_response`(统计引擎)。原来手拼的七处
  (`timedomain.py` 两处、`static_link.py`、`statistical.py`、`optical_stage.py`、`reconstruct.py`、`cdr_tracking.py`)全部改用它;
  475 值引擎指纹与 44 值 TX 路径指纹(剖面时钟、重建、CDR 追踪、光发射功率、串联)与 main 逐位相同。
  `com.py` 与 `io/ami.py` 的 NativeCom 保留参考 TX,docstring 写明"no DAC / driver model"。

### 新增
- `TxConfig.dac_bits / dac_fs / dac_thermo_msbs / dac_unit_sigma`:`tx/dac.py::TxDac` —— 中升均匀量化(满量程峰峰值,默认 = FFE 峰值,
  不削峰),温度计 MSB + 二进制 LSB 分段的单元电流失配 INL(端点修正后 E[INL²] = σ²(M−1)/6),削峰计数;失配用独立随机流,
  开 DAC 不移动链路的抖动 / 噪声抽样。
- `TxConfig.drv_nl / drv_compression / drv_p1db_v / drv_oip3_v`:`tx/driver.py`,Hammerstein(先压缩后带宽);`"curve"` 复用
  `core/static_curve.py::StaticCurve`(从 `optical/eo.py` 提出来两边共用,c 与 `li_compression` 同一定义,同 c 同输入逐位相同),
  `"tanh"` 按 1 dB 压缩点、`"cubic"` 按 OIP3(HD3 闭式)。`apply_single_pole` 搬到 `tx/driver.py`,`builder.py` 重导出。
- `analysis/tx_metrics.py`:`tx_sndr_db`、`measured_rlm`、`tx_report`(同边沿的理想 TX 作参考,符号中心采样,最小二乘增益对齐)。
- 统计引擎:DAC 折成每 UI 白噪 σ_q² = LSB²/12 + E[INL²]·LSB²,经 DAC→判决器的符号响应(全部游标平方和)到 RX;
  驱动器非线性开启时 warning。`engine/backchannel.py`:有 DAC 时峰值约束 sum|taps| ≤ dac_fs / swing。
- 表单 `tx.dac_*`、`tx.drv_*`(桌面与 Android 共用);示例 `35_dsp_tx_sndr.py`;`tests/test_dac.py`、`tests/test_tx_pipeline.py`。

## [未发布] — 光互联链路(阶段 3:E/O 大信号曲线 + TDECQ)

### 新增
- **`OpticalConfig.li_compression`**(0 = 线性,默认):E/O 静态大信号曲线 `optical.StaticCurve` —— VCSEL 二阶 L-I
  带热翻转(凹,压缩顶电平,κ = c / (2(2 − c)),R_LM = 1 − 8κ/3)、EML 指数型 EAM 吸收(凸,压缩底电平);两端外电平钉住,
  OMA / ER 不变;零光功率下限、翻转峰后保持。`optical_rlm`(802.3 120D.3.1.2 的 R_LM)让 `tx.rlm` 成为驱动器设定、光域
  R_LM 成为导出量;`oe.level_powers` 与接收机电平(`_levels`)随曲线移动。
- 时域引擎在曲线开启时三段卷积:驱动(段 A × E/O)→ 曲线 → 光纤 → PD 节点噪声 → 接收端;曲线关闭时与阶段 2 逐位相同
  (475 值引擎指纹含光链路预置)。统计引擎在曲线开启时发 warning(不在铁律 3 的保证内)。
- **`analysis/tdecq.py`**:802.3 121.8.5 的 TDECQ —— 0.5×baud BT4 参考接收机、抽头和为 1 的 T 间隔 FFE(5 抽头 /
  802.3dj 15 抽头)、0.45 / 0.55 UI 两个 0.04 UI 直方图、阈值 P_ave ± OMA/3、OMA 取游程中心 2 UI、SER 4.8e-4
  (Q_t = 3.414)、C_eq;来源与简化逐条写在模块 docstring。`engine.optical_stage.transmitter_power` 给出 TP2(或过光纤后)
  的光功率波形,含激光 RIN。
- `ChannelModel.band_limited()`:单独使用的块(SMF 色散幅度全通、单极点 EAM)在网格顶端四分之一滚降并延时 32/f_max,
  避免零相位砖墙 sinc 绕回。
- 表单 `topology.optical.li_compression`;`studies.tdecq_study`(桌面与 Android 同一份 study 广告);GUI「Optical」页加
  TDECQ / 光域 R_LM / C_eq 卡片与 ER、激光带宽两条扫描;示例 `34_tdecq.py`;`tests/test_tdecq.py` 与 `test_optical.py` 增补。

## [未发布] — 光互联链路(阶段 2:重定时串联)

### 新增
- **`symbols=` 入参**(`run_time_link` 两条路径、`run_static_link`):外部用户符号流代替 `sim.pattern`;只收整数索引
  (`check_symbols`,浮点数组按波形拒收,铁律 5),给 PRBS 自己的序列时与不给逐位相同。`SimResult.extras["decisions"]`
  带出打过分的用户域判决(从 `warmup` 起对齐)。
- **`TopologyConfig.retimer: none | both`、`retimer_rx`、`retimer_tx`**;**`engine/cascade.py`**:`run_cascade(cfg, statistical=)`
  把链切成 host→段 A→重定时 / 重定时→光路→重定时 / 重定时→段 B→host 三段串联,每段的判决是下一段的符号源,
  端到端 BER 按 host 判决 vs host 符号联合计数,同时报每段 BER 与 1 − ∏(1 − pᵢ);`run_cascade_statistical` 只用统计引擎
  (ADC 收端用时域引擎的 MMSE 起始 FFE),给扫描与界面用。
- 预置 `configs/pam4_100g_lpo_vcsel.yaml`(100G/λ LPO,`retimer: both` 即 retimed);`config_bridge` 加 `topology.retimer*`
  字段,每个预置都能从表单值原样重建(新增守卫)。
- **Optical study**(`studies.optical_study`,桌面 / Android 同一份广告)与 GUI **「Optical」页**(第 17 个标签页):
  级联插损在光电二极管处的切分、每电平 PD 节点噪声、光纤长度 reach(as configured vs retimed)。
- 示例 `33_three_topologies.py`:LPO / DSP retimed / CPO 同光路 reach 阶梯 + 电损耗 / 光噪声 / 重定时三杠杆表;
  `tests/test_cascade.py`。

### 验证
- 三段各自 ~1e-3 / ~1e-2 BER 时端到端在自身 Wilson 区间(z = 3)内等于 1 − ∏(1 − pᵢ),ADC PAM4 与 mixed-signal NRZ 都过;
  重定时光路段 BER < LPO 整链;统计级联 / 时域端到端 2× 内;`topology=None` 的 409 值引擎指纹不变(仅新增 `decisions` 键)。

## [未发布] — 光互联链路(阶段 1:光路作为信道段 + 电平相关噪声)

### 新增
- **`LinkConfig.topology`**(`TopologyConfig(seg_a, optical, seg_b)`,默认 None = 电链路,逐字节不变):
  Host TX → 电段 A → E/O → 光纤 → O/E → 电段 B → Host RX。LPO / CPO / retimed 是同一条链的不同切法。
- **`optical/`**(只依赖 numpy):`eo`(VCSEL 二阶小信号 f_r / damping;EML 单极点)、`fiber`(OM4 高斯模式带宽,
  EMB/L 为 −3 dBo;SMF 色散 cos θ − α sin θ)、`oe`(PD+TIA 二阶 Butterworth;OMA+ER → 四电平光功率)、
  `noise.OpticalNoise`(散粒 2qRP + RIN(RP)² + TIA i_n²,`inject` 给时域、`sigma_per_level` 给统计,一个对象两个引擎)。
- `ChannelModel.cascade(*m)`(同网格点乘,`ref_gain` 连乘:两段理想电信道 H = 0.25 而 `loss_at` 为 0 dB;
  Touchstone 自级联 `loss_at` 恰为 2 倍),`from_topology` 挂 `.optical = OpticalStages(pre_pd, post_pd, noise)`。
- 时域引擎:有 topology 时两段卷积,在光电二极管电流节点注入噪声(O/E 之前,TIA 带宽决定噪声带宽),两种 RX 架构同一路径。
- 统计引擎:每个发送电平一个高斯核(`level_sigma`);核相同时与单核逐位相同。电平 σ 取判决样本沿接收滤波器记忆的
  光功率二阶矩(`engine/optical_stage.py`),对时域判决流分电平实测 2% 内。
- `config_bridge`:`topology.*` 27 个字段(桌面 / Android 共用);示例 `32_lpo_vs_cpo.py`;`tests/test_optical.py`;
  `cairn/光互联建模.md`(功率尺度约定、器件参数核验表、被推翻的判断)。

### 验证
- 铁律 3 在光路上:ADC PAM4 统计/时域 0.88 / 0.71 / 0.63(ER 3 / 4.5 / 6 dB),mixed-signal NRZ 0.92 / 0.84 / 0.86;
  409 值电链路引擎指纹不变。

## [未发布] — PLL 时钟相噪剖面(阶段 3:RX 采样时钟 + 活桥)

### 新增
- **`RxConfig.clock`**(`ClockConfig`,默认理想全零):接收端采样时钟的剖面 / 白噪 RJ / SJ。
  两个内核(`cdr/kernels.py`、`cdr/adc_kernel.py`)新增 `rx_clock_offset_samples` 入参,逐符号加到
  环路相位上再采样,`phase_track` 报告实际采样位置;**全零时与改动前逐位一致**(两条 JIT 路径
  哈希相同,409 值引擎指纹不变)。`cdr/rx_clock.py` 合成偏移,理想时钟不消耗随机数。
- 统计引擎:TX、RX 两份剖面经同一条 `|1−H|` 功率相加;全白噪时 σ = rj_tx ⊕ rj_rx。
- **`io/pll_bridge.py`**:`profile_from_analysis(AnalysisResult)`(无需 import)与
  `profile_from_preset(name)`(函数内 `import pllsim`,缺失时 ImportError 指向 `[pll]` extra);
  pyproject 加 `pll` extra。对 sibling 检出的 pllsim,活桥与 ex22 导出的七个文件逐位一致。
- `config_bridge`:`rx.clock.kind / file / f0_hz / rj_ui` 四个字段(桌面与 Android 自动出现,
  `rx.clock.file` 同为内置剖面下拉)。
- `tests/test_rx_clock.py`。

## [未发布] — PLL 时钟相噪剖面(阶段 2:CDR 追踪与双引擎交叉校验)

### 新增
- **`cdr/linear.py`**:两个数字 CDR 环路的小信号模型(PI 环路 `|1−H(f)|`、`|H(f)|`;BB 鉴相器
  增益自洽定点 + 自噪声;MM 鉴相器增益/噪声在锁定点游标集上做种子化期望)。锁定点由
  `lock_offset_samples` 搜出,不再假设在脉冲峰值。
- **统计引擎**:`tx.clock.kind: profile` 时相位轴模糊 σ = 环路未追踪的剖面功率 ⊕ 环路自噪声,
  自 1/(N·UI) 起积分;`StatResult.extras["clock_loop"]` 给出模型参数;漂移速率接近 `kp`
  时发 `slew-limited` 警告。`kind: white` 路径与指纹逐位不变。
- `ClockProfile.untracked_sigma_s / rms_rate_ui_per_s / scaled_to_rms / save`;
  `analysis/cdr_tracking.py`(时域恢复时钟 − 发送时钟的实测残余)。
- `examples/31_pll_clock_profile.py`:PAM4 112 GBd,三个 200 fs 时钟 × `kp_shift` 扫描。
- GUI Jitter 面板:剖面 L(f) 叠 `20 log|1−H|`,模型 σ 与实测残余并列。
- `tx.clock.file` 变为 `opt_enum`(内置剖面下拉),桌面 GUI 与 Android 表单同时支持;
  Android `FormContractTest` 多一条"能选中剖面并通过校验"。
- `tests/test_clock_profile_cdr.py`(20 项:模型 vs 内核实测、双引擎 2×、同 RMS 白噪 vs 1/f²、
  滑移警告、JTOL、ADC/MM、GUI)。

## [未发布] — PLL 时钟相噪剖面(阶段 1:TX 有色抖动)

### 新增
- **`ClockConfig`**(`tx.clock`):`kind: white | profile`。`profile` 读一份时钟相噪剖面文件
  (`docs/clock_profile.md` 定义;pll_simulator 导出),在 1/UI 采样率上合成有色逐沿时间偏移,
  杂散逐条叠加;三项白噪抖动仍可叠加其上。`rj_ui`/`sj_ui`/`sj_freq`/`dcd_ui` 从 `TxConfig`
  迁入,旧 YAML 由 loader 自动迁移。
- `data/clock_profiles/`:pll_simulator 七个 JSSC 基准 PLL 的剖面(ex22 导出)。
- `src/halo_serdes/vendor/pllsim/`:`synth_from_psd`、`integrate_pn` 的逐字节 vendor 副本;
  `tools/vendor_check.py` + CI `vendor-drift` job。
- `tests/test_clock_profile.py`(40 项闭式验收)。

### 修复
- `apply_overrides` 按 dataclass 分组一次替换;此前联合校验的字段按表单顺序逐键应用会被拒绝。

### 不变
- `kind="white"`(默认)下所有引擎输出逐位不变(469 个数值指纹)。

## [未发布] — 界面进入 CI

### 新增
- **`UiRenderTest`**:`createAndroidComposeRule<MainActivity>` 起真 Activity、
  跑真 Python,五个测试覆盖开屏预设 / 内置信道芯片 / 时域三档 / BER+浴盆+眼图 /
  Sweeps 列表。图表用像素断言(数颜色种类),**不做 golden image**。
- 截图经 `TestStorage` 交给 AGP,作为 `ui-screenshots` artifact 上传。
- `run_instrumented.sh` 把计数与失败摘要写进 `ci-summary.txt`,workflow 最后一步
  再打印一次 —— 让"谁来读、怎么读"决定证据放在哪。

### 修复
- **`assertDrew` 自己是瞎的**:`Color.value.toInt()` 对任何 sRGB 颜色都返回同一个数
  (ARGB 在高 32 位),于是任何图像都报"1 种颜色"。改用 `toArgb()`。
- **截图从来不是"没写出来"**:`connectedAndroidTest` 跑完卸载两个 APK,文件随 app 一起消失。
- **证据类产物不再当闸门**:截图上传失败一次让 32 个测试全过的运行变红。

### 已知
- **`assertDrew` 只能判断"画了东西",不能判断"画对了"。** 截图是给人看的旁证,不是 oracle。

---

## [未发布] — Android M7–M9：时域长跑、Touchstone 导入、通用扫描页

### 新增
- **M7 时域引擎**：三档质量（2 万 / 10 万 / 50 万符号）、前台服务、可取消。
  `run/TimeRunController.kt`（单例，因为 run 必须活过界面）+ `TimeRunService.kt`。
  前台服务类型选 `specialUse` 而非 `dataSync` —— 没有任何东西在同步，而 `dataSync`
  的每日运行时预算是给网络传输的。`START_NOT_STICKY`：run 是本进程的 Python 线程，
  进程没了活也没了，被重启的 service 只会播报一个不存在的任务。
- **门面新增 `result`**：`poll` 只给 handle，此前无法读出跑完的时域结果。
  它把 `n_errors` / `n_checked` 摆在 BER 旁边并给 `ber_is_upper_bound`；
  也同时报 `n_symbols` 与 `n_requested`（引擎丢热身与尾部，"快速档"实测约 1.4 万）。
- **M8 Touchstone 导入**：SAF 选文件 → 拷进私有目录 → `import_touchstone` →
  确认卡片 → 写回配置。装了 scikit-rf，`data/channels/*.s4p` 也一并打包，
  九个预设现在都能跑。资产解压改为按包 `lastUpdateTime` 打戳。
- **M9 扫描页**：两个标签页共用一个 ViewModel；扫描页里**没有任何一个 study 的
  名字** —— 列表、标题、说明、面板规格全部来自 `schema`（即 `studies.STUDY_LABELS`
  与 `STUDY_PLOTS`）。`LogLineChart` 变成 `MultiLineChart` 的单序列简写。

### 修复
- **skrf 的 `Network.interpolate` 依赖别人先 import 过 `scipy.interpolate`。**
  它只做了 `import scipy` 就去用 `scipy.interpolate.interp1d`。现代 SciPy 惰性加载
  子包把这件事盖住了，**而手机上装的 SciPy 1.8.1 不会** —— 设备上这条路能不能跑通
  取决于 import 顺序。`touchstone.py` 现在显式 import；测试断言的是
  `sys.modules` 里有它，因此与 SciPy 版本无关。由 `wheel-versions` job 抓到。
- **`org.json.optString` 把显式 null 变成字符串 `"null"`。** 取消后的 job 因此拿到
  一个叫 `"null"` 的 handle，每条 `field: None` 的错误都会去标红一个叫 `"null"`
  的输入框。统一走 `stringOrNull`。由仪器化测试抓到 —— 而我最初的断言里是同一个 bug。
- **`fec` 的 `concat` 在声明为对数的轴上有 9 个精确零**：`_clamp_log_axes` 钳到
  1e-300（其余 study 本来就是这个值）。

### 已知
- 进度条是不确定态：接收机内核是一次调用，跑完整个符号循环；要从里面报进度就得
  切块并跨接缝传 CDR/DFE 状态，而铁律 #3/#4 建立在那段代码上。
- 取消只在阶段边界生效，界面照实说明内核不能中途打断。
- **界面观感仍无自动化覆盖**：CI 不启动 Activity。

---

## [未发布] — Android M6：浴盆曲线与统计眼

### 新增
- `ui/charts/Charts.kt`：Compose Canvas 原生绘图 —— 对数十倍频程线图 + 密度图。
  不引入图表库（要画的形状只有两种，通用库换来版本匹配风险和用不到的功能）。
- 眼图按需拉取（缩减后仍约 4k 个数），位图按 run handle 缓存。

### 修复
- **`series(stat_eye)` 声明的色阶范围与数据不符**：`zmin=-12/zmax=0` 两端都不真
  —— 实测最大约 −2.7、约 1/3 的格子在 −18 钳位底。改为返回实际范围 + 显式 `floor`；
  floor 格子渲染成背景色，因为它们表示"没解出概率"而非"概率很小"。

### 已知
- 设备验证：16 项仪器化测试全过（ChartData 2 / FormContract 5 / LinkFacade 4 / PythonStack 5）。
- **图表的实际观感仍无自动化覆盖**：CI 不启动 Activity，曲线和热图画出来什么样，
  只能装 APK 看。

---

## [未发布] — Android M5：SECTIONS 自动生成参数表单

### 新增
- `ui/FormModel.kt` / `ui/FormFields.kt`：12 个分组、78 个字段全部由
  `config_bridge.SECTIONS` 渲染，与桌面 Dash 同源。给配置层加字段，手机上自动出现。
- 去抖校验（300 ms）→ 字段级错误标红、分组标题显示非法字段数（折叠不会藏住 Run 变灰的原因）。
- `FormContractTest`（设备）+ `test_bool_fields_must_not_be_sent_as_text`（宿主）。

### 已知
- **bool 必须以 JSON 布尔过界**：`coerce_in` 对数值 kind 接受字符串，但 `bool("false")`
  是 `True`，开关当文本发会静默取反。已由上述两个测试钉住。
- 设备验证：14 项仪器化测试全过（FormContract 5 / LinkFacade 4 / PythonStack 5）。
- **界面观感无自动化覆盖**：CI 只跑仪器化测试，不启动 Activity —— 布局、折叠动画、
  深色模式下的错误标记，都要装 APK 自己看。

---

## [未发布] — Android M4：Compose 界面接上共用计算核

### 新增
- `android/app/src/main/java/.../ui/`：Compose 界面（预设下拉 → 派生量 → 包络告警 →
  跑统计引擎 → 读 BER），以及 `api/HaloApi.kt` —— 信封只拆一次，界面永远不碰
  `ok` / `error.message` / `PyException`；所有入口 `suspend` 且强制切到单线程解释器
  dispatcher（一个解释器一个 GIL）。
- `derive` 新增 `channel: {ok, message}`：手机上不带 `.s4p`，touchstone 预设跑不了，
  必须在按 Run **之前**说清楚。`valid` 保持 `true` —— 配置没问题，是数据不在。
- `android/tools/run_instrumented.sh`：跑仪器化测试，并在日志里报出到底跑了几个；
  零测试按失败处理。

### 修复
- **`load_preset` 之外的第二处静默降级**：界面会摆出跑不了的预设，且要按了 Run 才知道。
  现在显示说明卡片并禁用 Run，启动时选第一个能跑的预设。

---

## [未发布] — Android（Chaquopy）M0 可行性验证

### 新增
- `src/halo_serdes_app/`：表现层无关的应用层（`config_bridge` / `studies` / `runner`
  从 `halo_serdes_gui` 迁出，原路径留 re-export shim），加上 `api.py` —— 进出都是 JSON
  字符串的门面，供任何非 Python UI 调用。
- `android/`：M0 去风险工程（Chaquopy + 一个 Activity），以及回答 SciPy-wheel 问题的
  `.github/workflows/android.yml`。golden 值由**同一 commit 在 CI 宿主上生成**，
  设备重算后比对（`rtol=1e-9`），避免写死常量随引擎演进而腐化。
- `tests/test_import_hygiene.py`：屏蔽 skrf/PyYAML/matplotlib/numba/galois/Dash 后，
  手机的计算路径仍须跑通。

### 修复
- **Android 上 `configs/` 不在 APK 内**：它们在仓库根、不在 Python 源码树里，
  Chaquopy 的 `srcDirs` 带不上，于是设备上只剩合成的 "Library defaults" 预设 ——
  而它默认 touchstone 且无文件，最终以 `channel.file is unset` 的样子从引擎里炸出来。
  改为经 Android assets 打包、首启解压、用 `HALO_SERDES_DATA_DIR` 交接。
- **`load_preset()` 找不到预设时静默回退到 `LinkConfig()`**：正是它把上面那个打包问题
  伪装成了配置问题。改为抛 `KeyError`/`FileNotFoundError`，并在消息里报出搜索过的目录。

### 已知
- Chaquopy 解析到 numpy 1.26.2 + **scipy 1.8.1**，而 `pyproject.toml` 声明 `scipy>=1.11`。
  新增 `wheel-versions` job 在宿主上降级到这两个版本跑一遍手机的计算路径。**已验证无碍**：
  BER 与现代版本差 1 ULP，COM 与 post-FEC 逐位相同；pip 报的不兼容只在元数据层面。
- M0 判定**通过**：三个 job 全绿，模拟器 5/5（含 `rtol=1e-9` golden 比对）；
  真机 aarch64 独立复现 `golden MATCH`（BER 1.3201e-06 / COM 3.35 dB / 164 ms）。
  仍未证明：**16 KB page 机型**（只能说该测试设备可以，其页大小无从判断）。

---

## [未发布] — 文档与审计

### 新增
- `docs/USAGE.md`：按**任务**组织的使用指南（配置、三个引擎的取舍、读结果、换信道、
  参数扫描、串扰、COM、抖动/JTOL、MLSD/预编码、FEC、IBIS-AMI、定点/RTL、性能、FAQ）。
  每段代码都对真实 API 执行验证过。
- `CLAUDE.md`：架构不变量、易踩的坑、代码与文档约定、已知限制。
- `tests/test_docs_fresh.py`：文档防腐 —— 相对链接可解析、README 引用的示例数/标签页数
  与代码一致、USAGE 覆盖主要入口。
- `tests/test_config_validation.py`、`tests/test_lti_chunked.py`、
  `tests/test_examples_api.py`：补齐三处零覆盖/无守卫的区域。

### 修复
- **`icn_rms` 结果依赖侵略者列表顺序**：把 `aggressors[0].swing` 套用到了整个 RSS，
  混合 swing 的 bank 下 `[a,b]` 与 `[b,a]` 相差 2×。改为逐 lane 各带自己的 swing。
- **配置层零校验**：`osr=-4` 曾被接受并产生**负的 dt**（静默错误物理），
  `modulation="pam5"` 等拼写错误只在引擎深处炸出难懂的 traceback。
  各配置类补 `__post_init__` 校验，每条错误都点名字段。
- GUI 预设 "PAM4 112G ADC (TI mismatch)" 实跑 112 GBd = 224 Gb/s，与全工程
  "224G 指数据率" 的约定差 2×，改名为 "PAM4 224G ADC (TI mismatch)"。
- README 停留在 "当前状态（Phase 0 完成）"，且测试数/示例数/能力列表全面过期，重写。
- Nuitka onefile 构建失败：`{VERSION}` 占位符需要 `--product-version`。

### 变更
- `engine/lti.py` 的 overlap-save 分块路径此前**零覆盖**（专为百万符号长跑而存在，
  而测试从不跑那么长）。已验证数值正确（相对误差 ~1e-16）并用小 chunk 强制走该分支钉住。
- 性能数字更正为本机实测：10⁶ 符号 @106.25 GBd，OSR16 **8.1 s** / OSR32 **11.6 s**，
  峰值内存约 0.9 GB。

---

## MLSD 与 1+D 预编码接入引擎

此前两者只是独立内核，引擎没有开关，统计引擎因此系统性低估带 MLSD 的架构。

- `rx.mlsd` 开关（`none`/`sliding`/`viterbi` + `memory`/`seq_len`/`margin`）接入**两条
  RX 路径**：在 FFE/DFE 之后按残余光标重新判决；`extras` 回报 `ser_slicer`（原始判决器
  基线）与 `mlsd_resid`，增益可直接量。
- `precode` 链路开关：Tx 侧 1/(1+D) mod-N 预编码、Rx 侧解码，时域/静态引擎端到端一致，
  BER 按用户符号计分（孤立错误精确翻倍，1+D 的已知代价）。
- 统计引擎按**匹配滤波器界**折算 MLSD 增益：进入网格的后光标从 ISI 中移除并转为
  等效噪声下降 —— 计划中「增益查表」的闭式版本。
- 示例 30；GUI 新增 MLSD 配置区与预编码开关。

实测：欠均衡链路上 1979 → 1439 个错误（1.38×），低噪声下达 2.17×；两引擎在 <2× 内一致。

---

## 第二梯队

### IBIS-AMI 真执行
- `io/ami_c/halo_fir_ami.c`：一个**真实的、符合 spec 的 IBIS-AMI 模型**（UI 间隔 FFE），
  实现 `AMI_Init` / `AMI_GetWave` / `AMI_Close` 三个 C 入口，附 `.ami` 参数文件。
- `AmiCModel`：用 ctypes 按**真实 IBIS-AMI C ABI** 加载并执行编译出的共享库，
  不依赖 pyibisami 或厂商二进制。`build_reference_ami()` 用系统 C 编译器现场编译。
- 与原生 FIR 参考实现逐位一致（Init 精确、GetWave 机器精度），经引擎两条流验证。示例 29。

### 忠实 IEEE 802.3 COM
- `analysis/com.py`：Clause 93A/178A 方法 —— CTLE/DFE 网格按 FOM 优化均衡器、DFE 抽头
  由光标经 `b_max` 上界导出、**A_ni 从卷积后的干扰+噪声 PDF** 在目标 DER 处读取
  （而非高斯 RSS）。`Com93a` 经既有 `ComAdapter` 接缝接入。示例 26。

### 多 lane 串扰 + MLSD 解析增益
- `aggressor_bank()`：N 个 FEXT + M 个 NEXT 独立 lane（逐 lane 耦合抖动与独立数据种子）；
  `icn_rms()`：行为级 MDFEXT/MDNEXT 功率和（~√N），同一 bank 直接喂 COM 引擎作 σ_XT。
- `post_detect()`：把 MLSD 内核接到真实链路的判决器输入上；
  `mlse_min_distance_sq()` / `mlse_gain_over_dfe_db()`：误差事件最小距离给出对理想 DFE 的
  渐近编码增益闭式解（1+D → 3.01 dB、EPR4 → 6.02 dB，匹配滤波器界）。示例 27、28。

### ADC 架构的数字域眼图
- Eyes 标签页对 ADC 架构改为三栏：闭合的模拟 ADC 输入眼、**重建的 post-FFE 眼**
  （收敛后的波特率抽头上采样 ⊗ 模拟波形）、slicer 采样云（DFE 是逐符号非线性反馈，
  没有连续波形）。重建逻辑抽到 `analysis/reconstruct.py`，与示例 16 共用。

---

## 第一梯队

- **CI**：Linux 测试（numba / 纯 Python 双路径矩阵）+ ruff lint + RTL lockstep 三个 job。
- **JTOL**：`analysis/jtol.py` 二分搜索每个频率下可容忍的 SJ 幅度；低于 CDR 环带宽平坦、
  高于则 ~20 dB/dec 滚降。GUI 标签页 + 示例 25。
- **黄金模型 → RTL 闭环**：`rtl/` 下 FFE+DFE+slicer 的**独立 SystemVerilog 实现**，
  经 Icarus Verilog 与 Python 黄金模型**逐位比对**；`dims.svh` 由黄金模型生成，
  保证 RTL 参数与 Python 单源。

---

## GUI 与桌面打包

- **G0–G4**：16 标签页 Plotly Dash 工作台（单次运行、眼图、双引擎、信道、CTLE、抖动、
  自适应、CDR、ADC、背channel、扫描/reach、FEC、串扰、AMI/COM、定点、JTOL），
  由 schema 自动生成配置表单；`docs/GUI.md` + 图文导览 `docs/GUI_tour.md`。
- **Windows 桌面版**：no-JIT + pywebview 原生窗口，PyInstaller 与 Nuitka
  （standalone 与 onefile）三种构建 + CI 冒烟验证。
- 应用图标（exe/任务栏/浏览器标签/侧栏 logo）。
- 修复：exe 内预设为空、Touchstone 找不到、窗口内图形无限拉长。

---

## 三项增量

- **抖动分解接入管线**：`stage_jitter_budget` / `total_jitter` + 引擎 `collect_jitter`
  逐级预算（Tx / 信道 / CTLE 后）。示例 22。
- **IBIS-AMI 接缝 + 行为级 COM**：`AmiModel` 双流接口、`NativeFirAmi` 参考模型、
  `ComAdapter` 接缝。示例 23。
- **时域 FEXT/NEXT 串扰**：`XtalkAggressor` 同一对象驱动时域与统计两个引擎；
  `synthetic_aggressor` + `import_xtalk`（多端口 Touchstone 提取）。示例 24。

---

## Phase 0–6：框架主体

| 阶段 | 内容 | 关键验证 |
|---|---|---|
| **0** | 包骨架、配置层（YAML → frozen dataclass）、`core`（Waveform/PRBS）、信道层（Touchstone 1/2/4/8/12 端口、混模转换、保守外推、广义端接、解析 RLGC） | 冲激响应对照参考库黄金数据；端接公式独立 Γ 交叉验证 |
| **1** | 最小静态链路：Tx FIR、CTLE、ZF/MMSE FFE、理想 DFE、蒙特卡洛 BER | AWGN 下 BER 对照闭式解；零噪声时 MMSE 退化为 ZF |
| **2** | 时域引擎 + mixed-signal 架构：自适应 DFE、bang-bang CDR、RJ/SJ/DCD 抖动注入、分阶段启动状态机 | ±ppm 频偏 → 相位斜坡解析对照；numba 与纯 Python 逐点一致 |
| **3** | StatEye 统计引擎：PDF 卷积外推至 1e-15、浴盆曲线、统计眼 | 与蒙特卡洛交叉校验 **1.03×**（要求 <2×） |
| **4** | ADC-based 架构：时间交织 ADC（offset/gain/skew 失配、ENOB）、数字 FFE/DFE、Mueller-Müller CDR | 量化 SNR = 6.02N+1.76 dB；失配归零退化为单 ADC；10⁶ 符号性能达标 |
| **5** | MLSD（Viterbi + sliding-detector）、RS-FEC（KP4/KR4）+ 级联内码、三层抖动分解、双架构对比 | 1+D 信道 MLSE 增益解析对照；FEC t=15/7 纠错边界 |
| **6** | 定点双模式（int64 + 移位定标，RTL 语义）、`dump_vectors` 黄金向量出口 | 定点 vs 独立整数参考 bit-true；字长 → ∞ 收敛到浮点 |

期间的架构探索成果（产品级 mixed-signal 三层包络、深 LR 的杠杆分解等）见
`docs/SUMMARY.md`。

---

## 起点

- `docs/serdes_opensource_repos_analysis.md`：对 serdespy、PyBERT、Stanford DragonPHY2
  三个开源项目的深度调研 —— 本框架的设计蓝本。三方能力对比见 `docs/COMPARISON.md`。
