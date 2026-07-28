from libc.stdint cimport int64_t
from libcpp.string cimport string as cpp_string

from bt_core.execution.core.finance.common cimport CashData


cdef struct AccountCoreData:
    cpp_string experiment_id
    int64_t datetime
    double  portfolio_value
    double  cash
    double  pnl
    double  leverage
    double  margin
    

cdef class Account:
    cdef readonly AccountCoreData core
    cdef object cached_uuid

    cdef void set_cash(self, CashData body, bint reset=*)
    
    cdef void restore(self)

    cdef void add_cash(self, double cash)

    cdef void update(self, list trades, double pnl)
    
    cdef void sync(self, int64_t tick, dict pobjs, dict closes)
    
    cdef Account clone(self)
    
    cdef object serialize(self)
    
    cdef object to_schema(self)
    
    cdef AccountCoreData get_snapshot(self)