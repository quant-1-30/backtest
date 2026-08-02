#!/usr/bin/env python
# -*- coding: utf-8; py-indent-offset:4 -*-
###############################################################################
#
# Copyright (C) 2015-2023 Daniel Rodriguez
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
###############################################################################
import os
import numpy as np
import pyarrow as pa
from typing import Union, List, Mapping, Any, Generator, Tuple, Dict

from bt_core.store import Store
from bt_core.execution.trade_api import TdApi, SubTopic
from bt_core.execution.core.finance.order import OrderType, ExecType
from bt_core.execution.actor.writer_actor import BatchWriterActor

from bt_sdk.ctx import initialize_runner, get_md_api
from bt_protocol._protocol import *


__all__ = ["BTStore"]


class LocalStore(Store):
    '''Singleton class wrapping to control the connections.

    Params:

      - ``token`` (default:``None``): API access token

      - ``account`` (default: ``None``): account id

      - ``practice`` (default: ``False``): use the test environment

      - ``account_tmout`` (default: ``10.0``): refresh period for account
        value/cash refresh
    '''
    # autoregister when metaclass init 
    BrokerCls = None  
    DataCls = None  

    params = (
        ("client_id", b""),
    )

    def __init__(self): 
        mdapi = get_md_api()

        q_size = int(os.getenv("QSize")) 
        batch_size = int(os.getenv("BatchSize"))
        buffer_size = int(os.getenv("BufferSize"))
        actor = BatchWriterActor(q_size=q_size, batch_size=batch_size) 
        tdapi = TdApi(client_id=self.p.client_id, q_size=q_size, buffer_size=buffer_size, actor=actor)

        self.actor = actor
        self._feed = self.DataCls(mdapi=mdapi, timeout=self.p.timeout) 
        self.broker = self.BrokerCls(tdapi=tdapi)
        self._runner = initialize_runner()

    def start(self, *args, **kwargs):
        self._runner.start() # new_event_loop
        _loop = self._runner.get_loop()

        self._feed._prepare(_loop)
        self.broker._prepare(_loop)
        
        # backgroud running writer actor
        _loop.create_task(self.actor.run())

    def setenvironment(self, env):
        '''Receives an environment (cerebro) and passes it over to the store it
        belongs to'''
        super(BTStore, self).setenvironment(env)

    def get_feed(self):
        '''Returns a feed with the given parameters'''
        return self._feed

# ------------------------------------------------------------------- data api ---------------------------------------------------------------------

    def get_instrument(self) -> Dict[str, Any]:
        '''Returns the assets data'''
        return self._feed.instrument
    
    def get_benchmark_dret(self) -> pa.Table:
        table = self._feed.benchmark_dret
        return table
 
# ------------------------------------------------------------------- broker api --------------------------------------------------------------------

    def register(self, strategy: str, cerebro_info: str) -> bytes:
        body = RegisterBody(strategy=strategy, extra_info=f"Strategy: {cerebro_info}", client_id=self.p.client_id)
        resp = self.broker.register(body)
        return resp
    
    def set_cash(self, experiment_id: bytes, session: int, cash: float) -> SnapshotBody:
        body = CashBody(cash=cash, session=session)
        resp = self.broker.set_cash(experiment_id, body)
        return resp
    
    def submit(self, experiment_id: bytes, body: OrderBody) -> SnapshotBody:
        resp = self.broker.submit(experiment_id, body)
        return resp
    
    def on_dt_over(self, experiment_id: bytes, last_dts: int, dts: int) -> SnapshotBody:
        resp = self.broker.on_dt_over(experiment_id, last_dts, dts)
        return resp
    
    def subscribe(self, topic: int, experiment_id: bytes, body: QueryBody) -> List[Union[AccountBody, PositionBody]]:
        return self.broker.subscribe(topic, experiment_id, body)

# ------------------------------------------------------------------- snapshot api --------------------------------------------------------------------

    def get_snapshot(self, experiment_id: bytes) -> SnapshotBody:
        resp = self.broker.get_snapshot(experiment_id)
        return resp
    
    def getdata(self, psids: List[bytes], tick: int) -> Mapping[str, Any]:
        '''Returns the current snapshot'''
        snapshot = self._feed.get_snapshot(psids, tick)
        return snapshot

    def cancel(self, order_id: bytes):
        raise NotImplementedError("cancel not implemented in BTStore")
    
    def stop(self):
        '''Stops and tells the store to stop'''
        super().stop()

        # Stop broker which calls tdapi.stop() -> engine.stop() -> simulator.shutdown() -> writer.stop()
        # This already handles stopping the writer actor
        if hasattr(self, 'broker') and self.broker is not None:
            self.broker.stop()

        # Stop AsyncRunner singleton to reset for next execution
        if hasattr(self, '_runner') and self._runner is not None:
            self._runner.stop()
