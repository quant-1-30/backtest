# 行为测试（A 股账务核心回归网）

锁住三轮评审已验证的语义（见 `docs/ashare_adaptability_review.md`、根目录 `agents.md` §5/§6）。
**改动 `bt_core/execution/core/finance/`、`timer.pyx`、`feed.py`、`linebuffer.py` 前必须跑过这里。**

## 运行

```bash
# poetry venv
$PY -m pytest tests/behavior -q        # 首次会自动编译 _finance_behavior.pyx（~30s）
$PY -m pytest tests/behavior -q -k timer
```

前提：仓库根目录已 `setup.py build_ext --inplace`（测试 cimport 的 .pxd 与运行时 .so 同源）。
`.pxd`/`.pyx` 变化会自动触发重编（见 `_extbuild.py`）。

## 覆盖面

| 文件 | 锁定的行为 |
|---|---|
| `_finance_behavior.pyx` + `test_finance.py` | T+1 锁定/解锁/超卖 raise、同日多笔 ts 时序、成本加权、已实现盈亏、10送2转1派5（130/+50/7.6923）、10配3@8（130/-240/9.5385、配股 T+1）、零股 floor（136/31）、available≤size、退市全损失、吸并换标的、停牌保状态、订单累计成交量状态机、pickle 往返、`restricted()` 板块/新股跨月豁免、Account 现金流符号与 ymd 归一化守卫 |
| `test_comminfo.py` | 费率四分界恒等式（佣金 3‰→0.5‰@2015-06-09、印花 1‰→0.5‰@2023-08-28、过户费三分界、深市 2015 前免收）、5 元最低仅适用佣金 |
| `test_timer.py` | repeat≤0 每日一次、负 offset、allow 透传、日内 repeat |
| `test_feed_factor.py` | 除权因子 (record_dt, current_dt] 到期区间、停牌洞内事件复牌补乘（复合）、当根 bar 保留原值、量价反向、warmup 推进 |
| `test_linebuffer.py` | get 窗口公式 [idx+ago-size+1, idx+ago]（环形回绕恰好 size 个、ago 生效）、reset 复位 idx、apply_factor 全 buffer（两种模式） |

## 已知坑

- cdef 方法只能在本目录的 Cython 测试模块里类型化调用（agents.md §2.3）。
- `Position/Order/Account` 的 `experiment_id` 必须是 16 字节 uuid。
- QBuffer 环形请求窗口超出 maxlen 属调用方契约违规（旧值已覆写），测试只锁有效窗口。
