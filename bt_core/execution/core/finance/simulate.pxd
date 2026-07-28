from libc.stdint cimport int32_t

from bt_core.execution.core.finance.order cimport Order
from bt_core.execution.core.finance.simulate_types cimport ActorId
from bt_core.execution.core.finance.cash cimport SyncCashManager
from bt_core.execution.core.finance.cache cimport AssetCache
from bt_core.execution.actor.writer_actor cimport BatchWriterActor


cdef class TrackerActor:
    cdef int32_t buffer_size
    cdef bytes experiment_id 

    cdef SyncCashManager cash_manager      
    cdef AssetCache asset_cache  
    cdef BatchWriterActor _writer 

    cdef bint _snapshot_dirty 
    cdef list _cached_pos_snaps
    cdef list _cached_pobj_body
    cdef set _dirty_pkeys

    cdef list _put_buffer
    cdef object cached_uuid
    cdef object _latest_snapshot
    cdef object _loop

    cdef dict positions

    cpdef object set_cash(self, object payload)

    cpdef object process_order(self, Order order)

    cdef void _sync_event(self, bytes experiment_id, dict pobjs, dict py_adj_dfs, dict py_rgt_dfs)

    cdef void _clean(self) # filter psize=0

    cdef void _create_snapshot(self, str reason, bint writer=?, list trades=?)

    cdef void _check_flush(self)
    
    cpdef object get_snapshot(self)
    

cdef class Simulator:
    cdef int32_t q_size
    cdef int32_t buffer_size
    cdef AssetCache _asset_cache
    cdef object _writer
    cdef object _loop
    cdef dict _actors 
    
    cdef void attach(self, loop)
    
    cdef TrackerActor _get_or_create_actor(self, bytes experiment_id)

    cpdef object set_cash(self, object event)

    cpdef object submit(self, Order order)

    cpdef object on_dt_over(self, object event) # nonblocking

    cpdef object get_snapshot(self, object event)
    