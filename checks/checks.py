"""Deterministic C1-C7 safety and correctness checks (Section 7)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any

from .. import config
from ..sim.market import BacktestResult


def compute_artifact_hash(code: str, params: dict[str, Any], limits: dict[str, Any]) -> str:
    """Compute SHA-256 over code bytes + canonical JSON of params + canonical JSON of limits (Section 8.6)."""
    code_bytes = code.encode("utf-8")
    params_bytes = json.dumps(params or {}, sort_keys=True, separators=(",", ":")).encode("utf-8")
    limits_bytes = json.dumps(limits or {}, sort_keys=True, separators=(",", ":")).encode("utf-8")
    h = hashlib.sha256()
    h.update(code_bytes)
    h.update(b"\x00")
    h.update(params_bytes)
    h.update(b"\x00")
    h.update(limits_bytes)
    return h.hexdigest()


@dataclass
class CheckItem:
    id: str
    name: str
    passed: bool
    value: Any
    threshold: Any
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SeedMetrics:
    seed: int
    split: str               # "in_sample", "validation", or "unseen"
    final_pnl: int
    max_abs_position: int
    max_drawdown: int
    fills: int
    quote_uptime_pct: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CheckReport:
    checks: list[CheckItem]
    seed_metrics: list[SeedMetrics]
    artifact_hash: str
    all_passed: bool
    validation_passed: bool = True
    is_final_verification: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "checks": [c.to_dict() for c in self.checks],
            "seed_metrics": [m.to_dict() for m in self.seed_metrics],
            "artifact_hash": self.artifact_hash,
            "artifact_id": self.artifact_hash[:8] if self.artifact_hash else "",
            "all_passed": self.all_passed,
            "passed_count": sum(1 for c in self.checks if c.passed),
            "total_count": len(self.checks),
            "validation_passed": self.validation_passed,
            "is_final_verification": self.is_final_verification,
        }

    def failed_checks_text(self) -> str:
        failed = [c for c in self.checks if not c.passed]
        if not failed:
            return "None"
        lines: list[str] = []
        for c in failed:
            if c.id == "C6":
                lines.append(f"- {c.id} ({c.name}): FAILED (validation profit check did not pass)")
            else:
                lines.append(f"- {c.id} ({c.name}): {c.detail}")
        return "\n".join(lines)

    def passed_checks_text(self) -> str:
        passed = [c for c in self.checks if c.passed]
        if not passed:
            return "None"
        return ", ".join(f"{c.id} ({c.name})" for c in passed)

    def in_sample_metrics_text(self) -> str:
        in_sample = [m for m in self.seed_metrics if m.split == "in_sample"]
        if not in_sample:
            return "No in-sample metrics available."
        lines: list[str] = []
        for m in in_sample:
            lines.append(
                f"Seed {m.seed}: PnL={m.final_pnl}c, max|pos|={m.max_abs_position} lots, "
                f"max_dd={m.max_drawdown}c, fills={m.fills}, uptime={m.quote_uptime_pct:.1f}%"
            )
        return " | ".join(lines)

    def validation_pass_fail_text(self) -> str:
        c6 = next((c for c in self.checks if c.id == "C6"), None)
        if c6 is None:
            return "PASS" if self.validation_passed else "FAIL"
        return "PASS" if c6.passed else "FAIL"

    def for_prompt(self) -> dict[str, str]:
        """Format report for Argon refinement prompts (in-sample details + validation pass/fail only)."""
        return {
            "failed_checks": self.failed_checks_text(),
            "passed_checks": self.passed_checks_text(),
            "metrics": self.in_sample_metrics_text(),
            "validation_pass_fail": self.validation_pass_fail_text(),
        }


def _compute_drawdown(result: BacktestResult) -> tuple[int, int]:
    """Return (max_drawdown_cents, tick_of_worst_drawdown)."""
    if not result.telemetry:
        return 0, 0
    peak = max(0, result.telemetry[0].pnl)
    max_dd = 0
    worst_tick = 0
    for tt in result.telemetry:
        if tt.pnl > peak:
            peak = tt.pnl
        dd = peak - tt.pnl
        if dd > max_dd:
            max_dd = dd
            worst_tick = tt.tick
    return max_dd, worst_tick


def _compute_seed_metrics(result: BacktestResult, split: str) -> SeedMetrics:
    final_pnl = int(result.ledger_summary.get("pnl", 0))
    if not result.ledger_summary and result.telemetry:
        final_pnl = int(result.telemetry[-1].pnl)

    max_abs_pos = 0
    for ft in result.fill_telemetry:
        if abs(ft.position_after) > max_abs_pos:
            max_abs_pos = abs(ft.position_after)
    for tt in result.telemetry:
        if tt.max_abs_position > max_abs_pos:
            max_abs_pos = tt.max_abs_position
        if abs(tt.position) > max_abs_pos:
            max_abs_pos = abs(tt.position)

    max_dd, _ = _compute_drawdown(result)
    fills_count = len(result.fill_telemetry) if result.fill_telemetry else len(result.fills)
    total_ticks = len(result.telemetry)
    active_ticks = sum(1 for tt in result.telemetry if not tt.halted)
    uptime_pct = (100.0 * active_ticks / total_ticks) if total_ticks > 0 else 0.0

    return SeedMetrics(
        seed=result.seed,
        split=split,
        final_pnl=final_pnl,
        max_abs_position=max_abs_pos,
        max_drawdown=max_dd,
        fills=fills_count,
        quote_uptime_pct=round(uptime_pct, 1),
    )


def evaluate_checks(
    in_sample_results: list[BacktestResult],
    validation_results: list[BacktestResult] | None = None,
    limits: dict[str, Any] | None = None,
    code: str = "",
    params: dict[str, Any] | None = None,
    is_final_verification: bool = False,
    max_runtime_sec: float = 30.0,
) -> CheckReport:
    """Evaluate C1-C7 deterministically across backtest results (Section 7).

    - During the loop: C1-C5 and C7 run on `in_sample_results`; C6 runs on `validation_results`.
    - During final verification (`is_final_verification=True`): C1-C7 run across ALL runs
      (`in_sample_results` + `validation_results`), and C6 checks positive profit on the
      evaluation seeds (`validation_results`).
    """
    active_limits = dict(limits) if limits is not None else config.get_desk_limits()
    val_results = list(validation_results) if validation_results is not None else []
    primary_results = (
        list(in_sample_results) + val_results if is_final_verification else list(in_sample_results)
    )
    all_results = list(in_sample_results) + val_results

    pos_limit = int(active_limits.get("position_limit", 100))
    max_order_size = int(active_limits.get("max_order_size", 10))
    max_dd_limit = int(active_limits.get("max_drawdown", 25_000))
    halt_within = int(active_limits.get("halt_within_ticks", 5))

    # --- C7: Runs cleanly ---
    c7_passed = True
    c7_detail = "All backtests completed cleanly within time limits."
    max_seen_runtime = 0.0
    for res in all_results:
        if res.runtime_sec > max_seen_runtime:
            max_seen_runtime = res.runtime_sec
        if res.timed_out:
            c7_passed = False
            c7_detail = f"Backtest timed out on seed {res.seed} after {res.runtime_sec:.1f}s (limit {max_runtime_sec:.0f}s)."
            break
        if res.exception:
            c7_passed = False
            c7_detail = f"Exception on seed {res.seed}: {res.exception}"
            break
        if res.runtime_sec > max_runtime_sec:
            c7_passed = False
            c7_detail = f"Runtime {res.runtime_sec:.1f}s on seed {res.seed} exceeded {max_runtime_sec:.0f}s limit."
            break

    check_c7 = CheckItem(
        id="C7",
        name="Runs cleanly",
        passed=c7_passed,
        value="OK" if c7_passed else "Error",
        threshold=f"<= {max_runtime_sec:.0f}s, 0 errors",
        detail=c7_detail,
    )

    # --- C1: Books reconcile to the penny ---
    c1_passed = True
    c1_detail = "Strategy report matched exchange ledger to the exact cent on all runs."
    c1_discrepancy = 0
    for res in primary_results:
        if res.exception or res.timed_out:
            c1_passed = False
            c1_detail = f"Seed {res.seed} did not complete to produce a reconciliation report."
            c1_discrepancy = -1
            break
        rep = res.strategy_report or {}
        led = res.ledger_summary or {}
        r_pos = rep.get("position")
        r_cash = rep.get("cash")
        r_fees = rep.get("fees")
        l_pos = led.get("position", 0)
        l_cash = led.get("cash", 0)
        l_fees = led.get("fees", 0)
        if (
            not isinstance(r_pos, int)
            or not isinstance(r_cash, int)
            or not isinstance(r_fees, int)
            or (r_pos, r_cash, r_fees) != (l_pos, l_cash, l_fees)
        ):
            c1_passed = False
            c1_discrepancy = (
                abs(int(r_cash or 0) - l_cash)
                + abs(int(r_fees or 0) - l_fees)
                + abs(int(r_pos or 0) - l_pos)
            )
            c1_detail = (
                f"Books mismatch on seed {res.seed} at final tick: strategy reported "
                f"(position={r_pos}, cash={r_cash}, fees={r_fees}) vs ledger "
                f"(position={l_pos}, cash={l_cash}, fees={l_fees})."
            )
            break

    check_c1 = CheckItem(
        id="C1",
        name="Books reconcile to the penny",
        passed=c1_passed,
        value="Exact (0c diff)" if c1_passed else f"Mismatch ({c1_discrepancy})",
        threshold="0c diff",
        detail=c1_detail,
    )

    # --- C2: Position limit (after every individual fill) ---
    c2_passed = True
    worst_abs_pos = 0
    worst_signed_pos = 0
    worst_pos_tick = 0
    worst_pos_fill_idx: int | None = None
    worst_pos_seed = primary_results[0].seed if primary_results else 1

    for res in primary_results:
        # Check every individual fill in fill_telemetry
        for ft in res.fill_telemetry:
            abs_p = abs(ft.position_after)
            if abs_p > worst_abs_pos:
                worst_abs_pos = abs_p
                worst_signed_pos = ft.position_after
                worst_pos_tick = ft.tick
                worst_pos_fill_idx = ft.fill_index
                worst_pos_seed = res.seed
        # Also check tick telemetry in case a synthetic test fixture only populates telemetry
        for tt in res.telemetry:
            if tt.max_abs_position > worst_abs_pos:
                worst_abs_pos = tt.max_abs_position
                worst_signed_pos = tt.position if abs(tt.position) == tt.max_abs_position else tt.max_abs_position
                worst_pos_tick = tt.tick
                worst_pos_fill_idx = None
                worst_pos_seed = res.seed
            if abs(tt.position) > worst_abs_pos:
                worst_abs_pos = abs(tt.position)
                worst_signed_pos = tt.position
                worst_pos_tick = tt.tick
                worst_pos_fill_idx = None
                worst_pos_seed = res.seed

    if worst_abs_pos > pos_limit:
        c2_passed = False
        if worst_pos_fill_idx is not None:
            c2_detail = (
                f"Position reached {worst_signed_pos:+d} lots at tick {worst_pos_tick}, "
                f"fill {worst_pos_fill_idx} on seed {worst_pos_seed} (limit {pos_limit})."
            )
        else:
            c2_detail = (
                f"Position reached {worst_signed_pos:+d} lots at tick {worst_pos_tick} "
                f"on seed {worst_pos_seed} (limit {pos_limit})."
            )
    else:
        c2_detail = f"Max |position| was {worst_abs_pos} lots across all fills (limit {pos_limit})."

    check_c2 = CheckItem(
        id="C2",
        name="Position limit",
        passed=c2_passed,
        value=f"{worst_abs_pos} lots",
        threshold=f"<= {pos_limit} lots",
        detail=c2_detail,
    )

    # --- C3: Order size & valid quotes ---
    c3_passed = True
    max_quoted_size = 0
    c3_detail = f"All quotes valid with max size <= {max_order_size} lots."

    for res in primary_results:
        if res.violations:
            c3_passed = False
            v0 = res.violations[0]
            c3_detail = f"Invalid quote at tick {v0.get('tick', 0)} on seed {res.seed}: {v0.get('reason', 'violation')}."
            break
        for tt in res.telemetry:
            if tt.quote is not None:
                q_max = max(tt.quote.bid_size, tt.quote.ask_size)
                if q_max > max_quoted_size:
                    max_quoted_size = q_max
                if q_max > max_order_size and c3_passed:
                    c3_passed = False
                    c3_detail = (
                        f"Quoted size {q_max} lots at tick {tt.tick} on seed {res.seed} "
                        f"exceeded max_order_size (limit {max_order_size})."
                    )
        if not c3_passed:
            break

    if c3_passed:
        c3_detail = f"Max quoted size was {max_quoted_size} lots with 0 invalid quotes (limit {max_order_size})."

    check_c3 = CheckItem(
        id="C3",
        name="Order size",
        passed=c3_passed,
        value=f"{max_quoted_size} lots" if c3_passed or max_quoted_size > max_order_size else "Invalid quote",
        threshold=f"<= {max_order_size} lots",
        detail=c3_detail,
    )

    # --- C4: Max drawdown ---
    worst_dd = 0
    worst_dd_tick = 0
    worst_dd_seed = primary_results[0].seed if primary_results else 1
    for res in primary_results:
        dd, dd_tick = _compute_drawdown(res)
        if dd > worst_dd:
            worst_dd = dd
            worst_dd_tick = dd_tick
            worst_dd_seed = res.seed

    c4_passed = worst_dd <= max_dd_limit
    if c4_passed:
        c4_detail = f"Max drawdown was {worst_dd} cents on seed {worst_dd_seed} (limit {max_dd_limit} cents)."
    else:
        c4_detail = (
            f"Max drawdown reached {worst_dd} cents at tick {worst_dd_tick} "
            f"on seed {worst_dd_seed} (limit {max_dd_limit} cents)."
        )

    check_c4 = CheckItem(
        id="C4",
        name="Max drawdown",
        passed=c4_passed,
        value=f"{worst_dd}c",
        threshold=f"<= {max_dd_limit}c",
        detail=c4_detail,
    )

    # --- C5: Circuit breaker ---
    # Window: [crash.start + halt_within_ticks, crash.start + crash.duration]
    c5_passed = True
    total_active_in_crash = 0
    c5_detail = "Strategy halted quoting within 5 ticks of crash start across all runs."

    for res in primary_results:
        win_start = res.scenario.start + halt_within
        win_end = min(res.ticks - 1, res.scenario.start + res.scenario.duration)
        active_ticks_in_win: list[int] = []
        for tt in res.telemetry:
            if win_start <= tt.tick <= win_end:
                is_quoting = tt.quote is not None and (tt.quote.bid_size > 0 or tt.quote.ask_size > 0)
                if is_quoting or not tt.halted:
                    active_ticks_in_win.append(tt.tick)
        if active_ticks_in_win:
            total_active_in_crash += len(active_ticks_in_win)
            if c5_passed:
                c5_passed = False
                first_bad = active_ticks_in_win[0]
                c5_detail = (
                    f"Strategy quoted on {len(active_ticks_in_win)} ticks inside crash window "
                    f"[{win_start}, {win_end}] on seed {res.seed}, first at tick {first_bad} (limit 0 active quotes)."
                )

    check_c5 = CheckItem(
        id="C5",
        name="Circuit breaker",
        passed=c5_passed,
        value="0 quotes in crash" if c5_passed else f"{total_active_in_crash} active quotes",
        threshold="0 quotes in crash window",
        detail=c5_detail,
    )

    # --- C6: Validation profit ---
    eval_set = val_results if val_results else primary_results
    profitable_count = 0
    worst_val_pnl: int | None = None
    worst_val_seed: int | None = None
    for res in eval_set:
        pnl = int(res.ledger_summary.get("pnl", res.telemetry[-1].pnl if res.telemetry else 0))
        if pnl > 0 and not res.exception and not res.timed_out:
            profitable_count += 1
        if worst_val_pnl is None or pnl < worst_val_pnl:
            worst_val_pnl = pnl
            worst_val_seed = res.seed

    total_val = len(eval_set)
    c6_passed = total_val > 0 and profitable_count == total_val
    label_set = "final verification" if is_final_verification else "validation"
    if c6_passed:
        c6_detail = f"Positive final PnL on all {profitable_count}/{total_val} {label_set} seeds (min PnL +{worst_val_pnl}c)."
    else:
        c6_detail = (
            f"Final PnL > 0 on only {profitable_count}/{total_val} {label_set} seeds "
            f"(lowest PnL {worst_val_pnl}c on seed {worst_val_seed} at tick {eval_set[0].ticks - 1 if eval_set else 0})."
        )

    check_c6 = CheckItem(
        id="C6",
        name="Validation",
        passed=c6_passed,
        value=f"{profitable_count}/{total_val} profitable",
        threshold=f"{total_val}/{total_val} > 0c",
        detail=c6_detail,
    )

    checks = [check_c1, check_c2, check_c3, check_c4, check_c5, check_c6, check_c7]
    all_passed = all(c.passed for c in checks)

    seed_metrics: list[SeedMetrics] = []
    for res in in_sample_results:
        seed_metrics.append(_compute_seed_metrics(res, "in_sample"))
    val_split_name = "unseen" if is_final_verification else "validation"
    for res in val_results:
        seed_metrics.append(_compute_seed_metrics(res, val_split_name))

    artifact_hash = compute_artifact_hash(code, params or {}, active_limits)

    return CheckReport(
        checks=checks,
        seed_metrics=seed_metrics,
        artifact_hash=artifact_hash,
        all_passed=all_passed,
        validation_passed=c6_passed,
        is_final_verification=is_final_verification,
    )


def make_validation_error_report(
    errors: list[str],
    limits: dict[str, Any] | None = None,
    code: str = "",
    params: dict[str, Any] | None = None,
) -> CheckReport:
    """Create a failed CheckReport when static AST validation rejects generated code."""
    active_limits = dict(limits) if limits is not None else config.get_desk_limits()
    err_summary = "; ".join(errors) if errors else "Static validation failed."
    checks = [
        CheckItem("C1", "Books reconcile to the penny", False, "Not run", "0c diff", f"Skipped due to static validation error: {err_summary}"),
        CheckItem("C2", "Position limit", False, "Not run", f"<= {active_limits.get('position_limit', 100)} lots", f"Skipped due to static validation error: {err_summary}"),
        CheckItem("C3", "Order size", False, "Not run", f"<= {active_limits.get('max_order_size', 10)} lots", f"Skipped due to static validation error: {err_summary}"),
        CheckItem("C4", "Max drawdown", False, "Not run", f"<= {active_limits.get('max_drawdown', 25000)}c", f"Skipped due to static validation error: {err_summary}"),
        CheckItem("C5", "Circuit breaker", False, "Not run", "0 quotes in crash window", f"Skipped due to static validation error: {err_summary}"),
        CheckItem("C6", "Validation", False, "Not run", "All > 0c", f"Skipped due to static validation error: {err_summary}"),
        CheckItem("C7", "Runs cleanly", False, "Validation error", "Valid AST", f"Static validation failed at tick 0: {err_summary}"),
    ]
    return CheckReport(
        checks=checks,
        seed_metrics=[],
        artifact_hash=compute_artifact_hash(code, params or {}, active_limits),
        all_passed=False,
        validation_passed=False,
        is_final_verification=False,
    )
