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
from __future__ import (absolute_import, division, print_function,
                        unicode_literals)

from .metabase import with_metaclass, MetaParams
from typing import List, Dict, Any

from bt_protocol._protocol import SnapshotBody


class Sizer(with_metaclass(MetaParams, object)):
    '''This is the base class for *Sizers*. Any *sizer* should subclass this
    and override the ``_getsizing`` method

    Member Attribs:

      - ``strategy``: will be set by the strategy in which the sizer is working

        Gives access to the entire api of the strategy, for example if the
        actual data position would be needed in ``_getsizing``::

           position = self.strategy.getposition(data)

      - ``broker``: will be set by the strategy in which the sizer is working

        Gives access to information some complex sizers may need like portfolio
        value, ..
    '''
    params = (('stake', 0.9),) # retain 1 - stake for stafety 

    def getsizing(self, topk_info: Dict[bytes, Any], snapshot: SnapshotBody, isbuy: bool):
        return self._getsizing(topk_info, snapshot, isbuy)

    def _getsizing(self, topk_info: Dict[bytes, Any], snapshot: SnapshotBody, isbuy: bool):
        '''This method has to be overriden by subclasses of Sizer to provide
        the sizing functionality

        Params:
          - ``topk_info``: target of the operation, additional information about the target securities 

          - ``snapshot``: a snapshot of the current state of the strategy (see Strategy.snapshot())
          
          - ``isbuy``: will be ``True`` for *buy* operations and ``False``
            for *sell* operations

        The method has to return the actual size (an int) to be executed. If
        ``0`` is returned nothing will be executed.

        The absolute value of the returned value will be used

        '''
        raise NotImplementedError


SizerBase = Sizer  # alias for old naming
