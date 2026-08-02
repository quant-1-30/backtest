# bt_core 架构与模块交互链路文档

---

## 1. 总体架构概览

`bt_core` 是一个量化回测与仿真交易框架，设计上参考了 **backtrader** 的 Lines/元类架构，同时深度集成了 `bt_sdk` 的协议层（`OrderBody`、`SnapshotBody` 等），以支持高性能回测与在线交易的统一抽象。

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              User Strategy                                   │
│                     (继承 Strategy → 重写 next/buy/sell)                      │
└──────────────────────────────┬──────────────────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────────────────┐
│                              Cerebro                                         │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐ │
│  │   DataFeeds │  │  Strategies │  │   Observers │  │     Analyzers       │ │
│  │  (feed.py)  │  │(strategy.py)│  │(observer.py)│  │    (analyzer.py)    │ │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  └─────────────────────┘ │
│         │                │                │                                 │
│         └────────────────┴────────────────┘                                 │
│                          │                                                  │
│                    ┌─────▼──────┐                                           │
│                    │  _runnext  │  ← 主循环：逐 bar 推进                     │
│                    └────────────┘                                           │
└──────────────────────────────┬──────────────────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────────────────┐
│                               Store (Singleton)                              │
│  ┌─────────────────────────────┐  ┌────────────────────────────────────────┐│
│  │         Data Feed           │  │              Broker                    ││
│  │    (LocalStore._feed)       │  │      (LocalStore.broker / BTBroker)    ││
│  │   → 行情数据 (MdApi)        │  │   → 订单/持仓/资金查询 (TdApi)         ││
│  └─────────────────────────────┘  └────────────────────────────────────────┘│
└──────────────────────────────┬──────────────────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────────────────┐
│                         bt_sdk / Execution Layer                             │
│         (TradeApi, OrderBody, SnapshotBody, AsyncRunner, Actor)              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. 核心模块职责

### 2.1 元类与基础架构层 (`metabase.py`, `lineroot.py`, `linebuffer.py`, `lineseries.py`)

| 文件 | 核心职责 |
|------|---------|
| `metabase.py` | 定义 `MetaBase`、`MetaParams`、`AutoInfoClass`。所有框架类通过元类实现参数自动注入 (`params`) 与生命周期钩子 (`donew` / `dopreinit` / `doinit` / `dopostinit`)。 |
| `lineroot.py` | 定义 `LineRoot`、`LineSingle`、`LineMultiple`，是 Lines 体系的根类。 |
| `linebuffer.py` | 定义 `LineBuffer`，实现时间序列的核心缓冲区。索引 `0` 表示当前值，正索引表示历史值。支持 `forward()`、`advance()`、`qbuffer()`（内存节省模式）。 |
| `lineseries.py` | 定义 `LineSeries`、`Lines` 类，管理多条 `LineBuffer` 的集合，提供 `_derive` 动态派生子类。 |
| `lineiterator.py` | 定义 `LineIterator`，是 `Strategy`、`Indicator`、`Observer` 的公共基类。负责 `_clock` 同步、`_next()` 调度、`qbuffer()` 级联。 |

**Lines 索引语义**：
- `line[0]` = 当前 bar 的值
- `line[-1]` = 上一个 bar 的值
- `line[1]` = 未来值（若已 extend）

### 2.2 数据层 (`feed.py`, `dataseries.py`, `feeds/`)

| 文件/目录 | 核心职责 |
|-----------|---------|
| `dataseries.py` | 定义 `DataSeries`、`OHLC`、`OHLCDateTime`、`TimeFrame`、`_Bar`。`DataSeries` 继承 `LineSeries`，默认 Lines 为 `open/high/low/close/volume/amount/datetime`。 |
| `feed.py` | 定义 `AbstractDataBase`（即 `DataBase`），是所有数据馈送的抽象基类。实现数据状态机（`CONNECTED` ~ `UNKNOWN`）、`_start()` / `_load()` / `next()` 生命周期、filter/resample 支持。 |
| `feeds/` | 具体数据实现。当前主要使用 `pqtfeed.ParquetData`（读取 parquet 文件）和 `rpcfeed`。 |

