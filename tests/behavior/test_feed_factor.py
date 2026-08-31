import types

from bt_core.feed import AbstractDataBase
from bt_core.linebuffer import LineBuffer

# 北京时间某日 10:00 的 unix ts -> 用 ymd 直推：ymd*1 与真实 ts 无关，
# apply_factor 内部只做 ts2intdt(datetime[0])，构造等差 ts 即可
def ts_of(ymd):
    # 任意单调 ts 都行；用 ymd*86400 保证 ts2intdt 落回同一 ymd 不划算，
    # 直接借助 dateintern 的可逆性构造
    from datetime import datetime, timezone
    y, m, d = ymd // 10000, (ymd // 100) % 100, ymd % 100
    return datetime(y, m, d, 2, 0, tzinfo=timezone.utc).timestamp()


def make_line(values):
    lb = LineBuffer()
    for v in values:
        lb.forward(v)
    return lb


class Stub:
    """apply_factor 触碰到的最小属性集"""

    def __init__(self, adj_factors, record_dt, current_ts, n_hist=3):
        import numpy as np
        rng = np.random.default_rng(7)
        self.lines = types.SimpleNamespace(datetime=make_line(
            [current_ts - 86400.0 * (n_hist - i) for i in range(1, n_hist + 1)] + [current_ts]))
        self.open = make_line(list(rng.uniform(8, 9, n_hist + 1)))
        self.high = make_line(list(rng.uniform(9, 10, n_hist + 1)))
        self.low = make_line(list(rng.uniform(7, 8, n_hist + 1)))
        self.close = make_line(list(rng.uniform(8, 9, n_hist + 1)))
        self.volume = make_line(list(rng.uniform(1e4, 2e4, n_hist + 1)))
        self.adj_factors = adj_factors
        self.record_dt = record_dt

    def hist(self, name):
        return list(getattr(self, name).array)[:-1]

    def cur(self, name):
        return getattr(self, name)[0]


def apply(stub):
    AbstractDataBase.apply_factor(stub)


def test_single_ex_date_adjusts_history_not_current():
    ex = 20240605
    s = Stub({ex: 0.8}, record_dt=0, current_ts=ts_of(ex), n_hist=3)
    prices = {n: (s.hist(n), s.cur(n)) for n in ("open", "high", "low", "close")}
    vols, vol_cur = s.hist("volume"), s.cur("volume")

    apply(s)

    for n, (h, c) in prices.items():
        assert all(abs(a - b * 0.8) < 1e-9 for a, b in zip(s.hist(n), h)), f"{n} history *factor"
        assert abs(s.cur(n) - c) < 1e-12, f"{n} current bar stays raw"
    assert all(abs(a - b / 0.8) < 1e-6 for a, b in zip(s.hist("volume"), vols))
    assert abs(s.cur("volume") - vol_cur) < 1e-6
    assert s.record_dt == ex


def test_suspension_window_compounds_due_factors():
    # 停牌: record_dt=D2, 窗口内两个 ex-date（D3 在洞内、D10 为复牌日）
    d3, d10 = 20240103, 20240110
    s = Stub({d3: 0.5, d10: 0.8}, record_dt=20240102, current_ts=ts_of(d10))
    closes = s.hist("close")
    apply(s)
    assert all(abs(a - b * 0.4) < 1e-9 for a, b in zip(s.hist("close"), closes)), \
        "复合因子 0.5*0.8 一次性补乘"
    assert s.record_dt == d10


def test_exact_match_boundary_is_inclusive_right():
    # current == record_dt: 无到期因子, 不动
    s = Stub({20240605: 0.8}, record_dt=20240605, current_ts=ts_of(20240605))
    closes = s.hist("close")
    apply(s)
    assert s.hist("close") == closes
    assert s.record_dt == 20240605

    # current = record_dt + 1 个 ex-date: (record, current] 右闭 -> 应用
    s2 = Stub({20240606: 0.8}, record_dt=20240605, current_ts=ts_of(20240606))
    closes2 = s2.hist("close")
    apply(s2)
    assert all(abs(a - b * 0.8) < 1e-9 for a, b in zip(s2.hist("close"), closes2))


