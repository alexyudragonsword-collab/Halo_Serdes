# Project Cairn 日志

本文件按倒序记录实质性进展 —— 最新条目在本行正下方。每条保持简短(摘要+指针),
结论沉淀进 `cairn/<topic>.md`。

## 2026-10-24 · 自由运行 bang-bang 环路的码型-相位相关进统计引擎(fix/bb-data-correlated-errors,ROADMAP P3 #13)

- 探针链(NRZ 16G 12 mV + rj 0.03,160 万符号):自由运行五种子存符号 / 相位 / 投票 / 错;冻结环路 +3…+8;抽头电平冻在自由运行终值的冻结相位;
  抽头冻在锁定点光标的冻结相位(= 统计持有曲线,10 % 内);抽头冻住的自由运行;15 / 18 mV 各三种子 + 锁定点抽头冻结相位。
- 结论:原假设成立但在更长的历史上 —— 4–6 符号类看不见(联合 / 独立 0.86–1.10×),按 (前串长 La, 串长 Lb, 下一符号) 分类才现形:La ≥ 6、Lb 2–3、
  下一符号反向的类相位尾 6–12 %(总体 1.2 %)、冻结相位 +6 处误码 9e-3 到 3e-2(总体 2.5e-4);联合 / 独立 2.01 / 1.40 / 1.15×(12 / 15 / 18 mV)。
  排除:抽头电平自适应(冻住 1.0e-5 对 8.3e-6)、噪声色(限带到 baud,半 UI 整数倍处零相关)、冻结环路参照(抽头在偏移相位重自适应,偏乐观 1.9×)。
- 模型 `_pattern_phase_factor`:2^12 历史 × 下一符号为类;相位 = 平稳分布按类的平均投票偏置平移(窗内各边的条件鉴相器均值减无条件均值,按 (1−θ)^m 衰减,
  再按平稳分布平均成常数 —— 逐相位移 p0(u − Δ(u)) 不是条件密度,读 9.4×);误码 = 条件化光标 + 锁定点持有 DFE + 引擎逐相位边沿抖动核 + 高斯余项。
  因子 2.15 / 1.49 / 1.17× 对实测 2.01 / 1.40 / 1.15×;各类平移对实测 0.1–0.2 个样本内;模型自己的绝对曲线比引擎持有曲线偏低 2–3.5×(锁定点吻合),只取比值。
- 数:NRZ 16G 12 / 15 / 18 mV 统计 / 时域 0.44 / 0.70 / 0.97× → 0.95 / 1.04 / 1.14×;预设 NRZ 16G 2.2e-8 → 6.4e-8、NRZ 28G 6.5e-28 → 7.2e-28;PAM4 / ADC 不变。
- 文档:ROADMAP #13 清空;CHANGELOG;`cairn/DSP发端与PR.md` §33(§32 余项指过去);`architecture-invariants.md` §3 补一句;`engineering-pitfalls.md` 四条;USAGE §9。
- 验证:本地全量 1236 passed / 8 skipped(25.9 分钟,+6);ruff;Cython(statistical);指纹 986 值 6 处动(NRZ 16G / 28G / 32G static 统计);
  示例 47 个重跑,24 / 44 / 45 接受(`derived` 104 处成立)。CI 行见下。