### 2.3 策略层 (`strategy.py`)

| 类/方法 | 职责 |
|---------|------|
| `Strategy` | 用户策略基类。继承自 `LineIterator` → `LineSeries` → `LineRoot`。自动绑定 `datas`、`indicators`、`observers`、`analyzers`。 |
| `next()` | 用户重写：在每个 bar 到达时执行交易逻辑。 |
| `prenext()` | 在最小周期（`minperiod`）未满足前调用。 |
| `nextstart()` | 仅在第一个满足 `minperiod` 的 bar 调用一次，默认调用 `next()`。 |
| `buy()` / `sell()` | 构造 `OrderBody` 并提交到 `store.submit()`。支持 `plimit`、`execType`、`filler`（`oco/occ/smooth/likehood`）。 |
| `notify_timer()` | 定时器回调，触发 `store.on_dt_over()` 进行日终结算（T+0 / T+1）。 |
| `notify_trade()` | 接收订单回报与成交列表，更新内部 `_orders`、`_trades`、`snapshot`。 |
| `_next()` | 框架内部调度：更新时钟 → 计算 `minperstatus` → 调用 observers/analyzers → 调用用户 `next()`。 |
| `_next_flat_fast()` | 性能优化路径：扁平化 indicators 调用链，避免递归开销。 |

### 2.4 指标层 (`indicator.py`, `indicators/`)

| 文件 | 职责 |
|------|------|
| `indicator.py` | `MetaIndicator` 元类 + `Indicator` 基类。`_ltype = LineIterator.IndType`。支持对象缓存 (`_icache`)。 |
| `indicators/` | 内置技术指标：SMA、EMA、MACD、ATR、Stochastic、RSI 等。 |

### 2.5 观察器层 (`observer.py`, `observers/`)

| 文件 | 职责 |
|------|------|
| `observer.py` | `MetaObserver` + `Observer` 基类。`_ltype = LineIterator.ObsType`。默认 `prenext()` 调用 `next()`。 |
| `observers/` | 内置观察器：`Broker`（现金/市值）、`BuySell`（买卖点标记）、`Trades`、`DrawDown`、`TimeReturn`、`Benchmark`。 |

### 2.6 分析器层 (`analyzer.py`, `analyzers/`)

| 文件 | 职责 |
|------|------|
| `analyzer.py` | `MetaAnalyzer` + `Analyzer` 基类。非 Lines 对象，但生命周期与 Strategy 同步（`start` / `stop` / `prenext` / `next` / `nextstart`）。支持父子嵌套 (`_children`)。 |
| `analyzers/` | 内置分析器：Sharpe、Returns、DrawDown、TradeAnalyzer、Transactions、SQN、VWR 等。 |

### 2.7 经纪商层 (`broker.py`, `brokers/`)

| 文件 | 职责 |
|------|------|
| `broker.py` | `BrokerBase` 抽象基类。定义 `cancel()`、`stop()`、`_start()`。通过 `tdapi` 与交易网关交互。 |
| `brokers/btbroker.py` | `BTBroker`：默认实现，封装 `TdApi` 调用（`register` / `set_cash` / `submit` / `getvalue` / `on_dt_over`）。 |
| `brokers/` | 其他 broker 适配（IB、Oanda、VC 等，当前主要为 BTBroker）。 |

### 2.8 存储/连接层 (`store.py`, `stores/`)

| 文件 | 职责 |
|------|------|
| `store.py` | `MetaStore`（单例模式）+ `Store` 基类。`BrokerCls` / `DataCls` 自动注册。管理 `_orderspending`、`_tradespending`。 |
| `stores/localstore.py` | `LocalStore`：实际使用的单例 Store。初始化 `MdApi`（行情）和 `TdApi`（交易），创建 `AsyncRunner` 事件循环。封装所有 broker/data API。 |
| `stores/__init__.py` | 注册 `_stores = {"local": LocalStore}`。 |

### 2.9 Sizer / 风控层 (`sizer.py`, `sizers/`, `pnc.pyx`)

