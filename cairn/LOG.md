# Project Cairn 日志

本文件按倒序记录实质性进展 —— 最新条目在本行正下方。每条保持简短(摘要+指针),
结论沉淀进 `cairn/<topic>.md`。

## 2026-10-02 · DSP 发端,阶段 0 + 1:TxPipeline + DAC + 驱动器压缩(feat/dsp-tx)

- **阶段 0**:七处手拼的 TX(timedomain 两处、static_link、statistical、optical_stage、reconstruct、cdr_tracking)收成
  `tx/pipeline.py::TxPipeline`;475 值引擎指纹 + 44 值 TX 路径指纹(剖面时钟、重建、CDR 边沿、光发射功率、串联)与 main 逐位同,
  测试数与基线同(jit 550 / nojit 548)。COM 与 NativeCom 保留参考 TX。清单与行号:`cairn/DSP发端与PR.md` §1。
- **阶段 1**:`tx.dac_*`(中升量化、温度计 + 二进制单元失配 INL、削峰计数,失配用独立随机流)、`tx.drv_*`(Hammerstein;
  `"curve"` 与 E/O 共用 `core/static_curve.py`,同 c 逐位相同)、`analysis/tx_metrics.py`、统计引擎 σ_q 折算 + 驱动器 warning、
  backchannel 峰值约束读 `dac_fs`、表单字段、示例 35。
- 闭式:SQNR(峰值位置平均后)N = 5..8 差 ≤ 0.09 dB;INL RMS = σ√((M−1)/6) ±20%;cubic HD3 差 < 1e-13 dB;tanh P1dB −1.00 dB;
  σ_q 折算与波形差 RMS 15% 内。铁律 3(只开 DAC,静态 vs 统计,三损耗点):4 bit 0.73–0.81,6 bit 0.94–0.95。
- 示例 35(示例 18 的 224G LR):DAC 6/7/8 bit 的 reach 与理想差在 ±0.25 dB 扫描分辨率内;驱动器 c = 0.2 掉 1.6–1.7 dB、c = 0.1 掉 0.3 dB
  —— 这条链上瓶颈是驱动器压缩,不是 7 bit。
