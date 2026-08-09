# Implementation Plan

[Overview]
审计并修复 bt_core/analyzers/ 下全部 10 个 analyzer 的 metric 输出逻辑，保证公式正确、输出契约一致（每个 metric 在每个交易日都 publish，用 NaN 表示"数据不足"），以及命名规范统一。

上轮会话已修复 6 个文件（calmar/timereturn/sharpe/sqn/drawdown/pyfolio）的崩溃和 NaN 行数问题。本轮深入审计剩余 5 个（benchmark/orders/periodstats/positions/transactions），并统一全部 10 个的输出契约和公式一致性。

[Types]

无新增类型。所有修复均在现有 analyzer 类内部完成。

**输出契约定义（本次统一规范）：**

| 契约 | 规则 | 理由 |
|------|------|------|
| 契约A：每日必发 | 每个 metric 在每个 `on_dt_over(dt0, snapshot)` 调用时必须 publish | 保证 parquet 行数一致，便于按时间戳 join |
| 契约B：NaN 表示数据不足 | 当数据不足以计算（如首日无前值、无交易、方差为0）时，publish `float('nan')` | NaN 在 parquet 中原生支持，下游可用 `dropna()` 灵活处理，区分"无数据"vs"真实值0" |
| 契约C：0 表示真实值0 | 当计算结果确实为 0（如无平仓日的 NetPnL 变化）时，publish `0.0` | 避免把"真实为0"误认为"无数据" |
| 契约D：命名规范 | metric 名采用 `PascalCase`，无下划线/前缀混用 | 统一 parquet 列名风格 |

[Files]

**全部修改均在 bt_core/analyzers/ 目录，无新增/删除文件。**

### P0 修复（公式错误，直接影响数值正确性）

- **bt_core/analyzers/periodstats.py**
  - 问题1：`np.std(self.period_returns)` 使用总体标准差（ddof=0），与 sharpe.py 的样本标准差（ddof=1）不一致
  - 修复：改为 `np.std(self.period_returns, ddof=1)` 当样本数>1 时，否则 NaN
  - 问题2：首日 `if self.last_value <= 0: return` 跳过 publish，违反契约A
  - 修复：移除 early return，首日 publish NaN（因为无前值无法算 ret）
  - 问题3：docstring 承诺 best/worst/nochange 但未输出
  - 修复：补全 best/worst/nochange metric 输出

### P1 修复（契约一致性）

- **bt_core/analyzers/calmar.py**（上轮已修复年化崩溃）
  - 补充：`if self._initial_value <= 0: return` 改为 publish NaN 后 return，保证首日有行

- **bt_core/analyzers/timereturn.py**（上轮已修复年化崩溃）
  - 同 calmar.py，补充首日 NaN publish

- **bt_core/analyzers/positions.py**
  - 问题：`daily_win_rate = ... else 0.0` 当无平仓时用 0，违反契约B
  - 修复：改为 `float('nan')` 当 daily_closed == 0
  - 同理 `daily_avg_hold` 改为 NaN

### P2 修复（命名规范统一）

- **全部 10 个 analyzer**
  - 统一 metric 名为 PascalCase（当前 `drawDown`→`DrawDown`、`ind_SMA_close` 保持原样因为是 indicator prefix）
  - 移除带空格的命名（`PeriodStats AvgRet`→`PeriodStatsAvgRet`）

### 无需修改（已正确）

- **benchmark.py**：公式正确，searchsorted 越界已用 NaN 兜底 ✅
- **orders.py**：逻辑简单正确 ✅
- **transactions.py**：逻辑简单正确 ✅
- **drawdown.py**：上轮已移除 print，公式正确 ✅
- **pyfolio.py**：上轮已移除 print ✅
- **sharpe.py**：上轮已修复 NaN，公式正确 ✅
- **sqn.py**：上轮已修复 NaN，公式正确 ✅

[Functions]

### 修改的函数

