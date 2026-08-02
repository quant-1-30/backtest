# distutils: language = c++
# cython: language_level=3

from libc.stdint cimport uint8_t, int32_t, int64_t
from libc.time cimport time_t, localtime, mktime, tm, gmtime


cdef const int64_t HOURS_PER_DAY = 24 # DEF is desprecated

cdef enum MarketConstants: # DEF is desprecated in Cython 3.x / cdef enum / cdef int64_t 
    MINUTES_PER_HOUR = 60
    SECONDS_PER_MINUTE = 60
    OPEN_OFFSET = 34200
    CLOSE_OFFSET = 54000
    SECONDS_PER_DAY = 86400 
    SHANGHAI_OFFSET = 28800


cdef enum:
    TF_TICK=0
    TF_MicroSeconds = 1
    MilliSecond = 2
    TF_Seconds = 3
    TF_Minutes = 4
    TF_Days = 5
    TF_Weeks = 6
    TF_Months = 7
    TF_Years = 8
    TF_NoTimeFrame = 9


cdef struct MarketTime:
    int64_t open_ts
    int64_t close_ts


# C typedef / cython ctypedef
cpdef object num2date(double ts, bint localize=?) # *

cpdef double date2num(object dt)

cpdef int32_t ts2intdt(double ts) # only cdef nogil and bint native=?

cpdef object tzparse(str tz)

cdef MarketTime market_utc(int64_t ts) noexcept nogil

cpdef int64_t get_dt_cmpkey(double dt_ts, int64_t timeframe, int64_t compression=?)

cdef double _get_subday_cmpkey_c(double dt_ts, tm* tm_ptr, int64_t timeframe, int64_t compression)
