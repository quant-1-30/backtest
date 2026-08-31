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
import warnings
import numpy as np
import queue
import pyarrow as pa
import pyarrow.compute as pc
import polars as pl
import reactivex.operators as ops

from toolz import valmap
from typing import List, Mapping, Any

from bt_core.feed import DataBase
from bt_core.dataseries import TimeFrame
from bt_core.metabase import with_metaclass
from bt_core.stores.localstore import LocalStore
from bt_sdk.utils.util import _merge2DataFrame

from bt_protocol._protocol import QueryBody
from bt_protocol.constant import FactorTopic, RpcTopic

__all__ = ["RemoteData"]


class Instrument(object):
    '''Descriptor instrument'''
    def __init__(self):
        self.assets = {}
    
    def __set__(self, instance, value):
        raise AttributeError("not allowed to set")
    
    def __get__(self, instance, owner):
        if instance is None: return self
        
        mdapi = getattr(instance, 'mdapi', None)
        if len(self.assets) == 0:
            table = mdapi.get_instrument() 
            # ctable = pa.concat_tables(tables, promote_options="permissive") # zero_copy , but combine_chunk is heavy memory ops
            self.assets = table.to_pylist() # row-wise dict list / table.to_pandas() and df.to_dict('records') # Arrow --> Pandas
        return self.assets


class MetaRemoteData(DataBase.__class__):
    
    def __init__(cls, name, bases, dct):
        """auto Register with the store when type class __import__"""
        super(MetaRemoteData, cls).__init__(name, bases, dct)
        LocalStore.DataCls = cls

    def donew(cls, *args, **kwargs):
        # print("MetaRemoteData donew kwargs ", kwargs) 
        _obj, args, kwargs = super(MetaRemoteData, cls).donew(*args, **kwargs)
        # print("MetaRemoteData donew kwargs after", kwargs)
        return _obj, args, kwargs
    
    def dopostinit(cls, _obj, *args, **kwargs):
        # print("MetaRemoteData dopostinit kwargs ", kwargs) 
        _obj, args, kwargs = super().dopostinit(_obj, *args, **kwargs) 
        # print("MetaRemoteData dopostinit kwargs ", kwargs)  
        _obj.mdapi = _obj.p.mdapi
        _obj.chan = queue.Queue()
        return _obj, args, kwargs


