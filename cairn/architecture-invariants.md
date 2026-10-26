---
type: project_topic
status: active
summary: "Halo_Serdes 的六条架构不变量与代码/文档约定 —— 破坏其中任何一条都会让某类结论失去可信度"
tags: [architecture, invariants, conventions, serdes]
contains: [architecture-invariant, code-convention, doc-convention, validation-tradeoff]
created: "2026-08-18"
updated: "2026-10-13"
related: [engineering-pitfalls.md, knowledge-inventory.md]
authoring_mode: ai_generated
---
# 架构不变量与约定

## 当前结论

### 1. 配置是唯一真相源

一切由 frozen dataclass `LinkConfig` 决定:引擎、GUI 表单、COM、RTL 参数导出全部读它,
**没有全局状态**。新增参数的完整动作是三步:加字段 + 在 `__post_init__` 里校验 +
在 `config_bridge.SECTIONS` 里加一行(GUI 表单会自动生成)。
纯函数的记忆化不算全局状态:键含全部输入、命中与重算逐位相同(`tx/driver.py` 的 `lru_cache`;2026-10-26 起 `_pattern_phase_factor` 按输入摘要缓存最近 8 个结果)。

**校验的取舍:只拦真正的非法值。** 已知两处**故意不拦**:

- `kind="touchstone"` 而 `file=None` —— `LinkConfig()` 默认就是它,若在此拦截会让
  默认构造函数失效(GUI 的 "Library defaults" 预设就是它);加载器 `ChannelModel.from_config`
  自会精确报错。
- 单抽头 FIR 的 `fir_n_pre` —— 引擎在 `len(fir_taps) > 1` 时才应用 FIR,单抽头整个跳过,
  此时 `n_pre` 无意义。

这两处都是**被测试打回来过的**:第一版校验规则比引擎本身更严格,误伤了 `LinkConfig()`
与 `test_ami.py`。规则应当匹配引擎的实际语义,而不是理想语义。

### 2. 双 RX 架构必须公平可比

mixed-signal 与 ADC-DSP **共享** Tx、信道、分析层、统计引擎骨架;差异只允许存在于
`engine/timedomain.py` 的两个组装函数与各自专属模块内。

这是整个框架能回答"两种架构谁更适合这条信道"的前提 —— 一旦某个改动只惠及一侧,
所有架构对比结论都失去意义。新增能力时要先想清楚它属于共享层还是专属层。

2026-10-05 补(COM):`analysis/com.py` 的参考接收机按架构分 —— mixed-signal 93A(CTLE + DFE)、ADC 178A 式
(CTLE + MMSE Rx FFE + DFE,σ_N 含量化噪声)。这不破坏本条:每侧用自己真实接收机的结构,两侧共用同一套网格、
FOM 与 PDF 读法;此前 ADC 也按 93A 算,是让 ADC 一侧吃亏的不公平(各 ADC 预设读 −6…−9 dB)。

