from libc.stdint cimport int32_t, int64_t
from libcpp.vector cimport vector
from libcpp.string cimport string as cpp_string
from libcpp.unordered_map cimport unordered_map

from bt_core.sizer cimport Sizer


cdef struct TraderPlan:
    cpp_string sid # char sid[32]   
    double weight    
    int32_t size      
    int32_t priority
    int32_t execType 


cdef class Pnc:
    cdef Sizer sizer

    cdef int32_t interval
    cdef double stake
    cdef double dd

    cdef vector[int32_t] v_trading_days
    cdef int32_t _last_trade_day
    
    cdef unordered_map[cpp_string, TraderPlan] pending_sells
    
    cpdef void _start(self, list days_list)

    cdef int32_t _get_days_held(self, int32_t created_day, int32_t current_day) noexcept nogil
    
    cpdef vector[TraderPlan] on_risk(self, object snapshot, dict stats, int32_t execType=?)
    
    cpdef unordered_map[cpp_string, vector[TraderPlan]] generate_plan(self, int32_t current_day, 
                                                                        dict topk_info, object snapshot, int32_t execType=?) 

    cpdef void on_execute(self, dict sell_trades)
