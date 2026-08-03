# A股交易逻辑 Bug 审计与修复记录

> 审计范围：`cerebro / strategy / indicator / linebuffer / pnc / execution`
> 审计日期：2026-08-03
> 严重度：P0（资金计算错误）→ P1（交易逻辑错误）→ P2（代码质量）

---

## P0 严重 Bug

### 1. 摘牌退市 realized_pnl 丢失

**文件**：`bt_core/execution/core/finance/position.pyx` → `_dt_over()`

**问题描述**：
股票退市摘牌（delist）时，持仓 `size` 被清零，但投资成本未计入 `realized_pnl`。导致回测净值虚高，完全掩盖了退市损失这一重要风险。

**根因**：
原代码仅做了 `size = 0`，没有把对应的成本从持仓中结转为已实现亏损。

**修复**：
```cython
# 非吸合并摘牌 → 投资成本直接归零，需计入已实现亏损
if asset_core.delist > 0 and asset_core.delist <= end_dt:
    if not asset_core.merger.empty():
        self._handle_merger(...)
    else:
        self.core.realized_pnl -= self.core.size * self.core.cost_basis  # 新增
        self.core.size = 0
        self.core.available = 0
        self.core.pnl = 0
```

---

### 2. drawdown 清仓卖单未登记 pending_sells

**文件**：`bt_core/pnc.pyx` → `on_risk()` Phase 3

**问题描述**：
触发最大回撤（maxdd）全仓卖出时，卖单加入了 `sells_by_risk`，但**未写入** `self.pending_sells`，且缺少 pending 去重检查。后续 `generate_plan` 会重复生成同一股票的卖单，导致：
1. 重复挂单（broker 侧产生废单/超卖）
2. slot 计数错误（`pending_sells_count` 偏小，多放买单）

**修复**：
```cython
if stats["drawdown"].maxdd >= self.dd:
    for pos in positions:
        c_sid = <cpp_string>pos.sid
        # 新增 pending 去重
        if pos.available > 0 and self.pending_sells.find(c_sid) == self.pending_sells.end():
            tmp.sid = c_sid
            ...
            sells_by_risk.push_back(tmp)
            self.pending_sells[c_sid] = tmp  # 新增登记
```

---

### 3. created_dt 时间格式不匹配导致持仓天数恒为 0

**文件**：`bt_core/pnc.pyx` → `_get_days_held()` 及 3 个调用点

**问题描述**：
- `pos.created_dt` 存储的是 **epoch seconds**（int64，如 `1700000000`）
- `v_trading_days` 与 `current_day` 是 **YYYYMMDD** 格式（int32，如 `20231114`）
- `_get_days_held` 用 `lower_bound` 在 `v_trading_days` 中查找 `created_day`

两者格式完全不同，`lower_bound` 查找结果错乱，**持仓天数计算永远是 0 或无意义值**，导致基于持仓周期（`interval`，默认 5 天）的调仓逻辑永远无法触发——长持仓股票不会被轮动卖出。

**修复**：
```cython
# 导入转换函数
from bt_core.utils.dateintern import ts2intdt

# 3 个调用点统一转换（on_risk / generate_plan Phase 2 / Phase 4）
days_held = self._get_days_held(<int32_t>ts2intdt(pos.created_dt), current_day)
```

---

## P1 中等 Bug

### 4. already_held 判断不完整导致重复买入

**文件**：`bt_core/pnc.pyx` → `generate_plan()` Phase 8

**问题描述**：
买入去重判断使用了 `pos.available > 0`。但 A 股实行 **T+1** 规则：
- 当日新买入的持仓，`available = 0`（次日 `on_dt_over` 才解锁为可卖）
- 若同一天内策略被多次调用，或 topk 中包含当日已买入的股票
- `available > 0` 判断为 False → 认为"未持仓" → **重复买入**

**修复**：
改用 `pos.size > 0`（持仓量而非可卖量）作为已持仓判断标准：
```cython
for pos in positions:
    if <cpp_string>pos.sid == c_sid and pos.size > 0:  # 改 available → size
        already_held = True
        break
```

---

## P2 代码质量

