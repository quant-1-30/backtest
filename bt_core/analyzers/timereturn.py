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
import math

import bt_core as bt
from bt_protocol._protocol import SnapshotBody


class TimeReturn(bt.TimeFrameAnalyzerBase):
    '''Total, Average, Compound and Annualized Returns calculated using a
    logarithmic approach

    See:

      - https://www.crystalbull.com/sharpe-ratio-better-with-log-returns/

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

      - ``tann`` (default: ``None``)

        Number of periods to use for the annualization (normalization) of the

        namely:

          - ``days: 252``
          - ``weeks: 52``
          - ``months: 12``
          - ``years: 1``

    Methods:

      - get_analysis

        Returns a dictionary with returns as values and the datetime points for
        each return as keys

        The returned dict the following keys:

          - ``rtot``: Total compound return
          - ``ravg``: Average return for the entire period (timeframe specific)
          - ``rnorm``: Annualized/Normalized return
          - ``rnorm100``: Annualized/Normalized return expressed in 100%
    '''
    params = (
        ('timeframe', bt.TimeFrame.Days),
        ('compression', 1),
        ('tann', None),
    )

    _TANN = {
        bt.TimeFrame.Days: 252.0,
        bt.TimeFrame.Weeks: 52.0,
        bt.TimeFrame.Months: 12.0,
        bt.TimeFrame.Years: 1.0,
    }

    def __init__(self):
        super().__init__()

        self._initial_value = 0.0  
        self._prev_value = 0.0     
        self._tcount = 0
        
        self.tann = self.p.tann or self._TANN.get(self.p.timeframe, 252.0)

    def start(self):
        snap = self._owner.snapshot
        val = snap.account.portfolio_value + snap.account.cash
        self._initial_value = val
        self._prev_value = val

    def on_dt_over(self, dt0: int, snapshot: SnapshotBody):
        curr_value = snapshot.account.portfolio_value + snapshot.account.cash
        
        if self._initial_value <= 0:
            nan = float('nan')
            self.log_shm.publish_metric(b"DailyReturn", nan, dt0)
            self.log_shm.publish_metric(b"CumReturn", nan, dt0)
            self.log_shm.publish_metric(b"AnnualReturn", nan, dt0)
            return

        self._tcount += 1

        # Daily Return
        dret = (curr_value / self._prev_value) - 1.0 if self._prev_value > 0 else 0.0
        
        # CumReturn 
        cum_ret = (curr_value / self._initial_value) - 1.0
        
        # --- Annualized Return ---
        if self._tcount > 0:
            # math.pow(negative number, non-integer) raises ValueError; handle loss scenarios where base <= 0
            base = 1.0 + cum_ret
            if base <= 0:
                ann_ret = -1.0
            else:
                ann_ret = math.pow(base, self.tann / self._tcount) - 1.0
        else:
            ann_ret = 0.0

        self.log_shm.publish_metric(b"DailyReturn", dret, dt0)
        self.log_shm.publish_metric(b"CumReturn", cum_ret, dt0)
        self.log_shm.publish_metric(b"AnnualReturn", ann_ret, dt0)

        self._prev_value = curr_value
        
    def stop(self):
        super(TimeReturn, self).stop()
