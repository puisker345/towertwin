"""Deterministic order-flow market simulator (Section 5.3)."""

from __future__ import annotations

import math
import time
import traceback
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from numpy.random import Generator, PCG64

from .. import config
from .ledger import Ledger
from .scenarios import BASELINE_CRASH, Scenario, fit_to_length
from .types import Fill, MarketState, Quote


@dataclass(frozen=True)
class FillTelemetry:
    tick: int
    fill_index: int      # 0-based index within the tick
    global_fill_id: int  # 0-based index across the entire run
    side: str
    price: int
    size: int
    fee: int
    position_after: int  # position immediately after this fill


@dataclass(frozen=True)
class TickTelemetry:
    tick: int
    mid: int
    quote: Quote | None
    position: int            # end-of-tick position
    max_abs_position: int    # max |position| at any point during this tick (start + after every fill)
    pnl: int                 # mark-to-market PnL in cents at end of tick
    fills_count: int
    halted: bool             # True if quote is None or both sizes == 0


@dataclass
class BacktestResult:
    seed: int
    ticks: int
    scenario: Scenario
    limits: dict[str, Any]
    telemetry: list[TickTelemetry] = field(default_factory=list)
    fill_telemetry: list[FillTelemetry] = field(default_factory=list)
    fills: list[Fill] = field(default_factory=list)
    quotes: list[tuple[int, Quote | None]] = field(default_factory=list)
    ledger_summary: dict[str, int] = field(default_factory=dict)
    strategy_report: dict[str, Any] = field(default_factory=dict)
    violations: list[dict[str, Any]] = field(default_factory=list)
    runtime_sec: float = 0.0
    exception: str | None = None
    timed_out: bool = False

    def to_serializable(self, include_full_telemetry: bool = True) -> dict[str, Any]:
        """Convert BacktestResult into a JSON-serializable dict for subprocess IPC."""
        data: dict[str, Any] = {
            "seed": self.seed,
            "ticks": self.ticks,
            "scenario": self.scenario.to_dict(),
            "limits": dict(self.limits),
            "ledger_summary": dict(self.ledger_summary),
            "strategy_report": dict(self.strategy_report) if isinstance(self.strategy_report, dict) else {},
            "violations": list(self.violations),
            "runtime_sec": float(self.runtime_sec),
            "exception": self.exception,
            "timed_out": bool(self.timed_out),
            "fill_telemetry": [
                {
                    "tick": ft.tick,
                    "fill_index": ft.fill_index,
                    "global_fill_id": ft.global_fill_id,
                    "side": ft.side,
                    "price": ft.price,
                    "size": ft.size,
                    "fee": ft.fee,
                    "position_after": ft.position_after,
                }
                for ft in self.fill_telemetry
            ],
        }
        if include_full_telemetry:
            data["telemetry"] = [
                {
                    "tick": tt.tick,
                    "mid": tt.mid,
                    "quote": (
                        {
                            "bid_price": tt.quote.bid_price,
                            "bid_size": tt.quote.bid_size,
                            "ask_price": tt.quote.ask_price,
                            "ask_size": tt.quote.ask_size,
                        }
                        if tt.quote is not None
                        else None
                    ),
                    "position": tt.position,
                    "max_abs_position": tt.max_abs_position,
                    "pnl": tt.pnl,
                    "fills_count": tt.fills_count,
                    "halted": tt.halted,
                }
                for tt in self.telemetry
            ]
        return data

    @classmethod
    def from_serializable(cls, data: dict[str, Any]) -> "BacktestResult":
        """Reconstruct a BacktestResult from a JSON-deserialized dict."""
        scenario = Scenario.from_dict(data["scenario"])
        telemetry: list[TickTelemetry] = []
        quotes: list[tuple[int, Quote | None]] = []
        for tt in data.get("telemetry", []):
            q_raw = tt.get("quote")
            q = (
                Quote(
                    bid_price=q_raw["bid_price"],
                    bid_size=q_raw["bid_size"],
                    ask_price=q_raw["ask_price"],
                    ask_size=q_raw["ask_size"],
                )
                if q_raw is not None
                else None
            )
            telemetry.append(
                TickTelemetry(
                    tick=int(tt["tick"]),
                    mid=int(tt["mid"]),
                    quote=q,
                    position=int(tt["position"]),
                    max_abs_position=int(tt["max_abs_position"]),
                    pnl=int(tt["pnl"]),
                    fills_count=int(tt["fills_count"]),
                    halted=bool(tt["halted"]),
                )
            )
            quotes.append((int(tt["tick"]), q))

        fill_telemetry: list[FillTelemetry] = []
        fills: list[Fill] = []
        for ft in data.get("fill_telemetry", []):
            fill_telemetry.append(
                FillTelemetry(
                    tick=int(ft["tick"]),
                    fill_index=int(ft["fill_index"]),
                    global_fill_id=int(ft["global_fill_id"]),
                    side=str(ft["side"]),
                    price=int(ft["price"]),
                    size=int(ft["size"]),
                    fee=int(ft["fee"]),
                    position_after=int(ft["position_after"]),
                )
            )
            fills.append(
                Fill(
                    tick=int(ft["tick"]),
                    side=str(ft["side"]),
                    price=int(ft["price"]),
                    size=int(ft["size"]),
                    fee=int(ft["fee"]),
                )
            )

        return cls(
            seed=int(data["seed"]),
            ticks=int(data["ticks"]),
            scenario=scenario,
            limits=dict(data.get("limits", {})),
            telemetry=telemetry,
            fill_telemetry=fill_telemetry,
            fills=fills,
            quotes=quotes,
            ledger_summary=dict(data.get("ledger_summary", {})),
            strategy_report=dict(data.get("strategy_report", {})),
            violations=list(data.get("violations", [])),
            runtime_sec=float(data.get("runtime_sec", 0.0)),
            exception=data.get("exception"),
            timed_out=bool(data.get("timed_out", False)),
        )


