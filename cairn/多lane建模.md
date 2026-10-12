---
type: project_topic
status: active
summary: "多 lane 数据通路的范围评估(ROADMAP P3 #9):现状盘点、两个探针(误码成簇对 KP4 的影响、串扰相位 / lane 间 skew 对双引擎的影响)、802.3 的 FEC 交织结构(检索摘要)、拆分与顺序"
tags: [multi-lane, crosstalk, skew, fec, interleave, burst, kp4, invariant-3, cdr]
contains: [scope-assessment, measurement, verification-table, gap]
created: "2026-10-13"
updated: "2026-10-13"
related: [architecture-invariants.md, DSP发端与PR.md, 光互联建模.md]
authoring_mode: ai_generated
---
# 多 lane 数据通路:范围评估

> ROADMAP P3 #9 原文:"多 lane 目前只在串扰侧;真正的多 lane 系统级(lane 间 skew、共享 CDR/校准、per-lane FEC 交织)是另一个量级的工作,
> 按需再评估。" 这里先量后拆,不写库代码;两个探针的脚本在会话 scratchpad,数字可按下表的配置复现。

## 当前结论

### 1. 现状

| 方面 | 已有 | 没有 |
|---|---|---|
| 串扰 | `channel/crosstalk.py`:`XtalkAggressor`(耦合冲激 + 独立随机数据)、`synthetic_aggressor`、`aggressor_bank`(N 个 FEXT / NEXT、耦合 ±2 dB 散布)、`icn_rms`、`import_xtalk`(多端口 Touchstone);时域 `run_time_link(xtalk=…)`、统计 `run_statistical(xtalk_pulses=…)`、流式引擎;COM;GUI 串扰页;应用的 `multilane_study`(ICN ≈ √N 与 COM 随 aggressor 数) | 串扰不在 `LinkConfig` 里(是 API 入参,铁律 1 的缺口);aggressor 相对受扰 lane 的相位(skew)、频偏(准同步)都不是参数 |
| FEC | `fec/rs.py`:KP4 / KR4 编解码(galois)、`pre_to_post_fec_ber`(按"比特误码互相独立"的二项投影,docstring 自己写着突发会破坏这个假设)、级联内码 | 误码位置 → FEC 符号 → 码字的映射;lane 间复用(比特 / 符号对)与码字交织 |
| 误码位置 | `BerResult.error_idx`(符号下标),每次时域运行都有 | — |
| 时钟 | 每条 lane 自己的 CDR;收发时钟剖面 | 多 lane 共享 CDR / 参考时钟;lane 间相关抖动 |

### 2. 探针一:误码成簇,以及它对 KP4 的影响

每条链路一次时域运行(100 万符号;示例 42 的链路用它的 `make_cfg`),相隔 ≤ 8 个符号的误码算一个事件;按"5 个 PAM4 符号 = 一个
10 bit RS 符号、连续映射"(单 lane、不交织)数每个事件碰到几个 RS 符号,与二项投影的 RS 符号误码率 1 − (1 − BER)^10 比:

| 链路 | BER | 误码 / 事件 | 符号 / 事件 | 多符号事件 | RS 符号 / 事件(1 / 2 / 3) | RS SER 实测 / 二项 |
|---|---|---|---|---|---|---|
| PAM4 32G 混合信号(预置,DFE4,噪声 10.5 mV) | 3.21e-4 | 638 / 635 | 1.00 | 0 % | 0.997 / 0.003 / 0 | 3.20e-3 / 3.20e-3 |
| deep-LR ADC(预置,FFE + DFE8,3.4 mV) | 1.67e-3 | 3312 / 2893 | 1.14 | 13 % | 0.961 / 0.039 / 0 | 1.51e-2 / 1.65e-2 |
| 224G ADC 106 GBd(预置,DFE1,3.0 mV) | 4.51e-3 | 8960 / 7218 | 1.24 | 20 % | 0.920 / 0.075 / 0.004 | 3.94e-2 / 4.42e-2 |
| 示例 42 链路,delta + Viterbi mem2,0.24 m | 1.53e-2 | 30410 / 18149 | 1.68 | 43 % | 0.792 / 0.178 / 0.026 | 1.14e-1 / 1.43e-1 |
| 示例 42 链路,1 + aD + Viterbi mem2,0.26 m | 4.29e-3 | 8518 / 2663 | 3.20 | 99 % | 0.578 / 0.391 / 0.027 | 1.95e-2 / 4.20e-2 |

