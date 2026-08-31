# cython.boundscheck(False) # 关闭边界检查
# cython.wraparound(False)  # 关闭负指数索引检查
# distutils: language = c++

from bt_core.utils.dateintern cimport ts2intdt

from libc.time cimport tm as c_tm, time_t

# timegm 是 POSIX 扩展, Cython 自带 libc.time.pxd 未声明, 手动 extern
# (glibc / macOS libc 均提供; Windows CRT 对应 _mkgmtime, 如需移植再适配)
cdef extern from "<time.h>" nogil:
    time_t timegm(c_tm *timeptr) noexcept

cdef const int64_t CYB_CKPT = 1598232600


cdef inline int32_t _days_from_civil(int32_t y, int32_t m, int32_t d) noexcept nogil:
    """
    y-m-d 距 1970-01-01 的真实日历天数 (新股豁免需要跨月/跨年差):
    填 struct tm 后交给 libc timegm (UTC 语义) 算出 Unix 秒, 除以 86400 取天。
    闰年/大小月全部由 C 库负责, 引擎侧不维护任何历法逻辑。
    约束: y-m-d 为合法日期 (调用方由 ymd int 拆出, 恒成立)。
    """
    cdef c_tm t
    t.tm_sec = 0
    t.tm_min = 0
    t.tm_hour = 0
    t.tm_mday = d
    t.tm_mon = m - 1
    t.tm_year = y - 1900
    t.tm_wday = 0
    t.tm_yday = 0
    t.tm_isdst = 0

    return <int32_t>(timegm(&t) // 86400)


cdef class Asset:
    """
        前5个交易日,科创板科创板还设置了临时停牌制度, 当盘中股价较开盘价上涨或下跌幅度首次达到30%、60%时，都分别进行一次临时停牌
        单次盘中临时停牌的持续时间为10分钟。每个交易日单涨跌方向只能触发两次临时停牌, 最多可以触发四次共计40分钟临时停牌。
        如果跨越14:57则复盘, 之后交易日20%
        科创板盘后固定价格交易 15:00 --- 15:30
        若收盘价高于买入申报指令，则申报无效；若收盘价低于卖出申报指令同样无效

        A股主板, 中小板首日涨幅最大为44%而后10%波动，针对不存在价格笼子（科创板，创业板后期对照科创板改革）
        科创板在连续竞价机制 ---买入价格不能超过基准价格(卖一的102%, 卖出价格不得低于买入价格98%)
        设立市价委托必须设立最高价以及最低价
        科创板盘后固定价格交易 --- 以后15:00收盘价格进行交易 --- 15:00 -- 15:30 时间优先原则，逐步撮合成交

        沪深主板单笔申报不超过100万股, 创业板单笔限价30万股;市价不超过15万股;定价申报100万股
        科创板单笔申报10万股;市价5万股;定价100万股
        风险警示板单笔买入申报50万股; 单笔卖出申报100万股, 退市整理期100万股
        科创板（竞价机制要求）---买入价格不能超过基准价格 卖一的102%,卖出价格不得低于买入价格98%
            
        科创板 --- 申报最小200股, 递增可以以1股为单位 | 卖出不足200股一次性卖出
        创业板 --- 申报100 倍数 | 卖出不足100, 全部卖出
        
        2020年8月24日 创业板ST由5% 变为 20% 
        20260706 ‌主板 ST 股涨跌幅调整为 10% / ‌盘后固定价格交易覆盖全A --- 15:05 至 15:30 /‌基金收盘 14:57- 15:00 集合竞价
    """
    def __init__(self, 
                bytes sid, 
                bytes name, 
                int32_t first_trading, 
                int32_t delist,
                bytes merger,
                float ratio
                ):
        self.sid = sid
        self.core.name = name
        self.core.first_trading = first_trading
        self.core.delist = delist
        self.core.merger = merger
        self.core.ratio = ratio

        if sid.startswith(b"688"):
            self.core.board = 2
            self.core.tick_size = 200
            self.core.increment = False
        elif sid.startswith(b"3"):
            self.core.board = 1
            self.core.tick_size = 100
            self.core.increment = True
        elif sid.startswith(b"4") or sid.startswith(b"8"):
            self.core.board = 3
            self.core.tick_size = 100
            self.core.increment = True
        else:
            self.core.board = 0  # 60 / 0
            self.core.tick_size = 100
            self.core.increment = True

    cdef double restricted(self, int64_t ts) noexcept:
        """
            涨跌幅限制:
            - 科创板(688)/创业板(3): 上市前5个交易日无涨跌幅限制, 之后20%
            - 主板(60/00): 10%
            - 创业板2020-08-24前为10%
        """
        cdef double thres
        cdef bint is_new_stock
        cdef int32_t current_ymd

        if self.core.first_trading > 0:
            current_ymd = ts2intdt(<double>ts)
            is_new_stock = (_days_from_civil(current_ymd // 10000, (current_ymd // 100) % 100, current_ymd % 100)
                            - _days_from_civil(self.core.first_trading // 10000,
                                               (self.core.first_trading // 100) % 100,
                                               self.core.first_trading % 100)) <= 5
        else:
            is_new_stock = False

        if self.core.board == 2:  
            if is_new_stock:
                return 1.0  
            thres = 0.2
        elif self.core.board == 1:  
            if is_new_stock and ts >= CYB_CKPT:
                return 1.0  
            thres = 0.2 if ts >= CYB_CKPT else 0.1
        else:  
            thres = 0.1
        return thres

    cdef AssetCore serialize(self):
        return self.core

    def __reduce__(self):#  class / args
        return (Asset, (self.sid, 
                        self.core.name, 
                        self.core.first_trading, 
                        self.core.delist,
                        self.core.merger,
                        self.core.ratio
            ))

    def __repr__(self):
        return f"Asset(sid={self.sid}, name={self.core.name}, first_trading={self.core.first_trading}, \
                        delist={self.core.delist}, merger={self.core.merger}, ratio={self.core.ratio})"