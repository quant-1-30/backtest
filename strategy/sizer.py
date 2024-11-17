#!/usr/bin/env python3
# -*- coding: utf-8 -*-


class Sizer:

    def addsizer(self, sizercls, *args, **kwargs):
        '''Adds a ``Sizer`` class (and args) which is the default sizer for any
        strategy added to cerebro
        '''
        self.sizers[None] = (sizercls, args, kwargs)
        

class Turtle(Sizer):
    """
        a. 海龟算法 基于波动率测算持仓比例 --- 基于数据的波动性划分仓位 最小波动率对应最大仓位
    """

    def addsizer(self, sizercls, *args, **kwargs):
        return super().addsizer(sizercls, *args, **kwargs)  
    

class Kelly(Sizer):
    """
        b. 基于策略胜率 
    """

    def addsizer(self, sizercls, *args, **kwargs):
        return super().addsizer(sizercls, *args, **kwargs)
    
