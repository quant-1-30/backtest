# cython: language_level=3
# distutils: language = c++

import pickle
import uuid

from libcpp.vector cimport vector
from libc.stdint cimport int32_t, int64_t

from bt_core.execution.core.finance.position cimport Position
from bt_core.execution.core.finance.asset cimport Asset
from bt_core.execution.core.finance.order cimport Order, OrderStatus
from bt_core.execution.core.finance.trade cimport OrderExecutionBit
from bt_core.execution.core.finance.account cimport Account
from bt_core.execution.core.finance.common cimport AdjustmentData, RightData, EventItem
from bt_core.utils.dateintern cimport ts2intdt


EID = uuid.uuid4().bytes     # Position/Order/Account 要求 16 字节 uuid
OID = uuid.uuid4().bytes

# 北京时间 2024-06-03 10:00 / 10:30 / 15:00 的 unix ts（日内工作表示）
TS_MORNING = 1717380000
TS_NOON = 1717398000
TS_CLOSE = 1717417200


cdef Asset _asset(bytes sid, int32_t first_trading=0, int32_t delist=0,
                  bytes merger=b"", float ratio=0.0):
    return Asset(sid, b"tst", first_trading, delist, merger, ratio)


cdef OrderExecutionBit _bit(bint isbuy, int64_t dt, int32_t size, double price,
                            double comm=0.0):
    return OrderExecutionBit(OID, dt, size, price, comm, isbuy)


def _assert_close(double got, double exp, str what, double tol=1e-9):
    assert abs(got - exp) <= tol * max(1.0, abs(exp)), \
        f"{what}: got {got!r} != {exp!r}"


# =====================================================================
# T + 1
# =====================================================================

def test_t1_lock_and_unlock():
    cdef Asset a = _asset(b"600519")
    cdef Position p = Position(EID, b"600519", a, 0, 0, 0, 0, 0.0)
    cdef OrderExecutionBit bit

    # 当日买入: available = 0（T+1 锁定）
    p.update(_bit(True, TS_MORNING, 100, 10.0))
    assert p.core.size == 100
    assert p.core.available == 0
    _assert_close(p.core.cost_basis, 10.0, "open cost")

    # 当日卖出 -> T+1 violation
    try:
        p.update(_bit(False, TS_NOON, 100, 10.5))
        raise AssertionError("same-day sell must raise T+1 violation")
    except ValueError:
        pass
    assert p.core.size == 100, "failed sell must not change size"

    # 日切换解锁后再卖
    p.on_dt_over(20240603, 10.2)
    assert p.core.available == 100
    p.update(_bit(False, TS_MORNING + 86400, 60, 10.8))
    assert p.core.size == 40
    assert p.core.available == 40

    # 清仓后 available 归零
    p.update(_bit(False, TS_CLOSE + 86400, 40, 11.0))
    assert p.core.size == 0
    assert p.core.available == 0


def test_t1_sell_over_available_raises():
    cdef Asset a = _asset(b"000001")
    cdef Position p = Position(EID, b"000001", a, 0, 0, 100, 100, 10.0)
    try:
        p.update(_bit(False, TS_MORNING, 101, 10.0))
        raise AssertionError("sell > available must raise")
    except ValueError:
        pass


def test_intraday_dt_stays_ts_and_settles_to_ymd():
    cdef Asset a = _asset(b"600519")
    cdef Position p = Position(EID, b"600519", a, 0, 0, 0, 0, 0.0)

    # 同日多笔成交必须保持原始 ts 时序（agents.md §3）
    p.update(_bit(True, TS_MORNING, 100, 10.0))
    p.update(_bit(True, TS_NOON, 100, 12.0))
    assert p.core.datetime == TS_NOON, "intraday datetime must stay unix ts"

    p.on_dt_over(20240603, 12.0)
    assert p.core.datetime == 20240603, "settlement must normalize to ymd"


def test_short_position_rejected():
    cdef Asset a = _asset(b"600519")
    cdef Position p = Position(EID, b"600519", a, 0, 0, 0, 0, 0.0)
    try:
        p.update(_bit(False, TS_MORNING, 100, 10.0))
        raise AssertionError("sell from empty must raise (short unsupported)")
    except ValueError:
        pass


