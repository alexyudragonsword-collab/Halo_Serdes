---
type: project_topic
status: active
summary: "两个引擎对外部独立实现(serdespy 1.0)的对照:信道 |H|、脉冲峰值处 SER,发现冲激截断丢长尾 ISI(ROADMAP P1 #42);哪些没校验、为什么没有别的参考"
tags: [validation, serdespy, touchstone, impulse-trim, isi-tail, cross-check]
contains: [comparison-table, finding, scope, pitfall]
created: "2026-10-30"
updated: "2026-10-30"
related: [architecture-invariants.md, engineering-pitfalls.md]
authoring_mode: ai_generated
---
# 对外校验(external validation)

两个引擎互相对照(铁律 3)查不出它们**共用**的那一步错:信道导入、脉冲构造、电平与噪声约定。这里记与外部独立实现的对照。
复现:`tools/external_check.py`(用法见其 docstring;serdespy 不是依赖,解开 wheel 传目录,4 核约 2 分钟)。

## 当前结论(2026-10-30)

### 1. 参考选了什么,为什么只有它

- **serdespy 1.0**(PyPI,Richard Barrie / 多伦多大学,MIT):自己的 Touchstone 混模 + 端接(`four_port_to_diff`)、
  冲激与补零(`zero_pad` / `freq2impulse`)、接收机(`Receiver.noise` / `slice_signal` / `nrz_DFE_BR` / `pam4_DFE_BR`)。
  与我们的实现没有共用代码 —— 我们的 `channel/analytic.py` 移植自它的 `chmodel`,但这里只用到 Touchstone 路径,没有用 RLGC。
- **没做成的**:IEEE 802.3 COM 的参考信道与参考结果(本环境经代理取不到 IEEE 的表格和信道文件);SerDes PyBERT
  (PyPI 上的 `pybert` 是同名的 NLP BERT 包,GitHub 源码仓经代理 403);公开测量数据。见 ROADMAP P3 #12。
- **共用、因此没校验的**:CTLE 的定义(一零两极,serdespy 没有 CTLE,对方那边用 `scipy.signal.freqs` 按同样的角频率算);
  噪声约定(接收端每样本 `noise_rms` 的 AWGN);发端电平(`swing` 为峰峰值)。**CDR 没校验**:serdespy 没有 CDR,两边都在脉冲峰值采样。
  ADC、FFE 求解、PR / MLSD、光路段都不在范围内。

### 2. 信道:与 serdespy 一致到 1e-15 dB(对称化后)

三个自带 s4p,|H| 到 −40 dB 的最大差:

| 文件 | 文件网格(混模 + 端接) | 我们的模型(插值到含 DC 的网格) |
|---|---|---|
| TEC_Whisper42p8in | 0.000 dB | 0.013 dB |
| Case4_FM_13SI | 0.057 dB | 0.058 dB |
| peters_01_0605 | 0.037 dB | 0.051 dB |

- 后两个的 0.04–0.06 dB(都在 −38 dB 附近,−20 dB 以上 ≤ 0.01 dB)是 **serdespy 的**:它的端口重排(1↔2 行列交换)之后
  把 `s[1,2]` / `s[2,1]` 按原值写回,对非互易文件等于把 S23 放到了 S32 的位置。把文件的 S23 / S32 取平均后两边差 3e-15 dB;
  Whisper 文件本身 S23 = S32,所以是 0。我们的两种端接(`terminate_renormalize` / `terminate_gamma`)彼此一致。
- 插值那一列多出的 ≤ 0.014 dB 来自插值;Case4 / peters 从 50 MHz 起,我们外推 DC、serdespy 不外推,比较从文件第二个点的两倍频率起。

### 3. 链路 SER:相同相位、相同噪声

Whisper 背板,4 抽头 DFE(理想抽头 = 脉冲后光标),脉冲峰值采样,60 万符号;数值是对 serdespy MC 的比值,括号里是误码数。
我们的统计引擎用无操作 FFE `[1, 0]` 读(静态引擎的读法:无环路、峰值采样)。"整段"是不截断冲激(§4)。

| 链路 | serdespy MC | 静态 MC | 统计 | 静态,整段 | 统计,整段 | 教科书(无误码传播) |
|---|---|---|---|---|---|---|
| NRZ 10 Gb/s,无 CTLE,30 mV | 5.65e-4(339) | 0.97(330) | 0.87 | 0.95(322) | 0.87 | 0.76 |
| NRZ 16 Gb/s,CTLE 7 dB,25 mV | 8.14e-4(488) | 0.82(402) | 0.81 | 0.98(477) | 0.91 | 0.84 |
| PAM4 16 GBd,CTLE 7 dB,4 mV | 5.71e-3(3425) | 0.70(2412 bit) | 0.79 | 1.01(3458 bit) | 1.20 | 1.03 |

