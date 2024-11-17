# /usr/bin/env python3
# -*- coding: utf-8 -*-

import pydantic
import numpy as np # type: ignore
from enum import Enum


class AlarmEnum(Enum):

    Loss = 1
    Drawback = 2


class AlarmEvent(pydantic.BaseModel):

    alarm_type: int
    alert_message: str


class Alarm(object):

    def emit_alarm(self, account_meta, live_ticks):
        raise NotImplementedError()
    

class LossFuse(Alarm):

    def __init__(self, risk):
        
        self._thres = risk

    def emit_alarm(self, position, live_ticks):
        returns = position.position_returns
        trigger = returns[-1] <= - abs(self._thres)
        return trigger


class DrawFuse(Alarm): 
    
    def __init__(self, withdraw):
        self._thres = withdraw

    def emit_alarm(self, position, live_ticks):
        returns = position.position_returns
        top = max(np.cumprod(returns.values()))
        trigger = (returns[-1] - top) / top > self._thres
        return trigger

