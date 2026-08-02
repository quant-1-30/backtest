# cython: language_level=3
# cython: boundscheck=False
# cython: wraparound=False
# cython: cdivision=True
# cython: language_level=3

from libc.stdint cimport int32_t, int64_t

import os
import uuid
from typing import Union
from sqlalchemy import select, func, over, text
from sqlalchemy.orm import joinedload, subqueryload, selectinload, load_only, aliased
from bt_core.execution.gateway.operator.operator import async_ops


from bt_sdk.ctx import get_md_api
from bt_protocol.constant import FactorTopic, RpcTopic
from bt_protocol.schema.trade import Experiment, vtPosition, vtOrder, vtAccount

cdef const int64_t BatchSize = 100


cdef class AsyncGateway:

    def __init__(self):
        self._mdapi = None

    @property
    def mdapi(self): # lazy load 
        if self._mdapi is None:
            self._mdapi = get_md_api()

        return self._mdapi
    
    async def register(self, object event): 
        cdef object body = event.body
        cdef object row, resp

        obj = Experiment(client_id=uuid.UUID(bytes=body.client_id),
                        strategy=body.strategy,
                        extra_info=body.extra_info)
        row = await self(obj, return_obj=True)
        resp = row[0].serialize()
        return resp

    async def get_account(self, object experiment_id):
        cdef object resp
        cdef list result = []

        async with async_ops as ctx: 
            # subquery = (
            #     select(vtAccount.experiment_id, func.max(vtAccount.datetime).label('max_datetime'))
            #     .group_by(vtAccount.experiment_id)
            #     .subquery()
            # )
            # stmt = (
            #     select(vtAccount)
            #     .join(subquery, (vtAccount.experiment_id == subquery.c.experiment_id) & (vtAccount.datetime == subquery.c.max_datetime))
            # )
            sub_stmt = (
                select(
                    vtAccount,
                    func.row_number().over( # over api group and calculate
                        partition_by=vtAccount.experiment_id, 
                        order_by=vtAccount.datetime.desc()
                    ).label("rn")
                )
                .where(vtAccount.experiment_id == experiment_id)
            ).subquery() 
            # aliased subquery related to SQLAlchemy entity class and result is vtAccount object
            account_alias = aliased(vtAccount, sub_stmt) 

            stmt = (
                select(account_alias)
                .where(sub_stmt.c.rn == 1)
            )
        
            stream_wrap = await ctx.on_query(stmt)

            async with stream_wrap as stream_proxy: 
                async for row in stream_proxy.scalars():
                    resp = row.serialize()
                    result.append(resp)
        return result

    async def get_position(self, object experiment_id):
        cdef object resp
        cdef list result = []

        async with async_ops as ctx: 
            sub_stmt = (
                select(
                    vtPosition,
                    func.row_number().over(
                        partition_by=vtPosition.experiment_id, 
                        order_by=vtPosition.datetime.desc()
                    ).label("rn")
                )
                .where(vtPosition.experiment_id == experiment_id)
            ).subquery() 
            position_alias = aliased(vtPosition, sub_stmt)

            stmt = (
                select(position_alias)
                .where(sub_stmt.c.rn == 1)
            )
            stream_wrap = await ctx.on_query(stmt)

            async with stream_wrap as stream_proxy: 
                async for row in stream_proxy.scalars():
                    resp = row.serialize()
                    result.append(resp)
        return result

# ------------------------------------------------------------------- subscribe api -------------------------------------------------------------------

    async def _subscribe_order(self, object experiment_id, object body):
        cdef list sids = body.sid
        cdef list batch = [], resp = []

        async with async_ops as ctx: 
            stmt = (
                select(vtOrder)
                .where(vtOrder.experiment_id == experiment_id)
                .where(vtOrder.created_dt.between(body.start_date, body.end_date))
                .options(
                    # load_only(vtOrder.id, vtOrder.experiment_id), # 加载必要的字段减少内存占用
                    # selectinload 代替 subqueryload 在处理ID 列表时通常比子查询更快 / row.order_bits 已在内存中无额外IO
                    selectinload(vtOrder.order_bits) 
                )
            )
        
            if sids:
                stmt = stmt.where(vtOrder.sid.in_(sids))
        
            stmt = stmt.order_by(vtOrder.created_dt.desc())
        
            stream_wrap = await ctx.on_query(stmt)

            async with stream_wrap as stream_proxy: 
                async for row in stream_proxy.scalars():
                    resp = [bit.serialize() for bit in row.order_bits]
                    batch.extend(resp)
                    while len(batch) >= BatchSize:
                        yield batch[:BatchSize]
                        batch = batch[BatchSize:]
                if batch:
                    yield batch

    async def _subscribe_position(self, object experiment_id, object body):
        cdef list sids = body.sid
        cdef list batch = []
        cdef object resp

        async with async_ops as ctx: 
            stmt = select(vtPosition).where(vtPosition.experiment_id == experiment_id)
            stmt = stmt.where(vtPosition.datetime.between(body.start_date, body.end_date))
            if sids:
                stmt = stmt.where(vtPosition.sid.in_(sids))
            stmt = stmt.order_by(vtPosition.datetime.desc())

            stream_wrap = await ctx.on_query(stmt)

            async with stream_wrap as stream_proxy: 
                async for row in stream_proxy.scalars():
                    resp = row.serialize() 
                    batch.append(resp)
                    if len(batch) >= BatchSize:
                        yield batch
                        batch = []
                if batch:
                    yield batch 

    async def _subscribe_account(self, object experiment_id, object body):
        cdef list batch = []
        cdef object resp
    
        async with async_ops as ctx: 
            stmt = select(vtAccount).where(vtAccount.experiment_id == experiment_id)
            stmt = stmt.where(vtAccount.datetime.between(body.start_date, body.end_date))
        
            stream_wrap = await ctx.on_query(stmt)

            async with stream_wrap as stream_proxy: 
                async for row in stream_proxy.scalars():
                    resp = row.serialize()
                    batch.append(resp)
                    if len(batch) >= BatchSize:
                        yield batch
                        batch = []
                if batch:
                    yield batch

    async def subscribe(self, object event): 
        cdef int topic = event.sub_topic
        cdef object body = event.body
        cdef object uuid_experiment_id = uuid.UUID(bytes=event.experiment_id)

        if topic == SubTopic.Order:
            async for serialbatch in self._subscribe_order(uuid_experiment_id, body):
                yield serialbatch
        elif topic == SubTopic.Position:
            async for serialbatch in self._subscribe_position(uuid_experiment_id, body):
                yield serialbatch
        elif topic == SubTopic.Account:
            async for serialbatch in self._subscribe_account(uuid_experiment_id, body):
                yield serialbatch
        else:
            yield []

# ------------------------------------------------------------------- rpc api -------------------------------------------------------------------
    
    async def rpc(self, object body, int32_t rpc_type, int32_t timeout=30):
        df = await self.mdapi.rpc_async(body, rpc_type) # timeout
        return df

    async def __call__(self, object objs, bint return_obj=False):
        async with async_ops as ctx:
            result = await ctx.on_insert_obj(objs, return_obj=return_obj)
            return result

    async def bulk_insert(self, str table_name, list data):
        async with async_ops as ctx:
            result = await ctx.on_insert(table_name, data)
            return result


async_gt = AsyncGateway()
