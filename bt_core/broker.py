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

from bt_core.metabase import with_metaclass, MetaParams


class MetaBroker(MetaParams):
    
    def __init__(cls, name, bases, dct):
        '''Class has already been created ... register'''
        # Initialize the class
        super(MetaBroker, cls).__init__(name, bases, dct)
    
    def donew(cls, *args, **kwargs):
        _obj, args, kwargs = super(MetaBroker, cls).donew(*args, **kwargs) 
        _obj.tdapi = _obj.p.tdapi
        return _obj, args, kwargs
    

class BrokerBase(with_metaclass(MetaBroker, object)):
    '''Broker implementation for Interactive Brokers.

    This class maps the orders/positions from Interactive Brokers to the
    internal API of ``backtrader``.

    Notes:

      - ``tradeid`` is not really supported, because the profit and loss are
        taken directly from IB. Because (as expected) calculates it in FIFO
        manner, the pnl is not accurate for the tradeid.

      - Position

        If there is an open position for an asset at the beginning of
        operaitons or orders given by other means change a position, the trades
        calculated in the ``Strategy`` in cerebro will not reflect the reality.

        To avoid this, this broker would have to do its own position
        management which would also allow tradeid with multiple ids (profit and
        loss would also be calculated locally), but could be considered to be
        defeating the purpose of working with a live broker
    '''
    params = (
        ("timeout", -1),
        ("tdapi", None),
    )
    
    def _start(self):
        if not self.tdapi.connected():
            raise Exception("TDAPI not connected")

    def cancel(self, vtorder_id):
        # TdApi exposes no cancel endpoint yet; fail loudly instead of a
        # confusing AttributeError on a missing attribute
        raise NotImplementedError("order cancel is not supported by TdApi")

    def stop(self):
        pass
