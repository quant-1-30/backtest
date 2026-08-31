# agents.md — bt_core 工程 Agent 工作指南

面向在本仓库工作的 AI/自动化 agent。架构总览见 `README.md` 与 `docs/architecture/ARCHITECTURE.md`，本文只讲**怎么安全地改这个仓库**：构建命令、关键不变量、历史踩坑、验证方法。

---

## 1. 项目一句话

A 股量化回测/仿真框架：backtrader 的 Lines/元类体系（纯 Python） + Cython 撮合账务核心（`bt_core/execution/core/finance/`） + Actor 异步执行层 + 共享内存零拷贝 IO（`bt_core/shm/`）。

## 2. 环境与构建（改任何 .pyx 后必读）

- Python venv（poetry）：`/Users/hengxinliu/Library/Caches/pypoetry/virtualenvs/bt-core-GmpHtvLH-py3.11`（下称 `$PY`）
- Cython 扩展清单由 `setup.py:get_ext_modules()` 提供（finance 全部 + timer/pnc/shm_buffer/writer_actor/interface/engine/trade_api/sizer/sink）。
- **改 .pyx/.pxd 后必须重编**，否则运行的还是旧 .so：

```bash
cd /Users/hengxinliu/startup/bt_core
$PY setup.py build_ext --inplace
```

- Cython 语法坑（都在本仓库踩过）：
  1. `cdef` 声明必须**文本位置在使用之前**（如 `cdef int32_t avail` 放到使用它的 clamp 之前），否则 C 编译错。
  2. `.pxd` 中的方法签名必须与 `.pyx` 一致，改签名两边同步改（报 `Signature not compatible`）。
  3. **cdef 方法对未类型的 Python 变量不可见**（`AttributeError`）。要测试 cdef 方法，写一个 Cython 测试模块，在函数体内 `cdef Position p` 等类型声明后调用；基类的 `def __call__`（如 `CommInfoBase.__call__`）可从 Python 直接调。
  4. 编译生成的 `.cpp` 与 `.so` 均在源码目录 in-place，不要手工编辑。

## 3. 时间表示约定（全仓库最大的坑）

两种表示混用，必须分清：

| 表示 | 含义 | 出现位置 |
|---|---|---|
| unix 秒（int64/double） | 真实时间戳（日内工作表示，保序） | Order.core.created_dt、OrderExecutionBit、Asset 上市日、EventItem、Position/Account.core.datetime（日内） |
| ymd int（如 20240105） | 交易日（结算/持久化表示） | on_dt_over 参数、Position.core.datetime（`_dt_over` 后）、Account.core.datetime（`sync` 后）、benchmark 日收益表 |

- unix → ymd（北京时间）用 `bt_core.utils.dateintern.ts2intdt`（内部 +28800 后 gmtime）。
- **ts 是日内工作表示，ymd 只在结算边界归一化一次**：`Position._dt_over` 写入 ymd（持久化前）；`Account.sync` 是唯一转换点——tick 从 simulate 传来是 ts、positions 已被 on_dt_over 归一为 ymd，ts 数值（~1.7e9）恒大于 ymd（~2e7）故 max 后单次 ts2intdt 即正确；并用 `>= 1e8` 量级守卫防止对已是 ymd 的值二次转换（ts2intdt(20240105) → 19700823）。
- **禁止**在日内路径（update/_execute）预先 ts2intdt——同日多次成交需保持严格时序。
- 跨日回调 `on_dt_over(prev_dts, cur_dts)` 两个参数都是 unix ts（cerebro 在推进 `last_dts` 前捕获 prev）；simulate 内部再 ts2intdt。`strategy.on_dt_over` 中 publish_dts 为 0 时回退 last_dts（stop() 结算最后一次）。

## 4. A 股规则实现位置（改动手册）

