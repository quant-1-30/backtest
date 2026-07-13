from libc.stdint cimport int64_t, int32_t


cpdef enum TimerEvent:
    METRIC = 0   
    RISK = 1    
    TRADE = 2 
    EOD = 3       
    UNKOWN = 4


cpdef enum Session:
    SESSION_START = 0
    SESSION_END = 1


cdef class Timer:
    cdef object when
    cdef int32_t offset
    cdef int32_t repeat
    cdef list weekdays
    cdef list monthdays
    cdef bint weekcarry
    cdef bint monthcarry
    cdef object allow
    cdef object _tzdata
    
    cdef bint _isdata
    cdef double _dtwhen
    cdef object _dwhen
    cdef object _lastcall
    cdef object _rstwhen
    
    cdef int32_t _curmonth
    cdef int32_t _curweek
    cdef int32_t _curmonth_idx
    cdef int32_t _curweek_idx

    cdef object _curdate
    cdef double _nextdteos
    cdef public int32_t event_type

    cpdef void start(self, object data)

    cdef void _reset_when(self, object ddate=?)

    cdef bint _check_month(self, int32_t dday, int32_t dmonth)

    cdef bint _check_week(self, int32_t dwkday, int32_t dweek)

    cpdef bint check(self, double dt)
    