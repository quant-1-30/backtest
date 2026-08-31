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

from datetime import date, datetime, timedelta

from .feed import AbstractDataBase
from bt_core.utils.dateintern import date2num, num2date


cdef class Timer:
    '''
        **Note**: can be called during ``__init__`` or ``start``

        Schedules a timer to invoke either a specified callback or the
        ``notify_timer`` of one or more strategies.

        Arguments:

          - ``when``: can be

            - ``datetime.time`` instance (see below ``tzdata``)
            - ``bt.timer.SESSION_START`` to reference a session start
            - ``bt.timer.SESSION_END`` to reference a session end

         - ``offset`` which must be a ``datetime.timedelta`` instance

           Used to offset the value ``when``. It has a meaningful use in
           combination with ``SESSION_START`` and ``SESSION_END``, to indicated
           things like a timer being called ``15 minutes`` after the session
           start.

          - ``repeat`` which must be a ``datetime.timedelta`` instance

            Indicates if after a 1st call, further calls will be scheduled
            within the same session at the scheduled ``repeat`` delta

            Once the timer goes over the end of the session it is reset to the
            original value for ``when``

          - ``weekdays``: a **sorted** iterable with integers indicating on
            which days (iso codes, Monday is 1, Sunday is 7) the timers can
            be actually invoked

            If not specified, the timer will be active on all days

          - ``weekcarry`` (default: ``False``). If ``True`` and the weekday was
            not seen (ex: trading holiday), the timer will be executed on the
            next day (even if in a new week)

          - ``monthdays``: a **sorted** iterable with integers indicating on
            which days of the month a timer has to be executed. For example
            always on day *15* of the month

            If not specified, the timer will be active on all days

          - ``monthcarry`` (default: ``True``). If the day was not seen
            (weekend, trading holiday), the timer will be executed on the next
            available day.

          - ``allow`` (default: ``None``). A callback which receives a
            `datetime.date`` instance and returns ``True`` if the date is
            allowed for timers or else returns ``False``

          - ``tzdata`` which can be either ``None`` (default), a ``pytz``
            instance or a ``data feed`` instance.

            ``None``: ``when`` is interpreted at face value (which translates
            to handling it as if it where UTC even if it's not)

            ``pytz`` instance: ``when`` will be interpreted as being specified
            in the local time specified by the timezone instance.

            ``data feed`` instance: ``when`` will be interpreted as being
            specified in the local time specified by the ``tz`` parameter of
            the data feed instance.

            **Note**: If ``when`` is either ``SESSION_START`` or
              ``SESSION_END`` and ``tzdata`` is ``None``, the 1st *data feed*
              in the system (aka ``self.data0``) will be used as the reference
              to find out the session times.

          - ``*args``: any extra args will be passed to ``notify_timer``

          - ``**kwargs``: any extra kwargs will be passed to ``notify_timer``

        Return Value:

          - The created timer
    '''

    def __init__(self, **kwargs): 
        self.when = kwargs.get("when", None)

        offset = kwargs.get("offset", timedelta())
        self.offset = offset.total_seconds() # avoid missing day

        repeat = kwargs.get("repeat", timedelta())
        self.repeat = repeat.total_seconds()

        self.weekdays = sorted(list(kwargs.get('weekdays', [])))
        self.weekcarry = kwargs.get("weekcarry", False)
        self.monthdays = sorted(list(kwargs.get('monthdays', [])))
        self.monthcarry =  kwargs.get("monthcarry", True)
        self._tzdata = kwargs.get("tzdata", None)
        self.allow = kwargs.get("allow", None) # callable that allows a timer to take place

        # reset status
        self._rstwhen = None
        self._isdata = False

        # record last tick and date
        self._dwhen = None
        self._dtwhen = 0.0
        self._lastcall = date.min 
        
        self._curmonth = -1  # non-existent month
        self._curmonth_idx = 0

        self._curweek = -1  # non-existent week
        self._curweek_idx = 0
        
        self._nextdteos = 0.0
        self._curdate = date.min
        
        # add timerEvent
        self.event_type = kwargs.get("event_type", 0) # default metrics

    cpdef void start(self, object data):
        self._tzdata = data

        # write down the 'reset when' value
        if not isinstance(self.when, int):  # expect time/datetime
            self._rstwhen = self.when
        else:
            if self.when == SESSION_START:
                self._rstwhen = self._tzdata.p.sessionstart
            elif self.when == SESSION_END:
                self._rstwhen = self._tzdata.p.sessionend

        self._isdata = isinstance(self._tzdata, AbstractDataBase)
        self._reset_when()

    cdef void _reset_when(self, object ddate=datetime.min):
        self._dwhen = None
        self._dtwhen = 0

        self._lastcall = ddate

    cdef bint _check_month(self, int32_t dday, int32_t dmonth):
        cdef bint curday = False
        cdef bint daycarry = False
        cdef int32_t val
        cdef int32_t length = len(self.monthdays)
        
        if not self.monthdays:
            return True
        
        if dmonth != self._curmonth:
            self._curmonth = dmonth
            if self.monthcarry and self._curmonth_idx < length:
                daycarry = True
            self._curmonth_idx = 0 
        
        while self._curmonth_idx < length:
            val = self.monthdays[self._curmonth_idx]
            if val < dday:
                self._curmonth_idx += 1
                if self.monthcarry:
                    daycarry = True
            elif val == dday:
                curday = True
                self._curmonth_idx += 1
                break
            else:
                break
        return daycarry or curday

    cdef bint _check_week(self, int32_t dwkday, int32_t dweek):
        cdef int32_t length = len(self.weekdays)
        cdef bint daycarry = False
        cdef bint curday = False
        cdef int32_t val
        
        if not self.weekdays:
            return True

        if dweek != self._curweek:
            self._curweek = dweek
            if self.weekcarry and self._curweek_idx < length:
                daycarry = True
            self._curweek_idx = 0
            
        while self._curweek_idx < length:
            val = self.weekdays[self._curweek_idx]
            if val < dwkday:
                self._curweek_idx += 1
                if self.weekcarry:
                    daycarry = True
            elif val == dwkday:
                curday = True
                self._curweek_idx += 1
                break
            else:
                break
        return daycarry or curday

    cpdef bint check(self, double dt):
        cdef int32_t dday, dmonth, dweek, dwkday
        cdef bint valid
        cdef object d, ddate, nexteos

        if self._dtwhen > 0.0 and dt < self._dtwhen: # not reach target
            return False

        if self._isdata:
            d = self._tzdata.num2date(dt)
        else:
            d = num2date(dt) # UTC ---> Aisa

        ddate = d.date()

        # non-repeating timers fire at most once per day: _lastcall records the
        # day of the last trigger/reset, and without this guard every bar after
        # the trigger re-generates the (already past) target time and fires again
        if self.repeat <= 0.0 and ddate == self._lastcall:
            return False

        if ddate > self._curdate:
            self._curdate = ddate
            
            dday = ddate.day
            dmonth = ddate.month
            _, dweek, dwkday = ddate.isocalendar()

            valid = self._check_month(dday, dmonth)
            if valid:
                valid = self._check_week(dwkday, dweek)
            
            if valid and self.allow is not None:
                valid = self.allow(ddate)
            
            if not valid:
                self._reset_when(ddate)
                return False
            
            self._dtwhen = 0.0 # reset on new day 

        if self._dtwhen <= 0.0: # generate first _dtwhen
            dwhen = datetime.combine(ddate, self._rstwhen) # attach when / session_start
            if d.tzinfo is not None:
                dwhen = dwhen.replace(tzinfo=d.tzinfo)
            
            if self.offset != 0:
                dwhen += timedelta(seconds=self.offset)
            
            self._dwhen = dwhen
            
            if self._isdata:
                self._dtwhen = self._tzdata.date2num(dwhen) # 转换为 double
            else:
                self._dtwhen = date2num(dwhen)

            if dt < self._dtwhen:
                return False

        # ==================================================================
        # dt >= self._dtwhen ---> trigger and calculate next self._dtwhen
        # ==================================================================

        self._lastcall = ddate 

        if self.repeat <= 0.0:
            self._reset_when(ddate)
        else:
            if dt > self._nextdteos:
                if self._isdata:  
                    _, self._nextdteos = self._tzdata._getnexteos() # session_end
                else:  
                    nexteos = datetime.combine(ddate, datetime.max.time()) # 23:59:59.999999 
                    if d.tzinfo is not None:
                        nexteos = nexteos.replace(tzinfo=d.tzinfo)
                    self._nextdteos = date2num(nexteos)
            
            while True:
                self._dtwhen += self.repeat # timestamp
                
                # --- Session End (EOS)  ---
                if self._dtwhen > self._nextdteos:
                    if (self._dtwhen - self.repeat) < self._nextdteos: # first cross session_end
                        self._dtwhen = self._nextdteos # include eos
                        
                        if self._isdata:
                            self._dwhen = self._tzdata.num2date(self._dtwhen)
                        else:
                            self._dwhen = num2date(self._dtwhen)
                        break
                    else:
                        self._reset_when(ddate) # already next day
                        break
                    
                # next self._dtwhen
                if self._dtwhen > dt:
                    if self._isdata:
                        self._dwhen = self._tzdata.num2date(self._dtwhen)
                    else:
                        self._dwhen = num2date(self._dtwhen)
                    break
                    
        return True