# =====================================================================
# 成本加权 / 已实现盈亏
# =====================================================================

def test_cost_averaging_realized_pnl_ratio():
    cdef Asset a = _asset(b"600519")
    cdef Position p = Position(EID, b"600519", a, 0, 0, 0, 0, 0.0)

    p.update(_bit(True, TS_MORNING, 100, 10.0))
    p.on_dt_over(20240603, 10.0)
    p.update(_bit(True, TS_MORNING + 86400, 100, 12.0))
    _assert_close(p.core.cost_basis, 11.0, "weighted cost")
    assert p.core.available == 100, "T+1: 当日新买仍锁定"

    p.on_dt_over(20240604, 11.5)
    assert p.core.available == 200
    p.update(_bit(False, TS_MORNING + 2 * 86400, 150, 11.5))
    assert p.core.size == 50 and p.core.available == 50
    _assert_close(p.core.realized_pnl, 150 * (11.5 - 11.0), "realized pnl")
    _assert_close(p.core.pnl, 50 * (11.5 - 11.0), "unrealized pnl")
    _assert_close(p.core.pnl_ratio,
                  (p.core.pnl + p.core.realized_pnl) / (50 * 11.0), "pnl_ratio")


# =====================================================================
# 分红送转 / 配股（期望值 = DB 反向验证口径）
# =====================================================================

cdef EventItem _adj(double bonus_share, double transfer, double bonus):
    cdef EventItem item
    item.event_type = 0
    item.adj = AdjustmentData(bonus_share, transfer, bonus)
    return item


cdef EventItem _rgt(double ratio, double price):
    cdef EventItem item
    item.event_type = 1
    item.rgt = RightData(ratio, price)
    return item


def test_event_bonus_transfer_10_2_1_5():
    # 10 送 2 转 1 派 5: size 100@10 -> 130 股 / 现金 +50 / 成本 7.6923
    cdef Asset a = _asset(b"600519")
    cdef Position p = Position(EID, b"600519", a, 0, 0, 100, 100, 10.0)
    cdef vector[EventItem] events
    events.push_back(_adj(2.0, 1.0, 5.0))

    cdef double cash = p.process_events(events, 1e18)
    assert p.core.size == 130
    assert p.core.available == 130
    _assert_close(cash, 50.0, "bonus cash = size * bonus/10")
    _assert_close(p.core.cost_basis, 10.0 / 1.3, "cost / sizer_ratio")


def test_event_rights_10_3_at_8():
    # 10 配 3 配股价 8: size 100@10 -> 130 股 / 现金 -240 / 成本 9.5385；配股部分 T+1
    cdef Asset a = _asset(b"600519")
    cdef Position p = Position(EID, b"600519", a, 0, 0, 100, 100, 10.0)
    cdef vector[EventItem] events
    events.push_back(_rgt(3.0, 8.0))

    cdef double cash = p.process_events(events, 1e18)
    assert p.core.size == 130
    _assert_close(cash, -240.0, "rights cash = -rights_size * price")
    _assert_close(p.core.cost_basis, (10.0 * 100 + 8.0 * 30) / 130.0, "weighted")
    assert p.core.available == 100, "right shares T+1: available unchanged"


def test_rights_abandoned_when_cash_insufficient():
    # 10配3@8 需缴 240, 现金不足 -> 整体放弃: 不增股不缴款, 成本不动
    cdef Asset a = _asset(b"600519")
    cdef Position p = Position(EID, b"600519", a, 0, 0, 100, 100, 10.0)
    cdef vector[EventItem] events
    events.push_back(_rgt(3.0, 8.0))

    cdef double cash = p.process_events(events, 239.99)
    assert p.core.size == 100, "insufficient cash must abandon rights"
    assert p.core.available == 100
    _assert_close(cash, 0.0, "abandoned rights pays nothing")
    _assert_close(p.core.cost_basis, 10.0, "abandoned rights keeps cost")


def test_rights_boundary_cash_exactly_enough():
    # 缴款恰等于现金: 可认购(严格大于才放弃)
    cdef Asset a = _asset(b"600519")
    cdef Position p = Position(EID, b"600519", a, 0, 0, 100, 100, 10.0)
    cdef vector[EventItem] events
    events.push_back(_rgt(3.0, 8.0))

    cdef double cash = p.process_events(events, 240.0)
    assert p.core.size == 130
    _assert_close(cash, -240.0, "exact cash still subscribes")


