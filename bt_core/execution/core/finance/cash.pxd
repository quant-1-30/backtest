from libc.stdint cimport int64_t
from bt_core.execution.core.finance.account cimport Account


cdef class SyncCashManager:
    cdef object acct

    cdef Account get_account(self, bytes experiment_id)
    
    cdef void set_cash(self, object event)

    cdef void add_cash(self, bytes experiment_id, double cash)
    
    cdef void update(self, bytes experiment_id, list trades)
    
    cdef void sync(self, bytes experiment_id, int64_t sync_tick, dict pobjs, dict closes)
    
    cdef remove_client(self, bytes experiment_id)