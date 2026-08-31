
import argparse
import asyncio
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

# 费率分界(unix 秒, 与 comminfo.pyx 同源但此处独立抄录 —— 验证器不得 import 被验证逻辑)
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


async def fetch_events(sids):
    """全区间除权/配股事件, 按 sid -> {ex_date: [(type, a, b, c)]}"""
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

    # 引导 md 客户端(与 localstore.start 同构): rpc_async 依赖 runner loop
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
    exp = uuid.UUID(str(exp))  # asyncpg 返回自带 UUID 类型, 统一成标准 uuid
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
    # 真实时序重建: DB 里的 executed_dt/created_dt 是"成交 bar 的戳"而非提交时刻
    # (agents.md §5.11): 次晨 09:30 的 on_risk 卖单由停在昨夜末 bar 的策略时钟
    # 盖昨日的戳, filler 按戳回填到昨日 bar 上成交。判别规则: 卖单 size 超过
    # 盖章日当时的可卖量(T+1 使其不可能当日成交) => 必为次晨卖单, 推迟到
    # D+1 开盘前(解锁/事件之后、当日成交之前)应用 —— 与引擎真实时序一致。
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
    deferred = []  # 次晨卖单: (sid, size)
    last_day = None
    all_days = sorted(set(bits_by_day) | {r["datetime"] for r in vpos})

    for day in all_days:
        # 日切换: 解锁 -> 到期事件 -> 次晨卖单 -> 当日成交
        if last_day is not None and day > last_day:
            for s in sids:
                p = st[s]
                p["avail"] = p["size"]
                apply_events(s, p, last_day, day, day)
            for s, sz in deferred:
                apply_sell(st[s], sz)
            deferred = []
        elif deferred:  # 日序不连续之外仍有遗留(防御)
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
    # 已知模式: 盖章日歧义的次晨卖单会造成个别行差异, 但次行自愈(agents.md
    # §5.11, 单标的验证时为 12/2590)—— 只有"不自愈"的差异才算失败。
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
    """
        DB 反向验证器: 对一次回测运行的 account / vtposition / vtorder / order_bit
        做独立于引擎的重算与逐项核对(agents.md §6.2 口径)

        用法(需要 md-server 在线取除权事件, PG 环境变量已在 .env):

            $PY tests/verify_event_cash.py --experiment <uuid> --cash 100000
            $PY tests/verify_event_cash.py --client 5a1f0c9e-... --cash 100000  # 该 client 最新实验

        检查项:
        1. account.datetime 全部落在 [19900101, 21001231] 且逐日唯一
        2. 逐 order_bit 费率恒等式(佣金分界/印花税/过户费三分界/交易所路由), 容差 0.005
        3. 现金守恒: init - Σ(±px*sz) - Σcomm + Σ分红 - Σ配股缴款 == 末日 cash
        4. 买入全部整手; vtposition 恒 available <= size
        5. 独立重放成交+除权事件 -> 与 vtposition 逐行 diff(允许已知的 T+1 次晨自愈差异)

        重放语义(与引擎一致, 见 agents.md §4/§5.11):
        - 日切换: 先无条件 T+1 解锁(available = size), 再按 (prev, curr] 到期区间
            应用除权/配股事件(停牌洞内事件在复牌日补派)
        - 送转: size/available 同按 floor 截断, cost /= sizer_ratio, 现金 += size*bonus/10
        - 配股: rights = floor(size*ratio/10), cost 加权, 现金 -= rights*price, available 不变(T+1)
    """
    sys.exit(asyncio.run(main()))