- DFE 链路在 KP4 门限附近几乎不成簇(DFE4 在 3.2e-4 时 0 %),二项投影就是对的;ADC + DFE8 / DFE1 在 1e-3 量级成簇 13–20 %。
- **PR 目标 + Viterbi 的误码天生成串**(1 + aD:99 % 的事件跨多个符号,平均 3.2 个)。在连续映射下一个事件多半只落进 1–2 个 RS 符号,
  RS 符号误码率只有二项投影的 0.46 倍 —— 也就是说,示例 36 / 38 / 42 这类按 BER 做 KP4 投影的 reach 在单 lane 连续映射下**偏悲观**。
- 但 802.3 的多 lane 结构恰好打散这种成串(下表):比特复用把线上相邻的比特送进不同的 PCS lane / RS 符号,码字交织再把它们分到不同码字。
  所以"哪种映射"才是问题的关键,不能只看单 lane;这正是 #9 里"FEC 交织"那一步要回答的。BER 都没正好落在门限上(MLSD 两点偏高),
  比值是方向性的。

802.3 的 FEC 交织结构(IEEE 原文被代理拦截,以下是任务组材料经检索摘要的转述,**二手**):

| 场合 | 结构 | 出处 |
|---|---|---|
| 400GBASE-R(802.3bs) | 两个 RS(544,514) 码字按 10 bit 符号交织,棋盘式分到 16 条 PCS lane,PMA 做比特复用;一个持续 75 个 PAM4 符号的突发有很大概率碰到 16 个 FEC 符号(不可纠) | Anslow 3bs 2015-09;802.3df Ran 2022 |
| 200G/λ(802.3df / dj) | 改用符号对复用(Ran 2022:相关误码下符号复用明显更好,8:1 比特复用不可接受);200GE / 400GE 在 PMA 做 4 码字交织(只有两个 RS 编码器,奇数 PCS lane 延迟两个码字),lane 间 skew 不对齐码字边界会破坏它 | Ran 3df 2022-11;He 3dj 2023-07;Shrikhande 3dj 2024-06 |
| DFE 误码传播模型 | 1 抽头 DFE 的 Gilbert 模型,传播概率 a,最坏取 0.5–0.75(a ≥ 0.6 时开预编码);预编码把长度 L 的突发变成两个 PAM4 符号误码 | Ran 3df 2022-11;He 3df 2022-11 |

### 3. 探针二:串扰相位(lane 间 skew)

`aggressor_bank` 的 4 个 aggressor(FEXT / NEXT 各半),把每个耦合冲激整体延后 0…7/8 UI(等于同步 lane 间的固定 skew),时域 30 万符号;
统计引擎用同一组 `aggressor.pulse()`:

| 链路 | 无串扰 时域 / 统计 | 时域 BER,aggressor 延后 0 / 1/8 / … / 7/8 UI | 统计(带串扰) | 统计 / 时域 |
|---|---|---|---|---|
| PAM4 32G 混合信号,4 × −30 dB,噪声 6 mV | 0 / 7.6e-7 | 3.13e-2 7.39e-3 6.71e-3 6.81e-3 8.18e-3 1.11e-2 1.68e-2 2.56e-2 | 3.81e-2 | 1.2–5.7× |
| NRZ 28G analytic,4 × −27 dB,噪声 10 mV | 0 / 9.9e-16 | 9.15e-4 1.14e-3 1.76e-3 3.22e-3 7.75e-3 1.81e-2 2.95e-2 5.59e-3 | 3.76e-2 | 1.3–41× |