| 规则 | 位置 | 要点 |
|---|---|---|
| T+1 | `position.pyx`（available vs size）、`filler.pyx` 卖出 clamp | 买入当天 available=0；`on_dt_over` 解锁 |
| 手数 | `filler.pyx _round_to_lot` | 买入按 100 股整手；**零股只能在卖出最后一笔一次性卖出**（`last_fill` 参数控制） |
| 印花税/过户费/佣金 | `comminfo.pyx` | 费率带时间分界点（常量 `*_CKPT`）；5 元最低**仅佣金**；过户费 2015-08-01 前沪市按面值 0.06‰、深市免收 |
| 分红送转/配股 | `position.pyx _process_event` | 送转/配股按 `floor` 截断（零头不足 1 股直接舍去，"不足就是不足"）；配股现金 = -rights_size*price；成本加权 |
| 新股涨跌幅豁免 | `asset.pyx restricted()` | 用**真实日历日**差（`_days_from_civil`），不能用 ymd 直接相减（跨月/跨年会错） |
| 除权除息因子 | `feed.py apply_factor` | **当日 bar 由 feed 层 save/restore line[0] 保留**（除权日行情本身已是除权后价格，勿再乘）；历史 bar 价格 `*factor`、量 `*1/factor`（amount 不动，恰保持 p×v≈amount 自洽）；minute 主 feed 与 resample 的 DataClone 各自对**自己的 buffer** 调整（clone 经 `_start` 共享 `adj_factors`）。因子按 `(record_dt, current_dt]` **到期区间**应用而非精确匹配 bar 日——停牌窗口内的 ex-date 在复牌首根 bar 补乘（300308 2016 停牌、20160801 十派0.1 实测）。`LineBuffer.apply_factor` 调全 buffer，环形模式下"排除当根"不可能用静态切片表达（`array[:-1]` 只在 `_cur_idx==maxlen-1` 时恰好排除当根，其余相位丢最老 bar 且漏调当根）——勿再试 |

## 5. 关键不变量（违反即引入回归）

