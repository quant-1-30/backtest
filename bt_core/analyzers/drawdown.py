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
import bt_core as bt
from bt_core.utils import AutoOrderedDict
from bt_protocol._protocol import SnapshotBody


__all__ = ['DrawDown']


class DrawDown(bt.TimeFrameAnalyzerBase):
    '''This analyzer calculates trading system drawdowns on the chosen
    timeframe which can be different from the one used in the underlying data
    Params:

      - ``timeframe`` (default: ``None``)
        If ``None`` the ``timeframe`` of the 1st data in the system will be
        used

        Pass ``TimeFrame.NoTimeFrame`` to consider the entire dataset with no
        time constraints

      - ``compression`` (default: ``None``)

        Only used for sub-day timeframes to for example work on an hourly
        timeframe by specifying "TimeFrame.Minutes" and 60 as compression

        If ``None`` then the compression of the 1st data of the system will be
        used
      - *None*

    Methods:

      - ``get_analysis``

        Returns a dictionary (with . notation support and subdctionaries) with
        drawdown stats as values, the following keys/attributes are available:

        - ``drawdown`` - drawdown value in 0.xx %
        - ``maxdrawdown`` - drawdown value in monetary units
        - ``maxdrawdownperiod`` - drawdown length

      - Those are available during runs as attributes
        - ``dd``
        - ``maxdd``
        - ``maxddlen``
    '''

    params = (
        ('timeframe', bt.TimeFrame.Days),
        ('compression', None),
    )

    def start(self):
        super(DrawDown, self).start()
        
        self.dd = 0.0
        self.maxdd = 0.0
        self.peak = 0.0 # float('-inf')

        self.ddlen = 0
        self.maxddlen = 0

    def on_dt_over(self, dt0: int, snapshot: SnapshotBody):
        acct = snapshot.account
        value = acct.portfolio_value + acct.cash

        # peak and reset
        if value >= self.peak:
            self.peak = value
            self.ddlen = 0 
        else:
          self.ddlen +=1 # avoid vaule / self.peak > 0.0 

        self.dd = (self.peak - value) / self.peak if self.peak > 0.0 else 0.0

        # update the maxdrawdown if needed
        self.maxdd = maxdd =  max(self.maxdd, self.dd)
        self.maxddlen = maxddlen = max(self.maxddlen, self.ddlen)
        self.log_shm.publish_metric(b"DrawDown", self.dd, dt0)
        self.log_shm.publish_metric(b"DrawDownLength", self.ddlen, dt0)
        self.log_shm.publish_metric(b"MaxDrawdown", maxdd, dt0)
        self.log_shm.publish_metric(b"MaxDrawdownLength", maxddlen, dt0)

    def stop(self):
        super(DrawDown, self).stop()