- **发现两处既有缺口**(未修,进 ROADMAP):接收端起始均衡看不见 TX FFE(静态引擎 TX (−0.08, 0.78, −0.14) 时 SNR 27.7 → 11.2 dB;
  修了会动 6 个预置的指纹,P1 1c);统计引擎从未建模 `tx.bw`(P3 #8)。坑:SQNR 闭式要对峰值位置平均;backchannel 的 `peak` 重名。
- 验证:jit 603 passed / 2 skipped,nojit 601 passed / 4 skipped,新开关全关时两份指纹逐位同;Android:ANDROID_TOTALS。

## 2026-10-02 · 光互联链路,阶段 3:E/O 大信号曲线 + TDECQ(feat/optical-link-stage3)

- **`OpticalConfig.li_compression`** → `optical.StaticCurve`:VCSEL 凹(热翻转)/ EML 凸(EAM 指数吸收),外电平钉住、OMA/ER 不变;
  `optical_rlm` 让光域 R_LM 成为导出量。验收:c = 0 时 R_LM = 1,c 增大单调降(VCSEL 与闭式 1 − 8κ/3 逐位同)。
- **被推翻:VCSEL 曲线放在 E/O 动态之前(Hammerstein)** → 两种器件都放在 E/O 小信号输出之后(Wiener);初稿让理想段 A
  单独卷积,2×baud 网格上的砖墙 sinc 绕回,TDECQ 读成 23 dB。修法 + `ChannelModel.band_limited()` 见 `cairn/光互联建模.md` §8 与 pitfalls。
- 时域引擎曲线开启时三段卷积(驱动 → 曲线 → 光纤);曲线关闭时 475 值指纹与 main 逐位同。统计引擎对曲线开启的配置发 warning
  (铁律 3 不保证;实测 ADC PAM4 统计/时域 0.61–0.70)。
- **`analysis/tdecq.py`**(802.3 121.8.5,标准原文被代理拦截,来源逐条在 docstring):理想眼 0.001 dB;已知 σ 的眼与闭式 0.969 dB
  差 0.07–0.14 dB(4 个种子,门槛 0.2);理想发射机过 BT4 0.34 dB。
- 示例 34:VCSEL 100G/λ 4.59 dB(SR1 4.4,刚好不过;ER ≥ 4.2 dB 或 f_r ≥ 22.3 GHz 过),EML 200G/λ 2.08 dB(DR1 3.4;带宽 ≥ 34.6 GHz);
  压缩 0.5 VCSEL +5.07 / EML +0.85 dB,VCSEL 的代价随带宽收缩 —— 部分已拆,剩余未验证。写进 SUMMARY §05。
- **更正阶段 2 条目**:「Optical」页交付时桌面点开即 `AttributeError`(GUI 兼容层没再导出 `optical_study`);已修 + 守卫测试,坑进 pitfalls。
  专题文档的「被推翻的判断」标题在阶段 2 被误删,已恢复并加更正说明。
- 表单 `topology.optical.li_compression`、`tdecq` study(桌面 + Android 同一份广告)、Optical 页 TDECQ 卡片;ROADMAP P3-11 改为余项(dj DFE、
  统计引擎曲线 ISI、LPO 线性 EQ)。本地 jit 550 passed / 2 skipped,nojit 548 passed / 4 skipped;Android CI(0b76f2e,run 36962844384;之后只改 tests/ 与文档):`instrumented totals: 33 tests, 0 failures, 0 errors, 0 skipped`
  (解释型与编译型 APK 都过,StudyContractTest 2/0f 含 tdecq 广告);指纹 475 值与 main 逐位同。

## 2026-10-02 · 光互联链路,阶段 2:重定时串联(feat/optical-link-stage2)

- **重定时 = 三条链路串联。** `TopologyConfig.retimer: none | both` + `retimer_rx` / `retimer_tx`;`engine/cascade.py`
  把链切成 host→段 A→重定时 / 重定时→光路(两端理想 0.5 段)→重定时 / 重定时→段 B→host,每段
  `run_time_link(seg_cfg, symbols=上一段判决)`,判决流取 `SimResult.extras["decisions"]`(新键;409 值指纹其余不变),
  段 k 种子 = seed + k;端到端 BER = host 判决 vs host 符号流,从 Σwarmup 起比对;同时报每段 BER 与 1 − ∏(1 − pᵢ)。
  统计版 `run_cascade_statistical`:ADC 收端用时域引擎的 MMSE 起始 FFE,扫描与界面用。
- **`symbols=`** 进两条时域路径与静态链路,只收整数索引(浮点即波形,拒收,铁律 5);给 PRBS 自己的序列时逐位相同。
- **验收**(`tests/test_cascade.py`):三段 ~1e-3 / ~1e-2 时端到端在 Wilson 区间(z = 3)内等于 1 − ∏(1 − pᵢ),
  ADC PAM4 与 MS NRZ 都过;重定时光路段 BER < LPO 整链;统计级联 / 时域端到端 2× 内;`test_domain_boundary` 继续过。
- **理想段 / 零相位光纤块让 trimmer 失效**:零长度段在 2×baud 网格上是砖墙 → 补零成 sinc;高斯 / 色散响应零相位 → 反因果半边绕到数组尾;两者都让按一阶差分能量的 trimmer 整段保留 32k 样本,再直接卷积 → 一点 177 s。理想段改到 1/(2dt) 网格、光纤块加纯延时后 76 样本、0.7 s。时域引擎尾部 2–3 个无效判决进 ROADMAP P1-1b。
- **全刻度陷阱**:同一个 `retimer_rx` 既收段 A(增益 0.5)又收光路(0.25),host RX 未重定时看整链(0.25)、重定时后看段 B(0.5);
  第一次跑三段时段 A 的 BER 4e-4 是 ADC 削波,不是信道 —— 预置里 host 0.3 / 重定时器 0.6。
- 示例 33(LPO / retimed / CPO 同光路 + 三杠杆表):reach LPO 8 dB 143 m / retimed 249 m / CPO 4 dB 182 m;杠杆(基线 97 m)电段 +62、RIN +81、重定时 +132、三者一起 +185 m(单项之和 +274)—— 不可叠加,三个杠杆动的是同一段光路的 SNR;写进 `docs/SUMMARY.md` §05。
- 预置 `pam4_100g_lpo_vcsel.yaml`;`config_bridge` 加 `topology.retimer*`(每个预置可从表单值原样重建,新守卫);
  `studies.optical_study` + GUI「Optical」页(第 17 页)+ Android 同一份 study 广告;Android CI(de1f86b,run 36957977369,解释型 APK):`instrumented totals: 33 tests, 0 failures, 0 errors, 0 skipped`(StudyContractTest 2/0f 含新 study 广告;解释型与编译型 APK 都过);test 矩阵、lint、import-clean、vendor-drift、rtl-lockstep 全绿;本地 jit 526 passed / 2 skipped,nojit 524 passed / 4 skipped;
  文档:USAGE §16、GUI.md、README、CHANGELOG、COMPARISON §③、ROADMAP P3-11、SUMMARY §05、`cairn/光互联建模.md` §7。

## 2026-10-01 · 光互联链路,阶段 1:光路作为信道段 + 电平相关噪声(feat/optical-link-stage1)

- **一条链三种拓扑。** `LinkConfig.topology = TopologyConfig(seg_a, optical, seg_b)`(None = 电链路,
  409 值引擎指纹逐位不变);`optical/{eo,fiber,oe,noise}.py` 只依赖 numpy;`ChannelModel.cascade`
  (`ref_gain` 连乘,两段理想电信道 H = 0.25、`loss_at` 0 dB)+ `from_topology` 挂 `.optical`;
  两个引擎在 `engine/optical_stage.py` 共用同一切点(光电二极管电流节点,**O/E 之前** —— 计划写的是之后,
  改的理由:三项噪声都是 TIA 输入电流,放在之前才由 TIA 带宽而非 osr 决定噪声带宽)。
- **统计引擎每电平一核,σ 改了一次。** 名义功率 σ 在 ER 6 dB 悲观 1.4–2.15×;改成判决样本沿接收滤波器
  记忆的光功率二阶矩后,对时域判决流分电平实测四个电平 2% 内。铁律 3(`tests/test_optical.py`):
  ADC PAM4 0.88 / 0.71 / 0.63,MS NRZ 0.92 / 0.84 / 0.86(ER 3 / 4.5 / 6 dB)。剩余乐观来自每电平
  噪声是高斯尺度混合;**判决:逐电平 σ 近似三点都过 2×,ISI 分箱核进 ROADMAP P3-11。**
  MS **PAM4** 的统计/时域在纯电 AWGN 下就只有 0.15–0.78(既有缺口),所以 MS 断言用 NRZ。
- **被推翻:"OM4 100 m 的 3 dB 点 = 47 GHz"** —— 47 GHz 是 −3 dBo(|H| = 0.5,电 −6 dB),按计划措辞会把
  光纤带宽高估 1.41×。器件参数核验表(标准原文域名被出口代理拦截,来源是基线提案 / 数据手册 / G.652 推算)在
  `cairn/光互联建模.md` §5:SR1 ER min 2.5 dB、RIN12OMA −131 dB/Hz、OMA −3…+3.5 dBm;DR1 ER min 3.5 dB、
  RIN×OMA −139;TIA 40–43 GHz / 10–13 pA/√Hz(Renesas);C2M 13 dB(OIF)→ 16 dB(LPO MSA)。
- 示例 32(LPO vs CPO,VCSEL + OM4,ADC + KP4,电段 4/8/12/16 dB):电段 4/8/12/16 dB(总 8/16/24/32 dB @26.56 GHz),OMA +1 dBm、ER 4 dB、RIN −145 dB/Hz,ADC 10 bit + CTLE 3 dB + FFE 4/12,KP4 1e-15:reach **209 / 153 / 0 / 0 m**(单调递减;12 dB 在 30 m 时 pre-FEC 3.9e-4 刚过 KP4 瀑布,16 dB 在 30 m SNR 14 dB);100 m 处 OMA 裕度 8.6 / 5.6 / — / — dB → **CPO 比 8 dB 段的 LPO 多 3.0 dB 光裕度**;12/16 dB 段在 SR1 OMA 窗口内任何 OMA 都关不上 —— 链路是 RIN 限制的(噪声随光功率涨),电损耗经 FFE 放大噪声才是决定量,OMA 几乎不动结果(+1 与 +3 dBm 同 SNR)。
- `config_bridge` 加 `topology.*` 27 个字段(桌面 / Android 自动出现,铁律 6 触发);
  Android CI(af0e61c,run 36943475934):`instrumented totals: 33 tests, 0 failures, 0 errors, 0 skipped`(FormContractTest 6/0f,解释型与编译型 APK 都过);test 矩阵 3.10/3.11 × jit/nojit、lint、import-clean、vendor-drift、rtl-lockstep 全绿(本地 jit 510 passed / 2 skipped,nojit 508 passed / 4 skipped);文档:USAGE §16、README、CHANGELOG、COMPARISON §③、ROADMAP P3-11、
  `cairn/光互联建模.md`(新)、pitfalls 三条。

## 2026-10-01 · PLL 时钟相噪剖面,阶段 3:RX 采样时钟 + 活桥(feat/clock-profile-rx)

- **接收端有了自己的时钟。** `RxConfig.clock`(同一个 `ClockConfig`,默认理想全零);两个内核
  新增 `rx_clock_offset_samples`,逐符号加到环路相位再采样,`phase_track` 报告实际采样位置;
  CDR 追踪的是两只时钟之差。**全零时与改动前内核逐位相同**(改动前纯 Python 内核冻结在
  `tests/golden/kernels_prechange.py`,同进程比对;最初的 sha256 版本在 CI 3.10 上就不一致),409 值引擎指纹不变;
  理想时钟不消耗随机数(RX 抽样排在所有既有抽样之后)。统计引擎把两份剖面经同一条 |1−H| 功率
  相加;全白噪时 σ = rj_tx ⊕ rj_rx。
- **"TX/RX 各自 1/f² 独立时残余功率相加 ±10%"只在线性环路上成立。** MM(ADC)环路实测
  0.98–1.09(两个种子、两个增益,宽带剖面让单次实现收敛);BB 环路鉴相器增益随总输入下降,
  第二只时钟同时收窄环路,实测超加性 1.24×,而模型(带这个依赖)仍与实测差 <10%。写验收时
  原话不能照搬到 BB 上 —— 改成"MM 相加、BB 超加且模型吻合"两条断言。
- **宽带抖动淹没 BB 鉴相器会失锁,slew 警告看不见。** σ_e 0.08 UI 锁定、0.14 UI 滑移;统计引擎
  从 0.1 UI 起发 `noise-limited` 警告。
- **活桥 `io/pll_bridge.py`**:`profile_from_analysis`(鸭子类型读 AnalysisResult,不 import)、
  `profile_from_preset`(函数内 import pllsim,缺失时 ImportError 指向 `[pll]` extra);
  对 sibling 检出的 pllsim(931cfaf)重现七个内置文件逐位一致(|ΔL| = 0.000 dB),
  `rms_jitter_s(int_band)` == `ar.jitter_fs`。手机计算路径不 import 它(测试钉着)。
- `config_bridge` 加 `rx.clock.kind/file/f0_hz/rj_ui`(桌面/Android 自动出现,铁律 6 触发);
  Android CI(ec32bf3,run 36860768206):`instrumented totals: 33 tests, 0 failures, 0 errors, 0 skipped`
  (FormContractTest 6/0f 含阶段 2 的剖面选择用例),解释型与编译型 APK 都过;文档:`docs/clock_profile.md` 阶段 3 节、USAGE §9、README、CHANGELOG;
  坑进 `engineering-pitfalls.md`。

## 2026-10-01 · PLL 时钟相噪剖面,阶段 2:CDR 追踪 + 双引擎交叉校验(feat/clock-profile-cdr)

- **统计引擎学会了 CDR。** `cdr/linear.py` 把两个内核跑的 PI 环路写成 `|1−H(f)|`、`|H(f)|`;
  剖面时钟的采样时刻 σ² = ∫S_φ|1−H|² + var(q)/k_pd²·mean|H|²(环路未追踪的 + 环路自己加的),
  自 1/(N·UI) 起积分(两引擎看同一频带)。`kind=white` 路径与 469 个指纹逐位不变。
- **BB 鉴相器增益不是常数**:k_pd = ρ√(2/π)/σ_e,σ_e 是鉴相器输入抖动 = 未追踪 ⊕ 自噪声 ⊕
  边沿噪声(AWGN+ISI 经边沿斜率换成 UI),自洽定点求解。安静时钟上自噪声收敛到 ~0.8 kp —— 经典
  的 BB 猎振,是推出来的不是假设的。实测 vs 模型:有损+噪声信道 kp_shift 4/6/8 为 0.97/0.99/1.00;
  干净信道 0.82/0.95/1.08(极限环不是白噪,余差在此)。
- **MM 鉴相器的闭式解在重 ISI 下错 3.7×**(斜率×E|ℓ| 对 112G 预 FFE 样本),改成在锁定点游标集上
  对随机符号做种子化期望(60k 符号,毫秒级,逐位可复现)—— 对实际波形:斜率 0.0144 vs 0.0148,
  方差 0.00322 vs 0.0032。112G ADC 链路实测/模型 kp 5/7/9:0.96/0.96/0.73(ki_shift 15 欠阻尼)。
- **锁定点 ≠ 脉冲峰值。** 短信道 NRZ 脉冲是 1 UI 平台,argmax 在前沿角,Alexander 边沿锁在平台
  中点(实测 +6~+11 样本)。第一版在峰值取斜率/ISI,边沿噪声估计差好几倍;`lock_offset_samples`
  先搜过零再取值后与实测边沿统计一致(干净 0.0031 vs 0.0032 UI,有损 0.051 vs 0.050 UI)。
- **验收(`tests/test_clock_profile_cdr.py`,20 项)**:LTI+AWGN+1/f²+BB-CDR 双引擎 BER
  比 1.33–1.49(kp 4/6/8,≤ 2×);同 0.11 UI RMS 白噪 vs 1/f² 残余 0.119 vs 0.013 UI(模型 0.120 /
  0.015)—— 接入的存在理由写成了断言;JTOL 在 profile 下可跑,10 MHz 容限 1.64→1.26 UI、
  100/400 MHz 不变;滑移警告在实测失锁处(速率/极限 ≥0.4)触发、锁定处(≤0.3)不触发。
- **ex31**:PAM4 112 GBd 三时钟(白噪/SSPLL/CPPLL 各 200 fs)× kp_shift 4–9,18 次跑 20 s;
  三个时钟最优都在 kp_shift 4,白噪 BER 2.1e-3 / SSPLL 3.9e-3 / CPPLL 3.2e-3。
- **两端选剖面**:`tx.clock.file` 改 `opt_enum`(选项 = 内置剖面),桌面 Select 与 Android
  下拉同时得到,Kotlin 只加了 `isEnum` 与空项;`FormContractTest` 新增一条。铁律 6 触发,Android
  CI 见 PR。
- 文档:`docs/clock_profile.md` "Through the CDR" 节、USAGE §9、CHANGELOG;坑进
  `engineering-pitfalls.md`(锁定点、MM 闭式解、滑移、残余测量要跳过捕获瞬态)。

## 2026-10-01 · PLL 时钟相噪剖面接入,阶段 1:TX 有色抖动(feat/clock-profile-tx)

- **接口是一份文件,不是 import。** `tx.clock.kind: profile` + `tx.clock.file` 指向一份
  剖面 YAML(`f0_hz`、`f_hz[]`、`l_dbc_hz[]`、`spurs[]`、`source`,格式定义在
  `docs/clock_profile.md`);pllsim 的导出器在它的 `feat/clock-profile-export`(67fe1c7)上
  已按此格式实现。本库只 vendor 了 `synth_from_psd` 与 `integrate_pn` 两个纯 numpy 函数
  (`vendor/pllsim/`,逐字节同 pll_simulator@931cfaf,`tools/vendor_check.py` + CI
  `vendor-drift` job 守着,2 verbatim / 0 DRIFT)。
- **`ClockConfig` 新增,四个抖动字段从 `TxConfig` 搬入。** loader 加了嵌套迁移,旧 YAML 的
  `tx.rj_ui` 仍加载到同一个 `LinkConfig`;22 个测试/示例文件的 `TxConfig(rj_ui=...)` 用 AST
  重写成 `clock=ClockConfig(...)`。**`kind="white"` 逐位不变**:9 个预设 × 4 条引擎路径
  469 个标量与数组哈希,与 main 完全一致。关键在 rng 顺序 —— 剖面合成只在 profile 时、
  且在白噪三项之前取随机数。
- **`tx/clock.py` 是本库唯一知道剖面长什么样的地方**:(log f, dB) 线性插值到 1/UI 采样率的
  FFT 网格,f0/2 以上置零(时钟自己的 Nyquist 之外剖面什么也没说),合成 φ[k] 后
  除以 2π·f0 得秒;杂散按 pllsim 的单边带定义 A = 2·10^(dbc/20) 逐条加正弦。
- **验收全部是闭式断言**(`tests/test_clock_profile.py`,40 项):平坦剖面 σ 与等效 rj_ui
  差 −0.34%(≤ 3%);1/f² 相邻差方差随滞后线性(R² > 0.99);−60 dBc 杂散从合成相位谱读回
  −60.0 ± 0.1 dB;f0/2 以上功率 < 1e-9;f0 减半秒数加倍相位不变;七个剖面各跑通
  PAM4 112G ADC 链路。
- **三条实测后改写的验收,都如实写成了测试而不是放宽容差**:(1) 纯 1/f² 单次实现与
  `integrate_pn` 的比天然散布 0.49–5.36,期望值比积分大 π²/6 —— 环路整形的剖面才有
  "单次 ≤ 10%",七个真实剖面都是;(2) `calc_jitter` 把有色低频抖动归到 Pj,Rj 只剩真 σ 的 6%,
  和积分比的是 `std(tie)`(实测 2.3%);(3) 过零检测对亚样本音读数减半,经引擎的 Pj ≤ 1 dB
  用 −20 dBc 验,约定在合成相位上用 −60 dBc 钉。五条坑进 `engineering-pitfalls.md`。
- **顺手修了一个会让 GUI/Android 建不出 profile 时钟的 bug**:`apply_overrides` 逐键应用,
  `kind=profile` 先于 `file` 到达就被校验拒绝。改成按 dataclass 分组一次 `replace`。
- **剖面文件的来源要说清**:本机没有 `examples/out/clock_profiles/`(gitignored),七个 yaml 是
  在 pllsim `feat/clock-profile-export`@67fe1c7 的隔离 venv 里跑它自己的 ex22 生成的,
  `source` 字段带 commit —— 不是手编数据,但也不是用户给的路径。
- 测试:jit `434 passed, 1 skipped,`,no-JIT `432 passed, 3 skipped,`(基线 380 / 378),ruff clean。"剖面文件是唯一接缝"进 `architecture-invariants.md` 1b;
  Android 侧 `config_bridge` 字段路径改了、`stageHaloAssets` 多打包 `data/clock_profiles/`,
  铁律 6 触发,CI 的 Android job 见 PR。

## 2026-09-12 · 体检后的五项整改:铁律 #2/#3/#5 从文字变成执行

- **铁律 #5 以前只是一句话。** `SymbolStream` 定义了却**全仓库零次构造**,没有采样器模块,
  11 个调用点各自就地 `[::osr]` 或 `np.repeat` 跨域。新建 `core/sampler.py` 作为唯一入口
  (`sample_baud` / `baud_samples` / `hold` / `upsampled_taps`),`tests/test_domain_boundary.py`
  扫描 `src/halo_serdes` 断言这些惯用写法只出现在 sampler 里。**没有东西执行的不变量就是注释。**
- **铁律 #2 有结构性风险:两条 RX 路径的打分是约 35 行复制粘贴**,slicer SNR 公式另有 3 处内联
  版本与正本 `metrics.slicer_snr_db` 并存。抽出 `engine/scoring.py`,三条引擎路径
  (mixed-signal / ADC / static)现在共用同一个 `score()`。
- **两次重构都以「不许改变任何数值」为验收**:9 个预设 × 4 条引擎路径 = **469 个标量与数组哈希,
  前后逐位一致**。"测试通过"是更弱的说法,所以先建了指纹基线再动手。
- **铁律 #3 的失败在界面上是不可见的**:`dual_engine` 把统计引擎异常变成 `rec.stat = None`,
  与"根本没跑"渲染完全一样 —— 而那个页面**就是**双引擎交叉校验。新增 `RunRecord.stat_error`,
  连同另外 4 处吞异常(坏 YAML 粘贴毫无反应、CTLE 卡片静默消失、两处眼图重建返回裸 None)一起改成显示原因。
- **写测试时又踩了本仓库已记过的坑**:`preset_names()[0]` 是 "Library defaults",信道建不起来,
  三个测试因此红在信道上而不是被测对象上。已改成"取第一个信道能建起来的预设"。
- **测试还抓到我自己修得不完整**:给 CTLE 卡片加了保护,但下一行的 Bode 图仍在裸调
  `Ctle.from_config`,整个 tab 照样崩 —— 提示渲染出来又被丢掉。
- **铁律 #6 有执行缺口**:`build-windows.yml` 只在 GUI/packaging 改动时触发,改核心引擎不验证
  桌面打包,而 PyInstaller 对核心变动最敏感。已补上 `src/halo_serdes/**` 等触发路径。
- 补 `LICENSE`(pyproject 早已声明 MIT,文件一直不存在)。
- **三个 workflow 全绿后 main 已 fast-forward 到 `b6e15ca`**:`test`、`android`
  (5 个 job 全部实际执行,含两个模拟器与 `--native`/`--pure` 两道闸门)、
  `build-windows`(PyInstaller + Nuitka standalone + onefile,各带 smoke test;
  Nuitka 单步编译 1 小时 48 分)。**默认分支仍是工作分支** —— 那是 GitHub
  仓库设置,没有对应 API,需要仓库所有者手动切。
- **一处不能声称已验证的**:`build-windows.yml` 这次被触发**不证明**新加的核心层路径
  生效 —— 这个提交同时改了 `src/halo_serdes_gui/**`,旧条件本来就会命中。
  真正的验证要等下一个只碰 `src/halo_serdes/**` 的提交。

## 2026-08-25 · Cython 编译版 APK 落地:交叉编译 + 模拟器 32 项全过

- **run #33 五个 job 全绿。** 编译版 APK 由 `compiled-apk` 出(交叉编译两个 ABI 用时
  3 分 10 秒),`compiled-emulator` 拿它跑完整仪器化套件:**32 tests, 0 failures,
  0 errors, 0 skipped**,6 张截图。干净的交叉编译不等于 `.so` import 得了,这一步
  是 CI 里唯一能回答后者的东西。
