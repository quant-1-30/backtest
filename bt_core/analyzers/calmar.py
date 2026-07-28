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
import collections
import math

import bt_core as bt
from bt_protocol._protocol import SnapshotBody

__all__ = ['Calmar']


class Calmar(bt.TimeFrameAnalyzerBase):
    '''This analyzer calculates the CalmarRatio
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

    See also:

      - https://en.wikipedia.org/wiki/Calmar_ratio

    Methods:
      - ``get_analysis``

        Returns a OrderedDict with a key for the time maxlen and the
        corresponding rolling Calmar ratio

    Attributes:
      - ``calmar`` the latest calculated calmar ratio     Calmar = 年化收益率 / 最大回撤
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
        self._peak = -float('inf') 

        self._max_dd = 0.0          
        self._tcount = 0            
        self.tann = self.p.tann or self._TANN.get(self.p.timeframe, 252.0)

    def start(self):
        acct = self._owner.snapshot.account
        val = acct.portfolio_value + acct.cash
        self._initial_value = val
        self._peak = val

    def on_dt_over(self, dt0: int, snapshot: SnapshotBody):
        # acct = self._owner.get_snapshot().account
        acct = snapshot.account
        curr_value = acct.portfolio_value + acct.cash
        
        if self._initial_value <= 0:
            return

        self._tcount += 1
        # ==========================================
        # 1. Max Drawdown - O(1)
        # ==========================================
        if curr_value > self._peak:
            self._peak = curr_value
        
        current_dd = (self._peak - curr_value) / self._peak if self._peak > 0 else 0.0
        if current_dd > self._max_dd:
            self._max_dd = current_dd

        # ==========================================
        # 2. Annualized Return
        # ==========================================
        total_ret = (curr_value / self._initial_value) - 1.0
        # (1 + 总收益) ^ (年化因子 / 总周期数) - 1
        print("ann_ret", curr_value, self._initial_value, total_ret, self.tann, self._tcount)
        ann_ret = math.pow(1.0 + total_ret, self.tann / self._tcount) - 1.0

        # ==========================================
        # 3. Calmar Ratio
        # ==========================================
        if self._max_dd > 0:
            calmar = ann_ret / self._max_dd
        else:
            calmar = 0.0

        self.log_shm.publish_metric(b"MaxDrawdown", self._max_dd, dt0)
        self.log_shm.publish_metric(b"Calmar", calmar, dt0)

