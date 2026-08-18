---
type: project_topic
status: active
summary: "Cairn 接入前既有知识资产的盘点(migration_mode=inventory_only):哪份文档装了什么、归谁维护、是否需要迁移"
tags: [inventory, documentation, migration]
contains: [asset-inventory, migration-decision]
created: "2026-08-18"
updated: "2026-08-18"
related: [architecture-invariants.md, engineering-pitfalls.md]
authoring_mode: ai_generated
---
# 既有知识资产盘点

Cairn 初始化时 `migration_mode` 选的是 `inventory_only`:**不改写任何历史文档**,
只盘清它们各装了什么、由谁维护,以及为什么不搬进 `cairn/`。

盘点时点:Cairn 初始化当天,仓库已有 62 个提交、297 项测试、31 个示例。

## 当前结论

### 面向使用者的文档(留在原位,不迁移)

| 文件 | 装了什么 | 为什么不进 `cairn/` |
|---|---|---|
| `README.md` | 门面:能力总览表、规模、路线图回顾、达标指标、架构包络约定、文档索引 | 面向使用者与评估者,是项目对外的第一入口 |
| `docs/USAGE.md` | 使用指南,按**任务**组织(配置/三引擎选型/读结果/换信道/扫描/串扰/COM/抖动/MLSD/FEC/AMI/定点/性能/FAQ) | 使用者文档;每段代码都对真实 API 验证过 |
| `docs/GUI.md`、`docs/GUI_tour.md` | GUI 安装启动、16 标签页用法、实拍图文导览 | 使用者文档 |
| `docs/SUMMARY.md` | 工程总结叙事:三次架构探索与结论 | 已是成型的叙事产物,拆开反而损失 |
| `docs/COMPARISON.md` | 与 serdespy / PyBERT / DragonPHY2 的逐项能力对比 | 对外定位文档 |
| `docs/serdes_opensource_repos_analysis.md` | 三个参考库的深度调研(设计蓝本,642 行) | 项目的起点材料,历史价值 |
| `rtl/README.md`、`packaging/README.md` | 各自子系统的专项说明 | 与代码同目录更易维护 |

### 面向协作者的知识(已迁入 `cairn/`)

| 来源 | 去向 | 说明 |
|---|---|---|
| 旧 `CLAUDE.md` 的架构不变量 + 代码/文档约定 | `cairn/architecture-invariants.md` | 完整保留并补了每条的"为什么" |
| 旧 `CLAUDE.md` 的"容易踩的坑" + 已知限制 | `cairn/engineering-pitfalls.md` | 补了触发条件与判别方法 |
| 旧 `CLAUDE.md` 的铁律清单 | `AGENTS.md`「项目铁律」(一行一条 + 指针) | 保持 AGENTS.md 可导航 |

### 项目状态类(留在根目录,由 AGENTS.md 指向)

| 文件 | 装了什么 | 与 Cairn 的关系 |
|---|---|---|
| `ROADMAP.md` | 尚未做的事,按"是否影响结论可信度"排序,每条附证据 | **代替 `cairn/ROADMAP.md`** —— 详见下方决策记录 |
| `CHANGELOG.md` | 已交付的演进,按里程碑组织 | 与 `cairn/LOG.md` 分工:CHANGELOG 记"交付了什么",LOG 记"每次协作推进了什么" |

## 决策记录

**不新建 `cairn/ROADMAP.md`。** Cairn 模板把 ROADMAP 放在 `cairn/` 下,但本项目根目录
已有一份内容充分、且被 README 文档索引引用的 `ROADMAP.md`。再建一份会立刻产生
两个真相源 —— 这正是 Cairn 要消除的问题。因此:根目录 `ROADMAP.md` 保持权威,
`AGENTS.md` 的阅读顺序第 2 步明确指向它。

**不迁移 `docs/` 下的使用者文档。** Cairn 管的是"协作者需要知道的项目知识",
`docs/` 管的是"使用者需要知道的用法"。两者读者不同、更新节奏不同,混在一起会
让 `cairn/` 变成第二个 docs 目录。`AGENTS.md` 的文档职责表里已写明这条分工。

**`CHANGELOG.md` 与 `cairn/LOG.md` 并存。** 前者是面向使用者的发布视角(按里程碑),
后者是面向协作者的过程视角(按次记录,含指针)。同一件事可以在两处各出现一次,
但粒度不同 —— 不是重复。

## 开放问题

- `LICENSE` 缺失,许可证类型需项目所有者决定(已记在 `ROADMAP.md` P3)。
- 若日后接入外部知识库(provider 目前暂缓对接),`docs/SUMMARY.md` 里的架构探索结论
  是最有毕业价值的候选 —— 它是跨项目可复用的方法论,而非本项目专有。