### 5. 配股事件 Cython 声明位置错误

**文件**：`bt_core/execution/core/finance/position.pyx` → `_process_event()`

**问题描述**：
配股（rights issue，`event_type != 0`）分支中，在执行语句之后使用 `cdef` 声明变量，Cython 编译器不允许（`cdef` 必须在 block 开头声明）。

**逻辑确认**：
配股新增股份当前**不立即加入** `available`，符合 A 股 T+1 规则（新增股次日 `on_dt_over` 才解锁为可卖），逻辑正确。

**修复**：
移除非法 `cdef` 声明，改为直接赋值：
```cython
# 修复前（编译错误）：
cdef int32_t new_size = <int32_t>floor(...)
cdef int32_t size_delta = new_size - origin_size  # 未使用
self.core.size = new_size

# 修复后：
self.core.size = <int32_t>floor(origin_size * (1.0 + sizer_ratio) + 0.5)
```

---

## 第二轮审计（execution/core/finance + analyzers）

第二轮对 `execution/core/finance`（account/simulate/filler）和 `analyzers/` 的复核结论：

### 撤回的误报

**误报 1：portfolio_value 不含 cash**
- 经核对，`account.portfolio_value` 仅表示持仓市值是**设计如此**
- 所有 analyzer 均正确使用 `portfolio_value + cash` 组合为总资产：
  - `DrawDown/TimeReturn/Calmar/Sharpe/PeriodStats/SQN/Positions` → `acct.portfolio_value + acct.cash` ✅
  - `PyFolio` 分别 publish `Portfolio` 与 `Cash` 两个独立 metric（导出器性质）✅
- **不是 bug，撤回**

**误报 2：filler 限价单方向反了**
- `_find_limit_execution` 中 `open > limit_price` 时返回 `limit_price` 是**正确**的
- A 股买入限价单语义：`limit_price` 是愿意支付的最高价
  - 当 `low <= limit_price`（盘中曾达限价以下）且 `open > limit_price`（开盘超限价）
  - → 盘中价格回落到限价时按 `limit_price` 成交
- 注释"当日不会成交"措辞误导，但**代码逻辑正确**

### 第二轮鲁棒性建议（非 bug，不强制修复）

1. **`simulate.pyx` `_sync_event` 多日事件覆盖**：当前 query 为单日（start=end），rpc 返回单行不会触发；但 `cpp_adj_map[int_sid] = ...` 直接赋值在多行场景会覆盖。建议改为 `vector` 收集（**已在本次修复，见下方 P1-6**）。

2. **`simulate.pyx` `_create_snapshot` 缓存**：当 `trades is not None` 时强制重建并写回缓存，下次无 trades 调用会用缓存。经分析缓存内容是最新状态，**逻辑正确**，无需修复。

---

### P1-6：`simulate.pyx` `_sync_event` 同 sid 多事件覆盖（已修复）

**文件**：`bt_core/execution/core/finance/simulate.pyx` → `_sync_event()`

**问题描述**：
`cpp_adj_map[int_sid] = AdjustmentData(...)` 和 `cpp_rgt_map[int_sid] = RightData(...)` 对同一 sid 直接赋值。若同一只股票有多条除权/配股事件（rpc 返回多行），只保留最后一条，前面的被静默覆盖。

`position.pyx` 的 `process_events` 已支持 `vector[EventItem]` 多事件顺序累积（`size *= sizer_ratio`、`cost_basis /= sizer_ratio` 逐条应用），业务语义支持多次事件累积，只是 `_sync_event` 收集时丢失了。

**修复方案**：
将 map 的 value 类型从单 struct 改为 `vector`，收集所有行，应用时遍历全部。共 4 处改动：

**改动 1：变量声明（`_sync_event` 开头）**
```cython
# 修复前：单 struct 直接覆盖
cdef unordered_map[int32_t, AdjustmentData] cpp_adj_map
cdef unordered_map[int32_t, RightData] cpp_rgt_map

# 修复后：vector 容器
cdef unordered_map[int32_t, vector[AdjustmentData]] cpp_adj_map
cdef unordered_map[int32_t, vector[RightData]] cpp_rgt_map
```

