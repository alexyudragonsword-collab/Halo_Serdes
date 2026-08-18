# Project Cairn 日志

本文件按倒序记录实质性进展 —— 最新条目在本行正下方。每条保持简短(摘要+指针),
结论沉淀进 `cairn/<topic>.md`。

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
