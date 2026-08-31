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
# from __future__ import (absolute_import, division, print_function,
#                         unicode_literals)

import bt_core as bt
from bt_protocol._protocol import SnapshotBody


class Transactions(bt.TimeFrameAnalyzerBase):
    consumes_shm = True  # drains shm events; registers a ring consumer
    '''This analyzer reports the transactions occurred with each an every data in
    the system

    It looks at the order execution bits to create a ``Position`` starting from
    0 during each ``next`` cycle.

    The result is used during next to record the transactions

    Params:

      - headers (default: ``True``)

        Add an initial key to the dictionary holding the results with the names
        of the datas

    Methods:

      - get_analysis

        Returns a dictionary with returns as values and the datetime points for
        each return as keys
    '''
    params = (
        ('timeframe', bt.TimeFrame.Days),
        ('compression', 1),
    )
    def __init__(self):
        self.trades_cnt = 0

    def _drain(self):
        events = self.get_shm_events()  
        trades_cnt = sum(1 for e in events if e["type"] == "trade")

        self.trades_cnt += trades_cnt
        return trades_cnt

    def on_dt_over(self, dt0: int, snapshot: SnapshotBody):
        self._drain() 
        self.log_shm.publish_metric(b"TradesCnt", self.trades_cnt, dt0) # slope
        self.trades_cnt = 0 # reset

    def stop(self):
        super(Transactions, self).stop()