- **切换开关只有一件事**:`android/app/pysrc/` 里有没有 wheel。有就走 `--find-links`
  + `install("halo-serdes")` 并从 srcDirs 里去掉 `../../src`;没有就是今天的解释版。
  manifest、Kotlin、Compose、Gradle 任务一律不动。
- **pip 按 tag 挑 wheel 这件事是被证据确认的,不是推断**:Chaquopy 安装日志里
  `halo-serdes` 在两个 ABI 的列表里**各出现一次**,而 `scikit-rf`/`six`/`pytz`
  这些纯 Python 包只出现一次。
- **`chaquopyTarget` 我猜错了一次,而那次红是这套东西最值的一次。** Chaquopy 16.1.0
  用 3.10.15-1,不是 Maven 上最新的 3.10.19-0。按错版本头文件编出来的 wheel,
  **交叉编译干净、APK 也 assemble 成功,构建日志一个字都没说**。要不是装配之后加了
  那道对照断言,下一步就是拿这个 APK 上模拟器,然后对着一个指向别处的崩溃查。
- **vendor 了 skill 的两个脚本进 `android/tools/`**(CI 没装 skill),`android_wheel.py`
  改了一处:wheel 组装不再按后缀丢 `.c`,只丢旁边有同名 `.so` 的。
