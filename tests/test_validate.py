"""Unit tests for AST validator and restricted namespace loading (SPEC Section 11.1)."""

from __future__ import annotations

from pathlib import Path

from towertwin import config
from towertwin.sandbox.harness import load_strategy_namespace
from towertwin.sandbox.validate import validate
from towertwin.sim.types import MarketState, Quote

_MOCKS_DIR = Path(__file__).resolve().parent.parent / "mocks"

VALID_MINIMAL = '''
import math
import statistics
import collections

PARAMS = {
    "half_spread_ticks": 3,
    "quote_size": 3,
}
SWEEP_PARAMS = ("half_spread_ticks", "quote_size")

class Strategy:
    def __init__(self, params, limits):
        self.half_spread = int(params["half_spread_ticks"])
        self.sz = int(params["quote_size"])

    def on_tick(self, state):
        return Quote(
            bid_price=state.mid - self.half_spread,
            bid_size=self.sz,
            ask_price=state.mid + self.half_spread,
            ask_size=self.sz,
        )

    def on_fill(self, fill):
        pass

    def report(self):
        return {"position": 0, "cash": 0, "fees": 0}
'''


def test_rejects_import_os():
    code = "import os\n" + VALID_MINIMAL
    res = validate(code)
    assert not res.ok
    assert any("import 'os'" in e or "Forbidden identifier 'os'" in e for e in res.errors)


def test_rejects_open_call():
    code = VALID_MINIMAL.replace("pass", "open('secret.txt')")
    res = validate(code)
    assert not res.ok
    assert any("open" in e for e in res.errors)


def test_rejects_dunder_import():
    code = VALID_MINIMAL.replace("pass", "__import__('os')")
    res = validate(code)
    assert not res.ok
    assert any("__import__" in e for e in res.errors)


def test_rejects_dunder_class_subclasses():
    code = VALID_MINIMAL.replace("pass", "().__class__.__subclasses__()")
    res = validate(code)
    assert not res.ok
    assert any("__class__" in e or "__subclasses__" in e for e in res.errors)


def test_rejects_getattr():
    code = VALID_MINIMAL.replace("pass", "getattr(math, 'x')")
    res = validate(code)
    assert not res.ok
    assert any("getattr" in e for e in res.errors)


def test_rejects_files_over_300_lines():
    padded = VALID_MINIMAL + ("\n# comment line" * 305)
    res = validate(padded)
    assert not res.ok
    assert any("300" in e for e in res.errors)


def test_rejects_missing_params_or_sweep_params():
    no_params = VALID_MINIMAL.replace("PARAMS = {", "OTHER = {")
    res1 = validate(no_params)
    assert not res1.ok
    assert any("PARAMS" in e for e in res1.errors)

    no_sweep = VALID_MINIMAL.replace('SWEEP_PARAMS = ("half_spread_ticks", "quote_size")', "OTHER_SWEEP = 1")
    res2 = validate(no_sweep)
    assert not res2.ok
    assert any("SWEEP_PARAMS" in e for e in res2.errors)


def test_accepts_all_four_mock_strategies_and_runs_in_restricted_namespace():
    limits = config.get_desk_limits()
    for fname in (
        "strategy_v1_naive.py",
        "strategy_v2_skew.py",
        "strategy_v3_desk.py",
        "strategy_v4_repaired.py",
    ):
        code = (_MOCKS_DIR / fname).read_text(encoding="utf-8")
        res = validate(code)
        assert res.ok, f"{fname} failed validation: {res.errors}"
        assert res.params, f"{fname} missing parsed PARAMS"
        assert res.sweep_params is not None and len(res.sweep_params) == 2

        # Verify strategy loads and runs inside restricted harness namespace
        # (which omits __import__ and includes __build_class__ and __name__)
        ns = load_strategy_namespace(code)
        assert "__import__" not in ns["__builtins__"]
        assert "__build_class__" in ns["__builtins__"]
        assert ns["__name__"] == "strategy"

        strat_cls = ns["Strategy"]
        bot = strat_cls(dict(ns["PARAMS"]), dict(limits))
        state = MarketState(
            tick=0,
            mid=10_000,
            recent_mids=(10_000, 10_000, 10_000),
            position=0,
            recent_trades=((10_000, "buy", 2),),
            limits=dict(limits),
        )
        q = bot.on_tick(state)
        assert isinstance(q, Quote)
        rep = bot.report()
        assert isinstance(rep, dict)
        assert set(rep.keys()) >= {"position", "cash", "fees"}
