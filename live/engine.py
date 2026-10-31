"""Real-time 20 Hz market twin engine with server-enforced approval gate and kill switch (Section 10.2)."""

from __future__ import annotations

import asyncio
import collections
import math
from pathlib import Path
from typing import Any

import numpy as np

from .. import config
from ..agent.final_verify import VerifiedArtifact, run_final_verification
from ..checks.checks import compute_artifact_hash
from ..events import event_bus
from ..sandbox.runner import LiveStrategySubprocess
from ..sandbox.validate import validate
from ..sim.ledger import Ledger
from ..sim.scenarios import BASELINE_CRASH, Scenario
from ..sim.types import Fill, MarketState, Quote

_MOCKS_DIR = Path(__file__).resolve().parent.parent / "mocks"


class LiveEngine:
    """Real-time (~20 ticks/sec) digital twin market engine."""

    def __init__(self) -> None:
        self.rng = np.random.default_rng(777)
        self.tick: int = 0
        self.mid_float: float = float(config.INITIAL_MID)
        self.mid: int = config.INITIAL_MID
        self.recent_mids: collections.deque[int] = collections.deque([config.INITIAL_MID], maxlen=50)
        self.recent_trades: collections.deque[tuple[int, str, int]] = collections.deque(maxlen=20)
        self.ledger = Ledger()
        self.peak_pnl: int = 0
        self.max_drawdown: int = 0

        self.limits: dict[str, Any] = config.get_desk_limits()
        self.verified_limits_of_loaded_bot: dict[str, Any] = config.get_desk_limits()

        # Loaded strategy state (always executed in an isolated child subprocess)
        self.loaded_code: str = ""
        self.loaded_params: dict[str, Any] = {}
        self.loaded_artifact_hash: str = ""
        self.loaded_artifact_id: str = ""
        self.bot_label: str = "desk strategy"
        self._worker: LiveStrategySubprocess | None = None

        # Pending verified artifact from the most recent orchestrator run
        self.pending_artifact: VerifiedArtifact | None = None
        self.startup_verified_artifact: VerifiedArtifact | None = None

        self.halted: bool = False
        self.killed: bool = False
        self.active_crash: Scenario | None = None
        self.crash_start_tick: int = -1
        self.crash_Start_mid: float = float(config.INITIAL_MID)

        self._task: asyncio.Task[None] | None = None
        self._running: bool = False

    @property
    def bot_status(self) -> str:
        if self.killed:
            return "KILLED BY HUMAN"
        ver_lim = int(self.verified_limits_of_loaded_bot.get("position_limit", 100))
        cur_lim = int(self.limits.get("position_limit", 100))
        if self.bot_label == "desk strategy" and ver_lim != cur_lim:
            return f"Running: desk strategy, verified at limit {ver_lim} (ACTIVE LIMIT {cur_lim})"
        return f"Running: {self.bot_label}, verified at limit {ver_lim}"

    async def initialize_startup_bot(self) -> VerifiedArtifact:
        """Verify `strategy_v3_desk.py` at default limit 100 on startup and deploy it (Section 10.2)."""
        desk_code = (_MOCKS_DIR / "strategy_v3_desk.py").read_text(encoding="utf-8")
        val = validate(desk_code)
        if not val.ok:
            raise RuntimeError(f"Startup desk strategy failed validation: {val.errors}")
        default_limits = config.get_desk_limits()
        artifact = await run_final_verification(
            code=desk_code,
            loop_params=val.params,
            review_params=val.params,
            limits=default_limits,
            report_md="Initial startup verification of desk strategy (`strategy_v3_desk.py`) at 100-lot limit.",
            briefing_text="Startup desk strategy verified at one hundred lots.",
            emit_events=False,
        )
        if not artifact.passed:
            failed = artifact.report.failed_checks_text()
            raise RuntimeError(f"Startup verification of strategy_v3_desk failed: {failed}")

        self.startup_verified_artifact = artifact
        self._deploy_artifact(artifact, label="desk strategy")
        return artifact

    def _deploy_artifact(self, artifact: VerifiedArtifact, label: str) -> None:
        if self._worker is not None:
            self._worker.close()
            self._worker = None

        self.loaded_code = artifact.code
        self.loaded_params = dict(artifact.params)
        self.limits = dict(artifact.limits)
        self.verified_limits_of_loaded_bot = dict(artifact.limits)
        self.loaded_artifact_hash = compute_artifact_hash(
            self.loaded_code, self.loaded_params, self.verified_limits_of_loaded_bot
        )
        assert self.loaded_artifact_hash == artifact.artifact_hash
        self.loaded_artifact_id = self.loaded_artifact_hash[:8]
        self.bot_label = label
        self.killed = False
        self.halted = False
        self._worker = LiveStrategySubprocess(
            code=self.loaded_code,
            params=self.loaded_params,
            limits=self.verified_limits_of_loaded_bot,
        )

    def set_desk_limits(self, new_limits: dict[str, Any]) -> None:
        """Update active desk rules while keeping the current bot trading until a new artifact is approved."""
        self.limits = dict(new_limits)
        # Invalidate any stale pending artifact if desk rules changed
        if self.pending_artifact is not None and self.pending_artifact.limits != self.limits:
            self.pending_artifact = None

    def set_pending_artifact(self, artifact: VerifiedArtifact | None) -> None:
        """Store the verified artifact produced by `final_verify.py` so `/api/approve` can verify its hash."""
        if artifact is not None and artifact.passed:
            self.pending_artifact = artifact
        else:
            self.pending_artifact = None

    def approve(self, artifact_hash: str) -> dict[str, Any]:
        """Server-enforced approval gate (Section 8.6 & 10.2).

        Raises PermissionError (mapped to HTTP 409) unless `artifact_hash` matches the stored verified artifact.
        """
        if not artifact_hash or not isinstance(artifact_hash, str):
            raise PermissionError("Missing artifact_hash.")
        if self.pending_artifact is None or not self.pending_artifact.passed:
            raise PermissionError("No verified artifact is awaiting sign-off (`signoff_ready` has not fired).")
        if artifact_hash != self.pending_artifact.artifact_hash:
            raise PermissionError(
                f"Hash mismatch: requested {artifact_hash[:8]} does not match verified artifact {self.pending_artifact.artifact_id}."
            )

        approved_art = self.pending_artifact
        # Consume pending artifact so a stale repeat or altered rule cannot reuse it without re-verifying
        self.pending_artifact = None

        # Reset live ledger & position cleanly when hot-swapping the newly approved strategy
        self.ledger = Ledger()
        self.peak_pnl = 0
        self.max_drawdown = 0
        self._deploy_artifact(approved_art, label=f"repaired strategy ({approved_art.artifact_id})")

        payload = {
            "artifact_hash": self.loaded_artifact_hash,
            "artifact_id": self.loaded_artifact_id,
            "params": dict(self.loaded_params),
            "limits": dict(self.limits),
            "bot_status": self.bot_status,
        }
        event_bus.emit("approved", **payload)
        return payload

    def trigger_crash(self, scenario: Scenario | None = None) -> dict[str, Any]:
        """Inject a flash crash starting at the next live tick (Section 10.2)."""
        sc = scenario if scenario is not None else BASELINE_CRASH
        self.active_crash = sc
        self.crash_start_tick = self.tick + 1
        self.crash_Start_mid = self.mid_float
        payload = {
            "tick": self.crash_start_tick,
            "scenario": sc.to_dict(),
        }
        event_bus.emit("crash_started", **payload)
        return payload

    def kill_switch(self) -> dict[str, Any]:
        """Immediately unload the bot and set all quotes to None (Section 10.2)."""
        self.killed = True
        self.halted = False
        if self._worker is not None:
            self._worker.close()
            self._worker = None
        payload = {
            "tick": self.tick,
            "bot_status": self.bot_status,
        }
        event_bus.emit("killed", **payload)
        return payload

    async def reset(self) -> dict[str, Any]:
        """Reset live twin to the initial startup desk strategy and clear crash/kill states."""
        self.active_crash = None
        self.crash_start_tick = -1
        self.ledger = Ledger()
        self.peak_pnl = 0
        self.max_drawdown = 0
        self.mid_float = float(config.INITIAL_MID)
        self.mid = config.INITIAL_MID
        self.recent_mids.clear()
        self.recent_mids.append(config.INITIAL_MID)
        self.recent_trades.clear()
        self.pending_artifact = None

        if self.startup_verified_artifact is None:
            await self.initialize_startup_bot()
        else:
            self._deploy_artifact(self.startup_verified_artifact, label="desk strategy")

        return {
            "tick": self.tick,
            "limits": dict(self.limits),
            "bot_status": self.bot_status,
            "artifact_hash": self.loaded_artifact_hash,
            "artifact_id": self.loaded_artifact_id,
        }

    def _synthesize_depth(self, in_crash: bool) -> dict[str, list[float]]:
        """Synthesize visual resting depth profile around mid for the 3D terrain mesh (Section 10.2)."""
        thin_mult = 0.22 if in_crash else 1.0
        bids: list[float] = []
        asks: list[float] = []
        for i in range(1, 21):
            base = 18.0 * math.exp(-0.11 * i) * thin_mult
            b_noise = float(self.rng.uniform(0.75, 1.25))
            a_noise = float(self.rng.uniform(0.75, 1.25))
            bids.append(round(base * b_noise, 2))
            asks.append(round(base * a_noise, 2))
        return {"bids": bids, "asks": asks}

    def step_once(self) -> dict[str, Any]:
        """Advance the live twin by 1 tick and emit a `tick` SSE event."""
        self.tick += 1
        t = self.tick

        in_crash = False
        in_recovery = False
        if self.active_crash is not None and self.crash_start_tick >= 0:
            sc = self.active_crash
            c_end = self.crash_start_tick + sc.duration
            r_end = c_end + config.RECOVERY_TICKS
            if self.crash_start_tick <= t < c_end:
                in_crash = True
            elif c_end <= t < r_end:
                in_recovery = True
            elif t >= r_end:
                self.active_crash = None
                self.crash_start_tick = -1

        if in_crash and self.active_crash is not None:
            sc = self.active_crash
            total_drop = self.crash_Start_mid * (sc.depth_pct / 100.0)
            drift = -total_drop / float(sc.duration)
            sigma = config.SIGMA_NORMAL * sc.sigma_mult
            buy_lam = config.LAMBDA_BASE * sc.buy_flow_mult
            sell_lam = config.LAMBDA_BASE * sc.sell_flow_mult
        elif in_recovery and self.active_crash is not None:
            sc = self.active_crash
            total_drop = self.crash_Start_mid * (sc.depth_pct / 100.0)
            drift = (total_drop * (sc.recovery_pct / 100.0)) / float(config.RECOVERY_TICKS)
            sigma = config.SIGMA_NORMAL * 1.4
            buy_lam = config.LAMBDA_BASE
            sell_lam = config.LAMBDA_BASE
        else:
            drift = -config.KAPPA * (self.mid_float - float(config.INITIAL_MID))
            sigma = config.SIGMA_NORMAL
            buy_lam = config.LAMBDA_BASE
            sell_lam = config.LAMBDA_BASE

        eps = float(self.rng.standard_normal())
        self.mid_float = max(100.0, self.mid_float + drift + sigma * eps)
        self.mid = int(round(self.mid_float))
        self.recent_mids.append(self.mid)

        # Compute realized volatility over recent mids
        mids_list = list(self.recent_mids)
        if len(mids_list) >= 4:
            diffs = [mids_list[i] - mids_list[i - 1] for i in range(1, len(mids_list))]
            mean_d = sum(diffs) / len(diffs)
            vol = math.sqrt(sum((d - mean_d) ** 2 for d in diffs) / len(diffs))
        else:
            vol = config.SIGMA_NORMAL

        quote_dict: dict[str, Any] | None = None
        tick_fills: list[dict[str, Any]] = []

        if not self.killed and self._worker is not None:
            state = MarketState(
                tick=t,
                mid=self.mid,
                recent_mids=tuple(self.recent_mids),
                position=self.ledger.position,
                recent_trades=tuple(self.recent_trades),
                limits=dict(self.limits),
            )
            q = self._worker.step(state, [])
            is_quoting = (
                (q.bid_price is not None and q.bid_size > 0)
                or (q.ask_price is not None and q.ask_size > 0)
            )
            if not is_quoting:
                if not self.halted:
                    self.halted = True
                    event_bus.emit("halted", tick=t, reason="circuit_breaker")
            else:
                if self.halted:
                    self.halted = False
                    event_bus.emit("resumed", tick=t)

            if is_quoting:
                quote_dict = {
                    "bid_price": q.bid_price,
                    "bid_size": q.bid_size,
                    "ask_price": q.ask_price,
                    "ask_size": q.ask_size,
                }

            # Simulate market orders hitting our quotes
            n_buys = int(self.rng.poisson(buy_lam))
            n_sells = int(self.rng.poisson(sell_lam))
            orders: list[tuple[str, int, int]] = []
            for _ in range(n_buys):
                sz = int(self.rng.integers(1, 6))
                agg = max(0, int(round(float(self.rng.exponential(config.AGGRESSION_SCALE_CENTS)))))
                orders.append(("buy", self.mid + agg, sz))
            for _ in range(n_sells):
                sz = int(self.rng.integers(1, 6))
                agg = max(0, int(round(float(self.rng.exponential(config.AGGRESSION_SCALE_CENTS)))))
                orders.append(("sell", max(1, self.mid - agg), sz))

            if len(orders) > 1:
                perm = self.rng.permutation(len(orders))
                orders = [orders[int(i)] for i in perm]

            rem_bid = q.bid_size if q.bid_price is not None else 0
            rem_ask = q.ask_size if q.ask_price is not None else 0
            new_fill_objs: list[Fill] = []

            for agg_side, limit_px, o_sz in orders:
                self.recent_trades.append((limit_px, agg_side, o_sz))
                if agg_side == "sell" and q.bid_price is not None and rem_bid > 0 and limit_px <= q.bid_price:
                    f_sz = min(rem_bid, o_sz)
                    fee = f_sz * config.FEE_CENTS_PER_LOT
                    f_obj = Fill(tick=t, side="buy", price=q.bid_price, size=f_sz, fee=fee)
                    self.ledger.apply_fill(f_obj)
                    rem_bid -= f_sz
                    new_fill_objs.append(f_obj)
                    tick_fills.append({"tick": t, "side": "buy", "price": q.bid_price, "size": f_sz, "fee": fee})
                elif agg_side == "buy" and q.ask_price is not None and rem_ask > 0 and limit_px >= q.ask_price:
                    f_sz = min(rem_ask, o_sz)
                    fee = f_sz * config.FEE_CENTS_PER_LOT
                    f_obj = Fill(tick=t, side="sell", price=q.ask_price, size=f_sz, fee=fee)
                    self.ledger.apply_fill(f_obj)
                    rem_ask -= f_sz
                    new_fill_objs.append(f_obj)
                    tick_fills.append({"tick": t, "side": "sell", "price": q.ask_price, "size": f_sz, "fee": fee})

            if new_fill_objs and self._worker is not None:
                # Deliver fills to strategy worker
                self._worker.step(state, new_fill_objs)

        pnl = self.ledger.pnl(self.mid)
        if pnl > self.peak_pnl:
            self.peak_pnl = pnl
        dd = self.peak_pnl - pnl
        if dd > self.max_drawdown:
            self.max_drawdown = dd

        depth_snapshot = self._synthesize_depth(in_crash)
        pos_limit = int(self.limits.get("position_limit", 100))

        evt = event_bus.emit(
            "tick",
            tick=t,
            mid=self.mid,
            best_bid=self.mid - 1,
            best_ask=self.mid + 1,
            depth=depth_snapshot,
            quote=None if self.killed else quote_dict,
            fills=tick_fills,
            position=self.ledger.position,
            position_breach=abs(self.ledger.position) > pos_limit,
            pnl=pnl,
            cash=self.ledger.cash,
            fees=self.ledger.fees,
            drawdown=dd,
            max_drawdown=self.max_drawdown,
            halted=self.halted,
            killed=self.killed,
            crash_active=in_crash,
            volatility=round(vol, 3),
            limits=dict(self.limits),
            bot_status=self.bot_status,
            artifact_hash=self.loaded_artifact_hash,
            artifact_id=self.loaded_artifact_id,
        )
        return evt

    async def _run_loop(self) -> None:
        while self._running:
            try:
                self.step_once()
            except Exception:  # noqa: BLE001
                pass
            await asyncio.sleep(0.05)

    async def start(self) -> None:
        if self._running:
            return
        if self.startup_verified_artifact is None:
            await self.initialize_startup_bot()
        self._running = True
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._worker is not None:
            self._worker.close()
            self._worker = None


live_engine = LiveEngine()
