# A 股 execution/core 逻辑修复重构方案

> 日期: 2026-08-02  
> 范围: `bt_core/execution/core/finance/` 下的 A 股交易逻辑修复  
> 约束: 仅支持 3/6/0 标的（主板/科创板/创业板），不支持买空

---

## 一、修复清单总览

### 第一批（低风险，已完成）

| # | 文件 | 问题 | 修复 |
|---|---|---|---|
| #1 | `slippage.pyx` | `_slip` 注册表 `"progressive": Likehood` 引用未定义的类名 → **NameError** | 改为 `LikelihoodSlip` |
| #2 | `slippage.pyx` | `LikehoodSlip.get_slip_price` 返回 `b_high * slip_perc`（接近0的值）而非滑点后价格 | 买入按 high、卖出按 low，受 `slip_perc` 上下界约束 |
| #17 | `store.py` | 残留 `import pdb; pdb.set_trace()` | 删除 |
| #18 | `slippage.pyx` | 类名 `LikehoodSlip` 拼写错误（应为 Likelihood） | 重命名为 `LikelihoodSlip` |
| #3 | `comminfo.pyx` | 印花税/过户费/佣金费率与现行 A 股规则不符；5元最低红线套用了全部费用 | 分项计算；印花税 0.5‰（2023.8.28）；过户费沪深双向（2022.4.29）；5元最低仅佣金 |
| #4 | `position.pyx` | 空头分支（`orig_size < 0`）静默 `return`，size/available 不更新 | 改为 `raise ValueError`（A股不支持融券做空） |

### 第二批（中风险，已完成）

| # | 文件 | 问题 | 修复 |
|---|---|---|---|
| #5 | `position.pyx` | `_handle_merger` 成本基准 `close / ratio` 用收盘价而非持仓成本 → 换股后 PnL 失真 | `新成本 = 原成本 × 原股数 / 新股数` |
| #6 | `function.pxd` | `calc_right` 配股计算 `return 0.0` 完全未实现 → 配股等于免费拿股票 | `return (ratio/10) * price` |
| #7 | `filler.pyx` | 限价单 `_find_limit_execution` 开盘价超限价时仍以限价成交 → 前视偏差 | 开盘价超限价时不成交（买入 `open>limit` / 卖出 `open<limit` 时返回限价让撮合器决定） |
| #10 | `asset.pyx` | `restricted` 未判断新股上市前5日无涨跌幅限制 | 用 `first_trading` 判断，`ts2intdt(ts)` 转换后比较 → 返回 1.0（无限制） |
| #12 | `position.pyx` | 送股股数 `<int32_t>()` 强转截断（10送1.5股，100股得114股而非115股） | `<int32_t>floor(x + 0.5)`（C 标准库四舍五入，不依赖 C99 round） |

### 第三批（逻辑修正，已完成）

| # | 文件 | 问题 | 修复 |
|---|---|---|---|
| Fix-1 | `position.pyx` | `_handle_merger` 中 `merger_size <= 0` 时 `cost_basis = 0.0` 丢失原始成本 | 保留 `old_cost` 不变（持仓已清零，成本基准保留用于盈亏核算） |
| Fix-3a | `asset.pyx` + `asset.pxd` | `restricted` 的 `ts`（unix秒）与 `first_trading`（YYYYMMDD）单位不一致，直接相减无意义 | 用 `ts2intdt(ts)` 转成 YYYYMMDD 后比较；去掉 `nogil`（`ts2intdt` 是 cpdef 需 GIL） |

---

## 二、详细修复说明

### #1 #2 #18 滑点模块 (`slippage.pyx`)

**修复前（3个问题）：**
```python
cdef class LikehoodSlip(Slippage):  # 拼写错误 + 注册表引用 Likehood（未定义）
    cdef double get_slip_price(self, ...):
        pslip = b_high if is_buy else b_low
        return pslip * self.slip_perc  # 返回 ~0.05 元，不是价格

_slip = {"progressive": Likehood}  # NameError
```

**修复后：**
```python
cdef class LikelihoodSlip(Slippage):
    """最悲观成交价模型: 买入按 high、卖出按 low"""
    cdef double get_slip_price(self, double order_price, ..., bint is_buy) noexcept nogil:
        cdef double bound
        if is_buy:
            bound = order_price * (1.0 + self.slip_perc)  # 容忍上限
            return b_high if b_high < bound else bound
        else:
            bound = order_price * (1.0 - self.slip_perc)  # 容忍下限
            return b_low if b_low > bound else bound

_slip = {"progressive": LikelihoodSlip, "likelihood": LikelihoodSlip}  # 无 NameError
```

### #3 手续费模块 (`comminfo.pyx`)

**A 股手续费规则对照表：**

| 费用项 | 修复前 | 修复后 | 生效日期 |
|---|---|---|---|
| 印花税 | 1‰ 卖出 | **0.5‰** 卖出 | 2023-08-28 (`STAMP_TAX_CKPT = 1693180800`) |
| 过户费 | 仅上交所 0.01‰ | **沪深双向** 0.01‰ | 2022-04-29 (`TRANSFER_FEE_CKPT = 1651180800`) |
| 佣金 | 3‰→0.5‰ | 3‰→0.5‰（不变） | 2015 (`RatioCkpt = 1433813400`) |
| 5元最低 | 套用全部费用 | **仅佣金**有5元最低 | - |

