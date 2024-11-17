# !/usr/bin/env python3
# -*- coding: utf-8 -*-
import array
import operator
import collections
from enum import Enum
from datetime import datetime
from typing import Callable
from meta import ParamBase
import re

class TimeFrame(Enum):

    second = 0
    minute = 1
    daily = 2
    week = 3
    year = 4
  

def num2calendar(timestamp: int):
    # isocalendar --- year month weekday
    dt = datetime.fromtimestamp(timestamp)
    return dt.isocalendar()


class LineRoot(ParamBase):

    def qbuffer(self, savemem):
        pass

    def updateperiod(self, period):
        pass

    def prenext(self):
        pass

    def next(self):
        pass


class LineBuffer(LineRoot):

    params = (
        ("sessionstart", None),
        ("sessionend", None)
    )

    def __init__(self, alias, datas, frame=TimeFrame.daily):
        self.alias = alias
        # buffer 无法改变值
        self.buffer = array.array("d", list(datas.values()))
        self.ticks = array.array("d", list(datas.keys()))
        self.frame = frame
        self.idx = 0

    def reset(self):
        if self.mode == self.QBuffer:
            # add extrasize to ensure resample/replay work because they will
            # use backwards to erase the last bar/tick before delivering a new
            # bar The previous forward would have discarded the bar "period"
            # times ago and it will not come back. Having + 1 in the size
            # allows the forward without removing that bar
            self.array = collections.deque(maxlen=self.maxlen + self.extrasize)
            self.useislice = True
        else:
            self.array = array.array(str('d'))
            self.useislice = False

        self.lencount = 0
        self.idx = -1
        self.extension = 0

    def get_idx(self):
        pass
    
    def __getitem__(self, idx: int):
        self.datas[self.idx + idx]

    def home(self):
        pass

    def forward(self, size: int):
        self.idx = self.idx + size

    def backwards(self, size: int):
        self.idx = self.idx - size

    def advance(self, size=1):
        self.idx = self.idx + size

    def rewind(self):
        self.idx = 0

    def extend(self, line: LineRoot):
        self.datas.extend(line.datas)

    def __len__(self):
        return len(self.datas[self.idx:])

    def _getnexteos(self):
        
        if not len(self):
            return datetime.datetime.min, 0.0
        
        dt = self[0]
        dtime = num2date(dt)
        nextedtos = datetime.datetime.combine(dtime, self.p.sessionend)
        nexteos = date2num(nextedtos)  # -> utc-like

        while self.ticks[self.idx] < nexteos:
            self.advance()
        yield self.idx

    def __add__(self, other):
        return self._operation(other, operator.__add__)

    def __radd__(self, other):
        return self._roperation(other, operator.__add__)

    def __sub__(self, other):
        return self._operation(other, operator.__sub__)

    def __rsub__(self, other):
        return self._roperation(other, operator.__sub__)

    def __mul__(self, other):
        return self._operation(other, operator.__mul__)

    def __rmul__(self, other):
        return self._roperation(other, operator.__mul__)

    def __div__(self, other):
        return self._operation(other, operator.__div__)

    def __rdiv__(self, other):
        return self._roperation(other, operator.__div__)

    def __floordiv__(self, other):
        return self._operation(other, operator.__floordiv__)

    def __rfloordiv__(self, other):
        return self._roperation(other, operator.__floordiv__)

    def __truediv__(self, other):
        return self._operation(other, operator.__truediv__)

    def __rtruediv__(self, other):
        return self._roperation(other, operator.__truediv__)

    def __pow__(self, other):
        return self._operation(other, operator.__pow__)

    def __rpow__(self, other):
        return self._roperation(other, operator.__pow__)

    def __abs__(self):
        return self._operationown(operator.__abs__)

    def __neg__(self):
        return self._operationown(operator.__neg__)

    def __lt__(self, other):
        return self._operation(other, operator.__lt__)

    def __gt__(self, other):
        return self._operation(other, operator.__gt__)

    def __le__(self, other):
        return self._operation(other, operator.__le__)

    def __ge__(self, other):
        return self._operation(other, operator.__ge__)

    def __eq__(self, other):
        return self._operation(other, operator.__eq__)

    def __ne__(self, other):
        return self._operation(other, operator.__ne__)

    def __nonzero__(self):
        return self._operationown(bool)

    __bool__ = __nonzero__

    # Python 3 forces explicit implementation of hash if
    # the class has redefined __eq__
    __hash__ = object.__hash__


