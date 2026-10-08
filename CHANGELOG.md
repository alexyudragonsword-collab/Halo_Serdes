# 更新日志 / Changelog

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，按里程碑而非
逐条提交组织。项目尚未发布正式版本（`pyproject.toml` 中为 `0.0.1`），下列条目按
完成顺序排列。

---

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
