# !/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Tue Mar 12 15:37:47 2019

@author: python
"""
import pandas as pd
from contextlib import ExitStack
from util.api_support import AlgoAPI
from trade import (
    SESSION_START,
    SESSION_END,
    BEFORE_TRADING_START
)


class AlgorithmSimulator(object):
    """
        initialization a.初始化相关模块以及参数
        before trading:
                        a. 针对ledger预处理
                        b. pipelineEngine计算的结果
                        c. 9:25(撮合价格) --- cancelPolicy过滤pipelineEngine的标的集合 -- 筛选可行域
                        d. 实施具体的执行计划(非bid_mechnasim --- 创建订单）
        session start:
                        a.基于可行域 -- 调用blotter模块(orders --- transactions)
                        b.transactions --- update ledger
        session end:
                        a.调用metrics_tracker --- generate metrics_perf
        simulation_end:
                        a. plot and generate pdf
    """
    EMISSION_TO_PERF_KEY_MAP = {
        'minute': 'minute_perf',
        'daily': 'daily_perf'
    }

    def __init__(self,
                 algorithm,
                 clock):

        self.algorithm = algorithm
        self.clock = clock

    def transform(self):
        """
        Main generator work loop.
        """
        ledger = self.algorithm.ledger
        broker = self.algorithm.broker
        metrics_tracker = self.algorithm.tracker

        def once_a_day(dts):
            dts = dts.strftime('%Y-%m-%d') if isinstance(dts, pd.Timestamp) else dts
            broker.implement_broke(ledger, dts)

        def on_exit():
            # Remove references to algo, data portal, et al to break cycles
            # and ensure deterministic cleanup of these objects
            self.algorithm = None

        with ExitStack() as stack:
            """
            由于已注册的回调是按注册的相反顺序调用的, 因此最终行为就好像with 已将多个嵌套语句与已注册的一组回调一起使用。
            这甚至扩展到异常处理-如果内部回调抑制或替换异常，则外部回调将基于该更新状态传递自变量。
            enter_context  输入一个新的上下文管理器, 并将其__exit__()方法添加到回调堆栈中。返回值是上下文管理器自己的__enter__()方法的结果。
            callback(回调, * args, ** kwds)接受任意的回调函数和参数, 并将其添加到回调堆栈中。
            """
            stack.callback(on_exit)
            stack.enter_context(AlgoAPI(self.algorithm))
            # print('ledger', ledger)

            metrics_tracker.handle_start_of_simulation(ledger)

            # 生成器yield方法 ，返回yield 生成的数据，next 执行yield 之后的方法
            for session_label, action in self.clock:
                print('session_label and action :', session_label, action)
                if action == BEFORE_TRADING_START:
                    metrics_tracker.handle_market_open(session_label, ledger)
                elif action == SESSION_START:
                    once_a_day(session_label)
                elif action == SESSION_END:
                    # Get a perf message for the given datetime.
                    yield metrics_tracker.handle_market_close(session_label, ledger)

            yield metrics_tracker.handle_simulation_end(ledger)