def test_batch_dividend_funds_rights_same_settlement():
    # 同批事件: 分红先到账滚动计入现金, 紧随其后的配股可据此认购
    cdef Asset a = _asset(b"600519")
    cdef Position p = Position(EID, b"600519", a, 0, 0, 100, 100, 10.0)
    cdef vector[EventItem] events
    events.push_back(_adj(0.0, 0.0, 24.0))   # 派 24/10 -> +240
    events.push_back(_rgt(3.0, 8.0))         # 需缴 240

    cdef double cash = p.process_events(events, 0.0)
    assert p.core.size == 130, "dividend in batch funds the rights"
    _assert_close(cash, 0.0, "net cash flow = +240 - 240")


def test_batch_rights_abandoned_then_later_dividend_still_paid():
    # 配股被放弃后, 同批后续分红仍正常入账(顺序处理互不影响)
    cdef Asset a = _asset(b"600519")
    cdef Position p = Position(EID, b"600519", a, 0, 0, 100, 100, 10.0)
    cdef vector[EventItem] events
    events.push_back(_rgt(3.0, 8.0))         # 需缴 240, 现金 0 -> 放弃
    events.push_back(_adj(0.0, 0.0, 5.0))    # 派 5/10 -> +50

    cdef double cash = p.process_events(events, 0.0)
    assert p.core.size == 100, "rights abandoned"
    _assert_close(cash, 50.0, "later dividend still paid")


def test_event_odd_share_floor():
    # 零头不足 1 股直接截断: 105 股 10 送 2 转 1 -> 136 = floor(136.5)
    cdef Asset a = _asset(b"600519")
    cdef Position p = Position(EID, b"600519", a, 0, 0, 105, 105, 10.0)
    cdef vector[EventItem] events
    events.push_back(_adj(2.0, 1.0, 0.0))
    p.process_events(events, 1e18)
    assert p.core.size == 136, f"expect floor(105*1.3)=136, got {p.core.size}"

    # 105 股 10 配 3 -> 31 = floor(31.5)
    cdef Position p2 = Position(EID, b"600519", a, 0, 0, 105, 105, 10.0)
    cdef vector[EventItem] events2
    events2.push_back(_rgt(3.0, 8.0))
    p2.process_events(events2, 1e18)
    assert p2.core.size == 105 + 31, f"expect rights floor(31.5)=31, got {p2.core.size}"


def test_event_available_le_size_after_ratio():
    # 截断规则同时作用于 available，保证 available <= size 恒成立
    cdef Asset a = _asset(b"600519")
    cdef Position p = Position(EID, b"600519", a, 0, 0, 105, 60, 10.0)
    cdef vector[EventItem] events
    events.push_back(_adj(2.0, 1.0, 0.0))
    p.process_events(events, 1e18)
    assert p.core.size == 136
    assert p.core.available == 78, f"expect floor(60*1.3)=78, got {p.core.available}"
    assert p.core.available <= p.core.size


def test_event_float_semantics_5700_14():
    # 浮点语义: floor(5700 * fl(1.4)) = 7979 而非 7980（DB 实测口径）
    cdef Asset a = _asset(b"600519")
    cdef Position p = Position(EID, b"600519", a, 0, 0, 5700, 5700, 10.0)
    cdef vector[EventItem] events
    events.push_back(_adj(2.0, 2.0, 0.0))  # 10送2转2 => sizer_ratio 1.4
    p.process_events(events, 1e18)
    assert p.core.size == 7979, f"expect 7979, got {p.core.size}"


# =====================================================================
# 退市 / 吸并 / 停牌
# =====================================================================

def test_delist_without_merger_is_full_loss():
    cdef Asset a = _asset(b"600519", 0, 20240605)
    cdef Position p = Position(EID, b"600519", a, 0, 0, 100, 100, 10.0)
    p.on_dt_over(20240606, 5.0)
    assert p.core.size == 0
    _assert_close(p.core.realized_pnl, -100.0 * 10.0, "full loss booked")


