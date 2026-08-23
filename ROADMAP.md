# 待办与路线图 / Roadmap

已完成的演进见 [`CHANGELOG.md`](CHANGELOG.md);README 里的「路线图」表是**回顾性**的
(记录已交付的阶段)。这份文件相反,只记**尚未做的**。

每条都标注了**证据**(在哪测到的、哪个文件),便于接手的人直接开工而不必重新调查。
优先级按「是否影响结论的可信度」排序,不是按工作量。

> 约定:`[P1]` 会让某类结论不可信或明显误导;`[P2]` 影响能力上限或使用体验;
> `[P3]` 可选扩展;`[边界]` 有意不做,仅记录范围。

---

## P1 — 声称与实现的落差

### 1. 定点只覆盖数据通路,不覆盖引擎与时钟恢复

**现状**:`engine/timedomain.py` **完全不读 `numeric.mode`** —— 定点走
`dsp/fixed_datapath.py` 的独立重放路径,只实现 FFE + DFE + slicer
(`_ffe_dfe_fixed_py`)。`cdr/` 下没有任何 `QFormat` 引用,`dsp/mlsd.py` 也没有定点路径。
`rtl/ffe_dfe_datapath.sv` 因此只能覆盖同样三级。

**为什么要紧**:Phase 6 的目标是"作为将来 RTL 的黄金模型"。CDR 环路增益是移位量
(`kp_shift`/`ki_shift`),本就是按硬件语义设计的,却没有 bit-true 路径去验证;
MLSD 的定点化(度量位宽、路径度量归一化)是真实 RTL 里最容易出错的地方之一。
现在的 lockstep 给人"黄金模型→RTL 已闭环"的印象,实际只闭了三分之一。

**怎么做**:先给 `mm_cdr` 加 int64 路径(PD 输出、环路累加器、相位插值索引),
配 `dump_vectors` 出口与一个 SV 对照模块;MLSD 同理(优先 sliding-detector,
它的定点化比 Viterbi 简单得多)。每一步都按现有范式:纯 Python 核 + numba 包装 +
独立整数参考仲裁。

**验收**:`rtl/run_lockstep.sh` 覆盖 FFE+DFE+slicer+CDR;`HALO_NO_JIT=1` 与 JIT
两条路径一致;定点与浮点在字长 → ∞ 时收敛。

---

### 2. 波形全量驻留,长跑受内存限制

**现状**:实测 10⁶ 符号 @106.25 GBd/OSR32 峰值 RSS ≈ **0.9 GB**
(tracemalloc 追踪到的 Python 峰值 786 MB)。`engine/lti.py::fft_filter` 内部已做
overlap-save 分块,**但波形数组本身没有窗口化** —— `tx_wave`、`tx_y`、`rx_y`
(以及 `collect_jitter` 时的 `ch_only`)同时全量存活。

**为什么要紧**:原计划写的是"观测点按需保留窗口"。现在跑 2×10⁶ 符号或 OSR=64
会到多 GB,而**深 LR 的低 BER 结论恰恰需要更长的码流**(1e-6 量级要 ~10⁷ 符号)。
`sim.chunk_symbols` 字段已存在但时域引擎并未据此分块。

**怎么做**:把时域引擎改成按 `chunk_symbols` 流式推进 —— 每块只保留
(前一块尾部 + 当前块)的波形,观测点(眼图、抖动、q 直方)按需累积统计量而非留全量。
难点在跨块的状态连续性:CDR 相位、DFE/FFE 抽头、Farrow 插值历史都要正确接续。

**验收**:同一配置下分块与全量结果**逐位一致**(这是硬要求,参照 `fft_filter`
的验证方式);10⁶ 符号峰值内存降到数百 MB;新增一个长跑(≥5×10⁶ 符号)冒烟测试。

---

### 3. `sim.chunk_symbols` 是死字段,时域长跑无法中断

**现状**:`config/schema.py` 定义并校验了 `sim.chunk_symbols`,`config_bridge` 也把它做成了
表单字段,但**时域引擎从不读它** —— `engine/timedomain.py` 对 `ms_rx`/`adc_rx` 是**整段
一次性调用**(混合信号与 ADC 两条路径各一处)。

**为什么要紧**:没有让出点意味着(a)拿不到进度,(b)**无法取消**一次已经开始的长跑,
(c)内存必须一次性容纳整条波形(这正是 P1 第 2 条的根因)。对桌面版是体验问题,
对手机是致命问题 —— Android 上 10 万符号要 1–2 分钟。

**Android M7 做到哪一步(2026-08-23)**:`run_link` 现在在**阶段边界**回调
(建信道 / 各引擎 / done),`api.start_time_run` 据此提供后台 job、`poll` 与 `cancel`,
前台服务保证长跑不被系统回收。**但这只是阶段级的**,本条要的东西一件没变:
进度条只能是不确定态,取消要等接收机内核那一次调用整个跑完(精确档就是十分钟),
内存也照样一次性驻留。界面把这两条限制照实说了出来,而不是假装能中途停下。
真正的解法仍然是下面这个 —— 把循环状态交给调用方。

