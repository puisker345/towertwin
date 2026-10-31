"""Unit tests for determinism, no look-ahead, and scenario fit_to_length (Section 11.1)."""

from __future__ import annotations

import itertools

from towertwin import config
from towertwin.mocks import load_redteam_scenarios_raw, strategy_v3_desk
from towertwin.sim.market import run_backtest
from towertwin.sim.scenarios import (
    BASELINE_CRASH,
    BOUNDS,
    Scenario,
    fit_to_length,
    parse_and_clamp_scenario,
)
from towertwin.sim.types import MarketState, Quote


def test_determinism_same_and_different_seeds() -> None:
    """Section 11.1: same seed, scenario and strategy produce identical telemetry and fills;
    a different seed produces different telemetry.
    """
    limits = config.get_desk_limits(100)
    s1_a = strategy_v3_desk.Strategy(strategy_v3_desk.PARAMS, limits)
    s1_b = strategy_v3_desk.Strategy(strategy_v3_desk.PARAMS, limits)
    s2 = strategy_v3_desk.Strategy(strategy_v3_desk.PARAMS, limits)

    res1_a = run_backtest(s1_a, seed=42, scenario=BASELINE_CRASH, ticks=600, limits=limits)
    res1_b = run_backtest(s1_b, seed=42, scenario=BASELINE_CRASH, ticks=600, limits=limits)
    res2 = run_backtest(s2, seed=43, scenario=BASELINE_CRASH, ticks=600, limits=limits)

    assert res1_a.telemetry == res1_b.telemetry
    assert res1_a.fills == res1_b.fills
    assert res1_a.fill_telemetry == res1_b.fill_telemetry
    assert res1_a.ledger_summary == res1_b.ledger_summary

    mids_1 = [t.mid for t in res1_a.telemetry]
    mids_2 = [t.mid for t in res2.telemetry]
    assert mids_1 != mids_2


def test_no_look_ahead_property() -> None:
    """Section 11.1: MarketState at tick t contains no data from t+1 onwards."""
    recorded_states: list[MarketState] = []

    class RecordingStrategy:
        def __init__(self) -> None:
            self.position = 0
            self.cash = 0
            self.fees = 0

        def on_tick(self, state: MarketState) -> Quote | None:
            recorded_states.append(state)
            return Quote(bid_price=state.mid - 2, bid_size=2, ask_price=state.mid + 2, ask_size=2)

        def on_fill(self, fill) -> None:
            if fill.side == "buy":
                self.position += fill.size
                self.cash -= fill.price * fill.size
                self.fees += fill.fee
            else:
                self.position -= fill.size
                self.cash += fill.price * fill.size
                self.fees += fill.fee

        def report(self) -> dict[str, int]:
            return {"position": self.position, "cash": self.cash, "fees": self.fees}

    strat = RecordingStrategy()
    res = run_backtest(strat, seed=7, scenario=BASELINE_CRASH, ticks=600)
    all_mids = [tt.mid for tt in res.telemetry]

    assert len(recorded_states) == 600
    for t, st in enumerate(recorded_states):
        assert st.tick == t
        assert st.mid == all_mids[t]
        expected_window = tuple(all_mids[max(0, t - 49) : t + 1])
        assert st.recent_mids == expected_window
        assert len(st.recent_mids) <= min(50, t + 1)


def test_crash_inside_every_run_bounds_and_mocks() -> None:
    """Section 11.1: for every red-team bound extreme and every mock scenario,
    fit_to_length(scenario, sweep_ticks) and fit_to_length(scenario, ticks) place
    the full crash window inside the run; a scenario with start=2800 at sweep_ticks=1500
    is rescaled, not silently skipped.
    """
    # Test explicit start=2800 at sweep_ticks=1500
    late_scen = Scenario(
        name="Late Crash",
        description="Crash near end of 3000-tick session.",
        start=2800,
        depth_pct=8.0,
        duration=30,
        sigma_mult=5.0,
        sell_flow_mult=4.0,
        buy_flow_mult=0.3,
        recovery_pct=40.0,
    )
    fitted_sweep = fit_to_length(late_scen, config.SWEEP_TICKS)
    assert fitted_sweep.start < 2800
    assert 200 <= fitted_sweep.start
    assert fitted_sweep.start + fitted_sweep.duration + 300 <= config.SWEEP_TICKS

    # Test all corners of BOUNDS
    for start_v, depth_v, dur_v, sig_v, sell_v, buy_v, rec_v in itertools.product(
        BOUNDS["start"],
        BOUNDS["depth_pct"],
        BOUNDS["duration"],
        BOUNDS["sigma_mult"],
        BOUNDS["sell_flow_mult"],
        BOUNDS["buy_flow_mult"],
        BOUNDS["recovery_pct"],
    ):
        scen = Scenario(
            name="Extreme",
            description="Bound extreme test.",
            start=int(start_v),
            depth_pct=float(depth_v),
            duration=int(dur_v),
            sigma_mult=float(sig_v),
            sell_flow_mult=float(sell_v),
            buy_flow_mult=float(buy_v),
            recovery_pct=float(rec_v),
        )
        for length in (config.SWEEP_TICKS, config.TICKS):
            f = fit_to_length(scen, length)
            assert 200 <= f.start
            assert f.start + f.duration + 300 <= length

    # Test all mock scenarios
    for raw in load_redteam_scenarios_raw():
        scen = parse_and_clamp_scenario(raw)
        for length in (config.SWEEP_TICKS, config.TICKS):
            f = fit_to_length(scen, length)
            assert 200 <= f.start
            assert f.start + f.duration + 300 <= length