- 结论与"仍未验证"见 `cairn/android-compiled-variant.md`;ROADMAP 6d 已完成删除,
  6b(edge-to-edge 未验证)与 6c(APK 打进 `halo_serdes_gui`)仍在。

## 2026-08-25 · Chaquopy 工程复审 + Cython 编译版宿主侧验证

- **复审(照 `python-android-apk` skill 的坑表逐条对)**:大部分没踩到 —— `--no-index`、
  多行 `run:` 折行、管道退出码、陈旧 sdist(本项目结构上免疫:走 `setSrcDirs` 而非
  sdist/pysrc)、`install("../..")`、缺 loading 状态、skip 冒充 pass,全部不适用或已避开。
  **两条成立**:(1) `targetSdk = 35` 却没有 `enableEdgeToEdge`/WindowInsets 处理,
  完全靠 Material3 `Scaffold`/`TopAppBar`/`NavigationBar` 的默认值 —— **未经验证**,
  因为模拟器是 API 34;(2) APK 里打进了 `halo_serdes_gui`(680 K 的 Dash 桌面 UI 源码),
  手机永远不会 import 它 —— `setSrcDirs(listOf("src/main/python", "../../src"))` 是整棵 `src/`。
- **编译版:宿主侧验收通过。** 53 个模块编 46 个,删掉 `.py` 后
  **356 passed, 1 skipped —— 与解释版基线逐项一致**;12 个 `.so` 里 grep docstring 0 命中。
  排除的 7 个各有具体原因(1 个 Cython 自身崩溃 + 6 个 numba 层),见
  `cairn/android-compiled-variant.md`。
