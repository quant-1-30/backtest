# 涨跌停撮合硬约束实施计划（P0）

> 2026-08-25 · 对应评审报告 §二 P0（`docs/ashare_adaptability_review.md`）
> 目标：撮合层用 `Asset.restricted()` + 昨收价对成交价与成交可行性做硬约束，
> 消除"涨停买入放行 / 逃跌停成功 / 一字板全量成交"三类系统性高估。

---

## 一、现状与根因

| 组件 | 现状 | 问题 |
|---|---|---|
| `asset.pyx restricted(ts)` | 板块/新股豁免齐备，**零调用者**（行为已被 tests/behavior 锁定） | 死代码 |
| `filler.pyx _execute_factor` | 唯一触板处理：bar 振幅 < 1e-5 且收在区间极值才拒成交 | 只挡住"完美一字板"，且不知道涨跌停价位在哪 |
| `_fill` 滑点 | `get_slip_price` 可越过涨跌停价 | 成交价越界 |
| 触板未封死 bar | 按量×impact 正常全量成交 | 忽略封单排队 |

三类失真：①涨停价买单被放行且成交价可越涨停价；②跌停价卖单总能"逃跌停"成功；③盘中开板回封的 bar 仍全量成交。

## 二、设计

### 2.1 昨收价注入：零额外 RPC（关键决策）

filler 侧不知道昨收。但 `TrackerActor` 在每个 Day Rollover 已经按 (T-1, T] 拉过 `RpcTopic.Close`（结算用）——把这张 close 表缓存下来，`process_order` 在调 filler 前把约束参数**盖到订单上**：

```
simulate.pyx TrackerActor:
    self._last_closes: dict[bytes sid -> double prev_close]   # rollover 时更新

process_order(order):
    order.ref_close  = self._last_closes.get(sid, 0.0)      # 复牌/首日无昨收 → 0
    order.limit_ratio = asset.restricted(core.created_dt)   # 新股前5日/无板 → 1.0
    _fillers[order.filler](order, ...)
```

`Order` 增加 `cdef public double ref_close, limit_ratio`（默认 0.0/1.0）。
**`__reduce__` 9 参保持不变**（writer/sink pickle 兼容，新增属性不序列化——每次 process_order 现场重注）。

停牌复牌：rollover 按 (prev, curr] 拉 close，天然取到复牌前最后交易日收盘 ✓（与 §6.5 除权区间语义同构，无新边界）。

### 2.2 涨跌停价计算

```
limit_up   = round_half_up(ref_close * (1 + ratio), 2)     # 分辨率 0.01
limit_down = round_half_up(ref_close * (1 - ratio), 2)
```

- `ratio >= 1.0` 或 `ref_close <= 0` → 不约束（新股豁免期/无昨收，与交易所规则一致）。
- 交易所口径是**四舍五入到分**（SSE/SZSE 一致）；Python `round` 是银行家舍入，用 `floor(x*100+0.5)/100`。
- 实现为 filler 模块级 `cdef inline` 纯函数，便于行为测试直接覆盖。

### 2.3 成交价 clamp（`_fill`）

滑点后的 `slip_price` 夹到 `[limit_down, limit_up]`：

```
if ref_close > 0 and limit_ratio < 1.0:
    slip_price = min(max(slip_price, limit_down), limit_up)
```

bar 内 OHLC 本身不会越界（真实行情），越界只可能来自滑点外推——clamp 后买单价永不超过涨停价、卖单价永不低于跌停价。

### 2.4 bar 级可行性分类（`_execute` 主循环）

对每根候选 bar 分类（`_execute_factor` 改造为 limit-aware）：