def _validate_quote(tick: int, raw_quote: Any) -> tuple[Quote | None, dict[str, Any] | None]:
    """Validate strategy quote against hard engine rules (Section 5.3 step 3)."""
    if raw_quote is None:
        return None, None

    if not hasattr(raw_quote, "bid_price") or not hasattr(raw_quote, "ask_price"):
        return None, {
            "tick": tick,
            "reason": f"Returned invalid quote object of type {type(raw_quote).__name__}",
        }

    bid_price = raw_quote.bid_price
    bid_size = raw_quote.bid_size
    ask_price = raw_quote.ask_price
    ask_size = raw_quote.ask_size

    if not isinstance(bid_size, int) or isinstance(bid_size, bool) or bid_size < 0:
        return None, {"tick": tick, "reason": f"Invalid bid_size={bid_size!r} (must be int >= 0)"}
    if not isinstance(ask_size, int) or isinstance(ask_size, bool) or ask_size < 0:
        return None, {"tick": tick, "reason": f"Invalid ask_size={ask_size!r} (must be int >= 0)"}

    if bid_size == 0:
        bid_price = None
    if ask_size == 0:
        ask_price = None

    if bid_price is not None and (not isinstance(bid_price, int) or isinstance(bid_price, bool) or bid_price <= 0):
        return None, {"tick": tick, "reason": f"Invalid bid_price={bid_price!r} (must be positive int cents)"}
    if ask_price is not None and (not isinstance(ask_price, int) or isinstance(ask_price, bool) or ask_price <= 0):
        return None, {"tick": tick, "reason": f"Invalid ask_price={ask_price!r} (must be positive int cents)"}

    if bid_size > 0 and bid_price is None:
        return None, {"tick": tick, "reason": f"bid_size={bid_size} with bid_price=None"}
    if ask_size > 0 and ask_price is None:
        return None, {"tick": tick, "reason": f"ask_size={ask_size} with ask_price=None"}

    if bid_price is not None and ask_price is not None and bid_price >= ask_price:
        return None, {
            "tick": tick,
            "reason": f"Crossed/locked quote: bid_price ({bid_price}) >= ask_price ({ask_price})",
        }

    return Quote(
        bid_price=bid_price,
        bid_size=bid_size,
        ask_price=ask_price,
        ask_size=ask_size,
    ), None


