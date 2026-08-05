# bt_core 性能卡点梳理与优化方案

> 范围：除 `bt_core/execution/core/finance/filler.pyx` 之外的核心模块
> 目标：识别严重性能卡点并给出优化方案，**不修改代码**
> 基准：基于实际源码静态阅读验证，行号引用当前仓库代码

---

## 1. 关键性能卡点总览

| 优先级 | 卡点 | 主要文件 | 复杂度 |
|--------|------|----------|--------|
| **严重** | Indicator 每次 `next()` 全量重算 | `bt_core/indicators/basicops.py`, `bt_core/indicators/sma.py`, `bt_core/indicators/ema.py`, `bt_core/indicators/rsi.py` | O(N²) |
| **严重** | Feed / Resample 频繁构造 `datetime` 对象 | `bt_core/feed.py`, `bt_core/resamplerfilter.py` | 高频分配 |
| **高** | Analyzer 重复拉取 snapshot / SHM events | `bt_core/strategy.py`, `bt_core/analyzers/*.py` | 重复 IPC/SHM |
| **高** | `PeriodStats` 每次都对全量收益做 `mean/std` | `bt_core/analyzers/periodstats.py` | O(N²) |
| **高** | Execution `Lines` 属性每次访问分配新 NumPy 数组 | `bt_core/execution/core/finance/line.pyx` | 高频分配 |
| **中** | Strategy 每 bar housekeeping 使用 `np.array` + `np.max` | `bt_core/strategy.py` | 不必要分配 |
| **中** | `dateintern.num2date` 在热路径创建 Python datetime | `bt_core/utils/dateintern.pyx` | 对象构造 |
| **中** | CSV / Pandas feed 逐行 `getattr` | `bt_core/feeds/csvgeneric.py`, `bt_core/feeds/pandafeed.py` | 反射开销 |
| **中** | SHM metric 逐个发布、字符串解码 | `bt_core/shm/shm_buffer.pyx`, `bt_core/sink/manager.py` | GIL + decode |

### 代码中已有的优化（注意保留）

在规划优化前，以下优化已在代码中存在，修改时需小心保留：

1. **`LineBuffer._cur_idx` 缓存**（`bt_core/linebuffer.py:100-139`）
   `__getitem__` 不再做 `self.idx % self.maxlen`，而是复用缓存的 `self._cur_idx`；只有 `__setitem__` / `forward` / `backwards` 会重算 `_cur_idx`。原分析认为每次都取模，实际已被优化。

2. **`UnBounded` 模式批量 `extend`**（`bt_core/linebuffer.py:208-210`）
   `forward` 在 `UnBounded` 模式下使用 `self.array.extend([value] * size)`，而非逐个 `append`。

3. **Resample 数值时间戳边界判断**（`bt_core/resamplerfilter.py:191-205`）
   `_barover_days` / `_barover_weeks` 已改为整数算术（`(int(dts) + 28800) // 86400`），不再调用 `num2date().isocalendar()`；只有 `_barover_months` / `_barover_years` 仍在用 `datetime`。

4. **`Lines` 缓存 bound method**（`bt_core/lineseries.py:241-242`）
   `Lines.__init__` 预缓存了 `line.forward` / `line.backwards` 到 `_forward_bounds` / `_backward_bounds`。

5. **`Analyzer.on_dt_over` 接收 snapshot**（`bt_core/strategy.py:243-249`）
   `Strategy.on_dt_over` 已经把 snapshot 作为参数传给 analyzer，`PeriodStats` 已使用传入 snapshot（不再独立 `get_snapshot()`）。部分 analyzer 尚未迁移。

---

## 2. 严重卡点详细说明与优化方案

### 2.1 Indicator 全量重算（O(N²)）

**已验证代码位置**

- `bt_core/indicators/basicops.py:98-103` (`Highest`)
- `bt_core/indicators/basicops.py:123-128` (`Lowest`)
- `bt_core/indicators/basicops.py:150-155` (`FindIndexHighest`)
- `bt_core/indicators/basicops.py:177-182` (`FindIndexLowest`)
- `bt_core/indicators/sma.py:46-51` (`MovingAverageSimple` / `SMA`)
- `bt_core/indicators/ema.py` (`MovingAverageExponential` / `EMA`)
- `bt_core/indicators/rsi.py` (`RSI`)

**实际实现（以 SMA 为例）**

