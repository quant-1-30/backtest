from libc.stdint cimport int32_t, int64_t
from libcpp.vector cimport vector
from libcpp.string cimport string as cpp_string
from libcpp.unordered_map cimport unordered_map
from libcpp.algorithm cimport sort as c_sort

from bt_core.sizer cimport Sizer


cdef struct TraderPlan:
    cpp_string sid # char sid[32]
    double weight
    int32_t size
    int32_t priority
    int32_t execType
    cpp_string filler


cdef struct Pair:
    cpp_string sid
    int32_t days_held
    int32_t in_topk
    int32_t size


cdef class Pnc:
    cdef Sizer sizer

    cdef int32_t interval
    cdef double stake
    cdef double dd
    cdef double stop_loss       # 个股止损阈值（pnl_ratio <= stop_loss 触发卖出）
    cdef int32_t max_positions  # 最大持仓数量控制

    cdef vector[int32_t] v_trading_days
    cdef int32_t _last_trade_day

    cdef unordered_map[cpp_string, TraderPlan] pending_sells
    
    cpdef void _start(self, list days_list)

    cdef int32_t _get_days_held(self, int32_t created_day, int32_t current_day) noexcept nogil
    
    cpdef vector[TraderPlan] on_risk(self, object snapshot, dict stats, int32_t current_day, int32_t execType=?, bytes filler=?)
    
    cpdef unordered_map[cpp_string, vector[TraderPlan]] generate_plan(self, int32_t current_day, dict topk_info, 
                                                                        object snapshot, int32_t execType=?, bytes filler=?) 

    cpdef void on_execute(self, dict sell_trades)
