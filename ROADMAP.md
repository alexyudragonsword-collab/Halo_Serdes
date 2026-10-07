# 待办与路线图 / Roadmap

已完成的演进见 [`CHANGELOG.md`](CHANGELOG.md);README 里的「路线图」表是**回顾性**的
(记录已交付的阶段)。这份文件相反,只记**尚未做的**。

每条都标注了**证据**(在哪测到的、哪个文件),便于接手的人直接开工而不必重新调查。
优先级按「是否影响结论的可信度」排序,不是按工作量。

> 约定:`[P1]` 会让某类结论不可信或明显误导;`[P2]` 影响能力上限或使用体验;
> `[P3]` 可选扩展;`[边界]` 有意不做,仅记录范围。

---

## P1 — 声称与实现的落差

(2026-10-06 清空:原 #1 定点 —— FFE/DFE/slicer、训练、整数 LMS、PR、MM CDR、sliding 与 Viterbi MLSD —— 已全部 bit-true 并有
SV 逐位对照,见 CHANGELOG 与 `rtl/README.md`;原 #2 流式见 P2 #2;原 #3 分块续跑已合入。)

---

## P2 — 一致性与交付

(2026-10-06 清空:原 #2 流式覆盖 —— 光拓扑、串扰、Init 流程 AMI、`collect_jitter` 已接入流式,见 CHANGELOG;
AMI GetWave 记入下方「边界」。)

---

## P3 — 能力扩展

### 7. 片上校准回路
(2026-10-07 阶段 1 已做:`adc.cal.mode: background` —— offset / gain 的后台数据驱动校准,量化器后数字修正,
步长 → 稳态残差与收敛时间的取舍见 `docs/USAGE.md` §19 与示例 39。)尚未做:
- (2026-10-07 阶段 2 已做:skew 校准 `adc.cal.mu_skew`,见 USAGE §19。)待查:有 skew 时 offset / gain 环收敛明显变慢
  (4×10⁵ 符号差理想 2 dB,10⁶ 时 0.2 dB),机理未确认;证据在 `tests/test_adc_cal.py` 最后一条的 docstring 与 LOG 2026-10-07。
- **定点 / RTL**:校准字的位宽与舍入、`dsp/fixed_loop.py` 与 SV 对照;现在定点模式开校准直接报错。
- 修正系数的有限分辨率(现在是浮点)、前台校准(上电时输入短接 / 已知参考)作为对照。

### 8. Duobinary / PR 整形
1+D 预编码已实现(`precode` 开关),但**预编码 ≠ PR 整形**:前者是符号映射,
后者要有意引入受控 ISI 并配匹配的检测器。

**阶段 2(收端)与阶段 3(发端)已合入**(2026-10-03,`PrConfig(at="rx"|"tx")`、`docs/USAGE.md` §18、`cairn/DSP发端与PR.md` §7–8)。
示例 18 的信道上:收端 1 + 0.75D 比 delta + Viterbi 多 4.7 dB reach(示例 36);发端 1 + aD 在峰值不变时 ≈ 无 PR − 20·log10(1 + a)
(示例 37),线性链路里发端整形不省收端的噪声放大。

尚未做:
- 更长的目标(四光标起网格 N⁴)。
  (2026-10-04/05 已做:a 自适应 `pr.adapt`、1 + aD + bD² 目标及其 (a, b) 的 LMS 跟踪、统计引擎对逐符号判决(误差传播)的建模。)
- 发端 PR 真正可能占优的场景本模型没有:发端带宽受限且噪声在发端之前 / 之内(如光调制器的 duobinary)、串扰源在发端。

### 9. 多 lane 数据通路
多 lane 目前只在**串扰侧**(`aggressor_bank`/`icn_rms`);链路本身仍是单 lane。
真正的多 lane 系统级(lane 间 skew、共享 CDR/校准、per-lane FEC 交织)是另一个量级
的工作,按需再评估。

### 11. 光互联的余项
阶段 1–3(`cairn/光互联建模.md`)有级联 H(f) + 电平相关噪声 + 重定时串联 + 大信号曲线与 TDECQ。**未做**:
- 802.3dj 200G/λ TDECQ 的 1 抽头 DFE(现在只有 15 抽头 FFE;DFE 版的上限 dB 不同);MMF PMD 的参考接收机带宽
  可能低于 0.5×baud(802.3cd SR 用过 11.2 GHz @ 26.5625 GBd),未核实前按 0.5×baud;
- 统计引擎对大信号曲线只用稳态电平,不建 ISI 被曲线弯折的部分(已发 warning);
- LPO 模块内的"线性"EQ:OIF CEI-112G-LINEAR 允许驱动器 / TIA 各带简单 CTLE,现在按零 EQ 做;
- 功耗。

### 10. 测试与文档的长尾
- GUI docstring 28%(核心库 66%);GUI 行覆盖 60%(核心库 89%)。
- (2026-10-07 已做:CI `examples` job 用 `tools/run_examples.py --smoke` 把 39 个示例全部真跑一遍,
  `n_symbols` 压到 2 万,~6 分钟。)剩余:冒烟只证明代码路径能跑,示例打印的数字(文档引用的那些)只有按原尺寸跑才有意义,
  仍是手动步骤(`python tools/run_examples.py`,不加 `--smoke`)。
- `LICENSE` 与 `CONTRIBUTING.md` 尚缺(许可证类型需要由项目所有者决定)。

---

## 边界 — 有意不做

这些超出**行为级框架**的设计范围,记录在此以免被反复提起(详见
[`docs/COMPARISON.md`](docs/COMPARISON.md)):

- 真实 RTL 的完整三视图方法学(model / rtl / fpga 一致性)—— 现有的是一个
  bit-exact 的 FFE+DFE lockstep,证明范式可行,不追求全芯片。
- FPGA AMS 混合仿真、物理实现流(综合 / PnR / DRC)、片上 BIST/DFT/JTAG。
- 晶体管级模拟前端 —— CTLE/VGA 是传函行为模型,不是电路。
- 流式模式下的 IBIS-AMI GetWave —— `AmiModel.get_wave` 一次处理整段波形;按块调用要模型自带跨调用状态,
  且结果是否与块长无关由模型决定,框架无法保证"分块逐位一致"。Init 流程的模型可以流式。

---

## 维护约定

- 完成一项就从这里删掉,并在 [`CHANGELOG.md`](CHANGELOG.md) 里记一笔。
- 新发现的问题请连同**证据**(复现方式、测到的数字、涉及文件)一起写进来 ——
  没有证据的条目会在下一次审计里被当作猜测处理。
- 架构不变量与易踩的坑写在 [`CLAUDE.md`](CLAUDE.md),不要写进这里。
