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
from libc.stdint cimport int64_t, int32_t


cdef struct AssetCore:
    # 8 bytes align
    cpp_string name
    cpp_string merger       
    double ratio            
    # 4 bytes align
    int32_t first_trading   
    int32_t delist         
    int32_t tick_size 
    int32_t board     

    bint increment          

cdef class Asset:
    cdef readonly AssetCore core
    cdef bytes sid
    cdef str name

    cdef double restricted(self, int64_t ts) noexcept
    
    cdef AssetCore serialize(self)