| 文件 | 职责 |
|------|------|
| `sizer.py` | `Sizer` 基类。定义 `getsizing(topk_info, snapshot, isbuy)` 接口。 |
| `sizers/generic.py` | 四种 Sizer：`FixedSize`、`WeightedSizer`、`TurtleSizer`（ATR 风控）、`KellySizer`。 |
| `pnc.pyx` | **Cython 实现的仓位与现金控制器**。`Pnc` 类负责：1) 风控硬止损（`p_tolerance` / `act_tolerance`）；2) 先卖后买逻辑；3) 锁仓逻辑（`lock_days`）；4) 调用 Sizer 计算目标权重。 |

### 2.10 引擎层 (`cerebro.py`)

| 方法 | 职责 |
|------|------|
| `__init__` | 初始化 `datas`、`strats`、`observers`、`indicators`、`signals`、`writers`、`_plot`。 |
| `adddata()` | 添加 Data Feed。支持 `dmaster=True` 自动插入主数据。 |
| `resampledata()` | 通过 `DataClone` + `Resampler` 实现数据重采样。 |
| `addstore()` | 根据名称从 `_stores` 创建 Store 单例。 |
| `addcontrol()` | 创建 `Pnc` 实例（风控 + Sizer）。 |
| `addstrategy()` | 注册策略类及参数，延迟到 `run()` 实例化。 |
| `run()` | **核心入口**：1) 准备数据 `_start`；2) 准备 Timer；3) 实例化 Writer；4) 调用 `runstrategies()`。 |
| `runstrategies()` | 实例化策略 → 添加默认 observers（若 `stdstats=True`）→ 添加用户 observers/indicators → 调用 `strat._start()` → `_runnext()` → `_stop()`。 |
| `_runnext()` | **主循环**：对每个 data 调用 `next()` → 检查 timer → 对每个 strategy 调用 `_next()` → 调用 writers。 |
| `plot()` | 调用 `Plot` 进行可视化。 |

---

## 3. 类继承体系

```
MetaBase (metabase.py)
  └── MetaParams (metabase.py)
        ├── MetaStore (store.py)
        │     └── LocalStore (stores/localstore.py) [Singleton]
        ├── MetaBroker (broker.py)
        │     └── BrokerBase (broker.py)
        │           └── BTBroker (brokers/btbroker.py)
        ├── MetaAnalyzer (analyzer.py)
        │     └── Analyzer (analyzer.py)
        │           └── [各类分析器 in analyzers/]
        ├── MetaStrategy (strategy.py)
        │     └── Strategy (strategy.py)
        │           └── SignalStrategy (strategy.py)
        │           └── [用户自定义策略]
        ├── MetaLineIterator (lineiterator.py)
        │     └── LineIterator (lineiterator.py)
        │           ├── LineSeries (lineseries.py)
        │           │     └── DataSeries (dataseries.py)
        │           │           └── OHLC → OHLCDateTime
        │           │                 └── AbstractDataBase (feed.py)
        │           │                       └── [具体 Feed in feeds/]
        │           ├── IndicatorBase
        │           │     └── Indicator (indicator.py)
        │           │           └── [各类指标 in indicators/]
        │           ├── ObserverBase
        │           │     └── Observer (observer.py)
        │           │           └── [各类观察器 in observers/]
        │           └── StrategyBase
        │                 └── Strategy (见上)
        ├── MetaIndicator (indicator.py)
        │     └── Indicator (见上)
        └── MetaObserver (observer.py)
              └── Observer (见上)

LineRoot (lineroot.py)
  ├── LineSingle
  │     └── LineBuffer (linebuffer.py)  ← 核心数值缓冲区
  └── LineMultiple
        └── Lines (lineseries.py)       ← LineBuffer 的集合

Sizer (sizer.py)
  └── [FixedSize, WeightedSizer, TurtleSizer, KellySizer]

Pnc (pnc.pyx) [Cython]
  └── TraderPlan
```

---

## 4. 运行时调用链路

### 4.1 初始化阶段 (`run()` 之前)

