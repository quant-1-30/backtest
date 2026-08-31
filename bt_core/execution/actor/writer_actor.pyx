# cython: language_level=3
# cython: boundscheck=False
# cython: wraparound=False

import os
import time
import json
import asyncio
import logging
from libc.stdint cimport int32_t, int64_t

from bt_core.execution.core.finance.position cimport Position 
from bt_core.execution.core.finance.order cimport Order
from bt_core.execution.core.finance.account cimport Account
from bt_core.execution.core.finance.simulate_types cimport MsgType
from bt_core.execution.gateway.interface import async_gt

logger = logging.getLogger(__name__)


cdef inline bytes _normalize_sid(object raw_sid) noexcept:
    if isinstance(raw_sid, bytes): return raw_sid
    if isinstance(raw_sid, str): return raw_sid.encode('utf-8')
    if isinstance(raw_sid, memoryview): return raw_sid.tobytes()
    if isinstance(raw_sid, bytearray): return bytes(raw_sid)
    return str(raw_sid).encode('utf-8')


cdef inline _sync_write(str path, list data):
    try:
        with open(path, 'w') as f:
            json.dump(data, f, default=str, indent=4)
    except TypeError:
        with open(path, 'w') as f:
            f.write(str(data))


cdef class BatchWriterActor: 

    def __init__(self, int32_t q_size, int32_t batch_size, int32_t retries=1):
        self._buffer = []
        self._running = True
        self.retries = retries
        self.batch_size = batch_size
        self._queue = asyncio.Queue(maxsize=q_size)
        self._finished_event = asyncio.Event()

    async def push(self, list snapshots):
        # self._queue.put_nowait(snapshots) # may cause oom
        await self._queue.put(snapshots)

    async def run(self):
        logger.info("BatchWriterActor started.")
        cdef object data

        while self._running:
            try:
                data = await self._queue.get()
                if [MsgType.Sentinel] == data: 
                    await self._flush()
                    self._running = False
                    break

                self._buffer.extend(data)
                if len(self._buffer) >= self.batch_size:
                    await self._flush()

            except Exception as e:
                logger.error(f"BatchWriterActor running error: {e}")
        
        self._finished_event.set()
        logger.info("BatchWriterActor stopped.")

    async def _flush(self):
        cdef list account_data = []
        cdef list order_data = []
        cdef list orderbit_data = []
        cdef list position_data = []
        
        cdef dict dedup_positions = {}  
        cdef dict dedup_accounts = {}   
        
        if not self._buffer:
            return
        try:
            for item in self._buffer:
                if "order" in item:
                    order_dict, order_bits = item["order"] 
                    order_data.append(order_dict)
                    
                    orderbit_data.extend(order_bits)

                if "positions" in item:
                    for p_dict in item["positions"]:
                        key = (int(p_dict['datetime']), _normalize_sid(p_dict['sid']), str(p_dict['experiment_id']))
                        dedup_positions[key] = p_dict
                
                if "account" in item:
                    a_dict = item["account"]
                    key = (int(a_dict['datetime']), str(a_dict['experiment_id']))
                    dedup_accounts[key] = a_dict
            
            if order_data:
                await self._chunked_write("vtorder", order_data)
            if orderbit_data:
                await self._chunked_write("order_bit", orderbit_data)
            
            if dedup_positions:
                await self._chunked_write("vtposition", list(dedup_positions.values()))
            if dedup_accounts:
                await self._chunked_write("account", list(dedup_accounts.values()))

        except Exception as e:
            logger.error(f"Critical error during flush: {e}", exc_info=True)
            print("crush error: ", e)
            await self._dump_fallback("flush_crash", self._buffer)
        
        finally:
            self._buffer.clear()

    async def _chunked_write(self, str table, list data):
        if not data:
            return
            
        cdef int32_t start
        cdef int32_t safe_size = 500
        cdef list chunk
        
        for start in range(0, len(data), safe_size): # seq occupy one conn avoid gather tasks(cons)
            chunk = data[start : start + safe_size]
            await self._retry_write(table, chunk)

    async def _retry_write(self, str table, list data):
        cdef int32_t i=0

        while i < self.retries:
            try:
                await async_gt.bulk_insert(table, data) 
                return 
            except Exception as e:
                i += 1
                if i < self.retries:
                    logger.warning(f"Write {table} failed (attempt {i}), retrying... Error: {e}")
                    await asyncio.sleep(0.1 * i)
                else:
                    logger.error(f"Write {table} failed permanently. Dumping to disk. Error: {e}")
                    await self._dump_fallback(table, data)

    async def _dump_fallback(self, str prefix, object data):
        try:
            dump_dir = "./dump_fallback"
            if not os.path.exists(dump_dir):
                os.makedirs(dump_dir, exist_ok=True)
            
            timestamp = int(time.time() * 1000)
            filepath = f"{dump_dir}/{prefix}_{timestamp}.json"
            
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, _sync_write, filepath, data)
            logger.info(f"Data saved to {filepath}")
        except Exception as e:
            logger.critical(f"FATAL: Could not dump data! Data lost. {e}")

    async def wait_until_finished(self): # wait to exit from run
        await self._finished_event.wait()

    async def stop(self):
        """Stop the actor gracefully by sending sentinel and waiting for completion."""
        # do NOT clear _running before the sentinel: run() checks it after each
        # item and would exit without draining the queue, silently dropping
        # everything still queued (including the final flush from shutdown)
        await self._queue.put([MsgType.Sentinel])
        # Wait for run() to finish processing
        await self._finished_event.wait()
        logger.info("BatchWriterActor gracefully stopped.")