def run_backtest(
    strategy: Any,
    seed: int = 1,
    scenario: Scenario = BASELINE_CRASH,
    ticks: int = config.TICKS,
    limits: dict[str, Any] | None = None,
    start_mid: int = config.START_MID,
    tick_size: int = config.TICK_SIZE,
    sigma: float = config.SIGMA,
    arrival_rate: float = config.ARRIVAL_RATE,
    order_size_min: int = config.ORDER_SIZE_MIN,
    order_size_max: int = config.ORDER_SIZE_MAX,
    fill_k: float = config.FILL_K,
    fee_per_lot: int = config.FEE_PER_LOT,
) -> BacktestResult:
    """Run a single deterministic backtest of `strategy` over `ticks` ticks (Section 5.3)."""
    t0 = time.perf_counter()
    active_limits = dict(limits) if limits is not None else config.get_desk_limits()
    fitted = fit_to_length(scenario, ticks)
    assert 200 <= fitted.start and fitted.start + fitted.duration + 300 <= ticks, (
        "Crash window must lie inside the run"
    )

    rng: Generator = Generator(PCG64(int(seed)))
    ledger = Ledger()

    mid = int(start_mid)
    recent_mids: list[int] = []
    recent_trades: list[tuple[int, str, int]] = []

    telemetry: list[TickTelemetry] = []
    fill_telemetry: list[FillTelemetry] = []
    all_fills: list[Fill] = []
    all_quotes: list[tuple[int, Quote | None]] = []
    violations: list[dict[str, Any]] = []

    crash_start = fitted.start
    crash_end = fitted.start + fitted.duration
    aftershock_end = crash_end + 35
    vol_end = crash_end + 200
    rec_end = crash_end + 300

    # Determine crash direction: negative drop unless buy flow dominates significantly (spike-up squeeze)
    direction = 1.0 if fitted.buy_flow_mult > fitted.sell_flow_mult * 1.5 else -1.0
    total_crash_cents = float(start_mid) * (float(fitted.depth_pct) / 100.0) * direction
    crash_drift_per_tick = total_crash_cents / float(max(1, fitted.duration))

    total_recovery_cents = -total_crash_cents * (float(fitted.recovery_pct) / 100.0)
    recovery_drift_per_tick = total_recovery_cents / 300.0

    drift_accum = 0.0
    global_fill_id = 0
    exc_str: str | None = None

    try:
        for t in range(ticks):
            # 1. Update mid: deterministic drift + Gaussian noise
            if crash_start <= t < crash_end:
                drift_accum += crash_drift_per_tick
            elif crash_end <= t < rec_end:
                drift_accum += recovery_drift_per_tick

            drift_step = int(round(drift_accum))
            drift_accum -= drift_step

            if crash_start <= t <= crash_end:
                vol_mult = fitted.sigma_mult
            elif crash_end < t < vol_end:
                vol_mult = 1.0 + (fitted.sigma_mult - 1.0) * 0.25
            else:
                vol_mult = 1.0
            noise = int(round(float(rng.normal(0.0, sigma * vol_mult))))
            step = drift_step + noise
            if crash_start <= t <= crash_end and abs(step) < 15:
                whip_sign = int(direction) if ((t - crash_start) % 2 == 0) else -int(direction)
                step = whip_sign * (15 + (abs(noise) % 4))
            mid = max(100, mid + step)

            recent_mids.append(mid)
            if len(recent_mids) > 50:
                del recent_mids[0]

            # 2. Build MarketState (past data only) and call strategy.on_tick(state)
            state = MarketState(
                tick=t,
                mid=mid,
                recent_mids=tuple(recent_mids),
                position=ledger.position,
                recent_trades=tuple(recent_trades[-20:]),
                limits=dict(active_limits),
            )
            raw_quote = strategy.on_tick(state)

            # 3. Validate quote against hard engine rules
            valid_quote, violation = _validate_quote(t, raw_quote)
            if violation is not None:
                violations.append(violation)
            all_quotes.append((t, valid_quote))

            halted = valid_quote is None or (valid_quote.bid_size == 0 and valid_quote.ask_size == 0)

            # 4. Draw market buy and sell orders (including immediate post-crash trough liquidations)
            in_stress_flow = crash_start <= t < aftershock_end
            if in_stress_flow:
                buy_rate = arrival_rate * fitted.buy_flow_mult
                sell_rate = arrival_rate * fitted.sell_flow_mult
            else:
                buy_rate = arrival_rate
                sell_rate = arrival_rate

            n_buys = int(rng.poisson(buy_rate))
            n_sells = int(rng.poisson(sell_rate))
            total_orders = n_buys + n_sells

            max_abs_pos = abs(ledger.position)
            tick_fills_count = 0

            if total_orders > 0:
                sizes = rng.integers(order_size_min, order_size_max + 1, size=total_orders)
                # 0..n_buys-1 are market buys; n_buys..total_orders-1 are market sells
                order_indices = rng.permutation(total_orders)

                rem_bid = valid_quote.bid_size if (valid_quote and valid_quote.bid_price is not None) else 0
                rem_ask = valid_quote.ask_size if (valid_quote and valid_quote.ask_price is not None) else 0
                eff_fill_k = fill_k * 0.45 if in_stress_flow else fill_k

                for idx in order_indices:
                    idx_int = int(idx)
                    order_sz = int(sizes[idx_int])
                    aggressor_side = "buy" if idx_int < n_buys else "sell"

                    # Record market trade in tape
                    trade_price = mid + (1 if aggressor_side == "buy" else -1)
                    recent_trades.append((trade_price, aggressor_side, order_sz))
                    if len(recent_trades) > 20:
                        del recent_trades[0]

                    if aggressor_side == "sell":
                        # Market sell can fill against our bid
                        if valid_quote is not None and valid_quote.bid_price is not None and rem_bid > 0:
                            dist = abs(valid_quote.bid_price - mid) / float(tick_size)
                            prob = min(1.0, math.exp(-eff_fill_k * dist))
                            if float(rng.random()) < prob:
                                fill_sz = min(order_sz, rem_bid)
                                rem_bid -= fill_sz
                                fee = fill_sz * fee_per_lot
                                fill = Fill(
                                    tick=t,
                                    side="buy",
                                    price=int(valid_quote.bid_price),
                                    size=int(fill_sz),
                                    fee=int(fee),
                                )
                                ledger.apply_fill(fill)
                                pos_after = ledger.position
                                if abs(pos_after) > max_abs_pos:
                                    max_abs_pos = abs(pos_after)
                                fill_telemetry.append(
                                    FillTelemetry(
                                        tick=t,
                                        fill_index=tick_fills_count,
                                        global_fill_id=global_fill_id,
                                        side=fill.side,
                                        price=fill.price,
                                        size=fill.size,
                                        fee=fill.fee,
                                        position_after=pos_after,
                                    )
                                )
                                all_fills.append(fill)
                                tick_fills_count += 1
                                global_fill_id += 1
                                strategy.on_fill(fill)
                    else:
                        # Market buy can fill against our ask
                        if valid_quote is not None and valid_quote.ask_price is not None and rem_ask > 0:
                            dist = abs(valid_quote.ask_price - mid) / float(tick_size)
                            prob = min(1.0, math.exp(-eff_fill_k * dist))
                            if float(rng.random()) < prob:
                                fill_sz = min(order_sz, rem_ask)
                                rem_ask -= fill_sz
                                fee = fill_sz * fee_per_lot
                                fill = Fill(
                                    tick=t,
                                    side="sell",
                                    price=int(valid_quote.ask_price),
                                    size=int(fill_sz),
                                    fee=int(fee),
                                )
                                ledger.apply_fill(fill)
                                pos_after = ledger.position
                                if abs(pos_after) > max_abs_pos:
                                    max_abs_pos = abs(pos_after)
                                fill_telemetry.append(
                                    FillTelemetry(
                                        tick=t,
                                        fill_index=tick_fills_count,
                                        global_fill_id=global_fill_id,
                                        side=fill.side,
                                        price=fill.price,
                                        size=fill.size,
                                        fee=fill.fee,
                                        position_after=pos_after,
                                    )
                                )
                                all_fills.append(fill)
                                tick_fills_count += 1
                                global_fill_id += 1
                                strategy.on_fill(fill)

            # 6. Record per-tick telemetry
            telemetry.append(
                TickTelemetry(
                    tick=t,
                    mid=mid,
                    quote=valid_quote,
                    position=ledger.position,
                    max_abs_position=max_abs_pos,
                    pnl=ledger.pnl(mid),
                    fills_count=tick_fills_count,
                    halted=halted,
                )
            )

        strat_report = strategy.report()
        if not isinstance(strat_report, dict):
            exc_str = f"strategy.report() returned {type(strat_report).__name__}, expected dict"
            strat_report = {}
    except Exception as exc:  # noqa: BLE001
        tb_lines = traceback.format_exception_only(type(exc), exc)
        tb_full = traceback.format_exc().strip().splitlines()
        loc = tb_full[-2].strip() if len(tb_full) >= 2 else ""
        exc_str = f"{tb_lines[-1].strip()} ({loc})" if loc else tb_lines[-1].strip()
        strat_report = {}

    runtime = time.perf_counter() - t0
    final_mid = telemetry[-1].mid if telemetry else start_mid

    return BacktestResult(
        seed=seed,
        ticks=ticks,
        scenario=fitted,
        limits=active_limits,
        telemetry=telemetry,
        fill_telemetry=fill_telemetry,
        fills=all_fills,
        quotes=all_quotes,
        ledger_summary=ledger.summary(final_mid),
        strategy_report=strat_report,
        violations=violations,
        runtime_sec=runtime,
        exception=exc_str,
        timed_out=False,
    )