```
用户代码
  │
  ├─→ cerebro = bt.Cerebro()
  │
  ├─→ cerebro.addstore("local", ...)        → Store 单例创建
  │     └─→ LocalStore.__init__()
  │           ├─→ 创建 MdApi → _feed (Data Feed)
  │           ├─→ 创建 TdApi + BatchWriterActor
  │           └─→ 创建 BTBroker(tdapi=tdapi)
  │
  ├─→ cerebro.addcontrol(lock_days=5, sizer_name="fixed")
  │     └─→ Pnc(lock_days, sizer_name)       → 风控+Sizer初始化
  │
  ├─→ cerebro.addstrategy(MyStrategy, ...)   → 仅注册到 cerebro.strats
  │
  ├─→ cerebro.addobserver(multi, obscls)     → 注册到 cerebro.observers
  │
  └─→ cerebro.addindicator(indcls)           → 注册到 cerebro.indicators
```

### 4.2 运行阶段 (`cerebro.run()`)

```
Cerebro.run(**kwargs)
  │
  ├─→ 1. 数据准备
  │     ├─→ adddata(dmaster=True)           → 插入主数据
  │     └─→ for data in datas: data._start()
  │           └─→ feed._prepare(_loop)       → 启动行情连接/加载文件
  │
  ├─→ 2. Timer 准备
  │     └─→ Timer(when=SESSION_START).start(data0)
  │
  ├─→ 3. 策略实例化
  │     └─→ runstrategies(iterstrat)
  │           ├─→ strat = stratcls(*datas, *args, **kwargs)
  │           │     └─→ MetaStrategy.donew()
  │           │           ├─→ findowner → cerebro
  │           │           ├─→ env.pnc = cerebro.pnc
  │           │           ├─→ store.register() → 分配 experiment_id
  │           │           └─→ strat.store = store
  │           │
  │           ├─→ 添加默认 Observers（stdstats）
  │           │     ├─→ Broker, Trades, BuySell, DrawDown, TimeReturn, Benchmark
  │           ├─→ 添加用户 Observers
  │           ├─→ 添加用户 Indicators
  │           │
  │           ├─→ strat._start(savemem, **kwargs)
  │           │     ├─→ set_cash() → store.set_cash(experiment_id, session, cash)
  │           │     ├─→ qbuffer(savemem) → 级联设置内存节省模式
  │           │     ├─→ _periodrecalc() → 重新计算最小周期
  │           │     ├─→ analyzer._start()
  │           │     └─→ observer._start()
  │           │
  │           └─→ writer.start()
  │
  └─→ 4. 主循环 _runnext(runstrats)
        │
        ▼ (逐 bar 循环)
        while d0ret:
          │
          ├─→ for d in datas: dret = d.next()
          │     └─→ DataBase.next() → _load() → 读取新 bar 到 lines[0]
          │
          ├─→ if not drets[0]: 处理 resample 的 _last()
          │
          ├─→ _check_timers(runstrats, dt0)
          │     └─→ strat.notify_timer(dt0)
          │           └─→ store.on_dt_over(experiment_id) → 日终结算
          │
          └─→ for strat in runstrats: strat._next()
                │
                ├─→ clk_update()
                │     ├─→ 若 data 长度增加: forward()
                │     └─→ lines.datetime[0] = max(d.datetime[0] for d in datas)
                │
                ├─→ _getminperstatus() → 判断当前处于 pre/next/nextstart
                │
                ├─→ _next_observers(minperstatus)
                │     ├─→ for analyzer: _next() / _nextstart() / _prenext()
                │     └─→ for observer: _next()
                │
                ├─→ LineIterator._next() → 级联调用所有 indicator._next()
                │     └─→ for ind in _lineiterators[IndType]:
                │           ├─→ ind._next() → ind.next() / nextstart() / prenext()
                │           └─→ 若 ind 有子 ind: 递归调用
                │
                └─→ 根据 minperstatus 调用用户逻辑
                      ├─→ minperstatus < 0  → strat.next()
                      ├─→ minperstatus == 0 → strat.nextstart()
                      └─→ minperstatus > 0  → strat.prenext()
                            └─→ 用户策略逻辑
                                  ├─→ 计算信号 / 指标
                                  ├─→ pnc.generate_plan() → 生成买卖计划
                                  ├─→ strat.buy(buys)  → 构造 OrderBody
                                  │     └─→ store.submit(experiment_id, order)
                                  │           └─→ broker.submit() → tdapi.submit()
                                  │                 └─→ 返回 SnapshotBody (含 trades)
                                  │     ←─ notify_trade(order, trades)
                                  │     ←─ snapshot = snapshot (更新账户/持仓)
                                  │
                                  └─→ strat.sell(sells) → 同上
                                        └─→ pnc.on_filled(trades)

        (循环结束)
        │
        ├─→ stop_writers(runstrats)
        ├─→ for strat: strat._stop() → strat.stop()
        └─→ for data: data.stop()
```