2026-10-13 补(P1 #21):**起点也是接收机的一部分**。混合信号的 DFE 从信道游标起步、一直在用;ADC 收端的 FFE 从不计 DFE 的 MMSE
起步,联合 LMS 的代价面沿"FFE 留 h1、DFE 消 h1"是平的,DFE 就一直 ≈ 0 —— ADC 预设的头条 BER 因此悲观一到两个数量级(224G
106 GBd 2.27e-4 vs 修后 4.0e-6),深 LR 的"DSP 深度"杠杆读成 +1.8 dB(修后 +4.7)。改为 DFE 感知的起点后两边的 DFE 都在用。
**规则:比较两种架构前,先看两边的每个自适应环路都真的走到了它该去的地方**(`SimResult.dfe_taps`)。`cairn/DSP发端与PR.md` §22。

### 3. 双引擎交叉校验是核心纪律

纯 LTI + AWGN 下,统计引擎与时域 MC 必须在 **2× 内**吻合(实测基线 0.92×,示例 04;2026-10-13 静态引擎的 FFE 改为 DFE 感知前 1.03×)。

加了非 LTI 环节(TI-ADC 失配、MLSD、CDR 残余抖动)后偏差会变大 —— 这些在统计引擎里
是**近似**,`engine/statistical.py` 顶部逐条列了假设清单。**新增近似必须往那儿补一条**,
否则下一个人无法判断偏差是 bug 还是已知近似。

剖面时钟(`tx.clock.kind: profile`)的交叉校验口径(2026-10-01 阶段 2):统计引擎用
`cdr/linear.py` 的线性化环路算采样时刻 σ,**时域引擎是裁判,不为迎合统计引擎而改**;
比较的量是恢复时钟 − 发送时钟的实测残余(`analysis/cdr_tracking.py`)与模型 σ,其次才是 BER。
实测基线:BB 环路锁定时 σ 比 0.95–1.08,BER 比 1.3–1.5×;环路进入滑移区(剖面漂移速率
≥ ~0.4 kp/update,或 BB 鉴相器输入抖动 ≥ ~0.1 UI)后线性模型失效,统计引擎必须警告而不是给数。
接收端时钟(阶段 3)进内核的方式是**逐符号采样偏移数组**,环路本身不变、全零逐位回归;
两只时钟的残余只在线性(MM)环路上功率相加,BB 上超加 —— 这是鉴相器的性质,不是误差。
2026-10-14 补(P2 #24):剖面时钟下比 MM 环路,两侧还得是**同一个码型** —— 检测器的数据噪声在短 PRBS 上是环路外的谱线,
PRBS13Q 与随机符号的自噪声差一个量级。统计引擎从 `sim.pattern` 取周期(配置是唯一真相源),模型读 DFE 之后的切片器输入;
示例 31 实测 / 模型 0.93–1.23,112 GBd 测试 0.96–1.23。线性模型不报 MM 定时函数见顶处的失锁(P2 #26)。
2026-10-14 补(P2 #25):发端的白 RJ 不进相位轴模糊 —— 时域里每个边沿各自移动,统计引擎按逐沿噪声建(`tx_edge_jitter`,分箱 + 被采符号
两个边沿精确);只有接收端时钟的 RJ 抹相位轴。0.03–0.09 UI 下 ADC 0.69–1.39×、混合信号 0.38–0.67×(白噪钟不建 BB 环路自身游走,P2 #27)。
2026-10-14 补(P2 #26):上面"线性模型不报 MM 定时函数见顶处的失锁"已补警告 —— 统计引擎量定时函数的拐点离锁定点多远(`hold_range_ui`),
与环路自己的游走(`sigma_wander_ui`,自噪声 + 带宽内跟晚的时钟,**不是**总 σ)比,< 8× 警告;阈值按时域失锁标定(2.8–6.9× 失锁、
8.6× 起没有),白噪钟不建环路也就不查。只加警告与两个字段,数不动(指纹逐位不变)。
2026-10-15 补(P2 #27):BB 环路在任何时钟下都猎 —— 白噪钟的混合信号也跑 `bb_loop_fixed_point`(发端 RJ 进边沿噪声,PAM4 按跳变分类),
平滑时 DFE 停在读点的抽头(浴盆仍是每相位重新适应的;剖面时钟例外,保持抽头读高 6.6–15×、原因未定,P2 #28)。≥ 20 个错的陡浴盆:NRZ 16G 0.11–0.54× → 0.50–0.89×、PAM4 32G 0.48–0.71× →
0.99–1.03×、NRZ 28G 0.53–0.98× → 0.75–1.12×。混合信号带 FFE(替静态引擎)与 ADC 白噪钟不加游走 —— 两种接收机的差别留在各自的环路模型里。
2026-10-15 再补(P2 #28):上面"剖面时钟例外"已取消。剖面时钟的 σ 改按**一次运行**算(合成器的谱线 k/(N UI)、环路从静止起步、同一窗口),
因为欠阻尼环路(示例 31 kp_shift 7–9,|E| 峰 4–16×)在 20 万符号里振铃不起来,稳态读 16 种子系综 1.14–1.70×;稳态另报、超运行 1.25× 警告。
平滑小于两个样本时保持抽头的曲线在格点之间真算(线性插值读高 1.6–3.6×)。示例 31 链路 kp 4–9、两种剖面,对 12 种子 0.66–1.40×(此前 0.24–1.42×)。
比剖面时钟的模型与时域时用多种子系综:慢游走下单个种子的 σ 差 3×,"实测 / 模型 0.93–1.23"(上文 P2 #24 补)是 seed 3 的偶合。
细网格也落到白时钟:≥ 20 个错时 NRZ 16G 0.45–0.89×、PAM4 32G 0.99–1.01×、NRZ 28G 0.67–1.08×,交叉校验 1.00–1.07×。
2026-10-15 三补(P2 #29):平滑时 PAM4 判决门限也停在读点相位的主光标(时域电平定下后不动;每相位重放在冻结对照里读 0.42–0.79×,
保持 0.95–1.19×)。ADC 的 LMS FFE 进环路模型,作为沿定时导数的跟踪路径(`LoopParams.ffe_track`,γ = n_lanes·mu·λ',检测器读 FFE 输出时
并联、读原始样本时高通):示例 31 kp 4–9、两种剖面、mu 0–1e-2 对 12 种子 0.68–1.61×(mu 1e-2 kp9 此前 ~12×),模型 σ / 实测 0.93–1.01。
白时钟 PAM4 32G ≥ 100 个错 1.03–1.13×(此前 0.99–1.01×),更深处 rj 0.01 读高 1.31–1.40× —— 登记 P2 #30;NRZ 不变。
2026-10-15 四补(P2 #30):上一条的"深处读高"拆开是两件事 —— PAM4 猎振 σ 读高 1.11–1.23×(偏离锁定点跳变的检测器输出由数据定,
按低频密度 / 码型谱线进环路后 0.97–1.01),以及门限位置:统计引擎跟着时域接收机把混合信号的门限放在它设电平的脉冲峰值(锁定点前 2 个样本;
ADC 与 `phase_ui` 读数放在读点)。PAM4 32G 深 BER 0.94–1.15×、≥ 100 个错 1.04–1.12×、交叉校验 1.01–1.04×;NRZ 逐位不变。
时域电平不自适应本身登记 P2 #31(2026-10-21 已做:内核加 dLev,统计门限放回锁定点,PAM4 32G 两引擎同降、统计 / 时域 0.79–1.13×);
NRZ 16G 自由运行时相位与码型相关、读低 0.5–0.7× 登记 P2 #32(2026-10-21 已做:第一项是 bang-bang 相位分布的指数尾,统计引擎改用鉴相器曲线的
平稳分布平滑,0.25–0.84× → 0.42–0.91×、PAM4 32G 1.04–1.06×;深 BER 预设随之上移 30–76×;数据相关的第二项登记 P3 #13。2026-10-24 已做:推晚环路的是短串之前的长反向串,它 DFE 抽头外的尾巴同时把短串的数据样本拉向反向电平,
统计引擎按 12 符号历史类条件化相位乘因子 `_pattern_phase_factor`,NRZ 16G 12 / 15 / 18 mV 0.44 / 0.70 / 0.97× → 0.95 / 1.04 / 1.14×,同日晚修正边沿抖动表的分箱顺序后 0.94 / 1.08 / 1.20×;PAM4 / ADC 不变。2026-10-25 往预设点补点:10 / 9 / 8 / 6 / 3 mV 统计 / 时域 0.53 / 0.53 / 0.50 / 0.40 / 0.51×,
6 mV 出 2×,登记 P3 #14 —— 长反向串类的条件相位尾比平移的分布重;2026-10-26 已做:类的相位分布取投票链的不动点,10 → 3 mV 0.62 / 0.71 / 0.79 / 0.86 / 1.23×。
同日查出这些时域对照都在 PRBS31 上:独立随机码元下内核在 15 / 12 / 8 mV 少错 1.16 / 1.54 / 4.1×,统计读它 1.09 / 1.38 / 3.2×,8 mV 出 2×,登记 P3 #15;同日 P3 #15 已做:统计引擎定为随机数据的模型,链带上积分路径、用自己的边际,对独立码元 18 → 6 mV 1.02–1.95×,
深处比对时域喂独立码元(PRBS31 在同一范围多错 1.03–10×,读它 0.17–1.05×);6 mV 起偏高、3 mV 未核,登记 P3 #16;同日 P3 #16 已做:积分器推动改二阶通量、去掉下限 1,对独立码元 18 → 6 mV 0.88–1.09×;预设点的 BER 只核到相位分布,登记 P3 #17)。

**两条前提(2026-10-03 补)**:(1) 比的是同一个采样点 —— 统计引擎在 mixed-signal 上按 bang-bang 锁定点读数
(静态引擎在 FFE 前脉冲峰值),ADC 上取浴盆最低点;读最低点曾让 MS PAM4 只有 0.19–0.70。(2) 噪声在两个引擎里是
同一个量 —— 时域引擎的接收噪声限带到 [0, baud](`engine/lti.receiver_awgn`),否则插值采样器在样本之间只放过
0.64–1.0 的方差,判决噪声随 CDR 的小数相位变。修后:MS NRZ / PAM4、ADC PAM4、tx.bw、光 ER 3–7.5 dB 都在 0.88–1.04×
(光路另加了邻符号分箱核,`cairn/光互联建模.md`)。

**第三条前提(2026-10-03,收端 PR)**:序列检测器面对的噪声若是有色的,统计引擎不能只用白噪声最小距离 —— PR 目标下 FFE 把
噪声染成强负相关,最小距离版本乐观 2.5–20 倍。PR 路径改为交替误差事件的 union bound(噪声自相关取自 FFE 抽头),
2× 判据只在 BER ≲ 5e-4 成立(union bound 在高 BER 偏松);测试工作点按此选(`tests/test_pr.py`)。
2026-10-04 再补:ADC 收端的统计引擎此前没有 ADC 自身噪声(时域在内核里量化),ENOB 5、接收噪声 0.5 mV 时读 0 而时域 1.5e-4;
现按 fullscale²/12 · 2^(−2 ENOB) 并入 FFE 输入的噪声(`adc_noise_sigma`),ENOB 5 / 6.5 / 8 在 0.91–2×。PR 测试从此不必 `enob=None`。
2026-10-04 补:相邻误差事件共享大部分噪声投影,简单求和在发端 a = 0.5 悲观到 2.9×;扣掉相邻重叠(Hunter 链式上界)后
收端 / 发端 PR 都在 1.0–1.8×(BER 7e-5…6e-3)。
2026-10-10 补:链式界只扣相邻长度,距离很平时仍多算,且同一个错误的后缀会从下一个起点再算一次 —— 手选 c < 0 的四光标目标
到 2.09×。改为整族事件(各长度、两族、所有重叠起点)取并集、每个事件按检测器实际选中的那条分摊误码后,16 个 PR 链路在
0.67–1.55×(`cairn/DSP发端与PR.md` §14)。
2026-10-10 再补:2× 的适用域不含 CDR 漂走 —— 强 PR 目标在高 SER 下逐符号判决的错误带偏 MM-CDR,时域周跳而统计引擎不建;
统计结果报 `pr_loop_load`(逐符号 SER × Σc_k²),≥ 0.08 时警告(实测失效 0.09–0.14,§15)。
2026-10-11 补:PR 目标配**短 FFE**(3–7 抽头)时统计引擎悲观 1.8–10.7×(§18 量到)。原因:联合界里每个事件只把噪声按距离缩小,
网格外的残余 ISI 仍按单个样本的大小算 —— 等于把 ISI 相对门限放大了 g 倍,短 FFE 的残余大才显形。改为残余 ISI 按每个事件投影
(d · ISI / |d|²)后短 FFE 0.77–1.24、§14 的 16 格 0.58–1.08(几何平均 0.80,剩下的近似都朝乐观走)。§19。
2026-10-12 补:时域一侧也改了 —— Viterbi 的网格原取自脉冲峰值处的模型,而环路不停在那里(1 抽头 FFE 时第一后光标符号都反了,
MLSD 比 slicer 多 7 倍误码);改为在实际采样点按线路符号拟合,与统计引擎“检测器知道真实游标”的假设对齐。重量后 16 格 0.60–1.08、
短 FFE 0.95–1.53、光链路 0.83–1.44,都在 2× 内。§21。
光链路发端 PR、噪声在调制器后 1.5–3.0× 偏悲观(原有,与投影无关)记 ROADMAP P2 #18。
2026-10-13 补:**串扰下没被覆盖**。统计引擎按每个 aggressor 自己的峰值相位取游标(所有 aggressor 同时最坏),时域是耦合冲激碰巧对齐的相位;
aggressor 整体延后 0…7/8 UI 时统计 / 时域 1.2–41×,`tests/test_crosstalk.py` 只查两边都变差。记 ROADMAP P2 #19(`cairn/多lane建模.md` §3)。
同日修:统计引擎在受扰 lane 的采样网格上读 aggressor、ADC 收端先过 FFE,`XtalkAggressor.skew_s` 两个引擎同用;各 skew 下 ADC 1.01–1.08×、
PAM4 混合信号 0.79–1.21×,测试按 skew 查 2×。
2026-10-13 再补(P3 #9b):串扰进了 `LinkConfig.crosstalk`(铁律 1 的缺口:此前只能 API 入参),两个引擎与 COM 从它建 aggressor;
准同步 lane(`ppm` ≠ 0)时域按 aggressor 自己的速率保持符号,统计引擎对相位求平均 —— 三条链路 ±100 ppm 0.72–0.86×(示例 44;ADC 的 DFE 起作用之前 0.79–0.93×,P1 #21),
(2026-10-14 更正:发端 RJ 改为逐沿噪声后 0.72–0.91×,P2 #25。)
(2026-10-15 更正:白时钟上 bang-bang 环路的游走进统计引擎、模糊下 DFE 保持后准同步 0.72–0.96×、同步 24 次 0.67–1.15×(此前 0.42–1.28×),P2 #27;
同日曲线在样本之间也算后准同步 0.72–0.95×、同步 0.44–1.15× —— 0.44 是 NRZ 28G 7/8 UI 那个浴盆太陡的点,P2 #28;
PAM4 判决门限保持后准同步 0.72–0.96×、同步 0.44–1.15×,P2 #29;PAM4 猎振 σ 改正后准同步 0.72–0.95×、同步 0.44–1.15×,P2 #30。)
(2026-10-16 更正:串扰 PDF 的卷积按网格中心对齐后准同步 0.70–0.93×、同步 0.44–1.11×,P2 #36。)
(2026-10-21 更正:bang-bang 采样相位按鉴相器曲线的平稳分布平滑后准同步 0.70–0.94×、同步 0.44–1.11×,P2 #32。)
测试查 2×(`cairn/多lane建模.md` §7)。
2026-10-13 补(P1 #21):**DFE 一旦用上,"理想 DFE"就不是近似而是错**。ADC 收端的 DFE 起作用(抽头 0.57–0.72)后,统计引擎反馈
正确判决的读数比时域乐观 1.8–2.9×(抽头 ≥ 0.69 时到 5×):一次错判留下 h1 × 电平间距,误码成对。加第一抽头的误码传播
(两状态链 s / (1 − p + s),`extras['dfe_ep_factor']`,已补进 `statistical.py` 的近似清单)后 0.81–1.25×;lane 冻结在偏移处、给定
它收敛的 FFE 0.93–1.47×。PR / MLSD 路径不加(检测器不同)。`cairn/DSP发端与PR.md` §22。
2026-10-12 补:#18 的原因是联合界的重叠权重按 `half_gap / noise_sigma` 抽样,光链路的 `noise_sigma` 只有接收端电噪声,光噪声在逐电平核里 ——
SNR 高估几十倍、并集退化成简单求和。改为按全部噪声抽样后光链路 PR 0.83–1.44×(原 0.91–2.99)。§20。

### 4. numba 是性能层,不是正确性层

热核写成纯 Python 函数,再用 `numba.njit` 包一层;`ImportError` 或 `HALO_NO_JIT=1`
时退回纯 Python。两条路径结果必须一致(CI 双路径都跑)。定点路径禁 `fastmath`。

### 5. 波形与符号是两个域,不能隐式互转

过采样连续域(`Waveform`)与波特率符号域(`SymbolStream`)之间只能经显式采样器。
隐式互转会引入 0.03 UI 量级的伪抖动而不报错。

**这条曾经只是一句话。** 2026-09 审计发现:没有采样器模块;`SymbolStream` 定义了
但**全仓库零次构造**;11 个调用点各自就地用 `[::osr]` 切片或 `np.repeat` 跨域。
**没有任何东西执行的不变量就是一条注释。**

现在的执行方式:

| | |
|---|---|
| 唯一允许跨域的地方 | `src/halo_serdes/core/sampler.py` |
| 波形 → 符号 | `sample_baud(wave, osr, phase) -> SymbolStream`(带 UI 与相位的类型化边界);`baud_samples(y, osr, phase)`(结果不是符号序列时用,如脉冲响应的 baud 间隔 cursor) |
| 符号 → 波形(零阶保持) | `hold(values, osr)` |
| 波特率滤波器系数 → 采样网格 | `upsampled_taps(taps, osr, length=None)` |
| 执行者 | `tests/test_domain_boundary.py` 扫描 `src/halo_serdes`,断言 `[...::osr]` 与 `np.repeat(..., osr)` **只出现在 sampler.py 里** |

**判据诚实声明**:那条扫描匹配的是**惯用写法**,不是所有可能的隐式转换 ——
把 `osr` 别名成另一个名字仍能绕过。它买到的是「按最自然的方式写这个 bug 会让构建变红」,
绕过必须是刻意的。测试文件里自带一条"种一个违例、要求扫描能看见"的自检,
因为**匹配不到任何东西的正则能通过任何建立在它之上的断言** —— 本项目已经出过一次
瞎断言(`assertDrew` 走 `Color.value.toInt()`)。

**这次改造不允许改变任何数值**:9 个预设 × 4 条引擎路径共 469 个标量与数组哈希,
重构前后逐位一致。

### 1b. 剖面文件是 pll_simulator 与本库之间唯一的接缝(铁律 1 的推论)

**规则**:PLL 的时钟进入链路只能以一份**时钟相噪剖面文件**(`f0_hz`、`f_hz[]`、
`l_dbc_hz[]`、`spurs[]`、`source`,格式定义在 `docs/clock_profile.md`)的形式,
由 `tx.clock.kind: profile` + `tx.clock.file` 指向。两个库在 import 层面互不依赖:
pllsim 写文件,本库读文件;本库只 vendor 了 `synth_from_psd` 与 `integrate_pn`
两个纯 numpy 函数(`src/halo_serdes/vendor/pllsim/`,`tools/vendor_check.py` 守着)。

**为什么是文件不是 import**:(1) 铁律 1 要求一切由 frozen `LinkConfig` 决定 —— 一个
PLL 对象进不了 YAML,一张表可以;(2) Android 只带 numpy + scipy,pllsim 硬依赖
matplotlib;(3) 格式由消费方定义,生产方遵守,pllsim 的导出器
(`export/clock_profile.py`)已按此实现。

**本库里只有 `tx/clock.py` 知道剖面长什么样。** 它把相位除以 2π·f0 变成秒之后,
下游 `tx/jitter.py` 与两个引擎看到的仍是原来那个逐沿偏移数组。阶段 2(统计引擎的
CDR 高通残余 σ)与阶段 3(RX 时钟)都在这条接缝之内扩展,不新开接口。

### 6. 桌面与 Android 必须同时验证

**规则**:凡是改动了共用层(`src/halo_serdes/` 或 `src/halo_serdes_app/`),
桌面与 Android **两端都跑通才算完成**。只验一端就宣布完成,是把一半的交付物
建立在没有检查过的假设上。

**为什么这条要单独立成铁律,而不是"记得多跑一次测试"**:两端跑的不是同一套东西。
共用的只有 Python 源码,**运行它的环境有五处系统性差异**,每一处都已经在本项目
真实咬过人:

| 差异 | 咬过的具体形式 |
|---|---|
| **依赖版本不同** | Chaquopy 解析到 numpy 1.26.2 + **scipy 1.8.1**,而 `pyproject.toml` 要 `scipy>=1.11`;pip 自己都报这对不兼容 |
| **子模块加载语义不同** | skrf 只 `import scipy` 就用 `scipy.interpolate` —— 现代 SciPy 惰性加载把它盖住,**手机上的 1.8.1 不会**,Touchstone 这条路能不能跑取决于 import 顺序 |
| **依赖是否存在不同** | 手机上没有 matplotlib / numba / galois;M0–M7 连 scikit-rf 都没有 |
| **文件系统语义不同** | Chaquopy 的 importer 不是文件系统,`Path(__file__).with_name(...)` 在设备上不成立;`configs/` 与 `data/channels/` 要走 assets + 解压 |
| **生命周期不同** | `connectedAndroidTest` 跑完会**卸载两个 APK**,测试写出的文件随之消失 |

前三条意味着:**桌面 `pytest` 全绿,不构成手机能跑的证据**。第四、五条意味着:
**手机上跑通,也不构成桌面打包正确的证据**(桌面走 PyInstaller/Nuitka,另一套打包)。
两端谁也替代不了谁。

**"完整验证"的具体含义**:

- **桌面**:`pytest -q` 全绿 + `ruff check src tests` 干净。动了 `halo_serdes_gui/`
  或它 re-export 的东西,还要确认 GUI 能起来。
- **Android**:CI 的 job 全绿 —— `assemble`(能不能打包)、`wheel-versions`
  (**在手机的 wheel 版本上跑一遍手机的计算路径**)、`emulator`(仪器化套件,API 34 / 35,
  脚本在零测试或任一失败时退出非零);Cython 编译版另有 `compiled-apk` 与 `compiled-emulator`
  (2026-10-08 更正:原写"三个 job",编译版两个 job 是后加的)。
- **判据不是"我跑过了",是"日志里那行数字"**:`instrumented totals: N tests, 0 failures`。
  绿勾本身不够 —— `connectedDebugAndroidTest` 跑零个测试也算成功。

**`wheel-versions` 这个 job 就是这条铁律的执行者。** 它在宿主上降到手机的
numpy/scipy 版本跑同一条计算路径,把"版本"这一维从"平台差异"里摘出来。
它已经兑现过一次:`scipy.interpolate` 那个 bug 是它抓到的,而桌面套件全绿。

## 实践指南

### 代码与文档约定

- **代码注释、图表文字、docstring 一律英文**;`docs/*.md`、`README.md`、`CHANGELOG.md`、
  `ROADMAP.md`、`cairn/` 是中文。**这是有意为之,不要"统一"。**
- **注释解释「为什么」,不解释「是什么」** —— 尤其是物理/数值上的取舍与已知近似。
- 新算法模块配闭式解单测(AWGN 解析 BER、退化情形、恒等式),不要只测 smoke。
- 新增示例脚本按编号排(`examples/NN_name.py`),它同时是文档;
  `tests/test_examples_api.py` 检查它能编译且 import 的符号都存在。
- README 里的示例数、标签页数由 `tests/test_docs_fresh.py` 守着;测试数没有自动检查,要手动同步(2026-10-08 更正:原写测试数也守着)。
- 文档引用的示例数字:每个示例的全尺寸输出存在 `examples/expected/`,改数的 PR 同时刷新它和文档;`tools/example_drift.py` 从"哪行输出变了"反查仍引用旧值的文档行,CI `examples-full` 跑同样的比对(2026-10-09 起)。文档里由输出推导的数(差、和、范围)登记在 `examples/derived.yaml`,测试按 `examples/expected/` 逐处检查(2026-10-09 起)。
- **调制格式的字母表只在 `core/mapping.py` 一处**(2026-10-15 起 `modulation ∈ {nrz, pam4, pam6, pam8}`):电平数用 `cfg.n_levels`、
  每符号比特用 `cfg.bits_per_symbol`(PAM6 是 2.5,不要再写 `2 ** bits_per_symbol`),码型比特数走 `bits_needed()`,
  比特 ↔ 符号走 `bits_to_symbols` / `symbols_to_bits` / `count_bit_errors`;`prbsNq` / `prqs10` 是 PAM4 的符号码型。
  铁律 5 不变:符号仍是电平索引,比特映射是显式函数,不藏在采样器里。
- **reach 一律用 `halo_serdes.fec` 的 `reach` / `refine` 读**:在 pre-FEC BER 越过 `fec_threshold()` 处插值,扫描在穿越点附近二分加密(示例 0.4 dB / 10 m),零误码点不插值。时域 MLSD 的误码用引擎的 `rx.mlsd` 在全部符号上数,不在 `res.y_slicer`(只存 2 万个符号)上离线数。理由与实测见 `engineering-pitfalls.md`"reach 读法会改 reach"(2026-10-09 起)。

### 常用命令

面向人类贡献者的版本(含 CI 各 job 守什么、按改动类型的额外检查)在根目录 [`CONTRIBUTING.md`](../CONTRIBUTING.md);这里是协作者速查。

```bash
pip install -e ".[gui,fec,jit,test]"    # 完整开发环境
pytest -q                               # 全套(JIT + iverilog 约 12 分钟,2026-10-08;原记 2 分钟已过时)
HALO_NO_JIT=1 pytest -q                 # 纯 Python 内核路径(CI 两条都跑)
ruff check src/ tests/ examples/ tools/ android/tools/ android/app/src/main/python/   # 与 CI 同
bash rtl/run_lockstep.sh                # SV vs Python 黄金模型逐位比对(需 iverilog)
python tools/fingerprint.py record A.json   # 数值指纹;改动前后各录一份,再 compare A.json B.json
python tools/run_examples.py --jobs 4 --save new/ && python tools/example_drift.py compare new/
                                        # 示例全尺寸输出 vs examples/expected/,列出仍引用旧值的文档行
python tools/example_drift.py derived   # examples/derived.yaml 里每处推导数的引用 vs examples/expected/
python tools/example_drift.py candidates [文档…]   # 比较措辞里所引示例不打印、又没登记的数:读,真推导数就登记
python -m halo_serdes_gui               # 启动 GUI
```

Android 那一半(铁律 #6)**没有本地等价物** —— 这个沙箱没有 Android SDK,
`dl.google.com` 也不通,所以 Kotlin 能不能编译、仪器化测试过不过,**只有 CI 能回答**。
推送后去看 `android` workflow 的各 job,判据是 emulator 最后一步打印的那行:

```
instrumented totals: N tests, 0 failures, 0 errors, 0 skipped
```

绿勾本身不是判据:`connectedDebugAndroidTest` 跑零个测试也算成功,
所以 `android/tools/run_instrumented.sh` 会在总数为零时退出非零。

### 提交尾注

```
Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_...
```

模型标识符不要写进提交信息、PR、代码注释或任何入库产物。

## 教训

**校验规则不能比被校验的代码更理想化。** 第一版配置校验按"理论上正确"来写
(`fir_n_pre` 必须是合法索引),结果误伤了引擎明确跳过的退化情形。测试是唯一能
发现这类过度严格的手段 —— 加校验后必须跑全套,失败要先判断是"抓到真 bug"还是
"规则过严"。