- **两处结构性不匹配**:skill 脚本假设「一发行版一包」,本仓库一个发行版三个包 ——
  两次调用产出同名 wheel,得合并并**重算 `RECORD`**;组装时无条件丢 `.c`,
  连带丢掉当 package data 发布的手写 `io/ami_c/halo_fir_ami.c`。
- **两次控制组都是必需的,不是仪式**:不先断言 `__file__` 是 `.so`,"全绿"可能只证明
  编译版根本没装上;不先在未编译模块的 `.pyc` 里 grep 命中,`.so` 里的 0 命中什么也不说明。
- **未决:要让编译版进 CI 就得把 skill 的 414 行脚本 vendor 进这个公开仓库**
  (无 license 头),这个决定不由我做。另外**交叉编译从未跑过** —— 本机无 NDK,
  且本次验证在 3.11、Chaquopy 目标是 3.10。

## 2026-08-24 · 新增铁律 #6:桌面与 Android 必须同时验证

- 规则本身一句话:**动了共用层(`src/halo_serdes/`、`src/halo_serdes_app/`)就两端都要跑通,
  任一端未验证即未完成。** 写进 `AGENTS.md` 铁律清单(第 6 条,文件到 65 行预算上限)。
- **为什么值得成为铁律,而不是"记得多跑一次"**:两端跑的不是同一套东西。共用的只有
  Python 源码,运行它的环境有**五处系统性差异**,每一处都在本项目真实咬过人 ——
  依赖版本(Chaquopy 给 scipy 1.8.1)、子模块加载语义(`scipy.interpolate` 那个 bug)、
  依赖是否存在(手机上没有 matplotlib/numba/galois)、文件系统语义(Chaquopy 的
  importer 不是文件系统)、生命周期(`connectedAndroidTest` 跑完卸载 APK)。
  论证与"完整验证"的具体含义见 `cairn/architecture-invariants.md` 第 6 条。