| bar 形态 | 判定 | 买单 | 卖单 |
|---|---|---|---|
| 一字涨停 `high==low==limit_up` | `amplitude<ε && high≈limit_up` | **拒绝**（排队不可成交） | 允许，价=limit_up |
| 一字跌停 `high==low==limit_down` | 同上对偶 | 允许，价=limit_down | **拒绝** |
| 触板且**收在板价**（封死收盘）`close≈limit_up/down` | close 命中板价 | **fill=0**（收盘仍封死，当日排不进） | 对偶 |
| 触板但收在板内（开过板） | high/low 命中、close 未命中 | 正常量×impact | 正常 |
| 未触板 | 都不命中 | 现行逻辑 | 现行逻辑 |

- 判定用 `abs(px - limit) < 1e-6`（价位是两位小数精确值，容差只为浮点）。
- 现行 `_execute_factor` 的"振幅+极值"启发式被上表**取代**（第一行即其一字板特例；其余形态按价位判定，不再猜）。
- 一字板被拒后订单自然产生 0 笔 bit——与现行"无 bit 不落库"语义一致；订单对象状态停留 Created/Submitted，不需要新状态。

### 2.5 触板 bar 的量折减（保守默认）

封死 bar 直接 0；开板 bar 保持 `volume × impact` 不折减（bar 成交量本身已包含板上成交，impact 已是参与率上限）。**不引入"封单估计"模型**（评审建议的可选项）——封单数据不在行情表里，估计值只会引入新的自由参数；先落地无参数版，若后续对打板策略仍显乐观再评估接入 level 数据。

## 三、改动清单

| 文件 | 改动 | 量级 |
|---|---|---|
| `order.pxd` / `order.pyx` | `ref_close` / `limit_ratio` 两个 public 属性（默认 0.0/1.0），`__reduce__` 不动 | 小 |
| `simulate.pyx` | rollover 缓存 `_last_closes`；`process_order` 注入两属性 | 小 |
| `filler.pyx` | 模块级 `cdef inline` 涨跌停价计算 + bar 分类函数；`_fill` clamp；`_execute` 主循环按分类跳过/定价 | 中（核心） |
| `tests/behavior/_finance_behavior.pyx` | 新增：价位 round_half_up 边界、bar 分类矩阵（一字/封死/开板/未触 × 买卖）、clamp 边界 | 小 |
| `tests/` | e2e：选含已知涨停密集段的标的+区间，断言一字涨停日买单零成交；复跑 verify_event_cash | 中 |

改 `.pyx` 均需重编（agents.md §2）；`Order` 属性新增不动 pickle 参数序（不变量 §5.7 保持）。

## 四、验证方案

1. **单元（行为测试）**：round_half_up 边界（如 10.01×1.1=11.011→11.01，9.99×0.9=8.991→8.99）；分类矩阵 10 例；clamp 边界。
2. **定向 e2e**：300308 在 2015 上半年（创业板牛市，涨停密集）或多只小市值标的，常量买入信号跑 3-6 个月：
   - 断言：所有买 bit 的 `executed_price ≤ 当日涨停价`、卖 bit `≥ 跌停价`（verify 脚本加一档检查，昨收从 Close RPC 独立取）；
   - 断言：一字涨停日（行情可独立判定）买单零 bit；
   - 费率恒等式/现金守恒仍 100%。
3. **回归金丝雀**：复跑三标的多标的 e2e（大盘股触板少）——订单流与验证结论应与 2026-08-25 基线基本一致（因 default filler 现行放行的触板日在新标的/区间才有差异）。

## 五、边界与已知不做

- **ST 5% 差异化**：`restricted()` 目前不识别 ST（需 instrument 表的名称标志），主板 ST 仍按 10% 建模——**单独小任务**（asset 注入 ST 标志 + restricted 分支 + 测试），不阻塞本计划。
- 科创板/创业板前 5 日无涨跌停（restricted()==1.0）天然走不约束路径。
- 北交所 30%、新股首日 44% 等细则：board 3/首日特殊幅度未建模，按现状 10% 兜底（与现状一致，不劣化）。
- 盘后固定价格交易、临时停牌机制不建模。

## 六、工期

实现 + 单测 1 天；定向 e2e + 回归 0.5 天。合计 **1.5 天**（评审估 1-2 天一致）。