```python
# bt_core/indicators/sma.py:46-51
def next(self):
    _arr  = np.asarray(self.data.array, dtype=np.float64)
    sma = talib.SMA(_arr, self.p.period)
    self.line[0] = sma[-1]
```

每个 `next()` 都执行：
1. `np.asarray(self.data.array, dtype=np.float64)` —— 将**整个历史数组** copy/convert 成 numpy；
2. `talib.SMA(_arr, self.p.period)` —— 跑完整段历史，分配一个完整输出数组；
3. `self.line[0] = sma[-1]` —— 只取最后一个值，其余全部丢弃。

单 indicator 每 bar 复杂度 O(N)，整个回测 O(N²)。多个 indicator 叠加（SMA + EMA + RSI + Highest 等），这是 CPU 大头。

**优化方案**

1. **增量/递推公式**
   - SMA：维护滚动窗口和 `window_sum`，每 bar 加新值、减旧值：`sma = window_sum / period`，O(1) 每 bar。
   - EMA：`ema[t] = alpha * price[t] + (1-alpha) * ema[t-1]`，O(1) 每 bar。
   - RSI：Wilder 平滑，维护上升/下降移动平均，O(1) 每 bar。
   - Highest/Lowest：单调队列（`collections.deque`）维护滑动窗口最值，均摊 O(1)。

2. **如必须用 TA-Lib 向量化，改为一次性预计算**
   在 `_start` / `nextstart` 阶段对输入调用一次 TA-Lib，缓存完整结果数组，后续 `next()` 按索引 `cached[idx]` 取当前值。仅适用于输入数据完全已知的离线回测。

3. **仅传滚动窗口**
   即使保留 TA-Lib，也传 `self.data.get(size=self.p.period)` 而不是 `self.data.array`，让 TA-Lib 处理的数据量为 O(period) 而非 O(N)。

**收益估计**：通常占 CPU 40% 以上，是收益最大的优化点。

---

### 2.2 Feed / Resample 频繁构造 `datetime` 对象

**代码位置**

- `bt_core/feed.py:226-230`（`load()` 中 `_tzinput` 转换）
- `bt_core/feed.py:158-170`（`_getnexteos` 使用 `num2date` + `datetime.combine`）
- `bt_core/resamplerfilter.py:207-211`（`_barover_months` 仍用 `num2date`）
- `bt_core/resamplerfilter.py:213-216`（`_barover_years` 仍用 `num2date`）
- `bt_core/resamplerfilter.py:251-283`（`_barover_subdays` 多次 `num2date(...).time()`）
- `bt_core/resamplerfilter.py:285-324`（`_dataonedge` 中 `data.datetime.date()`）
- `bt_core/resamplerfilter.py:327-382`（`_calcadjtime` 中 `num2date` + `replace` 链）

**实际实现示例**

```python
# bt_core/feed.py:226-230
dt = self.lines.datetime[0]
if self._tzinput:
    dtime = num2date(dt, localize=True)
    self.lines.datetime[0] = dt = date2num(dtime)
```

```python
# bt_core/resamplerfilter.py:207-211 (尚未数值化的部分)
def _barover_months(self, data):
    dt = data.num2date(self.bar.datetime).date()
    yearmonth = dt.year * 100 + dt.month
    bardt = data.datetime.datetime()
    bar_yearmonth = bardt.year * 100 + bardt.month
    return bar_yearmonth > yearmonth
```

**问题**
- `num2date` 每次构造 Python `datetime` 对象并做时区转换；
- `_barover_subdays` / `_calcadjtime` 反复 `.time()` / `.replace()` / `combine()`；
- Python `datetime` 与 `pytz` 转换本身很重，resample 每 bar 调用多次。

**注意**：`_barover_days` / `_barover_weeks` 已改为整数算术（见第 1 节"已有优化"），但仍需推广到月/年/子日级别。

**优化方案**

1. **月/年边界改用整数运算**
   - 预计算 `(year, month)` 整数对：可从 `ts2intdt` 风格的整数日期得到 `year*100+month`；
   - `_barover_months` / `_barover_years` 直接比较整数对，避免 `num2date().date()`。

2. **子日边界用数值时间戳**
   - `_barover_subdays` 的 `point` 计算改为基于浮点 timestamp 的模运算，避免 `num2date().time()`；
   - 预先把 session start/end 转成数值 timestamp 缓存。

