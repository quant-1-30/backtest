# cython.boundscheck(False) # 关闭边界检查
# cython.wraparound(False)  # 关闭负指数索引检查
# distutils: language = c++

import numpy as np

cimport numpy as cnp
cnp.import_array() # initialzie numpy C-API

from bt_core.execution.core.finance.common cimport Exchange
from bt_core.execution.core.finance.order cimport OrderCoreData
from bt_core.execution.core.finance.position cimport PositionCoreData

# A 股手续费时间分界点 (unix 秒)
cdef const int64_t STAMP_TAX_CKPT = 1693180800     # 2023-08-28 印花税 1‰ -> 0.5‰
cdef const int64_t TRANSFER_FEE_CKPT = 1651180800  # 2022-04-29 过户费 0.02‰ -> 0.01‰
cdef const int64_t TRANSFER_FEE_UNIFY_CKPT = 1438387200  # 2015-08-01 沪深统一按成交金额 0.02‰ (此前沪市按面值 0.06‰, 深市免收)
cdef const int64_t RatioCkpt = 1433813400          # 2015 佣金 3‰ -> 0.5‰ (万分之5)


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
            A 股手续费分项费率 均基于 size*price 计算:
            # 印花税: 2023-08-28 前 1‰ 卖出, 之后 0.5‰ 卖出; 买入不收
            # 过户费: 2022-04-29 前上交所 0.01‰ 买入, 沪深双向 0.01‰ 买卖
            # 交易佣金: 2015 前3‰, 0.5‰
            注意: 5 元最低红线仅适用于佣金, 不适用于印花税/过户费
        """
        cdef bint is_buy = order.isbuy
        cdef OrderCoreData core = order.core

        # 1. 印花税
        cdef double stamp_commission = 0.0
        if not is_buy:
            stamp_commission = 5e-4 if core.created_dt >= STAMP_TAX_CKPT else 1e-3

        # 2. 过户费 (0.01‰ = 1e-5, 旧费率 0.02‰ = 2e-5)
        cdef double transfer_commission
        if core.created_dt >= TRANSFER_FEE_CKPT:
            transfer_commission = 1e-5  # 沪深双向
        elif core.created_dt >= TRANSFER_FEE_UNIFY_CKPT:
            transfer_commission = 2e-5  # 2015-08-01 起沪深统一 0.02‰
        else:
            transfer_commission = 6e-5 if order.exchange == Exchange.SSE else 0.0

        # 3. 交易佣金
        cdef double trade_commission = 3e-3 if core.created_dt < RatioCkpt else 5e-4

        return stamp_commission + transfer_commission + trade_commission

    cdef double getcommission(self, Order order, int32_t size, double price):
        """
            A 股手续费 = 印花税 + 过户费 + 佣金
            其中佣金有 5 元最低红线, 印花税/过户费无最低门槛
        """
        cdef bint is_buy = order.isbuy
        cdef OrderCoreData core = order.core
        cdef double trade_value = abs(size) * price
        cdef double stamp_tax = 0.0
        cdef double stamp_rate
        cdef double transfer_fee
        cdef double comm_rate
        cdef double commission

        # 1. 印花税 (仅卖出, 无最低门槛)
        if not is_buy:
            stamp_rate = 5e-4 if core.created_dt >= STAMP_TAX_CKPT else 1e-3
            stamp_tax = trade_value * stamp_rate

        # 2. 过户费 (双向, 无最低门槛; 0.01‰ = 1e-5, 旧费率 0.02‰ = 2e-5)
        if core.created_dt >= TRANSFER_FEE_CKPT:
            transfer_fee = trade_value * 1e-5
        elif core.created_dt >= TRANSFER_FEE_UNIFY_CKPT:
            transfer_fee = trade_value * 2e-5
        else:
            transfer_fee = trade_value * 6e-5 if order.exchange == Exchange.SSE else 0.0

        # 3. 交易佣金 (5 元最低红线仅适用于佣金)
        comm_rate = 3e-3 if core.created_dt < RatioCkpt else 5e-4
        commission = trade_value * comm_rate
        if commission < 5.0:
            commission = 5.0

        return stamp_tax + transfer_fee + commission


cdef class CommInfo_Futures(CommInfoBase):
    
    cdef double calculate(self, Order order):
        return self.commission

    cdef double getcommission(self, Order order, int32_t size, double price):
        cdef double comm = size * self.fixed 
        return comm