"""Unit tests for C1-C7 deterministic checks, intra-tick breach, and artifact hash (Section 11.1)."""

from __future__ import annotations

from towertwin.checks.checks import compute_artifact_hash, evaluate_checks
from towertwin.sim.market import BacktestResult, FillTelemetry, TickTelemetry
from towertwin.sim.scenarios import BASELINE_CRASH
from towertwin.sim.types import Fill, Quote


def _make_clean_result(seed: int = 1, pnl: int = 500) -> BacktestResult:
    """Build a synthetic BacktestResult that passes all checks C1-C7."""
    limits = {
        "position_limit": 100,
        "max_order_size": 10,
        "max_drawdown": 25_000,
        "halt_within_ticks": 5,
        "calm_ticks_to_resume": 100,
    }
    telemetry: list[TickTelemetry] = []
    for t in range(3000):
        # Halt during crash window [2000, 2035]
        in_crash = 2000 <= t <= 2035
        q = None if in_crash else Quote(bid_price=9998, bid_size=5, ask_price=10002, ask_size=5)
        telemetry.append(
            TickTelemetry(
                tick=t,
                mid=10_000,
                quote=q,
                position=10,
                max_abs_position=10,
                pnl=pnl,
                fills_count=0,
                halted=in_crash,
            )
        )
    fill_telemetry = [
        FillTelemetry(
            tick=100,
            fill_index=0,
            global_fill_id=0,
            side="buy",
            price=9998,
            size=10,
            fee=10,
            position_after=10,
        )
    ]
    fills = [Fill(tick=100, side="buy", price=9998, size=10, fee=10)]
    ledger_summary = {"position": 10, "cash": -99_980 + pnl + 10 - 100_000, "fees": 10, "pnl": pnl}
    strategy_report = {
        "position": ledger_summary["position"],
        "cash": ledger_summary["cash"],
        "fees": ledger_summary["fees"],
    }
    return BacktestResult(
        seed=seed,
        ticks=3000,
        scenario=BASELINE_CRASH,
        limits=limits,
        telemetry=telemetry,
        fill_telemetry=fill_telemetry,
        fills=fills,
        quotes=[(tt.tick, tt.quote) for tt in telemetry],
        ledger_summary=ledger_summary,
        strategy_report=strategy_report,
        violations=[],
        runtime_sec=0.1,
        exception=None,
        timed_out=False,
    )


def test_all_checks_pass_on_clean_fixture() -> None:
    in_sample = [_make_clean_result(seed=1)]
    val = [_make_clean_result(seed=101, pnl=300)]
    report = evaluate_checks(in_sample, val, code="class Strategy: pass", params={"a": 1})
    assert report.all_passed
    by_id = {c.id: c for c in report.checks}
    for cid in ("C1", "C2", "C3", "C4", "C5", "C6", "C7"):
        assert by_id[cid].passed, f"{cid} unexpectedly failed: {by_id[cid].detail}"


def test_c1_books_reconcile_fail_fixture() -> None:
    res = _make_clean_result(seed=1)
    res.strategy_report = {"position": 10, "cash": res.ledger_summary["cash"] + 5, "fees": 10}
    report = evaluate_checks([res], [_make_clean_result(seed=101)])
    c1 = next(c for c in report.checks if c.id == "C1")
    assert not c1.passed
    assert (
        c1.detail
        == f"Books mismatch on seed 1 at final tick: strategy reported "
        f"(position=10, cash={res.strategy_report['cash']}, fees=10) vs ledger "
        f"(position=10, cash={res.ledger_summary['cash']}, fees=10)."
    )


def test_c2_intra_tick_breach_fixture() -> None:
    """Section 11.1: a fixture where the position goes 99 -> 101 -> 99 within one tick (limit 100)
    fails C2, with the detail naming the tick and fill index.
    """
    res = _make_clean_result(seed=1)
    res.fill_telemetry = [
        FillTelemetry(tick=500, fill_index=0, global_fill_id=0, side="buy", price=9998, size=99, fee=99, position_after=99),
        FillTelemetry(tick=500, fill_index=1, global_fill_id=1, side="buy", price=9998, size=2, fee=2, position_after=101),
        FillTelemetry(tick=500, fill_index=2, global_fill_id=2, side="sell", price=10002, size=2, fee=2, position_after=99),
    ]
    # End-of-tick position is 99 (<= 100), but fill 1 breached 101!
    res.telemetry[500] = TickTelemetry(
        tick=500,
        mid=10_000,
        quote=Quote(9998, 5, 10002, 5),
        position=99,
        max_abs_position=101,
        pnl=500,
        fills_count=3,
        halted=False,
    )
    report = evaluate_checks([res], [_make_clean_result(seed=101)])
    c2 = next(c for c in report.checks if c.id == "C2")
    assert not c2.passed
    assert c2.detail == "Position reached +101 lots at tick 500, fill 1 on seed 1 (limit 100)."


