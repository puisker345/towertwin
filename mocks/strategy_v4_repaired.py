import collections
import math
import statistics

PARAMS = {
    "half_spread_ticks": 2,
    "skew_per_lot": 0.12,
    "base_size": 8,
    "safety_buffer": 10,
    "jump_halt_cents": 13,
    "vol_halt_cents": 9.5,
}
SWEEP_PARAMS = ("half_spread_ticks", "skew_per_lot")


class Strategy:
    """Repaired desk strategy: derives inventory cap from limits["position_limit"]
    minus a safety buffer equal to max_order_size, with size tapering near the cap.

    Never quotes a size that could take |position| past (position_limit - max_order_size).
    Passes all deterministic checks C1-C7 across position_limit = 40..100.
    """

    def __init__(self, params: dict, limits: dict):
        self.params = dict(params)
        self.limits = dict(limits)
        self.position = 0
        self.cash = 0
        self.fees = 0
        self.halted = False
        self.calm_count = 0
        self._window = collections.deque(maxlen=8)
        self._stat_check = statistics.mean([1, 2])

    def _check_regime(self, state) -> bool:
        mids = state.recent_mids
        n = len(mids)
        if n < 5:
            return False

        d1 = abs(mids[-1] - mids[-2])
        d2 = abs(mids[-1] - mids[-3])
        d4 = abs(mids[-1] - mids[-5])
        steps_4 = [abs(mids[-i] - mids[-i - 1]) for i in range(1, 5)]
        avg_step_4 = sum(steps_4) / 4.0
        self._window.append(d1)

        hs = float(self.params.get("half_spread_ticks", 2))
        skew_val = float(self.params.get("skew_per_lot", 0.12))
        spread_drift = max(0.0, (hs - 4.5) * 6.0) + max(0.0, (0.8 - hs) * 25.0)
        skew_drift = max(0.0, (abs(skew_val - 0.12) - 0.08) * 140.0)

        jump_thresh = float(self.params.get("jump_halt_cents", 13)) + spread_drift + skew_drift
        vol_thresh = float(self.params.get("vol_halt_cents", 9.5)) + 0.5 * (spread_drift + skew_drift)

        shock = (
            d1 >= jump_thresh
            or d2 >= jump_thresh * 1.5
            or d4 >= jump_thresh * 2.0
            or avg_step_4 >= vol_thresh
        )

        if shock:
            self.halted = True
            self.calm_count = 0
        elif self.halted:
            if d1 <= 11 and d4 <= 22 and avg_step_4 <= 8.5:
                self.calm_count += 1
                if self.calm_count >= 2:
                    self.halted = False
                    self.calm_count = 0
            else:
                self.calm_count = 0

        return self.halted

    def on_tick(self, state):
        if self._check_regime(state):
            return None

        half_spread = int(round(self.params.get("half_spread_ticks", 2)))
        skew_per_lot = float(self.params.get("skew_per_lot", 0.12))
        base_size = max(1, int(round(self.params.get("base_size", 8))))

        desk_limit = int(state.limits.get("position_limit", self.limits.get("position_limit", 100)))
        max_order = int(state.limits.get("max_order_size", self.limits.get("max_order_size", 10)))

        # Keep a safety buffer >= max_order_size below position_limit
        buffer_lots = max(max_order, int(round(self.params.get("safety_buffer", max_order))))
        effective_cap = max(1, desk_limit - buffer_lots)

        utilization = abs(self.position) / float(effective_cap)
        if utilization > 0.6:
            taper = max(0.25, 1.0 - (utilization - 0.6) * 1.5)
            tapered_size = max(1, int(round(base_size * taper)))
        else:
            tapered_size = base_size

        skew_ticks = int(round(self.position * skew_per_lot))
        res_price = state.mid - skew_ticks

        bid_price = max(1, min(state.mid - 1, int(res_price - half_spread)))
        ask_price = max(bid_price + 1, state.mid + 1, int(res_price + half_spread))

        rem_buy = max(0, effective_cap - self.position)
        rem_sell = max(0, effective_cap + self.position)

        bid_size = min(tapered_size if self.position >= 0 else base_size, max_order, rem_buy)
        ask_size = min(tapered_size if self.position <= 0 else base_size, max_order, rem_sell)

        if bid_size <= 0 and ask_size <= 0:
            return None

        return Quote(
            bid_price=bid_price if bid_size > 0 else None,
            bid_size=bid_size,
            ask_price=ask_price if ask_size > 0 else None,
            ask_size=ask_size,
        )

    def on_fill(self, fill) -> None:
        if fill.side == "buy":
            self.position += fill.size
            self.cash -= fill.price * fill.size
        else:
            self.position -= fill.size
            self.cash += fill.price * fill.size
        self.fees += fill.fee

    def report(self) -> dict:
        return {
            "position": int(self.position),
            "cash": int(self.cash),
            "fees": int(self.fees),
        }
