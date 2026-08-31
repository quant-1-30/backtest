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

from datetime import timedelta

from .dataseries import TimeFrame, _Bar
from .metabase import with_metaclass
from . import metabase
from bt_core.utils.dateintern import num2date


# class DTFaker(object):

#     def __init__(self, data, forcedata=None):
#         self.data = data

#         # Aliases
#         self.datetime = self
#         self.p = self

#         if forcedata is None:
#             # _dtime = datetime.utcnow() + data._timeoffset()
#             # self._dt = dt = date2num(_dtime)  # utc-like time
#             # self._dtime = data.num2date(dt)  # localized time
#             self._dt = dt = data.datetime[0]
#             self._dtime = data.num2date(dt)
#         else:
#             self._dt = forcedata.datetime[0]  # utc-like time
#             self._dtime = forcedata.datetime.datetime()  # localized time

#         self.sessionend = data.p.sessionend

#     def __len__(self):
#         return len(self.data)

#     def __call__(self, idx=0):
#         return self._dtime  # simulates data.datetime.datetime()

#     def datetime(self, idx=0):
#         return self._dtime

#     def date(self, idx=0):
#         return self._dtime.date()

#     def time(self, idx=0):
#         return self._dtime.time()

#     @property
#     def _calendar(self):
#         return self.data._calendar

#     def __getitem__(self, idx):
#         return self._dt if idx == 0 else float('-inf')

#     def num2date(self, *args, **kwargs):
#         return self.data.num2date(*args, **kwargs)

#     def date2num(self, *args, **kwargs):
#         return self.data.date2num(*args, **kwargs)

#     # def _getnexteos(self):
#     #     return self.data._getnexteos()
    
#     def _geteos(self):
#         '''Returns the next end of session date and time in utc-like format'''
#         return self._geteos()


