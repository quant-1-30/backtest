# cython: language_level=3
# cython: boundscheck=False
# cython: wraparound=False

from libc.stdint cimport int32_t, int64_t
from libcpp.unordered_map cimport unordered_map
from libcpp.algorithm cimport lower_bound, sort as c_sort
from cython.operator cimport dereference as deref # C++ Map decref

from libc.string cimport strncpy

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
    def __init__(self, Sizer sizer_obj, list trading_days, **kwargs):
        self.interval = kwargs.pop("days_held", 5)
        self.stake = kwargs.pop("stake", 0.9)
        self.dd = kwargs.pop("dd", 0.25)
        self.sizer = sizer_obj

        self._last_trade_day = 0
        self.pending_sells = {}

    
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
    
    cpdef vector[TraderPlan] on_risk(self, object snapshot, dict stats):
        cdef double pnl
        cdef TraderPlan tmp
        cdef bytes sid
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
                if pos.available > 0:
                    sells_by_risk.push_back(
                        TraderPlan(pos.sid, 1.0, False, pos.available, priority=0)
                    )
            return sells_by_risk

        # ============================================================================================
        # 2. Asset Risk Control Closed in Backtest and applied in Live 
        # ============================================================================================
        # for pos in positions:
        #     sid = pos.sid
        #     
        #     if pos.available == 0 or <bytes>sid.c_str() in self.pending_sells:
        #         continue

        #     # PnL
        #     current_price = current_prices.get(sid, pos.cost_basis) 
        #     pnl = current_price / pos.cost_basis 

        #     if pnl <= self.stake: 
        #         tmp = TraderPlan(sid, 1.0, False, pos.available, priority=0) 
        #         
        #         sells_by_risk.push_back(tmp) 
        #         self.pending_sells[sid] = tmp
                 
        return sells_by_risk

    # ================================================================================================
    # generate execution plan
    # ================================================================================================

    cpdef unordered_map[cpp_string, vector[TraderPlan]] generate_plan(self, int64_t current_day, dict topk_info, object snapshot): 
           cdef cpp_string sid
           cdef int32_t days_held, slots, buy_rank=0, buy_count = 0
           cdef double wgt_ratio
           cdef TraderPlan tmp

           cdef vector[TraderPlan] sells
           cdef vector[TraderPlan] buys
           cdef unordered_map[cpp_string, vector[TraderPlan]] plan

           cdef unordered_map[cpp_string, double] c_topk_info = topk_info 
           cdef unordered_map[cpp_string, double] s_wgt
           cdef unordered_map[cpp_string, double] b_wgt
           cdef unordered_map[cpp_string, double].iterator it_wgt
           
           cdef list positions = snapshot.positions
           cdef object account = snapshot.account 
           cdef object pos
           
           if self._last_trade_day == current_day or c_topk_info.empty():  
               plan[<cpp_string>b"sell"] = sells
               plan[<cpp_string>b"buy"] = buys
               return plan
           
           s_wgt = self.sizer.getsizing(c_topk_info, snapshot, False)

           for pos in positions:
               sid = pos.sid

               if pos.available == 0 or <bytes>sid.c_str() in self.pending_sells:
                   continue

               # ===============================================================================
               # 1. HoldingDays and Conflict
               # ===============================================================================
               days_held = self._get_days_held(<int32_t>pos.created_dt, current_day)
               
               if days_held < self.interval - 1:
                   continue
               
               if c_topk_info.find(sid) != c_topk_info.end():
                   continue
           
               it_wgt = s_wgt.find(sid)
               wgt_ratio = deref(it_wgt).second if it_wgt != s_wgt.end() else 1.0

               strncpy(tmp.sid, sid.c_str(), 31)
               tmp.sid[31] = b'\0' # 确保结尾有安全终止符
               tmp.weight = wgt_ratio
               tmp.isbuy = False
               tmp.size = pos.available
               tmp.priority = 1

               sells.push_back(tmp) 
               self.pending_sells[<bytes>sid.c_str()] = tmp  

           c_sort(sells.begin(), sells.end(), compare_trader_plans)

           # ===================================================================================
           # 2. Slot Control
           # ===================================================================================
           slots = c_topk_info.size() - (len(positions) - len(self.pending_sells))
           
           if slots <= 0:
               plan[<cpp_string>b"sell"] = sells
               plan[<cpp_string>b"buy"] = buys
               return plan

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
               sid = <bytes>py_sid
               buy_rank += 1

               if <bytes>sid.c_str() in self.pending_sells:
                   continue

               it_wgt = b_wgt.find(sid)
               wgt_ratio = deref(it_wgt).second if it_wgt != b_wgt.end() else 0.0

               if wgt_ratio > 0:
                    strncpy(tmp.sid, sid.c_str(), 31)
                    tmp.sid[31] = b'\0'
                    tmp.weight = wgt_ratio
                    tmp.isbuy = True
                    tmp.size = 0
                    tmp.priority = 10 + buy_rank

                    buys.push_back(tmp)
                    buy_count += 1
               
               if buy_count >= slots:
                   break

           c_sort(buys.begin(), buys.end(), compare_trader_plans)

           self._last_trade_day = current_day
           
           plan[<cpp_string>b"sell"] = sells
           plan[<cpp_string>b"buy"] = buys
           return plan

    cpdef void on_execute(self, dict mtrades): # TradeBody
        cdef bytes sid
        cdef int32_t remain
        cdef TraderPlan tp
        cdef object trade

        for sid, trades in mtrades.items():
            executed_size = sum([trade.executed_size for trade in trades])

            if sid in self.pending_sells:
                tp = self.pending_sells[sid]
                remain = tp.size - executed_size
                if remain <= 0:
                    del self.pending_sells[sid]
