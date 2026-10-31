"""Calibration test suite for all four mock strategies (Section 11.3)."""

from __future__ import annotations

from towertwin import config
from towertwin.checks.checks import CheckReport, evaluate_checks
from towertwin.mocks import (
    read_mock_strategy,
    strategy_v1_naive,
    strategy_v2_skew,
    strategy_v3_desk,
    strategy_v4_repaired,
)
from towertwin.sim.market import run_backtest
from towertwin.sim.scenarios import BASELINE_CRASH


def _run_strategy_module(mod, position_limit: int) -> CheckReport:
    limits = config.get_desk_limits(position_limit)
    code = read_mock_strategy(mod.__name__.split(".")[-1])
    in_sample_res = [
        run_backtest(mod.Strategy(mod.PARAMS, limits), seed=s, scenario=BASELINE_CRASH, ticks=config.TICKS, limits=limits)
        for s in config.in_sample_seeds
    ]
    val_res = [
        run_backtest(mod.Strategy(mod.PARAMS, limits), seed=s, scenario=BASELINE_CRASH, ticks=config.TICKS, limits=limits)
        for s in config.oos_seeds
    ]
    return evaluate_checks(
        in_sample_results=in_sample_res,
        validation_results=val_res,
        limits=limits,
        code=code,
        params=mod.PARAMS,
    )


def test_calibration_v1_naive() -> None:
    """Section 11.3: strategy_v1_naive fails C2 and C5 (and may fail C4) at limit 100."""
    report = _run_strategy_module(strategy_v1_naive, position_limit=100)
    by_id = {c.id: c for c in report.checks}
    assert by_id["C1"].passed, by_id["C1"].detail
    assert not by_id["C2"].passed, f"Expected v1 to fail C2, got: {by_id['C2'].detail}"
    assert not by_id["C5"].passed, f"Expected v1 to fail C5, got: {by_id['C5'].detail}"
    assert by_id["C7"].passed, by_id["C7"].detail


def test_calibration_v2_skew() -> None:
    """Section 11.3: strategy_v2_skew passes C2 but fails C5 at limit 100."""
    report = _run_strategy_module(strategy_v2_skew, position_limit=100)
    by_id = {c.id: c for c in report.checks}
    assert by_id["C1"].passed, by_id["C1"].detail
    assert by_id["C2"].passed, f"Expected v2 to pass C2, got: {by_id['C2'].detail}"
    assert not by_id["C5"].passed, f"Expected v2 to fail C5, got: {by_id['C5'].detail}"
    assert by_id["C7"].passed, by_id["C7"].detail


def test_calibration_v3_desk() -> None:
    """Section 11.3: strategy_v3_desk passes all of C1-C7 at limit 100,
    and fails C2 at limits 40, 60 and 80.
    """
    rep_100 = _run_strategy_module(strategy_v3_desk, position_limit=100)
    for c in rep_100.checks:
        assert c.passed, f"v3_desk @ 100 failed {c.id}: {c.detail}"
    assert rep_100.all_passed

    for lim in (40, 60, 80):
        rep = _run_strategy_module(strategy_v3_desk, position_limit=lim)
        by_id = {c.id: c for c in rep.checks}
        assert not by_id["C2"].passed, f"Expected v3_desk @ {lim} to fail C2, got: {by_id['C2'].detail}"


def test_calibration_v4_repaired() -> None:
    """Section 11.3: strategy_v4_repaired passes all of C1-C7 at limits 40, 60, 80 and 100."""
    for lim in (40, 60, 80, 100):
        rep = _run_strategy_module(strategy_v4_repaired, position_limit=lim)
        for c in rep.checks:
            assert c.passed, f"v4_repaired @ {lim} failed {c.id}: {c.detail}"
        assert rep.all_passed