3. **`_getnexteos` 预计算**
   - 每个 session 的开始/结束预计算为数值 timestamp，直接比较 `datetime[0]`；
   - 避免每 bar `datetime.combine(...)` + `date2num(...)`。

4. **时区转换只在最终输出做**
   回测主循环保持 UTC 或内部数值表示，时区转换推迟到展示/落盘阶段。

---

## 3. 高优先级卡点详细说明与优化方案

### 3.1 Execution `Lines` 属性每次访问分配新数组

**代码位置**

- `bt_core/execution/core/finance/line.pyx:82-110`（`high` / `low` / `close` 等 property）

**问题描述**

```cython
property high:
    def __get__(self):
        cdef double[:] arr = <double[:self._size]>self.high.data()
        return np.asarray(arr)
```

调用方（如 filler）每次 `lines.high[exec_loc]` 都会：
1. 创建 memoryview；
2. `np.asarray` 创建新数组；
3. 再索引。

每次属性访问都是一次分配。

**优化方案**

1. **缓存 numpy 数组**
   在 `Lines` 中缓存每列的 numpy 视图，仅当 `_size` 变化时重新创建：
   ```cython
   cdef object _high_cache
   cdef Py_ssize_t _high_cache_size
   property high:
       def __get__(self):
           if self._high_cache is None or self._high_cache_size != self._size:
               self._high_cache = np.asarray(<double[:self._size]>self.high.data())
               self._high_cache_size = self._size
           return self._high_cache
   ```

2. **暴露 C 级 `__getitem__` / `get_price(idx)`**
   直接索引底层 C++ `std::vector<double>`，不经过 Python 数组。

3. **filler 中一次性取出所需列数组**
   在 filler 入口处取出 `high_arr = lines.high`、`low_arr = lines.low` 等，循环内反复使用同一引用，避免循环内反复触发 property。

---

### 3.2 Analyzer 重复拉取 snapshot / SHM events

**代码位置**

- `bt_core/strategy.py:237-249`（`on_dt_over`）
- `bt_core/analyzers/periodstats.py:78-79`（已使用传入 snapshot，✓）
- 其他 analyzer 中 `on_dt_over` / `notify_timer` 仍可能各自 `get_snapshot()` / `get_shm_events()`

**实际实现**

```python
# bt_core/strategy.py:237-249
def on_dt_over(self, last_dts: int, dts: int):
    snapshot = self.store.on_dt_over(self.experiment_id, last_dts, dts)
    if snapshot:
        self.shm_chan.publish_snapshot(snapshot)
        self.snapshot = snapshot

    self.shm_chan.publish_sentinel(dts)

    for analyzer in self.analyzers:
        if hasattr(analyzer, 'on_dt_over'):
            analyzer.on_dt_over(dts, snapshot)   # snapshot 已传入
```

**现状评估**
- `Strategy.on_dt_over` 已经把 snapshot 作为参数传给 analyzer；
- `PeriodStats` 已经接收 `snapshot` 参数（不再 `get_snapshot()`），✓；
- 但仍可能有 analyzer 在 `notify_metrics` / `notify_timer` 路径中各自调用 `self._owner.get_snapshot()` 或 `self.get_shm_events()`。

**优化方案**

1. **每 bar 只拉一次 snapshot 和一次 events batch**
   `Strategy.on_dt_over()` / `notify_metrics()` 中统一拉取，作为参数下发给各 analyzer。

2. **analyzer 接口统一**
   `on_dt_over(self, dt0, snapshot, events_batch)`、`notify_metrics(self, dt0, snapshot)`；旧签名保留兼容但内部复用传入数据。

3. **允许 `stdstats=False`**
   用户可通过 `Cerebro(stdstats=False)` 关闭默认 10+ 个 analyzer（见 `cerebro.py:309-321`），减少注册数量。

---

### 3.3 `PeriodStats` 全量收益统计 → O(N²)

**代码位置**

- `bt_core/analyzers/periodstats.py:89-96`

**实际实现**

```python
def on_dt_over(self, dt0: int, snapshot: SnapshotBody):
    current_value = snapshot.account.portfolio_value + snapshot.account.cash
    if self.last_value <= 0:
        return
    ret = (current_value / self.last_value) - 1.0
    self.period_returns.append(ret)
    self.last_value = current_value

    avg_ret = np.mean(self.period_returns)          # 全量扫描
    std_ret = np.std(self.period_returns)           # 全量扫描
    pos_cnt = sum(1 for r in self.period_returns if r > 0.0)   # 全量扫描
    neg_cnt = sum(1 for r in self.period_returns if r < 0.0)   # 全量扫描
    ...
```