def test_merger_converts_sid_and_size():
    cdef Asset a = _asset(b"600519", 0, 20240605, b"600000", 0.5)
    cdef Position p = Position(EID, b"600519", a, 0, 0, 100, 100, 10.0)
    p.on_dt_over(20240606, 8.0)
    assert p.core.sid == b"600000"
    assert p.core.size == 50
    assert p.core.available == 50
    _assert_close(p.core.cost_basis, 10.0 * 100 / 50, "amount preserved")


def test_suspend_close_zero_keeps_state():
    cdef Asset a = _asset(b"600519")
    cdef Position p = Position(EID, b"600519", a, 0, 0, 100, 0, 10.0)
    cdef double pnl_before = p.core.pnl
    p.on_dt_over(20240606, 0.0)  # 停牌: 无 close
    assert p.core.size == 100, "suspension must not touch size"
    assert p.core.pnl == pnl_before
    assert p.core.datetime == 20240606, "clock still advances with bar"


# =====================================================================
# 订单状态机（按累计成交量判定）
# =====================================================================

cdef Order _order():
    return Order(EID, b"600519", OID, 1.0, 10.0, 0, 0, TS_MORNING, b"default")


def test_order_multifill_judged_on_cumulative():
    cdef Order o = _order()
    o.execute(300, _bit(True, TS_MORNING, 100, 10.0), 10.0)
    assert o.status == OrderStatus.Partial, "1/300 -> Partial"
    o.execute(300, _bit(True, TS_NOON, 100, 10.1), 10.0)
    assert o.status == OrderStatus.Partial, "200/300 -> Partial (not single-bit)"
    o.execute(300, _bit(True, TS_CLOSE, 100, 10.2), 10.0)
    assert o.status == OrderStatus.Completed, "300/300 -> Completed"
    assert o.core.size == 300
    assert len(o._exbits) == 3


def test_order_single_fill_full_size_completes():
    cdef Order o = _order()
    o.execute(300, _bit(True, TS_MORNING, 300, 10.0), 10.0)
    assert o.status == OrderStatus.Completed


def test_order_exchange_routing():
    cdef Order o_sh = Order(EID, b"600519", OID, 1.0, 10.0, 0, 0, TS_MORNING, b"default")
    cdef Order o_sz = Order(EID, b"300308", OID, 1.0, 10.0, 0, 0, TS_MORNING, b"default")
    assert o_sh.exchange == 0, "60xxxx -> SSE"
    assert o_sz.exchange == 1, "30xxxx -> SZSE"


# =====================================================================
# pickle 往返（writer/sink 依赖 __reduce__ 参数序）
# =====================================================================

def test_pickle_roundtrips():
    cdef Order o = _order()
    o.execute(200, _bit(True, TS_MORNING, 100, 10.0), 10.0)
    cdef Order o2 = pickle.loads(pickle.dumps(o))
    assert o2.core.sid == b"600519"
    assert o2.core.created_dt == TS_MORNING
    assert o2.filler == b"default"
    assert o2.core.order_id == o.core.order_id

    cdef Asset a = _asset(b"600519", 20240101)
    cdef Position p = Position(EID, b"600519", a, 0, 0, 130, 130, 7.69)
    cdef Position p2 = pickle.loads(pickle.dumps(p))
    assert p2.core.size == 130 and p2.core.available == 130
    _assert_close(p2.core.cost_basis, 7.69, "position cost survives pickle")

    cdef OrderExecutionBit bit = _bit(False, TS_CLOSE, 50, 11.0, 5.5)
    cdef OrderExecutionBit bit2 = pickle.loads(pickle.dumps(bit))
    assert bit2.core.executed_size == 50 and bit2.core.isbuy is False
    _assert_close(bit2.core.comm, 5.5, "bit comm survives pickle")

    cdef Asset a2 = pickle.loads(pickle.dumps(a))
    assert a2.core.first_trading == 20240101


# =====================================================================
# 涨跌幅（Asset.restricted，撮合层接线前的语义锁定）
# =====================================================================

def test_restricted_main_board_10pct():
    cdef Asset a = _asset(b"600519")
    _assert_close(a.restricted(TS_MORNING), 0.1, "main board 10%")
    cdef Asset bse = _asset(b"430047")
    _assert_close(bse.restricted(TS_MORNING), 0.1, "BSE fallback 10%")


