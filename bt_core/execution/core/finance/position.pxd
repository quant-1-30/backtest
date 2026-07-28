# -------------------------------------------------------------------------------------------------
#  Copyright (C) 2015-2025 Nautech Systems Pty Ltd. All rights reserved.
#  https://nautechsystems.io
#
#  Licensed under the GNU Lesser General Public License Version 3.0 (the "License");
#  You may not use this file except in compliance with the License.
#  You may obtain a copy of the License at https://www.gnu.org/licenses/lgpl-3.0.en.html
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
# -------------------------------------------------------------------------------------------------
from libcpp.string cimport string as cpp_string
from libcpp.vector cimport vector
from libc.stdint cimport int64_t, int32_t

from bt_core.execution.core.finance.common cimport EventItem
from bt_core.execution.core.finance.trade cimport OrderExecutionBit
from bt_core.execution.core.finance.asset cimport Asset, AssetCore


cdef struct PositionCoreData:
    cpp_string experiment_id
    cpp_string sid
    int64_t datetime
    int32_t size
    int32_t available
    double cost_basis
    double pnl              # unrealized pnl = size * (price - cost_basis)
    double realized_pnl     # accumulated realized pnl from sells
    int64_t created_dt
    

cdef class Position:
    cdef readonly PositionCoreData core
    cdef Asset asset
    cdef object cached_uuid

    cdef int32_t get_available(self)
    
    cdef void update(self, OrderExecutionBit orderbit)
    
    cdef _execute(self, OrderExecutionBit orderbit)
    
    cdef double _process_event(self, EventItem item)
    
    cdef double process_events(self, vector[EventItem]& events)
    
    cdef void _handle_merger(self, bytes target_sid, float close, float ratio)

    cdef void _dt_over(self, int32_t end_dt, double close)
    
    cdef void on_dt_over(self, int32_t end_dt, double close)
    
    cdef Position clone(self)
    
    cdef object serialize(self)
    
    cdef object to_schema(self)
    
    cdef PositionCoreData get_snapshot(self)