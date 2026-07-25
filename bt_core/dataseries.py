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
import numpy as np
import datetime as _datetime
from collections import OrderedDict

from .lineseries import LineSeries
from bt_core.utils.dateintern import date2num
from bt_core.utils.autodict import AutoOrderedDict


class TimeFrame(object):
    (Ticks, MicroSeconds, MilliSecond, Seconds, Minutes,
     Days, Weeks, Months, Years, NoTimeFrame) = range(10)

    Names = ['Ticks', 'MicroSeconds', 'MilliSecond', 'Seconds', 'Minutes',
             'Days', 'Weeks', 'Months', 'Years', 'NoTimeFrame']

    names = Names  # support old naming convention

    @classmethod
    def getname(cls, tframe, compression=None):
        tname = cls.Names[tframe]
        if compression > 1 or tname == cls.Names[-1]:
            return tname  # for plural or 'NoTimeFrame' return plain entry

        # return singular if compression is 1
        return cls.Names[tframe][:-1]

    @classmethod
    def TFrame(cls, name):
        return getattr(cls, name)

    @classmethod
    def TName(cls, tframe):
        return cls.Names[tframe]


class DataSeries(LineSeries):
    plotinfo = dict(plot=True, plotind=True, plotylimited=True, plotname="Feed")

    # _compression = 1
    # _timeframe = TimeFrame.Days
    # _extra_info = "{}"  # placeholder for extra info

    params = (
        ("timeframe", TimeFrame.Days),
        ("compression", 1),
    )

    Open, High, Low, Close, Volume, Amount, DateTime = range(7)

    def getvalues(self):
        return [self.lines[i][0] for i in range(len(self.lines))]


class OHLC(DataSeries):
    # lines = ('close', 'low', 'high', 'open', 'volume', 'openinterest',)
    lines = ('open', 'high', 'low', 'close', 'volume', 'amount')


class OHLCDateTime(OHLC):
    lines = ('datetime',)


class _Bar(AutoOrderedDict):
    '''
    This class is a placeholder for the values of the standard lines of a
    DataBase class (from OHLCDateTime)

    It inherits from AutoOrderedDict to be able to easily return the values as
    an iterable and address the keys as attributes

    Order of definition is important and must match that of the lines
    definition in DataBase (which directly inherits from OHLCDateTime)
    '''
    # replaying = False

    # Without - 1 ... converting back to time will not work
    # Need another -1 to support timezones which may move the time forward
    MAXDATE = date2num(_datetime.datetime(9999, 12, 30))- 2

    def __init__(self, maxdate=False):
        super(_Bar, self).__init__()
        self.bstart(maxdate=maxdate)

    def bstart(self, maxdate=False):
        '''Initializes a bar to the default not-updated vaues'''
        # Order is important: defined in DataSeries/OHLC/OHLCDateTime
        self.close = float('NaN')
        self.low = float('inf')
        self.high = float('-inf')
        self.open = float('NaN')
        self.volume = 0.0
        self.amount = 0.0
        self.datetime = self.MAXDATE if maxdate else None
        # self.openinterest = 0.0

    def isopen(self):
        '''Returns if a bar has already been updated

        Uses the fact that NaN is the value which is not equal to itself
        and ``open`` is initialized to NaN
        '''
        o = self.open
        return o == o  # False if NaN, True in other cases

    def bupdate(self, data, reopen=False):
        '''Updates a bar with the values from data

        Returns True if the update was the 1st on a bar (just opened)

        Returns False otherwise
        '''
        if reopen:
            self.bstart()

        self.high = max(self.high, data.high[0])
        self.low = min(self.low, data.low[0])
        self.close = data.close[0]

        self.volume += data.volume[0]
        self.amount += data.amount[0]
        self.datetime = data.datetime[0]
        # self.openinterest = data.openinterest[0]

        o = self.open
        if reopen or not o == o:
            self.open = data.open[0]
            return True  # just opened the bar

        return False
