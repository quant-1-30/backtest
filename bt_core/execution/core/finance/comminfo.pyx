# cython.boundscheck(False) # 关闭边界检查
# cython.wraparound(False)  # 关闭负指数索引检查
# distutils: language = c++

import numpy as np

cimport numpy as cnp
cnp.import_array() # initialzie numpy C-API

from bt_core.execution.core.finance.common cimport Exchange
from bt_core.execution.core.finance.order cimport OrderCoreData
from bt_core.execution.core.finance.position cimport PositionCoreData

cdef const int64_t RatioCkpt = 1433813400 # 2015年 万分之5


cdef class CommInfoBase:

    def __init__(self, 
                 double commission = 0.0,
                 double interest = 0.0,
                 double fixed = 0.0,
                 int32_t commtype = 0):

        self.commission = commission / 100.0
        self.creditrate = interest / 365.0
        self.commtype = commtype
        self.fixed = fixed

    cdef double get_credit_interest(self, Position pobj, int64_t dt):
        cdef PositionCoreData core = pobj.core

        cdef long days = (dt - core.datetime) // 86400
        if days <= 0:
            return 0.0
        return days * self.creditrate * abs(core.size) * core.price

    cdef double calculate(self, Order order):
        return self.commission

    cdef double getcommission(self, Order order, int32_t size, double price):
        cdef double comm_rate = self.calculate(order)
        cdef double comm 
        
        comm = abs(size) * comm_rate

        if self.commtype == CommType.COMM_PERC:
            comm = abs(size) * comm_rate * price
        return comm
    
    def __call__(self, Order order, int32_t size, double price):
        '''Calculates the commission of an operation at a given price'''
        return self.getcommission(order, size, price)
    
    cdef double get_comm_rate(self, Order order):
        return self.calculate(order)


cdef class CommInfo_Stocks(CommInfoBase):

    cdef double calculate(self, Order order):
        """
            # 印花税 1‰(卖的时候才收取 全国统一)
            # 过户费：深圳交易所无 / 上海交易所万分之1 买卖
            # 交易佣金:最高收费为3‰ / 2015 5/10000
        """
        cdef bint is_buy = order.isbuy
        cdef OrderCoreData core = order.core

        stamp_commission = 0 if is_buy else 1e-3
        transfer_commission = 1e-4 if order.exchange == Exchange.SSE else 0
        trade_commission = 3e-3 if core.created_dt < RatioCkpt else 5e-4

        comm = stamp_commission + transfer_commission + trade_commission
        return comm

    cdef double getcommission(self, Order order, int32_t size, double price):
        cdef double comm_rate, comm

        comm_rate = self.calculate(order)
        comm = abs(size) * comm_rate * price
        comm = comm if comm >5.0 else 5.0
        return comm


cdef class CommInfo_Futures(CommInfoBase):
    
    cdef double calculate(self, Order order):
        return self.commission

    cdef double getcommission(self, Order order, int32_t size, double price):
        cdef double comm = size * self.fixed 
        return comm