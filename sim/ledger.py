"""Exchange-side ground-truth ledger in integer cents (Section 5.4)."""

from __future__ import annotations

from dataclasses import dataclass
from .types import Fill


@dataclass
class Ledger:
    position: int = 0  # lots (signed)
    cash: int = 0      # integer cents
    fees: int = 0      # integer cents

    def apply_fill(self, fill: Fill) -> None:
        """Apply a Fill to the exchange ledger using exact integer cents."""
        size = int(fill.size)
        price = int(fill.price)
        fee = int(fill.fee)
        if fill.side == "buy":
            self.position += size
            self.cash -= price * size
            self.fees += fee
        elif fill.side == "sell":
            self.position -= size
            self.cash += price * size
            self.fees += fee
        else:
            raise ValueError(f"Invalid fill side: {fill.side!r}")

    def record_buy(self, size: int, price: int, fee: int) -> None:
        """Convenience helper for recording a buy fill."""
        self.apply_fill(Fill(tick=0, side="buy", price=int(price), size=int(size), fee=int(fee)))

    def record_sell(self, size: int, price: int, fee: int) -> None:
        """Convenience helper for recording a sell fill."""
        self.apply_fill(Fill(tick=0, side="sell", price=int(price), size=int(size), fee=int(fee)))

    def pnl(self, mid: int) -> int:
        """Mark-to-market PnL in integer cents: cash - fees + position * mid."""
        return int(self.cash) - int(self.fees) + int(self.position) * int(mid)

    def summary(self, final_mid: int | None = None) -> dict[str, int]:
        """Return ledger state as a dictionary."""
        out = {
            "position": int(self.position),
            "cash": int(self.cash),
            "fees": int(self.fees),
        }
        if final_mid is not None:
            out["pnl"] = self.pnl(final_mid)
        return out
