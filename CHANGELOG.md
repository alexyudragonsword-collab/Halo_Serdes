# 更新日志 / Changelog

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，按里程碑而非
逐条提交组织。项目尚未发布正式版本（`pyproject.toml` 中为 `0.0.1`），下列条目按
完成顺序排列。

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
- M0 判定**通过**：三个 job 全绿，模拟器 5/5（含 `rtol=1e-9` golden 比对）。
  模拟器是 x86_64，**ARM 浮点、16 KB page 与真机性能仍待真机验证**。

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
