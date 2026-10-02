# 更新日志 / Changelog

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，按里程碑而非
逐条提交组织。项目尚未发布正式版本（`pyproject.toml` 中为 `0.0.1`），下列条目按
完成顺序排列。

---

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
