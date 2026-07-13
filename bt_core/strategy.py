#!/usr/bin389/env python
# -*- coding: utf-8; py-indent-offset:4 -*-
###############################################################################
#
# Copyright (C) 2015-2023 Daniel Rodriguez
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
###############################################################################
import numpy as np
import math
import warnings
import collections
import itertools
import operator
import json
import hashlib
from collections import defaultdict

import bt_core as bt
from .lineiterator import LineIterator, StrategyBase
from .lineseries import LineSeriesStub
from .metabase import with_metaclass, findowner
from .utils import AutoOrderedDict, fast_uuid4_bytes, ts2intdt
from .utils.comparsion import check_gt_zero, check_lt_zero, check_nanzero
from .shm import SharedRingBuffer

from bt_protocol._protocol import OrderBody, SnapshotBody

MAXINT = np.iinfo(np.int_).max


class MetaStrategy(StrategyBase.__class__):
    _indcol = dict()

    def __new__(meta, name, bases, dct):
        return super(MetaStrategy, meta).__new__(meta, name, bases, dct)

    def __init__(cls, name, bases, dct):
        '''
        Class has already been created ... register subclasses
        '''
        # Initialize the class
        super(MetaStrategy, cls).__init__(name, bases, dct)

        if not cls.aliased and \
           name != 'Strategy' and not name.startswith('_'):
            cls._indcol[name] = cls

    def donew(cls, *args, **kwargs):
        _obj, args, kwargs = super(MetaStrategy, cls).donew(*args, **kwargs)

        # Find the _owner and store it
        _obj.env = env = cerebro = findowner(_obj, bt.cerebro.Cerebro)
        _obj.pnc = env.pnc
        _obj.log_shm = env.log_shm

        # register strategy to generate unique id and setup shm for strategy
        _obj.store = store = env.store
        _obj.experiment_id = experiment_id = store.register(_obj.__class__.__name__, env.cerebro_id)

        shm_name = hashlib.md5(experiment_id).hexdigest()[:24] # uuid.UUID(bytes=experiment_id) # file name 36 too long
        _obj.shm_chan = SharedRingBuffer(shm_name=str(shm_name), capacity=10000000, is_creator=True)
        return _obj, args, kwargs

    def dopreinit(cls, _obj, *args, **kwargs):
        _obj, args, kwargs = \
            super(MetaStrategy, cls).dopreinit(_obj, *args, **kwargs)
        
        _obj._minperiods = list()
        _obj.analyzers = list() # ItemCollection()
        _obj.stats = {} 
        _obj.metrics = [] 

        _obj.snapshot = None
        return _obj, args, kwargs

    def dopostinit(cls, _obj, *args, **kwargs):
        _obj, args, kwargs = \
            super(MetaStrategy, cls).dopostinit(_obj, *args, **kwargs)

        _obj._periodset()
        return _obj, args, kwargs


