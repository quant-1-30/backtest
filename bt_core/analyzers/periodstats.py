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

from bt_protocol._protocol import SnapshotBody

__all__ = ['PeriodStats']


class PeriodStats(bt.Analyzer):
    '''Calculates basic statistics for given timeframe

    Params:

      - ``timeframe`` (default: ``Years``)
        If ``None`` the ``timeframe`` of the 1st data in the system will be
        used

        Pass ``TimeFrame.NoTimeFrame`` to consider the entire dataset with no
        time constraints

      - ``compression`` (default: ``1``)

        Only used for sub-day timeframes to for example work on an hourly
        timeframe by specifying "TimeFrame.Minutes" and 60 as compression

        If ``None`` then the compression of the 1st data of the system will be
        used

    ``get_analysis`` returns a dictionary containing the keys:

      - ``average``
      - ``stddev``
      - ``positive``
      - ``negative``
      - ``nochange``
      - ``best``
      - ``worst``

    If the parameter ``zeroispos`` is set to ``True``, periods with no change
    will be counted as positive
    '''
    params = (
        ('timeframe', bt.TimeFrame.Days), 
        ('compression', 1),
        ('zeroispos', False),
    )

    def __init__(self):
        super().__init__()

        self.last_value = 0.0
        self.period_returns = []

    def start(self):
        snap = self._owner.snapshot
        self.last_value = snap.account.portfolio_value + snap.account.cash

    def on_dt_over(self, dt0: int, snapshot: SnapshotBody):
        current_value = snapshot.account.portfolio_value + snapshot.account.cash
        
        if self.last_value <= 0:
            self._publish_nan(dt0)
            self.last_value = current_value
            return
            
        ret = (current_value / self.last_value) - 1.0
        self.period_returns.append(ret)
        self.last_value = current_value

        n = len(self.period_returns)
        avg_ret = np.mean(self.period_returns)
        
        std_ret = np.std(self.period_returns, ddof=1) if n > 1 else float('nan')
        best_ret = max(self.period_returns)
        worst_ret = min(self.period_returns)
        
        pos_cnt = sum(1 for r in self.period_returns if r > 0.0)
        neg_cnt = sum(1 for r in self.period_returns if r < 0.0)
        nochange_cnt = sum(1 for r in self.period_returns if r == 0.0)
        if self.p.zeroispos:
            pos_cnt += nochange_cnt

        self.log_shm.publish_metric(b"PeriodStatsAvgRet", avg_ret, dt0)
        self.log_shm.publish_metric(b"PeriodStatsStd", std_ret, dt0)
        self.log_shm.publish_metric(b"PeriodStatsBest", best_ret, dt0)
        self.log_shm.publish_metric(b"PeriodStatsWorst", worst_ret, dt0)
        self.log_shm.publish_metric(b"PeriodStatsPosCnt", pos_cnt, dt0)
        self.log_shm.publish_metric(b"PeriodStatsNegCnt", neg_cnt, dt0)
        self.log_shm.publish_metric(b"PeriodStatsNoChangeCnt", nochange_cnt, dt0)

    def _publish_nan(self, dt0: int):
        """首日或数据不足时 publish NaN，保证每个 metric 每日都有行"""
        nan = float('nan')
        self.log_shm.publish_metric(b"PeriodStatsAvgRet", nan, dt0)
        self.log_shm.publish_metric(b"PeriodStatsStd", nan, dt0)
        self.log_shm.publish_metric(b"PeriodStatsBest", nan, dt0)
        self.log_shm.publish_metric(b"PeriodStatsWorst", nan, dt0)
        # 计数类用 0 而非 NaN（无收益自然无正/负/平）
        self.log_shm.publish_metric(b"PeriodStatsPosCnt", 0, dt0)
        self.log_shm.publish_metric(b"PeriodStatsNegCnt", 0, dt0)
        self.log_shm.publish_metric(b"PeriodStatsNoChangeCnt", 0, dt0)
