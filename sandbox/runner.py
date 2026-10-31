"""Subprocess runner for isolated strategy backtesting, sweeps, and live ticks (Section 10.1)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from .. import config
from ..sim.market import BacktestResult
from ..sim.scenarios import BASELINE_CRASH, Scenario, fit_to_length
from ..sim.types import Fill, MarketState, Quote

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _scrubbed_child_env() -> dict[str, str]:
    """Scrub sensitive environment variables so keys never enter the sandbox child."""
    child_env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("GEMINI_API_KEY", "GOOGLE_API_KEY")
    }
    existing_py_path = child_env.get("PYTHONPATH", "")
    child_env["PYTHONPATH"] = (
        f"{_PROJECT_ROOT}{os.pathsep}{existing_py_path}" if existing_py_path else str(_PROJECT_ROOT)
    )
    return child_env


def _make_timeout_results(
    seeds: list[int],
    ticks: int,
    scenario: Scenario,
    limits: dict[str, Any],
    elapsed: float,
    reason: str,
) -> list[BacktestResult]:
    fitted = fit_to_length(scenario, ticks)
    return [
        BacktestResult(
            seed=int(s),
            ticks=ticks,
            scenario=fitted,
            limits=dict(limits),
            runtime_sec=elapsed,
            exception=reason,
            timed_out=True,
        )
        for s in seeds
    ]


def run_backtests_batch(
    code: str,
    scenario: Scenario = BASELINE_CRASH,
    seeds: list[int] | None = None,
    ticks: int = config.TICKS,
    limits: dict[str, Any] | None = None,
    params_override: dict[str, Any] | None = None,
    timeout_sec: float = 30.0,
    full_telemetry_seeds: list[int] | None = None,
) -> tuple[list[BacktestResult], dict[str, Any], tuple[str, str] | None]:
    """Run a batch of backtests in an isolated child subprocess (`python -m towertwin.sandbox.harness`).

    Returns `(results, effective_params, sweep_params)`.
    If the child times out or crashes, kills it and returns BacktestResult objects with
    `timed_out=True` / `exception` set so C7 reports failure.
    """
    active_seeds = list(seeds) if seeds is not None else list(config.in_sample_seeds)
    active_limits = dict(limits) if limits is not None else config.get_desk_limits()
    fitted = fit_to_length(scenario, ticks)

    payload = {
        "mode": "batch",
        "code": code,
        "scenario": fitted.to_dict(),
        "seeds": active_seeds,
        "ticks": int(ticks),
        "limits": active_limits,
        "params_override": params_override,
        "full_telemetry_seeds": full_telemetry_seeds,
    }
    stdin_bytes = json.dumps(payload).encode("utf-8")
    child_env = _scrubbed_child_env()

    t0 = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="towertwin_sbx_") as tmp_cwd:
        proc = subprocess.Popen(
            [sys.executable, "-m", "towertwin.sandbox.harness"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=tmp_cwd,
            env=child_env,
        )
        try:
            stdout_bytes, stderr_bytes = proc.communicate(input=stdin_bytes, timeout=timeout_sec)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.communicate(timeout=2.0)
            except Exception:  # noqa: BLE001
                pass
            elapsed = time.perf_counter() - t0
            return (
                _make_timeout_results(
                    active_seeds,
                    ticks,
                    fitted,
                    active_limits,
                    elapsed,
                    f"TimeoutExpired: Backtest subprocess exceeded {timeout_sec:.1f}s wall-clock limit.",
                ),
                dict(params_override or {}),
                None,
            )

    elapsed = time.perf_counter() - t0
    if not stdout_bytes:
        stderr_txt = stderr_bytes.decode("utf-8", errors="replace").strip()
        err_msg = f"Subprocess exited with code {proc.returncode}: {stderr_txt or 'empty stdout'}"
        return (
            [
                BacktestResult(
                    seed=int(s),
                    ticks=ticks,
                    scenario=fitted,
                    limits=active_limits,
                    runtime_sec=elapsed,
                    exception=err_msg,
                    timed_out=False,
                )
                for s in active_seeds
            ],
            dict(params_override or {}),
            None,
        )

    try:
        data = json.loads(stdout_bytes.decode("utf-8"))
    except json.JSONDecodeError as exc:
        return (
            [
                BacktestResult(
                    seed=int(s),
                    ticks=ticks,
                    scenario=fitted,
                    limits=active_limits,
                    runtime_sec=elapsed,
                    exception=f"Corrupt subprocess JSON output: {exc}",
                    timed_out=False,
                )
                for s in active_seeds
            ],
            dict(params_override or {}),
            None,
        )

    raw_results = data.get("results", [])
    results = [BacktestResult.from_serializable(item) for item in raw_results]
    params = dict(data.get("params") or {})
    raw_sp = data.get("sweep_params")
    sweep_params = (str(raw_sp[0]), str(raw_sp[1])) if isinstance(raw_sp, list) and len(raw_sp) == 2 else None
    return results, params, sweep_params


def run_backtests(
    code: str,
    scenario: Scenario = BASELINE_CRASH,
    seeds: list[int] | None = None,
    ticks: int = config.TICKS,
    limits: dict[str, Any] | None = None,
    params_override: dict[str, Any] | None = None,
    timeout_sec: float = 30.0,
) -> list[BacktestResult]:
    """Convenience wrapper returning the list of BacktestResult objects from subprocess runner."""
    results, _, _ = run_backtests_batch(
        code=code,
        scenario=scenario,
        seeds=seeds,
        ticks=ticks,
        limits=limits,
        params_override=params_override,
        timeout_sec=timeout_sec,
    )
    return results


def run_sweep_cells_subproc(
    code: str,
    cells: list[dict[str, Any]],
    scenarios: list[Scenario],
    seeds: list[int] | None = None,
    ticks: int = config.SWEEP_TICKS,
    limits: dict[str, Any] | None = None,
    timeout_sec: float = 30.0,
) -> list[dict[str, Any]]:
    """Run one or more parameter sweep cells across scenarios x in-sample seeds in a child subprocess."""
    active_seeds = list(seeds) if seeds is not None else list(config.in_sample_seeds)
    active_limits = dict(limits) if limits is not None else config.get_desk_limits()
    fitted_scenarios = [fit_to_length(sc, ticks).to_dict() for sc in scenarios]

    payload = {
        "mode": "sweep_cells",
        "code": code,
        "cells": cells,
        "scenarios": fitted_scenarios,
        "seeds": active_seeds,
        "ticks": int(ticks),
        "limits": active_limits,
    }
    stdin_bytes = json.dumps(payload).encode("utf-8")
    child_env = _scrubbed_child_env()

    with tempfile.TemporaryDirectory(prefix="towertwin_swp_") as tmp_cwd:
        proc = subprocess.Popen(
            [sys.executable, "-m", "towertwin.sandbox.harness"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=tmp_cwd,
            env=child_env,
        )
        try:
            stdout_bytes, _ = proc.communicate(input=stdin_bytes, timeout=timeout_sec)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.communicate(timeout=2.0)
            except Exception:  # noqa: BLE001
                pass
            return [
                {
                    "row": int(c["row"]),
                    "col": int(c["col"]),
                    "param_x": str(c["param_x"]),
                    "param_y": str(c["param_y"]),
                    "val_x": c["val_x"],
                    "val_y": c["val_y"],
                    "pass_rate": 0.0,
                    "passed_runs": 0,
                    "total_runs": len(scenarios) * len(active_seeds),
                    "avg_pnl": 0,
                    "scenario_rates": {sc.name: 0.0 for sc in scenarios},
                }
                for c in cells
            ]

    if not stdout_bytes:
        return []
    try:
        data = json.loads(stdout_bytes.decode("utf-8"))
        return list(data.get("cells") or [])
    except Exception:  # noqa: BLE001
        return []


class LiveStrategySubprocess:
    """Manages a persistent isolated child subprocess for executing live strategy ticks."""

    def __init__(self, code: str, params: dict[str, Any], limits: dict[str, Any]) -> None:
        self.code = code
        self.params = dict(params)
        self.limits = dict(limits)
        self._tmp_dir = tempfile.TemporaryDirectory(prefix="towertwin_live_", ignore_cleanup_errors=True)
        self._proc = subprocess.Popen(
            [sys.executable, "-m", "towertwin.sandbox.harness", "--live"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=self._tmp_dir.name,
            env=_scrubbed_child_env(),
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        init_msg = json.dumps({"cmd": "init", "code": code, "params": params, "limits": limits})
        assert self._proc.stdin is not None and self._proc.stdout is not None
        self._proc.stdin.write(init_msg + "\n")
        self._proc.stdin.flush()
        resp_line = self._proc.stdout.readline()
        resp = json.loads(resp_line) if resp_line else {"ok": False, "error": "Worker failed to start"}
        if not resp.get("ok"):
            self.close()
            raise RuntimeError(f"Live strategy worker failed to initialize: {resp.get('error')}")
        if isinstance(resp.get("params"), dict):
            self.params = dict(resp["params"])

    def step(self, state: MarketState, new_fills: list[Fill]) -> Quote:
        if self._proc.poll() is not None or self._proc.stdin is None or self._proc.stdout is None:
            return Quote(None, 0, None, 0)
        msg = {
            "cmd": "step",
            "fills": [
                {
                    "tick": f.tick,
                    "side": f.side,
                    "price": f.price,
                    "size": f.size,
                    "fee": f.fee,
                }
                for f in new_fills
            ],
            "state": {
                "tick": state.tick,
                "mid": state.mid,
                "recent_mids": list(state.recent_mids),
                "position": state.position,
                "recent_trades": [list(t) for t in state.recent_trades],
                "limits": dict(state.limits),
            },
        }
        try:
            self._proc.stdin.write(json.dumps(msg) + "\n")
            self._proc.stdin.flush()
            resp_line = self._proc.stdout.readline()
            if not resp_line:
                return Quote(None, 0, None, 0)
            resp = json.loads(resp_line)
            if not resp.get("ok"):
                return Quote(None, 0, None, 0)
            q = resp["quote"]
            return Quote(
                bid_price=int(q["bid_price"]) if q.get("bid_price") is not None else None,
                bid_size=int(q.get("bid_size", 0)),
                ask_price=int(q["ask_price"]) if q.get("ask_price") is not None else None,
                ask_size=int(q.get("ask_size", 0)),
            )
        except Exception:  # noqa: BLE001
            return Quote(None, 0, None, 0)

    def close(self) -> None:
        try:
            if self._proc.poll() is None and self._proc.stdin is not None:
                self._proc.stdin.write(json.dumps({"cmd": "stop"}) + "\n")
                self._proc.stdin.flush()
                self._proc.terminate()
                self._proc.wait(timeout=1.0)
        except Exception:  # noqa: BLE001
            try:
                self._proc.kill()
            except Exception:  # noqa: BLE001
                pass
        finally:
            try:
                self._tmp_dir.cleanup()
            except Exception:  # noqa: BLE001
                pass