class _BaseResampler(with_metaclass(metabase.MetaParams, object)):
    params = (
        ('bar2edge', True),
        ('adjbartime', True),
        ('rightedge', True),
        ('boundoff', 0),
        ('timeframe', TimeFrame.Days),
        ('compression', 1),
    )

    def __init__(self, data):
        self.subdays = TimeFrame.Ticks < self.p.timeframe < TimeFrame.Days
        self.subweeks = self.p.timeframe < TimeFrame.Weeks
        self.componly = (not self.subdays and
                         data._timeframe == self.p.timeframe and
                         not (self.p.compression % data._compression))

        self.bar = _Bar(maxdate=True)  # bar holder
        self.compcount = 0  # count of produced bars to control compression
        self._firstbar = True
        self.doadjusttime = (self.p.bar2edge and self.p.adjbartime and
                             self.subweeks)

        self._nexteos = None

        # Modify data information according to own parameters
        data.resampling = 1
        data._timeframe = self.p.timeframe
        data._compression = self.p.compression

        self.data = data

    def _checkbarover(self, data, fromcheck=False):
        # chkdata = DTFaker(data, forcedata) if fromcheck else data
        # chkdata = data

        isover = False
        if not self.componly and not self._barover(data):
            return isover

        if self.subdays and self.p.bar2edge:
            isover = True
        elif not fromcheck:  # fromcheck doesn't increase compcount
            self.compcount += 1
            if not (self.compcount % self.p.compression):
                # boundary crossed and enough bars for compression ... proceed
                isover = True

        return isover

    def _barover(self, data):
        tframe = self.p.timeframe

        if tframe == TimeFrame.Ticks:
            # Ticks is already the lowest level
            return self.bar.isopen()

        elif tframe < TimeFrame.Days:
            return self._barover_subdays(data)

        elif tframe == TimeFrame.Days:
            return self._barover_days(data)

        elif tframe == TimeFrame.Weeks:
            return self._barover_weeks(data)

        elif tframe == TimeFrame.Months:
            return self._barover_months(data)

        elif tframe == TimeFrame.Years:
            return self._barover_years(data)

    def _eosset(self):
        if self._nexteos is None:
            self._nexteos, self._nextdteos = self.data._getnexteos()
            return

    def _eoscheck(self, data, seteos=True, exact=False):
        if seteos:
            self._eosset()

        equal = data.datetime[0] == self._nextdteos
        grter = data.datetime[0] > self._nextdteos

        if exact:
            ret = equal
        else:
            # if the compared data goes over the endofsession
            # make sure the resampled bar is open and has something before that
            # end of session. It could be a weekend and nothing was delivered
            # until Monday
            if grter:
                ret = (self.bar.isopen() and
                       self.bar.datetime <= self._nextdteos)
            else:
                ret = equal

        if ret:
            self._lasteos = self._nexteos
            self._lastdteos = self._nextdteos
            self._nexteos = None
            self._nextdteos = float('-inf')

        return ret

    # def _barover_days(self, data):
    #     _, _, day = data.num2date(self.bar.datetime).date().isocalendar()

    #     _, _, barday = data.datetime.datetime().isocalendar()

    #     return day != barday

    def _barover_days(self, data): 
        # UTC + 8 hour = Asia avoid date().isocalendar()
        bar_dts = self.bar.datetime
        data_dts = data.datetime[0]
        
        bar_day = (int(bar_dts) + 28800) // 86400
        data_day = (int(data_dts) + 28800) // 86400

        return data_day != bar_day

    # def _barover_weeks(self, data):
    #     if self.data._calendar is None:
    #         year, week, _ = data.num2date(self.bar.datetime).date().isocalendar()
    #         yearweek = year * 100 + week

    #         baryear, barweek, _ = data.datetime.datetime().isocalendar()
    #         bar_yearweek = baryear * 100 + barweek

    #         return bar_yearweek > yearweek
    #     else:
    #         return data._calendar.last_weekday(data.datetime.datetime())

    def _barover_weeks(self, data):
        # 19700101 thursday avoid isocalendar
        bar_dts = self.bar.datetime
        data_dts = data.datetime[0]
        
        # (s + UTC8 + 3dayoff) ---> week second
        bar_week = (int(bar_dts) + 28800 + 259200) // 604800
        data_week = (int(data_dts) + 28800 + 259200) // 604800
        
        return data_week > bar_week

    def _barover_months(self, data):
        dt = data.num2date(self.bar.datetime).date()
        yearmonth = dt.year * 100 + dt.month

        bardt = data.datetime.datetime()
        bar_yearmonth = bardt.year * 100 + bardt.month

        return bar_yearmonth > yearmonth

    def _barover_years(self, data):
        return (data.datetime.datetime().year >
                data.num2date(self.bar.datetime).year)

    def _gettmpoint(self, tm):
        '''Returns the point of time intraday for a given time according to the
        timeframe

          - Ex 1: 00:05:00 in minutes -> point = 5
          - Ex 2: 00:05:20 in seconds -> point = 5 * 60 + 20 = 320
        '''
        point = tm.hour * 60 + tm.minute
        restpoint = 0

        if self.p.timeframe < TimeFrame.Minutes:
            point = point * 60 + tm.second

            if self.p.timeframe < TimeFrame.Seconds:
                point = point * 1e6 + tm.microsecond
            else:
                restpoint = tm.microsecond
        else:
            restpoint = tm.second + tm.microsecond

        point += self.p.boundoff

        return point, restpoint

    def _barover_subdays(self, data):
        if self._eoscheck(data):
            return True

        if data.datetime[0] < self.bar.datetime:
            return False

        # Get time objects for the comparisons - in utc-like format
        tm = num2date(self.bar.datetime).time()
        bartm = num2date(data.datetime[0]).time()

        point, _ = self._gettmpoint(tm)
        barpoint, _ = self._gettmpoint(bartm)

        ret = False
        # bar2edge via compression to align
        if barpoint > point:
            # The data bar has surpassed the internal bar
            if not self.p.bar2edge:
                # Compression done on simple bar basis (like days)
                ret = True
            elif self.p.compression == 1:
                # no bar compression requested -> internal bar done
                ret = True
            else:
                point_comp = point // self.p.compression
                barpoint_comp = barpoint // self.p.compression

                # Went over boundary including compression
                if barpoint_comp > point_comp:
                    ret = True

        return ret

    def _dataonedge(self, data):
        if not self.subweeks:
            if data._calendar is None:
                return False, True  # nothing can be done

            tframe = self.p.timeframe
            ret = False
            if tframe == TimeFrame.Weeks:  # Ticks is already the lowest
                ret = data._calendar.last_weekday(data.datetime.datetime().date())
            elif tframe == TimeFrame.Months:
                ret = data._calendar.last_monthday(data.datetime.datetime().date())
            elif tframe == TimeFrame.Years:
                ret = data._calendar.last_yearday(data.datetime.datetime().date())

            if ret:
                # Data must be consumed but compression may not be met yet
                # Prevent barcheckover from being called because it could again
                # increase compcount
                docheckover = False
                self.compcount += 1
                ret = not (self.compcount % self.p.compression) # to solve repeated data bug (one more time)
            else:
                docheckover = True

            return ret, docheckover

        if self._eoscheck(data, exact=True): # eos must add 
            return True, True

        if self.subdays:
            point, prest = self._gettmpoint(data.datetime.time())
            if prest:
                return False, True  # cannot be on boundary, subunits present

            # Pass through compression to get boundary and rest over boundary
            bound, brest = divmod(point, self.p.compression)

            # if no extra and decomp bound is point
            return (brest == 0 and point == (bound * self.p.compression), True)

        return False, True  # subweeks, not subdays and not sessionend

    def _calcadjtime(self, greater=False):
        if self._nexteos is None:
            # Session has been exceeded - end of session is the mark
            return self._lastdteos  # utc-like

        dt = self.data.num2date(self.bar.datetime)

        # Get current time
        tm = dt.time()
        # Get the point of the day in the time frame unit (ex: minute 200)
        point, _ = self._gettmpoint(tm)

        # Apply compression to update the point position (comp 5 -> 200 // 5)
        # point = (point // self.p.compression)

        point = point // self.p.compression

        # If rightedge (end of boundary is activated) add it unless recursing
        point += self.p.rightedge

        # Restore point to the timeframe units by de-applying compression
        point *= self.p.compression

        # Get hours, minutes, seconds and microseconds
        extradays = 0
        if self.p.timeframe == TimeFrame.Minutes:
            ph, pm = divmod(point, 60)
            ps = 0
            pus = 0
        elif self.p.timeframe == TimeFrame.Seconds:
            ph, pm = divmod(point, 60 * 60)
            pm, ps = divmod(pm, 60)
            pus = 0
        elif self.p.timeframe <= TimeFrame.MicroSeconds: # 微秒
            ph, pm = divmod(point, 60 * 60 * 1e6)
            pm, psec = divmod(pm, 60 * 1e6)
            ps, pus = divmod(psec, 1e6)
        elif self.p.timeframe == TimeFrame.Days:
            # last resort
            eost = self._nexteos.time()
            ph = eost.hour
            pm = eost.minute
            ps = eost.second
            pus = eost.microsecond

        if ph > 23:  # went over midnight:
            extradays = ph // 24
            ph %= 24

        # Replace intraday parts with the calculated ones and update it
        dt = dt.replace(hour=int(ph), minute=int(pm),
                        second=int(ps), microsecond=int(pus))
        if extradays:
            dt += timedelta(days=extradays)
        dtnum = self.data.date2num(dt)
        return dtnum

    def _adjusttime(self, greater=False):
        '''
        Adjusts the time of calculated bar (from underlying data source) by
        using the timeframe to the appropriate boundary, with compression taken
        into account

        Depending on param ``rightedge`` uses the starting boundary or the
        ending one
        '''
        dtnum = self._calcadjtime(greater=greater)
        if greater and dtnum <= self.bar.datetime:
            return False

        self.bar.datetime = dtnum
        return True

    def check(self, data):
        '''Called to check if the current stored bar has to be delivered in
        spite of the data not having moved forward. If no ticks from a live
        feed come in, a 5 second resampled bar could be delivered 20 seconds
        later. When this method is called the wall clock (incl data time
        offset) is called to check if the time has gone so far as to have to
        deliver the already stored data
        '''
        if not self.bar.isopen():
            return

        return self(data, fromcheck=True)


