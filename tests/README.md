# tests —— 三层测试体系

| 层 | 位置 | 跑法 | 依赖 | 耗时 |
|---|---|---|---|---|
| **1. 行为测试** | `tests/behavior/` | `pytest`（默认即跑） | 无（离线，自动编译 Cython 测试模块） | ~2s |
| **2. e2e 回测** | `tests/test_strategy.py`、`tests/test_multi_instrument.py` | `pytest -m e2e` 或 `python tests/test_multi_instrument.py` | md-server(50051)、PG bt_trade、已注册 client | 分钟级 |
| **3. DB 反向验证** | `tests/verify_event_cash.py`、`tests/run_simulation.py` | `python tests/verify_event_cash.py --client <uuid> --cash 100000` | 同上 | 分钟级 |

`test_plot.py` 是 bokeh 手工可视化工具（非测试），已从收集中排除。

## 常用命令

```bash
PY=/Users/hengxinliu/Library/Caches/pypoetry/virtualenvs/bt-core-GmpHtvLH-py3.11/bin/python

$PY -m pytest                       # 行为测试 44 个（pyproject addopts 默认反选 e2e）
$PY -m pytest tests/behavior -q -k timer    # 单文件/关键字
$PY -m pytest -m e2e --collect-only -q      # 查看 e2e 用例（不执行）
$PY -m pytest -m e2e tests/test_multi_instrument.py   # 真跑 e2e（分钟级）
RUNTAG=$(date +%H%M%S) $PY -m pytest -m e2e tests/test_multi_instrument.py  # 重跑须换 RUNTAG
```

## 层间关系

```
行为测试(44)  ──改动 finance/timer/feed/linebuffer 必须全绿──▶  提交
e2e 回测      ──跑完写 PG bt_trade──▶  verify_event_cash.py 反向验证
                                              (费率恒等式/现金守恒/仓位轨迹逐行 diff)
```

- 行为测试的覆盖面、契约与已知坑见 [`tests/behavior/README.md`](behavior/README.md)。
- e2e 多标的的固定 client：`5a1f0c9e-2b7d-4e8a-9f30-6c4d1b2e7a55`；单标的：`e9f8cd38-e73c-453f-8a47-55beda640ae6`。
- `verify_event_cash.py` 默认取该 client 最新实验；验证口径（费率分界、T+1 次晨盖章自愈差异等）见脚本头注释与根目录 `agents.md` §6。

## 提交前检查单

1. 改 `.pyx/.pxd` 后 `setup.py build_ext --inplace` 再跑行为测试；
2. `pytest` 全绿（44+）；
3. `git status` 确认生成物（.cpp/.so/build/logs）未入库（已在 .gitignore）。