**怎么做**:让 `ms_rx`/`adc_rx` 接受并返回循环状态(CDR 相位、抽头、Farrow 历史),
使调用方能按 `chunk_symbols` 分块推进。这会同时解决进度、取消与内存三件事,
并让那个死字段终于有意义。

**注意**:这两个是 numba 编译的热核,受铁律 #2/#4 约束,且 `rtl/run_lockstep.sh` 依赖其
数值行为 —— 必须以"分块与整段逐位一致"为验收标准,单独立项,不要夹在别的改动里。

---

## P2 — 一致性与交付

### 4. `has_getwave` 默认值不一致

`NativeFirAmi` 默认 `True`、`AmiCModel` 默认 `False`。语义上都说得通(前者两条流都
实现;后者让调用方明示),但**对比两个 AMI 模型时若不显式对齐,会把"走了不同的流"
误当成模型差异** —— 开发中确实踩过,一度以为 2.62 dB 的差异是 bug。

**怎么做**:两个选项 —— (a) 统一默认值并在 `AmiModel` 基类文档里写清;
(b) 保持现状但在 `load_ami_model` 里对"两个模型 flag 不一致"发 warning。
倾向 (a) + 在 `docs/USAGE.md` §12 已有的提示上再加一句。

### 5. 桌面版发 GitHub Release

CI 已经在构建三种 Windows 产物(PyInstaller / Nuitka standalone / Nuitka onefile)
并做 `--selfcheck` 冒烟,但只作为 **artifact 上传,会过期**。加一个 tag → Release 的
job,让 onefile exe(自带图标)成为长期可下载的交付物。工作量最小的一项。

### 6. Rx-FFE 纳入 COM 优化网格

`analysis/com.py` 现在优化 CTLE peaking × 采样相位 × (可选)Tx FFE,DFE 抽头由光标
经 `b_max` 导出 —— 这是 **93A 参考接收机(CTLE+DFE)** 的形态。178A 风格的
**Rx-FFE** 尚未纳入网格。`ComParams` 里已留了 `tx_fir_grid` 扩展位,加 `rx_ffe_grid`
是自然延伸。做完之后 ADC 架构的 COM 才与其真实接收机结构对齐。

---

## P3 — 能力扩展

### 7. 片上校准回路
现在只**建模失配**:`AdcConfig.calibrated=True` 是行为级把 offset/gain 归零,
不是真实算法。DragonPHY2 的 ADC unfolding 是可移植的参考。做了之后才能回答
"校准残差 vs 性能"这类问题。

### 8. Duobinary / PR 整形
1+D 预编码已实现(`precode` 开关),但**预编码 ≠ PR 整形**:前者是符号映射,
后者要在发端有意引入受控 ISI 并配匹配的检测器。属于独立能力。

### 9. 多 lane 数据通路
多 lane 目前只在**串扰侧**(`aggressor_bank`/`icn_rms`);链路本身仍是单 lane。
真正的多 lane 系统级(lane 间 skew、共享 CDR/校准、per-lane FEC 交织)是另一个量级
的工作,按需再评估。

### 10. 测试与文档的长尾
- GUI docstring 28%(核心库 66%);GUI 行覆盖 60%(核心库 89%)。
- 31 个示例只做 **import 守卫**(`tests/test_examples_api.py`),不执行。
  全量执行太慢(多个用 10⁶ 符号),可考虑加一个"小符号数档"的夜间 CI job。
- `LICENSE` 与 `CONTRIBUTING.md` 尚缺(许可证类型需要由项目所有者决定)。

---

## 边界 — 有意不做

这些超出**行为级框架**的设计范围,记录在此以免被反复提起(详见
[`docs/COMPARISON.md`](docs/COMPARISON.md)):

- 真实 RTL 的完整三视图方法学(model / rtl / fpga 一致性)—— 现有的是一个
  bit-exact 的 FFE+DFE lockstep,证明范式可行,不追求全芯片。
- FPGA AMS 混合仿真、物理实现流(综合 / PnR / DRC)、片上 BIST/DFT/JTAG。
- 晶体管级模拟前端 —— CTLE/VGA 是传函行为模型,不是电路。

---

## 维护约定

- 完成一项就从这里删掉,并在 [`CHANGELOG.md`](CHANGELOG.md) 里记一笔。
- 新发现的问题请连同**证据**(复现方式、测到的数字、涉及文件)一起写进来 ——
  没有证据的条目会在下一次审计里被当作猜测处理。
- 架构不变量与易踩的坑写在 [`CLAUDE.md`](CLAUDE.md),不要写进这里。
