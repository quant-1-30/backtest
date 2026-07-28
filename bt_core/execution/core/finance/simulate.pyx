# cython.boundscheck(False) # 关闭边界检查
# cython.wraparound(False)  # 关闭负指数索引检查
# distutils: language = c++

import os
import time
import json
import uuid
import asyncio
import logging
import numpy as np
import threading
import pyarrow as pa
import pyarrow.compute as pc
from collections import defaultdict
from itertools import chain

from bt_core.execution.gateway.interface import async_gt

from libcpp.unordered_map cimport unordered_map
from libcpp.pair cimport pair
from libcpp.vector cimport vector
from cython.operator cimport dereference as deref
from libc.stdint cimport int32_t, int64_t

from bt_core.execution.core.finance.asset cimport Asset
from bt_core.execution.core.finance.position cimport Position, PositionCoreData
from bt_core.execution.core.finance.order cimport OrderCoreData, Order
from bt_core.execution.core.finance.account cimport Account
from bt_core.execution.core.finance.trade cimport OrderExecutionBit
from bt_core.execution.core.finance.common cimport EventItem, AdjustmentData, RightData
from bt_core.execution.core.finance.filler import _fillers
from bt_core.execution.core.finance.simulate_types cimport MsgType
from bt_core.utils.dateintern cimport ts2intdt
from bt_core.execution.actor.writer_actor cimport BatchWriterActor

from bt_protocol._protocol import SnapshotBody, Resp, Event, QueryBody
from bt_protocol.constant import RpcTopic

cimport numpy as cnp
cnp.import_array() # initialize numpy C-API

logger = logging.getLogger(__name__)