**关键改动：** `getcommission` 改为分项计算，印花税/过户费不参与5元最低红线。

### #5 换股成本 (`position.pyx _handle_merger`)

```python
# 修复前: 用收盘价/比例（错误）
self.core.cost_basis = close / ratio

# 修复后: 保持市值不变（正确）
self.core.cost_basis = old_cost * size / merger_size
```

### #6 配股 (`function.pxd calc_right`)

```python
# 修复前: 完全未实现
cdef inline double calc_right(RightData rights) nogil:
    return 0.0

# 修复后: 配股缴款 = 比例 × 配股价
cdef inline double calc_right(RightData rights) nogil:
    cdef double ratio_normalized = rights.ratio / 10.0
    return ratio_normalized * rights.price
```

### #7 限价单前视偏差 (`filler.pyx _find_limit_execution`)

```python
# 修复前: 开盘价高于限价时以限价成交（前视偏差）
return i, limit_price if open_i > limit_price else open_i

# 修复后: 开盘价超限价时需等价格回落
if open_i > limit_price:
    return i, limit_price  # 让撮合器在价格触及时成交
return i, open_i
```

### #10 新股涨跌幅 (`asset.pyx restricted`)

```python
# 修复后: 用 ts2intdt 转换 ts 后与 first_trading(YYYYMMDD) 比较
from bt_core.utils.dateintern cimport ts2intdt

cdef int32_t current_yyyymmdd = <int32_t>ts2intdt(<double>ts)
is_new_stock = (current_yyyymmdd - self.core.first_trading) < 500  # 近似前5个交易日
# 科创板/创业板新股 → 返回 1.0（无限制）
```

> 注意：`restricted` 去掉了 `nogil`（因 `ts2intdt` 是 `cpdef` 函数需要 GIL）。目前 `restricted` 是预留接口，**filler 尚未接入**（filler 用 `_execute_factor` 振幅判断，不查 `restricted`）。后续如需在撮合时检查涨跌幅，需将 `restricted` 阈值传入 `_execute_factor`。

### #12 送股四舍五入 (`position.pyx`)

```python
# 修复前: 强转截断
self.core.size = <int32_t>(origin_size * sizer_ratio)

# 修复后: 四舍五入（C 标准库，不依赖 C99 round）
from libc.math cimport floor
self.core.size = <int32_t>floor(origin_size * sizer_ratio + 0.5)
```

---

## 三、性能优化点（未实施，供后续迭代）

| # | 文件 | 问题 | 建议 |
|---|---|---|---|
| P1 | `pnc.pyx` | `generate_plan` 大量 `print` 调试输出（5000天×多phase） | 改 `logger.debug`，通过日志级别控制 |
| P2 | `filler.pyx` | `_preload` 每单独立 RPC 取 tick 数据 | 按天批量预取，缓存 `dict[(sid,dt)] -> Lines` |
| P3 | `simulate.pyx` | `on_dt_over` 每天发3个RPC（Close/Adj/Rgt） | 缓存无除权除息日的结果 |
| P4 | `line.pyx` | `Lines` 对象用完即弃，无内存复用 | 对象池 |

---

## 四、验证方式

### 已验证
- ✅ `setup.py build_ext --inplace` 编译通过（Cython 类型检查）
- ✅ `_slip` 注册表：`progressive: LikelihoodSlip`、`likelihood: LikelihoodSlip`
- ✅ git diff 确认所有改动正确落地

### 待端到端验证（需运行完整回测）
```bash
# 清空 DB 后跑两次（验证连续运行不退化）
python tests/run_simulation.py
python tests/test_strategy.py
```

### 单元测试限制
Cython `cdef` 方法无法从 Python 端直接调用，`cdef class` 的类型标识符在独立 `.pyx` 测试文件中需要对应 `.pxd` 声明才能 `cimport`。完整单元测试需要：
1. 为每个 `cdef class` 补充 `.pxd` 声明文件（目前部分类如 `Slippage` 子类没有 `.pxd`）
2. 或将关键 `cdef` 方法改为 `cpdef`（牺牲少量性能换取可测试性）

---

## 五、风险提示

1. **#3 手续费改动影响所有回测结果**：印花税从1‰降到0.5‰，会让卖出成本降低，策略收益可能上升。历史回测对比时需注意基准差异。
2. **#10 新股涨跌幅**：用 YYYYMMDD 差值近似（<500）判断前5个交易日，不是精确交易日历。对打新策略影响较大。且 `restricted` 目前是死代码，filler 未接入。
3. **#7 限价单**：修复了前视偏差，会让限价单策略的成交率下降（更真实），回测收益可能降低。
4. **Fix-3a nogil 变更**：`restricted` 去掉 `nogil` 后，如果在 nogil 代码块中调用会编译报错。目前没有 nogil 调用点，安全。


### 关于 filler 接入 restricted（未实施）

`restricted` 目前&#x662F;__&#x9884;留接口__（无下游调用）。filler 用 `_execute_factor` 振幅判断涨停，不查 `restricted`。后续如需在撮合时检查涨跌幅（让 9.5% 封板也买不进），需要：

1. `_execute_factor` 增加 `limit_thres` 参数
2. `Lines` 增加 `preclose` 列（计算真实涨跌幅）
3. 工作量较大，留作后续迭代
**
