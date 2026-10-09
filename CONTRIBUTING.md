# 参与贡献

这是一个行为级 SerDes 仿真框架,它的产品是**结论**:哪条链路够得着、哪种接收机更合适、哪个杠杆值多少 dB。
所以对改动的要求不在代码量,而在可证明:要么证明它没让任何数悄悄变了,要么说清楚它让哪些数变了、为什么。

## 先读

1. [`AGENTS.md`](AGENTS.md):六条铁律与阅读导航(`CLAUDE.md` 只是指向它的一行)。
2. [`cairn/architecture-invariants.md`](cairn/architecture-invariants.md):每条铁律为什么存在、靠什么执行。
3. [`cairn/engineering-pitfalls.md`](cairn/engineering-pitfalls.md):踩过并付出过代价的坑,开工前读一遍。
4. [`ROADMAP.md`](ROADMAP.md):待办;每条附证据(复现方式、测到的数、文件),接手前先看证据。

## 环境

```bash
pip install -e ".[gui,fec,jit,test]"     # 开发环境
pip install ruff                         # lint
sudo apt-get install -y iverilog         # 可选:RTL lockstep
```

Python 3.10 与 3.11 都要通过:3.10 是 `requires-python` 的下限,也是 Android(Chaquopy)的目标版本。
手机上只有 numpy + scipy —— 计算路径不能依赖 matplotlib、numba、galois、Dash(CI 的 `import-clean` 守着)。

## 推送前

与 CI 相同的三条:

```bash
ruff check src/ tests/ examples/ tools/ android/tools/ android/app/src/main/python/
pytest -q                     # 全套;JIT + iverilog 时约 12 分钟
HALO_NO_JIT=1 pytest -q       # 纯 Python 内核:铁律 4,结果必须一致
```

按改动再加:

| 改了什么 | 再做什么 |
|---|---|
| 引擎、内核、DSP、配置 —— 任何本不该改数的改动 | 在基线提交上 `python tools/fingerprint.py record before.json`,改完 `record after.json`,再 `compare before.json after.json`。重构必须逐位相同;修复只许动它解释得了的数,并在 PR 里列出。工具列出的报错条目要逐个看:两边报同样的错也算"相同" |
| 定点数据通路或 `rtl/` | `bash rtl/run_lockstep.sh`(要 iverilog;没有 iverilog 时 `tests/test_rtl_lockstep.py` 会跳过,不算通过) |
| 示例、结果字段、配置字段名 | `python tools/run_examples.py --smoke` |
| 可能改示例输出数字的改动(库、示例、`configs/`、`data/`) | 全尺寸跑 `python tools/run_examples.py --jobs 4 --save new/`(只动了几个示例时按编号只跑那几个),再 `python tools/example_drift.py compare new/`:列出变了的输出行,以及文档里仍引用旧值的行。改完文档后 `accept new/` 刷新 `examples/expected/`,与改动放在同一个 PR。CI 的 `examples-full` 做同样的比对。推导出来的数(两个输出之差、比值)工具看不见,要自己查 |
| `src/halo_serdes/vendor/` | 不要原地改;从上游重新拷贝,跑 `python tools/vendor_check.py --fail-on-skip --siblings pll_simulator=<上游检出>`(没有上游检出时文件被跳过,不算通过;约定见该目录的 `__init__.py`) |
| 共用层 `src/halo_serdes/`、`src/halo_serdes_app/` | Android 与桌面打包会被 CI 触发,见下面"两端验证" |

## CI 守什么

