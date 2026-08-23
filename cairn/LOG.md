# Project Cairn 日志

本文件按倒序记录实质性进展 —— 最新条目在本行正下方。每条保持简短(摘要+指针),
结论沉淀进 `cairn/<topic>.md`。

## 2026-08-23 · M0 判定:通过(run #3 三个 job 全绿)

- `assemble` / `wheel-versions` / `emulator` 全绿;仪器化测试
  `Starting 5 tests` → `Finished 5 tests`,零失败,含 `rtol=1e-9` 的 golden 比对。
  **Chaquopy 路线成立。**
- **`wheel-versions` 消掉了版本这一维**:numpy 1.26.2 + scipy 1.8.1(手机拿到的版本)
  下,BER 与现代版本只差 **1 ULP**(相对 1.6e-16),COM 与 post-FEC 逐位相同。
  pip 报的不兼容只在元数据层面。今后 golden 若失配,可干净归因给平台。
- **两格仍未知,只能由真机回答**:模拟器是 x86_64,证不了 ARM 浮点与 16 KB page;
  真机性能同理。判定表见 `android/README.md`。
- 上一条(下方)记的打包 bug 至此确认修复。

## 2026-08-23 · M0 在真机跑通到了"presets 没打包"这一层

- **好消息(M0 的主要风险已排除)**:`assemble` job 绿 → Chaquopy 的 Py3.10 **有 SciPy
  wheel**;APK 74.4 MB(2 ABI,压缩后),远低于估的 ~160 MB;真机与模拟器上解释器启动、
  numpy/scipy 加载并算出 `erfc`、Kotlin 侧门面可调用(`jsonFacadeIsReachableFromKotlin` 过)。
- **失败点是打包不是算法**:`configs/*.yaml` 在仓库根、不在 Python 源码树,
  Chaquopy `srcDirs` 没带上 → `preset_names()` 退化 → `load_preset` 静默回退到
  `LinkConfig()`(默认 touchstone 无文件)→ 引擎抛 `channel.file is unset`。
  真机与 CI 模拟器给出同一个错误。
- 三处修复:Gradle `stageHaloAssets` 把 `configs/` 拷进 assets;`HaloPython.start()`
  解压并在 `Python.start()` **之前**导出 `HALO_SERDES_DATA_DIR`;`load_preset()` 改为
  **抛异常而非回退**。新增 `presetsSurvivedThePackaging`(仪器化)与
  `test_host_can_relocate_the_data_dir`(宿主)两道防线。
- **新发现待验**:Chaquopy 实际给的是 numpy 1.26.2 + **scipy 1.8.1**(pyproject 声明
  `scipy>=1.11`),pip 自报这对不兼容。新增 `wheel-versions` job 在宿主上降到这两个版本
  跑手机的计算路径 —— golden 比对现在跨"版本 + 平台"两个变量。
- 坑已沉淀进 `cairn/engineering-pitfalls.md` 新增的「打包 / 跨平台类」;
  经过与结构见 `android/README.md`。

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