**问题**
`period_returns` 每 period 增长一个元素，每次都全量计算 `mean/std/counts`，总复杂度 O(N²)。

**优化方案**

1. **Welford 在线算法**
   维护 `n`、`mean`、`M2`，每 period 一次更新：
   ```
   n += 1
   delta = x - mean
   mean += delta / n
   delta2 = x - mean
   M2 += delta * delta2
   variance = M2 / (n - 1)   # 当 n > 1
   ```

2. **增量计数器**
   维护 `pos_cnt`、`neg_cnt`、`zero_cnt`，新收益到来时只比较一次并增量更新。

3. **降低 publish 频率**
   仅在有分析输出需求时计算完整统计量，或按用户配置降低 publish 频率。

---

## 4. 中优先级卡点与优化方案

### 4.1 Strategy 每 bar housekeeping

**代码位置**

- `bt_core/strategy.py:228-235`（`clk_update`）

**实际实现**

```python
def clk_update(self):
    newdlens = np.array([len(d) for d in self.datas])
    if any(nl > l for l, nl in zip(self._dlens, newdlens)):
        self.forward()
    self.lines.datetime[0] = np.max([d.datetime[0] for d in self.datas if len(d)])
    self._dlens = newdlens
```

**问题**
- 对通常只有 1~3 个 data 的系统使用 `np.array` + `np.max`，比纯 Python `max`/列表推导开销大；
- 每 bar 都重新构造数组。

**优化方案**

1. **data 数量少时直接用 Python 内建**
   ```python
   newdlens = [len(d) for d in self.datas]
   if any(nl > l for l, nl in zip(self._dlens, newdlens)):
       self.forward()
   self.lines.datetime[0] = max((d.datetime[0] for d in self.datas if len(d)), default=0.0)
   self._dlens = newdlens
   ```

2. **维护全局最大长度变量**
   只有某个 data 长度变化时才更新，避免每 bar 全量计算。

---

### 4.2 `dateintern` 热路径创建 Python datetime

**代码位置**

- `bt_core/utils/dateintern.pyx:32-53`（`num2date`）

**优化方案**

1. 时区对象 `UTC_TZ`、`SHANGHAI_TZ` 已缓存，保持现状；
2. 在 resample 边界判断、feed 过滤等**热路径**避免调用 `num2date`，全部使用数值 timestamp（见 2.2）；
3. 如必须返回 datetime，考虑新增 `num2date_parts` 直接返回 `(year, month, day, hour, minute, second)` 元组，避免完整对象构造。

---

### 4.3 CSV / Pandas feed 逐行加载

**代码位置**

- `bt_core/feeds/csvgeneric.py:139-156`
- `bt_core/feeds/pandafeed.py:245-277`

**问题**
每行通过 `getattr` 查找 `params` / `lines`，并做字符串→浮点转换。

**优化方案**

1. **`start()` 中预计算 `(line_ref, column_index)` 元组列表**
   避免每行反射查找；
2. **Pandas feed 一次性转 numpy**
   将所需列一次性 `to_numpy(dtype=np.float64)`，批量写入 `LineBuffer`，而非 `iloc` 逐行访问；
3. **数值解析使用 `np.fromiter` 或 pandas 原生类型转换**。

---

### 4.4 SHM metric 逐个发布与字符串解码

**代码位置**

- `bt_core/shm/shm_buffer.pyx:333-354`（`publish_metric`）
- `bt_core/sink/manager.py:73-87`
- `bt_core/feed.py:375-392`（`notify_metrics` 每 bar 5 个 metric）
- `bt_core/strategy.py:255-266`（`notify_metrics` 遍历 `self.metrics` 列表）

**问题**
每个 metric 都经过 GIL、`strncpy`、UTF-8 decode；每 bar 可能有 5+ feed metrics、N 个 indicator metrics、10+ analyzer metrics。

**优化方案**

1. **减少 metric 数量**
   默认只开启必要 analyzer；`strategy._setupMetrics()`（`strategy.py:251-266`）中的 `ind_log` 按需注册；
2. **提供批量接口**
   `publish_metrics(metric_array, value_array, dt_array)`，一次写入共享内存；
3. **metric name 使用固定长度二进制 ID**
   sink 端按 ID 查表，避免每 bar 解码大量字符串。

---

