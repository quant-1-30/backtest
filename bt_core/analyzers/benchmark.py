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
from bt_core.utils.dateintern import ts2intdt
from bt_protocol._protocol import SnapshotBody


class Benchmark(bt.TimeFrameAnalyzerBase):
    '''This analyzer calculates the Returns by looking at the beginning
    and end of the timeframe

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

      - ``data`` (default: ``None``)

        Reference asset to track instead of the portfolio value.

        .. note:: this data must have been added to a ``cerebro`` instance with
                  ``addata``, ``resampledata`` or ``replaydata``

      - ``firstopen`` (default: ``True``)

        When tracking the returns of a ``data`` the following is done when
        crossing a timeframe boundary, for example ``Years``:

          - Last ``close`` of previous year is used as the reference price to
            see the return in the current year

        The problem is the 1st calculation, because the data has** no
        previous** closing price. As such and when this parameter is ``True``
        the *opening* price will be used for the 1st calculation.

        This requires the data feed to have an ``open`` price (for ``close``
        the standard [0] notation will be used without reference to a field
        price)

        Else the initial close will be used.

      - ``fund`` (default: ``None``)

        If ``None`` the actual mode of the broker (fundmode - True/False) will
        be autodetected to decide if the returns are based on the total net
        asset value or on the fund value. See ``set_fundmode`` in the broker
        documentation

        Set it to ``True`` or ``False`` for a specific behavior

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
        self.dts = self.returns = np.array([])

    def start(self):
        super(Benchmark, self).start()

        ret_table = self._owner.store.get_benchmark_dret()
        self.dts = ret_table["day"].to_numpy()
        self.returns = ret_table["ret"].to_numpy()

    def on_dt_over(self, dt0: int, snapshot: SnapshotBody):
        dtint = ts2intdt(dt0)

        loc = np.searchsorted(self.dts, dtint)
        loc_dret = self.returns[loc] if loc < len(self.dts) else np.nan

        self.log_shm.publish_metric(b"BenchmarkDret", loc_dret, dt0)

    def stop(self):
        super(Benchmark, self).stop()
