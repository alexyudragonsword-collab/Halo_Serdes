# Android 编译版(Cython → `.so`)评估

> 状态:**宿主侧已验证,交叉编译未做**(本机没有 NDK)。是否落地取决于一个尚未
> 回答的决定 —— 见文末「未决」。

## 这件事买到的是什么(先说清楚,免得高估)

把 Chaquopy APK 解开就能拿回 Python:载荷里是 `.py` 或 `.pyc`,`strings` 一跑就出
函数名、行号和整段 docstring。Cython 编译把这部分换成机器码,**仅此而已**:

- **未编译的模块照旧是字节码**,上面那些东西一样都不少。
- **字面常量在编译后仍然存在**,它们进的是常量池,按值搜就能搜到。
- **运行时可达的东西就是可达的** —— 界面显示的字段,任何能跑这个 app 的人都读得回来。

诚实的一句话:它把读**算法**的成本从「解压就能看」抬到「得反汇编」。它不是授权校验,
不是数据保护,也不遮蔽界面已经展示的任何内容。

## 编译集:53 个模块里编 46 个

`halo_serdes` 48 个模块 + `halo_serdes_app` 5 个。排除 7 个,每一个都有具体理由:

| 排除 | 数量 | 原因 |
|---|---:|---|
| `config/loader.py` | 1 | **Cython 3.3.0 自身崩溃**:`generate_keyvalue_args` 里 `TypeError: sequence item 0: expected str instance, NoneType found`,触发点是 `dataclasses.replace(obj, **{parts[0]: value})`。不是本项目的 bug。 |
| `cdr/adc_kernel.py`、`cdr/kernels.py`、`core/prbs.py`、`dsp/fixed_datapath.py`、`dsp/kernels.py`、`dsp/mlsd.py` | 6 | **numba 层**。这 6 个模块都是 `numba.njit(...)(_py_fn)` 包在 `try: ... except ImportError` 里。numba **不能 njit 一个 cython 函数**,而它抛的不是 `ImportError`,所以那层保护接不住 —— 编了就把宿主套件打红。铁律 #4 说 numba 是性能层不是正确性层,这里正是那条铁律的边界。 |

编译集(照抄可用):

```
--package halo_serdes --compile \
  __init__,afe,analysis,channel,engine,io,tx,fec,config/__init__,config/schema,\
cdr/__init__,core/__init__,core/fixed,core/mapping,core/waveform,dsp/__init__,dsp/ffe
--package halo_serdes_app --compile all
```

## 验收:删掉 `.py` 之后全套测试

**编译集是一个断言 —— 断言这些 `.py` 删掉之后测试套件照样全绿。** 所以就照这个断言验:

| | 解释版基线 | 编译版 |
|---|---|---|
| 测试 | 356 passed, 1 skipped | **356 passed, 1 skipped** |
| skip 的那一个 | `test_rtl_lockstep.py`(没装 iverilog) | 同一个 |
| 导入的是什么 | `src/halo_serdes/__init__.py` | `site-packages/halo_serdes/__init__.so` |
| wheel | — | 2256 KiB,46 `.so`,7 `.py`(即上表 7 个) |

**控制组**:断言前先确认 `halo_serdes.__file__.endswith('.so')`。否则"全绿"可能只是
证明它根本没装上、跑的还是源码树。

**docstring 泄漏检查**同样带控制组:先在一个**未**编译模块的 `.pyc` 里 grep 同类短语
(命中 1 次,证明 grep 本身有效),再去 12 个编译模块的 `.so` 里 grep —— **0 命中**。
少了前半步,后半步的 0 什么也不说明。

## 两处结构性不匹配(skill 脚本 vs 本仓库)

skill 的 `android_wheel.py` 假设「一个发行版 = 一个可导入包」,本仓库不是:
`halo-serdes` 这一个发行版下有 `halo_serdes` / `halo_serdes_app` / `halo_serdes_gui`
三个包,而 app 需要**前两个装在一起**。

1. **一包一 wheel → 文件名相撞。** 两次调用产出的 wheel 同名同 dist-info,装第二个
   会把第一个卸掉。需要一个合并步骤:解开两个 wheel 到同一棵树、**重算 `RECORD`**
   (后解开的那个覆盖了前一个的 RECORD,只列了一半文件,pip 装的时候会校验它)、重新打包。
2. **wheel 组装无条件丢弃 `.c`。** 那行是为了丢掉 Cython 生成的中间 C,却连带丢掉了
   `io/ami_c/halo_fir_ami.c` —— 本项目当作 package data 发布的**手写** C 参考模型。
   现象:wheel 正常产出、脚本自检通过,只有 `test_ami_c.py` 的 6 个测试报
   `RuntimeError: reference ...`,离真正原因隔着两层。判据要改成「只丢**旁边有同名
   `.so`** 的 `.c`」—— 生成的 C 必然有,当数据发布的 C 必然没有。

## 未决

CI 里没有装 skill,要让编译版进 CI,就得把那个 414 行的 `android_wheel.py`
**vendor 进本仓库**。它没有 license 头也没有版权声明,而本仓库是公开的 —— 这个决定
留给仓库所有者。三条路:(a) vendor,CI 全自动;(b) 不 vendor,编译版只在装了 skill
且有 NDK 的机器上手动出;(c) 只保留本文档的宿主侧结论,编译版暂缓。

**另外,交叉编译本身还没被证明过。** 干净的交叉编译和一个长得对的 wheel,都不等于
在真机上 `import` 成功 —— 尤其 Chaquopy 的目标是 Python 3.10 而本次验证跑在 3.11,
Cython 生成的 C 会用到内部头文件。真要落地,`--target-version` 必须对齐 Chaquopy
实际下载的那个 target 构建,并且**在设备上跑一次真实计算**才算数。
