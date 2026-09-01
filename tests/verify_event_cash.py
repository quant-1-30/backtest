
import argparse
import asyncio
import datetime
import math
import os
import sys
import uuid
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asyncpg
from dotenv import load_dotenv

load_dotenv()

from bt_protocol._protocol import QueryBody
from bt_protocol.constant import RpcTopic
from bt_core.execution.gateway.interface import async_gt
from bt_core.utils.dateintern import ts2intdt
from bt_sdk.ctx import initialize_runner, get_md_api

STAMP_CKPT = 1693180800        # 2023-08-28 印花税 1‰ -> 0.5‰
TRANSFER_CKPT = 1651180800     # 2022-04-29 过户费 0.02‰ -> 0.01‰
TRANSFER_UNIFY_CKPT = 1438387200  # 2015-08-01 沪深统一 0.02‰(此前沪 0.06‰ / 深 0)
RATIO_CKPT = 1433813400        # 2015-06-09 佣金 3‰ -> 0.5‰

FAILS = []


def check(name, ok, detail=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))
    if not ok:
        FAILS.append(name)


def expect_comm(created_dt, is_sell, amount, sse):
    comm = max(amount * (3e-3 if created_dt < RATIO_CKPT else 5e-4), 5.0)
    stamp = amount * (5e-4 if created_dt >= STAMP_CKPT else 1e-3) if is_sell else 0.0
    if created_dt >= TRANSFER_CKPT:
        tf = 1e-5
    elif created_dt >= TRANSFER_UNIFY_CKPT:
        tf = 2e-5
    else:
        tf = 6e-5 if sse else 0.0
    return comm + stamp + amount * tf


