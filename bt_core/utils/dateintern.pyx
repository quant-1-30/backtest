# distutils: language = c++
# cython: language_level=3

import pytz
import numpy as np
import datetime as py_datetime

from libc.stdint cimport uint8_t, int64_t 

from libc.time cimport time_t, localtime_r, mktime, tm, gmtime, gmtime_r
from libc.math cimport floor

from cpython.datetime cimport datetime, import_datetime, \
    PyDateTime_DATE_GET_HOUR, \
    PyDateTime_DATE_GET_MINUTE, \
    PyDateTime_DATE_GET_SECOND, \
    PyDateTime_DATE_GET_MICROSECOND

import_datetime()

cdef object UTC_TZ = py_datetime.timezone.utc
cdef object SHANGHAI_TZ = py_datetime.timezone(py_datetime.timedelta(seconds=SHANGHAI_OFFSET))

# === C timegm ignore OS tz and mktime affected by localtz ===
cdef extern from "time.h" nogil:
    time_t timegm(tm *timeptr)

cdef double _timegm_safe(tm* t_struct) nogil:
    return <double>timegm(t_struct)


cpdef object num2date(double ts, bint localize=True): 
    """
        UTC Timestamp ---> Python datetime 
    :param ts: UTC Timestamp
    :param localize: Asia/Shanghai (True) / UTC (False)
    """
    cdef int64_t ix = <int64_t>ts
    if np.isnan(ts) or ts <= 0:
        return py_datetime.datetime.min

    if 10000000 < ix < 30000000:
        year = <int>(ix // 10000)
        month = <int>((ix % 10000) // 100)
        day = <int>(ix % 100)
        return py_datetime.datetime(year, month, day)

    # binding timezone
    dt_aware = py_datetime.datetime.fromtimestamp(ts, tz=UTC_TZ)
    
    if localize:
        return dt_aware.astimezone(SHANGHAI_TZ)
    return dt_aware


cpdef double date2num(object dt):
    """
    Python Datetime (Aware or Naive) --> UTC Timestamp
    """
    if dt.tzinfo is not None:
        return dt.timestamp()

    return dt.replace(tzinfo=SHANGHAI_TZ).timestamp()


cpdef int32_t ts2intdt(double ts):  
    """
    param ts: UTC Timestamp
    """
    cdef time_t rawtime = <time_t>(ts + SHANGHAI_OFFSET) 
    cdef tm info
    gmtime_r(&rawtime, &info) 
    return (info.tm_year + 1900) * 10000 + (info.tm_mon + 1) * 100 + info.tm_mday


cdef MarketTime market_utc(int64_t ts) noexcept nogil:
    """
    param ts :UTC Timestamp
    """
    cdef MarketTime mt
    # 1. bj 00:00:00
    cdef int64_t bj_time = ts + SHANGHAI_OFFSET
    cdef int64_t bj_day_start = (bj_time // 86400) * 86400
    
    # 2. bj 00:00:00 ---> UTC 
    cdef int64_t local_day_start_utc = bj_day_start - SHANGHAI_OFFSET
    
    # 3. offset
    mt.open_ts = local_day_start_utc + OPEN_OFFSET
    mt.close_ts = local_day_start_utc + CLOSE_OFFSET
    return mt
 

cpdef int64_t get_dt_cmpkey(double dt_ts, int64_t timeframe, int64_t compression=1):
    cdef:
        time_t ts_sec
        tm t_struct 
        double result_ts
        int64_t days_to_sunday
        
    if timeframe == TF_NoTimeFrame:
        return <int64_t>dt_ts

    ts_sec = <time_t>(dt_ts + SHANGHAI_OFFSET) # utc ---> asia

    gmtime_r(&ts_sec, &t_struct) 

    if timeframe == TF_Years:
        t_struct.tm_mon = 11    
        t_struct.tm_mday = 31   
        t_struct.tm_hour = 23
        t_struct.tm_min = 59
        t_struct.tm_sec = 59
        
        result_ts = _timegm_safe(&t_struct)

    elif timeframe == TF_Months: 
        t_struct.tm_mon += 1    
        t_struct.tm_mday = 0    
        t_struct.tm_hour = 23
        t_struct.tm_min = 59
        t_struct.tm_sec = 59
        result_ts = _timegm_safe(&t_struct)

    elif timeframe == TF_Weeks: 
        days_to_sunday = (7 - t_struct.tm_wday) % 7
        t_struct.tm_mday += days_to_sunday
        t_struct.tm_hour = 23
        t_struct.tm_min = 59
        t_struct.tm_sec = 59
        result_ts = _timegm_safe(&t_struct)

    elif timeframe == TF_Days:
        t_struct.tm_hour = 23
        t_struct.tm_min = 59
        t_struct.tm_sec = 59
        result_ts = _timegm_safe(&t_struct)
    else:
        result_ts = _get_subday_cmpkey_c(dt_ts + SHANGHAI_OFFSET, &t_struct, timeframe, compression)

    # asia --> utc
    return <int64_t>(result_ts - SHANGHAI_OFFSET)


# === _timegm_safe replace mktime ===
cdef double _get_subday_cmpkey_c(double bj_ts, tm* tm_ptr, int64_t timeframe, int64_t compression):
    cdef:
        long point = 0
        long ph=0, pm=0, ps=0, pus=0
        long extradays = 0
        double result_ts
        
    point = tm_ptr.tm_hour * 60 + tm_ptr.tm_min

    if timeframe < TF_Minutes: 
        point = point * 60 + tm_ptr.tm_sec

    if timeframe < TF_Seconds: 
        pus = <long>((bj_ts - floor(bj_ts)) * 1e6)
        point = point * 1000000 + pus 

    point = point // compression  
    point += 1
    point *= compression

    if timeframe == TF_Minutes:
        ph = point // 60
        pm = point % 60
        ps = 0; pus = 0
        
    elif timeframe == TF_Seconds:
        ph = point // 3600
        pm = (point % 3600) // 60
        ps = point % 60
        pus = 0

    extradays = 0
    if ph > 23:
        extradays = ph // 24
        ph = ph % 24

    tm_ptr.tm_hour = <int64_t>ph
    tm_ptr.tm_min = <int64_t>pm
    tm_ptr.tm_sec = <int64_t>ps
    
    if extradays > 0:
        tm_ptr.tm_mday += <int64_t>extradays

    result_ts = _timegm_safe(tm_ptr)

    if timeframe == TF_Minutes:
        result_ts -= 60.0
    elif timeframe == TF_Seconds:
        result_ts -= 1.0

    return result_ts

cpdef object tzparse(str tz):
    # If no object has been provided by the user and a timezone can be
    # found via contractdtails, then try to get it from pytz, which may or
    # may not be available.
    if not tz:
        return py_datetime.timezone.utc

    tzinfo = pytz.timezone(tz)
    return tzinfo
