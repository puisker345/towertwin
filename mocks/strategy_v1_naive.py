import collections
import math
import statistics

PARAMS = {
    "half_spread_ticks": 2,
    "order_size": 8,
}
SWEEP_PARAMS = ("half_spread_ticks", "order_size")


class Strategy:
    """Naive symmetric market maker with no inventory control and no crash halt."""

    def __init__(self, params: dict, limits: dict):
        self.params = dict(params)
        self.limits = dict(limits)
        self.position = 0
        self.cash = 0
        self.fees = 0
        self.mid_window = collections.deque(maxlen=4)
        self._baseline = statistics.mean([1, 2])

    def on_tick(self, state):
        self.mid_window.append(state.mid)
        half_spread = max(1, int(round(self.params.get("half_spread_ticks", 2))))
        size = max(1, min(int(self.limits.get("max_order_size", 10)), int(round(self.params.get("order_size", 8)))))
        _ = math.floor(state.mid + self._baseline)
        bid_price = int(state.mid - half_spread)
        ask_price = int(state.mid + half_spread)
        return Quote(bid_price=bid_price, bid_size=size, ask_price=ask_price, ask_size=size)

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
