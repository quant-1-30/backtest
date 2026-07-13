# cython: language_level=3
# cython: boundscheck=False
# cython: wraparound=False

from libc.stdint cimport int32_t, int64_t
from libcpp.unordered_map cimport unordered_map
from libcpp.algorithm cimport lower_bound, sort as c_sort
from cython.operator cimport dereference as deref # C++ Map decref

# from libc.string cimport strncpy

from bt_core.sizer cimport Sizer


# ==============================================================================================
# C++ sort avoid by memory ptr
# ==============================================================================================
cdef inline bint compare_trader_plans(TraderPlan a, TraderPlan b) noexcept:
    return a.priority < b.priority


cdef class Pnc:
    """
        a. sell first 
        b. buy after 
    """
    def __init__(self, Sizer sizer_obj, *args, **kwargs):
        self.sizer = sizer_obj

        self.interval = kwargs.pop("days_held", 5)
        self.stake = kwargs.pop("stake", 0.9)
        self.dd = kwargs.pop("dd", 0.25)

        self._last_trade_day = 0

    # =============================================================================================
    # TradingDays from benchmarkret
    # =============================================================================================

    cpdef void _start(self, list trading_days):
        cdef int32_t d
        
        self.v_trading_days.clear()
        for d in trading_days:
            self.v_trading_days.push_back(d)

    cdef int32_t _get_days_held(self, int32_t created_day, int32_t current_day) noexcept nogil:
        if self.v_trading_days.empty():
            return 0
            
        cdef vector[int32_t].iterator it_created = lower_bound(self.v_trading_days.begin(), self.v_trading_days.end(), created_day)
        cdef vector[int32_t].iterator it_current = lower_bound(self.v_trading_days.begin(), self.v_trading_days.end(), current_day)
        
        return <int32_t>(it_current - it_created)

    # ==============================================================================================
    # risk control on tick
    # ==============================================================================================
    
    cpdef vector[TraderPlan] on_risk(self, object snapshot, dict stats, int32_t execType=0):
        cdef double pnl
        cdef TraderPlan tmp
        cdef double current_price
        cdef list positions = snapshot.positions
        
        # avid python heap
        cdef vector[TraderPlan] sells_by_risk
        
        # ===========================================================================================
        # 1. Macro Control --- Drawdown
        # ===========================================================================================
        if stats["drawdown"].maxdd >= self.dd:
            print("reach maxdd and execute sell all")
            for pos in positions:
                c_sid = <cpp_string>pos.sid

                if pos.available > 0:
                    
                    tmp.sid = c_sid
                    tmp.weight = 1.0
                    tmp.size = pos.available
                    tmp.priority = 0
                    tmp.execType = execType

                    sells_by_risk.push_back(tmp)

            return sells_by_risk

        # ============================================================================================
        # 2. Asset Risk Control Closed in Backtest and applied in Live 
        # ============================================================================================
        # for pos in positions:
        #     c_sid = <cpp_string>pos.sid
        #     
        #      if pos.available == 0 or self.pending_sells.find(c_sid) != self.pending_sells.end():
        #           continue

        #     # PnL
        #     current_price = current_prices.get(c_sid, pos.cost_basis) 
        #     pnl = current_price / pos.cost_basis 

        #     if pnl <= self.stake: 
        #         tmp.sid = c_sid
        #         tmp.weight = 1.0
        #         tmp.size = pos.available
        #         tmp.priority = 0
        #         tmp.execType = execType
        #         
        #         sells_by_risk.push_back(tmp) 
        #         self.pending_sells[py_sid] = tmp
                 
        return sells_by_risk

    # ================================================================================================
    # generate execution plan
    # ================================================================================================

    cpdef unordered_map[cpp_string, vector[TraderPlan]] generate_plan(self, int32_t current_day, dict topk_info, object snapshot, int32_t execType=0): 
            cdef bytes py_sid
            cdef cpp_string c_sid 

            cdef int32_t days_held, slots, buy_rank=10
            cdef double wgt_ratio
            cdef TraderPlan tmp

            cdef vector[TraderPlan] sells
            cdef vector[TraderPlan] buys
            cdef unordered_map[cpp_string, vector[TraderPlan]] plan

            cdef unordered_map[cpp_string, double] s_wgt, b_wgt
            cdef unordered_map[cpp_string, double].iterator it_wgt
            cdef unordered_map[cpp_string, double] c_topk_info = topk_info
           
            cdef list positions = snapshot.positions
            cdef object pos, account = snapshot.account 

            cdef int32_t active_positions_count = 0
            cdef bint already_held
            cdef bint should_sell
                
            if self._last_trade_day == current_day or c_topk_info.empty():  
                plan[<cpp_string>b"sell"] = sells
                plan[<cpp_string>b"buy"] = buys
                return plan
           
            if self.sizer is not None:
                s_wgt = self.sizer.getsizing(topk_info, snapshot, False)

            for pos in positions:
                c_sid = <cpp_string>pos.sid

                if pos.available == 0 or self.pending_sells.find(c_sid) != self.pending_sells.end():
                    continue

                # ===============================================================================
                # 1. HoldingDays and Conflict
                # ===============================================================================
                days_held = self._get_days_held(<int32_t>pos.created_dt, current_day)
                
                if days_held < self.interval - 1:
                    continue

                should_sell = False
           
                # c_sid not topk and sell
                if c_topk_info.find(c_sid) == c_topk_info.end():
                    should_sell = True
                elif days_held >= self.interval - 1:
                    should_sell = True

                if not should_sell:
                    continue

                if c_topk_info.find(c_sid) != c_topk_info.end():
                    continue
           
                it_wgt = s_wgt.find(c_sid)
                wgt_ratio = deref(it_wgt).second if it_wgt != s_wgt.end() else 1.0

                # strncpy(tmp.sid, c_sid, 31) # tmp.sid[31] = b'\0' 
                tmp.sid = c_sid
                tmp.weight = wgt_ratio
                tmp.size = pos.available
                tmp.priority = 1
                tmp.execType = execType

                sells.push_back(tmp) 
                self.pending_sells[c_sid] = tmp  

            c_sort(sells.begin(), sells.end(), compare_trader_plans)

            # ===================================================================================
            # 2. Slot Control
            # ===================================================================================
            active_positions_count = 0
            for pos in positions:
                c_sid = <cpp_string>pos.sid
                    
                if self.pending_sells.find(c_sid) == self.pending_sells.end():
                    active_positions_count += 1
                    
            slots = <int32_t>c_topk_info.size() - active_positions_count

            # ===================================================================================
            # 3. Cash Control
            # ===================================================================================
            if account.cash <= 10000: 
                plan[<cpp_string>b"sell"] = sells
                plan[<cpp_string>b"buy"] = buys
                return plan
           
            # ===================================================================================
            # 4. Buy Control
            # ===================================================================================
            b_wgt = self.sizer.getsizing(c_topk_info, snapshot, True)

            for py_sid in topk_info: 
                c_sid = <cpp_string>py_sid
                buy_rank += 1

                if self.pending_sells.find(c_sid) != self.pending_sells.end():
                    continue

                already_held = False
                for pos in positions:
                    if <cpp_string>pos.sid == c_sid:
                        already_held = True
                        break

                if not already_held and slots <= 0:
                    continue

                it_wgt = b_wgt.find(c_sid)
                wgt_ratio = deref(it_wgt).second if it_wgt != b_wgt.end() else 0.0

                if wgt_ratio > 0:
                    # strncpy(tmp.sid, c_sid, 31) # tmp.sid[31] = b'\0'
                    tmp.sid = c_sid
                    tmp.weight = wgt_ratio
                    tmp.size = 0
                    tmp.priority = buy_rank
                    tmp.execType = execType

                    buys.push_back(tmp)

                    if not already_held:
                        slots -= 1
                
            c_sort(buys.begin(), buys.end(), compare_trader_plans)

            self._last_trade_day = current_day
           
            plan[<cpp_string>b"sell"] = sells
            plan[<cpp_string>b"buy"] = buys
            return plan

    cpdef void on_execute(self, dict mtrades): 
        cdef bytes py_sid
        cdef cpp_string c_sid
        cdef int32_t remain, executed_size
        cdef object trades
        cdef unordered_map[cpp_string, TraderPlan].iterator it_sell

        for py_sid, trades in mtrades.items():
            c_sid = <cpp_string>py_sid
            executed_size = sum([trade.executed_size for trade in trades])

            it_sell = self.pending_sells.find(c_sid)
            if it_sell != self.pending_sells.end():
                remain = deref(it_sell).second.size - executed_size
                if remain <= 0:
                    self.pending_sells.erase(it_sell) # zero_copy
                else:
                    self.pending_sells[c_sid].size = remain 
