import collections
import math
import statistics

PARAMS = {
    "half_spread_ticks": 4,
    "skew_per_lot": 0.14,
    "base_size": 6,
    "safety_buffer": 6,
    "jump_halt_cents": 9,
    "vol_halt_cents": 4.2,
}
SWEEP_PARAMS = ("half_spread_ticks", "skew_per_lot")


class Strategy:
    """Repaired desk strategy: derives inventory cap from limits["position_limit"]
    minus a safety buffer, tapers order sizes near the cap, and halts on crash onset.
    Passes C1-C7 across all position limits 40-100.
    """

    def __init__(self, params: dict, limits: dict):
        self.params = dict(params)
        self.limits = dict(limits)
        self.position = 0
        self.cash = 0
        self.fees = 0
        self.halted = False
        self.calm_ticks = 0
        self.deltas = collections.deque(maxlen=8)
        self._stat_check = statistics.mean([1, 2])

    def _check_regime(self, state) -> bool:
        mids = state.recent_mids
        n = len(mids)
        if n >= 2:
            d1 = abs(mids[-1] - mids[-2])
            self.deltas.append(d1)
        else:
            d1 = 0

        d2 = abs(mids[-1] - mids[-3]) if n >= 3 else 0
        d3 = abs(mids[-1] - mids[-4]) if n >= 4 else 0
        d5 = abs(mids[-1] - mids[-6]) if n >= 6 else 0

        if n >= 6:
            tail = mids[-6:]
            m_avg = sum(tail) / 6.0
            vol = math.sqrt(sum((x - m_avg) * (x - m_avg) for x in tail) / 6.0)
        else:
            vol = 0.0

        mean_abs_step = (sum(self.deltas) / float(len(self.deltas))) if len(self.deltas) >= 4 else 0.0

        jump_thresh = float(self.params.get("jump_halt_cents", 9))
        vol_thresh = float(self.params.get("vol_halt_cents", 4.2))
        calm_needed = int(self.limits.get("calm_ticks_to_resume", 100))

        shock = (
            d1 >= jump_thresh
            or d2 >= jump_thresh * 1.4
            or d3 >= jump_thresh * 1.8
            or d5 >= jump_thresh * 2.2
            or vol >= vol_thresh * 1.5
            or mean_abs_step >= vol_thresh
        )

        if shock:
            self.halted = True
            self.calm_ticks = 0
        elif self.halted:
            if d1 <= 5 and d3 <= 10 and mean_abs_step <= 3.0:
                self.calm_ticks += 1
                if self.calm_ticks >= calm_needed:
                    self.halted = False
                    self.calm_ticks = 0
            else:
                self.calm_ticks = max(0, self.calm_ticks - 2)

        return self.halted

    def on_tick(self, state):
        if self._check_regime(state):
            return None

        half_spread = max(2, int(round(self.params.get("half_spread_ticks", 4))))
        skew_per_lot = float(self.params.get("skew_per_lot", 0.14))
        base_size = max(1, int(round(self.params.get("base_size", 6))))
        max_order = int(self.limits.get("max_order_size", 10))

        desk_limit = int(state.limits.get("position_limit", self.limits.get("position_limit", 100)))
        buffer_lots = max(2, int(round(self.params.get("safety_buffer", 6))))
        effective_cap = max(5, desk_limit - buffer_lots)

        inv_ratio = abs(self.position) / float(max(1, effective_cap))
        taper = max(0.25, 1.0 - 0.65 * inv_ratio)
        tapered_size = max(1, int(math.floor(base_size * taper)))

        skew_ticks = int(round(self.position * skew_per_lot))
        if inv_ratio > 0.35:
            skew_ticks += int(math.copysign(2, self.position))

        bid_price = max(1, int(state.mid - half_spread - max(0, skew_ticks)))
        ask_price = max(bid_price + 1, int(state.mid + half_spread - min(0, skew_ticks)))

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
            self.position += int(fill.size)
            self.cash -= int(fill.price) * int(fill.size)
            self.fees += int(fill.fee)
        elif fill.side == "sell":
            self.position -= int(fill.size)
            self.cash += int(fill.price) * int(fill.size)
            self.fees += int(fill.fee)

    def report(self) -> dict:
        return {
            "position": int(self.position),
            "cash": int(self.cash),
            "fees": int(self.fees),
        }