class Resampler(_BaseResampler):
    '''This class resamples data of a given timeframe to a larger timeframe.

    Params

      - bar2edge (default: True)

        resamples using time boundaries as the target. For example with a
        "ticks -> 5 seconds" the resulting 5 seconds bars will be aligned to
        xx:00, xx:05, xx:10 ...

      - adjbartime (default: True)

        Use the time at the boundary to adjust the time of the delivered
        resampled bar instead of the last seen timestamp. If resampling to "5
        seconds" the time of the bar will be adjusted for example to hh:mm:05
        even if the last seen timestamp was hh:mm:04.33

        .. note::

           Time will only be adjusted if "bar2edge" is True. It wouldn't make
           sense to adjust the time if the bar has not been aligned to a
           boundary

      - rightedge (default: True)

        Use the right edge of the time boundaries to set the time.

        If False and compressing to 5 seconds the time of a resampled bar for
        seconds between hh:mm:00 and hh:mm:04 will be hh:mm:00 (the starting
        boundary

        If True the used boundary for the time will be hh:mm:05 (the ending
        boundary)
    '''
    params = (
        ('bar2edge', True),
        ('adjbartime', True),
        ('rightedge', True),
    )

    def last(self, data):
        '''Called when the data is no longer producing bars

        Can be called multiple times. It has the chance to (for example)
        produce extra bars which may still be accumulated and have to be
        delivered
        '''
        if self.bar.isopen():
            if self.doadjusttime:
                self._adjusttime()
            data._add2stack(self.bar.lvalues())
            self.bar.bstart(maxdate=True)  # close the bar to avoid dups
            return True

        return False

    def __call__(self, data, fromcheck=False):
        '''Called for each set of values produced by the data source'''
        consumed = False
        onedge = False
        docheckover = True
        if not fromcheck:
            if self.componly:  # only if not subdays
                _, self._lastdteos = self.data._getnexteos()
                consumed = True
            else:
                onedge, docheckover = self._dataonedge(data)  # for subdays
                consumed = onedge

        if consumed:
            self.bar.bupdate(data)  # update new or existing bar
            data.backwards()  # remove used bar

        cond = self.bar.isopen()
        if cond:  # original is and, the 2nd term must also be true
            if not onedge:  # onedge true is sufficient
                if docheckover:
                    cond = self._checkbarover(data, fromcheck=fromcheck)
        if cond:
            dodeliver = True
            if dodeliver:
                if not onedge and self.doadjusttime:
                    self._adjusttime(greater=True)
                data._add2stack(self.bar.lvalues())
                self.bar.bstart(maxdate=True)  # reset bar

        if not fromcheck: # update bar incase bar not be consumed and keep bar is open
            if not consumed:
                self.bar.bupdate(data)  
                data.backwards() 
        return True


class ResamplerTicks(Resampler):
    params = (('timeframe', TimeFrame.Ticks),)


class ResamplerSeconds(Resampler):
    params = (('timeframe', TimeFrame.Seconds),)


class ResamplerMinutes(Resampler):
    params = (('timeframe', TimeFrame.Minutes),)


class ResamplerDaily(Resampler):
    params = (('timeframe', TimeFrame.Days),)


class ResamplerWeekly(Resampler):
    params = (('timeframe', TimeFrame.Weeks),)


class ResamplerMonthly(Resampler):
    params = (('timeframe', TimeFrame.Months),)


class ResamplerYearly(Resampler):
    params = (('timeframe', TimeFrame.Years),)
