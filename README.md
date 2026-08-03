# bt_core

> 面向 A 股的量化回测与仿真交易框架。基于 backtrader 的 Lines/元类架构重构，剥离传统 broker/feed/store 耦合，引入 Cython 性能层 + Actor 异步执行层 + 共享内存零拷贝通信，完整支持 T+1、涨跌停、除权除息、退市摘牌等 A 股特有规则。

---

## 目录

- [1. 项目定位](#1-项目定位)
- [2. 核心特性](#2-核心特性)
- [3. 架构总览](#3-架构总览)
- [4. 目录结构](#4-目录结构)
- [5. A 股交易逻辑](#5-a-股交易逻辑)
- [6. 性能优化](#6-性能优化)
- [7. 风控系统 Pnc](#7-风控系统-pnc)
- [8. 快速开始](#8-快速开始)
- [9. 构建与安装](#9-构建与安装)
- [10. 扩展开发](#10-扩展开发)
- [11. 开发约定](#11-开发约定)

---

## 1. 项目定位

`bt_core` 是一个**专为 A 股市场设计**的量化回测与在线仿真交易框架。

| 维度 | 说明 |
|------|------|
| **架构根基** | 继承 backtrader 的 Lines 时间序列抽象与元类参数系统 |
| **执行模型** | 用 Actor 模型 + asyncio 重构交易执行层，分离策略计算与订单撮合 |
| **性能层** | Cython 编译关键热路径（仓位/现金/日期/SHM），生产级性能 |
| **市场适配** | 原生支持 T+1、涨跌停、前复权、除权除息事件、退市摘牌 |
| **协议层** | 复用 `bt_sdk` / `bt_protocol`，gRPC + Protobuf 通信 |

---

## 2. 核心特性

### 🇨🇳 A 股原生支持

- **T+1 交易约束**：当日买入次日方可卖出，由 `Position.update()` 强制校验
- **价格限制**：支持涨跌停价位的订单拒绝逻辑（`Asset` 提供 `limit_up` / `limit_down`）
- **除权除息事件**：分红送股、转增股本、配股的自动化处理（`Position.process_events`）
- **退市摘牌**：非吸合并退市自动结转为已实现亏损，吸合并自动转换 SID
- **前复权**：行情数据按复权因子前向调整 OHLC 与成交量
- **交易日历**：内置 A 股交易日历，含 2016 年熔断特殊时段

### ⚡ 性能优化

- **环形缓冲区（QBuffer）**：`LineBuffer` 支持 `qbuffer(savemem=1)` 模式，仅保留 minperiod 长度的滑动窗口
- **Cython 热路径**：`Pnc`、`Sizer`、`Timer`、`Position`、`Account`、`Simulator` 等均用 Cython 编译
- **共享内存（SHM）**：跨进程零拷贝传递指标/订单/快照（`SharedRingBuffer`）
- **Actor 异步执行**：订单撮合与持久化解耦，`BatchWriterActor` 批量落盘

### 🔌 协议解耦

- **Store 单例**：统一封装行情 `MdApi` 与交易 `TdApi`
- **Broker 转发**：`BTBroker` 仅做协议转换，不含资金/仓位逻辑
- **Snapshot 协议**：所有状态变更通过 `SnapshotBody` 推送到 SHM

---

## 3. 架构总览

```
┌─────────────────────────────────────────────────────────────┐
│                       引擎层（Python）                       │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌─────────┐  │
│  │ Cerebro  │──▶│ Strategy │──▶│  Timer   │   │ Analyzer│  │
│  └────┬─────┘   └────┬─────┘   └──────────┘   └─────────┘  │
│       │ _runnext()   │ on_risk / on_trade                    │
└───────┼──────────────┼──────────────────────────────────────┘
        │              │
        ▼              ▼
┌─────────────────────────────────────────────────────────────┐
│                    数据层（Python + Cython）                │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌─────────┐  │
│  │   Feed   │──▶│LineBuffer│──▶│ Indicator│──▶│Resampler│  │
│  └──────────┘   └──────────┘   └──────────┘   └─────────┘  │
│       环形缓冲区 QBuffer + minperiod 聚合传播                │
└─────────────────────────────────────────────────────────────┘
        │              │
        ▼              ▼
┌─────────────────────────────────────────────────────────────┐
│                执行层（Cython + asyncio）                    │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌─────────┐  │
│  │   Pnc    │──▶│Simulator │──▶│ Position │   │ Account │  │
│  │ (风控)   │   │ (撮合)   │   │ (仓位)   │   │ (现金)  │  │
│  └──────────┘   └────┬─────┘   └──────────┘   └─────────┘  │
│       ▲              │ on_dt_over                            │
│       │              ▼                                       │
│  ┌────┴─────┐   ┌──────────┐   ┌──────────────────────┐    │
│  │  Store   │◀──│ RpcGateway│  │ BatchWriterActor     │    │
│  │ (单例)   │   │ (gRPC)   │   │ (Parquet 持久化)     │    │
│  └──────────┘   └──────────┘   └──────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
```

### 运行时调用链路

```
Cerebro.run()
  ├── 准备阶段：data._start() → Timer.start() → Strategy._start()
  └── 主循环 _runnext()
        ├── data.next()           # 加载新 bar
        ├── _check_timers()       # 调度定时器
        │     ├── METRIC  → publish metrics to SHM
        │     ├── RISK    → Strategy.on_risk() → Pnc.on_risk()
        │     ├── TRADE   → Strategy.on_trade() → Pnc.generate_plan()
        │     └── dt_over → Store.on_dt_over() (T+1 解锁/事件同步)
        └── Strategy._next()
              ├── Indicator._next()   # 级联计算
              └── user next()
```

---

## 4. 目录结构

```
bt_core/
├── __init__.py              # 公共 API 统一导出
├── cerebro.py               # 回测引擎主控
├── strategy.py              # Strategy / SignalStrategy
├── signal.py                # 信号类型
├── feed.py                  # AbstractDataBase / DataClone
├── dataseries.py            # DataSeries / OHLC / TimeFrame / _Bar
├── resamplerfilter.py       # 数据重采样
├── linebuffer.py            # LineBuffer 环形缓冲区
├── lineroot.py              # LineRoot / LineSingle / LineMultiple
├── lineseries.py            # LineSeries / Lines / LineAlias
├── lineiterator.py          # LineIterator / IndicatorBase / StrategyBase
├── indicator.py             # Indicator 元类
├── analyzer.py              # Analyzer 元类
├── broker.py                # BrokerBase
├── store.py                 # Store 元类（Singleton）
├── sizer.pyx/.pxd           # 【Cython】Sizer 基类
├── pnc.pyx/.pxd             # 【Cython】仓位/现金/风控控制器
├── timer.pyx/.pxd           # 【Cython】定时器
├── tradingcal.py            # A 股交易日历（含熔断）
├── optimizer.py             # 参数优化器
├── metabase.py              # 元类基础设施
├── flt.py / logic.py        # 过滤器 / Lines 运算逻辑
├── log.py / errors.py
│
├── analyzers/               # 分析器（Sharpe/DrawDown/Calmar/SQN/...）
├── brokers/                 # Broker 实现（BTBroker/IBBroker/Oanda/VcBroker）
├── feeds/                   # 数据源（CSV/Pandas/Parquet/IB/Oanda/InfluxDB）
├── filters/                 # 数据过滤器（Session/CalendarDays）
├── indicators/              # 技术指标（SMA/EMA/MACD/ATR/RSI/Bollinger/...）
├── stores/                  # Store 实现（LocalStore/IBStore/OandaStore）
│
├── execution/               # ★ 执行层（Actor + Simulator）
│   ├── trade_api.pyx        #   交易 API（TdApi）
│   ├── actor/               #   BatchWriterActor（批量落盘）
│   ├── gateway/             #   RpcGateway（gRPC 通信）
│   └── core/
│       ├── engine/          #   Engine（事件循环）
│       └── finance/         #   ★ 金融核心（Cython）
│           ├── simulate.pyx #     Simulator / TrackerActor
│           ├── position.pyx #     Position（T+1/事件处理）
│           ├── account.pyx  #     Account（现金管理）
│           ├── asset.pyx    #     Asset（行情/事件元数据）
│           ├── order.pyx    #     Order（订单）
│           ├── filler.pyx   #     PseudoFiller（成交价模拟）
│           ├── cash.pyx     #     CashManager
│           ├── cache.pyx    #     AssetCache
│           └── common.pxd   #     共享数据结构（EventItem/AdjustmentData/...）
│
├── shm/                     # 【Cython】共享内存环形缓冲区
├── sink/                    # 【Cython】日志/数据消费线程
└── utils/                   # 工具（dateintern/autodict/encoder/wrapper）
    ├── dateintern.pyx       #   【Cython】日期转换（epoch↔YYYYMMDD）
    └── util.pyx             #   【Cython】通用工具
```

---

## 5. A 股交易逻辑

### 5.1 T+1 交易约束

A 股实行 T+1 制度：当日买入的股票次日方可卖出。框架在多个层面强制该约束：

| 层面 | 实现 | 说明 |
|------|------|------|
| **Position** | `update()` 校验 `available` | 卖出时检查可卖份额，不足抛异常 |
| **Position** | `on_dt_over()` 解锁 | T 日收盘后将 `size` 同步到 `available` |
| **Pnc** | `already_held` 判断 | 用 `pos.size > 0`（而非 `available`）判断已持仓，防止重复买入 |
| **Pnc** | 涨停卖单重发 | 昨日涨停未成交的卖单自动重新挂单（`pending_sells` 机制） |

### 5.2 除权除息事件处理

每个交易日的 T-1 收盘后，框架通过 gRPC 拉取当日除权除息事件并应用到持仓：

```cython
# simulate.pyx: _sync_event
for adj_data in deref(adj_it).second:   # 支持同 sid 多事件累积
    temp.event_type = 0                  # 0 = 除权除息
    temp.adj = adj_data                  # bonus_share / transfer / bonus
    v_events.push_back(temp)

# position.pyx: process_events
# 除权除息：送股(bonus_share) + 转增(transfer) → 持仓量增加，成本摊薄
size *= sizer_ratio
cost_basis /= sizer_ratio
# 现金分红(bonus) → 直接增加账户现金
event_cash += bonus * origin_size
```

**事件类型**：
- `event_type = 0`：除权除息（送股/转增/分红）
- `event_type = 1`：配股（rights issue），按配股比例新增股份

### 5.3 退市摘牌处理

```cython
# position.pyx: _dt_over
if asset_core.delist > 0 and asset_core.delist <= end_dt:
    if not asset_core.merger.empty():
        # 吸合并：旧 SID 转换为新 SID，持仓保留
        self._handle_merger(asset_core.merger)
    else:
        # 非吸合并退市：投资成本直接结转为已实现亏损
        self.core.realized_pnl -= self.core.size * self.core.cost_basis
        self.core.size = 0
        self.core.available = 0
```

### 5.4 前复权

行情数据按复权因子前向调整：
- OHLC 价格：`price / factor`（向前除权）
- 成交量：`volume * factor`（向前还原）

---

## 6. 性能优化

### 6.1 环形缓冲区（QBuffer）

`LineBuffer` 支持两种模式：

| 模式 | 触发 | 内存占用 | 适用场景 |
|------|------|----------|----------|
| `UnBounded` | `savemem=0` | 完整历史 | 调试/绘图 |
| `QBuffer` | `savemem=1`（默认） | 仅 `minperiod` 长度 | 生产回测 |

```python
# cerebro.py
Cerebro.run(savemem=1)  # 启用环形缓冲区
```

环形缓冲区通过 `idx % maxlen` 实现索引映射，避免数组扩容与内存拷贝。

### 6.2 Cython 热路径

以下模块均用 Cython 编译为 C++ 扩展：

| 模块 | 职责 | 关键优化 |
|------|------|----------|
| `pnc.pyx` | 风控/调仓计划 | C++ STL `unordered_map` / `vector` |
| `position.pyx` | 仓位/事件处理 | `cdef struct` 零开销 |
| `simulate.pyx` | 订单撮合 | `cpdef` 双接口 |
| `timer.pyx` | 定时器调度 | ` nogil` 并行潜力 |
| `dateintern.pyx` | 日期转换 | 纯 C 实现 |
| `shm_buffer.pyx` | 共享内存 | 原子操作无锁 |

编译选项（生产级性能，需谨慎保证索引安全）：
```ini
boundscheck = False
wraparound = False
initializedcheck = False
cdivision = True
language_level = 3
```

### 6.3 共享内存（SHM）

`SharedRingBuffer` 实现跨进程零拷贝通信：

```python
# 指标/订单/快照通过 SHM 推送到独立 Writer 进程
strategy.shm_chan.publish_metric(b"sma_20", value, dts)
strategy.shm_chan.publish_snapshot(snapshot)
strategy.shm_chan.publish_order(order)
```

- **生产者**：Strategy / Pnc / Simulator
- **消费者**：`LogConsumerThread`（异步落盘 Parquet）
- **零拷贝**：基于 POSIX shared memory，无序列化开销

### 6.4 Actor 异步执行

执行层采用 Actor 模型解耦：

```
Strategy（同步） ──submit()──▶ Simulator ──run_coroutine_threadsafe──▶ TrackerActor（异步）
                                         │
                                         ├── process_order() → Position.update()
                                         ├── on_dt_over()    → rpc fetch + event sync
                                         └── BatchWriterActor → Parquet 批量落盘
```

- 策略线程同步调用 `submit()`，立即返回最新 `SnapshotBody`
- 订单撮合、事件同步、持久化在独立 asyncio 事件循环中异步执行

---

## 7. 风控系统 Pnc

`Pnc`（Position and Cash controller）是 Cython 实现的风控核心，提供两个入口：

### 7.1 `on_risk()` — 实时风控

在每个 bar 或定时器触发时调用，执行：

1. **最大持仓数约束**：超限时按"非 topk 优先 + 持仓最久优先"卖出
2. **个股止损**：`pnl_ratio <= stop_loss`（默认 -10%）触发卖出
3. **组合回撤清仓**：`maxdd >= dd`（默认 25%）全仓卖出

### 7.2 `generate_plan()` — 调仓计划

在调仓定时器触发时调用，生成买卖计划：

```
Phase 1: 清理已完成的 pending_sells
Phase 2: 强制执行 max_positions 约束
Phase 3: 计算 Sizer 权重
Phase 4: 重发昨日涨停未成交的卖单（pending_sells）
Phase 5: 正常卖出信号（持仓周期到期 / 不在 topk）
Phase 6: 计算可买入 slot 数
Phase 7: 现金控制（cash <= 10000 时停止买入）
Phase 8: 买入信号（topk 股票，按权重下单）
```

### 7.3 涨停卖单重发机制

A 股涨停时无法买入，跌停时无法卖出。框架通过 `pending_sells` 机制确保跌停未成交的卖单次日自动重发：

```cython
# generate_plan Phase 4
if self.pending_sells.find(c_sid) != self.pending_sells.end():
    # 重发卖单，priority=0（最高优先级）
    sells.push_back(tmp)
```

---

## 8. 快速开始

### 最小可运行示例

```python
from dotenv import load_dotenv
load_dotenv()

import bt_core as bt
from bt_core.cerebro import Cerebro
from bt_core.sizer import Sizer

# 1. 创建引擎
cerebro = Cerebro(client_id="my_strategy", savemem=1)

# 2. 配置 Store（行情 + 交易）
cerebro.addstore("local", timeout=10)

# 3. 配置 Sizer（仓位权重计算）
class EqualWeightSizer(bt.Sizer):
    def _getsizing(self, topk_info, snapshot, isbuy):
        from libcpp.unordered_map cimport unordered_map
        cdef unordered_map[cpp_string, double] result
        n = len(topk_info)
        for sid in topk_info:
            result[sid] = 1.0 / n
        return result

cerebro.addsizer(EqualWeightSizer)

# 4. 配置 Pnc（风控参数）
cerebro.addpnc(bt.Pnc,
    days_held=5,        # 持仓周期
    stake=0.9,          # 单只股票最大仓位比例
    dd=0.25,            # 最大回撤阈值
    stop_loss=-0.1,     # 个股止损线
    max_positions=5)    # 最大持仓数

# 5. 添加策略
class MyStrategy(bt.Strategy):
    def next(self):
        if self.data.close[0] > self.data.open[0]:
            # 策略逻辑...
            pass

cerebro.addstrategy(MyStrategy)

# 6. 添加定时器
cerebro.add_timer(
    when=bt.timer.SESSION_START,
    event_type=bt.timer.TRADE)  # 开盘触发调仓

cerebro.add_timer(
    when=bt.timer.SESSION_END,
    event_type=bt.timer.METRIC)  # 收盘记录指标

# 7. 运行
results = cerebro.run(
    fromdate="2023-01-01",
    todate="2024-01-01",
    cash=1000000)

# 8. 获取分析结果
strat = results[0][0]
for name, analyzer in strat.stats.items():
    print(f"{name}: {analyzer.get_analysis()}")
```

### 运行测试

```bash
poetry shell
python tests/test_strategy.py    # 基础策略示例
python tests/test_plot.py        # Bokeh 可视化
```

> ⚠️ 测试脚本依赖 `LocalStore` → `bt_sdk` 的 `MdApi` / `TdApi`，需配置 `.env` 并确保行情/交易服务可用。

---

## 9. 构建与安装

### 9.1 环境准备

```bash
# Python 3.11+ + Poetry 1.8+
poetry install --no-root    # 仅安装依赖
poetry install              # 安装本项目
```

> `bt-sdk`、`bt-protocol` 等包可能需要配置私有 PyPI 源（默认 `localhost:3141`）。

### 9.2 编译 Cython 扩展

```bash
# 方式 1：原地编译（开发调试）
python setup.py build_ext --inplace

# 方式 2：构建 wheel
poetry build

# 方式 3：Poetry 构建钩子
python build_ext.py
```

**编译产物**（已被 `.gitignore` 忽略）：
- `.so` / `.pyd`：动态链接库
- `.cpp`：Cython 生成的中间文件

### 9.3 Docker

```bash
docker build -t bt_core .
```

基于 `python:3.11.5-slim`，预装 `build-essential` + Poetry。

---

## 10. 扩展开发

| 扩展目标 | 基类 | 重写方法 |
|----------|------|----------|
| 自定义策略 | `bt.Strategy` | `next()` / `on_trade()` / `on_risk()` |
| 自定义指标 | `bt.Indicator` | `next()` / `__init__()` |
| 自定义分析器 | `bt.Analyzer` | `next()` / `get_analysis()` |
| 自定义 Sizer | `bt.Sizer`（Cython） | `_getsizing()` |
| 自定义数据源 | `bt.feed.DataBase` | `_load()` / `_start()` |
| 自定义 Broker | `bt.BrokerBase` | `submit()` / `cancel()` |
| 自定义 Store | `bt.Store` | `start()` / `on_dt_over()` |

### 自定义策略示例

```python
class RotationStrategy(bt.Strategy):
    params = (('period', 20), ('topk', 5))

    def __init__(self):
        # 添加指标（自动注册到策略）
        self.sma = bt.indicators.SMA(self.data.close, period=self.p.period)

    def on_trade(self, current_dts):
        # 调仓定时器触发
        current_day = ts2intdt(current_dts)
        topk = self.datas[-1].get_topk(current_day)
        snapshot = self.get_snapshot()
        plan = self.pnc.generate_plan(current_day, topk, snapshot)
        self.sell(plan[b"sell"])
        self.buy(plan[b"buy"])

    def on_risk(self, current_dts):
        # 风控定时器触发
        snapshot = self.get_snapshot()
        sell_plans = self.pnc.on_risk(snapshot, self.stats, ts2intdt(current_dts))
        if sell_plans:
            self.sell(sell_plans)
```

### 自定义 Sizer 示例

```cython
# my_sizer.pyx
from bt_core.sizer cimport Sizer
from libcpp.string cimport string as cpp_string
from libcpp.unordered_map cimport unordered_map

cdef class EqualWeightSizer(Sizer):
    cpdef unordered_map[cpp_string, double] _getsizing(
        self,
        unordered_map[cpp_string, double] topk_info,
        object snapshot,
        bint isbuy) except *:
        cdef unordered_map[cpp_string, double] result
        cdef double weight = 1.0 / topk_info.size()
        cdef cpp_string sid
        for sid in topk_info:
            result[sid] = weight
        return result
```

---

## 11. 开发约定

### 11.1 Cython 扩展约定

- `.pyx` 源码与 `.pxd` 声明文件成对出现
- 编译选项：`-O3 -std=c++11`，`language="c++"`
- 性能指令（生产环境需谨慎保证索引安全）：
  ```ini
  boundscheck = False
  wraparound = False
  initializedcheck = False
  cdivision = True
  ```

### 11.2 命名规范

- 缩进：4 个空格
- 类名：`CamelCase`（`Cerebro`、`LocalStore`、`EqualWeightSizer`）
- 函数/变量：`snake_case`
- 私有成员：以 `_` 开头
- 类参数：`params = (("name", default), ...)`，通过 `self.p.name` 访问

### 11.3 文件头

每个 `.py` 文件顶部包含统一的 GPL v3 版权头：

```python
#!/usr/bin/env python
# -*- coding: utf-8; py-indent-offset:4 -*-
###############################################################################
# Copyright (C) 2015-2023 Daniel Rodriguez
# ... GPL v3 ...
###############################################################################
```

### 11.4 安全注意事项

1. **私有 PyPI 源**：`pyproject.toml` 配置了 `devpi` 源，CI/其他机器需替换为可用地址
2. **`.env` 文件**：包含敏感配置，已加入 `.gitignore`
3. **Cython 编译安全**：`boundscheck=False` 等指令跳过运行期检查，修改 Cython 代码务必保证索引安全
4. **资金管理**：`Pnc` 与 `Sizer` 直接控制资金/仓位，修改前务必充分回测

---

## 关键配置文件速查

| 文件 | 作用 |
|------|------|
| `pyproject.toml` | Poetry 项目元数据、依赖、包源、构建后端 |
| `poetry.lock` | 依赖锁定 |
| `setup.py` | Cython 扩展模块定义 |
| `build_ext.py` | Poetry 构建钩子 |
| `MANIFEST.in` | 发布包额外文件清单 |
| `Dockerfile` | 容器构建 |
| `deploy.sh` | 部署脚本 |
| `.gitignore` | 忽略编译产物、虚拟环境、日志、Parquet、.env |
| `docs/ARCHITECTURE.md` | 详细架构文档 |
| `docs/ashare_bugfix_audit.md` | A 股逻辑审计记录 |
| `docs/ashare_logic_refactor.md` | A 股逻辑重构说明 |

---

## License

GPL v3（继承自 backtrader）