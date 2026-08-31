import array as pyarray

import numpy as np

from bt_core.linebuffer import LineBuffer, QBuffer


def unbounded(values):
    lb = LineBuffer()
    for v in values:
        lb.forward(v)
    return lb


def ring(values, maxlen):
    lb = LineBuffer()
    lb.mode = QBuffer
    lb.maxlen = maxlen
    lb.reset()
    for v in values:
        lb.forward()
        lb[0] = v
    return lb


# ---------------------------------------------------------------- windows

def test_unbounded_get_windows():
    lb = unbounded([1.0, 2.0, 3.0, 4.0])
    assert list(lb.get(0, 1)) == [4.0]
    assert list(lb.get(0, 2)) == [3.0, 4.0]
    assert list(lb.get(0, 4)) == [1.0, 2.0, 3.0, 4.0]
    assert list(lb.get(-1, 2)) == [2.0, 3.0]


def test_ring_get_exact_size_and_order():
    # maxlen=4 写入 1..6: 物理槽 [5,6,3,4]，当前值 6
    lb = ring([1, 2, 3, 4, 5, 6], 4)
    assert lb[0] == 6.0 and lb[-1] == 5.0

    assert list(lb.get(0, 1)) == [6.0]
    assert list(lb.get(0, 2)) == [5.0, 6.0]
    assert list(lb.get(0, 3)) == [4.0, 5.0, 6.0], "不得多带窗口外元素"
    assert list(lb.get(0, 4)) == [3.0, 4.0, 5.0, 6.0], "环形回绕按时间升序"
    assert list(lb.get(-1, 2)) == [4.0, 5.0], "ago 必须被环形分支尊重"


def test_ring_get_no_wrap_phase():
    lb = ring([1, 2, 3], 4)
    assert list(lb.get(0, 3)) == [1.0, 2.0, 3.0]
    assert list(lb.get(0, 2)) == [2.0, 3.0]


def test_ring_get_future_wrap_exact_size():
    # 正 ago 把窗口右端推出环头(end_index > maxlen): 必须仍返回恰好 size 个
    # 元素, 不得因 numpy 对 array[:end_index] 的静默截断而带出整个数组
    # (修复前 get(ago=3, size=2) 返回 5 个元素 [4,5,6,3,4])
    lb = ring([1, 2, 3, 4, 5, 6], 4)  # 物理 [5,6,3,4], idx%4 = 1
    r = lb.get(3, 2)
    assert len(r) == 2, f"future-wrap 必须恰好 size 个元素, got {len(r)}"
    assert list(r) == [4.0, 5.0]  # [start=6%... , ] 物理 [3] + [0] 按时间序

    # 未来单边贴环头但不越界仍走连续分支
    r = lb.get(2, 2)
    assert list(r) == [3.0, 4.0] and len(r) == 2

    # 越界一整圈以上(end_index % maxlen 回到窗口内)长度也必须守恒
    r = lb.get(6, 3)
    assert len(r) == 3, f"got {len(r)}"


def test_reset_rewinds_indices():
    lb = ring([1, 2, 3], 4)
    lb.reset()
    assert lb.idx == -1 and lb._cur_idx == -1 and lb.lencount == 0
    assert np.all(np.isnan(lb.array)), "QBuffer reset 应回填 nan"

    lb2 = unbounded([1.0, 2.0])
    lb2.reset()
    assert lb2.idx == -1 and lb2._cur_idx == -1
    assert len(lb2.array) == 0


# ------------------------------------------------------------ apply_factor

def test_apply_factor_unbounded_scales_all_history():
    lb = unbounded([10.0, 11.0, 12.0])
    lb.apply_factor(0.5)
    assert list(lb.array) == [5.0, 5.5, 6.0]
    assert isinstance(lb.array, pyarray.array), "UnBounded 保持 array.array('d')"


def test_apply_factor_ring_scales_whole_buffer():
    lb = ring([10.0, 20.0, 30.0, 40.0], 4)
    lb.apply_factor(0.1)
    assert list(lb.array) == [1.0, 2.0, 3.0, 4.0]
    assert lb[0] == 4.0, "当前值同步被调整（保留当前值是 feed 层的契约）"