class Strategy(with_metaclass(MetaStrategy, StrategyBase)):
    '''
    Base class to be subclassed for user defined strategies.
    '''
    plotinfo = dict(plot=True, plotname="Strategy")

    _ltype = LineIterator.StratType

    _filler = b"oco"

    lines = ('datetime',) 
    
    def _settz(self, tz):
        self.lines.datetime._settz(tz)

    def _periodset(self):
        dataids = [id(data) for data in self.datas]

        _dminperiods = collections.defaultdict(list)
        for lineiter in self._lineiterators[LineIterator.IndType]:
            clk = getattr(lineiter, '_clock', None)
            if clk is None:
                clk = getattr(lineiter._owner, '_clock', None)
                if clk is None:
                    continue

            while True:
                if id(clk) in dataids:
                    break  # already top-level clock (data feed)

                clk2 = getattr(clk, '_clock', None)
                if clk2 is None:
                    clk2 = getattr(clk._owner, '_clock', None)

                if clk2 is None:
                    break 

                clk = clk2

            if clk is None:
                continue  

            if isinstance(clk, LineSeriesStub):
                clk = clk.lines[0]

            _dminperiods[clk].append(lineiter._minperiod)

        # calc data minperiods
        for data in self.datas:
            dlminperiods = _dminperiods[data]
            for l in data.lines:  # search each line for min periods
                if l in _dminperiods:
                    dlminperiods += _dminperiods[l]  # found, add it

            # keep the reference to the line if any was found
            _dminperiods[data] = [max(dlminperiods)] if dlminperiods else []

            dminperiod = max(_dminperiods[data] or [data._minperiod])
            self._minperiods.append(dminperiod)
    
    def minbuffer(self):
        _minperiod = self._minperiod
        self.lines.minbuffer(_minperiod)

        for data in self.datas:
            data.minbuffer(_minperiod) # make sure data minperiod is at least strategy's  
        
        for itcls in self._lineiterators:
            for it in self._lineiterators[itcls]:
                it.minbuffer(_minperiod)

    def qbuffer(self, savemem=1):
        '''Enable the memory saving schemes. Possible values for ``savemem``:

          0: No savings. Each lines object keeps in memory all values

          1: All lines objects save memory, using the strictly minimum needed
        '''
        super().qbuffer(savemem=savemem)
        self.minbuffer()
    
    def _start(self, savemem, **kwargs):
        '''Called right before the bt_coreing is about to be started.'''
        self._filler = kwargs.get("filler", b"oco")

        # linebuffer qbuffer update maxlen with _minperiod
        self.qbuffer(savemem=savemem) 
        self._periodrecalc()

        # self._minperstatus = MAXINT  # start in prenext
        self._dlens = np.array([len(data) for data in self.datas])

        # initialize snapshot and shm_chan
        self.set_cash(**kwargs)
        self.snapshot = self.store.get_snapshot(self.experiment_id)
        self.shm_chan.publish_sentinel(0) 
        
        for analyzer in self.analyzers: # itertools.chain(self.analyzers, self._slave_analyzers)
            analyzer._start()

        # setup metric
        self._setupMetrics()

        # pnc _start
        trading_days = self.data0.benchmark_dret["day"].to_list()
        self.pnc._start(trading_days)

    def set_cash(self, **kwargs):
        cash = kwargs.pop("cash", 100000)
        session = kwargs["fromdate"]
        snapshot = self.store.set_cash(self.experiment_id, session, cash)
        self.shm_chan.publish_snapshot(snapshot)

    def _setupMetrics(self):
        for _line in self.getindicators_lines():
            line_name = _line.plotinfo.plotname or _line.__class__.__name__

            for i, line_alias in enumerate(_line.lines.getlinealiases()):
                metric_name = f"ind_{line_name}_{line_alias}".encode("utf-8")
                self.metrics.append(
                    (_line.lines[i], metric_name)
                )

    def _addindicator(self, indcls, *indargs, **indkwargs):
        indcls(*indargs, **indkwargs) # postinit will take care of the rest

    def _addanalyzer(self, ancls, *anargs, **ankwargs):
        anname = ankwargs.pop('_name', '') or ancls.__name__.lower()
        # nsuffix = next(self._alnames[anname])
        # anname += str(nsuffix or '')  # 0 (first instance) gets no suffix
        analyzer = ancls(*anargs, **ankwargs)
        # self.analyzers.append(analyzer, anname)
        self.analyzers.append(analyzer)
        self.stats[anname] = analyzer
        return analyzer

    def _getminperstatus(self):
        dlens = map(operator.sub, self._minperiods, map(len, self.datas))
        # self._minperstatus = minperstatus = np.max(dlens)
        minperstatus = max(dlens)
        return minperstatus

    def clk_update(self):
        newdlens = np.array([len(d) for d in self.datas])
        if any(nl > l for l, nl in zip(self._dlens, newdlens)):
            self.forward()

        self.lines.datetime[0] = np.max([d.datetime[0]
                                     for d in self.datas if len(d)])      
        self._dlens = newdlens

    def on_dt_over(self, last_dts: int, dts: int):
        snapshot = self.store.on_dt_over(self.experiment_id, last_dts, dts) 
        if snapshot:
            self.shm_chan.publish_snapshot(snapshot)
            self.snapshot = snapshot  
 
        self.shm_chan.publish_sentinel(dts)

        for analyzer in self.analyzers:
            if hasattr(analyzer, 'on_dt_over'):
                analyzer.on_dt_over(dts, snapshot)

        for _d in self.datas:
            _d.on_dt_over(dts)

    def notify_metrics(self, last_dts: int): 
        """
        This method is called when a timer event is triggered. 
        It can be used to log indicator metrics and notify analyzers that are interested in timer events.
        """
        self.shm_chan.publish_sentinel(last_dts)

        for ind_line, metric in self.metrics:
            val = ind_line[0]
            if np.isnan(val):
                continue

            self.log_shm.publish_metric(metric, val, last_dts)

        for analyzer in self.analyzers:
            if hasattr(analyzer, 'notify_metrics'):
                analyzer.notify_metrics(last_dts) # get_shm_events
        
        for _d in self.datas:
            _d.notify_metrics(last_dts)

    def _next(self):
        self.clk_update() # advance differ from lineiterator _clk_update
        super(Strategy, self)._next()

    def on_risk(self, current_dts: int): # position risk is not applied
        snapshot = self.get_snapshot()
        sell_plans = self.pnc.on_risk(snapshot, self.stats)

        if sell_plans:
            self.sell(sell_plans) 

    def on_trade(self, current_dts: int): 
        print(f"[Strategy] Trigger on {current_dts}")

        current_day = ts2intdt(current_dts)
        topk = self.data[-1].get_topk(current_day)

        snapshot = self.get_snapshot()
        _plan = self.pnc.generate_plan(current_day, topk, snapshot) 
        self.buy(_plan["sell"])
        self.sell(_plan["buy"])

    def buy(self, buys, execType=0):
        '''Create a buy (long) order and send it to the broker 
          
          - ``exectype`` (default: ``int``)

            Possible values:

            - ``0 `` alias for Order.Market. An order will be executed
              on created_dt 

            - ``1 ``. alias for Order.Limit. An order be executed at the given
                price`` or better. e.g.  oco / occ / smooth / trend

            - ``2 ``. alias for Order.Stop is not supported by A stock 

            - ``3``. alias for StopLimit is not supported by A stock  

          - ``filler``: logic for simulate executed price on created_dt
            
            Possible values:

            - ``oco``. An order which can only be executed on order created_dt 
                where open price be cheated on

            - ``occ``. An order which can only be executed on order created_dt
                where close price be cheated on

            - ``smooth``. An order which can only be executed on order created_dt
                where mean of ohlc

            - ``likehood``. An order which can only be executed on order created_dt 
                where high for buy order or low for sell order

          - ``**kwargs``: additional broker implementations may support extra
            parameters. ``backtrader`` will pass the *kwargs* down to the
            created order objects

        Returns:
          - the submitted order
        '''
        if not buys: return
        
        created_dt = self.lines.datetime[0]
        created_dt = 0.0 if np.isnan(created_dt) else created_dt

        for bplan in buys:
            order_id = fast_uuid4_bytes()
            order = OrderBody(
                        sid=bplan["sid"],
                        order_id=order_id,
                        sizer_ratio=bplan["weight"], 
                        price=bplan.get("price", 0.0),
                        order_type=0,
                        exec_type=bplan["execType"], 
                        created_dt=int(created_dt),
                        filler=self._filler)
            snapshot = self.store.submit(self.experiment_id, order)
            trades = snapshot.trades
            if trades:
                print("buy trades: ", len(trades))
                self.shm_chan.publish_snapshot(snapshot) # publish trade to shared memory for writer to consume

            self.shm_chan.publish_order(order)

        self.snapshot = snapshot  
        
    def sell(self, sells):
        '''
        To create a selll (short) order and send it to the broker

        See the documentation for ``buy`` for an explanation of the parameters

        Returns: the submitted order
        '''
        if not sells: return

        filled = {}
        created_dt = self.lines.datetime[0]
        created_dt = 0.0 if np.isnan(created_dt) else created_dt

        for splan in sells:
            order_id = fast_uuid4_bytes()
            sid = splan["sid"]
            order = OrderBody(
                        sid=sid,
                        order_id=order_id,
                        sizer_ratio=splan["weight"], 
                        price=splan.get("price", 0.0),
                        order_type=1,
                        exec_type=splan["execType"], 
                        created_dt=int(created_dt),
                        filler=self._filler)
        
            snapshot = self.store.submit(self.experiment_id, order)
            trades = snapshot.trades
            if trades:
                print("sell trades: ", len(trades))
                self.shm_chan.publish_snapshot(snapshot) 
                filled[sid] = trades 
            
            self.shm_chan.publish_order(order)

        self.pnc.on_execute(filled)
        self.snapshot = snapshot  

    def get_snapshot(self)-> SnapshotBody: 
        return self.snapshot

    def cancel(self, order_id):
        '''Cancels the order in the broker'''
        self.store.cancel(order_id)
   
    def _stop(self):
        self.stop()

    def stop(self):
        '''Called right before the bt_coreing is about to be stopped'''
        last_dts = self.data.datetime[0]
        self.on_dt_over(last_dts, 0)

        self.store.stop()

        self.shm_chan.close()
        print("shm_chan close")
        self.shm_chan.unlink()
        print("shm_chan unlink")
    