class BaseResampler:

    def __call__(self, line: LineBuffer, freq: str):

        # assert freq within daily
        step = freq2int(freq)
        resample = array.array("d", 0)
        if line.frame < TimeFrame.daily:
            line.home()
            init = 0
            for index in line._getnexteos():
                intervals = range(init, index, step)
                resample.extend(intervals)
                init = index
        # clone buffer



class Aggregator:

    def __call__(self, line: LineBuffer, aggregator: Callable, freq: str):

        idxs = array.array("d", 0)
        buffer = array.array("f")
        if line.frame < TimeFrame.daily:
            line.home()
            origin = 0
            for eos in line._getnexteos():
                idxs.append(eos)
                data = aggregator(line[origin:eos])
                buffer.append(data)
                origin = eos
        # buffer clone


class LineMultiple(LineBuffer):

    @classmethod
    def _getlinealias(cls, i):
        '''
        Return the alias for a line given the index
        '''
        lines = cls._getlines()
        if i >= len(lines):
            return ''
        linealias = lines[i]
        return linealias

    @classmethod
    def getlinealiases(cls):
        return cls._getlines()

    def itersize(self):
        return iter(self.lines[0:self.size()])

    def __init__(self, initlines=None):
        '''
        Create the lines recording during "_derive" or else use the
        provided "initlines"
        '''
        self.lines = list()
        for line, linealias in enumerate(self._getlines()):
            kwargs = dict()
            self.lines.append(LineBuffer(**kwargs))

        # Add the required extralines
        for i in range(self._getlinesextra()):
            if not initlines:
                self.lines.append(LineBuffer())
            else:
                self.lines.append(initlines[i])

    def __len__(self):
        '''
        Proxy line operation
        '''
        return len(self.lines[0])

    def size(self):
        return len(self.lines) - self._getlinesextra()

    def fullsize(self):
        return len(self.lines)

    def extrasize(self):
        return self._getlinesextra()

    def __getitem__(self, line):
        '''
        Proxy line operation
        '''
        return self.lines[line]

    def get(self, ago=0, size=1, line=0):
        '''
        Proxy line operation
        '''
        return self.lines[line].get(ago, size=size)

    def __setitem__(self, line, value):
        '''
        Proxy line operation
        '''
        setattr(self, self._getlinealias(line), value)

    def forward(self, value=NAN, size=1):
        '''
        Proxy line operation
        '''
        for line in self.lines:
            line.forward(value, size=size)

    def backwards(self, size=1, force=False):
        '''
        Proxy line operation
        '''
        for line in self.lines:
            line.backwards(size, force=force)

    def rewind(self, size=1):
        '''
        Proxy line operation
        '''
        for line in self.lines:
            line.rewind(size)

    def extend(self, value=NAN, size=0):
        '''
        Proxy line operation
        '''
        for line in self.lines:
            line.extend(value, size)

    def reset(self):
        '''
        Proxy line operation
        '''
        for line in self.lines:
            line.reset()

    def home(self):
        '''
        Proxy line operation
        '''
        for line in self.lines:
            line.home()

    def advance(self, size=1):
        '''
        Proxy line operation
        '''
        for line in self.lines:
            line.advance(size)

    def buflen(self, line=0):
        '''
        Proxy line operation
        '''
        return self.lines[line].buflen()


class Indicator:

    """
        # momentum / trend / reverse
        # resolution max window
    """

    params = (
        ("intraday", True),
        ("freq", "min"),
        ("window", 10),)

    def __init__(self, lines):

        self.buffer = lines
    
    def baseline(self):

        pattern = '[a-zA-Z]+'
        base_freq = re.match(pattern, self.p.freq)
        return base_freq
        
    def __call__(self, *args, **kwargs):
        
        """
            intraday
        """
        raise NotImplementedError("indicator logic")
    
    def align(self, other):
        assert self.baseline == other.baseline, "different base frequency must align"
        offset = self.p.window - other.p.window
        return offset

    def __add__(self, other):

        offset = self.align(other)
        if self.p.intraday:
            # getnexteos
            pass

    def __sub__(self, other):

        pass

    def __mul__(self, other):

        pass

    def __div__(self, other):

        pass