| workflow | job | 守什么 | 何时跑 |
|---|---|---|---|
| `test` | `lint` | ruff,只拦真缺陷(E9 + pyflakes) | 每个 PR;push 时纯文档改动除外 |
| | `test`(3.10 / 3.11 × jit / nojit) | 全套测试 | 同上 |
| | `examples` | 每个示例以 smoke 尺寸真跑 | 同上 |
| | `import-clean` | 不装 `[gui]` 时 app 层与手机计算路径能导入 | 同上 |
| | `vendor-drift` | vendored 文件与钉住的上游一致 | 同上 |
| | `rtl-lockstep` | SystemVerilog 与 Python 黄金模型逐位一致 | 同上 |
| `android` | `assemble`、`wheel-versions`、`emulator`(API 34 / 35)、`compiled-apk`、`compiled-emulator` | 能否打包;在手机的 numpy / scipy 版本上跑手机的计算路径;仪器化测试(解释版与 Cython 编译版) | push 改动 `android/`、共用层、`configs/`、`data/channels/`、`data/clock_profiles/`;PR 改动 `android/` |
| `examples-full` | `full` | 每个示例按原尺寸跑,输出与 `examples/expected/` 逐行比对(`tools/example_drift.py`);不一致即红,日志里列出仍引用旧值的文档行,输出存为 artifact | PR / push main 改动库、app 层、示例、`configs/`、`data/`、`pyproject.toml`;每周一次(依赖升级也会改数) |
| `build-windows-desktop` | `pyinstaller`、`nuitka`、`nuitka-onefile` | 冻结后的桌面包能启动(Nuitka 冷编译 1.5–2 小时,有缓存 30–42 分钟,见 `packaging/README.md`) | push 改动共用层、`src/halo_serdes_gui/`、`configs/`、`packaging/`、`pyproject.toml`;`v*` tag 时发布 |

## 两端验证(铁律 6)

动了共用层,桌面与 Android 两端都跑通才算完成 —— 两端的依赖版本、子模块加载、文件系统语义都不同,
桌面全绿不能证明手机能跑,反之亦然。Android 的判据不是绿勾,而是 `emulator` job 日志末尾那行:

```
instrumented totals: N tests, 0 failures, 0 errors, 0 skipped
```

(`connectedDebugAndroidTest` 一个测试都没跑也算成功。)本地构建见 [`android/README.md`](android/README.md)
「本地怎么跑」,桌面打包见 [`packaging/README.md`](packaging/README.md)。

## 约定

- 代码注释、docstring、图表文字用英文;`README.md`、`docs/`、`CHANGELOG.md`、`ROADMAP.md`、`cairn/` 用中文。
  这是有意的,不要"统一"。
- 注释写**为什么**(物理或数值上的取舍、已知近似),不写是什么。
- 新配置参数三步:子配置 dataclass 加字段 → `__post_init__` 里校验(只拦真正非法的值,规则跟着引擎的实际语义走)→
  `halo_serdes_app/config_bridge.py` 的 `SECTIONS` 加一行(GUI 与手机的表单由它生成)。不要加全局状态(铁律 1)。
- 两种接收机的差异只放在 `engine/timedomain.py` 的两个组装函数与各自专属模块里(铁律 2)。
- 波形域与符号域之间只经显式采样器(铁律 5)。
- 新算法配闭式解单测(解析 BER、退化情形、恒等式),不只 smoke;涉及两个引擎的,纯 LTI + AWGN 下要在 2× 内吻合(铁律 3)。
- 新示例按编号 `examples/NN_name.py`,它同时是文档。README 里的示例数、标签页数由 `tests/test_docs_fresh.py` 守着;
  测试数没有自动检查,加减测试后同步 `README.md`、`docs/SUMMARY.md`、`docs/COMPARISON.md`、`docs/summary.html`。

## 记录

- [`CHANGELOG.md`](CHANGELOG.md):按里程碑追加。
- [`ROADMAP.md`](ROADMAP.md):做完就删,在 CHANGELOG 记一笔;新发现的问题连同证据写进来 —— 没有证据的条目会被当作猜测。
- [`cairn/LOG.md`](cairn/LOG.md):实质性推进在顶部加一条(≤ 20 行,摘要 + 指针);结论写进 `cairn/` 对应的专题文档。
  修正旧判断时追加更正说明,不要静默覆盖;没确认的判断不要写成事实。

## PR

- 从 `main` 开分支,一个 PR 做一件事;不改写别人分支的历史。
- 描述里写:改了什么、为什么、怎么验证的(测试数、指纹结论,动了共用层时附 Android 的 instrumented totals)。
- 合并用 merge commit。

## 许可

MIT,见 [`LICENSE`](LICENSE)。`src/halo_serdes/vendor/` 下的文件来自其他仓库,文件头写明来源与提交,保留原样。
