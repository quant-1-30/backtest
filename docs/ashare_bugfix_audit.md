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