def test_restricted_cyb_ckpt():
    # 创业板 2020-08-24 注册制改革: 之后 20%
    cdef Asset a = _asset(b"300308")
    _assert_close(a.restricted(_ts(2020, 8, 21)), 0.1, "CYB before 2020-08-24")
    _assert_close(a.restricted(_ts(2020, 8, 25)), 0.2, "CYB after 2020-08-24")


def test_restricted_new_stock_cross_month():
    # 新股豁免跨月: first_trading 2024-08-30, 第 3 个日历日 2024-09-02 仍豁免
    # （ymd 直接相减 = 72 会误判，必须用真实日历日差）
    cdef Asset star = _asset(b"688001", first_trading=20240830)
    _assert_close(star.restricted(_ts(2024, 9, 2)), 1.0, "STAR new stock day+3 no limit")

    cdef Asset cyb = _asset(b"300999", first_trading=20200824)
    _assert_close(cyb.restricted(_ts(2020, 8, 25)), 1.0, "CYB new stock no limit")
    _assert_close(cyb.restricted(_ts(2020, 9, 1)), 0.2, "CYB after 5 days 20%")
    _assert_close(star.restricted(_ts(2024, 9, 10)), 0.2, "STAR after 5 days 20%")
    # 豁免第 5 日边界（含第 5 日）
    _assert_close(star.restricted(_ts(2024, 9, 4)), 1.0, "day+5 still exempt")


def test_restricted_star_board():
    cdef Asset star = _asset(b"688001")
    _assert_close(star.restricted(TS_MORNING), 0.2, "STAR 20%")


cdef int64_t _ts(int y, int m, int d):
    # 北京时间 y-m-d 10:00 的 unix ts（与 ts2intdt 闭环验证）
    import datetime as _dt
    cdef double ts = _dt.datetime(y, m, d, 2, 0, tzinfo=_dt.timezone.utc).timestamp()
    return <int64_t>ts


# =====================================================================
# Account 现金流与 ymd 归一化
# =====================================================================

def test_account_update_cashflow_signs():
    cdef Account acct = Account(EID, 0, 0.0, 100000.0)
    cdef list trades = [
        _bit(True, TS_MORNING, 100, 10.0, 7.0),    # 买: -1000 - 7
        _bit(False, TS_NOON, 50, 11.0, 6.0),       # 卖: +550 - 6
    ]
    acct.update(trades)
    _assert_close(acct.core.cash, 100000.0 - 1000.0 - 7.0 + 550.0 - 6.0, "cash flow")
    assert acct.core.datetime == TS_NOON, "intraday dt = max executed_dt"


def test_account_add_cash_negative_forbidden():
    # 事件现金通道(add_cash)禁止把 cash 推到 0 以下
    cdef Account acct = Account(EID, 0, 0.0, 100.0)
    acct.add_cash(-50.0)
    _assert_close(acct.core.cash, 50.0, "lawful deduction")

    try:
        acct.add_cash(-60.0)
        raise AssertionError("add_cash below zero must raise")
    except ValueError:
        pass
    _assert_close(acct.core.cash, 50.0, "failed deduction must not apply")


def test_account_sync_normalizes_ts_and_guards_ymd():
    cdef Asset a = _asset(b"600519")
    cdef Position p = Position(EID, b"600519", a, 0, 0, 100, 100, 10.0)
    p.on_dt_over(20240603, 0.0)  # position 侧已归一 ymd

    # tick 为 ts: max(ts, ymd) -> ts 主导 -> 单次转换
    cdef Account acct = Account(EID, TS_MORNING, 0.0, 1000.0)
    acct.sync(TS_CLOSE, {b"k": p}, {b"600519": 12.0})
    assert acct.core.datetime == ts2intdt(<double>TS_CLOSE), "ts input -> ymd"
    _assert_close(acct.core.portfolio_value, 100 * 12.0, "mark to close")

    # 全 ymd 输入: 量级守卫防止二次转换（ts2intdt(20240603) -> 1970...）
    cdef Account acct2 = Account(EID, 20240603, 0.0, 1000.0)
    acct2.sync(0, {b"k": p}, {b"600519": 12.0})
    assert acct2.core.datetime == 20240603, "ymd input must stay ymd"
