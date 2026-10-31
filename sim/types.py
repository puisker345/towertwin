"""Data types for the TowerTwin market simulator (Section 5.1)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Quote:
    bid_price: int | None   # cents; None = no bid
    bid_size: int           # lots, >= 0
    ask_price: int | None   # cents; None = no ask
    ask_size: int


@dataclass(frozen=True)
class Fill:
    tick: int
    side: str               # "buy" (we bought, hit on our bid) or "sell" (we sold, lifted on our ask)
    price: int              # cents
    size: int               # lots
    fee: int                # cents charged to us


@dataclass(frozen=True)
class MarketState:
    tick: int
    mid: int                         # cents, current mid
    recent_mids: tuple[int, ...]     # last 50 mids, oldest first (past only, never future)
    position: int                    # our position per exchange ledger (lots, signed)
    recent_trades: tuple[tuple[int, str, int], ...]  # last 20 market trades: (price, aggressor_side, size)
    limits: dict[str, Any]           # the desk rules (position_limit, max_order_size, ...)
