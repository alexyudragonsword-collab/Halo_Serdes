# 三参考仓库 vs Halo_Serdes 能力对比

> 归档于 2026-07。对比对象:`serdespy`、`PyBERT`、`DragonPHY2` 三个开源参考库,
> 与当前 `Halo_Serdes` 行为级仿真框架。用于回答:哪些已覆盖、哪些做得更好、
> 哪些还没做到。

---

## 三库定位速览

| 库 | 定位 | 核心价值 | 局限 |
|---|---|---|---|
| **serdespy** | 教学/研究用 Python SerDes 工具箱 | 混模转换、FFE 零迫解、LMS、KP4/KR4 FEC、PRBS/PRQS | 无统计引擎、无 ADC 架构、无 CDR 动态、无定点 |
| **PyBERT** | 成熟的链路仿真 GUI 工具 | 逐级 h/s/p/H 四响应管线、抖动分解、IBIS-AMI、COM、bathtub | God-object 架构、GUI 耦合重、无 ADC-DSP、无定点 |
| **DragonPHY2** | 真实流片的 ADC-based PHY(RTL+验证) | MM-CDR、sliding-detector MLSD、Wiener 自适应、TI-ADC 校准、黄金模型 lockstep | 面向单一硅方案、无统计引擎、无通用信道层 |

---

## ① 已覆盖(与参考库对齐)

**相对 serdespy —— 基本是超集**

- 混模 4/8/12 端口转差分(`se2mm`)、广义 S 参数端接
- `freq2impulse` / `zero_pad` / 保守外推,往返恒等
- ZF-FFE(Toeplitz 求解)、MMSE-FFE、sign-sign LMS 双模式
- KP4(544,514,t=15)/ KR4(528,514,t=7)FEC + pre/post-FEC 换算
- PRBS7-31 + PRBS13Q/31Q + PRQS 码型与 checker

**相对 PyBERT —— 覆盖核心信号链**

- 逐级 h/s/p/H 四响应(`ResponseSet` 懒计算)
- Tx FIR + driver + 抖动注入(RJ/SJ/DCD/ISI)
- 行为级 DFE + bang-bang CDR
- StatEye 统计引擎 + bathtub / 浴盆曲线
- YAML 配置(frozen dataclass,参数单源)

**相对 DragonPHY2 —— 覆盖 ADC 架构行为级**

- 时间交织 ADC(offset/gain/skew/带宽失配、ENOB 噪声)
- 数字 FFE / DFE 波特率流水
- Mueller-Müller CDR(线性 PD + 二阶 PI)
- sliding-detector MLSD(错误事件纠正)
- 定点 int64 + 移位定标(RTL 语义)、`dump_vectors` 黄金向量出口

---

## ② 做得更好(超越任一单库)

1. **StatEye 全统计引擎** —— 三库皆无。PDF 卷积外推到 1e-15,与时域 MC 交叉校验
   (比值 1.03×),是本框架最大差异化增量。
2. **双 RX 架构公平对比** —— mixed-signal 与 ADC-DSP 共享 Tx/信道/分析层,差异
   限制在两个组装类内。serdespy/PyBERT 只有 mixed-signal,DragonPHY2 只有 ADC。
3. **架构探索 / reach 阶梯** —— 系统性量化 224G 深 LR 的 18→28→29→35→41 dB
   杠杆分解(MLSD +、DFE/deeper MLSD +、better ADC +6dB、级联 FEC +6dB,正交可叠加)。
4. **双 MLSD 实现 + 解析 MLSE 增益** —— Viterbi MLSE(最优)+ DragonPHY 式
   sliding-detector(低复杂度),`post_detect` 一行接到真实链路;`mlse_min_distance_sq`/
   `mlse_gain_over_dfe_db` 给出对理想 DFE 的渐近编码增益闭式解(1+D→3.01 dB、EPR4→6.02 dB,
   匹配滤波器界),示例 27 用它标定实测增益阶梯。DragonPHY2 只有 sliding-detector,
   serdespy/PyBERT 都没有。
5. **级联内码 FEC 模型** —— 内码硬判决块码 + RS-KP4 外码,把可容忍 pre-FEC BER
   抬高约 300×,支撑 800G/1.6T 深 LR。三库皆无。
6. **三层抖动分解** —— 图样平均 → 谱阈值 → 双 Dirac,回收误差 <10%。
7. **三档 mixed-signal 包络** —— NRZ 16 / PAM4 32 默认、舒适 24/32、极限 30/36,
   30 GBd 硬顶,经眼图扫描标定的产品级边界。
8. **unrolled DFE tap-1** —— speculative/展开首抽头,满足判决延迟约束。
9. **工程质量** —— numba JIT 热核(`HALO_NO_JIT=1` fallback)、106 个测试全通过、
   双引擎交叉校验、bit-true 定点路径。

---

## ③ 还没做到(真实缺口)

**相对 PyBERT**