### 4.3 Order / Trade 生命周期

```
用户策略 next()
  │
  ├─→ 生成 TraderPlan 列表 (buys / sells)
  │     └─→ Pnc.generate_plan(topk_info, current_prices, snapshot, stats)
  │           ├─→ 风控检查（drawdown > act_tolerance → 清仓）
  │           ├─→ 止盈止损检查（pnl <= p_tolerance → 卖出）
  │           ├─→ 锁仓检查（lock_days）
  │           └─→ Sizer.getsizing() → 计算目标权重
  │
  ├─→ strat.buy(buys, plimit=0, execType=0, filler=b"oco")
  │     ├─→ 遍历 buys（TraderPlan 列表）
  │     ├─→ OrderBody(
  │     │       sid, sizer_ratio=weight, pricelimit=plimit,
  │     │       order_type=0(BUY), exec_type, created_dt, filler)
  │     └─→ snapshot = store.submit(experiment_id, order)
  │           └─→ BTBroker.submit(experiment_id, order)
  │                 └─→ tdapi.submit(experiment_id, order)
  │                       └─→ [异步 Actor 处理 → 撮合/成交]
  │
  ├─→ snapshot 返回
  │     ├─→ trades = snapshot.order      (成交列表)
  │     ├─→ 若 trades 非空:
  │     │     ├─→ lines.buy[0] = 1       (标记买点)
  │     │     ├─→ notify_trade(order, trades)
  │     │     │     ├─→ _orders.append(order)
  │     │     │     └─→ _trades.extend(trades)
  │     │     └─→ self.snapshot = snapshot (更新账户/持仓快照)
  │     └─→ 若 trades 为空: 无成交
  │
  └─→ strat.sell(...) 逻辑对称
        └─→ pnc.on_filled(trades)         (更新 Pnc 内部状态)
```

### 4.4 Timer / 日终结算链路

```
_runnext 循环
  │
  └─→ _check_timers(runstrats, dt0)
        └─→ for strat: strat.notify_timer(dt0)
              ├─→ on_dt_over = True
              ├─→ snapshot = store.on_dt_over(experiment_id)
              │     └─→ LocalStore.on_dt_over()
              │           ├─→ body = _feed.on_dt_over()      → 获取日终行情快照
              │           └─→ broker.on_dt_over(experiment_id, body)
              │                 └─→ tdapi.on_dt_over()        → 日终结算/分红除权
              │
              ├─→ if snapshot: self.snapshot = snapshot
              └─→ self.reset()
                    ├─→ _orders.clear()
                    ├─→ _trades.clear()
                    └─→ on_dt_over = False
```

### 4.5 Indicator 计算链路