- **两端谁也替代不了谁**:前三条差异意味着桌面 `pytest` 全绿不构成手机能跑的证据;
  后两条意味着手机跑通也不构成桌面打包正确的证据(桌面走 PyInstaller/Nuitka)。
- **`wheel-versions` job 就是这条铁律的执行者**,它已经兑现过一次 ——
  `scipy.interpolate` 那个 bug 是它抓到的,而当时桌面套件全绿。
- 判据写清了:不是"我跑过了",是日志最后一行的
  `instrumented totals: N tests, 0 failures` —— 绿勾本身不够。

## 2026-08-24 · 界面进 CI:32 项仪器化测试 + 6 张截图

- **`UiRenderTest` 用 `createAndroidComposeRule` 起真 Activity、跑真 Python**,
  五个测试断言:开屏落在**能跑的**预设、内置信道芯片与 facade 列表一致、时域三档带
  耗时估计、Run 后浴盆图与统计眼**真的画了东西**(像素级)、Sweeps 列出通告的每个 study。
  **结果:32 tests, 0 failures**(run #29),截图 6 张作为 artifact。
- **两类断言强度不同,分开写在类注释里**:结构用可见文本匹配(顺带把文案纳入测试);
  像素用 `assertDrew` 数颜色种类。**故意不做 golden image** —— 那种东西在模拟器字体
  和 GPU 一变就红,随之而来的"再基线一次"会让它彻底失去意义。
- **必须绕开的坑:不定进度条让 Compose 永不空闲。** 自动同步等的是时钟空闲,
  而 `CircularProgressIndicator` 是无限动画 —— 交互会**阻塞到 job 超时**而不是失败。
  所有不定进度条打 `TestTags.BUSY`,交互前 `waitUntil { 没有 BUSY }`。
- **这一轮红了四次,每一次的价值都不一样**:
  (1) `Color.value.toInt()` 对任何 sRGB 都是同一个数 —— 我加来抓"图表画白"的断言**自己是瞎的**;
  (2) `adb exec-out` 返回本地退出码,`run-as` 的错误文本被我的循环变成了四个文件名,
  其中带冒号的那个让**32 个测试全过的一次运行变红**;
  (3) 截图丢失的真因是 **`connectedAndroidTest` 跑完会卸载两个 APK** ——
  我为此换过两条取回路径,而**位置从来就不是问题**;改用 `TestStorage`。
- **花掉最多轮次的不是这些,是"读不到日志"**:诊断信息在日志几千行深、尾部全是拆机输出,
  我反复取窗口都落在旁边。两步才修对:先挪到**步骤最后**(不够,步骤末尾不是 job 末尾),
  再让脚本写 `ci-summary.txt`、workflow **最后一步** `cat` 一次。
- 五条坑均已沉淀进 `cairn/engineering-pitfalls.md`,含一条推理层面的:
  **两条独立路径给出同一类错误时,该怀疑的是共同前提,不是各自实现。**

## 2026-08-23 · M7–M9:时域长跑 / Touchstone 导入 / 通用扫描页

- **M7**:三档质量 + 前台服务 + 取消。服务类型选 `specialUse` 而非 `dataSync` ——
  没有东西在同步,而 `dataSync` 的每日预算是给网络传输的;`START_NOT_STICKY`,
  因为 run 是本进程的 Python 线程,重启的 service 只会播报一个不存在的任务。
- **新增门面方法 `result`**:`poll` 只给 handle,此前读不出跑完的结果。它把
  `n_errors`/`n_checked` 摆在 BER 旁边并给 `ber_is_upper_bound` —— 两万符号下舒适
  链路是**零错误**,`ber == 0.0` 读起来像"完美"、意思是"低于这次能看见的下限"。
  这一栏是为 pitfalls 里"四个错误上算增益"那条留的。
- **M8 的硬约束在开工前就问掉了**:先在宿主上屏蔽 pandas 跑完整条 Touchstone
  读取链路,证明 skrf 不需要它,再动 UI。装了 skrf,`.s4p` 也一并打包,九个预设
  现在都能跑;解压改成按 `lastUpdateTime` 打戳(4.4 MB 不能每次启动重拷)。
- **M9 的扫描页里没有任何一个 study 的名字**:列表/标题/说明/面板规格全部来自
  `schema`(`STUDY_LABELS` + `STUDY_PLOTS`)。与表单和 `SECTIONS` 是同一笔交易。
- **两次红都抓到了真问题,不是测试的毛病**:
  (1) `optString` 把显式 null 变成字符串 `"null"` —— 取消后的 job 拿到一个叫
  `"null"` 的 handle,`field: None` 的错误去标红一个叫 `"null"` 的输入框。
  **我最初的测试断言里是同一个 bug**,红得对。
  (2) `wheel-versions` 抓到 skrf 的 `Network.interpolate` 只 `import scipy` 就用
  `scipy.interpolate.interp1d` —— 现代 SciPy 惰性加载盖住了它,**手机上的 1.8.1
  不会**,设备上能不能跑通取决于 import 顺序。这正是那个 job 存在的理由。
- **`--no-deps` 这条路不通**:Chaquopy 不接受 `install("--no-deps", ...)`,
  而它的 `options()` 是全局的 —— 会连 numpy/scipy 的 openblas 一起剥掉。改为
  正常安装(多背一个 pandas),宿主那条"不需要 pandas"的测试保留,因为它说明
  这个依赖只是浪费、不是承重。
- 坑均已沉淀进 `cairn/engineering-pitfalls.md`;结构与理由见 `android/README.md`。

## 2026-08-23 · M6:浴盆曲线与统计眼(Canvas 原生绘图)

- 不引入图表库:要画的形状只有两种,通用库会带来版本匹配风险换一堆用不到的功能。
- **顺带修掉一个 API 缺陷**:`series(stat_eye)` 声明的 `zmin=-12/zmax=0` 两端都不真 ——
  实测 max ≈ −2.7(色带顶部 1/3 用不到)、33% 的格子在 −18 钳位底(低于声称下限)。
  改为返回数据实际范围 + 显式 `floor`;floor 格子渲染成背景,因为它们的含义是
  "没解出概率",不是"概率很小"。宿主测试钉住。
- 两个渲染决定:纵轴按数据自适应(舒适链路只跨 5 个数量级);眼图走一张缩放位图而非
  32768 次 `drawRect`,位图按 run handle 缓存(用 `z` 做 key 的深比较比绘制还贵)。
- **第一次 CI 红的原因不是我猜的那个**:我先假设是"取了跑不了的 touchstone 预设",
  但 XML dump 显示 `InvalidTestClassError: Method ...() should be void` ——
  `= runBlocking { ... }` 以 `release(...)` 收尾,推断返回类型不是 `Unit`,
  **整个类在校验阶段被拒**,一个测试都没跑。计数脚本报 "1 tests, 1 failed"
  (实际有 2 个)才让这件事可见。全部改为 `runBlocking<Unit>`。
- 那个 touchstone 假设**是个潜伏问题**(类跑起来就会栽),一并修了:
  "挑第一个预设"这个错我写了两遍,抽成 `TestPresets.runnable()` 一处。
- 眼图**按需拉取**而非随每次 Run 一起返回:缩减后仍有约 4k 个数,而多数时候按 Run 只为看 BER。
- **结果(run #14 全绿)**:`ChartDataTest 2 / FormContractTest 5 / LinkFacadeTest 4 /
  PythonStackTest 5`,`instrumented totals: 16 tests, 0 failures`。
  ChartDataTest 从"1 tests, 1 failed"(幽灵)变成"2 tests, 0 failed"。
- 这一轮红了两次,**两次都是我引入的**,第二次还是修第一次时引入的。教训不在 Kotlin,
  在于**批量文本替换要核对影响面** —— 脚本已经把"6 处替换 vs 5 个 @Test"打出来了,我跳过了。

## 2026-08-23 · M5:参数表单由 SECTIONS 自动生成

- 12 个分组、78 个字段全部从 `config_bridge.SECTIONS` 渲染,与桌面 Dash 读同一份规格 ——
  **给配置层加字段,手机上自动出现,Kotlin 零改动**。
- **踩到一个真陷阱并钉住**:Python 侧数值 kind 都接受字符串(所以表单可以一律按文本编辑),
  但 `bool("false")` 是 `True` —— 开关当文本发会**静默取反**。`toFormValues` 保持 bool 类型;
  宿主 `test_bool_fields_must_not_be_sent_as_text` + 设备
  `boolFieldsStayBooleanThroughTheValueMap` 两头验证。
- 校验去抖 300 ms(半个数字不算错);配置非法时 `derive` 只回 `valid` + `field_errors`,
  界面保留上一次派生量而不是清空。
- 新增 `FormContractTest`:schema 声明的 kind 必须都有控件、预设经表单值映射往返仍合法、
  改 symbol_rate 派生量真的变、非法值按 path 报错。
- **结果(run #11 全绿)**:`FormContractTest 5 / LinkFacadeTest 4 / PythonStackTest 5`,
  `instrumented totals: 14 tests, 0 failures`。上一轮修的分类归属这次输出正确。

## 2026-08-23 · M4:Compose 界面接上共用计算核

- 分层:`Compose UI → HaloApi(信封解析)→ HaloPython(单线程 dispatcher)→ api.call`。
  信封只拆一次;`suspend` 强制切到解释器线程(一个解释器一个 GIL,顺带防 registry 交错)。
- CI 回答了唯一的未知项:**Kotlin 编译通过**(Compose 插件版本跟随 `kotlinVersion`,
  Kotlin 2.0 起没有独立的 composeCompiler 旋钮)。APK 产物 74.4 → 80.6 MB,Compose 约 +6.2 MB。
- **第一版仪器化测试红了,原因是测试自己的 bug**:它取"第一个非 Library defaults 预设",
  而那是 touchstone 的 `nrz_16g_ms`,`.s4p` 有意没打进 APK → `run_stat` 失败。app 行为正确。
- 由此补了一个**产品侧**缺陷:`derive` 新增 `channel: {ok, message}`(`valid` 仍为 `true`),
  界面显示说明卡片并禁用 Run,启动时选**第一个能跑的**预设。契约由
  `unreachableChannelsAreFlaggedBeforeRunning`(设备)与两个宿主测试钉住。
- **最终结果(run #9)**:三个 job 全绿,`instrumented totals: 9 tests, 0 failures,
  0 errors, 0 skipped`(5 个 PythonStackTest + 4 个 LinkFacadeTest)。
- 路上踩了两个 CI 坑,都已沉淀进 `engineering-pitfalls.md`:
  (1) emulator action 的 `script` 走 `sh -c "<script>"`,内嵌双引号会把它截断 ——
  而我用 `sh -n` 检查语法通过,因此误判成"不是我的改动";
  (2) `connectedDebugAndroidTest` 跑零个测试也算成功,现已在脚本里按失败处理。

## 2026-08-23 · M0 判定:通过(run #3 三个 job 全绿)

- `assemble` / `wheel-versions` / `emulator` 全绿;仪器化测试
  `Starting 5 tests` → `Finished 5 tests`,零失败,含 `rtol=1e-9` 的 golden 比对。
  **Chaquopy 路线成立。**
- **`wheel-versions` 消掉了版本这一维**:numpy 1.26.2 + scipy 1.8.1(手机拿到的版本)
  下,BER 与现代版本只差 **1 ULP**(相对 1.6e-16),COM 与 post-FEC 逐位相同。
  pip 报的不兼容只在元数据层面。今后 golden 若失配,可干净归因给平台。
- **真机(aarch64)随后独立复现 `golden MATCH`**:`python 3.10.15 on aarch64`、
  numpy 1.26.2 / scipy 1.8.1、BER 1.3201e-06、COM 3.35 dB、164 ms。
  ARM 的 libm / FMA 合并 / OpenBLAS ARM 内核都没让 PDF 卷积在 1e-9 内漂开 ——
  这是 CI 最替代不了的一项。`skrf MISSING` 与 `numba absent: True` 均为预期。
- **两点保留**:164 ms 不可直接对比 CI 宿主的 79 ms(宿主确定冷启,真机是否首次按键
  未知,可能是热数据);16 KB page 只证明了**这台设备**可以,该机页大小无从判断。
- 上一条(下方)记的打包 bug 至此确认修复。判定表见 `android/README.md`。

## 2026-08-23 · M0 在真机跑通到了"presets 没打包"这一层

- **好消息(M0 的主要风险已排除)**:`assemble` job 绿 → Chaquopy 的 Py3.10 **有 SciPy
  wheel**;APK 74.4 MB(2 ABI,压缩后),远低于估的 ~160 MB;真机与模拟器上解释器启动、
  numpy/scipy 加载并算出 `erfc`、Kotlin 侧门面可调用(`jsonFacadeIsReachableFromKotlin` 过)。
- **失败点是打包不是算法**:`configs/*.yaml` 在仓库根、不在 Python 源码树,
  Chaquopy `srcDirs` 没带上 → `preset_names()` 退化 → `load_preset` 静默回退到
  `LinkConfig()`(默认 touchstone 无文件)→ 引擎抛 `channel.file is unset`。
  真机与 CI 模拟器给出同一个错误。
- 三处修复:Gradle `stageHaloAssets` 把 `configs/` 拷进 assets;`HaloPython.start()`
  解压并在 `Python.start()` **之前**导出 `HALO_SERDES_DATA_DIR`;`load_preset()` 改为
  **抛异常而非回退**。新增 `presetsSurvivedThePackaging`(仪器化)与
  `test_host_can_relocate_the_data_dir`(宿主)两道防线。
- **新发现待验**:Chaquopy 实际给的是 numpy 1.26.2 + **scipy 1.8.1**(pyproject 声明
  `scipy>=1.11`),pip 自报这对不兼容。新增 `wheel-versions` job 在宿主上降到这两个版本
  跑手机的计算路径 —— golden 比对现在跨"版本 + 平台"两个变量。
- 坑已沉淀进 `cairn/engineering-pitfalls.md` 新增的「打包 / 跨平台类」;
  经过与结构见 `android/README.md`。

## 2026-08-18 · Project Cairn 初始化(retrofit 进成熟项目)

- 在已有 62 个提交、297 项测试的项目上追加初始化 Cairn。
- 历史迁移模式:`inventory_only` —— 未改写任何历史文档,盘点见
  `cairn/knowledge-inventory.md`。
- **旧 `CLAUDE.md`(119 行)拆分迁移,内容零丢失**:铁律清单 → `AGENTS.md`;
  架构不变量与代码/文档约定 → `cairn/architecture-invariants.md`;
  踩过的坑与已知限制 → `cairn/engineering-pitfalls.md`;
  `CLAUDE.md` 按规范改为单行 `@AGENTS.md`。
- 决策:**不新建 `cairn/ROADMAP.md`** —— 根目录已有权威的 `ROADMAP.md`,
  再建一份会造成两个真相源;`AGENTS.md` 阅读顺序直接指向它。理由见盘点文档。
- 与模板的两处**有意偏离**(均已记录理由):不新建 `cairn/ROADMAP.md`(见上);
  `AGENTS.md` 预算从 ≤60 调为 ≤65 行,为 5 条项目铁律在必读文件里留位置 ——
  留一条文件自身违反的规则比调整预算更糟。
- 配置:`git_policy: track`(内容是技术结论,无敏感信息)、
  provider `none`(暂缓对接)、`language: zh`。详见 `.cairn/config.yaml`。