1. **`dataseries._Bar` 字段插入顺序必须与 lines 定义顺序一致**（open,high,low,close,volume,amount,datetime）——resample 通过 `lvalues()/_fromstack` zip 装配，错位会导致开收/高低互换。
2. **Timer 语义**：`repeat<=0` 的定时器每天只触发一次（`_lastcall` 守卫）；负 offset 有效（如 -10 表示收盘前 10 分钟）；`allow` 过滤器从 kwargs 透传。
3. **Cerebro 事件顺序**：日内新 bar 先判日切换（Day Rollover → `strat.on_dt_over(prev, cur)`）再跑 Scheduled Timers——保证 T+1 解锁先于当日 RISK/开仓定时器，且 `on_dt_over` 拿到的是**上一交易日**的 ymd。
4. **shm 消费者注册**：`analyzer.py dopostinit` 只给 `consumes_shm = True` 的对象注册共享内存消费者，其余 `shm_id = -1`——否则不排水消费者冻结 min_tail，卡死生产者。新增需要读 shm 的 analyzer 必须声明该类属性。
5. **analyzers 不得重名/不得 raise**：多个 analyzer 发布同名 metric 会互相覆盖（calmar/drawdown 事件已改名 `CalmarMaxDrawdown`）；order_id 未命中应 warning 跳过（历史被 merge/prune 过）而非抛异常终止整个回测。
6. **`Order.execute` 的完成判定**：按 `_exbits` 累计成交量判断 Completed/Partial，不看单笔。
7. **`Order.__reduce__` 参数顺序必须与 `__init__` 一致**（9 个参数），writer/sink 依赖 pickle。
8. **writer_actor.stop()** 先入队 sentinel 再置 `_running=False`（先置标志会与排水循环竞态丢尾数据）。
9. `linebuffer.get(idx, ago, size)` 两条分支统一窗口公式 `[idx+ago-size+1, idx+ago]`。
10. Python 3.11 兼容：`collections.abc.Iterable`（非 `collections.Iterable`）；无 `__div__`；functools.wraps。
11. **事件时序模型（经 DB 反向验证，易误读）**：主时钟是 store 的分钟 feed（`Cerebro Prepare Feed and Set Dmaster`），resample 出的日/周线是其 clone。Day Rollover 在 **D+1 首个 bar**（≥12h 间隔）触发、结算 D（prev_dts=D 最后一根 bar）→ D 的买入在 D+1 开盘解锁，T+1 恰好成立。`order_bit.executed_dt` 是**成交 bar 的戳**而非提交时刻：次晨提交的市价卖单对齐上一根已完成日 bar（戳 15:00 D、价=D 收盘）——"次开≈前收"近似。account/vtposition 的 D 行在 D+1 开盘 rollover 时写入，**不含 D+1 晨提交（戳 D 15:00）的卖单**：该行权益与真实值仅差未扣的卖佣（次行自愈），非记账错误。**T+1 解锁边界不变量**：`on_dt_over` 无条件 `available = size` 之所以精确，是因为成交与 bar 同步、end_dt=rollover 前最后一根 bar 的日子 ≥ 一切已成交买入日（勿引入按买入日记账的锁字段——曾实现过并回退，属不可达防御且维护成本高）。若未来引入异步成交/事件驱动时钟，此不变量失效，解锁逻辑必须改为按买入日判定。
12. **多标的常态化次晨盖章（2026-08 多标的闭环发现）**：`strategy.lines.datetime` 在 timer 派发时（`_check_timers` 先于 `strat._next()`）**停在前一根 bar**——隔夜即停在昨夜末根 bar。故 D+1 09:30 的 `on_risk` 卖单 created_dt 盖 D 末根 bar 的戳，filler 按戳回填到 D 的 bar 上成交。单标的低频下是罕见边角（12/2590 行次日自愈）；**多标的每日轮动下成为常态**（3 标的 14.5 年：2509 单/3483 笔全部正确，但 1061/1338 行的"盖章日视角"与真实时序错位）。DB 反向验证的重放规则：卖单 size 超过盖章日当时可卖量（T+1 使其当日不可能成交）⇒ 必为次晨卖单，推迟到 D+1 开盘前应用（见 tests/verify_event_cash.py）。重放后现金守恒 0.0000、轨迹 1334/1338 精确 + 4 行次日自愈。**回测口径注意**：这些次晨卖单的成交价取的是 D 末根 bar 价（"次开≈前收"），对高换手策略是系统性近似。

## 6. 验证方法