cdef class TrackerActor:

    def __init__(self, bytes experiment_id, BatchWriterActor writer, AssetCache asset_cache, int32_t q_size, int32_t buffer_size, object _loop):
        self.positions = {}
        self._put_buffer = [] 
        self.buffer_size = buffer_size
        self.experiment_id = experiment_id 
        self.cached_uuid = uuid.UUID(bytes=experiment_id)

        self.cash_manager = SyncCashManager()
        self.asset_cache = asset_cache
        self._writer = writer 
        
        self._loop = _loop
        self._latest_snapshot = None 

        # cache position snapshots
        self._snapshot_dirty = True
        self._cached_pos_snaps = []
        self._cached_pobj_body = []

        # clean tracking position which size=0
        self._dirty_pkeys = set()
     
    async def _start(self):
        cdef bytes sid, experiment_id
        cdef Position p_obj
        cdef tuple pkey
        cdef list datas
        cdef object body, row # Resp
        
        try:
            datas = await async_gt.get_position()
            for row in datas:
                body = row.body
                sid = body.sid
                experiment_id = body.experiment_id
                p_key = (experiment_id, sid)

                asset_core = self.asset_cache.get_cache_info(sid, self._loop)
                p_obj = Position(experiment_id = experiment_id,
                                sid = sid,
                                asset = asset_core,
                                datetime = body.datetime,
                                size = body.size,
                                available = body.available,
                                cost_basis = body.cost_basis,
                                pnl = body.pnl,
                                created_dt = body.created_dt)
                self.positions[p_key] = p_obj # setdefault return default object
            # print(f"TrackerActor _start positions: {self.positions}")

            await self.cash_manager._start()
            self._snapshot_dirty = True
            self._create_snapshot(reason="_start")

        except Exception as e:
            logger.exception(f"Error starting position tracker: {e}")

    async def _fetch_from_rpc(self, int32_t prev_dts, int32_t curr_dts, list psids): 
        """
        prev_dt: T-1 Close
        curr_dt: T   ex_dat Adj/Rgt
        """
        async def _fetch(object body, int32_t rpc_type):
            df_dict = await async_gt.rpc(body, rpc_type) 
            return df_dict 

        # 🌟 T-1 Close
        cdef int32_t last_dtint = ts2intdt(prev_dts)
        cdef int32_t curr_dtint = ts2intdt(curr_dts) if curr_dts > 0 else 0

        close_body = QueryBody(start_date=last_dtint, end_date=last_dtint, sid=psids) 
        close_task = asyncio.create_task(_fetch(close_body, RpcTopic.Close))
        
        if curr_dtint == 0:
            closes_df_map = await close_task
            return closes_df_map, {}, {}
            
        event_body = QueryBody(start_date=curr_dtint, end_date=curr_dtint, sid=psids) 
        adj_task = asyncio.create_task(_fetch(event_body, RpcTopic.Adjustment))
        rgt_task = asyncio.create_task(_fetch(event_body, RpcTopic.Rightment))
        
        closes_df_map, adjs_df_map, rgts_df_map = await asyncio.gather(close_task, adj_task, rgt_task) 
        return closes_df_map, adjs_df_map, rgts_df_map

    cpdef object set_cash(self, object payload):
        """set cash and accout wrt"""
        self.cash_manager.set_cash(payload)
        self._snapshot_dirty = True
        self._create_snapshot(reason="account_sync", writer=False)
        return Resp(body=self._latest_snapshot)

    cpdef object process_order(self, Order order):
        cdef OrderCoreData core = order.core
        cdef bytes sid = core.sid
        cdef bytes experiment_id = core.experiment_id
        cdef OrderExecutionBit ordbit
        cdef Position p_sid
        cdef tuple pkey = (experiment_id, sid)
        cdef list order_bits = []
        cdef dict order_dict

        cdef Asset asset = self.asset_cache.get_cache_info(sid, self._loop)
        
        order.addinfo(asset)

        if pkey not in self.positions: 
            self.positions[pkey] = Position(sid=sid, experiment_id=experiment_id, asset=asset, created_dt=core.created_dt)
        p_sid = self.positions[pkey]

        # PseudoFiller
        acct = self.cash_manager.get_account(experiment_id)
        _fillers[order.filler](order, acct.core.cash, p_sid, self._loop)

        if order.exbits:
            for ordbit in order.exbits:
                p_sid.update(ordbit)
                order_bits.append(ordbit.get_snapshot())
            self.cash_manager.update(experiment_id, order.exbits, p_sid.core.pnl) # avoid position update increment

        # clean dirty positions 
        if p_sid.core.size == 0:
            self._dirty_pkeys.add(pkey)

        # order snapshot
        order_dict = order.get_snapshot() 
        order_dict['experiment_id'] = self.cached_uuid
        order_dict.pop("sizer_ratio")

        if order_bits:
            self._put_buffer.append({"order": [order_dict, order_bits]})
    
        self._snapshot_dirty = True
        self._create_snapshot(reason="order_sync", writer=False, trades=order.serialize())
        self._check_flush()
        return Resp(body=self._latest_snapshot)

    cdef void _sync_event(self, bytes experiment_id, dict pobjs, dict py_adj_dfs, dict py_rgt_dfs):
        cdef double event_cash = 0.0
        cdef Position pos_obj  
        cdef unordered_map[int32_t, AdjustmentData] cpp_adj_map 
        cdef unordered_map[int32_t, RightData] cpp_rgt_map
        cdef vector[EventItem] v_events
        cdef EventItem temp
        cdef int32_t int_sid

        # pre-build sid decode cache to avoid repeated decode+int conversion
        cdef dict sid_cache = {}
        cdef bytes sid_bytes

        for sid_bytes in py_adj_dfs.keys():
            if sid_bytes not in sid_cache:
                sid_cache[sid_bytes] = int(sid_bytes.decode('utf-8'))
        for sid_bytes in py_rgt_dfs.keys():
            if sid_bytes not in sid_cache:
                sid_cache[sid_bytes] = int(sid_bytes.decode('utf-8'))

        for sid_bytes, py_adj_df in py_adj_dfs.items():
            if py_adj_df is not None and py_adj_df.height > 0:
                int_sid = sid_cache[sid_bytes]
                for bonus_share, transfer, bonus in py_adj_df.select(["bonus_share", "transfer", "bonus"]).rows():
                    cpp_adj_map[int_sid] = AdjustmentData(
                        bonus_share=float(bonus_share), 
                        transfer=float(transfer), 
                        bonus=float(bonus)
                    )

        for sid_bytes, py_rgt_df in py_rgt_dfs.items():
            if py_rgt_df is not None and py_rgt_df.height > 0:
                int_sid = sid_cache[sid_bytes]
                for ratio, price in py_rgt_df.select(["ratio", "price"]).rows():
                    cpp_rgt_map[int_sid] = RightData(
                        ratio=float(ratio), 
                        price=float(price)
                    )

        for (_, sid_bytes), pos_obj in pobjs.items():
            if sid_bytes not in sid_cache:
                sid_cache[sid_bytes] = int(sid_bytes.decode('utf-8'))
            int_sid = sid_cache[sid_bytes]
            v_events.clear() 

            adj_it = cpp_adj_map.find(int_sid) 
            if adj_it != cpp_adj_map.end():
                temp.event_type = 0
                temp.adj = deref(adj_it).second 
                v_events.push_back(temp)

            rgt_it = cpp_rgt_map.find(int_sid)
            if rgt_it != cpp_rgt_map.end():
                temp.event_type = 1
                temp.rgt = deref(rgt_it).second 
                v_events.push_back(temp)
                
            if not v_events.empty():
                event_cash += pos_obj.process_events(v_events)

        if event_cash != 0:
            self.cash_manager.add_cash(experiment_id, event_cash)

    cdef void _clean(self): # filter psize=0
        cdef tuple pkey
        cdef Position pos  
        
        if not self._dirty_pkeys:
            return
            
        for pkey in self._dirty_pkeys:
            if pkey in self.positions:
                pos = <Position>self.positions[pkey]
                if pos.core.size == 0:
                    del self.positions[pkey]

        self._dirty_pkeys.clear()

    async def on_dt_over(self, object event):
        cdef bytes experiment_id = event.experiment_id
        cdef int32_t last_sync_dts = event.body.start_date
        cdef int32_t current_dts = event.body.end_date
        cdef Position p_obj, exist_p
        
        cdef int32_t close_dt
        cdef int32_t total_size
        cdef double close_price
        cdef bytes sid_bytes, map_sid
        
        cdef tuple p_key
        cdef dict new_positions = {}, closes_map = {}
        cdef set unique_sids_set = {sid_bytes for (eid, sid_bytes) in self.positions.keys() if eid == experiment_id}

        if not unique_sids_set:
            self.cash_manager.sync(experiment_id, last_sync_dts, {}, {})
            self._snapshot_dirty = True
            self._create_snapshot(reason="on_dt_over", writer=True)
            self._check_flush()
            return Resp(body=self._latest_snapshot)
        
        closes_df_map, adjs_df_map, rgts_df_map = await self._fetch_from_rpc(last_sync_dts, current_dts, list(unique_sids_set))

        # used to sync portfolio_value 
        for map_sid in unique_sids_set:
            close_df = closes_df_map.get(map_sid, None)
            if close_df is not None and close_df.height > 0:
                closes_map[map_sid] = close_df.select("close").row(-1)[0]
            else:
                closes_map[map_sid] = 0.0

        self._clean() # remove size=0

        # track merger key changes for in-place update
        cdef list merger_keys = []
        for (eid, sid_bytes), p_obj in self.positions.items():
            if eid == experiment_id:
                close_df = closes_df_map.get(sid_bytes, None)
                if close_df is not None and close_df.height > 0:
                    for day, close in close_df.select(["day", "close"]).rows():
                        p_obj.on_dt_over(int(day), float(close))
                else: 
                    p_obj.on_dt_over(ts2intdt(last_sync_dts), 0.0)

            # check if merger changed the sid
            if p_obj.core.sid != sid_bytes:
                merger_keys.append((eid, sid_bytes))

        # handle merger key changes in-place
        for old_key in merger_keys:
            p_obj = self.positions.pop(old_key)
            new_key = (old_key[0], p_obj.core.sid)
            if new_key in self.positions:
                exist_p = self.positions[new_key]
                total_size = exist_p.core.size + p_obj.core.size
                if total_size > 0:
                    exist_p.core.cost_basis = ((exist_p.core.cost_basis * exist_p.core.size) + 
                                               (p_obj.core.cost_basis * p_obj.core.size)) / total_size
                else:
                    exist_p.core.cost_basis = 0.0
                exist_p.core.size = total_size
                exist_p.core.available += p_obj.core.available
                exist_p.core.pnl += p_obj.core.pnl
            else:
                self.positions[new_key] = p_obj

        # T-1 Sync 
        self.cash_manager.sync(experiment_id, last_sync_dts, self.positions, closes_map)

        # -------------------------------------------------------------
        # Sync T Event
        # -------------------------------------------------------------
        if current_dts >0:
            self._sync_event(experiment_id, self.positions, adjs_df_map, rgts_df_map)

        self._snapshot_dirty = True
        self._create_snapshot(reason="dt_over", writer=True)
        self._check_flush()
        return Resp(body=self._latest_snapshot)

    cdef void _create_snapshot(self, str reason, bint writer=False, list trades=None):
        cdef Account acct = self.cash_manager.get_account(self.experiment_id).clone()
        cdef a_dict = acct.get_snapshot()
        a_dict['experiment_id'] = self.cached_uuid
        
        cdef Position p_obj
        cdef dict p_dict

        # only rebuild position snapshots when dirty
        if self._snapshot_dirty or trades is not None:
            pos_snaps = []
            pobj_body = []
            for _, p_obj in self.positions.items():
                if p_obj.core.size == 0:
                    continue

                p_dict = p_obj.clone().get_snapshot()
                p_dict.pop("realized_pnl")
                p_dict['experiment_id'] = self.cached_uuid
                
                pos_snaps.append(p_dict)
                pobj_body.append(p_obj.serialize().body)
            
            self._cached_pos_snaps = pos_snaps
            self._cached_pobj_body = pobj_body
            self._snapshot_dirty = False
        else:
            pos_snaps = self._cached_pos_snaps
            pobj_body = self._cached_pobj_body
            
        self._latest_snapshot = SnapshotBody(
            account=acct.serialize().body, 
            positions=pobj_body,
            trades=trades
        )

        if writer:
            payload = {
                "positions": pos_snaps,
                "account": a_dict,
            }
            self._put_buffer.append(payload)

    cdef void _check_flush(self):
        if len(self._put_buffer) >= self.buffer_size:
            asyncio.run_coroutine_threadsafe(self._writer.push(self._put_buffer), self._loop)
            self._put_buffer = []

    cpdef object get_snapshot(self):
        self._create_snapshot(reason="query", writer=False)
        return Resp(body=self._latest_snapshot)
    
    async def shutdown(self):
        if self._put_buffer:
           await self._writer.push(self._put_buffer) 
        await self._writer.push([MsgType.Sentinel])