class MetaSigStrategy(Strategy.__class__): # Stragey元类 / obj.__class__ 类 / class.__class__ 元类

    def __new__(meta, name, bases, dct): # map user defined next to custom to be able to call own method before
        # if 'next' in dct:
        #     dct['_next_custom'] = dct.pop('next')

        cls = super(MetaSigStrategy, meta).__new__(meta, name, bases, dct)
        # cls._next = cls._next_catch # after class creation remap _next_catch to be next
        return cls

    def dopreinit(cls, _obj, *args, **kwargs):
        _obj, args, kwargs = \
            super(MetaSigStrategy, cls).dopreinit(_obj, *args, **kwargs)
        
        _obj._signals = collections.defaultdict(list)
        return _obj, args, kwargs

    def dopostinit(cls, _obj, *args, **kwargs):
        for sigtype, sigcls, sigargs, sigkwargs in _obj.p.signals:
            _obj._signals[sigtype].append(sigcls(*sigargs, **sigkwargs)) # autoregister signal indicator
        
        _obj, args, kwargs = \
            super(MetaSigStrategy, cls).dopostinit(_obj, *args, **kwargs)

        # Record types of signals
        _obj._long = bool(_obj._signals[bt.SIGNAL_LONG])
        _obj._short = bool(_obj._signals[bt.SIGNAL_SHORT])

        _obj._longexit = bool(_obj._signals[bt.SIGNAL_LONGEXIT])
        _obj._shortexit = bool(_obj._signals[bt.SIGNAL_SHORTEXIT])
        return _obj, args, kwargs