**1. PeriodStats.on_dt_over** (bt_core/analyzers/periodstats.py:78)
- 当前：`if self.last_value <= 0: return`（跳过首日）
- 修改后：
  ```python
  def on_dt_over(self, dt0, snapshot):
      current_value = snapshot.account.portfolio_value + snapshot.account.cash
      
      if self.last_value <= 0:
          # 首日无前值，publish NaN 保持行数一致
          self._publish_nan(dt0)
          self.last_value = current_value
          return
      
      ret = (current_value / self.last_value) - 1.0
      self.period_returns.append(ret)
      self.last_value = current_value
      
      n = len(self.period_returns)
      avg_ret = np.mean(self.period_returns)
      std_ret = np.std(self.period_returns, ddof=1) if n > 1 else float('nan')
      best_ret = max(self.period_returns)
      worst_ret = min(self.period_returns)
      
      pos_cnt = sum(1 for r in self.period_returns if r > 0.0)
      neg_cnt = sum(1 for r in self.period_returns if r < 0.0)
      nochange_cnt = sum(1 for r in self.period_returns if r == 0.0)
      if self.p.zeroispos:
          pos_cnt += nochange_cnt
      
      self.log_shm.publish_metric(b"PeriodStatsAvgRet", avg_ret, dt0)
      self.log_shm.publish_metric(b"PeriodStatsStd", std_ret, dt0)
      self.log_shm.publish_metric(b"PeriodStatsBest", best_ret, dt0)
      self.log_shm.publish_metric(b"PeriodStatsWorst", worst_ret, dt0)
      self.log_shm.publish_metric(b"PeriodStatsPosCnt", pos_cnt, dt0)
      self.log_shm.publish_metric(b"PeriodStatsNegCnt", neg_cnt, dt0)
      self.log_shm.publish_metric(b"PeriodStatsNoChangeCnt", nochange_cnt, dt0)
  ```

**2. Calmar.on_dt_over** (bt_core/analyzers/calmar.py:76)
- 当前：`if self._initial_value <= 0: return`（跳过）
- 修改后：改为 publish NaN 后 return
  ```python
  if self._initial_value <= 0:
      self.log_shm.publish_metric(b"MaxDrawdown", float('nan'), dt0)
      self.log_shm.publish_metric(b"Calmar", float('nan'), dt0)
      return
  ```

**3. TimeReturn.on_dt_over** (bt_core/analyzers/timereturn.py:62)
- 同 Calmar，首日 publish NaN
  ```python
  if self._initial_value <= 0:
      self.log_shm.publish_metric(b"DailyReturn", float('nan'), dt0)
      self.log_shm.publish_metric(b"CumReturn", float('nan'), dt0)
      self.log_shm.publish_metric(b"AnnualReturn", float('nan'), dt0)
      return
  ```

**4. PositionsAnalyzer.on_dt_over** (bt_core/analyzers/positions.py:163)
- 当前：`daily_win_rate = ... else 0.0`
- 修改后：
  ```python
  daily_win_rate = (daily_won / daily_closed) if daily_closed > 0 else float('nan')
  cum_win_rate = (self.cum_won / self.cum_closed) if self.cum_closed > 0 else float('nan')
  daily_avg_hold = (total_hold_days / daily_closed) if daily_closed > 0 else float('nan')
  ```

[Classes]

无新增/删除类。所有修改均在现有类的 `on_dt_over` 方法内部。

[Dependencies]

无新增依赖。所有使用的库（numpy, math）已存在于现有 import 中。

[Testing]

### 验证策略

1. **静态验证**：代码审查确认每个 analyzer 的 `on_dt_over` 在所有路径下都调用 `publish_metric`
2. **动态验证**（可选，需 Act mode）：
   - 运行 `tests/run_simulation.py` 生成新 parquet
   - 验证所有 metric 行数一致（应为交易日天数 × metric 数）
   - 验证无 datetime=0 行
   - 验证首日该为 NaN 的 metric 确实是 NaN

### 测试文件
- `tests/run_simulation.py`：现有回测脚本，无需修改
- `tests/logs/log_cerebro_0.parquet`：旧的输出，修复后重新生成对比

[Implementation Order]

1. **periodstats.py**：修复 std ddof、首日 NaN、补全 best/worst/nochange
2. **calmar.py**：补充首日 NaN publish
3. **timereturn.py**：补充首日 NaN publish
4. **positions.py**：win_rate/avg_hold 改为 NaN
5. **命名统一**（可选，P2）：统一 metric 命名为 PascalCase
6. **验证**：运行回测生成新 parquet 反向验证行数一致性