## 5. 推荐优化顺序

1. **Indicator 全量重算**（第 2.1 节）
   - 收益最大，通常占 CPU 40% 以上；
   - 改造范围明确（`basicops.py` / `sma.py` / `ema.py` / `rsi.py`）；
   - 风险：需保证数值精度与 TA-Lib 一致，建议对比测试。

2. **Feed / Resample datetime 对象**（第 2.2 节）
   - 减少对象分配，立竿见影；
   - 已有 `_barover_days` / `_barover_weeks` 数值化的成功案例可复用；
   - 风险：月/年/子日边界逻辑较复杂，需覆盖跨月、跨年、session 边界用例。

3. **Analyzer snapshot / SHM 复用 + PeriodStats 在线算法**（第 3.2 / 3.3 节）
   - 降低跨线程/IPC 和重复计算；
   - `PeriodStats` 改 Welford 简单且安全。

4. **Execution `Lines` 数组缓存**（第 3.1 节）
   - 减少 filler 周边分配；
   - 风险：需注意 `_size` 变化时失效缓存。

5. **中优先级项**（第 4 节）
   - 作为后续打磨，单项收益较小但累积可观。

---

## 6. Profiling 与验证建议

- 以上分析基于静态代码阅读，**未运行 profiler**；
- 实际优化前建议用以下工具在真实回测上验证热点：
  - `py-spy record -o profile.svg -- python tests/run_simulation.py`（采样 profiler，可视化火焰图）
  - `python -m cProfile -o profile.out tests/run_simulation.py` + `snakeviz profile.out`
  - `kernprof -l -v tests/run_simulation.py`（line_profiler，逐行）
- 重点关注：
  - `talib.SMA` / `talib.MAX` / `np.asarray` 累计时间
  - `num2date` / `datetime.combine` / `isocalendar` 累计调用次数
  - `Strategy.clk_update` / `LineBuffer.forward` 每 bar 调用次数
  - `SharedRingBuffer.publish_metric` / `LogRingBuffer.publish_metric` 调用次数

---

## 7. 风险与回归测试要点

修改以下文件属于基础结构改动，需同步跑完整测试套件：

- `bt_core/linebuffer.py` / `bt_core/lineseries.py`：影响 line binding、resample、replay；
- `bt_core/resamplerfilter.py`：影响所有 resample/replay 边界判断；
- `bt_core/feed.py`：影响所有数据加载与时区转换；
- `bt_core/indicators/*.py`：影响所有指标数值精度。

**最小回归测试集建议**
- 单 indicator 数值对比（与 TA-Lib 原始实现对比前 N 个值）；
- Resample 日→周、日→月边界用例；
- 多 data feed 对齐（不同时间长度）；
- 全周期回测净值曲线对比（优化前后应一致或在浮点误差内）。

---

## 附录：行号索引（当前仓库）

| 主题 | 文件 | 行号 |
|------|------|------|
| Indicator 全量重算（SMA） | `bt_core/indicators/sma.py` | 46-51 |
| Indicator 全量重算（Highest） | `bt_core/indicators/basicops.py` | 98-103 |
| Indicator 全量重算（Lowest） | `bt_core/indicators/basicops.py` | 123-128 |
| Feed `_tzinput` 转换 | `bt_core/feed.py` | 226-230 |
| Feed `_getnexteos` | `bt_core/feed.py` | 158-170 |
| Resample 日/周（已优化） | `bt_core/resamplerfilter.py` | 191-205 |
| Resample 月（待优化） | `bt_core/resamplerfilter.py` | 207-211 |
| Resample 子日 | `bt_core/resamplerfilter.py` | 251-283 |
| Resample `_calcadjtime` | `bt_core/resamplerfilter.py` | 327-382 |
| Strategy `clk_update` | `bt_core/strategy.py` | 228-235 |
| Strategy `on_dt_over` | `bt_core/strategy.py` | 237-249 |
| Strategy `notify_metrics` | `bt_core/strategy.py` | 255-266 |
| PeriodStats 全量统计 | `bt_core/analyzers/periodstats.py` | 89-96 |
| LineBuffer `_cur_idx` 缓存（已有优化） | `bt_core/linebuffer.py` | 100-139 |
| LineBuffer `forward` 批量 extend（已有优化） | `bt_core/linebuffer.py` | 208-210 |
| Cerebro 默认 analyzer 注册 | `bt_core/cerebro.py` | 309-321 |