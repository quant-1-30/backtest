# cython.boundscheck(False) # 关闭边界检查
# cython.wraparound(False)  # 关闭负指数索引检查
# distutils: language = c++

import asyncio
import warnings

from libc.stdint cimport int64_t

from bt_core.execution.core.finance.common cimport CashData
from bt_core.execution.gateway.interface import async_gt
from bt_core.execution.gateway.interface cimport AsyncGateway

from bt_protocol._protocol import Event


cdef class SyncCashManager:
    
    def __init__(self):
        self.acct = {}

    async def _start(self, object experiment_id):
        cdef object row, body # resp
        cdef bytes eid
        cdef list cash_data
        
        cash_data = await async_gt.get_account(experiment_id)
        for row in cash_data: # row
            body = row.body
            eid = body.experiment_id

            self.acct[eid] = Account(experiment_id=eid,
                                     datetime=body.datetime,
                                     portfolio_value=body.portfolio_value,
                                     cash=body.cash,
                                     pnl=body.pnl,
                                     leverage=body.leverage,
                                     margin=body.margin)

    cdef Account get_account(self, bytes experiment_id):
        acct = self.acct.setdefault(experiment_id, Account(experiment_id=experiment_id))
        return acct

    cdef void set_cash(self, object event):  
        """set cash from start"""
        cdef CashData meta
        cdef Account acct 
        cdef bytes experiment_id = event.experiment_id
        cdef object body = event.body

        meta.cash = body.cash
        meta.session = body.session
        acct = self.get_account(experiment_id)
        acct.set_cash(meta)

    cdef void add_cash(self, bytes experiment_id, double cash):
        cdef Account acct

        acct = self.get_account(experiment_id)
        acct.add_cash(cash)

    cdef void update(self, bytes experiment_id, list trades):
        """update account with position"""
        cdef Account acct

        acct = self.get_account(experiment_id)
        acct.update(trades)

    cdef void sync(self, bytes experiment_id, int64_t sync_tick, dict pobjs, dict closes):
        """sync account on_dt_over"""
        cdef Account acct

        acct = self.get_account(experiment_id)
        acct.sync(sync_tick, pobjs, closes)

    cdef remove_client(self, bytes experiment_id):
        self.acct.pop(experiment_id, None)