- 端到端冒烟：`tests/run_simulation.py`（需要 `~/startup/bt_studio/result/fsm/scores` 下含 `day/sid/fsm_score` 列的 parquet；缺文件时静默空信号、无成交）。
- **多标的端到端**（2026-08 起入库）：`tests/test_multi_instrument.py`（3 标的 300308/600000/000001、日/周/月 resample、MultiSignalPatch 按 ymd 轮动信号；FROMDATE/TODATE/RUNTAG 环境变量控制窗口与唯一性）→ `tests/verify_event_cash.py` 反向验证（默认取 client `5a1f0c9e-...` 最新实验）。前置：md-server（`cd ~/startup/rpc_feed && PYTHONPATH=. poetry venv 的 python rpc_feed/run_server.py`，端口 50051）、PG bt_trade 库、client 已注册 user_info。同配置重跑需换 RUNTAG（`uq_client_strategy_extra_info` 唯一约束，引擎不 upsert）。
- **DB 反向验证**（跑完 tests/test_strategy.py 后，`PGPASSWORD=... psql -U postgres -h localhost -d bt_trade`）：
  1. `account` 全部 datetime ∈ [19900101,21001231] 且每日唯一（`uq_acct_datetime_experiment_id` 不炸）；
  2. 逐 bit 费率恒等式：`comm == max(amt*(3e-3|5e-4 @2015-06-09), 5) + 卖出印花(1e-3|5e-4 @2023-08-28) + 过户费(1e-5|2e-5|6e-5仅沪市@分界 2022-04-29/2015-08-01)`，容差 0.005；
  3. 现金守恒：`100000 + Σ(±px*sz) - Σcomm == account 末日 cash`，残差 = 持仓期现金分红总和（>0）。分红可**逐笔闭环**：用 `account` 日现金跳变（非交易日流日）独立提取每笔实付分红，应逐一等于除权日持仓 `size × bonus/10`（bonus 来自 mdapi `RpcTopic.Adjustment`，与 simulate 同源）；送转后 size = `floor(size × fl(sizer_ratio))`（浮点下 5700×fl(1.4)=7979 而非 7980，DB 实测吻合）。
  4. 买入 size % 100 == 0；`vtposition` 无 available > size。
  5. 长期停牌场景（已修复，勿回退成精确匹配）：300308 在 20160311–20160929 重组停牌，**停牌不是数据缺失**——Adjustment 表明确记录洞内 `ex_date=20160801`（十派0.1、`register_date=20160729` 停牌中名册冻结）。引擎曾按"ex_date == 下一根 bar 日"精确拉事件/匹配因子，窗口内事件被静默丢弃：现金分红不入账、若为送转/配股则仓位轨迹永久错误。现为区间语义：`simulate._fetch_from_rpc` 按 `(prev, curr]` 拉事件（复牌 rollover 补派，`_sync_event` 按 ex_date 排序）、`feed.apply_factor` 按 `(record_dt, current_dt]` 到期区间补乘因子（复牌首根 bar）。验证：结算日行 20160310 现金跳 +72.15，残差与逐笔重放完全一致。停牌期无 rollover/account/vtposition 行属正常（时钟随 bar 走）；换标的后遇长期无 bar 先对照 Adjustment 表区分"停牌"（有洞内 ex_date 行）与"真数据缺失"（什么表都没有）。
- finance/timer 行为测试：Cython 测试模块方案（见 §2.3），历史上放在 `/tmp/bt_test/_financetest.pyx`，覆盖 T+1、分红/配股账务、多笔成交状态机、pickle 往返、费率分界点、新股跨月豁免。
- 改 .py 后：`$PY -m py_compile <files>`；再逐模块 import 一遍（很多 bug 只有 import 时暴露，如缺失导入名）。
- 提交前 `git diff` 通读一遍；生成物（.cpp/.so/build/）不要提交。

## 7. 已知未修复项（改动相关模块时优先评估）

- 涨跌停**撮合侧**无硬约束：`asset.restricted()` 只给费率/幅度参考，filler 只用振幅启发（`_execute_factor`），一字板无法成交的场景未完全建模。
- 交易日历无真实节假日表（`tradingcal.py` 只有周末 + 手工半日/熔断配置）。
- 分红持有期个税（差别化征税）未实现。
- ~~日内 VWAP filler 用全天量归一（未来函数）~~ **已禁用（2026-08-25）**：`AlgoFiller._execute` 整体注释、委托 `PseudoFiller` 逐 bar 因果撮合；b"vwap"/b"twap" 注册保留以兼容历史订单参数，语义等同 default。
- 科创板最小价差 200 股/ tick 近似。
- `publish_metric` 多生产者递增非原子（当前单生产者架构下无害）。
- merger（吸收合并）在 positions/sqn analyzer 中按全部损失处理。
- 多 data feed 的 bar 对齐（cerebro）未严格按时间戳归并。

## 8. 提交约定

- 分支：日常开发 `dev`，PR 目标 `main`。
- Bug 修复附简短说明；系统性修复写 `docs/bugfixes/<topic>.md`（已有先例：consecutive_run_deadlock、fix_analyzers 等）。
- 不要在代码里留调试 print（历史清理过一轮：cerebro/_dispatch、strategy.stop、btbroker 等）。