```
用户策略 __init__
  │
  └─→ self.sma = bt.ind.SMA(self.data, period=20)
        │
        └─→ MetaIndicator.__call__() → Indicator.__init__()
              ├─→ 作为参数传入的 self.data 被 MetaLineIterator.donew() 识别为 LineRoot
              │     → 加入 indicator.datas
              ├─→ 通过 findowner 找到 Strategy
              ├─→ dopreinit: _clock = datas[0], _minperiod = max(datas._minperiod)
              └─→ dopostinit:
                    ├─→ _minperiod = max(lines._minperiod)
                    ├─→ _periodrecalc() → 向上传播 minperiod
                    └─→ _owner.addindicator(self) → 注册到 strategy._lineiterators[IndType]

运行时 _runnext → strat._next()
  │
  └─→ LineIterator._next() 级联
        └─→ for ind in _lineiterators[IndType]: ind._next()
              │
              ├─→ ind._clk_update() → forward()
              ├─→ for child_ind: child_ind._next()  (递归)
              └─→ 根据 clock_len 与 _minperiod 关系:
                    ├─→ > : ind.next()
                    ├─→ ==: ind.nextstart()
                    └─→ < : ind.prenext()
                          └─→ 用户指标逻辑: self.lines[0] = ... (计算并写入当前值)
```

---

## 5. 关键数据流

### 5.1 Bar 数据流

```
[外部数据源] ──→ Feed._load() ──→ DataBase.lines[0] ──→ Strategy.datas[0]
                                                  │
                                                  ├─→ Indicator.datas[0]
                                                  ├─→ Observer.datas[0]
                                                  └─→ Analyzer.datas[0]
```

### 5.2 订单/成交数据流

```
Strategy.buy/sell()
  │
  ├─→ OrderBody ──→ Store.submit() ──→ Broker.submit() ──→ TdApi.submit()
  │                                                              │
  │                                                              ▼
  │                                                    [撮合引擎 / 交易所]
  │                                                              │
  └─← SnapshotBody ←── Store.submit() 返回 ←── 成交回报 ←───────┘
        │
        ├─→ snapshot.account   (资金)
        ├─→ snapshot.positions (持仓)
        └─→ snapshot.order     (成交明细)
```

### 5.3 Lines 绑定机制

当 Indicator 的计算结果被赋值给 Strategy 的某条 line 时：

```python
# 例如: self.lines.signal = self.data0.lines[0]
# 或:  self.sma.lines[0] → 自动绑定
```

内部通过 `LineAlias.__set__` 调用 `LineActions.addbinding()`，使得源 LineBuffer 的值变化时自动同步到目标 LineBuffer。

---

## 6. 模块目录结构

```
bt_core/
├── __init__.py              # 统一导出所有公共 API
├── _version.py              # 版本信息
│
├── cerebro.py               # 回测引擎主控 (Cerebro)
├── strategy.py              # 策略基类 (Strategy, SignalStrategy)
│
├── feed.py                  # 数据抽象基类 (AbstractDataBase)
├── dataseries.py            # DataSeries, OHLC, TimeFrame, _Bar
├── resamplerfilter.py       # 数据重采样/回放 (Resampler)
├── filters/                 # 数据过滤器 (HeikinAshi, Session, Renko...)
├── feeds/                   # 具体数据实现 (ParquetData, RPCFeed, ...)
│
├── linebuffer.py            # LineBuffer 核心缓冲区
├── lineroot.py              # LineRoot / LineSingle / LineMultiple
├── lineseries.py            # LineSeries / Lines / LineAlias
├── lineiterator.py          # LineIterator / IndicatorBase / ObserverBase / StrategyBase
│
├── indicator.py             # Indicator 元类与基类
├── indicators/              # 技术指标库 (SMA, EMA, MACD, ATR, ...)
│
├── observer.py              # Observer 元类与基类
├── observers/               # 观察器库 (Broker, BuySell, DrawDown, ...)
│
├── analyzer.py              # Analyzer 元类与基类
├── analyzers/               # 分析器库 (Sharpe, Returns, TradeAnalyzer, ...)
│
├── broker.py                # BrokerBase 抽象基类
├── brokers/                 # 经纪商实现 (BTBroker, IBBroker, ...)
│
├── store.py                 # Store 元类与基类 (Singleton)
├── stores/                  # Store 实现 (LocalStore)
│
├── sizer.py                 # Sizer 基类
├── sizers/                  # Sizer 实现 (Fixed, Weighted, Turtle, Kelly)
│
├── signal.py                # Signal 类型定义与 Signal 基类
├── flt.py                   # 过滤器辅助
├── logic.py                 # Lines 运算逻辑 (DivByZero 等)
│
├── plot/                    # 绘图模块 (Matplotlib / Bokeh)
│   ├── mpl/                 # Matplotlib 绘图
│   └── bkh/                 # Bokeh 绘图
│
├── contrib/                 # 扩展功能
│   ├── btrun/               # 命令行运行工具
│   ├── sheet/               # 报表/ tearsheet
│   ├── strategy/            # 策略辅助 (ASTC, FSM)
│   ├── ui/                  # UI 组件
│   ├── server.py            # 服务化入口
│   ├── panelfeed.py         # Panel 数据馈送
│   └── raystore/rayfeed/    # Ray 分布式支持
│
├── execution/               # 执行层 (与 bt_sdk 交互)
│   ├── actor/               # Actor 模型 (RunnerActor, WriterActor)
│   ├── gateway/             # 网关抽象
│   ├── core/                # 核心金融/引擎抽象
│   └── utils/               # 执行工具
│
├── pnc.pyx                  # Cython 风控仓位控制器 (Pnc, TraderPlan)
├── pnc.pxd                  # Cython 头文件
│
├── metabase.py              # 元类基础设施
├── tradingcal.py            # 交易日历
├── timer.py                 # 定时器 (Timer, Session)
├── writer.py                # 结果输出 (WriterFile)
├── optimizer.py             # 参数优化器
├── log.py                   # 日志
├── errors.py                # 异常定义
└── utils/                   # 通用工具
    ├── dateintern.py        # 日期转换
    ├── autodict.py          # AutoOrderedDict / DotDict
    ├── wrapper.py           # 装饰器
    └── encoder.py           # JSON 编码器
```

