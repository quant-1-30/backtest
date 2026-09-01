import faulthandler
faulthandler.enable()

import os
import uuid
import datetime
import warnings

from dotenv import load_dotenv

import bt_core as bt
import bt_core.indicators as btind

from bt_core.cerebro import Cerebro
from bt_core.feed import DataBase
from bt_core.pnc import Pnc
from bt_core.utils.dateintern import ts2intdt
from bt_core.feeds import *  
from bt_core.brokers import *  

warnings.filterwarnings('ignore')

MULTI_CLIENT_ID = uuid.UUID("5a1f0c9e-2b7d-4e8a-9f30-6c4d1b2e7a55")


class FixedSize(bt.Sizer):
    def __init__(self, *args, **kwargs):
        self.stake = kwargs.get("stake", 1.0)

    def _getsizing(self, topk_info, snapshot, isbuy: bool):
        if isbuy:
            ratio = self.stake / len(topk_info)
            return {sid: ratio for sid in topk_info.keys()}
        return {p.sid: 1.0 for p in snapshot.positions if p.size > 0}


class MultiSignalPatch(DataBase):
    lines = ('datetime',)
    params = (("sids", ()),)

    def _load(self):
        return False

    def get_topk(self, current_day: int) -> dict:
        return {
            s: 0.0
            for i, s in enumerate(self.p.sids)
        }

    def notify_metrics(self, dts: int):
        pass


class MultiStrategy(bt.Strategy):
    params = (("name", "multi_instrument"),)

    def __init__(self):
        # datas : [master(minute), ddata(day), wdata(week), mdata(month), patch]
        self.sma_day = btind.SMA(self.datas[1].close, period=5)
        self.sma_week = btind.SMA(self.datas[2].close, period=2)
        self.sma_month = btind.SMA(self.datas[3].close, period=2)

    def next(self):
        d, w = self.sma_day[0], self.sma_week[0]
        if d > 0 and w > 0:
            assert 0.5 < w / d < 2.0, f"resample suspect: day={d} week={w}"



if __name__ == '__main__':

    load_dotenv()
    cerebro = Cerebro(client_id=MULTI_CLIENT_ID.bytes, fmt="parquet")

    cerebro.addstore("local")
    cerebro.addsizer(FixedSize)
    cerebro.addpnc(Pnc, days_held=5, stake=0.9, dd=0.25, max_positions=5)

    cerebro.add_timer(
        when=bt.timer.Session.SESSION_START,
        offset=datetime.timedelta(minutes=0),
        weekdays=[1, 2, 3, 4, 5],
        weekcarry=False,
        event_type=bt.timer.TimerEvent.RISK,
    )
    cerebro.add_timer(
        when=bt.timer.Session.SESSION_END,
        offset=datetime.timedelta(minutes=-10),
        weekdays=[1, 2, 3, 4, 5],
        weekcarry=False,
        event_type=bt.timer.TimerEvent.TRADE,
    )

    # resample 日 / 周 / 月
    cerebro.resampledata(timeframe=bt.TimeFrame.Days, adjbartime=False)
    cerebro.resampledata(timeframe=bt.TimeFrame.Weeks, adjbartime=False)
    cerebro.resampledata(timeframe=bt.TimeFrame.Months, adjbartime=False)

    cerebro.addstrategy(MultiStrategy)

    patch = MultiSignalPatch(sids=[b"300308", b"600000", b"000001"])
    cerebro.adddata(patch)

    sids = [b"300308", b"600000", b"000001"]
    fromdate = int(os.getenv("FROMDATE", 20040101))
    todate = int(os.getenv("TODATE", 20260531))
    try:
        cerebro.run(cash=100000, sid=sids, fromdate=fromdate, todate=todate, benchmark=[b"1A0001"])
    except Exception as e:
        print(f"运行报错: {e}")
        if hasattr(cerebro, '_shutdown'):
            cerebro._shutdown()
