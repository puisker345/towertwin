"""Unit tests for isolated subprocess runner timeouts and exceptions (SPEC Section 11.1)."""

from __future__ import annotations

import time

from towertwin.checks.checks import evaluate_checks
from towertwin.sandbox.runner import run_backtests
from towertwin.sim.scenarios import BASELINE_CRASH


INFINITE_LOOP_STRATEGY = '''
import math

PARAMS = {
    "half_spread_ticks": 3,
    "quote_size": 3,
}
SWEEP_PARAMS = ("half_spread_ticks", "quote_size")

class Strategy:
    def __init__(self, params, limits):
        self.params = params
        self.limits = limits

    def on_tick(self, state):
        while True:
            pass

    def on_fill(self, fill):
        pass

    def report(self):
        return {"position": 0, "cash": 0, "fees": 0}
'''


ZERO_DIV_STRATEGY = '''
import math

PARAMS = {
    "half_spread_ticks": 3,
    "quote_size": 3,
}
SWEEP_PARAMS = ("half_spread_ticks", "quote_size")

class Strategy:
    def __init__(self, params, limits):
        self.params = params
        self.limits = limits

    def on_tick(self, state):
        x = 1 / 0
        return Quote(state.mid - 5, 2, state.mid + 5, 2)

    def on_fill(self, fill):
        pass

    def report(self):
        return {"position": 0, "cash": 0, "fees": 0}
'''


def test_infinite_loop_killed_and_reported_as_c7_failed():
    t0 = time.perf_counter()
    # Use a 2.5s timeout in unit test so it verifies subprocess kill well within the 35s ceiling
    results = run_backtests(
        code=INFINITE_LOOP_STRATEGY,
        scenario=BASELINE_CRASH,
        seeds=[101, 102],
        ticks=600,
        timeout_sec=2.5,
    )
    elapsed = time.perf_counter() - t0
    assert elapsed < 35.0
    assert len(results) == 2
    assert all(r.timed_out for r in results)

    report = evaluate_checks(results, code=INFINITE_LOOP_STRATEGY)
    c7 = next(c for c in report.checks if c.id == "C7")
    assert not c7.passed
    assert "timed out" in c7.detail.lower() or "timeout" in c7.detail.lower()


def test_exception_in_on_tick_reported_as_c7_failed_with_traceback():
    results = run_backtests(
        code=ZERO_DIV_STRATEGY,
        scenario=BASELINE_CRASH,
        seeds=[101],
        ticks=600,
        timeout_sec=15.0,
    )
    assert len(results) == 1
    assert results[0].exception is not None
    assert "ZeroDivisionError" in results[0].exception

    report = evaluate_checks(results, code=ZERO_DIV_STRATEGY)
    c7 = next(c for c in report.checks if c.id == "C7")
    assert not c7.passed
    assert "ZeroDivisionError" in c7.detail
