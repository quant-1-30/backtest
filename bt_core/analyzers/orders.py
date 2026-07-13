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


class OrdersAnalyzer(bt.TimeFrameAnalyzerBase):
    '''This analyzer reports the orders occurred with each an every data in
    the system

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
        ('compression', None),
    )

    def __init__(self):
        super().__init__()
        self.order_cnt = 0

    def _drain(self):
        events = self.get_shm_events()  
        open_orders_cnt = sum(1 for e in events if e["type"] == "order")

        self.order_cnt += open_orders_cnt
        return open_orders_cnt

    def on_dt_over(self, dt0: int, snapshot: SnapshotBody):
        self._drain()

        self.log_shm.publish_metric(b"OrdersCnt", self.order_cnt, dt0)
        self.order_cnt = 0 # reset 
