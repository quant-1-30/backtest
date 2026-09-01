# cython: language_level=3
# cython: boundscheck=False
# cython: wraparound=False
# cython: cdivision=True
# cython: language_level=3

import uuid

from bt_core.execution.core.finance.position cimport Position
from bt_core.execution.core.finance.trade cimport OrderExbitData, OrderExecutionBit
from bt_core.utils.dateintern cimport ts2intdt

from bt_protocol._protocol import AccountBody, Resp
from bt_protocol.schema.trade import vtAccount


cdef class Account:
    '''
    Keeps and updates the size and price of a position. The object has no
    relationship to any asset. It only keeps size and price.

    Member Attributes:
      - size (int): current size of the position
      - price (float): current price of the position

    The Position instances can be tested using len(position) to see if size
    is not null
    '''

    def __init__(self, 
                bytes experiment_id, 
                int64_t datetime=0, 
                double portfolio_value=0.0, 
                double cash=0.0, 
                double pnl=0.0, 
                double leverage=1.0, 
                double margin=0.0
                ):

        self.core.experiment_id = experiment_id
        self.core.datetime = <int64_t>datetime
        self.core.portfolio_value = portfolio_value
        self.core.cash = cash
        self.core.pnl = pnl 
        self.core.leverage = leverage
        self.core.margin = margin
        
        self.cached_uuid = uuid.UUID(bytes=experiment_id)
 
    cdef void set_cash(self, CashData body, bint reset=True):
        if reset:
            self.restore()
        self.core.cash = body.cash
        self.core.datetime = body.session

    cdef void restore(self):
        """set to zero"""
        self.core.datetime=0
        self.core.portfolio_value=0.0
        self.core.cash=0.0
        self.core.pnl=0.0
        self.core.leverage=1.0
        self.core.margin=0.0

    cdef void add_cash(self, double cash):
        cdef double _cash
        _cash = self.core.cash + cash
        if _cash < 0.0:
            raise ValueError(
                f"negative cash forbidden: cash={self.core.cash}, delta={cash}"
            )
        self.core.cash = _cash

    cdef void update(self, list trades):
        '''
        Updates the current Trade on Account
        '''
        cdef OrderExbitData core
        cdef OrderExecutionBit trade
        cdef double _val = 0.0
        cdef double _comm = 0.0
        cdef int64_t max_dt = 0

        for trade in trades:
            core = trade.core
            _val += core.executed_price * core.executed_size if core.isbuy else -1 * core.executed_price * core.executed_size
            _comm += core.comm
            max_dt = max(core.executed_dt, max_dt)

        self.core.cash -= (_val + _comm)
        # keep unix ts intraday (ts ordering survives multiple updates per day);
        # sync() is the single point that normalizes to ymd for persistence
        self.core.datetime = max_dt

    cdef void sync(self, int64_t tick, dict pobjs, dict closes):
        '''
        Updates the current position on Account
        '''
        cdef double _v = 0.0
        cdef double _cash = 0.0
        cdef double _pnl = 0.0
        cdef int64_t max_dt = max(self.core.datetime, tick)
        cdef Position p

        cdef double close
        cdef bytes sid
        for p in pobjs.values():
            sid = p.core.sid
            close = closes.get(sid, 0.0)
            _v += p.core.size * close
            _pnl += p.core.pnl + p.core.realized_pnl
            max_dt = max(p.core.datetime, max_dt)

        self.core.portfolio_value = _v
        self.core.pnl = _pnl
        # tick arrives as unix ts (simulate.on_dt_over); positions were already
        # normalized to ymd by their own on_dt_over. ts values dominate the max,
        # so a single conversion here covers the mixed inputs. Guard against
        # double conversion (ts2intdt(20240105) -> 19700823) when every input
        # is already ymd (restored-from-db account, ymd-only callers).
        if max_dt >= 100000000:  # unix ts magnitude; ymd ints stay below 1e8
            max_dt = ts2intdt(<double>max_dt)
        self.core.datetime = max_dt

    cdef Account clone(self):
        cdef Account obj = Account.__new__(Account) # only allocate memory
        cdef AccountCoreData core

        core.experiment_id = self.core.experiment_id
        core.portfolio_value=self.core.portfolio_value 
        core.cash=self.core.cash
        core.pnl=self.core.pnl
        core.leverage=self.core.leverage 
        core.margin=self.core.margin
        core.datetime = self.core.datetime
        obj.core = core
        return obj

    cdef object serialize(self):
        cdef object body, resp

        body = AccountBody(experiment_id=self.core.experiment_id, datetime=self.core.datetime, portfolio_value=self.core.portfolio_value, 
                            cash=self.core.cash, pnl=self.core.pnl, leverage=self.core.leverage, margin=self.core.margin)
        resp = Resp(body=body)
        return resp

    cdef object to_schema(self): # not suit for snapshot because sqlchemy object has state
        # cdef object experiment_id = uuid.UUID(self.core.experiment_id.decode("utf-8"))
        # cdef object experiment_id = uuid.UUID(bytes=self.core.experiment_id)  # 16 / 32 / 4

        return vtAccount(experiment_id=self.cached_uuid, 
                         datetime=self.core.datetime, 
                         portfolio_value=self.core.portfolio_value, 
                         cash=self.core.cash,
                         pnl=self.core.pnl, 
                         leverage=self.core.leverage, 
                         margin=self.core.margin)

    cdef AccountCoreData get_snapshot(self):
        return self.core

    def __reduce__(self):#  class / args
        return (Account, (self.core.experiment_id, 
                          self.core.datetime, 
                          self.core.portfolio_value, 
                          self.core.cash, 
                          self.core.pnl, 
                          self.core.leverage, 
                          self.core.margin))
    
    def __repr__(self):
        template = "Account(experiment_id={experiment_id} ," \
                   "datetime={datetime} ," \
                   "portfolio_value={portfolio_value} ," \
                   "cash={cash} ," \
                   "pnl={pnl} ," \
                   "leverage={leverage} ," \
                   "margin={margin})"
        return template.format(
            experiment_id=self.core.experiment_id,
            datetime=self.core.datetime,
            portfolio_value=self.core.portfolio_value,
            cash=self.core.cash,
            pnl=self.core.pnl,
            leverage=self.core.leverage,
            margin=self.core.margin,
        )