---

## 7. 设计模式与架构特点

1. **元类驱动参数系统 (`MetaParams`)**
   - 所有可配置类通过 `params = (('name', default), ...)` 声明参数。
   - 元类在 `__call__` 中自动将 kwargs 绑定到 `self.p`。

2. **Lines 时间序列抽象**
   - `LineBuffer` 用数组/循环缓冲区实现，索引 `0` 永远指向当前值。
   - `forward()` / `advance()` / `qbuffer()` 控制内存与指针移动。

3. **层级化 `_next()` 调度**
   - `Cerebro._runnext()` → `Strategy._next()` → `LineIterator._next()` → `Indicator._next()`。
   - 通过 `_clock` 和 `_minperiod` 实现跨周期同步。

4. **Store-Broker 解耦**
   - `Store` 是单例，统一封装行情 (`MdApi`) 和交易 (`TdApi`)。
   - `Broker` 只负责协议转换，`Store` 负责资源生命周期。

5. **Cython 性能优化**
   - `pnc.pyx` 将高频风控计算用 Cython 实现，减少 Python 开销。

6. **Actor 异步执行**
   - `BatchWriterActor`、`AsyncRunner` 支持异步订单写入和事件循环。

7. **Observer / Analyzer 生命周期绑定**
   - 两者都不由用户直接调用 `next`，而是由 `Strategy._next_observers()` 统一调度。

---

## 8. 扩展点

| 扩展目标 | 继承/实现 |
|---------|----------|
| 自定义策略 | `class MyStrategy(bt.Strategy)`，重写 `next()` |
| 自定义指标 | `class MyInd(bt.Indicator)`，重写 `next()` |
| 自定义 Observer | `class MyObs(bt.Observer)`，重写 `next()` |
| 自定义 Analyzer | `class MyAna(bt.Analyzer)`，重写 `next()` / `get_analysis()` |
| 自定义 Sizer | `class MySizer(bt.Sizer)`，重写 `_getsizing()` |
| 自定义数据源 | `class MyFeed(bt.feed.DataBase)`，重写 `_load()` |
| 自定义 Broker | 继承 `bt.BrokerBase`，接入新交易 API |
| 自定义 Store | 继承 `bt.Store`，重写数据与交易连接 |

---