- 串扰代价随 aggressor 相位变 4.7×(PAM4)/ 32×(NRZ):lane 间 skew 是串扰的一个一阶参数。
- **统计引擎取的是每个 aggressor 自己的峰值相位**(`statistical.py`:`xpk = argmax |pulse|`,按它取游标),等于每个 aggressor 同时处在最坏相位;
  时域则是耦合冲激碰巧对齐到哪就是哪(延后 0 只是 `synthetic_aggressor` 的构造,不是物理 skew)。两者在串扰下的比值随 skew 从 1.2× 到 41×,
  **铁律 3 在串扰下没被覆盖**:`tests/test_crosstalk.py` 只检查两个引擎都变差,不检查比值;`statistical.py` 顶部的近似清单里串扰一条
  ("独立平稳干扰")也没写"取峰值相位"。
- 准同步 lane(各自参考时钟、有 ppm 频偏)的 aggressor 相位会扫过一个 UI,物理上对应相位平均,两个引擎都没有这种模式。

### 4. 共享 CDR / lane 间 skew 预算

- 静态部分(一个 CDR 的相位给 N 条 lane 用,lane 间有固定 skew)等于每条 lane 在偏离自己最佳相位 skew 处的 BER —— 统计引擎已经报 BER 随相位
  (浴盆),读出来就是 skew 预算,不需要改引擎。
- 动态部分(主 lane 的 CDR 跟踪抖动,从 lane 带 skew 跟着走;lane 间相关抖动)要多 lane 同时跑的时域内核,是另一个量级,按需。

### 5. 拆分与顺序(写进 ROADMAP)

1. **P2 #19 串扰下双引擎口径不一致**(一致性,先做):两个引擎用同一个、显式的 aggressor 相位(同步 lane 给 skew;统计引擎按它取游标
   而不是峰值),补铁律 3 在串扰下的测试,把口径写进 `statistical.py` 的近似清单。证据见 §3。
2. **P3 #9a FEC 映射与交织**:误码位置(各 lane 的 `error_idx`)→ 按复用方式(单 lane 连续 / N lane 符号对复用 / 比特复用)与码字交织(1 / 2 / 4)
   映射到 FEC 符号 → 每码字的 FEC 符号误码分布 → FER(按事件率 × 实测事件足迹外推)。不改引擎;用它回头读示例 36 / 38 / 42 的
   PR reach 在 802.3 映射下是否偏悲观。证据见 §2。
3. **P3 #9b 准同步 lane 与串扰入配置**:频偏(时域按 ppm 重采样、统计引擎按相位平均),aggressor 环境进 `LinkConfig`(铁律 1)。依赖 #19。
4. **P3 #9c 共享 CDR**:先做静态 skew 预算(读浴盆);动态的多 lane 环路按需。

PCS 层的 deskew、对齐标记锁定、lane 重排是数字协议行为,留在「边界 — 有意不做」。

## 资料来源

- 802.3bs:Anslow,https://www.ieee802.org/3/bs/public/15_09/anslow_3bs_03_0915.pdf
- 802.3df:Ran,https://grouper.ieee.org/groups/802/3/df/public/22_11/ran_3df_01a_2211.pdf ;He,https://ieee802.org/3/df/public/22_11/he_3df_01_2211.pdf ;
  Ran 2022-05,https://grouper.ieee.org/groups/802/3/df/public/22_05/22_0518/ran_3df_01a_220518.pdf
- 802.3dj:He,https://www.ieee802.org/3/dj/public/23_07/he_3dj_01_2307.pdf ;Shrikhande,https://grouper.ieee.org/groups/802/3/dj/public/24_06/shrikhande_3dj_01a_2406.pdf
- RS(544,514) 与预编码:Anslow 3cd 2016-07,https://www.ieee802.org/3/cd/public/July16/anslow_3cd_01_0716.pdf
- 以上页面都被出口代理拦截(403),内容来自检索摘要。
