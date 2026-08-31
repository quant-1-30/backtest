#！/usr/bin/env python
# -*- coding: utf-8; py-indent-offset:4 -*-

import operator
from . import metabase
from .metabase import with_metaclass


class MetaLineRoot(metabase.MetaParams):
    '''
    Once the object is created (effectively pre-init) the "owner" of this
    class is sought
    '''

    def donew(cls, *args, **kwargs):
        # print("MetaLineRoot donew")
        _obj, args, kwargs = super(MetaLineRoot, cls).donew(*args, **kwargs)

        # Find the owner and store it
        # startlevel = 4 ... to skip intermediate call stacks
        ownerskip = kwargs.pop('_ownerskip', None)
        _obj._owner = metabase.findowner(_obj,
                                         _obj._OwnerCls or LineMultiple,
                                         skip=ownerskip)

        # Parameter values have now been set before __init__
        return _obj, args, kwargs


class LineRoot(with_metaclass(MetaLineRoot, object)):
    '''
    Defines a common base and interfaces for Single and Multiple
    LineXXX instances

        Period management
        Iteration management
        Operation (dual/single operand) Management
        Rich Comparison operator definition
    '''
    _OwnerCls = None
    _minperiod = 1
    # _opstage = 1
 
    IndType, StratType, ObsType = range(3)

    # def _operation(self, other, operation, r=False, intify=False):
    #     if self._opstage == 1:
    #         return self._operation_stage1(
    #             other, operation, r=r, intify=intify)

    #     return self._operation_stage2(other, operation, r=r)
    
    def _operation(self, other, operation, r=False, intify=False):
        return self._operation_stage(
                other, operation, r=r, intify=intify)

    def _roperation(self, other, operation, intify=False):
        '''
        Relies on self._operation to and passes "r" True to define a
        reverse operation
        '''
        return self._operation(other, operation, r=True, intify=intify)

    # def _operation_stage1(self, other, operation, r=False, intify=False):
    #     '''
    #     Two operands' operation. Scanning of other happens to understand
    #     if other must be directly an operand or rather a subitem thereof
    #     '''
    #     if isinstance(other, LineMultiple):
    #         other = other.lines[0]

    #     return self._makeoperation(other, operation, r=r, _ownerskip=self)
    
    # def _operation_stage2(self, other, operation, r=False):
    #     '''
    #     Rich Comparison operator. Scans other and returns either an
    #     operation with other directly or a subitem from other
    #     '''
    #     if isinstance(other, LineRoot):
    #         other = other[0]

    #     # operation(float, other) ... expecting other to be a float
    #     if r:
    #         return operation(other, self[0])

    #     return operation(self[0], other)
    
    def _operation_stage(self, other, operation, r=False, intify=False):
        if isinstance(other, LineMultiple):
            other = other.lines[0]

        return self._makeoperation(other, operation, r=r, _ownerskip=self)

    # def _operationown(self, operation):
    #     if self._opstage == 1:
    #         return self._operationown_stage1(operation)

    #     return self._operationown_stage2(operation)

    def _operationown(self, operation):
        return self._operationown_stage(operation)
    
    # def _operationown_stage1(self, operation):
    #     '''
    #     Operation with single operand which is "self"
    #     '''
    #     return self._makeoperationown(operation, _ownerskip=self)
    
    # def _operationown_stage2(self, operation):
    #     return operation(self[0])
    
    def _operationown_stage(self, operation):
        '''
        Operation with single operand which is "self"
        '''
        return self._makeoperationown(operation, _ownerskip=self)

    # Arithmetic operator
    def _makeoperation(self, other, operation, r=False, _ownerskip=None):
        raise NotImplementedError

    def _makeoperationown(self, operation, _ownerskip=None):
        raise NotImplementedError

    def qbuffer(self, savemem=0):
        '''Change the lines to implement a minimum size qbuffer scheme'''
        raise NotImplementedError

    def minbuffer(self, size):
        '''Receive notification of how large the buffer must at least be'''
        raise NotImplementedError

    def setminperiod(self, minperiod):
        '''
        Direct minperiod manipulation. It could be used for example
        by a strategy
        to not wait for all indicators to produce a value
        '''
        self._minperiod = minperiod

    def updateminperiod(self, minperiod):
        '''
        Update the minperiod if needed. The minperiod will have been
        calculated elsewhere
        and has to take over if greater that self's
        '''
        self._minperiod = max(self._minperiod, minperiod)

    def addminperiod(self, minperiod):
        '''
        Add a minperiod to own ... to be defined by subclasses
        '''
        raise NotImplementedError

    # def incminperiod(self, minperiod):
    #     '''
    #     Increment the minperiod with no considerations
    #     '''
    #     raise NotImplementedError

    def prenext(self):
        '''
        It will be called during the "minperiod" phase of an iteration.
        '''
        pass

    def nextstart(self):
        '''
        It will be called when the minperiod phase is over for the 1st
        post-minperiod value. Only called once and defaults to automatically
        calling next
        '''
        self.next()

    def next(self):
        '''
        Called to calculate values when the minperiod is over
        '''
        pass

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


class LineMultiple(LineRoot):
    '''
    Base class for LineXXX instances that hold more than one line
    '''
    def reset(self):
        self.lines.reset()

    def addminperiod(self, minperiod):
        '''
        The passed minperiod is fed to the lines
        '''
        # pass it down to the lines
        for line in self.lines:
            line.addminperiod(minperiod)

    # def incminperiod(self, minperiod):
    #     '''
    #     The passed minperiod is fed to the lines
    #     '''
    #     # pass it down to the lines
    #     for line in self.lines:
    #         line.incminperiod(minperiod)

    def _makeoperation(self, other, operation, r=False, _ownerskip=None):
        return self.lines[0]._makeoperation(other, operation, r=r, _ownerskip=_ownerskip)

    def _makeoperationown(self, operation, _ownerskip=None):
        return self.lines[0]._makeoperationown(operation, _ownerskip=_ownerskip)

    def qbuffer(self, savemem=0):
        for line in self.lines:
            line.qbuffer(savemem)

    def minbuffer(self, size):
        for line in self.lines:
            line.minbuffer(size)


class LineSingle(LineRoot):
    '''
    Base class for LineXXX instances that hold a single line
    '''
    def addminperiod(self, minperiod):
        '''
        Add the minperiod (substracting the overlapping 1 minimum period)
        '''
        # print("lineSingle addminperiod ", minperiod)
        self._minperiod += minperiod - 1 # overlapping 1 minimum period 

    def incminperiod(self, minperiod):
        '''
        Increment the minperiod with no considerations
        '''
        self._minperiod += minperiod