- ✅ **IBIS-AMI 接口 + 真执行** —— **本轮补齐 + 升级为真跑编译模型**:`io/ami.py` 的
  `AmiModel`(Init/GetWave 双流)+ `NativeFirAmi` 参考模型 + `IbisAmiModel`(pyibisami
  后端,绑定厂商 .ami/.dll);**新增 `AmiCModel`**:通过真实 IBIS-AMI **C ABI**(ctypes)
  加载并执行一个**编译的共享库**——随仓库附带的参考模型 `io/ami_c/halo_fir_ami.c`
  实现 spec 的 AMI_Init/AMI_GetWave/AMI_Close 三入口,`build_reference_ami()` 用系统
  C 编译器现编译成 .so,与 `NativeFirAmi` 逐位一致(Init 精确、GetWave 机器精度),
  经引擎 Tx/Rx 槽两流验证;示例 29。厂商模型即 `load_ami_model(so_file=...)` 直接替换。
- ✅ **COM(Channel Operating Margin)** —— **本轮补齐 + 升级为标准 COM**:`io/ami.py` 的
  `ComAdapter` 有两个实现:`NativeCom`(基于均衡脉冲响应的透明行为级 RSS 图,快、易读)与
  `Com93a`(**忠实的 IEEE 802.3 Clause 93A/178A COM**,`analysis/com.py`:CTLE/DFE 网格按
  FOM 优化均衡器、DFE 抽头由光标经 b_max 上界导出、A_ni 从**卷积后的干扰+噪声 PDF**在目标
  DER 处读取——非高斯 RSS)。两者共用同一 `compute` 接口,官方 802.3 工具亦可经此接入;
  示例 `26_com_802p3.py`。
- ✅ **抖动分解接入管线** —— **本轮补齐**:`stage_jitter_budget`/`total_jitter` +
  时域引擎 `collect_jitter` 逐级预算(Tx/信道/CTLE 后)+ 示例 22。
- ✅ **时域 FEXT/NEXT 串扰 + 多 lane 环境** —— **本轮补齐**:`channel/crosstalk.py` 的
  `XtalkAggressor`,同一对象驱动时域(`xtalk=`)与统计(`xtalk_pulses=`)两引擎;
  `synthetic_aggressor` + `import_xtalk`(多端口 Touchstone 提取);`aggressor_bank`
  构建多侵略者 lane 组、`icn_rms`(行为级 MDFEXT/MDNEXT 功率和,~√N),同一 bank 直接喂
  802.3 COM 引擎作 σ_XT;示例 28 + GUI Crosstalk 标签多 lane 扫描。
- **Duobinary / PR 信道整形** —— 未建模
- **GUI** —— 纯脚本/库,无交互界面(设计取向,非缺陷)
- ✅ **多 lane 串扰系统级** —— **本轮补齐**(`aggressor_bank`/`icn_rms`,见上);单 lane 数据仍为主

**相对 DragonPHY2**

- **真实 RTL** —— 本框架是行为级黄金模型,`dump_vectors` 出口预留但无实际 SV
- **三视图方法学(model/rtl/fpga 一致性)** —— 单一 Python 视图
- **FPGA AMS 混合仿真** —— 无
- **物理实现流(综合/PnR/DRC)** —— 无(超出行为级范畴)
- **模拟前端电路深度** —— CTLE/VGA 为传函行为模型,非晶体管级
- **校准引擎(ADC unfolding 等实际算法)** —— 只建模失配,未实现片上校准回路
- **片上 BIST/DFT、JTAG** —— 无

---

## 附:各库独有、本框架有意不做

- **PyBERT GUI + 交互式扫描** —— 取向为库+脚本
- **DragonPHY2 完整硅后流程** —— 取向为行为级建模
- **serdespy 教学 notebook 体系** —— 以编号示例脚本替代

---

## 一句话总结

> **Halo_Serdes 在"行为级架构探索 + 统计/时域双引擎 + 双 RX 架构公平对比"这条主线上,
> 是三库的超集并有实质超越;真实缺口集中在 PyBERT 的产业接口(IBIS-AMI/COM)、
> 若干已实现但未接线的能力(抖动分解、时域串扰),以及 DragonPHY2 的硅实现全流程
> ——后者超出行为级框架的设计边界。本轮已补齐前三项软件可修复缺口。**

---

## 本轮补齐清单(2026-07)

| 缺口 | 交付 | 示例 | 测试 |
|---|---|---|---|
| 抖动分解未接入管线 | `analysis/jitter.py` 逐级预算 + 引擎 `collect_jitter` | `22_jitter_budget.py` | +4 |
| 无 IBIS-AMI / COM 接口 | `io/ami.py`:AmiModel/IbisAmiModel/NativeCom + 引擎 Tx/Rx 槽 | `23_ami_com.py` | +8 |
| 时域无 FEXT/NEXT 串扰 | `channel/crosstalk.py`:XtalkAggressor,双引擎共用 | `24_crosstalk.py` | +6 |

全部 122 测试通过(较补齐前 +18)。IBIS-AMI 与官方 COM 的实际后端为可选依赖,
接口与原生参考实现无外部依赖、始终可用。
