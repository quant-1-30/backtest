from libc.stdint cimport int64_t


cdef struct CashData:
    double cash
    int session

    
cdef struct AdjustmentData:
    double bonus_share
    double transfer
    double bonus


cdef struct RightData:
    double ratio # not divide by 10
    double price


cdef struct EventItem:
    int event_type  # 0 为 adjustment, 1 为 right
    AdjustmentData adj
    RightData rgt


cdef enum FillerType:
    OCO = 0
    OCC = 1
    Smooth = 2
    Likely = 3


cdef enum Exchange:
    SSE = 0              # Shanghai Stock Exchange
    SZSE = 1             # Shenzhen Stock Exchange
    BSE = 2              # Beijing Stock Exchange