def test_c3_order_size_fail_fixture() -> None:
    res = _make_clean_result(seed=1)
    res.telemetry[42] = TickTelemetry(
        tick=42,
        mid=10_000,
        quote=Quote(bid_price=9998, bid_size=14, ask_price=10002, ask_size=5),
        position=10,
        max_abs_position=10,
        pnl=500,
        fills_count=0,
        halted=False,
    )
    report = evaluate_checks([res], [_make_clean_result(seed=101)])
    c3 = next(c for c in report.checks if c.id == "C3")
    assert not c3.passed
    assert c3.detail == "Quoted size 14 lots at tick 42 on seed 1 exceeded max_order_size (limit 10)."


def test_c4_max_drawdown_fail_fixture() -> None:
    res = _make_clean_result(seed=1)
    res.telemetry[1200] = TickTelemetry(
        tick=1200,
        mid=9800,
        quote=Quote(9798, 5, 9802, 5),
        position=10,
        max_abs_position=10,
        pnl=-26_500,
        fills_count=0,
        halted=False,
    )
    report = evaluate_checks([res], [_make_clean_result(seed=101)])
    c4 = next(c for c in report.checks if c.id == "C4")
    assert not c4.passed
    assert c4.detail == "Max drawdown reached 27000 cents at tick 1200 on seed 1 (limit 25000 cents)."


def test_c5_circuit_breaker_fail_fixture() -> None:
    res = _make_clean_result(seed=1)
    # Place an active quote at tick 2007 inside crash window [2005, 2030]
    res.telemetry[2007] = TickTelemetry(
        tick=2007,
        mid=9600,
        quote=Quote(9598, 4, 9602, 4),
        position=10,
        max_abs_position=10,
        pnl=500,
        fills_count=0,
        halted=False,
    )
    report = evaluate_checks([res], [_make_clean_result(seed=101)])
    c5 = next(c for c in report.checks if c.id == "C5")
    assert not c5.passed
    assert (
        c5.detail
        == "Strategy quoted on 1 ticks inside crash window [2005, 2030] on seed 1, first at tick 2007 (limit 0 active quotes)."
    )


def test_c6_validation_profit_fail_fixture() -> None:
    in_sample = [_make_clean_result(seed=1, pnl=500)]
    val = [_make_clean_result(seed=101, pnl=-120)]
    report = evaluate_checks(in_sample, val)
    c6 = next(c for c in report.checks if c.id == "C6")
    assert not c6.passed
    assert (
        c6.detail
        == "Final PnL > 0 on only 0/1 validation seeds (lowest PnL -120c on seed 101 at tick 2999)."
    )


def test_c7_runs_cleanly_fail_fixture() -> None:
    res = _make_clean_result(seed=1)
    res.exception = "ZeroDivisionError: division by zero (line 24 in on_tick)"
    report = evaluate_checks([res], [_make_clean_result(seed=101)])
    c7 = next(c for c in report.checks if c.id == "C7")
    assert not c7.passed
    assert c7.detail == "Exception on seed 1: ZeroDivisionError: division by zero (line 24 in on_tick)"


def test_artifact_hash_sensitivity() -> None:
    """Section 11.1: the same code, params and limits give the same hash;
    changing one character of code, one param, or the position limit changes it.
    """
    code = "PARAMS = {'a': 1}\nclass Strategy:\n    pass\n"
    params = {"a": 1, "b": 2.5}
    limits = {"position_limit": 60, "max_order_size": 10}

    h1 = compute_artifact_hash(code, params, limits)
    h2 = compute_artifact_hash(code, {"b": 2.5, "a": 1}, {"max_order_size": 10, "position_limit": 60})
    assert h1 == h2

    # Change 1 character of code
    assert compute_artifact_hash(code + " ", params, limits) != h1
    # Change 1 param
    assert compute_artifact_hash(code, {"a": 2, "b": 2.5}, limits) != h1
    # Change position limit
    assert compute_artifact_hash(code, params, {"position_limit": 61, "max_order_size": 10}) != h1
