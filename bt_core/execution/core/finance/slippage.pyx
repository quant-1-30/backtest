# cython.boundscheck(False) # 关闭边界检查
# cython.wraparound(False)  # 关闭负指数索引检查
# distutils: language = c++
# cython: language_level=3


cdef class Slippage:

    cdef double get_slip_price(self, double order_price, double open, double high, double low, double close, bint is_buy) noexcept nogil:
        return order_price
    

cdef class FixedPercSlip(Slippage):

    def __init__(self, 
                double slip_perc=0.005):
        self.slip_perc = slip_perc 

    cdef double get_slip_price(self, double order_price, double open, double high, double low, double close, bint is_buy) noexcept nogil:
        cdef double pslip

        if is_buy:
            pslip = order_price * (1 + self.slip_perc)
            pslip = pslip if pslip < high else high
            return pslip
        else:
            pslip = order_price * (1 - self.slip_perc)
            pslip = pslip if pslip > low else low
            return pslip


cdef class SmoothSlip(Slippage):
    
    def __init__(self, double slip_perc=0.005):
        self.slip_perc = slip_perc
        
    cdef double get_slip_price(self, double order_price, double b_open, double b_high, double b_low, double b_close, bint is_buy) noexcept nogil:
        cdef double smooth_price = (b_open + b_high + b_low + b_close) / 4.0, pslip

        if is_buy:
            pslip = smooth_price * (1.0 + self.slip_perc)
            return pslip if pslip < b_high else b_high
        else:
            pslip = smooth_price * (1.0 - self.slip_perc)
            return pslip if pslip > b_low else b_low


cdef class LikelihoodSlip(Slippage):
    """
    最悲观成交价滑点模型:
    买入按 bar 最高价成交 (追高代价)
    卖出按 bar 最低价成交 (砸盘代价)
    slip_perc 在此模型下仅作为是否触发极端价的阈值, 不再乘到价格上
    """

    def __init__(self, double slip_perc=0.005):
        self.slip_perc = slip_perc

    cdef double get_slip_price(self, double order_price, double b_open, double b_high, double b_low, double b_close, bint is_buy) noexcept nogil:
        cdef double bound
        if is_buy:
            # 买入: 为保证能成交, 按区间最高价; 但不超过 order_price*(1+slip_perc) 的容忍上限
            bound = order_price * (1.0 + self.slip_perc)
            return b_high if b_high < bound else bound
        else:
            # 卖出: 按区间最低价; 但不低于 order_price*(1-slip_perc) 的容忍下限
            bound = order_price * (1.0 - self.slip_perc)
            return b_low if b_low > bound else bound


_slip = {
    "default": FixedPercSlip,
    "smooth": SmoothSlip,
    "progressive": LikelihoodSlip,
}