class RemoteData(with_metaclass(MetaRemoteData, DataBase)):
    
    params = (
        ("mdapi", None),
        ("rtbar", False,), # use RealTime 5 seconds bars
    )

    instrument = Instrument()

    def _prepare(self, _loop): 
        self.mdapi.start(_loop)

    def _start(self, *args, **kwargs):
        super()._start(*args,**kwargs)
        # self._row_iter = None
        self._row_iter = False
        self.sid = kwargs["sid"]

        tick_body = QueryBody(start_date=kwargs["fromdate"], end_date=kwargs["todate"], sid=self.sid)
        self.get_adjfactor(tick_body)
        # print("finish tick adjadjust")

        bench_body = QueryBody(start_date=kwargs["fromdate"], end_date=kwargs["todate"], sid=kwargs["benchmark"])
        self.get_dret(bench_body)
        # print("finish benchmark data")

        observable = self.mdapi.subscribe(tick_body, RpcTopic.Tick)
        observable.pipe(
            # ops.sample(0.1),  # 100ms abandon reset 
            # ops.buffer_with_time_or_count(timespan=1.0, count=500), # up to 500 / 1 second to list
            # ops.throttle_first(0.05), # on receive / 50ms not receive
            # ops.publish_replay(1), # cache 1 record 
            # ops.ref_count()
            ops.map(lambda data: data["data"]),
            ops.share()
        ).subscribe( # nonblocking 
            on_next=self.chan.put,
            on_error=lambda e: self.chan.put(e),
            on_completed=lambda: self.chan.put(StopIteration) 
        )
 

    def _make_iter(self, table): 
        # tuple faster than zip(*cols) 
        # cols = [table[name].to_numpy() for name in ['tick', 'open', 'high', 'low', 'close', 'volume', 'amount']] # iter(msg.to_pylist()) 
        # return zip(*cols)
        self._dt_arr = table['tick'].to_numpy()
        self._op_arr = table['open'].to_numpy()
        self._hi_arr = table['high'].to_numpy()
        self._lo_arr = table['low'].to_numpy()
        self._cl_arr = table['close'].to_numpy()
        self._vo_arr = table['volume'].to_numpy()
        self._am_arr = table['amount'].to_numpy()
        
        self._row_idx = 0
        self._total_rows = len(self._dt_arr)
        return True 

    def _load(self):
        while True:
            if self._row_iter:
                if self._row_idx < self._total_rows:
                    if self.p.rtbar:
                        ret = self._load_rtbar()
                    else:
                        ret = self._load_bar()
                    self._row_idx += 1
                    return ret
                else:
                    self._row_iter = False 

            msg = self.chan.get()
            if msg is StopIteration: return False
            if isinstance(msg, Exception): raise msg

            self._row_iter = self._make_iter(msg)

    def _load_bar(self):
        dt = self.lines.datetime[0]
        cur_dt = self._dt_arr[self._row_idx]
        if not np.isnan(dt) and dt >= cur_dt:
            return False 
        
        idx = self._row_idx
        self.lines.datetime[0] = cur_dt
        self.lines.open[0] = self._op_arr[idx]
        self.lines.high[0] = self._hi_arr[idx]
        self.lines.low[0] = self._lo_arr[idx]
        self.lines.close[0] = self._cl_arr[idx]
        self.lines.volume[0] = self._vo_arr[idx]
        self.lines.amount[0] = self._am_arr[idx]
        return True

    def _load_rtbar(self, row): # tick 3s
        dt = self.lines.datetime[0]
        cur_dt = self._dt_arr[self._row_idx]
        if not np.isnan(dt) and dt >= row[0]:
            return False  
        
        self.lines.datetime[0] = cur_dt
        self.lines.open[0] = self._op_arr[idx]
        self.lines.high[0] = self._op_arr[idx]
        self.lines.low[0] = self._op_arr[idx]
        self.lines.close[0] = self._op_arr[idx]
        self.lines.volume[0] = self._vo_arr[idx]
        self.lines.amount[0] = self._am_arr[idx]
        return True

    @staticmethod
    def _collect_stream_sync(observable) -> List[pa.Table]:
        q = queue.Queue()
        observable.pipe(
            # ops.sample(0.1),  # 100ms abandon reset 
            # ops.buffer_with_time_or_count(timespan=1.0, count=500), # up to 500 / 1 second to list
            # ops.throttle_first(0.05), # on receive / 50ms not receive
            # ops.publish_replay(1), # cache 1 record 
            # ops.ref_count()
            ops.map(lambda data: data["data"]),
            ops.share()
        ).subscribe(
            on_next=q.put,
            on_error=q.put,
            on_completed=lambda: q.put(StopIteration)
        )
        
        tables = []
        while True:
            msg = q.get()
            if msg is StopIteration:
                break
            if isinstance(msg, Exception):
                raise msg
            tables.append(msg)
        return tables

    def get_snapshot(self, psids: List[bytes], tick: int) -> Mapping[str, Any]:
        body = QueryBody(start_date=tick, end_date=tick, sid=psids)
        obs = self.mdapi.subscribe(body, RpcTopic.Tick)
        tables = self._collect_stream_sync(obs)
        
        snapshot = {}
        if tables:
            raw_data = _merge2DataFrame(tables)
            for sid_bytes, df in raw_data.items():
                snapshot[sid_bytes] = df["close"][0] # .as_py()
        return snapshot

    def get_adjfactor(self, body: QueryBody):
            adj_data = self.mdapi.get_factor(body, FactorTopic.Qfq)
            adj = adj_data[body.sid[0]]
            # raw_factors ( ex-date ratio)
            factors = adj.raw_factors if adj else {} # adj_factors
            if factors:
                factors = dict(sorted(factors.items())) # sort by key
                self.adj_factors = factors

    def get_dret(self, body: QueryBody):
        obs = self.mdapi.subscribe(body, RpcTopic.Close)
        tables = self._collect_stream_sync(obs)
        
        raw_data = _merge2DataFrame(tables)
        if body.sid[0] in raw_data:
            close_df = raw_data[body.sid[0]]
            self.benchmark_dret = close_df.with_columns(
                pl.col("close").pct_change().fill_null(0).alias("ret")
            ).select(["day", "ret"])

    def stop(self):
        super().stop()
        self.mdapi.disconnect()

