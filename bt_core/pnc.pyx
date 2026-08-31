# cython: language_level=3
# cython: boundscheck=False
# cython: wraparound=False

from libc.stdint cimport int32_t, int64_t
from libcpp.unordered_map cimport unordered_map
from libcpp.algorithm cimport lower_bound, sort as c_sort
from cython.operator cimport dereference as deref # C++ Map decref

from bt_core.sizer cimport Sizer
from bt_core.utils.dateintern import ts2intdt


# ==============================================================================================
# C++ sort avoid by memory ptr
# ==============================================================================================
cdef inline bint compare_trader_plans(TraderPlan a, TraderPlan b) noexcept:
    return a.priority < b.priority


cdef inline bint compare_pairs(Pair a, Pair b) noexcept:
    # Priority: not in topk > longest held
    if a.in_topk != b.in_topk:
        return a.in_topk < b.in_topk  # Not in topk comes first
    return a.days_held > b.days_held  # Longer held comes first


cdef class Pnc:
    """
        a. sell first
        b. buy after
    """
    def __init__(self, Sizer sizer_obj, *args, **kwargs):
        self.sizer = sizer_obj

        self.interval = kwargs.pop("days_held", 5)
        self.stake = kwargs.pop("stake", 0.9)
        self.dd = kwargs.pop("dd", 0.25)
        self.stop_loss = kwargs.pop("stop_loss", -0.1)  # Default stop loss at -10% pnl_ratio
        self.max_positions = kwargs.pop("max_positions", 5)  # Default max 5 positions

        self._last_trade_day = 0

    # =============================================================================================
    # TradingDays from benchmarkret
    # =============================================================================================

    cpdef void _start(self, list trading_days):
        cdef int32_t d

        self.v_trading_days.clear()
        for d in trading_days:
            self.v_trading_days.push_back(d)

        # Clear pending_sells at start of each backtest
        self.pending_sells.clear()
        self._last_trade_day = 0

    cdef int32_t _get_days_held(self, int32_t created_day, int32_t current_day) noexcept nogil:
        if self.v_trading_days.empty():
            return 0

        cdef vector[int32_t].iterator it_created = lower_bound(self.v_trading_days.begin(), self.v_trading_days.end(), created_day)
        cdef vector[int32_t].iterator it_current = lower_bound(self.v_trading_days.begin(), self.v_trading_days.end(), current_day)

        return <int32_t>(it_current - it_created)

    # ==============================================================================================
    # risk control on tick
    # ==============================================================================================

    cpdef vector[TraderPlan] on_risk(self, object snapshot, dict stats, int32_t current_day, int32_t execType=0, bytes filler=b"default"):
        cdef double pnl
        cdef TraderPlan tmp
        cdef list positions = snapshot.positions

        # avid python heap
        cdef vector[TraderPlan] sells_by_risk
        cdef int32_t excess_count, sold_count, i, days_held
        cdef bint in_topk
        cdef vector[Pair] pos_with_days
        cdef Pair p_pair

        # ===========================================================================================
        # Clean up pending_sells at market open
        # ===========================================================================================
        # Remove pending_sells for positions that are no longer held or already sold
        # This prevents accumulation of stale pending_sells
        cdef unordered_map[cpp_string, int32_t] current_available
        cdef vector[cpp_string] keys_to_remove
        cdef cpp_string pos_sid

        # Build a map of current available positions for quick lookup
        for pos in positions:
            current_available[<cpp_string>pos.sid] = pos.available

        # Collect keys to remove
        for pos in positions:
            pos_sid = <cpp_string>pos.sid

        # Check each pending_sell
        for it_sell in self.pending_sells:
            pos_sid = it_sell.first
            # Check if position still exists and has available shares
            if current_available.find(pos_sid) == current_available.end() or current_available[pos_sid] == 0:
                keys_to_remove.push_back(pos_sid)

        # Remove collected keys
        for i in range(keys_to_remove.size()):
            self.pending_sells.erase(keys_to_remove[i])

        # ===========================================================================================
        # 1. Max Positions Enforcement (called at market open)
        # ===========================================================================================
        # This enforces max_positions constraint when on_risk is called at market open
        # Count only positions with available > 0 and not already pending sell
        cdef int32_t active_positions_count = 0
        for pos in positions:
            pos_sid = <cpp_string>pos.sid
            if pos.available > 0 and self.pending_sells.find(pos_sid) == self.pending_sells.end():
                active_positions_count += 1

        # Debug output
        # if active_positions_count > 0:
        #     print(f"[on_risk] Day {current_day}: active_positions={active_positions_count}, max_positions={self.max_positions}")

        # If we exceed max_positions, sell the excess (prioritize longest held, not in topk)
        if active_positions_count > self.max_positions:
            # print(f"[on_risk] Day {current_day}: EXCEEDED max_positions! Selling {active_positions_count - self.max_positions} positions")
            excess_count = active_positions_count - self.max_positions

            # Collect positions for sorting
            for pos in positions:
                pos_sid = <cpp_string>pos.sid
                if pos.available > 0 and self.pending_sells.find(pos_sid) == self.pending_sells.end():
                    days_held = self._get_days_held(ts2intdt(pos.created_dt), current_day)
                    # For on_risk, we don't have topk_info, so assume not in topk
                    in_topk = False

                    p_pair.sid = pos_sid
                    p_pair.days_held = days_held
                    p_pair.in_topk = 1 if in_topk else 0
                    p_pair.size = pos.available
                    pos_with_days.push_back(p_pair)

            # Sort: priority to sell = not in topk > longest held
            c_sort(pos_with_days.begin(), pos_with_days.end(), compare_pairs)

            # Add to sell plan (highest priority)
            sold_count = 0
            for i in range(pos_with_days.size()):
                if sold_count >= excess_count:
                    break
                p_pair = pos_with_days[i]

                tmp.sid = p_pair.sid
                tmp.weight = 1.0
                tmp.size = p_pair.size
                tmp.priority = 0  # Highest priority for max_positions enforcement
                tmp.execType = execType
                tmp.filler = filler

                sells_by_risk.push_back(tmp)
                self.pending_sells[p_pair.sid] = tmp
                sold_count += 1

        # ===========================================================================================
        # 2. Stop Loss Control --- pnl_ratio based risk control
        # ===========================================================================================
        cdef double pos_pnl_ratio
        for pos in positions:
            pos_sid = <cpp_string>pos.sid

            if pos.available > 0 and self.pending_sells.find(pos_sid) == self.pending_sells.end():
                pos_pnl_ratio = pos.pnl_ratio
                if pos_pnl_ratio <= self.stop_loss:
                    tmp.sid = pos_sid
                    tmp.weight = 1.0
                    tmp.size = pos.available
                    tmp.priority = 0  # Highest priority for stop loss
                    tmp.execType = execType
                    tmp.filler = filler

                    sells_by_risk.push_back(tmp)
                    self.pending_sells[pos_sid] = tmp

        # ===========================================================================================
        # 3. Macro Control --- Drawdown
        # ===========================================================================================
        if stats["drawdown"].maxdd >= self.dd:
            # print("reach maxdd and execute sell all")
            for pos in positions:
                c_sid = <cpp_string>pos.sid

                if pos.available > 0 and self.pending_sells.find(c_sid) == self.pending_sells.end():

                    tmp.sid = c_sid
                    tmp.weight = 1.0
                    tmp.size = pos.available
                    tmp.priority = 0
                    tmp.execType = execType
                    tmp.filler = filler

                    sells_by_risk.push_back(tmp)
                    self.pending_sells[c_sid] = tmp

        return sells_by_risk

    # ================================================================================================
    # generate execution plan
    # ================================================================================================

    cpdef unordered_map[cpp_string, vector[TraderPlan]] generate_plan(self, int32_t current_day, dict topk_info, object snapshot, int32_t execType=0, bytes filler=b"default"):
        cdef bytes py_sid
        cdef cpp_string c_sid

        cdef int32_t days_held, slots, buy_rank=10
        cdef double wgt_ratio
        cdef TraderPlan tmp

        cdef vector[TraderPlan] sells
        cdef vector[TraderPlan] buys
        cdef unordered_map[cpp_string, vector[TraderPlan]] plan

        cdef unordered_map[cpp_string, double] s_wgt, b_wgt
        cdef unordered_map[cpp_string, double].iterator it_wgt
        cdef unordered_map[cpp_string, double] c_topk_info = topk_info

        cdef list positions = snapshot.positions
        cdef object pos, account = snapshot.account

        cdef int32_t active_positions_count = 0
        cdef bint already_held
        cdef bint should_sell
        cdef bint in_topk

        # Max positions control variables
        cdef vector[Pair] pos_with_days
        cdef Pair p_pair
        cdef int32_t excess_count, sold_count, i
        cdef int32_t max_slots_from_topk, slots_by_max_pos

        if self._last_trade_day == current_day or c_topk_info.empty():
            plan[<cpp_string>b"sell"] = sells
            plan[<cpp_string>b"buy"] = buys
            return plan

        # ===================================================================================
        # PHASE 0: Clean up pending_sells at trade time
        # ===================================================================================
        # Remove pending_sells for positions that are no longer held or already sold
        cdef unordered_map[cpp_string, int32_t] current_available
        cdef vector[cpp_string] keys_to_remove

        # Build a map of current available positions for quick lookup
        for pos in positions:
            current_available[<cpp_string>pos.sid] = pos.available

        # Check each pending_sell
        for it_sell in self.pending_sells:
            c_sid = it_sell.first
            # Check if position still exists and has available shares
            if current_available.find(c_sid) == current_available.end() or current_available[c_sid] == 0:
                keys_to_remove.push_back(c_sid)

        # Remove collected keys
        for i in range(keys_to_remove.size()):
            self.pending_sells.erase(keys_to_remove[i])

        # ===================================================================================
        # PHASE 1: Calculate current active positions
        # ===================================================================================
        # Count positions EXCLUDING those in pending_sells; use size > 0 so that
        # T+1 locked buys (available == 0 until tomorrow) still occupy a slot,
        # otherwise same-day buys could breach max_positions
        active_positions_count = 0
        for pos in positions:
            c_sid = <cpp_string>pos.sid
            if pos.size > 0 and self.pending_sells.find(c_sid) == self.pending_sells.end():
                active_positions_count += 1

        # Debug output for PHASE 1
        # if active_positions_count > 0:
        #     print(f"[generate_plan PHASE1] Day {current_day}: active_positions={active_positions_count}, max_positions={self.max_positions}")

        # ===================================================================================
        # PHASE 2: Enforce max_positions limit FIRST (highest priority)
        # ===================================================================================
        if active_positions_count > self.max_positions:
            excess_count = active_positions_count - self.max_positions
            # print(f"[generate_plan PHASE2] Day {current_day}: EXCEEDED max_positions! Selling {excess_count} positions")

            # Collect active positions for sorting
            pos_with_days.clear()
            for pos in positions:
                c_sid = <cpp_string>pos.sid
                # Only include positions with available > 0 and not in pending_sells
                if pos.available > 0 and self.pending_sells.find(c_sid) == self.pending_sells.end():
                    days_held = self._get_days_held(<int32_t>ts2intdt(pos.created_dt), current_day)
                    in_topk = c_topk_info.find(c_sid) != c_topk_info.end()

                    p_pair.sid = c_sid
                    p_pair.days_held = days_held
                    p_pair.in_topk = 1 if in_topk else 0
                    p_pair.size = pos.available
                    pos_with_days.push_back(p_pair)

            # Sort: priority to sell = not in topk > longest held
            c_sort(pos_with_days.begin(), pos_with_days.end(), compare_pairs)

            # Add to sell plan (highest priority)
            sold_count = 0
            for i in range(pos_with_days.size()):
                if sold_count >= excess_count:
                    break
                p_pair = pos_with_days[i]

                tmp.sid = p_pair.sid
                tmp.weight = 1.0
                tmp.size = p_pair.size
                tmp.priority = 0  # Highest priority for max_positions enforcement
                tmp.execType = execType
                tmp.filler = filler

                sells.push_back(tmp)
                self.pending_sells[p_pair.sid] = tmp
                sold_count += 1

            # Update active count after max_positions enforcement
            active_positions_count -= sold_count

        # ===================================================================================
        # PHASE 3: Get sizing for sell orders
        # ===================================================================================
        if self.sizer is not None:
            s_wgt = self.sizer.getsizing(topk_info, snapshot, False)

        # ===================================================================================
        # PHASE 4: Process sell signals with auto-reissue for pending sells
        # ===================================================================================
        # KEY: Re-submit sell orders for stocks that failed to sell yesterday (limit-down stuck)
        # This ensures "dead orders" get re-issued to the broker every day until sold
        for pos in positions:
            should_sell = False
            c_sid = <cpp_string>pos.sid

            # Skip if no available shares to sell
            if pos.available == 0:
                continue

            # ===============================================================================
            # CRITICAL: Check for "stale pending sells" from previous failed attempts
            # ===============================================================================
            # If this stock is already in pending_sells, it means we tried to sell it before
            # but the order failed (e.g., limit-down with zero volume).
            # We MUST re-issue the sell order today to keep trying to exit the position.
            if self.pending_sells.find(c_sid) != self.pending_sells.end():
                # print(f"[generate_plan PHASE4] Day {current_day}: RE-ISSUING sell order for stuck position {c_sid.decode('utf-8', errors='ignore')} (available={pos.available})")

                tmp.sid = c_sid
                tmp.weight = 1.0
                tmp.size = pos.available  # Sell all available shares
                tmp.priority = 0          # HIGHEST priority - must sell stuck positions first
                tmp.execType = execType
                tmp.filler = filler

                sells.push_back(tmp)
                # Update pending_sells record with current available quantity
                self.pending_sells[c_sid] = tmp
                continue  # Move to next position

            # ===============================================================================
            # Normal sell logic: Check if we should initiate a new sell
            # ===============================================================================
            # HoldingDays check
            days_held = self._get_days_held(ts2intdt(pos.created_dt), current_day)

            if days_held >= self.interval:
                should_sell = True

            # Not in topk check (only if topk is not empty)
            if not c_topk_info.empty() and c_topk_info.find(c_sid) == c_topk_info.end():
                should_sell = True

            if not should_sell:
                continue

            it_wgt = s_wgt.find(c_sid)
            wgt_ratio = deref(it_wgt).second if it_wgt != s_wgt.end() else 1.0

            tmp.sid = c_sid
            tmp.weight = wgt_ratio
            tmp.size = pos.available
            tmp.priority = 1  # Lower priority than max_positions enforcement, higher than new buys
            tmp.execType = execType
            tmp.filler = filler

            sells.push_back(tmp)
            self.pending_sells[c_sid] = tmp

        c_sort(sells.begin(), sells.end(), compare_trader_plans)

        # ===================================================================================
        # PHASE 5: Recalculate active positions AFTER all sells
        # ===================================================================================
        # Count held positions (size > 0, incl. T+1 locked) NOT in pending_sells
        active_positions_count = 0
        for pos in positions:
            c_sid = <cpp_string>pos.sid
            if pos.size > 0 and self.pending_sells.find(c_sid) == self.pending_sells.end():
                active_positions_count += 1

        # Also count pending_sells (they still occupy slots until executed)
        cdef int32_t pending_sells_count = <int32_t>self.pending_sells.size()
        cdef int32_t total_held_count = active_positions_count + pending_sells_count

        # ===================================================================================
        # PHASE 6: Calculate available slots for buying
        # ===================================================================================
        # Slots based on max_positions constraint (consider both active and pending sells)
        slots_by_max_pos = self.max_positions - total_held_count

        # Also consider topk size (if topk is not empty)
        if not c_topk_info.empty():
            max_slots_from_topk = <int32_t>c_topk_info.size()
        else:
            max_slots_from_topk = 0

        # Use the smaller constraint
        if not c_topk_info.empty():
            slots = min(slots_by_max_pos, max_slots_from_topk)
        else:
            slots = slots_by_max_pos

        # Debug output for PHASE 6
        # print(f"[generate_plan PHASE6] Day {current_day}: slots={slots}, slots_by_max_pos={slots_by_max_pos}, active={active_positions_count}, pending={pending_sells_count}, total={total_held_count}, topk_size={max_slots_from_topk if not c_topk_info.empty() else 0}")  # 泄露策略参数

        # ===================================================================================
        # PHASE 7: Cash Control
        # ===================================================================================
        if account.cash <= 10000:
            # still mark the day as traded: skipping this would make the
            # same-day guard at the top re-run the whole plan
            self._last_trade_day = current_day
            plan[<cpp_string>b"sell"] = sells
            plan[<cpp_string>b"buy"] = buys
            return plan

        # ===================================================================================
        # PHASE 8: Buy Control (strictly enforce slot limit)
        # ===================================================================================
        if self.sizer is not None:
            b_wgt = self.sizer.getsizing(c_topk_info, snapshot, True)

        for py_sid in topk_info:
            c_sid = <cpp_string>py_sid
            buy_rank += 1

            if self.pending_sells.find(c_sid) != self.pending_sells.end():
                continue

            it_wgt = b_wgt.find(c_sid)
            wgt_ratio = deref(it_wgt).second if it_wgt != b_wgt.end() else 0.0

            # weight first: a candidate with 0 weight must not burn a slot
            if wgt_ratio <= 0:
                continue

            # Check if already held (consider size > 0, not just available, to avoid
            # duplicate buying of T+1 locked positions)
            already_held = False
            for pos in positions:
                if <cpp_string>pos.sid == c_sid and pos.size > 0:
                    already_held = True
                    break

            # Only consume slots for NEW positions (not adding to existing)
            if not already_held:
                if slots <= 0:
                    continue  # No more slots for new positions
                slots -= 1  # Consume one slot for new position

            tmp.sid = c_sid
            tmp.weight = wgt_ratio
            tmp.size = 0
            tmp.priority = buy_rank
            tmp.execType = execType
            tmp.filler = filler

            buys.push_back(tmp)

        c_sort(buys.begin(), buys.end(), compare_trader_plans)

        self._last_trade_day = current_day

        plan[<cpp_string>b"sell"] = sells
        plan[<cpp_string>b"buy"] = buys
        return plan

    cpdef void on_execute(self, dict mtrades):
        cdef bytes py_sid
        cdef cpp_string c_sid
        cdef int32_t remain, executed_size
        cdef object trades
        cdef unordered_map[cpp_string, TraderPlan].iterator it_sell

        for py_sid, trades in mtrades.items():
            c_sid = <cpp_string>py_sid
            executed_size = sum([trade.executed_size for trade in trades])

            it_sell = self.pending_sells.find(c_sid)
            if it_sell != self.pending_sells.end():
                remain = deref(it_sell).second.size - executed_size
                if remain <= 0:
                    self.pending_sells.erase(it_sell) # zero_copy
                else:
                    self.pending_sells[c_sid].size = remain