- **整段冲激下两个引擎都与 serdespy 一致**:静态 MC 0.95–1.01×(在计数误差内),统计 0.87–1.20×。截断时 CTLE 的两条链路偏低 0.70–0.82×,
  NRZ 10G 不受影响(没有 CTLE 时尾巴相对眼高小)。
- 统计引擎与 MC 的剩余差是误码传播:统计按第一抽头两态链乘 ×1.13–1.28(`dfe_ep_factor`),MC 里四个抽头都传播;
  教科书列不含误码传播,NRZ 两条比 MC 低 16–24 %,与这个解释同向。PAM4 的教科书列 1.03× —— serdespy 的 PAM4 判决阈值放在
  `main_cursor ×` 电平中点,与我们同;那里误码传播与截断的尾巴量级相当,不细分。
- 快速版(15 万符号,`--quick`)同号:NRZ 10G 0.93 / 0.85,PAM4 截断 0.67 / 0.77、整段 1.03 / 1.17。

### 4. 发现:冲激截断丢掉慢尾 ISI(ROADMAP P1 #42)

PAM4 那条链路两个引擎都比 serdespy 低,而 serdespy 的 MC 与教科书 ISI 枚举一致。逐项对过之后:脉冲主光标 0.18796 / 0.18793、
前 7 个后光标差 < 0.5 %,CTLE 在 8 GHz 两边都是 1.5507 —— 不是信道也不是 CTLE。差在**长度**:`ChannelModel.response_set` 按
**一阶差分**能量 99.99 % 截断(`trim_impulse`,PyBERT 的准则),慢衰减的尾巴差分小、积分不小,Whisper 16 GBd 只留 13.9 UI;
serdespy 留 400 UI。主光标后 5–24 UI 游标 rms 0.021(我们 0.020),24–64 UI 0.0039、64–400 UI 0.0014(我们 0)。
PAM4 半眼 0.031 V、噪声 4 mV,丢掉的尾巴与噪声同量级。整段冲激(`_untrimmed`)重跑:静态 0.70× → 1.01×、统计 0.79× → 1.20×;NRZ 16G 0.82 / 0.81 → 0.98 / 0.91(§3 表)。

预设(统计引擎默认读数,截断 / 整段):NRZ 16G 9.63e-9 / 2.30e-8(×2.4)、NRZ 28G 2.4e-33 / 5.0e-32(×21)、NRZ 32G ×1.01、
PAM4 32G ×1.02(它本就留 2043 UI)。ADC 预设不带 FFE 时统计读数 0.15–0.20 不是链路读数,没比。三个引擎(静态、时域、统计)
和 COM 都走 `response_set`,所以双引擎 2× 内吻合照旧 —— 两边丢的一样。没修:改准则会动指纹、预设、示例输出和引用它们的文档,单独一项。

## 坑(contains)

- **serdespy 的端接参数是对文件单端 z0 的**:`four_port_to_diff(network, port_def, source, load)` 的反射系数用 `network.z0[0,0]`(50 Ω),
  而它的混模矩阵是差分 100 Ω 参考。匹配的差分负载要传 `(50, 50)`(它自己的示例 `1_channel.py` 就这么传);传 `(100, 100)` 差 1.8–3.3 dB
  (`docs/serdes_opensource_repos_analysis.md` §2 已记为 serdespy 的缺陷,Phase 0 移植时对照过)。`port_def` 参数不被使用(端口顺序写死为 1→2、3→4 直通)。
- **serdespy 的 `Receiver.noise` 用全局 `np.random`**,复现要先 `np.random.seed`。`Transmitter` 依赖 `samplerate`(C 扩展),本工具只用
  `Receiver`,导入前桩掉该模块。`chmodel.sparam` 用了 numpy 2 已删除的 `np.complex_`,不要调。
- **统计引擎的环路模型会让对照很慢**:把 bang-bang 增益调到 2^-14 / 2^-28 想"关掉"环路,码型-相位链要迭代很多步才收敛,
  一条链路十分钟以上。改用无操作的两抽头 FFE `[1, 0]`:有 FFE 时混合信号统计引擎不建环路、在脉冲峰值采样,正是静态引擎的读法。