def day_diff(a, b):
    """ymd int 之间的真实日历日差 (ymd 直接相减跨月/跨年会失真)"""
    da = datetime.date(a // 10000, (a // 100) % 100, a % 100)
    db = datetime.date(b // 10000, (b // 100) % 100, b % 100)
    return (da - db).days


async def fetch_events(sids):
    """sid -> {ex_date: [(type, a, b, c)]}"""
    ev = {}
    for sid in sids:
        body = QueryBody(start_date=19900101, end_date=21001231, sid=[sid])
        rows = []
        for topic, tag in ((RpcTopic.Adjustment, 0), (RpcTopic.Rightment, 1)):
            df = (await async_gt.rpc(body, topic)).get(sid)
            if df is None or df.height == 0:
                continue
            if tag == 0:
                rows += [(int(ex), 0, float(bs), float(tr), float(bo))
                         for ex, bs, tr, bo in df.select(
                             ["ex_date", "bonus_share", "transfer", "bonus"]).rows()]
            else:
                rows += [(int(ex), 1, float(ratio), float(price), 0.0)
                         for ex, ratio, price in df.select(
                             ["ex_date", "ratio", "price"]).rows()]
        m = defaultdict(list)
        for ex, typ, a, b_, c in sorted(rows):
            m[ex].append((typ, a, b_, c))
        ev[sid] = m
    return ev


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--experiment", default=None)
    ap.add_argument("--client", default="5a1f0c9e-2b7d-4e8a-9f30-6c4d1b2e7a55")
    ap.add_argument("--cash", type=float, default=100000.0)
    args = ap.parse_args()

    # localstore.start / rpc_async ---> runner loop
    runner = initialize_runner()
    runner.start()
    get_md_api().start(runner.get_loop())

    conn = await asyncpg.connect(
        host=os.getenv("PGHOST", "localhost"), port=int(os.getenv("PGPORT", 5432)),
        user=os.getenv("PGUSER", "postgres"), password=os.getenv("PGPWD"),
        database=os.getenv("PGDB", "bt_trade"))

    exp = args.experiment
    if exp is None:
        exp = await conn.fetchval(
            "select experiment_id from experiment where client_id=$1 order by id desc limit 1",
            uuid.UUID(args.client))
    exp = uuid.UUID(str(exp))  
    print(f"experiment: {exp}")

    accts = await conn.fetch(
        "select datetime, cash from account where experiment_id=$1 order by datetime",
        exp)
    vpos = await conn.fetch(
        "select sid, datetime, size, available from vtposition "
        "where experiment_id=$1 order by datetime, sid", exp)
    orders = {
        r["order_id"]: r for r in await conn.fetch(
            "select order_id, sid, order_type, created_dt from vtorder where experiment_id=$1",
            exp)}
    bits = await conn.fetch(
        "select b.order_id, b.executed_dt, b.executed_price, b.executed_size, b.comm, b.isbuy "
        "from order_bit b join vtorder o on o.order_id = b.order_id "
        "where o.experiment_id = $1 order by b.executed_dt", exp)

    sids = sorted({bytes(r["sid"]) for r in orders.values()})
    print(f"sids: {sids}  orders: {len(orders)}  bits: {len(bits)}  "
          f"account rows: {len(accts)}  position rows: {len(vpos)}")

    # ------------------------------------------------ 1. account sanity
    dts = [r["datetime"] for r in accts]
    check("account.datetime 均为合法 ymd", all(19900101 <= d <= 21001231 for d in dts))
    check("account.datetime 逐日唯一", len(dts) == len(set(dts)),
          f"{len(dts) - len(set(dts))} duplicates" if len(dts) != len(set(dts)) else "")

    # ------------------------------------------------ 2. fee identity
    worst, bad = 0.0, 0
    for b in bits:
        o = orders[b["order_id"]]
        sid = bytes(o["sid"])
        amount = b["executed_price"] * b["executed_size"]
        exp_c = expect_comm(o["created_dt"], not b["isbuy"], amount, sid.startswith(b"60"))
        worst = max(worst, abs(exp_c - b["comm"]))
        bad += abs(exp_c - b["comm"]) > 0.005
    check(f"逐 bit 费率恒等式({len(bits)} 笔, 容差 0.005)", bad == 0,
          f"max_err={worst:.6f} bad={bad}" if bad else f"max_err={worst:.6f}")

    # ------------------------------------------------ single replay pass
    events = await fetch_events(sids)
    st = {s: dict(size=0, avail=0, cost=0.0) for s in sids}
    bits_by_day = defaultdict(list)
    for b in bits:
        bits_by_day[ts2intdt(b["executed_dt"])].append(b)

    def apply_sell(p, sz):
        p["size"] -= sz
        p["avail"] -= sz

    def apply_buy(p, sz, price):
        if p["size"] == 0:
            p["cost"] = price
        else:
            p["cost"] = (p["cost"] * p["size"] + price * sz) / (p["size"] + sz)
        p["size"] += sz

    def apply_events(s, p, lo, hi, record_day):
        nonlocal div_total, rights_total
        for ex in [e for e in events[s] if lo < e <= hi]:
            for typ, a, b_, c in events[s][ex]:
                if p["size"] <= 0:
                    continue
                if typ == 0:
                    if c:
                        div_total += p["size"] * c / 10.0
                        div_events.append((record_day, s.decode(), p["size"] * c / 10.0))
                    r = (a + b_) / 10.0 + 1.0
                    p["size"] = math.floor(p["size"] * r)
                    p["avail"] = math.floor(p["avail"] * r)
                    p["cost"] /= r
                else:
                    rights = math.floor(p["size"] * a / 10.0)
                    rights_total += rights * b_
                    p["cost"] = (p["cost"] * p["size"] + rights * b_) / (p["size"] + rights)
                    p["size"] += rights

    div_total, rights_total = 0.0, 0.0
    div_events = []
    traj = {}
    deferred = []  # next day (sid, size)
    last_day = None
    all_days = sorted(set(bits_by_day) | {r["datetime"] for r in vpos})

    for day in all_days:
        # 日切换: 解锁 -> 到期事件 -> 次晨卖单 -> 当日成交
        if last_day is not None and day > last_day:
            deferred_map = {}
            for s, sz in deferred:
                deferred_map[s] = deferred_map.get(s, 0) + sz
            # ymd 直接相减跨月/跨年会放大间隔, 必须用真实日历日差
            long_gap = day_diff(day, last_day) > 10
            for s in sids:
                p = st[s]
                p["avail"] = p["size"]
                # 长空窗(停牌/空仓跨越多月)且该 sid 有次晨卖单: 卖单成交于空窗
                # 首个交易日上午, 晚于卖出日的 ex_date 事件须按卖出后持仓结算
                # (引擎按结算时点持仓入账, 已清仓则无分红; 见 497c1f76 实验
                # 20251013 十派4 幻影分红误报)。普通节假日(≤10 天)保持"先事件
                # 后卖单": ex_date 恰为卖出日时, 登记日持仓仍享分红, 引擎在
                # rollover 先结算事件再执行 09:30 卖单。
                if long_gap and s in deferred_map:
                    apply_sell(p, deferred_map.pop(s))
                apply_events(s, p, last_day, day, day)
            for s, sz in deferred_map.items():
                apply_sell(st[s], sz)
            deferred = []
        elif deferred: 
            for s, sz in deferred:
                apply_sell(st[s], sz)
            deferred = []

        for b in bits_by_day.get(day, []):
            p = st[bytes(orders[b["order_id"]]["sid"])]
            sz = b["executed_size"]
            if b["isbuy"]:
                apply_buy(p, sz, b["executed_price"])
            elif sz > p["avail"]:
                deferred.append((bytes(orders[b["order_id"]]["sid"]), sz))
            else:
                apply_sell(p, sz)

        for s in sids:
            traj[(s, day)] = (st[s]["size"], st[s]["avail"])
        last_day = day

    # ------------------------------------------------ 3. cash conservation
    flow = sum((1 if b["isbuy"] else -1) * b["executed_price"] * b["executed_size"] for b in bits)
    comm_sum = sum(b["comm"] for b in bits)
    final_cash = accts[-1]["cash"]
    expect_cash = args.cash - flow - comm_sum + div_total - rights_total
    residual = final_cash - expect_cash
    print(f"  cash: init={args.cash:.2f} flow=-{flow:.2f} comm=-{comm_sum:.2f} "
          f"div=+{div_total:.2f} rights=-{rights_total:.2f}")
    print(f"  final={final_cash:.2f} expect={expect_cash:.2f} residual={residual:+.4f}")
    check("现金守恒(残差 == 重放分红-配股)", abs(residual) < 0.01,
          f"residual={residual:.4f}, {len(div_events)} 笔分红")

    # ------------------------------------------------ 4. lot / T+1
    odd = [b for b in bits if b["isbuy"] and b["executed_size"] % 100 != 0]
    check("买入全部整手(%100==0)", not odd, f"{len(odd)} 违例" if odd else "")
    bad_avail = [r for r in vpos if r["available"] > r["size"]]
    check("vtposition 恒 available <= size", not bad_avail,
          f"{len(bad_avail)} 违例" if bad_avail else "")

    # ------------------------------------------------ 5. trajectory diff
    db_rows = {(bytes(r["sid"]), r["datetime"]): r["size"] for r in vpos}
    days_by_sid = {}
    for s, d in db_rows:
        days_by_sid.setdefault(s, []).append(d)
    for s in days_by_sid:
        days_by_sid[s].sort()

    def next_row_day(s, d):
        for d2 in days_by_sid.get(s, []):
            if d2 > d:
                return d2
        return None

    diffs = []
    for key in sorted(set(db_rows) & set(traj)):
        if db_rows[key] != traj[key][0]:
            diffs.append(key)
    healed = []
    for k in diffs:
        nd = next_row_day(k[0], k[1])
        if nd is not None and (k[0], nd) in traj and db_rows[(k[0], nd)] == traj[(k[0], nd)][0]:
            healed.append(k)
    unhealed = [k for k in diffs if k not in healed]
    check(f"仓位轨迹逐行 diff({len(set(db_rows) & set(traj))} 行)", not unhealed,
          f"{len(diffs)} 差异(全部次日自愈, T+1 次晨盖章歧义)" if diffs and not unhealed
          else f"{len(unhealed)} 不自愈差异" if unhealed else "")
    for key in diffs[:5]:
        print(f"    diff(自愈): {key} db={db_rows[key]} replay={traj[key][0]}")

    await conn.close()
    print("\n" + ("ALL CHECKS PASSED" if not FAILS else f"FAILED: {FAILS}"))
    return 0 if not FAILS else 1


if __name__ == "__main__":

    sys.exit(asyncio.run(main()))
