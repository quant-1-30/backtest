#!/usr/bin/env python
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
import datetime
import pprint as pp
import numpy as np
from collections import OrderedDict

import bt_core as bt
from bt_core.dataseries import TimeFrame
from bt_core.metabase import with_metaclass, MetaParams, findowner
from bt_protocol._protocol import SnapshotBody


class MetaAnalyzer(MetaParams):
    def donew(cls, *args, **kwargs):
        '''
        Intercept the strategy parameter
        '''
        # Create the object and set the params in place
        _obj, args, kwargs = super(MetaAnalyzer, cls).donew(*args, **kwargs)

        _obj._children = list()

        # inherit from strategy
        _obj._owner = strategy = findowner(_obj, bt.Strategy)
        _obj.datas = strategy.datas
       
        # setup shm for analyzers
        _obj.log_shm = strategy.log_shm
        _obj.shm = strategy.shm_chan

        _obj._parent = findowner(_obj, Analyzer)

        # For each data add aliases: for first data: data and data0
        if _obj.datas:
            _obj.data = data = _obj.datas[0] # _clock

            for l, line in enumerate(data.lines):
                linealias = data._getlinealias(l)
                if linealias:
                    setattr(_obj, 'data_%s' % linealias, line)
                setattr(_obj, 'data_%d' % l, line)

            for d, data in enumerate(_obj.datas):
                setattr(_obj, 'data%d' % d, data)

                for l, line in enumerate(data.lines):
                    linealias = data._getlinealias(l)
                    if linealias:
                        setattr(_obj, 'data%d_%s' % (d, linealias), line)
                    setattr(_obj, 'data%d_%d' % (d, l), line)
        
        # _obj.create_analysis()
        return _obj, args, kwargs

    def dopostinit(cls, _obj, *args, **kwargs):
        _obj, args, kwargs = \
            super(MetaAnalyzer, cls).dopostinit(_obj, *args, **kwargs)

        if _obj._parent is not None:
            _obj._parent._register(_obj) # analyzer with analyzer

        # Register to shm
        shm_id = _obj.shm.register_consumer()
        _obj.shm_id = shm_id
        return _obj, args, kwargs


class Analyzer(with_metaclass(MetaAnalyzer, object)):
    '''Analyzer base class. All analyzers are subclass of this one

    An Analyzer instance operates in the frame of a strategy and provides an
    analysis for that strategy.

    Automagically set member attributes:

      - ``self.strategy`` (giving access to the *strategy* and anything
        accessible from it)

      - ``self.datas[x]`` giving access to the array of data feeds present in
        the the system, which could also be accessed via the strategy reference

      - ``self.data``, giving access to ``self.datas[0]``

      - ``self.dataX`` -> ``self.datas[X]``

      - ``self.dataX_Y`` -> ``self.datas[X].lines[Y]``

      - ``self.dataX_name`` -> ``self.datas[X].name``

      - ``self.data_name`` -> ``self.datas[0].name``

      - ``self.data_Y`` -> ``self.datas[0].lines[Y]``

    This is not a *Lines* object, but the methods and operation follow the same
    design

      - ``__init__`` during instantiation and initial setup

      - ``start`` / ``stop`` to signal the begin and end of operations

      - ``prenext`` / ``nextstart`` / ``next`` family of methods that follow
        the calls made to the same methods in the strategy

    The mode of operation is open and no pattern is preferred. As such the
    analysis can be generated with the ``next`` calls, at the end of operations
    during ``stop`` and even with a single method like ``notify_trade``

    The important thing is to override ``get_analysis`` to return a *dict-like*
    object containing the results of the analysis (the actual format is
    implementation dependent)

    '''
    def _register(self, child):
        self._children.append(child)

    def _start(self):
        for child in self._children:
            child._start()

        self.start()

    def start(self):
        '''Invoked to indicate the start of operations, giving the analyzer
        time to setup up needed things'''
        pass

    def get_shm_events(self): # get_analysis 
        '''Returns the events from the shared memory channel for this analyzer'''
        events  = self.shm.drain_events(self.shm_id)
        return events

    # def create_analysis(self):
    #     '''Meant to be overriden by subclasses. Gives a chance to create the
    #     structures that hold the analysis.

    #     The default behaviour is to create a ``OrderedDict`` named ``rets``
    #     '''
    #     self.rets = OrderedDict()

    # def get_analysis(self):
    #     '''Returns a *dict-like* object with the results of the analysis

    #     The keys and format of analysis results in the dictionary is
    #     implementation dependent.

    #     It is not even enforced that the result is a *dict-like object*, just
    #     the convention

    #     The default implementation returns the default OrderedDict ``rets``
    #     created by the default ``create_analysis`` method

    #     '''
    #     return self.rets

    def _stop(self):
        for child in self._children:
            child._stop()

        self.stop()

    def stop(self):
        '''Invoked to indicate the end of operations, giving the analyzer
        time to shut down needed things'''
        pass


class MetaTimeFrameAnalyzerBase(Analyzer.__class__):
    def __new__(meta, name, bases, dct):
        # Hack to support original method name
        if '_on_dt_over' in dct:
            dct['on_dt_over'] = dct.pop('_on_dt_over')  # rename method

        return super(MetaTimeFrameAnalyzerBase, meta).__new__(meta, name,
                                                              bases, dct)


class TimeFrameAnalyzerBase(with_metaclass(MetaTimeFrameAnalyzerBase,
                                           Analyzer)):
    params = (
        ('timeframe', TimeFrame.Days),
        ('compression', None),
    )

    def _start(self):
        # Override to add specific attributes
        self.timeframe = self.p.timeframe or self.data._timeframe
        self.compression = self.p.compression or self.data._compression

        # self.dtcmp = 0 # boundary
        # self.dtkey = datetime.datetime.min

        super(TimeFrameAnalyzerBase, self)._start()
    
    def notify_metrics(self, dts: int):
        pass

    def on_dt_over(self, dts: int, snapshot: SnapshotBody):
        pass
