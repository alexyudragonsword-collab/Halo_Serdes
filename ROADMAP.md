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

### 8. Duobinary / PR 整形
1+D 预编码已实现(`precode` 开关),但**预编码 ≠ PR 整形**:前者是符号映射,
后者要有意引入受控 ISI 并配匹配的检测器。

**阶段 2(收端)与阶段 3(发端)已合入**(2026-10-03,`PrConfig(at="rx"|"tx")`、`docs/USAGE.md` §18、`cairn/DSP发端与PR.md` §7–8)。
示例 18 的信道上:收端 1 + 0.75D 比 delta + Viterbi 多 4.7 dB reach(示例 36);发端 1 + aD 在峰值不变时 ≈ 无 PR − 20·log10(1 + a)
(示例 37),线性链路里发端整形不省收端的噪声放大。

尚未做:
- (2026-10-04/05 已做:a 自适应 `pr.adapt`、1 + aD + bD² 目标及其 (a, b) 的 LMS 跟踪、统计引擎对逐符号判决(误差传播)的建模。
  2026-10-08 评估了四光标目标:在示例 38 的链路上约 +0.5 dB,恰好落在事先定的 0.5 dB 门槛上,门槛分不出;按成本暂不做,
  移入下方"边界";证据见 `tools/pr_target_length.py`、`cairn/DSP发端与PR.md` §12。)
- 发端 PR 真正可能占优的场景本模型没有:发端带宽受限且噪声在发端之前 / 之内(如光调制器的 duobinary)、串扰源在发端。

### 9. 多 lane 数据通路
多 lane 目前只在**串扰侧**(`aggressor_bank`/`icn_rms`);链路本身仍是单 lane。
真正的多 lane 系统级(lane 间 skew、共享 CDR/校准、per-lane FEC 交织)是另一个量级
的工作,按需再评估。

### 11. 光互联的余项
阶段 1–3(`cairn/光互联建模.md`)有级联 H(f) + 电平相关噪声 + 重定时串联 + 大信号曲线与 TDECQ。**未做**:
- (2026-10-08 已做:802.3dj 200G/λ TDECQ 的 1 抽头 DFE,`tdecq(dfe=True)`。)剩余:dj 后加的 FFE 抽头约束
  (w(i)/w(0)、|w(1) − w(−1)|)与上限是否下调到 3.0 dB 未核实(条文被代理拦截);MMF PMD 的参考接收机带宽
  可能低于 0.5×baud(802.3cd SR 用过 11.2 GHz @ 26.5625 GBd),未核实前按 0.5×baud;
- (2026-10-08 已做:统计引擎建了大信号曲线对 ISI 的弯折 —— 按邻符号分箱的逐相位平移,四条链路 c = 0…0.5 统计 / 时域
  0.86–1.28,原只用稳态电平时 0.30–1.16;`cairn/光互联建模.md` §8。)
- (2026-10-08 已做:LPO 模块驱动器 / TIA 的 CTLE,`OpticalConfig.drv_ctle_db` / `tia_ctle_db`;示例 32 第 3 问,
  `cairn/光互联建模.md` §6。)剩余:OIF 对 CTLE 峰化的具体上限未查到(代理拦截);预加重的过冲与激光器余量只给了两个口径的界;
- 功耗。

### 10. 测试与文档的长尾
- (2026-10-08 已做:GUI docstring 37% → 100%、app 层 57% → 88%(按函数 / 类 / 模块计,含私有;原记的 28% 口径不明,
  同口径核心库 65%);GUI 行覆盖 61% → 89%(只算 `tests/test_gui*.py`),`tests/test_gui_panels.py` 在真实运行记录上渲染每个标签页。
  剩下没覆盖的是 `desktop.main` / `__main__` 的起服务路径(CI 的打包 selfcheck 在跑)。)
- (2026-10-07 已做:CI `examples` job 用 `tools/run_examples.py --smoke` 把示例全部真跑一遍,`n_symbols` 压到 2 万。
  2026-10-08 已做:40 个示例按原尺寸全跑(3 路并行),输出逐条对照现行文档,修了约 40 处漂移;`run_examples.py` 加了
  `--save DIR`(存每个示例的输出)与 `--jobs N`。)剩余:全尺寸运行仍是手动步骤,数字会随模型改动再漂 —— 大改前后各
  `--save` 一份,`diff` 即知哪些文档数字要动。
- (2026-10-08 已做:`CONTRIBUTING.md`,附 `tools/fingerprint.py`("重构用数值指纹证明"这条规则此前没有入库工具)。
  更正:原记 "`LICENSE` 尚缺" 不实 —— MIT 的 `LICENSE` 自 2026-09-12(b6e15ca)就在根目录,`pyproject.toml` 也写着 MIT。)

---

## 边界 — 有意不做

这些超出**行为级框架**的设计范围,记录在此以免被反复提起(详见
[`docs/COMPARISON.md`](docs/COMPARISON.md)):

- 真实 RTL 的完整三视图方法学(model / rtl / fpga 一致性)—— 现有的是一个
  bit-exact 的 FFE+DFE lockstep,证明范式可行,不追求全芯片。
- FPGA AMS 混合仿真、物理实现流(综合 / PnR / DRC)、片上 BIST/DFT/JTAG。
- 晶体管级模拟前端 —— CTLE/VGA 是传函行为模型,不是电路。
- 四个及以上受控光标的 PR 目标 —— 不是超出框架,是不划算:示例 38 的 224G LR 链路上第四个光标约 +0.5 dB
  (基带 MC 原值 +0.65 dB,按它对前两档的高估比例折算;同 256 状态时也有这些,所以代价不在网格,而在内核判决、
  定点数据通路、统计引擎的判决模型都要多带一个受控光标)。增量恰在事先定的门槛上,"暂不做"是按成本的判断。
  重开条件:某条链路在三光标之外只差 ≲ 0.5 dB 就够到目标,或换到损伤结构不同的信道 —— 先跑
  `tools/pr_target_length.py` 看增量(`cairn/DSP发端与PR.md` §12)。
- 流式模式下的 IBIS-AMI GetWave —— `AmiModel.get_wave` 一次处理整段波形;按块调用要模型自带跨调用状态,
  且结果是否与块长无关由模型决定,框架无法保证"分块逐位一致"。Init 流程的模型可以流式。

---

## 维护约定

- 完成一项就从这里删掉,并在 [`CHANGELOG.md`](CHANGELOG.md) 里记一笔。
- 新发现的问题请连同**证据**(复现方式、测到的数字、涉及文件)一起写进来 ——
  没有证据的条目会在下一次审计里被当作猜测处理。
- 架构不变量与易踩的坑写在 [`cairn/architecture-invariants.md`](cairn/architecture-invariants.md) 与
  [`cairn/engineering-pitfalls.md`](cairn/engineering-pitfalls.md),不要写进这里(`CLAUDE.md` 只是指向 `AGENTS.md` 的一行)。
