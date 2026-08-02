# distutils: language = c++
# cython: profile=False
# cython: language_level=3

import asyncio
import numpy as np
import polars as pl

from libc.math cimport floor

from bt_core.execution.gateway.interface import async_gt
from bt_core.utils.dateintern cimport ts2intdt
from bt_core.execution.core.finance.order cimport OrderCoreData, ExecType
from bt_core.execution.core.finance.position cimport Position
from bt_core.execution.core.finance.slippage import _slip
from bt_core.execution.core.finance.comminfo cimport CommInfo_Stocks, CommInfoBase
from bt_core.execution.core.finance.trade cimport OrderExecutionBit

from bt_protocol._protocol import QueryBody
from bt_protocol.constant import RpcTopic

cimport numpy as cnp
cnp.import_array()


# =====================================================================
# Module-level helpers
# =====================================================================

cdef inline int _round_to_lot(int32_t size, int32_t tick_size, bint is_last_sell):
    """buy_size = multiply tick size / sell_size allow fraction"""
    if is_last_sell:
        return size
    return (size // tick_size) * tick_size


cdef inline int calculate(Order order, Position p_obj, double cash, double price,
                          Slippage slip, CommInfoBase comm):
    cdef AssetCore info = order.info
    cdef OrderCoreData core = order.core
    cdef bint is_buy = order.isbuy

    cdef int32_t avail
    if not is_buy:
        avail = p_obj.get_available()
        if avail <= 0:
            return 0
        return <int32_t>(avail * core.sizer_ratio)

    # slip and comm intended for buy
    cdef double slip_price = slip.get_slip_price(price, price, price, price, price, True) # order_price + ohlc + direction
    cdef double comm_rate = comm.get_comm_rate(order)
    
    cdef double cost_per_lot = slip_price * info.tick_size * (1.0 + comm_rate)
    cdef double sizer_cash = core.sizer_ratio * cash

    if cost_per_lot > sizer_cash or cost_per_lot <= 0:
        return 0

    cdef double raw_lots = floor(sizer_cash / cost_per_lot)
    return <int32_t>(raw_lots * info.tick_size)


cdef inline double _execute_factor(double bar_high, double bar_low, double bar_close,
                                   double highest_so_far, double lowest_so_far, bint is_buy) noexcept nogil:
    cdef double rel_eps = 1e-5

    if bar_low <= 0 or lowest_so_far <= 0 or highest_so_far <= 0:
        return 0.0

    cdef double amplitude = (bar_high - bar_low) / bar_low

    if amplitude < rel_eps: # tick_amplitude
        if is_buy and (highest_so_far - bar_close) / highest_so_far < rel_eps: # period amplitude
            return 0.0 

        if not is_buy and (bar_close - lowest_so_far) / lowest_so_far < rel_eps:
            return 0.0 

    return 1.0


# =====================================================================
# PseudoFiller
# =====================================================================

cdef class PseudoFiller:

    def __init__(self, double impact=0.05, int32_t batch_size=1000,
                 double slip_perc=0.005, slip_name="default"):
        self.impact = impact
        self.batch_size = batch_size
        self.slip = _slip[slip_name](slip_perc=slip_perc)
        self.comm = CommInfo_Stocks()
        self._lines_cache = {}
        self._current_cache_dt = 0

    # ----------------------------------------------------
    # Tick Engine
    # ----------------------------------------------------
    cdef _preload(self, Order ord, object loop):
        cdef OrderCoreData core = ord.core
        cdef int32_t int_dt = ts2intdt(core.created_dt)
        cdef tuple cache_key = (core.sid, int_dt)

        if self._current_cache_dt != int_dt:
            self._lines_cache.clear()
            self._current_cache_dt = int_dt

        if cache_key in self._lines_cache:
            return self._lines_cache[cache_key]

        cdef Lines lines = Lines()
        cdef object request = QueryBody(int_dt, int_dt, [core.sid])

        async def loader(object req):
            cdef double[:, ::1] np_view
            cdef list cols = ["tick", "open", "high", "low", "close", "volume", "amount"]
            cdef object df, cast_df, np_arr
            try:
                data = await async_gt.rpc(req, RpcTopic.Tick)
                df = data[req.sid[0]]
                cast_df = df.select(pl.col(cols).cast(pl.Float64))
                np_arr = np.ascontiguousarray(cast_df.to_numpy())
                np_view = np_arr
                lines.batch_load(np_view)
            except GeneratorExit:
                pass
            except Exception as e:
                print(f"Loader Error: {e}")

        asyncio.run_coroutine_threadsafe(loader(request), loop).result(timeout=30)
        self._lines_cache[cache_key] = lines
        return lines

    # ----------------------------------------------------
    # Price helpers
    # ----------------------------------------------------
    cdef (int32_t, double) _find_limit_execution(self, int32_t loc, double limit_price,
                                                  bint is_buy, Lines lines):
        cdef int32_t n = len(lines)
        cdef int32_t i
        cdef double open_i
        for i in range(loc, n):
            if is_buy and lines.low[i] <= limit_price:
                open_i = lines.open[i]
                # 买入限价单: 开盘价已高于限价时当日不会成交, 需等价格回落到限价
                if open_i > limit_price:
                    return i, limit_price
                return i, open_i
            elif not is_buy and lines.high[i] >= limit_price:
                open_i = lines.open[i]
                # 卖出限价单: 开盘价已低于限价时当日不会成交, 需等价格回升到限价
                if open_i < limit_price:
                    return i, limit_price
                return i, open_i
        return -1, 0.0

    cdef double _get_exec_price(self, Order order, Lines lines, int32_t loc):
        cdef OrderCoreData core = order.core
        if core.exec_type == ExecType.Limit:
            return core.price
        elif core.exec_type == ExecType.Close:
            return lines.close[loc]
        else:
            return lines.open[loc]

    # ----------------------------------------------------
    # Core: execute single fill with cash guard
    # ----------------------------------------------------
    cdef (int32_t, double) _fill(self, Order order, int32_t total_size, int32_t exec_loc,
                                  int32_t req_size, bint is_buy, double order_price, Lines lines,
                                  double cash):
        cdef OrderCoreData core = order.core
        cdef AssetCore info = order.info
        cdef int32_t fill_size = req_size
        cdef double comm_rate, unit_cost
        cdef int32_t max_by_cash

        cdef double slip_price = self.slip.get_slip_price(
            order_price, lines.open[exec_loc], lines.high[exec_loc],
            lines.low[exec_loc], lines.close[exec_loc], is_buy)

        # cash insurance cost <= cash
        if is_buy:
            comm_rate = self.comm.get_comm_rate(order)
            unit_cost = slip_price * (1.0 + comm_rate)
            if unit_cost <= 0:
                return 0, 0.0
            max_by_cash = <int32_t>(cash / unit_cost)
            max_by_cash = (max_by_cash // info.tick_size) * info.tick_size
            if max_by_cash <= 0:
                return 0, 0.0
            if fill_size > max_by_cash:
                fill_size = max_by_cash

        cdef double comm = self.comm.getcommission(order, fill_size, slip_price)

        cdef OrderExecutionBit order_bit = OrderExecutionBit(
            order_id=core.order_id,
            executed_dt=lines.tick[exec_loc],
            executed_size=fill_size,
            executed_price=slip_price,
            comm=comm,
            isbuy=is_buy)

        order.execute(total_size, order_bit, order_price)
        return fill_size, slip_price * fill_size + comm

    # ----------------------------------------------------
    # Eager Execute
    # ----------------------------------------------------
    cdef void _execute(self, Order order, Position p_obj, double cash, Lines lines):
        cdef OrderCoreData core = order.core
        cdef AssetCore info = order.info
        cdef bint is_buy = order.isbuy
        cdef bint is_limit = (core.exec_type == ExecType.Limit)

        cdef int32_t start_loc = lines.get_loc(core.created_dt)
        cdef int32_t n = len(lines)

        # Causal Extrema
        cdef double loc_highest = lines.high[0]
        cdef double loc_lowest = lines.low[0]
        cdef int32_t i

        for i in range(1, start_loc):
            if lines.high[i] > loc_highest: loc_highest = lines.high[i]
            if lines.low[i] < loc_lowest: loc_lowest = lines.low[i]

        # exec_type price
        cdef double target_price = self._get_exec_price(order, lines, start_loc)
        
        # calculate buy size or sell size
        cdef int32_t total_size = core.size if core.size > 0 else calculate(order, p_obj, cash, target_price, self.slip, self.comm)
        cdef int32_t remains = total_size

        cdef int32_t exec_loc, filler_size, filled
        cdef double order_price, fill_factor, cost

        while remains > 0 and start_loc < n:
            if is_limit:
                exec_loc, order_price = self._find_limit_execution(
                    start_loc, target_price, is_buy, lines)
            else:
                exec_loc = start_loc
                order_price = self._get_exec_price(order, lines, exec_loc)

            if exec_loc < 0:
                break

            if lines.high[exec_loc] > loc_highest: loc_highest = lines.high[exec_loc]
            if lines.low[exec_loc] < loc_lowest: loc_lowest = lines.low[exec_loc]

            fill_factor = _execute_factor(
                lines.high[exec_loc], lines.low[exec_loc], lines.close[exec_loc], loc_highest, loc_lowest, is_buy)
            if fill_factor <= 0.0:
                start_loc += 1
                continue

            filler_size = min(remains, <int32_t>(lines.volume[exec_loc] * self.impact * fill_factor))
            if filler_size <= 0:
                start_loc = exec_loc + 1
                continue

            filler_size = _round_to_lot(filler_size, info.tick_size, exec_loc == n - 1 and not is_buy)
            if filler_size <= 0:
                start_loc = exec_loc + 1
                continue

            filled, cost = self._fill(order, total_size, exec_loc, filler_size, is_buy,
                                       order_price, lines, cash)
            if filled <= 0:
                break

            if is_buy:
                cash -= cost
            remains -= filled
            start_loc = exec_loc + 1

    def __call__(self, Order order, double cash, Position p_obj, object loop):
        try:
            lines = self._preload(order, loop)
            if len(lines) > 0:
                self._execute(order, p_obj, cash, lines)
        except Exception as e:
            print(f"Error during filler preload: {e}")


# =====================================================================
# AlgoFiller (VWAP / TWAP)
# =====================================================================

cdef class AlgoFiller(PseudoFiller):

    def __init__(self, bint is_vwap=True, double impact=0.05,
                 int32_t batch_size=1000, double slip_perc=0.005):
        super().__init__(impact=impact, batch_size=batch_size, slip_perc=slip_perc)
        self.is_vwap = is_vwap

    cdef void _execute(self, Order order, Position p_obj, double cash, Lines lines):
        cdef OrderCoreData core = order.core
        cdef AssetCore info = order.info
        cdef int32_t start_loc = lines.get_loc(core.created_dt)
        cdef int32_t n = len(lines)
        cdef int32_t total_bars = n - start_loc
        if total_bars <= 0:
            return

        # loc high / low 
        cdef double loc_highest = lines.high[0]
        cdef double loc_lowest = lines.low[0]
        cdef int32_t i

        for i in range(1, start_loc):
            if lines.high[i] > loc_highest: loc_highest = lines.high[i]
            if lines.low[i] < loc_lowest: loc_lowest = lines.low[i]

        cdef int32_t total_size = core.size if core.size > 0 else calculate(order, p_obj, cash, lines.open[start_loc], self.slip, self.comm)
        if total_size <= 0:
            return

        cdef bint is_buy = order.isbuy

        cdef int32_t remains = total_size
        cdef int32_t filled_so_far = 0
        cdef int32_t chunk_size, filled
        cdef double expected_ratio, target_fill, fill_factor, order_price, cost

        # VWAP ESTIMATE Sum Volume
        cdef double total_vol = 0.0
        cdef double accum_vol = 0.0
        cdef int32_t bars_passed = 0

        if self.is_vwap:
            for i in range(start_loc, n):
                total_vol += lines.volume[i]
            if total_vol <= 0:
                return

        for exec_loc in range(start_loc, n):
            if remains <= 0:
                break

            # Schedule ratio
            if self.is_vwap:
                accum_vol += lines.volume[exec_loc]
                expected_ratio = accum_vol / total_vol
            else:
                bars_passed += 1
                expected_ratio = <double>bars_passed / total_bars

            target_fill = (total_size * expected_ratio) - filled_so_far
            if target_fill <= 0:
                continue

            # update extreme value
            if lines.high[exec_loc] > loc_highest: loc_highest = lines.high[exec_loc]
            if lines.low[exec_loc] < loc_lowest: loc_lowest = lines.low[exec_loc]

            fill_factor = _execute_factor(
                lines.high[exec_loc], lines.low[exec_loc], lines.close[exec_loc], loc_highest, loc_lowest, is_buy)
            if fill_factor == 0.0:
                continue

            chunk_size = <int32_t>(target_fill * fill_factor)
            chunk_size = _round_to_lot(chunk_size, info.tick_size, exec_loc == n - 1 and not is_buy)
            if chunk_size > remains:
                chunk_size = remains
            if chunk_size <= 0:
                continue

            order_price = lines.close[exec_loc]
            filled, cost = self._fill(order, total_size, exec_loc, chunk_size, is_buy,
                                       order_price, lines, cash)
            if filled <= 0:
                break

            if is_buy:
                cash -= cost
            filled_so_far += filled
            remains -= filled


cdef class VWAPFiller(AlgoFiller):
    def __init__(self, **kwargs):
        super().__init__(is_vwap=True, **kwargs)


cdef class TWAPFiller(AlgoFiller):
    def __init__(self, **kwargs):
        super().__init__(is_vwap=False, **kwargs)


_fillers = {
    b"default": PseudoFiller(),
    b"vwap": VWAPFiller(),
    b"twap": TWAPFiller()
}