cdef class Simulator:
    
    def __init__(self, int32_t q_size, int32_t buffer_size, BatchWriterActor actor):
        self._loop = None 
        self._actors = {}
        self.q_size = q_size 
        self.buffer_size = buffer_size
        self._asset_cache = AssetCache()

        self._writer = actor

    cdef void attach(self, loop):
        self._loop = loop

    cdef TrackerActor _get_or_create_actor(self, bytes experiment_id):
        cdef TrackerActor actor

        if experiment_id not in self._actors:
            actor = TrackerActor(experiment_id, self._writer, self._asset_cache, self.q_size, self.buffer_size, self._loop)
            self._actors[experiment_id] = actor
            # avoid self._loop.create_task()
            asyncio.run_coroutine_threadsafe(actor._start(), self._loop).result() 
        return self._actors[experiment_id]
        
    cpdef object set_cash(self, object event):
        cdef bytes experiment_id = event.experiment_id
        cdef TrackerActor actor = self._get_or_create_actor(experiment_id)
        
        result = actor.set_cash(event)
        return result

    cpdef object submit(self, Order order):
        cdef bytes experiment_id = order.core.experiment_id
        cdef TrackerActor actor = self._get_or_create_actor(experiment_id)
        
        result = actor.process_order(order)
        return result

    cpdef object on_dt_over(self, object event): # blocking - waits for coroutine completion
        cdef bytes experiment_id = event.experiment_id
        cdef TrackerActor actor = self._get_or_create_actor(experiment_id)
        
        cdef object future = asyncio.run_coroutine_threadsafe(actor.on_dt_over(event), self._loop)
        return future.result()

    cpdef object get_snapshot(self, object event):
        cdef bytes experiment_id = event.experiment_id
        cdef TrackerActor actor = self._get_or_create_actor(experiment_id)
        
        result = actor.get_snapshot()
        return result

    async def shutdown(self):
        for actor in self._actors.values():
            await actor.shutdown()

        logger.info("All TrackerActors stopped.")
        await self._writer.wait_until_finished()
        logger.info("Simulator shutdown complete.")