- CI(PR #102,头 ce5c9b3;push 与 pull_request 两条 test 工作流各跑一遍,全绿):测试矩阵 3.10 jit 17.9 / 23.2 分钟、3.11 jit 12.1 / 10.8、
  3.10 nojit 22.4 / 22.4、3.11 nojit 19.6 / 19.8(上限 45),examples 15.1 / 18.5、examples-full 30.4 分钟,lint、import-clean、RTL lockstep、vendor-drift 绿;
  Android(5d3d5a8,之后两个提交只动文档与示例)API 34 / 35 / 编译版 instrumented 各 34 tests、0 failures(3.7 / 5.0 / 4.1 分钟,工作流 13 分钟);
  pyinstaller 2.7 分钟绿,nuitka 与 nuitka-onefile 写到这里时已跑 105 分钟仍在进行(上限 180;此前缓存未命中 48 分钟),结论补在下。
- 坑:容器重建后 venv 没了(`uv venv` + `-e .[jit,fec,test]` 重建 1 分钟);探针脚本里用 `sys.argv[0].rsplit('/')` 取目录在无目录调用时取到文件名本身。

## 2026-10-22 · PAM6 / PAM8 的 R_LM 与 TDECQ(feat/pam6-pam8-rlm-tdecq,ROADMAP P3 #12)

- 口径(本项目约定,802.3 只定义 PAM4):R_LM 取 802.3 公式的等价形式 1 − max |ES_k / e_k − 1| 对每个内电平;TDECQ 保留 PAM4 程序的每个元素、
  让电平数穿过去(N − 1 门限在标称中点、半眼 OMA / (2(N−1))、Q_t 按占用 1.5 / 1.75 / 1.75 Q、目标 SER 4.8e-4 照用)。两处 docstring 与文档都写明"约定,不是标准量"。
- 踩到一个结构性的坑:PAM6 corner32 永不连发三个外电平((5,5) / (0,0) 对被排除,跨对的三连必含一个这样的对),802.3 按 7 / 6 连的 OMA 在 PAM6 上找不到串,
  第一版直接 NaN;改为没有 ≥ 3 串时取两符号串的中间 1 UI,`oma_runs` 报 (2, 2)。带限眼上这比 PAM4 的读法低约 0.2 dB OMA,文档注明。
- 数:理想眼 PAM4 / PAM6 / PAM8 +0.001 / +0.001 / +0.024 dB;0.6σ 高斯噪声对闭式 0.969 dB 读 0.91 / 1.01 / 1.02;`tx.rlm` 0.9 三格式都读回 0.900。
  示例 34 三格式表 VCSEL 100G (SR1) 4.59 / 14.85 / 21.49 dB、EML 200G (DR1) 1.88 / 3.85 / 12.49 dB;LPO 预设 53 GBd 2.95 / 5.92 / 17.35 dB。
- 验证:PAM4 逐位不变 —— 指纹 986 值 bit-identical(两次,含 OMA 回退改动后),示例 34 旧行一字不动、只多 9 行,`derived` 104 处成立;
  受影响测试 270 + 新 13 绿,本地全量 1237 passed / 1 skipped(22 分钟,+13);ruff;Cython(tdecq / static_curve / mapping / tx_metrics / eo)。CI 行见下。
- 文档:ROADMAP #12 清空;CHANGELOG;`cairn/448G调制格式.md` §6;USAGE §1。
- CI(PR #101,头 8706e28;两条 test 工作流各跑一遍,全绿):测试矩阵 jit 11.0–22.4 分钟、3.10 nojit 11.9 / 20.6 分钟(needs_jit 之后第二轮,上限 45)、
  3.11 nojit 14.3 / 15.9 分钟,examples 10 / 11、examples-full 19 分钟,lint、import-clean、RTL lockstep、vendor-drift 绿;Android API 34 / 35 / 编译版 instrumented
  各 34 tests、0 failures(5 / 4 / 5 分钟);pyinstaller 3、nuitka 48(缓存未命中)、nuitka-onefile 41 分钟绿。

## 2026-10-22 · nojit CI 腿的预算:引擎级交叉校验标 needs_jit(ci/nojit-budget)

- 3.10 nojit 腿连续三个 PR 41–42 / 45 分钟。逐文件 `HALO_NO_JIT=1 --durations=0` 量(本地 3.11,75 个文件 2644 s):前 25 个用例占一半,
  几乎全是引擎级的统计 / 时域交叉校验(`test_tx_noise` 四格 287 s、`test_crosstalk` 漂移 aggressor 96 s、`test_ffe_tracks_wander` 161 s、
  `test_skew` 62 s、`test_profile_run_smear` 80 s、`test_clock_profile_cdr` 99 s、`test_pr` 110 s、`test_stream` 117 s、`test_optical` 138 s、`test_mlsd_precode_wiring` 66 s)
  —— 时域内核两条路径逐位相同、内核测试直接查,慢路径从它们学不到东西(`test_mlsd_reduced` 早就这么标)。
- 做法:`tests/conftest.py` 共享 `needs_jit` 标记 + `HALO_NO_JIT=1` 自动跳过,`pyproject` 登记;已有本地 skipif 的文件沿用。标 25 个标记用例 + 4 处本地 skipif。
  nojit 全量 2644 → 1292 s(1123 passed / 102 skipped),十个文件 1583 → 296 s;numba 下收集 1225 不变、标过的用例照跑。内核路径的重用例
  (`test_chunked_kernels[adc_dsp_pr4]` 62 s、`test_fixed_loop[pr4]` 53 s、`test_rtl_lockstep`)有意保留。工作流 45 分钟上限不动,注释记预算。
- 坑:整套 `--durations` 一次跑到底,容器重启(两次,各 ~1 小时一次)就全丢;改成逐文件各写一个 log 的循环,重启后续跑。本地 numba 全量两次被容器重启打断(47 %),以 CI 的四条 jit 腿为准。
- CI(PR #100,头 609902a;两条 test 工作流各跑一遍,全绿):**3.10 nojit 21.2 / 20.9 分钟(此前 42 / 42)**、3.11 nojit 18.5 / 18.3(此前 21–37),
  jit 腿 16.6–21.6 分钟,examples 15 / 18、examples-full 29 分钟,lint、import-clean、RTL lockstep、vendor-drift 绿;pyinstaller 2、nuitka 28、nuitka-onefile 52 分钟绿。
  Android 工作流按路径过滤(android / src / configs / data)没有触发 —— 这个 PR 只动 tests / pyproject / test.yml,共用层没动。

## 2026-10-21 · bang-bang 采样相位的平稳分布(fix/nrz-bb-pattern-phase,ROADMAP P2 #32)

- 登记的"相位与码型相关"拆成两项。第一项不是码型(按前串长分箱相位均值只动 0.001–0.008 UI),是分布形状:内核的采样相位到 2σ 是高斯,
  往外是指数尾且晚侧更重(+0.23 UI 处 3–7× 同 σ 高斯,早侧与高斯同);根源是鉴相器饱和,按内核读法(原始样本符号比较)建的均值曲线 M(u)
  与内核分箱实测到 ±5 个样本差 0.01–0.03、斜率 = 线性模型 k_pd 的 0.97×。统计引擎改用势 −∫M 的 Boltzmann 分布平滑,RMS 对齐线性模型 σ
  (`bb_stationary_density`;按曲率归一会把描述函数增益里的饱和算两遍,没取)。NRZ 16G 三点 0.84 / 0.64 / 0.25× → 0.91 / 0.78 / 0.42×,
  PAM4 32G 1.02 → 1.04–1.06×;预设深 BER 大动(NRZ 16G 2.9e-10 → 2.2e-8、PAM4 32G 1.4e-10 → 4.1e-9),是指数尾外推,内核实测到 10.5 个样本处
  分布仍偏轻 2.7×。第二项才是数据相关(12 mV 处同相位误码率 2.1× 持有曲线、3.8× 冻结环路,五种子 66 个错),登记 P3 #13。
- 顺带:kp_shift 8 失锁的报警 —— 线性化阻尼比 ζ = kp√(k_pd/ki)/2(`loop_damping`),7 时 0.34 锁住、8 时 0.17 滑周,< 0.25 报警。
- 探针法:先量"经验相位直方图 × 持有曲线"把分布与曲线分开(18 / 15 / 12 mV 1.13 / 1.18 / 1.86× 统计),再按相位分箱量鉴相器输出得实测 M(u),
  再拿 Boltzmann 分布对尾质量;PAM4 分箱的 M 比静态曲线陡(±3 个样本处 ±0.42),是数据定输出的滞后 1 反相关混进了条件均值,不能当参照。
  缓存仿真到 npz 再迭代模型,五个条件一轮 5 分钟。坑:`pgrep -f 探针名` 会杀掉自己的 shell(exit 144),两次。
- 验证:新测试 10 项 + 三处 monkeypatch 改接;受影响模块 141 passed;ruff;Cython(linear / statistical);指纹 986 里动 8 处(4 个预设统计 BER / SER)。
  示例全跑:24 / 44 / 45 的统计行动(24 的 NRZ 28G 深尾抬 5 个量级、44 的 PAM4 32G 同步 0.95–1.03 → 0.97–1.04、45 的 minmax 1.83 → 2.35 / 10.05 → 38.22),
  其余 44 个不变,`derived` 104 处成立;远尾 400 万符号 × 2 种子(四个并行被 OOM 杀,每个 4.7 GB —— 一次只跑一个)。
  本地全量 1224 passed / 1 skipped(21 分钟,+10);新测试 `HALO_NO_JIT=1` 也绿。
- 文档:ROADMAP #32 清空、P3 #13 登记;CHANGELOG;`cairn/DSP发端与PR.md` §32 + §31 余项更正;invariants / pitfalls(高斯近似那条更正);USAGE。
- CI(PR #99,头 c641b29;两条 test 工作流各跑一遍,全绿):测试矩阵 jit 10.6–20.7 分钟、3.10 nojit 42.2 / 42.4 分钟(第三次贴着 45 分钟上限,差 3 分钟)、
  3.11 nojit 20.9 / 36.6 分钟,examples 14 / 18、examples-full 31 分钟,lint、import-clean、RTL lockstep、vendor-drift 绿;Android API 34 / 35 / 编译版 instrumented
  各 34 tests、0 failures(5 / 4 / 5 分钟);pyinstaller 3、nuitka 31、nuitka-onefile 35 分钟绿。

## 2026-10-21 · 混合信号判决电平自适应 dLev(fix/ms-pam4-level-adapt,ROADMAP P2 #31)

- 定了:加 dLev(`DfeConfig.level_adapt`,默认 `sign_sign`),不再把"电平定在脉冲峰值"的悲观写进文档了事。内核按判决误差的符号逐批
  (n_ave)更新判决电平,步长 mu × |主光标|,只在 DFE 自适应时跑,交叉即撤销;统计引擎门限放回锁定点主光标。
- 探针(main 的 worktree 跑旧、本分支跑新;PAM4 32G 预设,4–8 种子 × 50 万):电平 0.1853 → 0.1837 V(锁定点在峰值后 2 个样本,幅度低 0.9 %);
  时域 BER 噪声 0.005 + rj 0.03 时 0.79×、0.004 + rj 0.06 时 0.88×、0.008 / 0.012 时 0.94 / 0.99×;统计同向 0.71 / 0.84 / 0.92 / 0.98×,
  统计 / 时域 0.79–1.13×;预设统计 BER 3.3e-10 → 1.4e-10。越深差越大,与登记时的 1.3–1.8× 一致。NRZ 预设电平动 0.1–0.7 %,BER 不变。
  预设噪声 + rj 0.03 那一点 400 万符号只有 3 个错,新旧逐个相同 —— 深点要拿错数说话,不能拿它比。
- 验证:本地全量 1212 passed + 2 红(都是把 PAM4 电平钉在峰值的旧断言:`test_skew` 的 phase_ui 读数、`test_ffe_tracks_wander` 的冻结参照,改后绿,
  计 1214 / 1 skipped,+3);`HALO_NO_JIT=1` 内核 / 分块 / 配置测试绿;ruff;Cython(kernels / timedomain / statistical / schema);指纹 980 里 33 处动(混合信号
  预设的时域行与 PAM4 32G 统计 BER);示例全跑一遍 02 / 07 / 09 / 10 / 13 / 44 末位动并 accept,其余 41 个不变,`derived` 104 处成立。
  坑:4 路并行示例 + 全量测试 + 指纹同时跑,示例 43(400 万符号)被杀报 FAIL,单跑 682 s 逐行同期望 —— 并行跑挂了先单跑再下结论。
- 文档:ROADMAP #31 清空;CHANGELOG;`cairn/DSP发端与PR.md` §30 余项;invariants / pitfalls 的 P2 #31 句;USAGE。
- CI(PR #98,头 bd886b5;两条 test 工作流各跑一遍):测试矩阵 jit 16–21 分钟、3.10 nojit 42 / 42 分钟(两次都离 45 分钟上限只差 3 分钟 —— 该动手了:
  把最重的 nojit 用例标 needs_jit 或提上限)、3.11 nojit 22 / 37 分钟,examples 9 / 14、examples-full 21 分钟,lint、import-clean、RTL lockstep、vendor-drift 绿;
  Android API 34 / 35 / 编译版 instrumented 各 34 tests、0 failures(4 / 3 / 4 分钟);pyinstaller 3、nuitka 32(缓存命中)、nuitka-onefile 129 分钟绿。

## 2026-10-17 · pam6 / pam8 的混合信号包络标定(fix/pam6-pam8-ms-envelope,ROADMAP P2 #34)

- 探针(示例 12 的判据:Whisper 背板、CTLE 四档择优 + 4 抽头 unrolled DFE、判决后最窄子眼 > 0,PAM6 / PAM8 以 1 GBd 步长):最后开眼 PAM6 13 GBd
  (32.5 Gb/s,12.4 mV,−16.1 dB)、PAM8 11 GBd(33 Gb/s,1.5 mV,−14.5 dB),对 PAM4 −20.4 dB 差 4.3 / 5.9 dB(电平代价 4.4 / 7.4);眼高 / 电平间距
  PAM6 12 GBd 0.28(= PAM4 16 GBd 的 0.28)、PAM8 8 GBd 0.42、9 GBd 0.24。0.2 m 解析走线 PAM6 19 GBd、PAM8 14 GBd。
- 定档:舒适 PAM6 30 / PAM8 24 Gb/s,极限 32.5 / 33 Gb/s,硬顶 30 GBd 不变;`ms_envelope()` 四格式都有,"未核实"警告不再出现(None 分支留给未知格式)。
- 判据修正:PAM6 在 Whisper 14 GBd 闭眼后 15 GBd 又"开" 0.1 mV(4 万样本最小值的涨落),"最后一个开眼点"会错判到 37.5 Gb/s;示例 12 的极限
  改为"第一次闭眼之前的最后一个开眼点",NRZ / PAM4 不变。登记 #34 时猜"包络大概率远低于 PAM4":按波特 13 / 11 对 18 GBd 成立,按比特率只低 3–4 Gb/s。
- 改:schema 两张表 + 注释;timedomain / config_bridge 的 None 分支措辞;示例 12 加 PAM6 / PAM8 两条曲线(1 GBd 步长)并 accept,`derived.yaml` 登记
  pam6 / pam8 的闭眼损耗(README 引用);test_ms_envelope 的 pam6 / pam8 用例改为三档断言(+1 参数化),formats 的两处 `pytest.warns` 改为 "exceeds"。
- 验证:本地全量 1211 passed / 1 skipped(+1;第一遍 1 红是 `test_mapping_formats` 里断言 pam6 / pam8 为 None 的旧用例,改为四格式断言后绿);ruff;
  Cython(schema / timedomain);指纹 980 个值 bit-identical;示例 12 全尺寸重跑两次后 accept,`derived` 104 处引用成立;示例 00–11 / 13–46 未动。
- 文档:ROADMAP #34 清空;CHANGELOG;README 包络表加 PAM6 / PAM8 列;USAGE §1;SUMMARY §04 / 448G 段;COMPARISON;`cairn/448G调制格式.md` §6 / §7。
- CI(PR #97,头 5e535ea;两条 test 工作流各跑一遍):测试矩阵 jit 13–21 分钟、3.10 nojit 32 / 41 分钟(又一次离 45 分钟上限 4 分钟)、3.11 nojit 36 / 36 分钟,
  examples 10 / 18、examples-full 28 分钟,lint、import-clean、RTL lockstep、vendor-drift 绿;Android API 34 / 35 / 编译版 instrumented 各 34 tests、0 failures(5 / 4 / 4 分钟);
  pyinstaller 3、nuitka 70、nuitka-onefile 124 分钟绿(合并时 onefile 仍在跑)。

## 2026-10-17 · PAM6 串扰 aggressor 发 corner32 对流(fix/pam6-xtalk-occupancy,ROADMAP P3 #12 的一条)

- 探针(main 的 worktree 跑旧、本分支跑新):aggressor 从均匀 6 电平改成 corner32 对流后两引擎 SER 同降 0.78–0.93×(二阶矩 0.4 / 0.467),
  统计 / 时域几乎不动(混合信号 1.03–1.08×、ADC 1.03–1.09×、准同步 0.95–0.97×);旧统计对真实对流读高 1.17–1.27×。ICN ×0.926(−0.67 dB);
  PAM6 COM 无串扰 +0.04 / +0.26 dB、带 bank +0.29 / +0.54 dB。第一轮探针耦合取太强(SER 0.2–0.5)看不出工作区间,重取 −56…−36 dB。
- 改:`XtalkAggressor.symbol_volts` pam6 走 `bits_to_symbols`;统计引擎五处 aggressor `isi_pdf` 带 `level_probs`;`core.mapping.symbol_variance`
  进 `icn_rms` 与 COM(σ_XT、σ_ISI、A_ni PDF)。仍近似:aggressor 相邻光标的对相关(统计按独立占用)。
- 验证:本地全量 1210 passed / 1 skipped(+5,26.6 分钟);`HALO_NO_JIT=1` 下新测试绿(混合信号串扰铁律 3 一条 22.5 s);ruff;Cython(mapping / crosstalk /
  statistical / com);指纹 980 个值 bit-identical;示例 24 / 26 / 28 / 44 / 46 零漂移。
- 文档:ROADMAP P3 #12 串扰那条改为已做;CHANGELOG;`cairn/448G调制格式.md` §2 / §7;`cairn/多lane建模.md` §7;USAGE §7 一句。
- CI(PR #96,头 88bb14c;两条 test 工作流各跑一遍):测试矩阵 jit 13–20 分钟、3.10 nojit 21 / 41 分钟(一次离 45 分钟上限只差 4 分钟,运行器负载;新加的混合信号串扰测试 nojit 22 s)、
  3.11 nojit 28 / 33 分钟,examples 9 / 17、examples-full 28 分钟,lint、import-clean、RTL lockstep、vendor-drift 绿;Android API 34 / 35 / 编译版 instrumented 各 34 tests、0 failures(4 / 5 / 5 分钟);
  pyinstaller 3、nuitka-onefile 74 分钟绿,nuitka 合并时仍在跑(≥ 106 分钟,未等它)。

## 2026-10-16 · PAM6 PR 路径的比特也按对(fix/pam6-pr-pair-bits,ROADMAP P3 #12 的一条)

- 探针:ADC 1 + aD + Viterbi 格时域每个符号错 1.02 比特、统计 1.19(B6);不是搭档条件(该路径 c(+1) 由检测器分辨,搭档几乎不推门限),
  是多符号事件:交替的两符号事件落在一对里是字的对角移动,corner32 下 1.7–2.4 比特,不是两个单步的 2.4–2.9。
- 改:`pair_event_bits`(事件按触碰到的对算字距,起步位置各半,其余符号按允许移动的发送字取)进事件路径,每错比特 = 事件比特 / 事件长度
  (w_ev 已含错数);判决链(`pr_symbol_decisions`)在第二位置用状态里前一个错与自己的错查 `pair_word_bits`,每符号半对。
- 数:ADC 1 + aD + Viterbi BER 比 1.20 → 1.05×(SER 1.03,每错比特 1.19 → 1.04 对时域 1.02);逐符号判决链(ADC 1 + aD,无序列检测)0.20 / 0.24 m 的每错比特 1.19 → 0.99 对时域 0.99 / 1.00,BER 比 1.42 / 1.22 → 1.18 / 1.02×(SER 1.19 / 1.03);示例 46 的 PAM6 PR 格动了:1 + aD K = 1 统计 reach 18.84 → 18.90 dB,1 + aD + bD² K = 2 19.54 → 19.64 dB(时域 19.65,统计 / 时域 1.01–1.41 → 0.88–1.25×),PR + DFSE 相对 delta 的收益 +2.97 → +3.07 dB,KP4+BCH 统计 reach 19.43 → 19.62 dB,PAM6 / PAM8 浴盆交叉 21.6 → 23.8 dB;PAM4 / PAM8 与 PAM6 的 delta 格不动。
- 验证:本地全量 1205 passed / 1 skipped(+1);`HALO_NO_JIT=1` 下四个相关测试文件 116 passed / 40 skipped;ruff;Cython(statistical / mapping);
  指纹:合成 PAM6 / PAM8 链路 2 mV 时统计 BER 下溢为 0、对本条与 P2 #33 都不敏感,噪声提到 10 mV 后 30 个合成行的值动,预设 860 个 bit-identical;
  示例 46 两次全尺寸运行逐行相同后 accept,`derived` 12 个数 102 处成立。
- 文档:ROADMAP P3 #12 那条改为已做(余下的近似写明);`cairn/448G调制格式.md` §2 / §3;CHANGELOG。
- CI(PR #95,头 5e4d6a2):测试矩阵 jit 12–19 分钟、3.10 nojit 23、3.11 nojit 37 分钟,examples 18、examples-full 30 分钟,lint、import-clean、
  RTL lockstep、vendor-drift 全绿;Android(00be298)API 34 / 35 / 编译版 instrumented 各 34 tests、0 failures(4 / 5 / 5 分钟);
  pyinstaller 2、nuitka 117、nuitka-onefile 123 分钟绿。

## 2026-10-16 · PAM6 比特按对:搭档已知时的代价(fix/pam6-pair-ber,ROADMAP P2 #33)

- 探针:四个混合信号格的时域判决流按对解码,每个符号错实际 1.01–1.06 比特、B6 预期 1.17–1.34(ratio 1.33 / 1.22 / 1.10 / 1.07),
  统计 BER/SER 的倍数正好是 B6 的期望;两个符号同时错 0 次。登记时的"两符号误码相关性"不是主因 —— 是出错事件的搭档分布偏向代价低的那些。
- 改:`core.mapping.pair_bit_distance`(位置 × 发 × 搭档 × 判 的精确比特);统计引擎切片路径按(位置、电平、搭档)分解,搭档电平是门限的平移,
  `_pair_isi_pdfs` 另给每个位置"除搭档外"的 PDF;第一位置的搭档在后、走预光标 c(−1),第二位置的在前、走 c(+1)(#35 的 per-level 混合对此对称,
  比特不对称 —— 验证靠统计 bits/SER 1.02 / 1.05 / 1.06 / 1.12 对时域 1.01 / 1.04 / 1.06 / 1.12)。
- 数:混合信号无 DFE BER 比 1.37 / 1.35 / 1.30 → 1.05 / 1.13 / 1.18×,带 DFE 0.61 → 0.58×,ADC delta 1.03×;ADC 1 + aD 1.20×(PR 路径仍 B6,P3 #12)。
  示例 46 只动 PAM6 delta 格(ADC,FFE 把 ±1 光标压平)的末位:11 抽头 delta reach 16.40 → 16.39 dB、DAC 表 16.55 / 16.45 / 16.02 → 16.54 / 16.46 / 16.03(6 bit 的代价 0.12 → 0.11 dB),PR 格一位不动。
- 验证:本地全量 1204 passed / 1 skipped(+2);`HALO_NO_JIT=1` 下三个格式 / 统计测试文件 69 passed / 8 skipped;ruff;Cython(statistical / mapping);
  指纹 980 个值 bit-identical(合成 PAM6 链路走 PR 路径);示例 46 两次全尺寸运行逐行相同后 accept,`derived` 12 个数 102 处成立。
- 文档:ROADMAP #33 清空、P3 #12 加 PR 路径一条;`cairn/448G调制格式.md` §2 / §3 / §7;pitfalls 一条;CHANGELOG。
- CI(PR #94,头 76c3726):测试矩阵 jit 10–12 分钟、3.11 nojit 37、3.10 nojit 41 分钟(上限 45,余量仍薄),examples 18、examples-full 28 分钟,
  lint、import-clean、RTL lockstep、vendor-drift(pll_simulator 公开后 7 秒)全绿;Android(5c82d24)API 34 / 35 / 编译版 instrumented 各 34 tests、0 failures
  (4 / 4 / 5 分钟);pyinstaller 3、nuitka 118、nuitka-onefile 127 分钟绿。

## 2026-10-16 · 串扰 / 漂移 PDF 的卷积按中心对齐(fix/xtalk-convolve-centred,ROADMAP P2 #36)

- 探针:平信道 + AWGN 上给耦合为零(−120 dB)的 aggressor,统计 SER 每个多 0.2–0.6 %,8 个多 8 %(NRZ)/ 22 %(PAM4);准同步漂移路径同;
  COM 8 个少 0.25 dB。都是 `np.convolve(mode="same")` 在偶数网格上每卷一次晚一格(P2 #35 顺带发现时的猜测"百分之几"偏小:深尾更大)。
- 改:`statistical.py` 四处 + `analysis/com.py` 一处换 `_convolve_centred`;高斯核是奇数长度,不动。改后耦合为零的 aggressor 逐位等于没有。
- 数:示例 24 统计 BER 6.41e-32 … 3.90e-3 → 5.42e-32 … 3.89e-3(−18 dB 处两引擎 0.71×);示例 28 COM 各高 0.03–0.06 dB、示例 26 一点 −1.96 → −1.94 dB(本地漏跑,CI examples-full 抓到);示例 44 统计 / 时域 同步 24 次 0.44–1.15× → 0.44–1.11×、准同步 0.72–0.95× → 0.70–0.93×(统计读数低 1–4 %,时域不变);
  指纹 980 个值 0 变(预设无串扰)。
- 验证:本地全量 1202 passed / 1 skipped(+3);`HALO_NO_JIT=1` 下 crosstalk / statistical / com 三个文件 75 passed;ruff;Cython(statistical / com);
  指纹 980 个值 bit-identical;示例 24 / 28 / 44 的期望重 accept(45 不变),`derived` 12 个数 102 处成立。
- 文档:ROADMAP #36 清空;`cairn/多lane建模.md` §3 加注;pitfalls 那条补判别法(耦合为零的 aggressor 必须逐位等于没有);CHANGELOG。
- CI(PR #93,头 6f6484f):测试矩阵两套 jit 11–22 分钟、3.11 nojit 20–31、3.10 nojit 24–42 分钟,examples 10–18、examples-full 23 分钟(85dd594 上抓到
  本地漏跑的示例 26),lint、import-clean、RTL lockstep 绿;Android(a8d0842)API 34 / 35 / 编译版 instrumented 各 34 tests、0 failures(5 / 4 / 5 分钟);
  pyinstaller 3、nuitka 83、nuitka-onefile 130 分钟绿。`vendor-drift` 红过三轮:兄弟仓库 pll_simulator 当天转私有,默认 GITHUB_TOKEN 拉不到
  ("Repository not found");workflow 改为优先用 secret `PLL_SIMULATOR_TOKEN`、无则退回默认 token,用户随后把仓库改回公开,re-run 绿。

## 2026-10-16 · PAM6 成对符号的 ISI 相关性(fix/pam6-ms-lock,ROADMAP P2 #35)

- 先按登记的猜测查锁定相位:两引擎都锁在 +0.209 UI,时域冻结在那里读 2.4e-4、自由运行 1.9e-4 —— 不是环路。给时域喂独立同分布符号
  (占用同 corner32):SER 1.34e-3 / 2.97e-3 / 2.13e-3(0.10 / 0.15 / 0.12 m),与统计平滑读数 1.33e-3 / 3.00e-3 / 2.19e-3 几乎相同;
  corner32 本身把 SER 压到 4.7e-4 / 1.88e-3 / 1.14e-3。一步把"引擎差"和"数据差"分开。
- 改:`core.mapping.neighbour_conditional`(外层的搭档在内层四个里均匀、内层的六个均匀);统计引擎逐电平建 ±1 光标的 ISI(`_pair_isi_pdfs`),
  卷积按网格中心对齐(`np.convolve(mode="same")` 偶数网格偏一格,平信道闭式差 4–7 %)。三格 SER 1.04–1.19×、BER 1.30–1.37×;DFE 格 0.63、
  ADC 0.96–1.20 不动;指纹只有合成 PAM6 的 2 个 1e-16 量级的值动。
- 余项:B6 按符号的比特矩阵(P2 #33,BER 比 SER 多 1.1–1.3×);串扰 PDF 的 `same` 卷积同样偏一格(新登记 P2 #36);带 DFE 的 PAM6 格 0.63× 未查。
- 文档:ROADMAP #35 清空(登记时的猜测标为推翻)、`cairn/448G调制格式.md` §2 / §3 / §7、pitfalls 两条、CHANGELOG。
- 验证:本地全量 1198 passed / 1 skipped(+3);`HALO_NO_JIT=1` 下两个格式测试文件 36 passed / 8 skipped(交叉格 `needs_jit`);ruff、Cython
  (mapping / statistical);指纹 980 个值只有合成 PAM6 的 2 个 1e-16 量级的数动;示例 00–46 不变(本地一次用未对齐的中间版本跑出示例 46 的 0.01–0.03 dB 假漂移并 accept 进库,CI examples-full 抓回,
  用当前代码重跑与旧期望逐行相同;教训:accept 前确认跑的是提交的代码),`derived` 12 个数 102 处成立。
- CI(PR #92,头 f0e69e5):测试矩阵两套各跑一遍,jit 11–20 分钟、3.11 nojit 20、3.10 nojit 29 / 42 分钟(第二条离 45 分钟上限只剩 3 分钟,
  nojit 腿的余量已经很薄)、examples 14–18、examples-full 23 分钟(抓回示例 46 的假漂移后 00–46 零漂移)、RTL lockstep、lint、import-clean、
  vendor-drift 全绿;Android(ba521ad,此后 src 未变)API 34 / 35 / 编译版 instrumented 各 34 tests、0 failures(4 / 4 / 5 分钟,APK 2、编译版 8 分钟);
  pyinstaller 3、nuitka 104、nuitka-onefile 129 分钟绿。

## 2026-10-15 · 448G/lane 调制格式比较:PAM6 / PAM8 进字母表,示例 46(feat/448g-formats)

- 用户 prompt 要的 v4 文档 #20 + #37:仓库没有 #37,现有 #20 是已清空的 PAM4 0↔3 条目 —— 不删,新登记余项;P2 #31 / #32 按用户要求暂停。
- 泛化:`modulation ∈ {nrz, pam4, pam6, pam8}`,字母表只在 `core/mapping.py`(PAM6 corner32 对映射贪心表、PAM8 Gray),`n_levels` / `bits_per_symbol`
  替掉 9 处 `2 ** bits_per_symbol`;时域按对精确计数,统计引擎带电平占用(1/8 / 3/16)与比特距离矩阵。NRZ / PAM4 指纹 860 值 bit-identical。
- 铁律 3:ADC PAM6 / PAM8 0.96–1.20×;混合信号 PAM6 带 DFE 0.55–0.67、无 DFE 1.45×(更短链路 2.5 / 3.9× → P2 #35),PAM8 要 CTLE,0.53–1.07×。
- 示例 46(394 s):三格式都选 1 + aD + bD² K = 2,KP4 时域 reach PAM4 20.44 / PAM6 19.65 / PAM8 19.38 dB,PAM4 赢 0.79 dB、省 0.70 pJ/b,3 种子一致;
  delta 收端 PAM8 领先,PAM4 靠 PR + DFSE +4.25 dB 翻盘;DAC 6 / 7 / 7 bit;KP4+BCH 下 PAM4 统计 reach 时域未确认(判决导向环路失锁,P2 #15 余项的又一例)。
- 踩的坑:包络警告分支里的 `return` 让 `run_time_link` 返回 None(测试只看警告);统计引擎逐图样分箱 N³,PAM8 带 PR 单跑 56 s → 示例里 rj 设 0。
- 文档:新专题 `cairn/448G调制格式.md`;USAGE §1 / §10;SUMMARY §04 小节;ROADMAP P2 #33–#35、P3 #12;pitfalls 两条;invariants;inventory;指纹加两条合成链路(860 → 980 个值,原 860 个 bit-identical)。
- 验证:本地全量 1195 passed / 1 skipped(+59;`HALO_NO_JIT=1` 下三个新文件 47 项通过;铁律 3 的 8 个交叉格随后标 `needs_jit` —— CI 的 3.10 nojit 腿带着它们超过 45 分钟上限被取消,3.11 nojit 33–41 分钟);ruff、Cython 13 个模块;`run_examples.py --smoke` 46 / 46;示例 00–45 全尺寸零漂移(47 / 47 跑通,`derived` 全部成立;46 的期望输出 accept 入库,全量 364 s)。
- CI(PR #91,头 9cc3970):测试矩阵 jit 16–20 分钟、3.11 nojit 28、3.10 nojit 35–43 分钟(上限 45:交叉格标 `needs_jit` 之前 3.10 nojit 被取消,
  `test.yml` 的注里就是这条老账)、examples 17、examples-full 28 分钟(00–46 在 CI 上零漂移)、RTL lockstep、lint、import-clean、vendor-drift 全绿;
  Android(c338693,此后 src 未变)API 34 / 35 / 编译版 instrumented 各 34 tests、0 failures,APK 与编译版 APK 构建绿;pyinstaller 3、nuitka 119、
  nuitka-onefile 129 分钟绿。两次返工:`from tests.` 导入让裸 `pytest` 收集失败(pitfalls)、示例 46 的 `[Ns total]` 没被漂移工具掩掉。

## 2026-10-15 · PAM4 猎振的数据噪声;门限放在接收机设电平处(fix/smear-distribution,ROADMAP P2 #30)

- 拆开量(PAM4 32G / NRZ 16G / NRZ 28G 白噪钟,8–16 种子):实测相位分布换进曲线只差 0–15 %,冻结时域对照曲线对得上 —— 登记时猜的"高斯尾部"不对。
  ① PAM4 猎振模型 σ 读高 1.11–1.21×:偏离锁定点跳变的检测器输出由数据定、相邻跳变相消,却按白噪声全功率算。② 时域混合信号的 PAM4 电平取在脉冲
  峰值(锁定点前 2 个样本),统计引擎门限放在锁定点,读低 0.70–0.95×。
- 改:`cdr.linear.bb_data_noise`(随机数据低频密度 / 码型谱线)、`bb_loop_fixed_point(mean_output=, period=)`、`_bb_pairs` / `_bb_mean_output`;平滑时门限放在
  接收机设电平的相位(`i_level`);无 DFE 的 PAM4 门限保持补上。PAM4 猎振 0.97–1.01;PAM4 32G 深 BER 0.94–1.15×,≥ 100 个错 1.04–1.12×,交叉校验 1.01–1.04×。
- 余项 → P2 #31(时域 PAM4 电平不自适应,待定)、P2 #32(NRZ 16G 自由运行时相位与码型相关、读低 0.5–0.7×;kp_shift 8 失锁不报警)。
- 示例 44 / 45 的 PAM4 列变(45 KP4 窗口 0.321 → 0.363 UI、minmax / 单 lane 2.94 → 1.83;skew 对游走 lane 0.71–0.99×),其余 44 个不变;指纹 4 个值变
  (都在 PAM4 32G 预设,BER 2.2e-9 → 3.3e-10),NRZ / ADC 逐位不变。
- 文档:`cairn/DSP发端与PR.md` §31(§28 加更正)、pitfalls 三条、invariants、`多lane建模.md` §7 / §8、clock_profile、USAGE、COMPARISON。
- 验证:本地全量 1136 passed / 1 skipped(+4 `tests/test_bb_data_noise.py`);`HALO_NO_JIT=1` 下新文件 4 条通过;ruff、Cython(linear / statistical)通过;
  `derived` 96 条成立。
- CI(4ee8edb):Android API 34 / 35 / 编译版 instrumented 各 34 tests、0 failures;测试矩阵(含 nojit)、examples、full、RTL lockstep、lint、import-clean、
  vendor-drift、pyinstaller、APK 构建绿,nuitka / nuitka-onefile 绿(80 / 112 分钟)。
- 上一条(#89)于 2026-10-15 合入。合入时其 CI(3dd8d37)Android API 34 / 35 / 编译版 instrumented 各 34 tests、0 failures;之后测试矩阵(含 nojit)、examples、
  full、RTL lockstep、lint、import-clean、vendor-drift、pyinstaller、APK 构建全绿,nuitka / nuitka-onefile 绿(120 / 87 分钟)。

## 2026-10-15 · 判决门限跟抽头一起保持;LMS FFE 进环路模型(fix/ffe-tracks-wander,ROADMAP P2 #29)

- 用最终代码重量,原 #29 是两件事。① PAM4 判决门限按每相位主光标重放(时域电平定下后不动):冻结时域停在锁定点外,重放 0.42–0.79×、保持
  0.95–1.19×(示例 31 与 PAM4 32G 预设)。② LMS FFE 沿定时导数以 γ = n_lanes·mu·λ' 跟走偏移(示例 31 mu 1e-3 2.4e-5,时域拟合 3e-5)。
  更正:登记时"冻结均衡器 0.63–0.90×、曲线本身 0.6–0.8×"是中间代码的数、方向不对(见 ROADMAP 清空注)。
- 改:`fixed` 相位门限取 `lock_main`;`LoopParams.ffe_track` / `ffe_in_loop`(读 FFE 输出并联、读原始样本高通),`_polys` / `_on_circle`
  (N − D 分开构造、单位圆上按 z − 1 算);`statistical._ffe_lms_track`;GUI 抖动面板 |E| 带 FFE。`ffe_track` = 0 时逐位不变。
- 结果:示例 31 kp 4–9、两种剖面、mu 0 / 1e-3 / 1e-2 对 12 种子 0.68–1.61×(mu 1e-2 kp9 此前 ~12×),σ 0.93–1.01;示例 31 自身配置 16 种子
  kp 8–9 0.89–1.07×(此前 0.69–0.85×)。白时钟 PAM4 32G 1.03–1.13×(此前 0.99–1.01×)。
- 余项 → ROADMAP P2 #30:PAM4 32G 猎振模型 σ 是实测的 1.19×、实测分布 2.5σ 外比高斯重,深 BER(rj 0.01 1.31–1.40×)与 skew 早侧(1.34–1.73×)读高;
  示例 31 冻结均衡器 0.68–0.88×。
- 示例 31 / 44 / 45 统计列变(45 PAM4 32G KP4 窗口 0.360 → 0.321 UI、minmax / 单 lane 1.73 → 2.94;44 准同步 0.72–0.96×),其余 43 个不变;指纹 4 个统计值变
  (PAM4 32G 预设 7.6e-11 → 2.2e-9,深 BER 全在平滑尾部)。
- 文档:`cairn/DSP发端与PR.md` §30(§29 加更正)、pitfalls 三条、invariants、`多lane建模.md` §7 / §8、clock_profile、USAGE、COMPARISON、示例 31 说明文字。
- 验证:本地全量 1132 passed / 1 skipped(+12 `tests/test_ffe_tracks_wander.py`);`HALO_NO_JIT=1` 下新文件 9 条非时域测试通过;ruff、Cython(linear /
  statistical)通过;`derived` 96 条成立。

## 2026-10-15 · 剖面时钟:运行的 σ、样本之间的曲线、DFE 保持(fix/profile-held-dfe,ROADMAP P2 #28)

- 定位(示例 31 链路,12–16 种子):剖面时钟保持抽头读高 6.6–15× 拆成三件事。① 模型 σ 是稳态,欠阻尼环路(kp 7–9,|E| 峰 4–16×)在一次
  20 万符号的运行里振铃不起来,稳态读系综 RMS 1.14–1.70×;线性模型按时域条件仿真(谱线 k/(N UI)、从静止起步)即对上。② 保持抽头的曲线在整样本
  之间线性插值读高 1.6–3.6×(冻结时域停在 ±0.25…±0.75 个样本量过)。③ 时域 LMS FFE 在 kp 8–9 跟上一部分慢游走(BER 低 1.6–1.8×)。
- 改:`ErrorResponse.run_mean_sq` / `run_sq_at`(极点展开闭式)、`ClockProfile.run_untracked_sigma_s`(按运行的谱线);读数取运行中 12 个点的平均;
  稳态另报 `sigma_steady_ui`、超 1.25× 警告;平滑小于两个样本时在样本之间真算(`_pulse_at`);剖面时钟也保持抽头。结果 0.66–1.40×(此前 0.24–1.42×)。
- 余项 ③ → ROADMAP P2 #29(仍在 2× 内)。更正:示例 31 的"模型 / 实测 0.81–1.07×"与 P2 #27 的"收端剖面 0 误码"都是 seed 3 的偶合。
- 示例 24 / 31 / 44 / 45 统计列变(44 同步 0.67–1.15 → 0.44–1.15×,NRZ 7/8 UI 陡点回到 0.44;skew 对游走 lane 0.92–1.31 → 0.67–1.09×),其余不变;
  P2 #27 白时钟组 NRZ 16G 0.45–0.89×、PAM4 32G 0.99–1.01×、NRZ 28G 0.67–1.08×。指纹 10 个统计值变,时域不变。统计单跑 1.3–1.7× 慢。
- 文档:`cairn/DSP发端与PR.md` §29(§28 加注)、pitfalls 新增三条 + 两条加注、invariants、`多lane建模.md` §3 / §7 / §8 更正、clock_profile、USAGE、COMPARISON。
- 验证:本地全量 1120 passed / 1 skipped(+9 `tests/test_profile_run_smear.py`;首轮 USAGE 一处代码注释被链接检查误认,改后 `test_docs_fresh` 14 条通过;`HALO_NO_JIT=1` 下新文件的 7 条闭式 / 插值测试通过);ruff、Cython(linear / statistical / clock)通过;`derived` 96 条成立。
- CI(0b4de9c,含代码提交 30551b4 —— 两个提交一起推送,CI 只在头上跑):Android API 34 / 35 / 编译版 instrumented 各 34 tests、0 failures;
  测试矩阵(含 nojit)、examples、examples-full、RTL lockstep、lint、import-clean、vendor-drift、pyinstaller、APK 构建绿,nuitka / nuitka-onefile 绿(117 / 73 分钟)。
- 上一条(#87)于 2026-10-15 合入。

## 2026-10-15 · 白时钟上 bang-bang 环路的游走进统计引擎(fix/bb-white-wander,ROADMAP P2 #27)

- 定位:白时钟统计引擎不建 CDR,时域 BB 环路在猎 0.025–0.062 UI;陡浴盆统计 / 时域 NRZ 16G 0.11–0.54×、PAM4 32G 0.48–0.71×、NRZ 28G 0.53–0.98×。
  把时域实际采样相位代进 BER(φ) 拆出两个缺口:PAM4 检测器 ±1 ↔ ∓1/3 的过零离锁定点 0.14–0.15 UI(只带噪声),以及平滑时理想 DFE 每相位重新适应。
- 改:白时钟也走 `bb_loop_fixed_point`(发端 RJ 进边沿噪声);`_bb_transitions` 按跳变分类(NRZ 逐位同旧);白时钟平滑时 DFE 停在读点的抽头,
  线性插值后的高斯平均(`_smeared_at`)。结果 NRZ 16G 0.50–0.89×、PAM4 32G 0.99–1.03×、NRZ 28G 0.75–1.12×,交叉校验 1.00–1.09×。
- 剖面时钟保持抽头读高 6.6–15×(示例 31),而时域均衡器同样不跟、保持曲线本身对、收端剖面时域 0 误码 —— 原因未定,剖面暂按重新适应读 → ROADMAP P2 #28。
- 示例 24 / 44 / 45 统计列变(44 同步 0.42–1.28 → 0.67–1.15×、准同步 0.72–0.91 → 0.72–0.96×;45 混合信号曲线变陡,minmax/单 lane 0.17 → 1.92),
  其余不变;`test_skew` 时域参照改为冻结 + 回放自由环路的游走(0.92–1.31×)。指纹 16 个统计值变(白时钟混合信号),时域不变。
- 文档:`cairn/DSP发端与PR.md` §28、`多lane建模.md` §3 / §7 / §8.2 / §8.3 更正、pitfalls 三条、invariants 两处、USAGE §9 与多 lane、COMPARISON、clock_profile。
- 验证:本地全量 1111 passed / 1 skipped(+8 `tests/test_bb_white_wander.py`;`HALO_NO_JIT=1` 下统计路径两条通过);ruff、Cython(linear / statistical)通过;`derived` 96 条成立。
- CI(60855a0,含 21324bc / 8615adf / 5162d47 —— 四个提交一起推送,CI 只在头上跑):Android API 34 / 35 / 编译版 instrumented 各 34 tests、0 failures;
  测试矩阵(含 nojit)、examples、examples-full、RTL lockstep、lint、import-clean、vendor-drift、pyinstaller、APK 构建绿,nuitka / nuitka-onefile 绿(81 / 122 分钟)。
- 上一条(#86)于 2026-10-14 合入。

## 2026-10-14 · MM 定时函数在锁定点附近见顶:报拐点距离、按环路游走警告(fix/mm-hump-warning,ROADMAP P2 #26)

- 定位:指纹 53 Gb/s 合成链路的 MM 定时函数被均衡在锁定点压到零,0.42 个样本(0.026 UI)后见顶、顶点只高出零约 2e-4(低于模型分辨率);
  过了拐点拉回力变弱、再过零点反向,时域环路先慢爬(+0.02 → +0.08 UI)再按平方跑走。预设拐点 0.075–0.15 UI。
- 改:`cdr.linear.mm_hold_range_ui`(拐点距离;与 `mm_pd_statistics` 共用 `_mm_cursor_set`,后者逐位不变)、`LoopSolution.hold_range_ui` /
  `sigma_wander_ui`(自噪声 + 带宽内跟晚的时钟),统计引擎剖面时钟 MM 分支 `hold < 8 × wander` 警告;GUI 抖动面板加一行。白噪钟不建环路,不查。
- 标定(SSPLL 剖面,PRBS7Q–15Q × kp_shift 4–10 × 4 种子):2.8–6.9× 失锁、8.6× 起没有;随机符号 0.1–0.7×;预设 15–33×、示例 31 15–37×。
  拿总 σ 比时示例 31 全部误报(1.6–6.7×)—— 带宽外的抖动环路不跟。更正 clock_profile.md"PRBS13Q 下内核锁住":只在白时钟下成立。
- 数不动:指纹 860 值逐位不变,示例 31 重跑与期望一致。`cairn/DSP发端与PR.md` §27(§25 加注与更正)、pitfalls 两条、invariants 铁律 3 补一条。
- 验证:本地全量 1103 passed / 1 skipped(+4 `tests/test_mm_hold_range.py`,`HALO_NO_JIT=1` 同样通过);ruff、Cython(linear / statistical)通过;`derived` 96 条成立。
- CI(2aff693,含代码提交 c1b3da0 —— 两个提交一起推送,CI 只在头上跑):Android API 34 / 35 / 编译版 instrumented 各 34 tests、0 failures;
  测试矩阵(含 nojit)、examples、examples-full、RTL lockstep、lint、import-clean、vendor-drift、pyinstaller、APK 构建绿,nuitka / nuitka-onefile 绿
  (117 / 80 分钟,C 编译缓存没命中)。
- 上一条(#85)于 2026-10-14 合入。

## 2026-10-14 · 发端白抖动按逐沿噪声进统计引擎(fix/tx-edge-jitter,ROADMAP P2 #25)

- 定位:统计引擎把发端 `rj_ui` 当采样时刻抖动抹相位轴,时域每个边沿各自移动 —— 经信道是跟着跳变走的噪声。示例 31 白时钟统计 / 时域 0.03×,
  0.03–0.09 UI 下五条链路 0.00–0.10×。逐步:线性一个方差 0.21× → 按邻居分箱 0.45× → 混合信号仍 ≤0.16×:冻结环路 + 蒙特卡洛分清是
  线性化(快信道上 3σ 的边沿把阶跃陡段拖到采样点,线性读低 29×);被采符号自己的两个边沿精确即与全部精确相同。
- 改:`tx_edge_jitter` → `TxEdgeJitter`(逐相位、分箱、两个自身边沿的精确核直接卷积),相位轴只留接收端 RJ;电压格跨度与 `phase_ui` 无关。
  统计 / 时域:224G ADC 0.69–1.39×,示例 31 白时钟 0.55×,PAM4 32G 混合信号 0.47–0.67×,NRZ 16G 0.38×(0.09 UI)。
- 余项 → ROADMAP P2 #27:白噪钟下 BB 环路自身游走(0.025–0.062 UI)统计引擎不建;实测游走加上后 0.83–0.90×。
- 示例 24 / 31 / 44 / 45 统计列变(44 的准同步 0.72–0.86 → 0.72–0.91×),其余 42 个不变;指纹 25 个统计值变、时域不变。`cairn/DSP发端与PR.md` §26、
  pitfalls 两条(逐沿抖动不能线性化;`tools/fingerprint.py` 自己插 `src`)、invariants 铁律 3 补一条、`多lane建模.md` §7 / §8.3 更正。
- 验证:本地全量 1099 passed / 1 skipped(+4 `tests/test_tx_edge_jitter.py`;两条白噪钟断言改为只含接收端 RJ);ruff、Cython(statistical)通过;`derived` 96 条成立。
- CI(86df226,最后一个改代码的提交):Android API 34 / 35 / 编译版 instrumented 各 34 tests、0 failures;测试矩阵(含 nojit)、examples、
  RTL lockstep、pyinstaller、nuitka、nuitka-onefile 绿。5fa3f11(中途文档提交)的 nuitka-onefile 在 build 与冒烟都通过后 upload-artifact 时
  runner 掉线,已在 PR 上说明、未重跑。
- 上一条(#84)于 2026-10-14 合入。

## 2026-10-14 · MM 环路模型:检测器读 DFE 之后,噪声按低频谱密度(fix/mm-loop-model,ROADMAP P2 #24)

- 定位两处:模型的 MM 检测器读 DFE 之前的样本(内核读切片器输入;冻结环路实测增益 0.0276,旧模型 0.0195,扣锁定点 DFE 光标后 0.032);
  噪声当白噪声用了逐符号方差 —— 块内相邻主光标抵消,慢环路积分的低频谱密度只有 1/5;PRBS13Q 的数据噪声是 13.7 MHz 起的谱线、在环路外,
  时域自噪声比随机符号小一个量级。
- 改:`mm_pd_statistics(n_feedback, n_lanes, period_levels) -> (k_pd, PdNoise)`(白底 + 码型谱线),`self_noise_ui` 逐条过 `|H|²`;统计引擎
  `_pattern_period` 从 `sim.pattern` 取周期。示例 31 模型 / 实测 0.97–1.39× → 0.81–1.07×;112 GBd 测试 0.98 / 1.23 / 0.96(区间 0.85–1.3)。
- 中途:只改增益的版本让指纹合成链路 σ 0.020 → 0.129 UI(该链路定时函数在锁定点后见顶,随机符号下内核失锁 7.7 UI);补上噪声后 0.0045 UI,
  时域 40 万符号稳态 0.0036 + 未跟踪 0.0033。指纹只动这 4 个值;时域逐位不变。
- 新登记 ROADMAP P2 #25(白时钟发端边沿抖动过信道)、#26(MM 定时函数见顶处失锁、模型不报)。`cairn/DSP发端与PR.md` §25、
  `docs/clock_profile.md`、pitfalls 两条 + 两处更正、invariants 铁律 3 补一条。
- 验证:本地全量 1095 passed / 1 skipped(+1 `test_mm_noise_is_what_a_slow_loop_integrates`,112 GBd 测试 1 → 3 个 kp_shift);ruff、Cython(linear / statistical)通过;`derived` 96 条成立。
- CI(92eff58,含代码提交):Android API 34 / 35 / 编译版 instrumented 各 34 tests、0 failures;测试矩阵(含 nojit)、examples、RTL lockstep、
  pyinstaller 绿,nuitka / nuitka-onefile 绿(74 / 133 分钟,C 编译缓存没命中);PR 头 0443ffd(只改 LOG)上测试矩阵、examples、examples-full 全绿。
- 上一条(#83)于 2026-10-14 合入。

## 2026-10-14 · 统计单跑的 ADC 代用 FFE 就是时域起点(fix/standin-ffe,ROADMAP P2 #23)

- **原判断更正**:ROADMAP 写"时域 LMS 把 FFE 从起点挪得很远、代用解只是起点" —— 错。时域 `w_ffe0` 与终点只差 0.2 %(20 万符号),
  统计引擎读起点与读终点相同(4.84e-5);代用解 `initial_ffe_taps` 是起点求解的拷贝,在不含发端 FFE 的脉冲上解,差 7.5 %、读 1.03e-3。
- 改:起点求解抽成 `timedomain.adc_start`,时域引擎 / `initial_ffe_taps` / `front_end_pulse` / skew 锁定点共用。时域指纹 860 值逐位不变;
  六种 ADC 配置上代用解与时域起点逐位相同;lane 冻结在偏移处 14–25× → 0.9–1.5×。
- 示例 45:ADC lane 锁定点 1.0e-3 → 4.8e-5,KP4 窗口 0.435 UI(FFE 重解)/ 0.156 UI(沿用)。应用的统计 study 与双引擎面板的 ADC 结果随之变。
  pitfalls 加"代用的东西调同一个函数";`cairn/DSP发端与PR.md` §24、`多lane建模.md` §8 更正。
- 验证:本地全量 1092 passed / 1 skipped(+3 `test_standin_is_the_time_engines_start`);ruff、Cython(timedomain / cascade / skew)通过;`derived` 96 条成立。
- CI(2941172,含代码提交):Android API 34 / 35 / 编译版 instrumented 各 34 tests、0 failures;测试矩阵(含 nojit)、examples、RTL lockstep、
  pyinstaller 绿;PR 头 f8c1820(只改 LOG)上测试矩阵、examples、examples-full 全绿。
- 上一条(#82)于 2026-10-14 合入。

## 2026-10-13 · 静态引擎的 FFE 把后光标留给 DFE(fix/static-dfe-start,ROADMAP P2 #22)

- 改:`run_static_link` 的 FFE 原先连 DFE 的后光标一起压掉(DFE ≈ 0),改为 MMSE-DFE(`free_post = rx.dfe.n_taps`),与 P1 #21 的时域起点同一规则,
  两种架构一样;不加 ADC 噪声(静态引擎不量化)。
- 量:NRZ 32G static 10 mV 2.56e-2 → 1.47e-3(DFE 1.03 / 0.35);224G 2.5 mV 4 → 1、112 GBd stress 4 mV 32 → 4 个误码。指纹 72 个 `static` 值变,
  切片器 SNR 六个预设 +0.5…+2.6 dB、三个 −0.1…−0.5 dB(窗口内残余 ISI 与噪声放大都更小,差在代价不含的项上,未查)。
- 示例 01 / 03 SNR 15.1 → 19.3 dB、内眼 6.8 → 16.2 mV,系统框图重画;示例 04 StatEye 对 MC 1.03× → 0.92×(README / SUMMARY / COMPARISON /
  铁律 3 基线改数,旧值保留)。静态引擎对 ADC 预设仍比时域乐观约 100×(不建 ADC 量化,定位如此)。`cairn/DSP发端与PR.md` §23。
- 验证:本地全量 1089 passed / 1 skipped(+1 `test_ffe_leaves_the_dfe_its_postcursors`);ruff、Cython(static_link)通过;`derived` 96 条成立。
- CI(2684225,含代码提交):Android API 34 / 35 / 编译版 instrumented 各 34 tests、0 failures;测试矩阵(含 nojit)、examples、RTL lockstep、
  pyinstaller 绿;PR 头 f7f8d8a(只改 LOG)上测试矩阵、examples、examples-full 全绿。
- 上一条(#81)于 2026-10-13 合入。

## 2026-10-13 · ADC 收端的 DFE 起作用(fix/adc-dfe-start,ROADMAP P1 #21)

- 原因:联合 FFE/DFE 的 LMS 代价面沿"FFE 留 h1、DFE 消 h1"是平的,终点由起点决定(`dfe.mu` ×3000 抽头只动 0.01;三种起点三种终点);
  起点是不计 DFE 的 MMSE → DFE ≈ 0。`多lane建模.md` §8.4 原说"步长没按功率归一",已更正。
- 改:`mmse_ffe(free_post=)`,时域 ADC 起点与 `initial_ffe_taps` 把 DFE 的后光标留给 DFE、有 DFE 时正则加 ADC 噪声;预编码 1 + D 复合判决例外
  (246 → 5779 个误码)。统计引擎加第一抽头 DFE 误码传播,两状态链 s / (1 − p + s)(`dfe_ep_factor`):理想 DFE 乐观 1.8–5× → 0.81–1.25×。
- 结果:224G 106 GBd 2.27e-4 → 4.0e-6,stress / deep-LR → 0 误码;指纹 136 值变。13 个示例输出变,19–21 扫描加长、43 的 DFE 剖析点挪到新门限。
  **结论更正**:深 LR 阶梯 31 → 33 → 36.3 / 38.5 → 42.8 变 31 → 36.0 → 40.5 / 44.2 → 47.8 dB,DSP 深度 +1.8 → +4.7 dB(与收端 PR 打平),
  FEC 与 ADC 合起来比之和少 0.9 dB(原多 1.1);DFE1 的误码不再孤立(每事件 3.05),FEC 映射对它与 PR 同向,PR 对 DFE1 +4.98 → +2.13 dB;
  示例 08 字长墙 wl 5 → 7。示例 43 的方向断言缺数据时原先静默跳过(这回第一次重跑因此"通过"),改为打印。`cairn/DSP发端与PR.md` §22。
- 余项 → ROADMAP P2 #22 静态引擎 FFE 不计 DFE(6–14×)、#23 统计代用 FFE 离时域收敛点 14–25×(示例 45 的 ADC 曲线)、#24 MM 环路模型两端高估 1.26–1.39×。
- 验证:本地全量 1088 passed / 1 skipped(+2:`test_mmse_leaves_the_dfe_its_postcursors`、`test_adc_dfe_engages`);指纹两次录逐位相同;
  ruff、Cython(ffe / timedomain / cascade / statistical / skew)通过;`derived` 96 条引用成立。
- CI:最后一个代码提交 a212192 上 Android API 34 / 35 / 编译版 instrumented 各 34 tests、0 failures(API 35 第一次报 34/0 后 Gradle
  非零退出、step 红,同一 app 代码在 f774ed1 / 43fd12e 上都过,重跑一次通过);pyinstaller、nuitka、nuitka-onefile 绿。
  PR 头 bcd8bb0:测试矩阵(含 nojit)、examples、examples-full、RTL lockstep、lint 全绿(a212192 上 examples 红是期望输出还没更新)。
- 上一条(#80)于 2026-10-13 合入。

## 2026-10-13 · 共享时钟的 lane 间静态 skew 预算(feat/shared-cdr-skew,ROADMAP P3 #9c 静态部分)

- 建:`analysis/skew.py`(`lane_curve` / `LaneCurve.window` / `shared_cdr` 三种放法);`run_statistical(phase_ui=)` 在锁定点 + 偏移处采样、相位轴居中于此
  (`None` 原路径,`0` 逐位同原 `ber`);`initial_ffe_taps(at_ui=)`。示例 45。
- **更正**范围评估 §4 的"读浴盆即可、不改引擎":ADC 的浴盆沿用锁定点 FFE(偏 1/4 UI 读高 21×),均衡后脉冲最大样本可能不是主光标(从浴盆挪着读 12.6×)。
- 对时域(冻结环路停在偏移处、均衡器从该相位游标起步):混合信号 0.49–1.78×,ADC 给定该 lane 收敛的 FFE 0.98–1.26×。不从该相位起步时量到的是
  "没适应的接收机"(PAM4 32G −3/8 UI 1.5e-1 vs 1.7e-4)—— 记入 pitfalls。`cairn/多lane建模.md` §8。
- **发现** → ROADMAP **P1 #21**:时域 ADC 收端的 DFE 不起作用(FFE 从不计 DFE 的 MMSE 起步,联合 LMS 停在 DFE ≈ 0);DFE 感知起点下 224G 106 GBd
  2.29e-4 → 5.2e-6,另两个带 DFE 的预设误码 → 0。未修(会动全部 ADC 时域结果)。
- 验证:本地全量 1086 passed / 1 skipped(1074 → 1086,+10 `test_skew.py`、+2 示例参数化);指纹 846 值逐位不变;ruff、Cython(skew / analysis 包 /
  cascade / statistical)通过;`HALO_NO_JIT=1` 下铁律 3 测试通过;示例 45 全尺寸 24 s,`derived` 95 条引用成立。
- CI(4c317e3,代码提交):Android API 34 / 35 / 编译版 instrumented 各 34 tests、0 failures;pyinstaller / nuitka / onefile、测试矩阵、examples、
  examples-full、RTL lockstep 全绿。PR 头 dbdc5aa(只改 LOG)上 `test (3.10, nojit)` 两次跑满 30 分钟被取消 —— main 的 ae0d60e 也超时过,
  近十次 nojit 两极(15–16 / 25–30 分钟、三次停在 29),本 PR 的 `test_skew.py` nojit 下 33 s 把它推过线:测试 job `timeout-minutes` 30 → 45。
- 上一条(#79)于 2026-10-13 合入。

## 2026-10-13 · 串扰进配置;准同步 lane(feat/xtalk-config,ROADMAP P3 #9b)

- 建:`LinkConfig.crosstalk`(bank + 逐个 `AggressorConfig`,可从 Touchstone 取耦合),时域 / 流式 / 统计 / COM 从它建 aggressor;显式入参替换并警告;
  静态引擎与重定时拓扑遇到配置串扰报错;表单加 bank 七项(Android 同一份 SECTIONS);应用的串扰 study 先去掉配置串扰。
- 准同步:`ppm` ≠ 0 时域按 aggressor 自己的速率保持符号(`symbol_index`,两种引擎同一个函数),统计对相位求平均(一个 aggressor 时 = 各 skew 平均,单测 1e-6)。
- 量(示例 44):同步 8 个 skew 时域差 8.9–32.2×;准同步统计 / 时域 0.79–0.93×,时域是同步平均的 0.80–0.97。`cairn/多lane建模.md` §7。
- 验证:本地全量 1073 passed / 1 skipped / 1 failed —— 失败的是铁律 5 的源码扫描(统计引擎里新写的 `xy[ph::osr]` 按 osr 跨步切片),
  改用 `core.sampler.baud_samples` 后单独重跑通过(数值相同);指纹 846 值逐位不变;Cython(schema / crosstalk / statistical / timedomain /
  stream / cascade / static_link / com / 应用两模块)通过。测试 1054 → 1074(+18,+2 是示例参数化多了 44)。
- CI(0989789):Android API 34 / 35 / 编译版 instrumented 各 34 tests、0 failures(表单多了 crosstalk 一节,FormContractTest 通过);
  测试矩阵(含 nojit)、examples、examples-full、RTL lockstep、pyinstaller 全绿。首个提交 6eea12f 上 API 35 的模拟器 13 分钟没出测试报告就失败
  (API 34 / 编译版同代码通过),0989789 上同一 job 通过 —— 模拟器的事,不是代码。
- 上一条(#78)于 2026-10-13 合入。

## 2026-10-13 · PAM4 比特误码按 Gray 码不同的比特数计(fix/pam4-gray-bits,ROADMAP P2 #20)

- 原:`symbol_checker` 与统计引擎的逐符号判决链按 min(|电平差|, 2) 数比特,0 ↔ 3(00 / 10,只差 1 bit)记成 2;预编码 mod 4 回绕出的正是它,预编码 PAM4 BER 高 25 %。
- 改:两处都用 `core.mapping.GRAY_BIT_DISTANCE`。指纹 846 值逐位不变(无预编码预设)。`tests/test_cascade.py` 原先把 0 → 3 断言成 2 bit,改为 1。
- 示例:44 个全尺寸重跑后比对 —— 40 的两行预编码(收端 f_r 11.94 → 11.86、发端 17.30 → 17.10 GHz,噪声容限各动 0.2–0.3 dB,结论不变)、
  36 的预编码点 8.9e-6 → 7.6e-6,37 / 38 / 42 只动 BER ≥ 0.07 的点。引用处更新、旧值写在日期说明里;`derived` 95 条引用成立。
- 验证:本地全量 1053 passed / 1 skipped / 1 failed(就是上面那条钉住旧计数的测试,改后单独重跑通过);ruff、Cython(`mapping.py` / `statistical.py`)通过。
  测试 1050 → 1054(+4)。
- 坑:并行 4 个全尺寸示例时 38(1024 状态 × 40 万符号)与 43(256 状态 × 400 万符号)的 Viterbi 回溯表各要 3.3 / 8 GB,被杀掉无回溯;单独跑都通过。
- 上一条(#77)于 2026-10-13 合入。
- CI:源码提交 525bed8 上 Android API 34 / 35 / 编译版 instrumented 各 34 tests、0 failures,pyinstaller / nuitka / onefile 通过(它的测试矩阵红,
  是那条钉住旧计数的 cascade 测试与尚未更新的 expected,下一提交修);head 7f3cb1a 上测试矩阵(含 nojit)、examples、examples-full 全绿。

## 2026-10-13 · 误码按 lane 复用与码字交织映射到 RS 码(feat/fec-mapping,ROADMAP P3 #9a)

- 建:`fec/interleave.py`(`bit_errors` / `FecLayout` / `burst_profile` → 复合泊松 FER、等效门限、按事件 bootstrap);时域与静态链路 extras 加 `reference`
  (只加键;指纹原有 819 值逐位相同,另 27 个是新键)。校验:四条 lane 逐码字直接计数对复合泊松 t = 0…5 吻合;独立误码 0.95–0.99、孤立误码 0.986。
- 量(示例 43,示例 42 的链路,门限附近 400 万符号):二项投影对 PR + Viterbi 在仿 200G/lane 的符号对复用 + 四码字下偏保守(reach +0.58 dB),
  单 lane / 4:1 / 8:1 比特复用下偏乐观(1 + aD −0.30 / −0.60 / −2.14 dB);DFE −0.25…+0.02 dB。1 + aD 对 DFE1 的优势随映射 3.09–5.55 dB。
- **更正**范围评估 §2:单 lane 下 RS 符号误码率低(0.51 倍)≠ FER 低,t = 15 的尾部由码字内簇大小决定,方向反了(已在 §2 追加更正;坑记入 pitfalls)。
- 顺带发现 `symbol_checker` 把 0 ↔ 3 记 2 bit,预编码 PAM4 BER 高 25 % → ROADMAP P2 #20(未修)。
- 落点:`cairn/多lane建模.md` §6、ROADMAP(9a 移除、P2 #20 新增)、CHANGELOG、USAGE §11、README。测试 1023 → 1050(+25 个新测试,+2 是按示例参数化的测试多了示例 43)。
- 上一条(#76)CI:ebd6964 上 Android API 34 / 35 / 编译版 instrumented 各 34 tests、0 failures;pyinstaller 3 分钟、测试矩阵(含 nojit)、
  examples、RTL lockstep 全绿;nuitka 125 / onefile 93 分钟通过。#76 于 2026-10-13 合入。
- 验证:本地全量 1050 passed / 1 skipped;ruff 通过;Cython 编译 `interleave.py` / `fec/__init__.py` / `timedomain.py` / `static_link.py` 通过;
  示例 43 全尺寸 702 s(二项列与示例 42 逐位相同)、smoke 15 s;`example_drift.py derived` 95 条引用全部成立。
- CI(80b7257):Android API 34 / 35 / 编译版 instrumented 各 34 tests、0 failures;pyinstaller 3 分钟、nuitka 28 分钟、测试矩阵(含 nojit)、
  examples、examples-full(与 `examples/expected` 比对)、RTL lockstep 全绿;onefile 记这条时仍在跑。

## 2026-10-13 · 串扰在受扰 lane 的采样相位上读(fix/xtalk-phase,ROADMAP P2 #19)

- 原因两条:统计引擎按每个 aggressor 自己的峰值取游标(所有 aggressor 同时最坏);ADC 收端的串扰没过 FFE(时域在 ADC 前叠加,跟信号一起被 FFE 放大)。
- 改:在受扰 lane 的采样网格上读全部游标,ADC 先过 FFE;`XtalkAggressor.skew_s`(同步 lane 的 skew)经 `launched_coupling` 给时域 / 流式 / 统计同用。
- 各 skew 下统计 / 时域:224G ADC 2.9–49× → 1.01–1.08×,PAM4 32G MS 0.79–1.21×,NRZ 28G 0.41–1.17×(0.41 是浴盆在锁定点旁太陡,§3)。
  示例 24 在 −18 dB 24× → 0.66×。COM 不动(自有口径)。`cairn/多lane建模.md` §3。测试 1020 → 1023(+3)。
- 验证:本地全量 1023 passed / 1 skipped;指纹对 main 819 值逐位不变(无串扰的路径);Cython 编译 `statistical.py` / `crosstalk.py` /
  `stream.py` 通过;示例 26 / 28 输出不变。
- 上一条(#75)已于 2026-10-13 合入。

## 2026-10-13 · 多 lane 范围评估(study/multilane-scope,ROADMAP P3 #9)

- 用户选"先出范围评估"。现状:串扰侧完整(aggressor、ICN、COM、两引擎),链路本身单 lane;FEC 只有"比特误码独立"的二项投影;`error_idx` 现成。
- 探针一(误码成簇):DFE4 在 3.2e-4 不成簇;1 + aD + Viterbi 99 % 事件成串、平均 3.2 符号,单 lane 连续映射下 RS 符号误码率只有二项投影的 0.46 倍。
  802.3 的比特 / 符号对复用与 2 / 4 码字交织会打散成串(任务组材料,检索摘要)→ 拆成 9a。
- 探针二(串扰相位):aggressor 延后 0…7/8 UI,时域变 4.7× / 32×;统计引擎取各 aggressor 峰值相位,统计 / 时域 1.2–41×,铁律 3 在串扰下没被覆盖
  → 新增 ROADMAP P2 #19(一致性,先做);准同步 / 串扰入配置 → 9b;共享 CDR → 9c(静态读浴盆,动态按需)。PCS 协议行为进「边界」。
- 落点:`cairn/多lane建模.md`(新)、ROADMAP、`architecture-invariants.md` 铁律 3 补一条、COMPARISON。只改文档,不改库。
- 上一条(#74)CI:dccba92 上 Android API 34 / 35 / 编译版 instrumented 各 34 tests、0 failures;pyinstaller 4 分钟通过;测试矩阵(含 nojit)、
  examples、examples-full、RTL lockstep 全绿;nuitka 120 / onefile 97 分钟通过(2026-10-13 补记)。#74 于 2026-10-12 合入。

## 2026-10-12 · TDECQ 未定条文:前后抽头差限制与多模光纤仿真滤波器(feat/tdecq-constraints,ROADMAP P3 #11 余项)

- 核验(IEEE / OIF / LPO MSA 原文全被代理 403,只有检索摘要):dj D2.1 加了前后抽头差限制(按 w(0) 归一化,DFE 不归一化),数值没拿到,
  候选 (−0.3, 0.15)(2025-09 提案)/ 0.25(802.3ds 基线);DR1 上限 3.0 dB 是提案,未见采纳;MMF 的 TDECQ 是 0.5×baud 参考接收机
  再级联一个光纤仿真滤波器(802.3db Table 167-12 的 f_A),数值没拿到;OIF CTLE 表值仍没拿到。
- 建:`tdecq(pre_post_limits=…)`(越界移到边界,搜索可沿边界走)、`tdecq(fiber_emulation_hz=…)`、`TdecqResult.pre_post`;默认都不加。
- 量(示例 34 新增一节,原输出逐行不变):EML 上两种候选处处不起作用;DR1 若 3.0 dB 最小带宽 29.83 → 33.72 GHz;VCSEL 在 f_A 25.8 GHz 处
  过 SR1,本模型 100 m OM4 约当 f_A 25 GHz。`cairn/光互联建模.md` §8。测试 1017 → 1020(+3)。
- 验证:本地全量 1020 passed / 1 skipped,Cython 编译 `tdecq.py` 通过;指纹不覆盖 TDECQ,`src/` 只改了 `analysis/tdecq.py`(引擎未动),默认路径不变由示例 34 原输出逐行相同与原有 18 个测试证明。
- 上一条(#73)已于 2026-10-12 合入。

## 2026-10-12 · CPO 相对 LPO 的功耗差(feat/power-cpo,ROADMAP P3 #11 余项)

- 原状:功耗模型里 LPO 与 CPO 同价(拓扑只改电段损耗)。拆成两项:模块自身开销 —— `topology.optics_placement`
  (pluggable / co_packaged,只有功耗模型读)+ `co_packaged_optics_j_per_bit` 6.9 pJ/b(Broadcom CPO 5.5 W / 800G、JLT 2026 比 LPO 省 23%;
  一手页面全被代理拦截,经检索摘要);短走线让收端省下的 —— 由仿真定:示例 41 在 100 m OM4 上跑时域 ENOB 阶梯,LPO 最低 5.0、CPO 3.5。
- 结果:LPO 12.57、CPO 9.74 pJ/b,省 22%(模块 2.10 + 1.5 bit ADC 0.72);系数区间内 CPO / LPO 0.67–0.86。`cairn/功耗建模.md` §5、坑、更正。
- 坑:CTLE / 满量程不分别调,差距会被放大到至少 2 bit;只给一档 CTLE 时阶梯停在擦边点。发端随摆幅省的那部分仍不建。测试 1015 → 1017(+2)。
- 验证:本地全量 1017 passed / 1 skipped,指纹对 main 819 值逐位不变,Cython 编译 `schema.py` / `power.py` 通过;
  示例 41 全尺寸与 smoke 阶梯一致。Android(57d9201,含全部 `src/` 改动)API 34 / 35 / 编译版 instrumented 各 34 tests、0 failures;桌面打包 pyinstaller 3 / nuitka 84 / onefile 126 分钟通过;
  测试矩阵(含 nojit)、examples、examples-full、RTL lockstep 全绿。
- 上一条(#72)已于 2026-10-12 合入。

## 2026-10-12 · Viterbi 的网格在接收机实际采样点拟合(fix/mlsd-one-tap-ffe,ROADMAP P2 #17)

- 原因:网格取自脉冲峰值处的模型,环路不停在那里;1 抽头 FFE 时第一后光标模型 −0.086 / 实际 +0.058,MLSD 比 slicer 多 7 倍误码。
  有 DFE 时模型还把 DFE 之后的光标放错了延迟。改为按线路符号在实际采样点最小二乘拟合(决策导向的版本在脏 slicer 上被带偏,弃用)。
- 用户选"全量修复 + 文档重算":12 个 MLSD 示例重跑,reach 动 ≤ 0.1 dB、结论不变;95 处推导数与引用打印值的文档同步。双引擎重量都在 2× 内。
  `cairn/DSP发端与PR.md` §21。测试 1013 → 1015(+2)。
- 验证:本地全量 1015 passed / 1 skipped,指纹逐位不变(MLSD 关闭的路径),Cython 编译 `timedomain.py` 通过。Android(acd123e,含全部
  `src/` 改动)API 34 / 35 / 编译版 instrumented 各 34 tests、0 failures;桌面打包 pyinstaller 4 / nuitka 86 / onefile 124 分钟通过;
  测试矩阵(含 nojit)、examples、examples-full(漂移与 95 处推导数)、RTL lockstep 全绿。
- 上一条(#71)已合入;PR #69 / #70 / #71 于 2026-10-12 合入。

## 2026-10-12 · 光链路上联合界的 SNR 计入光噪声(fix/stat-optical-tx-pr,ROADMAP P2 #18)

- 排除:关掉光噪声只留电噪声时发端 PR 1.02×(不是信道 / 电平);残余 ISI 与分箱平移 ≈ 0;光噪声着色差别太小。
- 原因:联合界重叠权重按 `half_gap / noise_sigma` 抽样,光链路的 `noise_sigma` 只有电噪声 → SNR 高估、并集退化成简单求和。
- 改:按全部噪声(电 + 光的平均方差)抽样。示例 40 链路上 PR 0.91–2.99 → 0.83–1.44×;电链路指纹逐位不变。`cairn/DSP发端与PR.md` §20。
- 记下未做:事件噪声取首符号电平的方差(离线精确混合再低 0.6–0.8×)。测试 1012 → 1013(+1)。
- 验证:本地全量 1013 passed / 1 skipped,指纹逐位不变,Cython 编译 `statistical.py` 通过。Android(9607ef5,含全部 `src/` 改动)
  API 34 / 35 / 编译版 instrumented 各 34 tests、0 failures;桌面打包 pyinstaller 3 / nuitka 85 / onefile 121 分钟通过;测试矩阵(含 nojit)、
  examples、examples-full、RTL lockstep 全绿。
- 上一条(#70)已记 CI;#69 / #70 于 2026-10-12 合入。

## 2026-10-11 · 统计引擎 PR 联合界的残余 ISI 按事件投影(fix/stat-short-ffe-pr,ROADMAP P2 #16)

- 原因:联合界每个事件只把噪声按距离缩小,网格外残余 ISI 按单样本算(相对门限放大 g 倍);短 FFE 残余大才显形(1.8–10.7×)。
- 改:每个事件的 ISI = 残余光标在事件差 d 上的投影 d · ISI / |d|²(成对条件的精确形式)。短 FFE 0.77–1.24×,§14 的 16 格
  0.67–1.55 → 0.58–1.08×(几何平均 0.80,剩下的近似朝乐观走)。非 PR 路径指纹逐位不变;示例输出不变。`cairn/DSP发端与PR.md` §19。
- 顺带:光链路发端 PR、噪声在调制器后统计悲观 1.5–3.0×(原有)→ ROADMAP P2 #18。测试 1009 → 1012(+3)。
- 验证:本地全量 1012 passed / 1 skipped,指纹 819 值逐位不变,Cython 编译 `statistical.py` 通过。Android(b61fb2e,含全部 `src/`
  改动)API 34 / 35 / 编译版 instrumented 各 34 tests、0 failures;桌面打包 pyinstaller 3 / nuitka 116 / onefile 88 分钟通过;
  测试矩阵(含 nojit)、examples、examples-full、RTL lockstep 全绿。
- 上一条(#69)的 CI:b123ec9 上测试矩阵(含 nojit)、examples smoke、examples-full、RTL lockstep 全绿;本地全量 1009 passed / 1 skipped。

## 2026-10-11 · 降状态网格在短 FFE 上的代价(study/dfse-short-ffe,ROADMAP P3 #12 余项)

- 示例 42 加第二部分(3 抽头 FFE):delta memory 3 的 64 / 16 / 4 状态 30.06 / 30.04 / 29.97 dB,1 + aD 的 64 / 4 状态 31.04 / 31.05 dB。
  只留目标本身也只差 ≤ 0.09 dB(定长处误码约 +12%);原因是 3 抽头 FFE 留在网格窗口的残余只有 0.02–0.06。ROADMAP #12 清空。
- 顺带发现两件,未修:短 FFE + PR 目标统计引擎悲观 1.8–10.7×(P2 #16,铁律 3 例外,示例不在此区间,已记进
  `cairn/architecture-invariants.md`);1 抽头 FFE 下 MLSD 比 slicer 差约 7×(P2 #17)。证据见 `cairn/DSP发端与PR.md` §18"短 FFE"。
- 只动示例与文档,不动 `src/`;测试数不变(1009)。

## 2026-10-11 · 降状态检测的定点与 RTL(feat/fixed-dfse,ROADMAP P3 #12)

- 新:bit-true DFSE(`fixed_viterbi.run_fixed_dfse`)与 `rtl/dfse_mlsd.sv`。期望字 = 状态部分未舍入表 + 反馈份额,统一舍入一次 ——
  K = L 时等于全网格表、逐判决相同。定点模式不再拒绝 K < L。
- 验证:与独立参考(逐路径整段符号序列)在随机移位 / 饱和下逐判决相同;输入精确时等于浮点 DFSE;引擎定点误码数与浮点 3σ 内;
  SV lockstep(K = 1、4 状态、反馈 2 个光标、3000 符号)逐位通过,改一个金标即 FAIL。ROADMAP #12 剩"短 FFE 上的代价"。
- 测试 996 → 1009(+13);全量 1009 passed / 1 skipped,`run_lockstep.sh` 九段全 PASS,引擎指纹 819 值逐位不变,本地 Cython 编译改过的
  3 个模块通过。Android(f66bb97,含全部 `src/` 改动)API 34 / 35 / 编译版 instrumented 各 34 tests、0 failures;桌面打包
  三个 job 通过(pyinstaller 3 / nuitka 89 / onefile 113 分钟);eca39e2 上测试矩阵、RTL lockstep 全绿。
- 上一条(#67)的 CI:Android(a49d9fd,含全部 `src/` 改动)API 34 / 35 / 编译版 instrumented 各 34 tests、0 failures;
  桌面打包 pyinstaller 3 / nuitka 68 / onefile 88 分钟通过(onefile 在合并之后完成)。b2fa979 上测试矩阵、examples-full 全绿。

## 2026-10-11 · 降状态序列检测 DFSE(feat/reduced-state-mlsd,接功耗模型的结论)

- 新:`rx.mlsd.state_memory` = K,网格状态只记最近 K 个符号,其余光标按各幸存路径的判决反馈;不设 / K = L 逐判决等于全网格。
  统计引擎把错误事件的距离截到降状态网格的合并点(前 n + K 个样本)。定点模式拒绝 K < L(bit-true / SV 未建,ROADMAP P3 #12)。
- 示例 42(示例 38 的链路):K = 受控光标数时 reach 与全网格逐点相同 —— 1 + aD 4 = 64 状态 36.33 dB、两光标 16 = 256 状态
  38.60 dB、三光标 64 = 256 状态 38.94 dB;两光标 4 状态 37.75 dB。两光标收端估计 17.68 → 5.97 pJ/b。统计 / 时域 0.99–1.10。
- 改了 `cairn/功耗建模.md` 的"要做只能是降状态检测(本模型不建)",SUMMARY 杠杆表补降状态的代价。详见 `cairn/DSP发端与PR.md` §18。
- 测试 978 → 996(+18)。

## 2026-10-11 · 功耗估计(feat/power-model,ROADMAP P3 #11 余项"功耗")

- 新:`analysis/power.py` 按模块给 pJ/bit(发端 / 两种收端共用块按每 bit 总量,混合信号比较器与模拟 DFE,ADC 按 Walden / Schreier FOM,
  数字 FFE / DFE / Viterbi 按运算次数,光模块与重定时器);只读配置,不动引擎。系数全是公开数据的估计:Murmann ADC 调查表(中位数
  70 fJ / 146 dB)、Horowitz 45 nm、DeepScaleTool 45 → 7 nm 能耗比 0.175(用户上传的 .xlsm,只读宏源码未执行)、已发表收发器与实测 800G 模块。
- 核对:112G 收发器 4.23 vs 4.63 pJ/b;224G 收端 2.44 vs 1.41;retimed 减 LPO 8.51 vs 实测 7.4–8.5。示例 41 + 示例 07 换掉旧代理。
- 结论(系数取到区间两端仍成立):ADC 收端是混合信号的 2.01–5.85×;Viterbi 每多一个受控光标 ×3.76 / ×3.91,第二个光标 +9.48 pJ/b;
  同到 64 状态收端 PR 比 DSP 深度划算;retimed / LPO 1.53–1.75。不确定:ENOB +1 的代价(Walden / Schreier 差一个数量级,且 ENOB 7.5
  @ 112 GS/s 无已发表器件);LPO 与 CPO 在模型里同价。更正 SUMMARY 杠杆表"N² 状态"(实为 N³ = 64)。详见 `cairn/功耗建模.md`。
- 测试 952 → 978(`tests/test_power.py` 24 项,`test_examples_api` 对示例 41 参数化 2 项;全量 978 passed / 1 skipped);
  本地 Cython 编译 `analysis/power.py` 通过;引擎指纹 819 值逐位不变。Android(ba09502,含全部 `src/` 改动)API 34 / 35 / 编译版
  instrumented 各 34 tests、0 failures;桌面打包三个 job 通过(pyinstaller 3 / nuitka 83 / onefile 122 分钟);b4a5d8f 上测试矩阵、
  examples-full 全绿。

## 2026-10-11 · MMSE 起始解计入发端噪声(feat/mmse-tx-noise)

- 问题(示例 40 量到):`pr.adapt: mmse` 的起始解只看 FFE 输入处的白噪声,发端噪声为主时选 a = 0.98(12 GHz)/ 0.26(22 GHz),
  正确答案是 delta(噪声与信号同形时 MSE ∝ 1 + a²);`adapt: lms` 从那里几乎不动(0.980 → 0.981)。
- 改:`mmse_ffe` / `mmse_pr_target` 加 `noise_cov`;时域 ADC 收端与静态引擎的起始求解传入 σ²(`tx.noise_rms`)× DAC → FFE 逐 UI
  响应的自相关(Toeplitz)。DAC 量化不计入;无 `tx.noise_rms` 时逐位不变(819 值指纹)。
- 数:MMSE / LMS 选回 a ≈ 0,容忍的发端噪声 36.7 → 42.8 mV、45.6 → 48.2 mV,与对照相同;40 mV 时 BER 5.5e-4 → 1.0e-4。
  示例 40 重跑,发端 PR 两行小移(LMS 起点变了)。更正 §16 的"仍选 a ≈ 0.38"(那是 DAC 量化试验的值)。坑:自适应不会替你修起始解。
- 测试 950 → 952(全量 952 passed / 1 skipped);本地 Cython 编译改过的 4 个模块通过。Android(76b880c,含全部 `src/` 改动)
  API 34 / 35 / 编译版 instrumented 各 34 tests、0 failures;桌面打包三个 job 通过(nuitka 113 / onefile 121 分钟);b45c6bf 上
  测试矩阵、examples-full 全绿。详见 `cairn/DSP发端与PR.md` §17。

## 2026-10-11 · 光 duobinary:发端噪声 + 带宽受限调制器上 PR 放哪(study/tx-pr-optical,ROADMAP P3 #8 余项)

- 新:`tx.noise_rms`(DAC 输出处每 UI 白噪,调制器带宽之前,独立随机流 `[seed, 0x7E5]`;关着逐位不变,819 值指纹)。
  统计引擎把它与 DAC σ_q 合并,PR 联合界的噪声自相关加发端那份的颜色(只用 FFE 抽头时收端 PR 0.22–0.48×、发端 a = 0.5 2.9×,
  加后 0.52–1.88×)。`tx_report` 参考发端改为无噪声(原来同一份噪声做差抵消)。
- 示例 40(VCSEL f_r 当带宽限制):噪声在调制器后,需要的 f_r 对照 14.23 / 收端 PR 11.94 / 发端 PR 16.74–17.36 GHz;
  噪声在发端,带宽 12 vs 22 GHz 只差 1.1 dB,收端 PR −0.5…−2.3 dB、发端 −3.0…−5.7 dB。两种情形发端 PR 都不占优;
  光 duobinary 的收益由收端 PR 拿到,发端该做预加重。ROADMAP P3 #8 清空。
- 坑:第一版拿 4 bit DAC 量化当发端噪声,误差有界、统计按高斯,读数失真(收端 PR 代价 5.5 dB 对高斯下 1.5–2.3)。
- 测试 941 → 950(全量 950 passed / 1 skipped,JIT + iverilog);本地 Cython 编译改过的 5 个模块通过。
  Android(ff0ccf6,含全部 `src/` 改动)API 34 / 35 / 编译版 instrumented 各 34 tests、0 failures;桌面打包三个 job 通过
  (nuitka 113 / onefile 89 分钟);e7447f8 上测试矩阵、examples-full 全绿。详见 `cairn/DSP发端与PR.md` §16。

## 2026-10-10 · 推导数登记补全 + `example_drift.py candidates`(tools/derived-coverage,ROADMAP P3 #10)

- 扫法:先按"段落引用某示例、该示例不打印、未登记"扫(217 行、493 个数,几乎全是配置 / 历史 / 理论值,噪声太大);
  改按比较措辞("多 / 差 / 再加 X dB""+X dB""X×")扫,默认 79 行 + `--strict` 多出约 14 行,逐条读。
- 结果:真推导数 6 个 —— 示例 38 的 +0.34(ROADMAP)/ 约 0.3(USAGE)/ 2.3 / 0.03 dB(DSP §10 / §13),示例 37 发端与预测之差 0.4 / 1.3 dB;
  数都还对。登记 8 组 76 处 → 9 组 84 处(含 pitfalls 举例的两处)。其余是历史值、单独实测(多种子、定点扫描)、配置或别的示例打印的数。
- 工具:`candidates [文档…] [--strict]` 只列不拦;推导式 `max` / `min` 支持多操作数。顺带修 `docs/summary.html` 三处停在 913 的测试数。
- 测试 940 → 941(全量 941 passed / 1 skipped,JIT + iverilog);只动 tools / docs,不涉及 `src/`,无需 Android;CI(7fb884a)
  测试矩阵、lint、examples、examples-full 全绿。坑见 `engineering-pitfalls.md`
  "文档里引用的示例数字"一条的 2026-10-10 更新。

## 2026-10-10 · 高 SER 下 CDR 失效:统计引擎报环路负载(study/pr-high-ser,ROADMAP P2 #15 余项)

- 隔离:(1, 1.4, 1.0, 0.4) 3 mV 时域 BER 0.16 是 MM-CDR 漂走(−3.9 UI、4 次周跳),冻结 CDR 后 4.3e-3 对统计 3.1e-3(0.71×);
  冻结 LMS 无效。机理:MM 读"样本 − c_k × 判决"的残差,错判留下 c_k 倍误差。
- 界线:6 个目标扫噪声,逐符号 SER 分不开(首次失效 0.03–0.24);× Σc_k² 后首次失效都在 0.09–0.14,正常最高 0.117。
- 改:`extras['pr_loop_load']`,≥ 0.08 警告(预编码 1 + D 最早 0.090 定阈值;0.08–0.117 误报)。不建模漂走本身。
- 非 PR 逐位不变(指纹);本地 Cython 编译通过;测试 939 → 940(全量 940 passed / 1 skipped,JIT + iverilog)。P2 再次清空。
  Android(94ea0ff)API 34 / 35 / 编译版 instrumented 各 34 tests、0 failures;桌面打包三个 job 通过
  (nuitka 116 / onefile 121 分钟)。详见 `cairn/DSP发端与PR.md` §15。

## 2026-10-10 · 统计引擎 PR 联合界改为整族事件的并集(study/pr-event-union,ROADMAP P2 #15)

- 机理:链式界只扣同起点相邻长度,距离很平时仍多算;一个错误的后缀从下一个起点又算一次。原型只修前者 2.09 → 1.88×,
  两者都修 2.09 → 1.31×。
- 改:`pr_error_events(snr=...)` 竞争集合 = 各长度 × 两族 × ±11 符号内所有重叠起点 × 两种符号;在事件 j 成立且符号可行的条件下
  重要性抽样,权重 = 支持率 × mean(选中事件误码 / 同时成立数)(ALOE 估计器)。锁定相位算一次、按比值用到其他相位;+0.1 s / 次。
  删 `_bvn_upper`。
- 数:16 个 PR 链路统计 / 时域 0.78–2.09× → 0.67–1.55×(几何平均 1.45 → 1.05);直接抽样核对并集 / 直接 0.98–0.99。
  非 PR 逐位不变(指纹 819 值),示例输出不变。P2 #15 第一项清空,第二项(高 SER 判决导向环路失效)留着。
- 测试 935 → 939(全量 939 passed / 1 skipped,JIT + iverilog);ruff 通过。动了 `src/` → Android:第一次推送编译版 APK 失败(Cython 不认
  长度不同的元组条件表达式,坑进 pitfalls),改写后本地 Cython 编译通过;
  Android(435ae33)API 34 / 35 / 编译版 instrumented 各 34 tests、0 failures;桌面打包三个 job 通过(nuitka 62 / onefile 115 分钟)。详见 `cairn/DSP发端与PR.md` §14。

## 2026-10-10 · 第三个受控光标:实现与实测(feat/pr-four-cursor,ROADMAP P3 #8)

- 用户按"重算后越过 0.5 dB 门槛"选择实现。`pr.target = (1, a, b, c)`:内核 `pr_gamma` / `fs[6]`、`mmse_pr_target(4)`、定点环
  `prs[2]` + c 的范围、RTL `ab[3]` + 新向量 `vectors/pr4`(逐位一致)。Viterbi 与 343 状态判决模型本来通用。
- 旧配置逐位不变:仓库指纹 819 值;另录 11 个 PR 用例(含定点 / 统计)前后一致;RTL 旧向量只多 c 行与两参数。
- 实测(示例 38):四光标 memory 1 38.94 dB、memory 2 38.97,三光标 38.60 → **+0.34 dB**;种子 7 / 8 / 9 +0.18 / +0.31 / +0.48。
  **四个种子都不到门槛**;MC 折算的 +0.6–0.7 约高一倍(§12 写的"CDR / LMS 反作用可能更重"成真)。可用,不推荐。
- 顺带修:统计引擎 PR 下相位网格以锁定点为中心(a > 1 时原读到下一个光标,BER 2.7 对 6e-4)。双引擎四光标:MMSE 型 c > 0
  0.78–1.73×;手选 c < 0 1.75–2.09×(一格越界)、强目标高 SER 时域失效 → ROADMAP P2 #15。坑两条进 pitfalls。
- 测试 913 → 935(全量 935 passed / 1 skipped,JIT + iverilog);`HALO_NO_JIT=1` 下四光标内核 / 定点测试 35 passed;ruff 通过。
  Android(e9c889d,其后未再动 src)API 34 / 35 / 编译版 instrumented 各 34 tests、0 failures;桌面打包三个 job 通过
  (nuitka 112 / onefile 114 分钟,缓存未全命中,仍在 180 分钟上限内)。详见 `cairn/DSP发端与PR.md` §13。

## 2026-10-10 · 示例 06 / 18 的 MLSD 增益全程计数(study/mlsd-full-count,ROADMAP P2 #14)

- 示例 18 / 06 改用引擎 `rx.mlsd` 计分:`extras["ser_slicer"]`(MLSD 前)与 `ser` / `ber`(MLSD 后)来自同一组判决、约 79 万比特;
  残余光标仍拟合 `y_slicer`。先在 6 个点上对过两种做法(离线 2 万符号 vs 引擎全程),结论方向一致。
- 数:21 抽头 MLSD 1.0×(零误码到 −27.3 dB,原记 −28.8);3 抽头 2.4–4.3×(原 1.9–4.4×);示例 06 memory 1 / 2:2.1 / 2.4×。
  SUMMARY、summary.html、pitfalls、光互联建模的引用同步;ROADMAP #14 清空。只动示例与文档,不动库。

## 2026-10-09 · reach 读法与计数口径(study/reach-grid,登记推导数时的新发现)

- 发现:同一接收机在示例 20 读 32.4、示例 21 读 33.2 dB。查下来两个读法问题 + 一个计数问题:post-FEC 对数插值偏向差点(示例 21
  全栈 44.2 vs pre-FEC 越线约 42.8)、零误码点的替身决定答案、示例 19 / 20 / 21 / 35 的离线 Viterbi 只数 `y_slicer` 的 2 万个符号。
- 改:`fec.fec_threshold / reach / refine`(pre-FEC 阈值插值、二分加密、零误码不插值),九个示例与 app 的两个 reach 研究换用;
  19 / 20 / 21 / 35 改用引擎 `rx.mlsd` 全程计分(20 / 21 按标签 memory 3)。同一接收机三个示例都读 33.0 dB。
- 数:杠杆 +1.7 / +5.5 / +3.3、全栈 42.8 dB;收端 PR +5.0、两光标 +7.3;光三拓扑 156 / 255 / 214 m。旧数留作历史注记。
  第四光标估计 +0.5 → +0.6–0.7 dB(三种子),越过门槛,ROADMAP 移回 P3 #8 待决定(不替用户翻案)。
- 剩余进 ROADMAP P2 #14:示例 06 / 18 仍在 2 万符号上比判决器 vs MLSD。坑进 pitfalls(两条),约定进 architecture-invariants。
- 动了 `src/halo_serdes/fec`(新模块)与 `src/halo_serdes_app/studies.py`:桌面全套 913 passed / 1 skipped;Android(601f8f2,其后未再动 src)API 34 / 35 / 编译版 instrumented 各 34 tests、0 failures,桌面打包三个 job 通过。测试 909 → 913。

## 2026-10-09 · 文档推导数的漂移检查(tools/derived-numbers,ROADMAP P3 #10)

- 盘点:扫文档里带"多 / 差 / ×"的数、并查示例输出是否直接打印。多数增量示例自己打印(示例 33 / 36 / 37 / 38 的 gain 列);
  真正推导的集中在示例 12 / 19 / 20 / 21 / 32 / 33 / 34。ROADMAP 原举的"收端 PR 多 4.7 dB"其实示例 36 打印了(+4.71),已更正。
- 改:`examples/derived.yaml` 登记 7 组 57 处引用(算子所在输出行 + 引用文字,`{expr}` 标出引用的数);`example_drift.py`
  的 `compare` 用新输出重算并列出不再成立的引用,新增 `derived` 子命令,测试按 `examples/expected/` 逐处核对。
  模拟示例 19 的 ADC 档 37.7 → 38.1:原查找列出 4 处 37.7,推导检查另列出 10 处 "+5.3 / ~5 dB"。
- 第一次重算就抓到一处:示例 12"差值恰为 9.5 dB 电平代价",两点实差 9.8 dB(步长 1.6 dB)—— 算子被往理论值近似了。
  README / SUMMARY / summary.html 改为给两点与差值、写"相符";坑进 engineering-pitfalls。
- 只改工具、测试与文档,不动库。测试 905 → 909。

## 2026-10-09 · 桌面打包 Nuitka 编译缓存(ci/nuitka-timeout,ROADMAP P2 #13)

- 115 次桌面打包的 job 耗时:10 月 nuitka 中位数 ~108–111、onefile ~115–118 分钟,上限 120;onefile 10-03…08 被取消 7 次。
  日志:Python 层 8 分钟、生成 C 2 分钟、MSVC 编 3643 个 C 文件 97 分钟("clcache with 0 cache hits")、链接打包 7 分钟。
- 改:`actions/cache` 跨运行保存 `NUITKA_CACHE_DIR`(step 级 env —— job 级 env 拿不到 `runner` 上下文),上限 180 分钟。
  三轮实测:冷 onefile 87 / standalone 109 分钟,手动再触发一轮热的 40 / 31 分钟(3642 / 3643 命中),文档提交触发的第三轮
  42 / 35 分钟(3643 / 3643 命中),缓存 92 MB / job。全命中时 C 阶段仍用了 ~26 分钟(原因未查)。
- 坑进 engineering-pitfalls(超时是 cancelled 不是 failure,`continue-on-error` 不管,还会让 tag 的 release 被跳过)。
  合并后 main 的第一次仍是冷的(分支缓存 main 读不到)。只改 workflow 与文档,不动库与测试。

## 2026-10-09 · ADC 收端的统计结果按 MM 锁定点读(study/adc-phase,ROADMAP P2 #12)

- 查"时域 MM CDR 为何不动":把环路起点挪开 ±0.5 / ±2 采样再跑。`ffe` 输入的五条 PAM4 链路都往原起点回摆(衰减往复,周期约
  10 万 UI;示例 32 CPO 40 万 UI 内回到 ±0.02),起点出发的留在起点 —— 锁在均衡前脉冲峰,不是不动。`adc` 输入的 NRZ 锁在
  h(−1) = h(+1)(−2.33 / −2.83 采样,`lock_offset_samples(..., osr)` 预测 −2.30 / −2.80)。
- 改:`run_statistical` 对 ADC 也按锁定点报(mixed-signal 早已如此),最低点留在 `extras['ber_min_phase']`。电链路预置上两者本就
  同点;40 个示例全尺寸只有示例 31 的 kp_shift 9 两行变(1.37 → 1.85×,抖动 1.2 采样时读在环路自己的相位上),正文范围改 0.3–1.9×;
  示例 04 的"at best phase"标签本来就不准,改了。示例 32 的 CPO + FFE、c = 0.5:0.35 → 0.91×。
- 测试 +2(均在旧代码上挂)。`example_drift.py` 改为同数字个数的行按位置配对(改标签的行不再误报)。指纹:不带 FFE 抽头调统计引擎
  的 ADC 条目变(未均衡,无意义)。
- 顺带:桌面打包 nuitka 两个 job 各用 108–118 分钟,上限 120;2026-10-08 main 的 `nuitka-onefile` 在 120 分 04 秒被取消 → ROADMAP P2 #13。
- 全量(JIT + iverilog):905 passed / 1 skipped(+2);ruff 通过。
- PR #55;CI 全部 test job + examples + rtl-lockstep + `examples-full`(40 / 40)绿;Android instrumented 34 tests / 0 failures(API 34、35、编译版 APK)。

## 2026-10-09 · 统计引擎的光噪声核补上发端 FFE(study/stat-strong-eq,ROADMAP P3 #11 一项)

- 复现 16 dB + 驱动器 9 dB、c = 0.5 悲观 2.66 / 2.80×。逐电平比噪声:时域实际几乎不随电平变,统计核随电平变得多;c = 0 时线性仿真
  与统计核也对不上,且统计的 t_s 比含 FFE 的脉冲峰早 1 UI → 噪声核用阶段 1 `h1` 拼脉冲,**漏了发端 FFE**(主脉冲与曲线图样平均都卷了)。
- 修:`optical_stage._symbol_pulse_at_pd`。与无噪声波形的精确期望 0.15 % 内(旧 −22…+22 %);`_adc_cfg` + host FFE 线性链路
  2.5–3.0× → 0.94–1.16×(铁律 3 原被打破);示例 32 各点 1.27–2.66× → 0.85–1.47。不开 FFE 的逐位同(指纹 804 值),指纹加 `optical/stat`。
- CPO + FFE、c = 0.5 修后按最优相位 0.35×:是相位口径 —— ADC 统计报最优相位,时域 MM CDR 在 FFE 自适应下不动(σ 0.006 采样),
  按时域相位 0.91×。记 ROADMAP P2 #12,不在本 PR 改约定。
- 试过没留:按曲线后真实功率逐箱重算噪声(11 点平均 |ln 比值| 0.193 → 0.183,有升有降,在计数误差内)。
- 示例 32 带 FFE 的 7 行裕度变了(16 dB + FFE 3.92 → 5.52 dB 等),`example_drift.py` 第一次实战找出文档行;顺手把一位数排除
  (170 → 74 行)。推翻一条:"c = 0.5 时 CPO 上 FFE 增益缩水"(修后 4.17 vs 线性 4.12)。§6 更正段、SUMMARY / USAGE 同步,预加重研究表重跑。
- 坑进 engineering-pitfalls(两条)。全量(JIT + iverilog):903 passed / 1 skipped(+3);ruff 通过;新测试在旧代码上三条全挂。
- PR #54;CI 全部 test job + examples + rtl-lockstep + `examples-full`(40 / 40)+ pyinstaller 绿;Android instrumented 34 tests / 0 failures(API 34、35、编译版 APK)。

## 2026-10-09 · 示例输出入库 + 文档数字漂移检查(tools/example-drift,ROADMAP P3 #10 一项)

- 先量静态绑定:引用示例的文档段落共 1243 个数,只有 334 个(27%)能在输出里找到 → 不可行。改为差分:
  `examples/expected/` 存 40 个示例的全尺寸输出(计时、仓库路径屏蔽);`tools/example_drift.py compare` 从变了的输出行反查
  能舍入成旧值、舍入不成新值的文档数(段落 / 小节 / 别处三档);`accept` 刷新。坑进 engineering-pitfalls(两条)。
- 试跑:10-08 的输出对当前 main,38 个逐字节同;示例 27 的 sliding 列点到 2 处文档,都是有意保留的历史描述;示例 32 只多了第 3 问。
- CI `examples-full`(改库 / 示例的 PR、main push、每周):4 核约 10 分钟。首跑跨机器 39 / 40 逐字节同,唯一不同是示例 29 的
  C .so 与 numpy 之差(本地 1.73e-18、CI 3.47e-18,COMPARISON 还引用了它)→ 示例改打印上界 < 1e-15。
- 本地全尺寸 4 路并行约 20 分钟(示例 32 用 416 s);CONTRIBUTING、architecture-invariants 速查同步。
- PR #53;CI 全部 test job + examples + rtl-lockstep 绿,`examples-full` 修后 40 / 40 一致(10.5 分钟)。
- 全量(JIT + iverilog):900 passed / 1 skipped(+10);ruff 通过;库代码未动。

## 2026-10-08 · 预加重过激光器曲线(study/preemphasis-vs-laser,ROADMAP P3 #11 一项)

- 上个 PR 只给了过冲的两个口径(不罚 / 无余量)。本模型的激光器限制过冲的是零功率下限与翻转峰;7 种均衡 × ER 4 / 6 × 曲线
  (无 / 仅下限 / c = 0.2)扫 100 m OMA 裕度,另补 c = 0.5:下限几乎不罚(≤ 0.1 dB,即使 0.5–1 % 样本触零),压缩罚 0.3–0.4 dB,
  裕度小时更多(RIN 限制);"无余量"口径过悲观。示例 32 第 3 问加"laser c=0.2"列。
- 统计引擎核对:c = 0.2 带 EQ 1.06–1.35;c = 0.5 + 16 dB + 驱动器 9 dB 悲观 2.5–2.8×。试了两个假设:残差与远处 ISI 反相关(实测不相关)、
  高斯尾部太重(换箱内经验分布只到 2.51,撤回未保留)。原因未查清,记 ROADMAP #11 与 §6。
- 中途更正一处自己的数:c = 0.5 的翻转峰在 u = 3 而不是 1.5(要截 1.8 的过冲需 c ≈ 0.71)。
- 全量(JIT + iverilog):890 passed / 1 skipped(不变);ruff 通过;示例 32 全尺寸与 smoke(294 s)都过;库代码未动。

## 2026-10-08 · LPO 模块驱动器 / TIA CTLE(feat/lpo-linear-eq,ROADMAP P3 #11 一项)

- `OpticalConfig.drv_ctle_db` / `tia_ctle_db`(`afe.Ctle`,直流增益 1,0 = 不插入);驱动器的在 PD 噪声与大信号曲线之前,TIA 的在其后。
  默认值下 804 值指纹与 main 逐位相同;带 EQ 双引擎 0.98–1.01。
- 示例 32 第 3 问(100 m OMA 裕度):TIA CTLE 一律小亏(8 dB 段 5.47 → 5.18、16 dB + FFE 3.92 → 3.29);驱动器 CTLE 与 host Tx FFE 都在
  噪声之前,各自收回大部分(16 dB 段 + FFE 3.92 → 驱动器 6 / 9 dB 7.44 / 9.31)。原"CPO 多 3.1 dB"只在两边都不做发端均衡时成立。
- 自己挑出的一处不公平:先只比驱动器 CTLE 时它看起来能补回 LPO 全部差距;加上 host Tx FFE(模型里本来就有)对照后,结论改成"位置在
  噪声之前",而不是"模块 EQ"。预加重过冲按 TP2 峰峰值 / OMA 给了无余量口径(CPO + FFE 1.80 倍,扣 2.5 dB)。
- 全量(JIT + iverilog):890 passed / 1 skipped(+6);ruff 通过;示例 32 全尺寸与 smoke(168 s)都过。
- PR #51(5bf64cd):test 矩阵、examples、rtl-lockstep、lint、import-clean、vendor-drift 全绿;桌面 pyinstaller / nuitka / nuitka-onefile 全绿。
  Android(run 37806779777)`instrumented totals: 34 tests, 0 failures, 0 errors, 0 skipped`(API 34、编译版 APK;API 35 第一次 34/0 但测试后
  模拟器掉线,卸载 APK 报 `device 'emulator-5554' not found`,Gradle 判失败 —— 基础设施,PR 上留言后重跑一次,34/0 绿)。

## 2026-10-08 · 统计引擎建模大信号曲线对 ISI 的弯折(study/stat-large-signal,ROADMAP P3 #11 一项)

- 先量:四条链路 c = 0…0.5(80 万符号),只用稳态电平时 VCSEL 0.64–1.16 尚在 2× 内,EML 与 MS NRZ 随 c 单调变乐观,
  c = 0.5 读 0.30 / 0.40;NRZ 的统计 BER 对 c 完全不动(两个外电平被钉住)。
- 拆解:曲线在 E/O 动态之后,弯的是已带邻符号 ISI 的波形;无噪声残差按"当前符号 + 两个最近邻"分组解释 92–96 % 方差 ——
  正是光噪声已有的分箱。中途一次对错齐(`pre_pd` 与 `drive * optics` 延迟不同)读出 3.7× 半间距的假残差,改用时域引擎自己的链(坑进 pitfalls 测量类)。
- 建:`optical_stage.curve_pattern_offsets`(码型上的精确条件均值 / 方差,逐相位)进分箱平移与箱内方差;24 点 0.86–1.28。
  `cairn/光互联建模.md` §8「统计引擎里的曲线」。曲线关闭时 804 值指纹与 main 逐位相同。
- 第一版手写了 `x[::osr]` 上采样,铁律 5 的 `test_domain_boundary` 抓到;改走 `sampler.hold` / `baud_samples`,两个抽查点结果不变。
- 全量(JIT + iverilog):884 passed / 1 skipped(+4);ruff 通过;新测试 HALO_NO_JIT 下 40 s。
- PR #50(d1ee95d):test 矩阵、examples、rtl-lockstep、lint、import-clean、vendor-drift 全绿;Android(run 37785538977)
  `instrumented totals: 34 tests, 0 failures, 0 errors, 0 skipped`(API 34、API 35、编译版 APK 三处);桌面 pyinstaller / nuitka /
  nuitka-onefile 全绿(run 37785538929)。

## 2026-10-08 · CONTRIBUTING.md 与数值指纹工具(docs/contributing,ROADMAP P3 #10 一项)

- `CONTRIBUTING.md`:只放命令与指针(铁律、坑、证据要求都指回 `AGENTS.md` / `cairn/`),CI 表按 workflow 文件逐项核对过触发路径。
- "重构用数值指纹证明"此前没有入库工具,一直靠会话草稿里的脚本 → 入库为 `tools/fingerprint.py`(record / compare,804 值,约 1 分钟)。
- **更正**:入库时发现草稿脚本调 `compute_com` 参数顺序错,基线与改后都记成同一个 `TypeError` —— 此前各 PR 写的"指纹逐位同"**不含 COM**。
  工具现在单独列出报错条目;坑进 engineering-pitfalls(API 类,挨着"吞掉异常"那条)。两次独立 record 逐位相同(工具本身确定)。
- 顺带更正:ROADMAP 记"LICENSE 尚缺"不实(MIT,2026-09-12 起就在);invariants 实践指南的耗时 / ruff 路径 / Android job 数 / 测试数守护四处过时。
- 全量(JIT + iverilog):880 passed / 1 skipped(+4);ruff 通过;库代码未动。

## 2026-10-08 · 四光标 PR 目标:≈ +0.5 dB,暂不做(study/pr-four-cursor)

- ROADMAP #8 余项"更长的目标":先估增益,事先定门槛 < 0.5 dB 不做。`tools/pr_target_length.py`:示例 38 光标上的基带 MC
  (联合 MMSE FFE + 首一目标,库 Viterbi 跑 FFE 实际光标),一个噪声比例(×1.75)拟合 delta 的实测 31.6 dB。
- MC 高估前两档(+5.58 / +8.45 对实测 +4.7 / +7.0,比 0.84 / 0.80);第四光标原值 +0.65 dB,折算 ≈ +0.52,三种子 0.47–0.55;
  第五个再 +0.4–0.6(原值)。残余 memory 1 ≈ memory 2,同 256 状态也有这些:代价在每层多带一个光标,不在网格。
- 判断:恰在门槛上,门槛分不出,按成本暂不做;ROADMAP 移入"边界"并写重开条件。`cairn/DSP发端与PR.md` §12;
  坑两条进 engineering-pitfalls(MLSD 类:一个噪声比例校准不了 PR 增益;Viterbi 回溯表的内存)。库代码未动。
- 全量(JIT + iverilog):876 passed / 1 skipped(+3);ruff 通过;新测试在 HALO_NO_JIT 下 6 s。
- PR #48;CI test(jit / nojit × 3.10 / 3.11)+ rtl-lockstep + examples 全绿;Android 与打包 workflow 按路径过滤未触发(未动共用层,铁律 6 不涉及)。

## 2026-10-08 · 系统框图由示例 03 的运行生成(docs/system-diagram)

- `tools/draw_system_diagram.py` 跑示例 03,图上每个数取自那次运行;同时写 `.mmd` 并刷新 summary.html 的 base64 副本。
- 重画时又发现两处手抄错:CDR Kp 写成 1/32(配置是 kp_shift 6 → 1/64)、CTLE 写 "6–9 dB"(配置峰值 6 dB,实际 3.1 dB)。
- 写测试时抓到工具自己的一个 bug:按"后面 2000 字符里有图注"找内嵌图,会选中图注前面那一张;改为图注须在下一个 `<img>` 之前。
- 全量(JIT + iverilog):873 passed / 1 skipped(+2);ruff 通过;库代码未动。

## 2026-10-08 · 修正:sliding 检测器不修正接收机的判决(fix/sliding-detector)

- 复核时记下的观察(示例 27 里 sliding 不如 DFE)查清是 bug:`post_detect` 丢掉传入的判决,从忽略后光标的切片起步,单翻转修不了成对错误。
- 更严重的在引擎:PR 1 + 0.5D + sliding 让 SER 2.1e-5 → 3.3e-2(浮点 / 定点,定点注释写着"与浮点一样从切片起步")。无 PR 时看不出
  (y 已减过 DFE,切片 ≈ 判决),所以指纹与示例 30 都没照到。修:`post_detect(dec0=...)`,引擎 / 定点传自己的判决;RTL 向量生成器早就用反馈判决。
- 三光标 PR + sliding 修前修后都坏(单后光标模型),改为配置报错 → viterbi。修后示例 27:DFE > sliding > Viterbi 每个 σ 成立。
- 回归测试 4 条(旧代码全挂、新代码全过);坑进 engineering-pitfalls(API 类)。指纹逐位同,lockstep 七段过,nojit 过。
- PR #46;CI 全部 test job + rtl-lockstep + examples + pyinstaller 绿;Android instrumented 34 tests / 0 failures(API 34、35、编译版 APK)。
- 全量(JIT + iverilog):871 passed / 1 skipped(+4);ruff 通过。

## 2026-10-08 · 示例全尺寸复核(fix/example-numbers,ROADMAP P3 #10 一项)

- 40 个示例按原尺寸全跑(3 路并行),全部 rc=0;5 个子代理分批把输出与现行文档(README / USAGE / SUMMARY / COMPARISON /
  summary.html / clock_profile / cairn 专题,不含 CHANGELOG / LOG 等历史记录)逐条对照,我逐条核对后修改,约 40 处。
- 最大的一处是结论性的:级联 FEC "300 倍" 实为 31 倍(KP4 门限 2.2e-4 不是 2.2e-5;用示例 20 自己的公式复算)。
  其余多是重跑后没同步的 docstring / 表体 / summary.html,以及两条已失效的能力描述(PR 整形 "未建模"、发端 "[PR 占位]")。
- 示例 39 与昨天的运行逐字节相同。未改:时间类数字(非种子化,且并行运行有争用);示例 27 里 sliding 检测器在所有 σ 下都不如
  理想 DFE —— 文档没有声称相反的排序,记为观察,未深究。
- `tools/run_examples.py` 加 `--save` / `--jobs`(带测试)。PNG 系统框图不是由 .mmd 生成,未重画,图下注明新值。
- 全量(JIT + iverilog):867 passed / 1 skipped(+1);ruff 通过;库代码未动(只改 tools、示例 docstring 与文档)。

## 2026-10-08 · GUI 文档与覆盖 + ADC 统计结果没套 FFE(feat/gui-docs-coverage,ROADMAP P3 #10 一项)

- 基线复现:GUI 行覆盖 61%(ROADMAP 记 60%);docstring 按函数 / 类 / 模块含私有计 37%(ROADMAP 的 28% 口径不明,同口径核心库 65%)。
- 新 `tests/test_gui_panels.py` 在真实记录上渲染每个标签页并断言用户该读到的文字;回调经 `__wrapped__` 直接调。覆盖 61% → 89%。
- 顺带发现并修了两个 bug:① app 层裸调统计引擎,ADC 收端无 FFE —— 106 GBd 预置 StatEye 0.16 与时域 2.3e-4 并排(双引擎页即铁律 3 的界面)。
  `runner.stat_equaliser` 统一处理,修后 2.40e-4 vs 2.29e-4;坑进 engineering-pitfalls(API 类)。② ADC 页 mismatch 图单位标错(V 标成 code、样本标成 UI)。
- docstring:GUI 100%、app 层 88%(剩 studies 的 9 个 `compute` 闭包)。浮点 / TX 指纹逐位同(库层未动)。
- PR #44;CI 全部 test job + rtl-lockstep + examples + pyinstaller 绿;Android instrumented 34 tests / 0 failures(API 34、35、编译版 APK)。
- 全量(JIT + iverilog):866 passed / 1 skipped(+10);ruff 通过。

## 2026-10-08 · TDECQ 的 802.3dj 参考 DFE(feat/tdecq-dfe,ROADMAP P3 #11 一项)

- `tdecq(dfe=True)`:FFE15 + DFE1,0 ≤ b ≤ 0.3、FFE 和 = 1 + b、OMA / 阈值在 FFE 输入、判决用发送符号、C_eq 只算 FFE。
  条文:IEEE / Keysight 原文被出口代理拦截(403,未重试),按 dj 意见处理材料的检索摘要转述,cairn/光互联建模.md §8 标"二手"。
- 闭式:单后标 h → 10·log10(1 + h)、b = h(差 ≤ 0.002 dB);h = 0.5 钳到 0.3;负后标 b = 0 与只 FFE 逐位同。
- EML 200G/λ(示例 34):基线 2.08 → 1.88 dB,DFE 省 0.09(80 GHz)… 0.93 dB(25 GHz),最小带宽 34.59 → 29.83 GHz。
  与材料里"约 0.5–1 dB"同量级。研究页 ≥ 80 GBd 自动带 DFE。
- `dfe=False` 与 main 逐位同(worktree 对照三个场景);浮点指纹逐位同;test_tdecq nojit 18 passed。
- PR #43;CI 全部 test job + rtl-lockstep + examples + pyinstaller 绿;Android instrumented 34 tests / 0 failures(API 34、35、编译版 APK)。
- 全量(JIT + iverilog):856 passed / 1 skipped(+5);ruff 通过。

## 2026-10-07 · 前台校准 + 校准字分辨率(feat/adc-foreground-cal,ROADMAP P3 #7 余项 → #7 删除)

- `adc.cal.mode: foreground`:上电经各 lane 自己的量化器 / 噪声测短接与 ±参考,修正冻结、折进量化器前(近似);单独随机流,实例不变。
  有 ENOB 噪声时 1024 次即到理想 26.90 dB;无噪声停在 q/√12(29.21 vs 理想 29.74,后台 29.55)。示例 39 第三部分、USAGE §19。
- 分辨率扫描暴露 bug:定点 gain LMS 对公共增益无观测,舍入噪声随机游走,9–10 位时漂到 0.7–0.85。修:生效增益 = 寄存器 − 平均 + 1,
  Python / replay / SV 同一份,`vectors/cal` 重生成。修后单调:gain ≥ 9 位、offset 小数 ≥ 2 位饱和。坑进 engineering-pitfalls(建模约定类)。
- 旧测试断言 "foreground 非法" 随之改为未知模式;表单(GUI / Android)加 foreground 与两个字段。
- PR #42;CI 全部 test job + rtl-lockstep + examples 绿;Android instrumented 34 tests / 0 failures(API 34、35、编译版 APK;编译版首跑在 sdkmanager 下载模拟器包时失败、未进构建,重跑通过)。
- 全量(JIT + iverilog):1 failed / 850 passed / 1 skipped —— 失败的是旧断言("foreground" 非法),改后单独重跑通过;其后的表单改动跑了 bridge / form 测试 52 passed → 851 passed / 1 skipped(+6);定点校准 nojit 10 passed / 4 skipped;浮点指纹逐位同;lockstep 七段过;ruff 通过。

## 2026-10-07 · 定点 / RTL 的 skew 修正(feat/fixed-skew-cal,ROADMAP P3 #7 阶段 4 → #7 收尾)

- 每 lane 整数延时修正寄存器(相位寄存器单位),MM 鉴相器在校准后字上驱动;PI 码 = `(ph − (cts[l] − Σcts >>> lane_shift)) >>> pi_sh`,
  生效值零均值精确到 LSB(lane 数 2 的幂,求平均是移位)。闭环 / replay / SV 同一语义;`vectors/cal` 加 0.04 UI skew,末态修正寄存器比对。
- 定点 vs 浮点(三种失配、2×10⁵ 符号末 1/4):`mu_skew` 2⁻⁷ / 2⁻⁸ / 2⁻⁹ 差 0.40 / 0.21 / 0.15 dB;定点 skew 残差 0.019 / 0.009 / 0.005 UI。
- 校准在浮点 / 定点 / RTL 三层全部闭环。会话中途重连杀掉过一次后台全量,重跑。
- PR #41;CI 全部 test job + rtl-lockstep + examples 绿;Android instrumented 34 tests / 0 failures(API 34、35、编译版 APK);Windows 三种打包成功。
- 全量(JIT + iverilog):845 passed / 1 skipped(+2);定点 / 校准 nojit 45 passed / 9 skipped;浮点指纹逐位同;ruff 通过。

## 2026-10-07 · 定点 / RTL 的 ADC 后台校准(feat/fixed-adc-cal,ROADMAP P3 #7 阶段 3)

- `_cal_word_py`:ADC 字 → 校准 → FFE,闭环与 replay 同一函数;offset 是浮点那份的整数版,gain 改成硬件形式的 LMS(功率拉向各 lane 平均,
  无开方 / 除法),平衡点同浮点、动态不逐位同。记录里 `xin` 改为原始 ADC 字(RTL 输入),`x_cal` 为校准后。
- 坑:第一版用"右移 62"表示步长 0 —— 负数算术右移 62 得 −1,寄存器每次往下漂;真值冻结只有 21.6 dB。改为负移位 = 关闭 + 舍入移位后 26.65 dB。
  (先冻结真值再看学习,一步就定位到了修正通路。)
- 定点 vs 浮点(2×10⁵ 符号末 1/4):2⁻⁸ / 2⁻¹⁰ / 2⁻¹² 下差 0.13 / 0.18 / 0.19 dB。
- SV:Icarus 12 在 automatic task 把 output 写进数组元素时段错误、task 里不许 return → 校准内联进 run()。lockstep 七段全过,末态寄存器逐位同;
  原有向量只多了 CAL 参数(golden 不变)。
- PR #40;CI 全部 test job + rtl-lockstep + examples 绿;Android instrumented 34 tests / 0 failures(API 34、35、编译版 APK)。
- 全量(JIT + iverilog):843 passed / 1 skipped(+11);定点 / 校准 nojit 44 passed / 8 skipped;浮点指纹逐位同;ruff 通过。

## 2026-10-07 · 修正:`adc.calibrated` 改变了随机实例(fix/cal-convergence-diag,ROADMAP #7 待查项)

- 用户选"查收敛变慢"。诊断三步:① 修好分窗 SNR(上次只看了 y_slicer 的截断头部;改为拦截 score() 输入)→ 后台 o/g 不是慢,是平台在 23.1 dB;
  ② 冻结真值(mu = 0)仍 23.24 dB → 不是估计;③ 比较抽到的 skew → 理想模型与未校准是两组 skew。
- 根因:`TiAdc` 在 `calibrated=True` 时跳过 offset / gain 抽取,之后的 skew、ENOB 噪声、Rx 时钟全变。改为抽了再置零。
  修后同一链路上后台 o/g 距理想 0.04 dB(4×10⁵)/ 0.05 dB(10⁶)。只影响 calibrated + 非零 sigma 的配置;浮点指纹逐位同。
- **更正**:撤回阶段 2 记下的"有 skew 时 o/g 收敛变慢"(以及更早撤回的"gain 环把 skew 当增益")。"随运行长度变化"也是假象:
  `n_symbols` 改变抽取量,10⁶ 与 4×10⁵ 是不同实例。坑写进 engineering-pitfalls。
- 回归测试两条(改前失败、改后通过);示例 39 第二部分理想值 24.31 → 24.17 dB。
- PR #39;CI 全部 test job + rtl-lockstep + examples 绿;Android instrumented 34 tests / 0 failures(API 34、35、编译版 APK)。
- 全量(JIT + iverilog):832 passed / 1 skipped(+2);ruff 通过。

## 2026-10-07 · TI-ADC 后台校准 阶段 2:skew(feat/adc-skew-cal,ROADMAP P3 #7)

- `adc.cal.mu_skew`:每 lane 的 MM 鉴相器(修正后 ADC 字、外层电平归一)调本 lane 采样延时 `cal[4]`,修正量零均值(公共相位归 CDR)。
- 实验先行:2⁻⁸ 时残差 0.008 UI、相关 0.98、SNR 回到无失配 0.1 dB 内;三种失配一起时 14.21 → 26.70 dB(示例 39 第二部分)。
- **更正**:我一度断言"有 skew 时 gain 环把 skew 当增益"并写进了测试。核实:gain 残差几乎不变(0.39% vs 0.32%)、CDR 锁定点一样 →
  这个解释不成立,已删。真实现象是有 skew 时 offset / gain 环收敛明显变慢(4×10⁵ 符号差理想 2 dB,10⁶ 时 0.2 dB),机理未确认,记入 ROADMAP #7。
- `mu_skew` = 0(默认)时阶段 1 逐位不变;浮点指纹逐位同。
- PR #38;CI 全部 test job + rtl-lockstep + examples 绿;Android instrumented 34 tests / 0 failures(API 34、35、编译版 APK)。
- 全量(JIT + iverilog):830 passed / 1 skipped(+2);test_adc_cal nojit 5 passed / 4 skipped;ruff 通过。

## 2026-10-07 · TI-ADC 后台校准 阶段 1(feat/adc-calibration,ROADMAP P3 #7)

- 用户选 P3 #7。`adc.cal.mode: background`:内核里量化后数字修正 (q − ô)·ĝ;offset = lane 滑动均值,gain = 各 lane 平均功率 / 本 lane 功率 开方。
- 坑 1:功率估计从 1.0 起,小步长时 gain 半天不动 → 改从 0 起(比值从一开始就对),满 1/mu 次转换前 gain 保持 1。
- 坑 2:我自己的分窗 SNR(y_slicer 对 decisions 逐窗)算错,给出"越校越差"的假象;换成引擎 SNR + `warmup_discard` 后结论正常。教训:诊断量先对照引擎自己的定义。
- 结果(示例 39,10⁶ 符号、末 1/4):未校准 14.34 dB → 2⁻¹⁰ 26.05 / 2⁻¹² 26.80 dB,理想 26.90 dB;残差约随 √mu 缩小。表见 USAGE §19。
- 真值冻结(mu = 0)= 理想模型,说明修正通路本身无误;关闭时浮点指纹逐位同。
- 未做:skew 校准、定点 / RTL 校准字(定点开校准报错)→ ROADMAP #7。
- PR #37;CI 全部 test job + rtl-lockstep + examples 绿;Android instrumented 34 tests / 0 failures(API 34、35、编译版 APK)。
- 全量(JIT + iverilog):828 passed / 1 skipped(+9);test_adc_cal nojit 5 passed / 2 skipped;ruff 通过。

## 2026-10-07 · 示例冒烟运行(feat/example-smoke,ROADMAP P3 #10 的一项)

- 用户选 P3 #10。`tools/run_examples.py --smoke`:子进程里包一层 `SimConfig.__init__` 把 n_symbols 压到 2 万再 runpy 跑示例 ——
  只动测试工具,不给库加全局开关(铁律 #1)。
- 首跑 39/39 通过,~6 分钟(最慢 34_tdecq 84 s)→ 放进每次 push 的 CI,不必按原计划做成夜间 job。
- 顺带发现:README 快速开始的两个示例名早已失效;加了"文档提到的示例路径必须存在"的测试(改之前失败、改后通过)。
- 局限:冒烟不验证数字;文档引用的数值仍需按原尺寸手动跑。
- PR #36;CI 全部 test job + rtl-lockstep + 新 `examples` job(CI 上 5 分钟)绿。没改共用库层,Android / Windows 工作流按路径过滤未触发(铁律 #6 不涉及)。
- 全量(JIT + iverilog):819 passed / 1 skipped(+2);ruff 通过。

## 2026-10-06 · 流式覆盖光 / 串扰 / AMI Init / 抖动分解(feat/stream-coverage,ROADMAP P2 #2 → P2 清空)

- `engine/stream.py`:光路两段 / 三段(FIR + 逐样本光噪声 + E/O 曲线)、攻击者(保持 + 耦合 FIR)、接收节点加噪前抽头。
- 随机数:光噪声与接收噪声都拷贝生成器读默认引擎同一批抽样,主生成器按序跳过 → 同一链路实例。
- `analysis/jitter.py`:`CrossingCollector` 分块收过零点,与整段逐位同(含 -0.0/+0.0 的 NaN 穿越,见 engineering-pitfalls)。
- AMI GetWave 不流式,移入 ROADMAP「边界」;Init 流程可以。
- 实测光链路 10⁶ 符号:默认 0.77 GB / 33.7 s → 流式 0.16 GB / 4.1 s,误码 17257 vs 17262(USAGE §14)。
- PR #35;CI 全部 test job + rtl-lockstep 绿;Android instrumented 34 tests / 0 failures(API 34、35、编译版 APK)。
- 全量(JIT + iverilog):817 passed / 1 skipped(+9);stream/jitter/crosstalk nojit 40 passed / 1 skipped;浮点指纹逐位同。

## 2026-10-06 · 定点 Viterbi(feat/fixed-viterbi,ROADMAP P1 #1 收尾 → P1 清空)

- `dsp/fixed_viterbi.py`:期望字表 `rnd(Σ c_i L[x_i], fl)`、分支度量 `e² >> sq_shift`、饱和、每步减最小值、全程回溯;
  加比选顺序(前状态升序、符号升序、严格小于才换)与浮点核相同 → 精确输入上逐符号一致。
- 独立参考按"显式符号历史 + 字典"写,覆盖 memory 1/2、非均匀电平、移位与饱和;窄度量(22 位)在 5 万符号上与宽度量同判决。
- 引擎:定点 + viterbi 时在环路 slicer 字上跑整数版(此前浮点)。SV 第六段 16 状态通过。
- 未建模:固定回溯深度(硬件会截断);写进 USAGE 与模块 docstring。
- P1 清空;剩 P2 #2(流式覆盖光 / 串扰 / AMI / collect_jitter)与 P3。
- PR #34;CI 全部 test job + rtl-lockstep 绿;Android instrumented 34 tests / 0 failures(API 34、35、编译版 APK)。
- 全量(JIT + iverilog):808 passed / 1 skipped(+13);定点 MLSD / Viterbi / RTL nojit 24 passed / 2 skipped;浮点指纹逐位同。

## 2026-10-06 · 定点 PR(feat/fixed-pr,ROADMAP P1 #1 的 PR 部分)

- `_digital_step` 加 PR(lp[11..18] + `pr_lv`、`prs`、`xl`、`r_out`),逐行对照浮点核 pr_mode 1 / 2:DFE 用 x̂、从 s − nt 起;
  训练时 dec = x̂ = ref;误差三分支(delta / 合成 / 减光标);a 只在 FFE 或 DFE LMS 开着时才动(浮点核同);PD 读 r。
- 顺带对齐:定点 sliding 的 dec0 改为平铺切片(浮点 `post_detect` 就是这么做的,此前用的是环路判决,训练段不同)。
- SV:`%` 对负数保号(Python 取非负),合成判决写成 `((x % N) + N) % N`。
- 观察:宽测试用的 a 步长 2e-2 下,a 在 20k 符号里随机游走 0.43 → ~0.32(定点 / 浮点都如此,等效步长比 0.90),
  误码跟着各自游到哪(117 vs 42)—— 是步长问题不是字长;默认步长 2e-4 下两者误码 5 vs 8。
- 剩余:定点 Viterbi → ROADMAP P1 #1(已改写)。
- PR #33;CI 全部 test job + rtl-lockstep 绿;Android instrumented 34 tests / 0 failures(API 34、35、编译版 APK)。
- 全量(JIT + iverilog):795 passed / 1 skipped(+17);定点 nojit 41 passed / 2 skipped;浮点指纹逐位同。

## 2026-10-06 · 定点训练 + 整数 LMS(feat/fixed-lms,ROADMAP P1 #1 的自适应部分)

- `_digital_step` 加训练(s < train_len 且 ref ≥ 0 用参考判决)与 LMS(与浮点核同序:判决后、PD 前);参数打包成 `lp` 数组。
  步长 2 的幂:`sh = round(log2(mu · lsb_e · lsb_x · 2^(fl+G)))`;默认 mu 5e-5、G 24 时 FFE sh ≈ -5,G 16 时 ≈ -13
  会把典型更新(|e·x| ~1e3)舍成 0 —— 所以默认 G = 24。
- 定点模式改为从初始权重 + 同一训练日程自适应(不再冻结浮点终值);`adapt: none` 时两者相同。
- 宽字长 + 大步长(mu 1e-2):权重行程 1.5e-3,与浮点 LMS 差 3.4e-6(0.2%);slicer 值差 > 10 µV 的 < 0.5%。
- 默认 10 位权重下,MMSE 初值离 LMS 终点不到 1 个 LSB(向量里 FFE 权重最多动 1 LSB)—— 自适应在这个字长上几乎只是抖动。
- SV:`ref` 是关键字(Icarus 报 "Syntax error in variable list"),改名 `ref_sym`;tb 增比最终权重。
- 剩余:定点 Viterbi、定点 PR → ROADMAP P1 #1(已改写)。
- PR #32;CI 全部 test job + rtl-lockstep 绿;Android instrumented 34 tests / 0 failures(API 34、35、编译版 APK)。
- 全量(JIT + iverilog):778 passed / 1 skipped(测试数不变,扩展了现有测试);定点 nojit 24 passed / 2 skipped;浮点指纹逐位同。

## 2026-10-06 · 定点 sliding MLSD + RTL 对照(feat/fixed-mlsd,ROADMAP P1 #1 的 MLSD 部分)

- `dsp/fixed_mlsd.py`:逐电平反馈表 `fbt[m] = (rp·L[m] + ½) >> fl`;假设翻转按定义重算 e[k]、e[k+1](浮点版减的是
  均匀 step 的签名;取整后电平不再严格均匀,两者不等,重算才让"接受后的残差流"与从头算一致)。
- 校验:浮点运算精确的输入(电平 4 的倍数、r = 0.25)上与浮点 `sliding_detector` 两遍逐符号一致;独立参考(每个假设整窗重算)
  覆盖非均匀电平、sq_shift、饱和、margin。
- 发现:真实 ADC 链路上 MMSE FFE 把第一后光标几乎全吃掉(`resid_ratios[0]` 量化后 rp = 0),sliding 一次都不翻 —— 浮点也一样
  (ser == ser_slicer)。所以 SV 向量用合成强残差流(r 0.45,291 → 254 错,81 次翻转)。
- 剩余:整数 LMS、定点 Viterbi → ROADMAP P1 #1(已改写)。
- PR #31;CI 全部 test job + rtl-lockstep 绿;Android instrumented 34 tests / 0 failures(API 34、35、编译版 APK)。
- 全量(JIT + iverilog):778 passed / 1 skipped;定点 / MLSD nojit 35 passed / 2 skipped;浮点指纹 506 + 44 值逐位同。

## 2026-10-06 · 定点 CDR 闭环 + RTL 对照(feat/fixed-cdr,ROADMAP P1 #1 的 CDR 部分)

- `dsp/fixed_loop.py`:数字后端 + MM CDR 全 int64。输入字 2c+1(保住中点量化的半 LSB;旧 `fixed_datapath` 喂 c,丢了它)。
  增益 = 移位:`kp_tot = kp_shift + round(log2(L/x_lsb))`,相位寄存器 2^-(pi_bits+phase_frac_bits) UI,每符号加 `c >>> log2 L`。
- 关键结构:闭环里每符号调用同一个 `_digital_step`,开环 `replay_digital` 也调它 → 从记录的 ADC 字重放与闭环逐位同。
  RTL 只需实现"ADC 字之后",模拟侧已在输入里。
- `numeric.mode: fixed` 首次生效:浮点训练 → 冻结量化权重 → 定点闭环重跑;mixed-signal / PR / stream / 非 2 幂 lane 报错。
- SV:`rtl/adc_dsp_loop.sv` 写成 task(Icarus 12 对这么大的 always_comb 断言 `vvp_fun_anyedge_sa`)。向量用 Rx SJ 0.12 UI
  + kp 3 / ki 7 + 钳位 + 32 符号时延:PI 码 -4..2;更大的 SJ 浮点与定点环路都跟不上(SER ~8–10%,两者一致)。
- 收敛:pi_bits 16、权重 32 位、slicer 44 位时与浮点 ADC 内核相位差 < 1e-3 采样、判决差 < 1e-3,
  ADC 字只在 PI 步把采样推过量化门限处偶有 ±1(< 0.1%)。默认字长误码与浮点同量级。
- 剩余:定点 MLSD、整数 LMS → ROADMAP P1 #1(已改写)。
- PR #30;CI 全部 test job + rtl-lockstep 绿;Android instrumented 34 tests / 0 failures(API 34、35、编译版 APK)。
- 全量(JIT,本机装了 iverilog,lockstep 两项也跑):767 passed / 1 skipped;定点相关 nojit 21 passed / 1 skipped;浮点指纹 506 + 44 值逐位同。
- 顺带:仓库里提交的 `rtl/vectors/` 是很早(90bd81f)生成的旧向量,main 现在重生成就与之不同(已用 main 的 worktree 验证,与本分支一致),这次一并刷新。

## 2026-10-05 · 流式时域引擎 sim.stream(feat/waveform-windowing,原 ROADMAP P1 #2)

- 取舍(用户选定):默认引擎的噪声砖墙与 `tx.bw` 单极点是整段循环 FFT,不可能流式且逐位同;所以加**可选**流式模式,
  默认路径一字不动(指纹 506 + 44 值逐位同)。流式里两者换 FIR(噪声:同一批白噪 + Kaiser sinc,居中;单极点:`driver_response`)。
- `engine/stream.py`:ZOH 窗口(= `jittered_zoh` 的区间限制,逐位同,含 ppm 斜坡与交叉边沿)、固定块 overlap-save `Fir`、`Lead`、
  `drive_stream`(块级扩窗,保留下一采样点前 8 UI)。内核 core 加 `y_off` / `n_total` 与状态码 2(要更多)/ 3(已丢,报错)。
- 链路主 RNG 跳过噪声抽样(`skip_normals`,分块抽取与整段抽取逐位同,已验),比较器失调 / Rx 时钟 / ADC 失配与默认同 →
  同一链路实例。噪声 FIR 不居中时与默认噪声不相关(眼图差 √2σ),居中后差 0.06σ。
- 实测(同机同口径 RSS):10⁶ 符号 OSR32 默认 1.89 GB / 16 s,流式 0.33 GB / 5 s;5×10⁶ @OSR16 流式 13 s、tracemalloc 0.70 GB。
  ROADMAP 原写"0.9 GB"是另一配置 / 口径,USAGE §14 已注明,未覆盖原值。
- 新 `tests/test_stream.py`(15 项):ZOH 窗口逐位、FIR 读法无关、噪声 FIR 带宽与能量、流式分块逐位(两架构含 tx.bw)、
  流式 vs 默认统计一致(两架构 × 有无极点)、眼图、不支持路径报错、5×10⁶ 冒烟(needs_jit)。
- 剩余:光 / 串扰 / AMI / collect_jitter 的流式 → ROADMAP P2 #2。
- 全量(JIT):752 passed / 2 skipped(+15);流式 + 分块测试 nojit 19 passed / 1 skipped(5×10⁶ 冒烟 needs_jit)。PR #29;Android instrumented 34 tests / 0 failures(API 34、API 35、编译版 APK,代码提交 06c6558 上)。

## 2026-10-05 · 接收机内核分块续跑(feat/chunked-kernels,原 ROADMAP P1 #3)

- 两个内核拆成 `[k0, k1)` 的 core + `MsRxRun` / `AdcRxRun`;状态:MS = 位置、积分器、求和节点反馈、上一符号符号 + LMS 批计数、
  抽头历史行 + `w` / `corr`;ADC = 位置、PD 累加、积分器、当前修正、a、b + PD 块计数、队列下标 + `wf` / `wd` / 延迟队列。
  输出数组本身就是回看历史(dec、xl、q_hist…),预分配全长。`ms_rx` / `adc_rx` 改为调一次 core 的包装,签名不变。
- 验证:冻结旧核对照(test_rx_clock)、JIT == Python(test_pr / test_cdr_kernel)、新 `test_chunked_kernels`(整段 vs 997 vs 1 符号一块,
  两架构,ADC 含 PR + LMS a;波形中途耗尽;进度序列;回调抛异常即取消)JIT 与 nojit 都过;预设指纹逐位同(默认分块已实际生效)。
- App:progress(stage, fraction);poll 加 `fraction`;新测试:chunk 2048 的 precise 跑能 poll 到 0 < fraction < 1、其后 cancel → cancelled。
  Android:确定进度条 + 通知百分比 + 取消文案;runner / api / Kotlin 注释里"内核不可中断"的旧判断都改写并注明更正。
- 内存(P1 #2)没动:内核仍读整条 `rx_y`。ROADMAP #2 已改写为"剩下的一半"。
- 全量(JIT):737 passed / 2 skipped(+5 分块、+1 API 取消);PR #28。CI 全绿;Android instrumented 34 tests / 0 failures(API 34、API 35、编译版 APK 三个模拟器 job 均过)。

## 2026-10-05 · Android edge-to-edge + API 35 模拟器(fix/android-edge-to-edge,原 ROADMAP P2 #6b)

- `enableEdgeToEdge()`(API 34 也按 15 的方式布局)+ Scaffold 内容 `consumeWindowInsets(pad).imePadding()`(键盘成 inset 后表单仍可滚到焦点框)。
- 测试断言的是**内容**:Material `TopAppBar` / `NavigationBar` 有意把底色铺到系统栏后面、只给内容加 padding,量 bar 自身边界会误报;
  所以量标题文字(状态栏 inset 之下)与 "Link" Tab 标签(导航栏 inset 之上),窗口坐标。新加 TestTags TOP_BAR / NAV_BAR。
- android.yml 解释型 emulator job 改 matrix [34, 35](fail-fast: false),artifact 名带 api 后缀;actionlint 干净。
- 本地无 Android SDK,Kotlin 编译与两档模拟器结果由 CI 判定;`consumeWindowInsets` 是否仍是 ExperimentalLayoutApi 不确定,已 OptIn。
- 首跑(6e1c410):编译过,新测试过;API 34 上 `runningTheStatisticalEngineDrawsABathtub` 又 180 s 超时 —— 第三次(#16、#24、#27)。
  根因判断:按钮 `enabled = s.ready`(还要求 `channelIssue == null`、无字段错误),启动探预设期间可能无 spinner 但按钮未启用,
  测试只等了文字与 spinner,点到禁用按钮被静默丢弃。修:点击前 `waitUntil(hasText(...) and isEnabled())`。
  (6e1c410 上 push 触发的那次同代码 Android run 是全过的 —— 间歇性,不是本 PR 的布局造成。)
- 修后(c76ab83):push 与 pull_request 两次 Android run 全过 —— 解释型 API 34 ×2、API 35 ×2、编译型 ×2;
  API 35 `instrumented totals: 34 tests, 0 failures, 0 errors, 0 skipped`(含新测试);测试 CI 全绿。

## 2026-10-05 · 解释型 APK 排除 halo_serdes_gui(fix/apk-drop-gui,原 ROADMAP P2 #6c)

- Chaquopy 源集加 `exclude("halo_serdes_gui/**")`;只影响解释型(编译型 wheel 只打 halo_serdes / halo_serdes_app)。
  确认 `src/halo_serdes`、`halo_serdes_app`、`android/.../python` 无 import 它;桌面端的 shim 不在 APK 范围内,不受影响。
- `inspect_apk.py --absent`(假 APK 两例:含 → exit 1,不含 → exit 0);android.yml 两个 gate 都加。actionlint、ruff 干净。
- Chaquopy 16.1 认 `exclude`:CI(2b89202,run 37315518172)解释型 APK gate `ok halo_serdes_gui: not in this APK`
  (halo_serdes 72 / halo_serdes_app 5 个源文件照常);编译型 gate 同过。两型模拟器
  `instrumented totals: 33 tests, 0 failures, 0 errors, 0 skipped`;测试 CI 全绿。

## 2026-10-05 · 桌面版随版本 tag 发 GitHub Release(ci/desktop-release,原 ROADMAP P2 #5)

- `build-windows.yml` 加 `release` job(ubuntu,`contents: write`,`gh release create --generate-notes --verify-tag`):
  onefile exe + PyInstaller zip;tag 必须等于 pyproject 版本;onefile 失败时只发 zip(`if` 显式写状态,隐式 success() 会整体跳过)。
- 坑:`push` 只写 `tags` 会让分支推送不再触发,所以加 `branches: ["**"]`;路径过滤对 tag 推送不生效。
- onefile `--product-version` 从 pyproject 读(CI + 本地 ps1)。actionlint 干净。
- 已验证(分支推送,run 37304439562,9706b05):`branches` 过滤生效(推送照常触发),pyinstaller / nuitka / nuitka-onefile 全过,
  `release` = skipped;测试 CI(8347a74)全绿。未验证:release job 本身要推 tag 才会跑(会真的发 Release,由用户决定)。

## 2026-10-05 · COM:ADC 架构的 178A 式 Rx FFE + 量化噪声(feat/com-rx-ffe,原 ROADMAP P2 #6)

- `ComParams.rx_ffe`("auto":ADC 取 `rx.ffe` 大小,mixed-signal 无);每候选相位 MMSE 解抽头(DFE 覆盖的后光标不计、噪声与串扰经抽头计入),
  ISI / 串扰 / 噪声 / 抖动斜率都在 FFE 后取。ADC 的 σ_N 加 `adc_noise_sigma`(与统计引擎同式)。
- 预设 COM:mixed-signal 逐位不变;ADC 106 GBd −8.35 → 4.58、112 stress −7.15 → 5.42、deep-LR −8.99 → 4.78、TI −6.23 → 9.26、LPO −9.41 → 19.78 dB。
  只加 FFE 不加量化噪声时 106 GBd 读 17.3 dB —— 量化噪声是大头。示例 26 −5.40 → −1.96、示例 28 无串扰 2.76 → 15.01 dB。
- 坑:无接收噪声时 MMSE FFE 退化成迫零,COM 不再随损耗单调(test_com 的配置原 noise 0,补 1 mV)。旧指纹脚本调 `compute_com` 签名错,
  从未覆盖 COM —— 本次另跑逐预设对照。invariants #2 补注(按架构分参考接收机不破坏公平)。
- 验证:ruff 干净;jit 731 passed / 2 skipped。CI(7bab7eb)测试矩阵、lint、import-clean、rtl-lockstep、vendor-drift 全绿;
  Android CI(cc899da,run 37257274811,含全部代码改动):解释型 `instrumented totals: 33 tests, 0 failures, 0 errors, 0 skipped`;
  编译型首跑 `UiRenderTest#runningTheStatisticalEngineDrawsABathtub` 180 s 超时(统计引擎路径,不经 com.py;#16 曾同样超时一次),
  重跑一次后 33 / 0 failures,PR 上留言说明。

## 2026-10-05 · AMI 模型 `has_getwave` 默认值统一(fix/ami-getwave-default,原 ROADMAP P2 #4)

- 选 (a):`AmiCModel` / `load_ami_model(so_file=...)` 默认 False → True,与 `NativeFirAmi`、`IbisAmiModel` 同;`NativeFirAmi(has_getwave=)`
  构造参数,`load_ami_model(has_getwave=)` 对三种都生效(厂商 `GetWave_Exists` 为假仍走 Init)。基类文档写明"比较须同流"。
- 行为变更:不显式指定的 `AmiCModel` 旧调用改走 GetWave;仓库内调用都显式或只调 Init,示例 29 输出逐字同(Init 15.370 / GetWave 12.780 dB)。
- 新测试 `test_every_model_defaults_to_the_same_flow`(main 上红)。pitfalls 条目改写,旧默认保留。
- 验证:ruff 干净;jit 727 passed / 2 skipped。CI(484fcc1)测试矩阵、lint、import-clean、rtl-lockstep、vendor-drift 全绿;
  Android CI(77b9b57,run 37251407880,含全部代码改动):`instrumented totals: 33 tests, 0 failures, 0 errors, 0 skipped`,解释型与编译型 APK 都过。

## 2026-10-05 · 统计引擎建模 PR 逐符号判决(feat/pr-symbol-decisions,原 ROADMAP P3 #8 余项)

- `pr_symbol_decisions`:最近一或两个判决误差的马尔可夫链(受控光标 × 误差平移 PDF,平稳分布取 SER);预编码 a = 1 走合成电平。
  `extras["ser_slicer"]`;`mlsd: none` 时即 BER。对 numpy 逐符号判决 MC:7 种情形都在 10 % 内。
- 时域 / 统计(`mlsd: none`,示例 18 信道 0.24 m、`enob=None`):a 0.5 / 0.75 / 1 / 1 预编码 2.66 / 2.98 / 4.09 / 1.21× → 1.64 / 0.83 / 1.03 / 0.97×;
  Viterbi 下 `ser_slicer` 0.84–1.64×,Viterbi BER 不变。专题 `DSP发端与PR.md` §11;ROADMAP P3 #8 只剩四光标。
- 指纹逐位同 main(506 + 44 值,无 PR 预设)。口径:DFE 仍理想,对 LMS / CDR 的反作用不建。
- 验证:ruff 干净;jit 726 passed / 2 skipped;nojit 新测试 8 passed / 3 skipped(引擎级 needs_jit)。CI(695b041)测试矩阵、lint、
  import-clean、rtl-lockstep、vendor-drift 全绿;Android CI(8eed042,run 37245979942,含全部代码改动):
  `instrumented totals: 33 tests, 0 failures, 0 errors, 0 skipped`,解释型与编译型 APK 都过。

## 2026-10-04 · 整 UI 周跳后按段重对齐计分(fix/cycle-slip-rescore,原 ROADMAP 4c)

- `score()` 加 `find_slips`:512 符号窗,当前偏移错 > 1/4 且相对 ±4 内某偏移错不到一半才换,边界取两段错数和最小处;
  偏移无上限(先按绝对 ±4 做,慢漂的 kp 8 链路只找到 2 次、仍 0.49 —— 改成相对当前偏移)。`extras["cycle_slips"]` / `slip_at`。
- 示例 24 −18 dB:0.5 / −3.37 dB → 5.51e-3 / 6.98 dB(训练期内滑了一位)。时钟剖面快漂 kp 8:16 次周跳,段内 0.32(原 0.2–0.5)。
- 注入 ±1 UI 采样相位跳(6 个预设、两种架构):都报 1 次、位置 = 注入点 − warmup(±2)、BER 与不注入同量级。
- 指纹:只有 `NRZ 32G static` 时域行变(本就不通,0.4998 → 0.4933,前 ~3000 符号对在 +4 UI);其余逐位同,另加 `x.cycle_slips` 0。
- `extras["decisions"]` 仍是接收机原样输出,级联端到端不重排 —— 记入 pitfalls 与 `光互联建模.md`。
- 验证:ruff 干净;jit 715 passed / 2 skipped;nojit 本文件 15 passed / 2 skipped。CI(11a5ff4)测试矩阵、lint、import-clean、
  rtl-lockstep、vendor-drift 全绿;Android CI(b5953e2,run 37239822113,含全部代码改动):
  `instrumented totals: 33 tests, 0 failures, 0 errors, 0 skipped`,解释型与编译型 APK 都过。

## 2026-10-04 · 统计引擎计入 ADC 量化噪声(fix/stat-adc-noise,原 ROADMAP 4b)

- 根因:时域内核按 ENOB 加 ADC 噪声,统计引擎从不加 —— ADC 链路上统计偏乐观到"0 误码"(ENOB 5:统计 0、时域 1.46e-4)。
- 修:`adc_noise_sigma(cfg)` = fullscale/√12 · 2^(−ENOB)(无 ENOB 或 ≥ n_bits 用 n_bits),仅 `adc_dsp`,与 `noise_rms` 功率相加后过 FFE。
- 新测试 ENOB 5 / 6.5 / 8 统计 / 时域在 2× 内(实测 0.91、0.94);main 上红。
- 指纹(修复,非逐位):只 ADC 统计行动,`noise_sigma` +2.4–3.5 mV(106 GBd 预设 0 → 3.19 mV),SER 变 ≤ 0.004;
  TX 指纹 ADC 统计行 2.4e-247 → 3.5e-208、0 → 1.5e-255。时域与 mixed-signal 逐位同。
- 示例重跑(04 / 31 / 32 / 33 新旧对照):只有 31 变 —— 统计 BER 5.8e-16…9.4e-6 → 8.8e-6…3.2e-4,时域 1.0e-5…2.4e-4(比 0.3–1.4×),
  收尾说明改写;04、32、33 输出逐字同。专题 `architecture-invariants.md` 补注;`DSP发端与PR.md` §7 旧说法作废。
- 验证:ruff 干净;jit 698 passed / 2 skipped。CI(226d5df)测试矩阵、lint、import-clean、rtl-lockstep、vendor-drift 全绿;
  Android CI(071b3e7,run 37207966064,含全部代码改动):`instrumented totals: 33 tests, 0 failures, 0 errors, 0 skipped`,解释型与编译型 APK 都过。

## 2026-10-04 · 三光标目标的 LMS 跟踪(feat/pr-lms-three-cursor)

- 内核对 b 与 a 同误差更新(b 对 x₋₂ 求梯度,功率归一,a ∈ [0, 2]、b ∈ [−1, 1]);配置不再拒绝 lms + 三光标。1 + aD 逐位同前。
- 示例 38:LMS 38.58 dB vs MMSE 38.63 dB。坑:reach 外(42.4 dB)错判把 (a, b) 拉偏,BER 4.9e-2 vs 固定 8.0e-3 —— 记入专题 §10。
- 测试:内核从 (0.5, 0) 找到 (1.0, 0.4)(±0.04);numba == python(含 a、b);引擎上 lms 与 mmse 目标差 < 0.05、BER 同。
- 验证:指纹逐位同 main(488 + 44 值);ruff 干净;jit 695 passed / 2 skipped。
- CI(71263f2)测试矩阵、lint、import-clean、rtl-lockstep、vendor-drift 全绿;Android CI(f65ddf3,run 37195017468,含全部代码改动):
  `instrumented totals: 33 tests, 0 failures, 0 errors, 0 skipped`,解释型与编译型 APK 都过。

## 2026-10-04 · 第二个受控光标 1 + aD + bD²(feat/pr-longer-target,叠在 feat/pr-alpha-adapt 上)

- `pr.target=(1.0, a, b)`:内核减两个受控光标(判决、残差、LMS),DFE 从第三光标后起;Viterbi [1, a, b, r…];`mmse_pr_target` 联合解;
  统计引擎 b < 0 时加同号事件族;发端除以 Σ|target|。
- 示例 38:31.6(无 PR)/ 36.3(1 + aD)/ **38.6 dB**(1 + aD + bD²);MMSE 目标 (0.77, 0.28) → (1.17, 0.51),比 EPR4 平。
- 统计 / 时域 1.15–1.54×。专题 `DSP发端与PR.md` §10,USAGE §18,ROADMAP P3 #8 余项改写。
- 验证:1 + aD 与无 PR 时指纹逐位同 feat/pr-alpha-adapt(488 + 44 值);ruff 干净;jit 692 passed / 2 skipped(首跑 2 项失败:
  config 测试仍把 (1, 0.5, 0.25) 当非法、USAGE 注释里 `[-1, 1](EPR4…)` 被当成链接,改后 92/92)。
- CI(61160c9)测试矩阵、lint、import-clean、rtl-lockstep、vendor-drift 全绿(f72ed5a 上的 2 项失败即上面两项,5803d0b 已修);
  Android CI(f72ed5a,run 37188794340,含全部代码改动):`instrumented totals: 33 tests, 0 failures, 0 errors, 0 skipped`,解释型与编译型 APK 都过。

## 2026-10-04 · PR 目标的 a 由收端选(feat/pr-alpha-adapt,原 ROADMAP P3 #8 余项)

- `pr.adapt`:`mmse` 闭式解单位主光标 MMSE 目标的 a(`mmse_pr_alpha`,残差对 a 二次);`lms` 从此起步、在内核里与 FFE 同误差更新 a。
- 示例 36 第 4 部分:起始 a 0.60(27 dB)→ 0.76(39 dB),LMS 只动 < 0.01;reach 36.32 dB vs 固定 a 最优 36.33 dB —— 不用扫 a。
- 测试:闭式解对暴力扫描 1e-3 内;内核从 a = 0.3 起找到真值 0.7(±0.03);numba == python(含 a 输出);引擎上 mmse / lms 与最优固定 a 同 BER;配置限制。
- 专题 `DSP发端与PR.md` §9,USAGE §18,ROADMAP P3 #8 划掉 a 自适应。
- 验证:adapt 关时指纹逐位同 main(488 + 44 值,内核步长 0 逐位同前);ruff 干净;jit 679 passed / 2 skipped。
- CI(6adee96)测试矩阵、lint、import-clean、rtl-lockstep、vendor-drift 全绿;Android CI(9b13c98,run 37187817962,含全部代码改动):
  `instrumented totals: 33 tests, 0 failures, 0 errors, 0 skipped`,解释型与编译型 APK 都过。

## 2026-10-04 · PR 统计引擎扣掉相邻误差事件的重叠(fix/pr-event-overlap,原 ROADMAP P3 #8 缺口)

- 根因:发端 1 + 0.5D 后 FFE 噪声 ρ1 = −0.67,长度 2–6 的交替事件距离持平且相邻共享噪声投影,简单 union bound 重复计 → 2.5–2.9× 悲观。
- 修:`pr_error_events(snr=)` 对 L ≥ 2 乘 1 − P(A_L ∩ A_{L−1}) / P(A_L)(二元正态 via Owen T,Hunter 链式上界,仍是上界)。
- 统计 / 时域:发端 a = 0.5 1.78–1.79×、发端 a = 1 预编码 1.00×、收端 a = 0.5 1.29–1.56×、收端 a = 1 预编码 1.28–1.34×(BER 7e-5…6e-3)。
- 测试:`test_invariant3_with_a_tx_pr_target` 加 a = 0.5(main 上 2.9× → 红,修后绿);`test_mlsd_fec` 加重叠权重与二元正态精度。
  专题 `DSP发端与PR.md` §8 更正、`architecture-invariants.md` §3、pitfalls 1 条、ROADMAP P3 #8 删去该缺口。
- 验证:无 PR 时指纹逐位同 main(488 值,统计引擎改动只在 PR + 序列检测器分支);ruff 干净;jit 674 passed / 2 skipped。
- CI(bb48600)测试矩阵、lint、import-clean、rtl-lockstep、vendor-drift 全绿;Android CI(9eedac9,run 37179068939):首跑解释型 APK 的
  `UiRenderTest#runningTheStatisticalEngineDrawsABathtub` 等 BER 超 180 s(默认预置、无 PR,本改动不触及),重跑一次通过:
  `instrumented totals: 33 tests, 0 failures, 0 errors, 0 skipped`,编译型 APK 首跑即过。

## 2026-10-03 · 发端 PR 整形与三方同台(feat/tx-pr,原 ROADMAP P3 #8 阶段 3)

- `pr.at: tx`:`TxPipeline.pr_filter` = (x_k + a·x_{k−1}) / (1 + a)(峰值归一),并进等效符号响应;收端沿用 1 + aD 检测。
- 坑:1 + aD 脉冲 argmax 可能落在 a·x_{k−1} 上 → 时域在不含 PR 的脉冲上定主光标(−30 dB 处 BER 1.5e-2 → 3.7e-3,先红后绿)。
- 示例 37:无 PR 31.6 dB、收端 a = 0.75 36.3 dB、发端 a = 0.25 / 0.5 / 0.75 / 1 29.7 / 28.1 / 26.9 / 26.9 dB ≈ 无 PR − 20·log10(1 + a)。
  线性链路里发端整形不省收端的噪声放大,只交出峰值;判断:PR 放收端。第一版"不限峰值"对照放大了 ADC 满量程 → ENOB 噪声同步放大,删掉。
- 缺口(ROADMAP P3 #8):统计引擎在发端 a = 0.5 时悲观 2.5–2.9×(ρ1 ≈ −0.67,长事件距离持平,union bound 重复计);发端 a = 1 预编码 1.2–1.5×。
- 验证:无 `at: tx` 时指纹逐位同 main(488 + 44 值);ruff 干净;jit 672 passed / 2 skipped(跑测时 README 示例数未更新的 1 项失败,更新后 14/14 过)。
  专题 `DSP发端与PR.md` §8,USAGE §18,SUMMARY §04,pitfalls 2 条。
- CI:3.10 nojit 撞 30 分钟上限(test_pr.py 在纯 Python 内核下 26 分钟,6 个 60 万符号级引擎测试各 2–6 分钟)。这些测试改为只在 numba 下跑
  (`needs_jit`,与文件里已有的跳过条件相同);nojit 下内核等价由 `test_numba_kernel_matches_python_with_pr` 负责。nojit 26 → 3 分钟。
- CI(7060031)测试矩阵(nojit 7–14 分钟)、lint、import-clean、rtl-lockstep、vendor-drift 全绿;Android CI(9ce91bb,run 37134632073,含全部代码改动):
  `instrumented totals: 33 tests, 0 failures, 0 errors, 0 skipped`,解释型与编译型 APK 都过。

## 2026-10-03 · 接收端 PR 整形 1 + aD(feat/rx-pr,原 ROADMAP P3 #8 阶段 2)

- `PrConfig` + ADC 收端接线(起始 FFE target、LMS 期望值、DFE 起点、MLSD 光标 [1, a, r…]、逐符号判决、a = 1 预编码切合成电平)。
- 偏离方案两处:(1) CDR 不用 `pd_offset` —— a ≥ 0.75 时环路走 0.4–4 UI;改读"样本 − a × 上一判决",锁定偏差 ≤ 0.0011 UI。
  (2) 统计引擎不只用 MLSE 最小距离 —— PR 下 FFE 噪声 ρ1 ≈ −0.25、ρ2 ≈ −0.31,最小距离版乐观 2.5–20×;改为交替误差事件 union bound,
  BER ≤ 5e-4 时 1.0–1.5×。
- 示例 36:reach 对照 31.6 dB → a = 0.25 / 0.5 / 0.75 / 1:33.9 / 35.5 / 36.3 / 36.2 dB(最优 0.75,+4.7 dB);1+D Viterbi 增益扣重数后 3.01 dB 内。
  33.3 dB 处 2.5e-6 不是误码底(种子 3/4/5:2/0/2 个比特错)。专题 `DSP发端与PR.md` §7,USAGE §18,SUMMARY §04 加一行杠杆。
- 验证:`target=(1.0,)` 指纹逐位同 main(488 + 44 值);ruff 干净;本地 jit 664 passed / 2 skipped(本地 nojit 全量跑到 86% 被时限停掉,
  其间无失败;此前一轮 4 个失败是 stash 后的陈旧缓存,单独重跑 39 项全过)。CI(5da9dc4)测试矩阵含 3.10 / 3.11 nojit、lint、import-clean、
  rtl-lockstep、vendor-drift 全绿;Android CI(run 37122891581):`instrumented totals: 33 tests, 0 failures, 0 errors, 0 skipped`,解释型与编译型 APK 都过。

## 2026-10-03 · 统计引擎一致性 A1–A4(fix/stat-engine-consistency,原 ROADMAP 1b、#8 tx.bw、#11 分箱)

- A1 根因:ADC FFE 在第 k 个样本才判第 k − n_pre 个符号,末尾 n_pre 个 `dec` 是没写过的 0 却被计分。截掉后 ADC 预置少 3–11 错。
- A2 根因:统计引擎的脉冲不含 TX 驱动器极点。`TxPipeline.driver_response`(与 `apply_single_pole` 同一 H,±32 UI 窗)接入后 0.003× → 1.00 / 1.04×。
- A3 根因两条:统计端报全相位最小值而非 bang-bang 锁定相位;时域 Catmull-Rom 插值把逐样本白噪声压到 Σc² = 0.64–0.81。
  修:按 FFE 延迟把锁定点映到均衡后相位;`receiver_awgn` 限带到 [0, baud]。PAM4 MS 0.19 / 0.41 / 0.70 → 0.88 / 0.97 / 0.94;
  **更正**:`光互联建模.md` 里把 MS PAM4 偏差归给 DFE 传播的判断不对,是这两条。
- A4 根因:单高斯核匹配方差但尾部比尺度混合窄;两邻符号分箱后 ER 3 / 4.5 / 6 在 1.5× 内(0.99 / 0.97 / 0.96)。
- 推翻:`test_clock_profile_cdr` 的 JTOL"有剖面方向更差"只在单种子成立(种子 7 / 8 / 9 修前就是 1.52 / 1.24 / 1.07),改断言为有限且有形状。
- 示例 23 个有变化,前后数在 SUMMARY / summary.html / COMPARISON / 两专题(数旧值保留);新 ROADMAP 4b(统计引擎缺 ENOB 噪声)、4c(周跳后不重对齐)。
- pitfalls 加 4 条;`architecture-invariants.md` §3 记两条前提。
- 验证:ruff 干净;jit 637 passed / 2 skipped,nojit 635 passed / 4 skipped。CI(ab8aaed)测试矩阵、lint、import-clean、rtl-lockstep、vendor-drift 全绿;
  Android CI(3548102,run 37118161226,最后一个改代码的 commit):`instrumented totals: 33 tests, 0 failures, 0 errors, 0 skipped`,解释型与编译型 APK 都过。

## 2026-10-03 · 示例 18 改为量 MLSD 何时有用;summary.html 换图(feat/ex18-mlsd,原 ROADMAP P2 6d)

- 扫描 FFE 长度 1/1、1/3、2/5、6/14 × 4 个损耗 × CTLE 0/6 dB:只有 3 抽头时 MLSD 有增益(memory-2 最高 5×),1/3 起就 ≤ 1.2×;
  3 抽头 + MLSD 从不胜过 21 抽头 FFE。delta 目标的 LMS FFE 把残余压到 < 0.002 —— MLSD 要加 reach 得有 1+αD 目标(P3 #8 已记)。
- 示例 18 两种 FFE 同台(21 抽头配置不变,示例 19 / 35 的引用仍有效);SUMMARY、summary.html、pitfalls MLSD 类按此改。
- summary.html 的示例 15 / 16 / 21 三张内嵌图换新;其余六张是 mixed-signal 示例,不受两处修复影响,未动。
- 验证:ruff 干净;示例 18 跑通;CI(b0bdee8)测试矩阵、lint、import-clean、rtl-lockstep 全绿。只动示例与文档,不碰共用层,Android 工作流按路径不触发(铁律 6 不适用)。

## 2026-10-03 · `cdr.pd_input` 默认 `auto` + 重跑受影响的 20 个示例(feat/pd-input-auto,原 ROADMAP P1 1d)

- 48 点扫描:PAM4 上 `ffe` 持平或大幅更好(0.25 m SNR 17.9 → 27.4 dB),NRZ 上 `ffe` 在轻 ISI 链路相位游走 ~0.4 UI。默认改 `auto`
  (`LinkConfig.mm_pd_input`:PAM4 → ffe,NRZ → adc)。指纹只动 LPO 预置时域块(9 → 11 误码)。专题 §5 有表并更正"被推翻"那句。
- 20 个示例新旧代码各跑一遍:示例 18 的 MLSD 增益没了(1.0×,旧 29× 来自锁偏);224G 阶梯 → 18 / 32 / 36–39 / 44 dB;光互联阶梯
  小幅上移、结论不变。SUMMARY / summary.html / COMPARISON / 两个专题按新数改、旧数留在原处。示例 19 `<start` 误报修掉,示例 31 改 `ffe`。
- 新 ROADMAP P2 6d(示例 18 换一个 MLSD 真有用的配置、重嵌 summary.html 的图);pitfalls 加"杠杆增益要在修好的基线上量"。
- 验证:jit 612 passed / 2 skipped(+ 新 7 项单跑通过),nojit 617 passed / 4 skipped;Android CI(0354903,run 37093567169):`instrumented totals: 33 tests, 0 failures, 0 errors, 0 skipped`,解释型与编译型 APK 都过(第二个 commit 只动示例与文档)。

## 2026-10-03 · Android CI 的 push 路径补上 APK 资源目录(ci/android-paths)

- `configs/**`、`data/channels/**`、`data/clock_profiles/**` 加进 `android.yml` 的 push 路径:PR #9 只改预置,Android 没触发。
  坑记入 `engineering-pitfalls.md` 打包类首条。
- 验证:本 PR 动工作流文件,自身就触发了 Android(push 与 pull_request 各一次);Android CI(8ab5c42,run 37093101089):`instrumented totals: 33 tests, 0 failures, 0 errors, 0 skipped`,解释型与编译型 APK 都过;测试矩阵、lint、import-clean 全绿。

## 2026-10-03 · 4 个 224G ADC 预置 `cdr.pd_input: adc → ffe`(fix/adc-presets-pd-ffe)

- 被推翻:预置注释"均衡后的样本没有 MM 梯度"。`ffe` 时 CDR 照样跟踪(SJ 0.1 UI @ 0.5 MHz,跟踪误差 rms 0.026–0.062 UI,
  `adc` 是 0.058–0.152 UI),无 SJ 时贴住峰值;`adc` 自己就漂 0.17–0.29 UI。时域 BER(106G / 112G stress / TI / deep-LR):
  3.1e-3 → 2.3e-4、7.8e-3 → 1.6e-5、3.8e-3 → 1.3e-5、6.4e-2 → 3.9e-5;指纹只变这 4 个预置的时域块。
- 库默认 `pd_input` 仍是 `adc`;自己搭 ADC 接收机的示例用的是它 → ROADMAP P1 1d 并入"先定默认值再重跑"。专题 §5 追加。
- 验证:jit 612 passed / 2 skipped,nojit 610 passed / 4 skipped;Android CI(e6ae1ff,run 37085024935,手动 dispatch——PR 只改 configs/ 不触发路径过滤,但预置随 APK 打包):`instrumented totals: 33 tests, 0 failures, 0 errors, 0 skipped`,解释型与编译型 APK 都过。

## 2026-10-03 · 修:接收端起始均衡看见 TX FFE(fix/rx-init-tx-ffe,原 ROADMAP P1 1c)

- `TxPipeline.receiver_view(h)`:TX FFE 卷进脉冲分析用的 h,返回 n_pre·osr 超前量;静态、时域两架构、`front_end_waveform` 改用它。
  单抽头逐字节不变。强 TX FFE:静态 NRZ SNR 11.2 → 26.2 dB、PAM4 BER 5.3e-2 → 0、ADC 时域 BER 0.10 → 4.6e-5;静态 vs 统计
  (± 5 bit DAC,三损耗点)0.83–1.17。8 项新测试在旧行为下全红。
- 指纹 131 / 475 变(6 个带 TX FIR 预置的静态 / 时域;统计、COM、单抽头不变)。静态全面变好;**4 个 ADC 预置时域 BER 高
  1.1–1.6 倍** —— 不是本修复的错:这些预置 MM-CDR 读均衡前 ADC 采样(`pd_input: adc`),锁偏 0.1–0.3 UI 并来回摆,两种起点
  相位轨迹逐位相同;相位在峰值附近时新起点好 3–10 倍,`pd_input: ffe` 时新起点 BER 低 18–30 倍。改不改预置由用户定 →
  ROADMAP P1 1c(新);16 个带 TX FIR 的示例要重跑(示例 35 的 reach +3.4 dB)→ P1 1d。查法进 pitfalls(SNR 好 BER 坏 → 按时间分段看误码和 CDR 相位)。专题 `cairn/DSP发端与PR.md` §5 追加更正。
- 验证:jit 612 passed / 2 skipped,nojit 610 passed / 4 skipped(+9 项新测试);Android CI(a8ebf6b,run 37081913516):`instrumented totals: 33 tests, 0 failures, 0 errors, 0 skipped`,解释型与编译型 APK 都过。

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
- 验证:jit 603 passed / 2 skipped,nojit 601 passed / 4 skipped,新开关全关时两份指纹逐位同;Android CI(e0ed63d,run 37007280461):`instrumented totals: 33 tests, 0 failures, 0 errors, 0 skipped`,解释型与编译型 APK 都过。

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
