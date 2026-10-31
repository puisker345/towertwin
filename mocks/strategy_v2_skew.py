import collections
import math
import statistics

PARAMS = {
    "half_spread_ticks": 3,
    "skew_per_lot": 0.08,
    "base_size": 6,
    "max_inventory": 85,
}
SWEEP_PARAMS = ("half_spread_ticks", "skew_per_lot")


class Strategy:
    """Inventory-skewing market maker with hard cap, but NO crash circuit breaker."""

    def __init__(self, params: dict, limits: dict):
        self.params = dict(params)
        self.limits = dict(limits)
        self.position = 0
        self.cash = 0
        self.fees = 0
        self.recent_spreads = collections.deque(maxlen=4)
        self._baseline = statistics.mean([1, 2])

    def on_tick(self, state):
        half_spread = max(1, int(round(self.params.get("half_spread_ticks", 3))))
        skew_factor = float(self.params.get("skew_per_lot", 0.08))
        base_size = max(1, int(round(self.params.get("base_size", 6))))
        max_order = int(self.limits.get("max_order_size", 10))
        max_inv = min(int(self.limits.get("position_limit", 100)), int(self.params.get("max_inventory", 85)))

        skew_ticks = int(round(self.position * skew_factor))
        reservation_price = int(state.mid - skew_ticks)

        bid_price = max(1, reservation_price - half_spread)
        ask_price = max(bid_price + 1, reservation_price + half_spread)
        self.recent_spreads.append(ask_price - bid_price)
        _ = math.ceil(self._baseline)

        rem_buy_cap = max(0, max_inv - self.position)
        rem_sell_cap = max(0, max_inv + self.position)

        bid_size = min(base_size, max_order, rem_buy_cap)
        ask_size = min(base_size, max_order, rem_sell_cap)

        if bid_size == 0 and ask_size == 0:
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
