from libcpp.unordered_map cimport unordered_map
from libcpp.string cimport string as cpp_string


cdef class Sizer:

    cdef unordered_map[cpp_string, double] getsizing(
        self, unordered_map[cpp_string, double] topk_info,  
        object snapshot, 
        bint isbuy) except *

    cpdef unordered_map[cpp_string, double] _getsizing(
        self, 
        unordered_map[cpp_string, double] topk_info, 
        object snapshot, 
        bint isbuy) except *