class SignalStrategy(with_metaclass(MetaSigStrategy, Strategy)):
    '''This subclass of ``Strategy`` is meant to to auto-operate using
    **signals**.

    *Signals* are usually indicators and the expected output values:

      - ``> 0`` is a ``long`` indication

      - ``< 0`` is a ``short`` indication

    There are 5 types of *Signals*, broken in 2 groups, short is not suppored

    **Main Group**:

      - ``LONG``:
        - ``long`` indications are taken to go long

          - If a ``LONGEXIT`` (see below) signal is in the system it will be
            used to exit the long

    **Exit Group**:

      - ``LONGEXIT``: ``short`` indications are taken to exit ``long``
        positions

    Params:

      - ``signals`` (default: ``[]``): a list/tuple of lists/tuples that allows
        the instantiation of the signals and allocation to the right type

        This parameter is expected to be managed through ``cerebro.add_signal``

      - ``_accumulate`` (default: ``False``): allow to enter the market
        (long/short) even if already in the market

      - ``_data`` (default: ``None``): if multiple datas are present in the
        system which is the target for orders. This can be

        - ``None``: The first data in the system will be used

        - An ``int``: indicating the data that was inserted at that position

        - An ``str``: name given to the data when creating it (parameter
          ``name``) or when adding it cerebro with ``cerebro.adddata(...,
          name=)``
    '''
    params = (
        ('signals', []),
        ('_accumulate', True),
    )
    
    def _start(self, savemem, **kwargs):
        # self._sentinel = None  # sentinel for order concurrency
        super(SignalStrategy, self)._start(savemem, **kwargs)

    def signal_add(self, sigtype, signal):
        self._signals[sigtype].append(signal)

    def _next(self): # next
        super(SignalStrategy, self)._next()

    def on_trade(self, current_dts: int): 
        sigs = self._signals
        nosig = [[0.0]]

        # Calculate current status of the signals
        # ls_long = all(x[0] > 0.0 for x in sigs[bt.SIGNAL_LONGSHORT] or nosig)
        # ls_short = all(x[0] < 0.0 for x in sigs[bt.SIGNAL_LONGSHORT] or nosig)

        l_enter0 = check_gt_zero(sigs[bt.SIGNAL_LONG], nosig)
        l_enter1 = check_lt_zero(sigs[bt.SIGNAL_LONG_INV], nosig)
        l_enter2 = check_nanzero(sigs[bt.SIGNAL_LONG_ANY], nosig)
        l_enter = l_enter0 or l_enter1 or l_enter2

        s_enter0 = check_lt_zero(sigs[bt.SIGNAL_SHORT], nosig)
        s_enter1 = check_gt_zero(sigs[bt.SIGNAL_SHORT_INV], nosig)
        s_enter2 = check_nanzero(sigs[bt.SIGNAL_SHORT_ANY], nosig)
        s_enter = s_enter0 or s_enter1 or s_enter2

        l_ex0 = check_lt_zero(sigs[bt.SIGNAL_LONGEXIT], nosig)
        l_ex1 = check_gt_zero(sigs[bt.SIGNAL_LONGEXIT_INV], nosig)
        l_ex2 = check_nanzero(sigs[bt.SIGNAL_LONGEXIT_ANY], nosig)
        l_exit = l_ex0 or l_ex1 or l_ex2

        s_ex0 = check_gt_zero(sigs[bt.SIGNAL_SHORTEXIT], nosig)
        s_ex1 = check_lt_zero(sigs[bt.SIGNAL_SHORTEXIT_INV], nosig)
        s_ex2 = check_nanzero(sigs[bt.SIGNAL_SHORTEXIT_ANY], nosig)
        s_exit = s_ex0 or s_ex1 or s_ex2

        # but only if no "xxxExit" exists
        l_rev = not self._longexit and s_enter # reverse --- longexit
        s_rev = not self._shortexit and l_enter # reverse --- shortexit

        # Opposite of individual long and short
        l_leav0 = check_lt_zero(sigs[bt.SIGNAL_LONG], nosig)
        l_leav1 = check_gt_zero(sigs[bt.SIGNAL_LONG_INV], nosig)
        l_leav2 = check_nanzero(sigs[bt.SIGNAL_LONG_ANY], nosig)
        l_leave = l_leav0 or l_leav1 or l_leav2

        s_leav0 = check_gt_zero(sigs[bt.SIGNAL_SHORT], nosig)
        s_leav1 = check_lt_zero(sigs[bt.SIGNAL_SHORT_INV], nosig)
        s_leav2 = check_nanzero(sigs[bt.SIGNAL_SHORT_ANY], nosig)
        s_leave = s_leav0 or s_leav1 or s_leav2

        # Invalidate long leave if longexit signals are available
        l_leave = not self._longexit and l_leave
        # Invalidate short leave if shortexit signals are available
        s_leave = not self._shortexit and s_leave

        # execute pnc
        has_signal = l_enter or l_exit or l_rev or l_leave or s_enter or s_exit or s_rev or s_leave
        
        if not has_signal: 
            return

        current_day = ts2intdt(current_dts)
        topk = self.datas[-1].get_topk(current_day)
        
        snapshot = self.get_snapshot()

        _plan = self.pnc.generate_plan(current_dts, topk, snapshot)
        print("generate_plan", _plan)

        if l_enter:
            if self.p._accumulate:
                self.buy(_plan[b"buy"])
        # elif l_exit or l_rev or l_leave:
        else:
            self.sell(_plan[b"sell"]) 
