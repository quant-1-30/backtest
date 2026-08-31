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
from datetime import datetime, timedelta, time

from .metabase import MetaParams, with_metaclass
from .utils.dateintern import tzparse

__all__ = ['TradingCalendarBase', 'TradingCalendar']

# Imprecission in the full time conversion to float would wrap over to next day
# if microseconds is 999999 as defined in time.max
_time_max = time(hour=23, minute=59, second=59, microsecond=999990)


MONDAY, TUESDAY, WEDNESDAY, THURSDAY, FRIDAY, SATURDAY, SUNDAY = range(7)
(ISONODAY, ISOMONDAY, ISOTUESDAY, ISOWEDNESDAY, ISOTHURSDAY, ISOFRIDAY,
 ISOSATURDAY, ISOSUNDAY) = range(8)

WEEKEND = [SATURDAY, SUNDAY]
ISOWEEKEND = [ISOSATURDAY, ISOSUNDAY]
ONEDAY = timedelta(days=1)


class TradingCalendarBase(with_metaclass(MetaParams, object)):
    def _nextday(self, day: datetime):
        '''
        Returns the next trading day (datetime/date instance) after ``day``
        (datetime/date instance) and the isocalendar components

        The return value is a tuple with 2 components: (nextday, (y, w, d))
        '''
        raise NotImplementedError

    def schedule(self, day: datetime):
        '''
        Returns a tuple with the opening and closing times (``datetime.time``)
        for the given ``date`` (``datetime/date`` instance)
        '''
        raise NotImplementedError

    def nextday(self, day: datetime):
        '''
        Returns the next trading day (datetime/date instance) after ``day``
        (datetime/date instance)
        '''
        return self._nextday(day)[0]  # 1st ret elem is next day

    def nextday_week(self, day: datetime):
        '''
        Returns the iso week number of the next trading day, given a ``day``
        (datetime/date) instance
        '''
        return self._nextday(day)[1][1]  # 2 elem is isocal / 0 - y, 1 - wk, 2 - day

    def last_weekday(self, day: datetime):
        '''
        Returns ``True`` if the given ``day`` (datetime/date) instance is the
        last trading day of this week
        '''
        # Next day must be greater than day. If the week changes is enough for
        # a week change even if the number is smaller (year change)
        return day.isocalendar()[1] != self._nextday(day)[1][1]

    def last_monthday(self, day: datetime):
        '''
        Returns ``True`` if the given ``day`` (datetime/date) instance is the
        last trading day of this month
        '''
        # Next day must be greater than day. If the week changes is enough for
        # a week change even if the number is smaller (year change)
        return day.month != self._nextday(day)[0].month

    def last_yearday(self, day: datetime):
        '''
        Returns ``True`` if the given ``day`` (datetime/date) instance is the
        last trading day of this month
        '''
        # Next day must be greater than day. If the week changes is enough for
        # a week change even if the number is smaller (year change)
        return day.year != self._nextday(day)[0].year


class TradingCalendar(TradingCalendarBase):
    '''
    Wrapper of ``pandas_market_calendars`` for a trading calendar. The package
    ``pandas_market_calendar`` must be installed

    Params:

      - ``open`` (default ``time.min``)

        Regular start of the session

      - ``close`` (default ``time.max``)

        Regular end of the session

      - ``holidays`` (default ``[]``)

        List of non-trading days (``datetime.datetime`` instances)

      - ``offdays`` (default ``ISOWEEKEND``)

        A list of weekdays in ISO format (Monday: 1 -> Sunday: 7) in which the
        market doesn't trade. This is usually Saturday and Sunday and hence the
        default

    - ``earlydays`` (default ``[]``)

        List of tuples determining the date and opening/closing times of days
        which do not conform to the regular trading hours where each tuple has
        (``datetime.datetime``, ``datetime.time``, ``datetime.time`` )

    # 熔断机制 2016-01-01 2016-01-07(1月8日起暂停实施指数熔断机制)
    # 2016年1月4日, A股遇到史上首次“熔断” 沪深300指数并于13点13分超过5%引发熔断,三家交易所暂停交易15分钟, 恢复交易并于13点34分触及7% 三个交易所暂停交易至收市
    # 2016年1月7日, 9点42分沪深300指数跌幅扩大至5%再度触发熔断线9点57分恢复交易, 10:00 沪深300指数再度快速探底最大跌幅7.21% 二度熔断触及阈值
    '''

    params = (
        ('open', time(hour=9, minute=30)),
        ('close', time(hour=15, minute=0)),
        ('holidays', []),
        ('earlydays', [(datetime(2016, 1, 4), time(hour=9, minute=30), time(hour=13, minute=34)), 
                        (datetime(2016, 1, 7), time(hour=9, minute=30), time(hour=10, minute=0))]),  
        ('offdays', ISOWEEKEND), # soweekdays
    )

    def __init__(self):
        # normalize to datetime.date: schedule() compares against day.date()
        # (a datetime is never equal to a date -> earlydays never matched)
        self._earlydays = [x[0].date() if isinstance(x[0], datetime) else x[0]
                           for x in self.p.earlydays]

    def _nextday(self, day: datetime):
        '''
        Returns the next trading day (datetime/date instance) after ``day``
        (datetime/date instance) and the isocalendar components

        The return value is a tuple with 2 components: (nextday, (y, w, d))

        e.g.
            from datetime import date
            new_year_day = date(2026, 1, 1)

            print(new_year_day.year)  # 2026
    
            print(new_year_day.isocalendar()) # IsoCalendarDate(year=2025, week=52, weekday=4)
        '''
        while True:
            day += ONEDAY
            isocal = day.isocalendar() # (year, week_nth, weekday) 1-7
            if isocal[2] in self.p.offdays or day in self.p.holidays:
                continue

            return day, isocal

    def schedule(self, day: datetime, tz:str=''):
        '''
        Returns the opening and closing times for the given ``day``. If the
        method is called, the assumption is that ``day`` is an actual trading
        day

        The return value is a tuple with 2 components: opentime, closetime
        '''
        tzinfo = tzparse(tz)

        while True:
            dt = day.date()
            try:
                i = self._earlydays.index(dt)
                o, c = self.p.earlydays[i][1:]
            except ValueError:  
                o, c = self.p.open, self.p.close

            closing = datetime.combine(dt, c).replace(tzinfo=tzinfo)

            if day > closing:  
                day += ONEDAY
                continue

            opening = datetime.combine(dt, o).replace(tzinfo=tzinfo)
            return opening, closing
