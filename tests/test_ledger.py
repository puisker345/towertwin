"""Unit tests for the exchange-side integer-cent ledger (Section 11.1)."""

from __future__ import annotations

from towertwin.sim.ledger import Ledger
from towertwin.sim.types import Fill


def test_ledger_hand_computed_example() -> None:
    """Section 11.1:
    buy 3 @ 10,000 (fee 3), sell 2 @ 10,005 (fee 2), mark at 10,002
    -> position 1, cash = -30,000 + 20,010 = -9,990, fees 5,
    PnL = -9,990 - 5 + 10,002 = 7 cents. Assert exactly.
    """
    ledger = Ledger()
    ledger.apply_fill(Fill(tick=10, side="buy", price=10_000, size=3, fee=3))
    ledger.apply_fill(Fill(tick=11, side="sell", price=10_005, size=2, fee=2))

    assert ledger.position == 1
    assert ledger.cash == -30_000 + 20_010
    assert ledger.cash == -9_990
    assert ledger.fees == 5
    assert ledger.pnl(10_002) == -9_990 - 5 + 10_002
    assert ledger.pnl(10_002) == 7

    summary = ledger.summary(10_002)
    assert summary == {
        "position": 1,
        "cash": -9_990,
        "fees": 5,
        "pnl": 7,
    }