**改动 2：除权事件收集（`for sid_bytes, py_adj_df` 循环）**
```cython
# 修复前：直接赋值，多行覆盖只留最后一条
cpp_adj_map[int_sid] = AdjustmentData(
    bonus_share=float(bonus_share),
    transfer=float(transfer),
    bonus=float(bonus))

# 修复后：push_back 保留所有行
cpp_adj_map[int_sid].push_back(AdjustmentData(
    bonus_share=float(bonus_share),
    transfer=float(transfer),
    bonus=float(bonus)))
```

**改动 3：配股事件收集（`for sid_bytes, py_rgt_df` 循环）**
```cython
# 修复前：直接赋值
cpp_rgt_map[int_sid] = RightData(ratio=float(ratio), price=float(price))

# 修复后：push_back
cpp_rgt_map[int_sid].push_back(RightData(ratio=float(ratio), price=float(price)))
```

**改动 4：事件应用（`for (_, sid_bytes), pos_obj` 循环）**
```cython
# 修复前：只取单个 struct，最多触发一次 process_event
adj_it = cpp_adj_map.find(int_sid)
if adj_it != cpp_adj_map.end():
    temp.event_type = 0
    temp.adj = deref(adj_it).second   # 单值
    v_events.push_back(temp)

rgt_it = cpp_rgt_map.find(int_sid)
if rgt_it != cpp_rgt_map.end():
    temp.event_type = 1
    temp.rgt = deref(rgt_it).second   # 单值
    v_events.push_back(temp)

# 修复后：遍历 vector 中所有事件，按顺序 push 到 v_events
adj_it = cpp_adj_map.find(int_sid)
if adj_it != cpp_adj_map.end():
    for adj_data in deref(adj_it).second:   # 遍历全部
        temp.event_type = 0
        temp.adj = adj_data
        v_events.push_back(temp)

rgt_it = cpp_rgt_map.find(int_sid)
if rgt_it != cpp_rgt_map.end():
    for rgt_data in deref(rgt_it).second:   # 遍历全部
        temp.event_type = 1
        temp.rgt = rgt_data
        v_events.push_back(temp)
```

**修复后效果**：
`position.pyx` 的 `process_events(v_events)` 会按 vector 顺序逐条应用：
- adj：`size *= sizer_ratio`，`cost_basis /= sizer_ratio`
- rgt：`size *= (1 + sizer_ratio)`

多次除权配股事件可正确累积，与 A 股真实业务语义一致。

---

## 审计中确认正常的模块

### linebuffer / lineseries / indicator
- 环形缓冲区（QBuffer）的 `idx % maxlen` 索引计算正确
- `minperiod` 聚合从 data → indicator → strategy 逐层传播正确
- LineBinding 机制（一个 line 变化自动同步到绑定的 line）正确
- 无 A 股特定逻辑问题

### cerebro `_dt_over`
- 用 `dt0 - last_dts >= 12*3600`（12 小时）检测跨日，能正确区分 A 股上午/下午时段
- 配合 `_check_timers` 在 `next` 前调用，时序正确

### timer
- `SESSION_START / SESSION_END` + `offset/repeat` 调度逻辑正确
- 时区统一为 `Asia/Shanghai`，`_getnexteos` 基于 sessionend 计算

### position T+1 约束
- `update()` 中检测 `available < 0` 抛异常，防止超卖
- 明确禁止做空（`orig_size < 0` 抛 ValueError）
- T+1 解锁在 `on_dt_over` 中执行 `available = size`

### feed 复权
- `apply_factor` 对 OHLC 用 `factor`、对 volume 用 `1/factor`（前复权反向）
- 符合 A 股除权除息规则

---

## 修改文件清单

| 文件 | 修改点 |
|------|--------|
| `bt_core/execution/core/finance/position.pyx` | P0-1 摘牌 realized_pnl、P2-5 配股 cdef |
| `bt_core/pnc.pyx` | P0-2 drawdown pending、P0-3 created_dt 转换、P1-4 already_held |
| `bt_core/execution/core/finance/simulate.pyx` | P1-6 _sync_event 多事件 vector 收集 |
