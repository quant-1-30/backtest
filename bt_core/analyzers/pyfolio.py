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
import bt_core as bt
from bt_protocol._protocol import SnapshotBody


class PyFolio(bt.TimeFrameAnalyzerBase):
    '''This analyzer reports the value of the positions of the current set of
    datas

    Params:

      - timeframe (default: ``None``)
        If ``None`` then the timeframe of the 1st data of the system will be
        used

      - compression (default: ``None``)

        Only used for sub-day timeframes to for example work on an hourly
        timeframe by specifying "TimeFrame.Minutes" and 60 as compression

        If ``None`` then the compression of the 1st data of the system will be
        used

      - headers (default: ``False``)

        Add an initial key to the dictionary holding the results with the names
        of the datas ('Datetime' as key

      - cash (default: ``False``)

        Include the actual cash as an extra position (for the header 'cash'
        will be used as name)

    Methods:

      - get_analysis

        Returns a dictionary with returns as values and the datetime points for
        each return as keys
    '''
    params = (
        ('timeframe', bt.TimeFrame.Days),
        ('compression', None),
        ('cash', False),
    )

    def start(self):
        tf = min(d._timeframe for d in self.datas)
        self._usedate = tf >= bt.TimeFrame.Days

    def on_dt_over(self, dt0: int, snapshot: SnapshotBody):
        acct = snapshot.account
        self.log_shm.publish_metric(b"Portfolio", acct.portfolio_value, dt0) 
        self.log_shm.publish_metric(b"Cash", acct.cash, dt0) 
        self.log_shm.publish_metric(b"Pnl", acct.pnl, dt0)
  
    def stop(self):
        super(PyFolio, self).stop()