def test_warmup_single_bar_advances_record():
    # warmup 阶段（首根 bar 即 ex-date）: 历史为空, 当根保留, record_dt 推进
    s = Stub({20240105: 0.9}, record_dt=0, current_ts=ts_of(20240105), n_hist=1)
    c = s.cur("close")
    apply(s)
    assert abs(s.cur("close") - c) < 1e-12
    assert s.record_dt == 20240105


def test_empty_factors_noop():
    s = Stub({}, record_dt=0, current_ts=ts_of(20240605))
    closes = s.hist("close")
    apply(s)
    assert s.hist("close") == closes


def test_two_rollovers_match_cumulative_reference():
    """逐事件连乘(两次 rollover) 必须等价于 bt_sdk join_asof 的累计系数一次性乘。

    实测口径(bt_sdk adj_factor.calc_adjust_factors, 十派10 + 十转10):
      raw_factors = {dt1: 0.99, dt2: 0.5}          <- 单事件比率(bt_core 用这个)
      adj_factors = {dt1: 0.99*0.5, dt2: 0.5}      <- 累计后缀积(参考实现用这个)
    era A(<dt1) 最终应 ×0.495, era B([dt1,dt2)) 应 ×0.5, dt2 当根保持原始。
    若有人把 get_adjfactor 换成累计系数, era A 会变成 0.495*0.5 —— 本测试即红。
    """
    dt1, dt2 = 20240110, 20240410
    r1, r2 = 0.99, 0.5                      # raw_factors
    cum1, cum2 = r1 * r2, r2                # adj_factors(前复权累计)

    era_a = [10.0, 10.0, 10.0]              # < dt1 原始值
    era_b = [20.0, 20.0, 20.0]              # [dt1, dt2)
    factors = {dt1: r1, dt2: r2}

    # ---- 第一次 rollover: dt1 当根, buffer = era A + dt1 ----
    s = Stub(factors, record_dt=0, current_ts=ts_of(dt1), n_hist=3)
    apply(s)
    assert s.record_dt == dt1

    # ---- 第二次 rollover: dt2 当根, buffer = era A(已×r1) + era B(原始) + dt2 ----
    # (独立 Stub, era A 手动带入第一次 rollover 后的值: 价格×r1, 量/r1)
    era_a_adj = [v * r1 for v in era_a]
    era_a_vol = [100.0 / r1] * 3
    s2 = Stub(factors, record_dt=dt1, current_ts=ts_of(dt2), n_hist=6)
    s2.open = make_line(era_a_adj + era_b + [30.0])
    s2.high = make_line(era_a_adj + era_b + [31.0])
    s2.low = make_line(era_a_adj + era_b + [29.0])
    s2.close = make_line(era_a_adj + era_b + [30.0])
    s2.volume = make_line(era_a_vol + [100.0] * 3 + [7.0])
    apply(s2)

    closes = s2.hist("close")
    # era A: 已 ×r1, 再 ×r2 -> 累计 cum1 (参考实现 adj_factors[dt1])
    assert all(abs(closes[i] - era_a[i] * cum1) < 1e-9 for i in range(3)), \
        f"era A 应累计 ×{cum1}, got {closes[:3]}"
    # era B: 首次调整 ×r2 -> 累计 cum2 (参考实现 adj_factors[dt2])
    assert all(abs(closes[3 + i] - era_b[i] * cum2) < 1e-9 for i in range(3)), \
        f"era B 应 ×{cum2}, got {closes[3:]}"
    # 当根(dt2 除权日)保持原始
    assert abs(s2.cur("close") - 30.0) < 1e-12
    # 量反向: era A /cum1, era B /cum2
    vols = s2.hist("volume")
    assert all(abs(vols[i] - 100.0 / cum1) < 1e-6 for i in range(3))
    assert all(abs(vols[3 + i] - 100.0 / cum2) < 1e-6 for i in range(3))
    assert s2.record_dt == dt2
