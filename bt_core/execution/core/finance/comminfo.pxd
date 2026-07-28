from libc.stdint cimport int64_t, int32_t

from bt_core.execution.core.finance.position cimport Position
from bt_core.execution.core.finance.order cimport Order


cdef enum CommType:
    COMM_PERC = 0
    COMM_FIXED = 1


cdef class CommInfoBase:
    cdef double commission
    cdef double creditrate
    cdef int32_t commtype
    cdef double fixed
    
    cdef double calculate(self, Order order)

    cdef double getcommission(self, Order order, int32_t size, double price)

    cdef double get_credit_interest(self, Position pobj, int64_t dt)

    cdef double get_comm_rate(self, Order order)


cdef class CommInfo_Stocks(CommInfoBase):
    
    cdef double calculate(self, Order order)


cdef class CommInfo_Futures(CommInfoBase):

    cdef double calculate(self, Order order)