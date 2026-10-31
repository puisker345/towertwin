"""Subprocess harness that executes generated strategies in a restricted namespace (Section 10.1)."""

from __future__ import annotations

import ast
import builtins
import collections
import json
import math
import statistics
import sys
from pathlib import Path
from typing import Any

# Ensure towertwin package is importable when invoked as `python -m towertwin.sandbox.harness`
_ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(_ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(_ROOT_DIR))

from towertwin import config  # noqa: E402
from towertwin.checks.checks import evaluate_checks  # noqa: E402
from towertwin.sandbox.validate import ALLOWED_MODULES, validate  # noqa: E402
from towertwin.sim.market import BacktestResult, run_backtest  # noqa: E402
from towertwin.sim.scenarios import BASELINE_CRASH, Scenario, fit_to_length  # noqa: E402
from towertwin.sim.types import Fill, MarketState, Quote  # noqa: E402

RESTRICTED_BUILTINS: dict[str, Any] = {
    "__build_class__": builtins.__build_class__,
    "abs": abs,
    "min": min,
    "max": max,
    "round": round,
    "int": int,
    "float": float,
    "len": len,
    "range": range,
    "reversed": reversed,
    "sum": sum,
    "sorted": sorted,
    "enumerate": enumerate,
    "zip": zip,
    "list": list,
    "dict": dict,
    "tuple": tuple,
    "set": set,
    "bool": bool,
    "isinstance": isinstance,
    "any": any,
    "all": all,
    "pow": pow,
    "divmod": divmod,
    "object": object,
    "Exception": Exception,
    "ValueError": ValueError,
    "ZeroDivisionError": ZeroDivisionError,
    "True": True,
    "False": False,
    "None": None,
}

_PREINJECTED_MODULES: dict[str, Any] = {
    "math": math,
    "statistics": statistics,
    "collections": collections,
}


def apply_resource_limits(cpu_seconds: int = 20, memory_mb: int = 512) -> None:
    """Apply OS-level CPU and memory resource limits where supported (POSIX)."""
    try:
        import resource  # type: ignore[import-not-found]

        if hasattr(resource, "RLIMIT_CPU"):
            resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
        if hasattr(resource, "RLIMIT_AS"):
            mem_bytes = memory_mb * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (mem_bytes, mem_bytes))
    except (ImportError, ValueError, OSError):
        # Windows or restricted container where setrlimit is unavailable
        pass


class _ImportStripper(ast.NodeTransformer):
    """Strip validated import statements and record alias bindings against pre-injected modules."""

    def __init__(self) -> None:
        super().__init__()
        self.extra_bindings: dict[str, Any] = {}

    def visit_Import(self, node: ast.Import) -> ast.AST | None:
        for alias in node.names:
            if alias.name in _PREINJECTED_MODULES:
                target_name = alias.asname or alias.name
                self.extra_bindings[target_name] = _PREINJECTED_MODULES[alias.name]
            else:
                raise ImportError(f"Forbidden import: {alias.name}")
        return ast.Pass()

    def visit_ImportFrom(self, node: ast.ImportFrom) -> ast.AST | None:
        if node.level > 0 or not node.module or node.module not in _PREINJECTED_MODULES:
            raise ImportError(f"Forbidden from-import: {node.module}")
        mod_obj = _PREINJECTED_MODULES[node.module]
        for alias in node.names:
            if alias.name.startswith("_") or not hasattr(mod_obj, alias.name):
                raise ImportError(f"Forbidden attribute {alias.name!r} from {node.module}")
            target_name = alias.asname or alias.name
            self.extra_bindings[target_name] = getattr(mod_obj, alias.name)
        return ast.Pass()


def load_strategy_namespace(code: str) -> dict[str, Any]:
    """Validate and execute strategy code in the restricted namespace (Section 10.1).

    Raises ValueError if static AST validation fails.
    """
    val = validate(code)
    if not val.ok:
        raise ValueError("; ".join(val.errors))

    tree = ast.parse(code, filename="strategy.py")
    stripper = _ImportStripper()
    transformed = ast.fix_missing_locations(stripper.visit(tree))
    compiled = compile(transformed, filename="strategy.py", mode="exec")

    namespace: dict[str, Any] = {
        "__name__": "strategy",
        "__builtins__": dict(RESTRICTED_BUILTINS),
        "Quote": Quote,
        "math": math,
        "statistics": statistics,
        "collections": collections,
    }
    namespace.update(stripper.extra_bindings)

    exec(compiled, namespace, namespace)  # noqa: S102
    if "Strategy" not in namespace:
        raise ValueError("Strategy class not found after execution.")
    return namespace


def execute_batch_request(payload: dict[str, Any]) -> dict[str, Any]:
    """Execute a batch of backtests inside the harness and return JSON-serializable results."""
    code: str = payload["code"]
    seeds: list[int] = [int(s) for s in payload.get("seeds", config.in_sample_seeds)]
    ticks: int = int(payload.get("ticks", config.TICKS))
    limits: dict[str, Any] = dict(payload.get("limits") or config.get_desk_limits())
    params_override: dict[str, Any] | None = payload.get("params_override")
    raw_scenario = payload.get("scenario")
    scenario = Scenario.from_dict(raw_scenario) if isinstance(raw_scenario, dict) else BASELINE_CRASH
    full_telemetry_seeds_raw = payload.get("full_telemetry_seeds")
    full_telemetry_seeds: set[int] | None = (
        {int(s) for s in full_telemetry_seeds_raw} if full_telemetry_seeds_raw is not None else None
    )

    try:
        ns = load_strategy_namespace(code)
    except Exception as exc:  # noqa: BLE001
        err_msg = f"{type(exc).__name__}: {exc}"
        failed_results = [
            BacktestResult(
                seed=s,
                ticks=ticks,
                scenario=scenario,
                limits=limits,
                exception=err_msg,
            ).to_serializable(include_full_telemetry=True)
            for s in seeds
        ]
        return {
            "ok": False,
            "error": err_msg,
            "params": {},
            "sweep_params": None,
            "results": failed_results,
        }

    base_params = dict(ns.get("PARAMS", {}))
    if params_override:
        base_params.update(params_override)
    sweep_params_raw = ns.get("SWEEP_PARAMS")
    sweep_params = list(sweep_params_raw) if isinstance(sweep_params_raw, (list, tuple)) else None
    strategy_cls = ns["Strategy"]

    results_out: list[dict[str, Any]] = []
    for seed in seeds:
        try:
            strategy_instance = strategy_cls(dict(base_params), dict(limits))
            bt = run_backtest(
                strategy=strategy_instance,
                seed=seed,
                scenario=scenario,
                ticks=ticks,
                limits=limits,
            )
        except Exception as exc:  # noqa: BLE001
            bt = BacktestResult(
                seed=seed,
                ticks=ticks,
                scenario=scenario,
                limits=limits,
                exception=f"{type(exc).__name__}: {exc}",
            )
        include_full = True if full_telemetry_seeds is None else (seed in full_telemetry_seeds)
        results_out.append(bt.to_serializable(include_full_telemetry=include_full))

    return {
        "ok": True,
        "error": None,
        "params": base_params,
        "sweep_params": sweep_params,
        "results": results_out,
    }


def execute_sweep_cells_request(payload: dict[str, Any]) -> dict[str, Any]:
    """Execute a list of parameter grid cells across scenarios x in-sample seeds (Section 8.3)."""
    code: str = payload["code"]
    cells: list[dict[str, Any]] = payload["cells"]
    raw_scenarios: list[dict[str, Any]] = payload["scenarios"]
    seeds: list[int] = [int(s) for s in payload.get("seeds", config.in_sample_seeds)]
    ticks: int = int(payload.get("ticks", config.SWEEP_TICKS))
    limits: dict[str, Any] = dict(payload.get("limits") or config.get_desk_limits())

    try:
        ns = load_strategy_namespace(code)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "cells": []}

    base_params = dict(ns.get("PARAMS", {}))
    strategy_cls = ns["Strategy"]
    scenarios = [fit_to_length(Scenario.from_dict(sc), ticks) for sc in raw_scenarios]

    # Assert crash window is inside every run (Section 8.3 step 3)
    for sc in scenarios:
        assert sc.start >= 200 and sc.start + sc.duration + 300 <= ticks

    cell_results: list[dict[str, Any]] = []
    target_check_ids = {"C2", "C3", "C4", "C5", "C7"}

    for cell in cells:
        row = int(cell["row"])
        col = int(cell["col"])
        param_x = str(cell["param_x"])
        param_y = str(cell["param_y"])
        val_x = cell["val_x"]
        val_y = cell["val_y"]

        cell_params = dict(base_params)
        cell_params[param_x] = val_x
        cell_params[param_y] = val_y

        passed_runs = 0
        total_runs = 0
        total_pnl = 0
        scenario_rates: dict[str, float] = {}

        for sc in scenarios:
            sc_passed = 0
            sc_total = 0
            for seed in seeds:
                total_runs += 1
                sc_total += 1
                try:
                    strat = strategy_cls(dict(cell_params), dict(limits))
                    bt = run_backtest(
                        strategy=strat,
                        seed=seed,
                        scenario=sc,
                        ticks=ticks,
                        limits=limits,
                    )
                    rep = evaluate_checks([bt], limits=limits, code=code, params=cell_params)
                    run_ok = all(c.passed for c in rep.checks if c.id in target_check_ids)
                    pnl = int(bt.ledger_summary.get("pnl", 0))
                except Exception:  # noqa: BLE001
                    run_ok = False
                    pnl = 0
                total_pnl += pnl
                if run_ok:
                    passed_runs += 1
                    sc_passed += 1
            scenario_rates[sc.name] = round(sc_passed / sc_total, 4) if sc_total > 0 else 0.0

        pass_rate = round(passed_runs / total_runs, 4) if total_runs > 0 else 0.0
        avg_pnl = int(round(total_pnl / total_runs)) if total_runs > 0 else 0
        cell_results.append(
            {
                "row": row,
                "col": col,
                "param_x": param_x,
                "param_y": param_y,
                "val_x": val_x,
                "val_y": val_y,
                "pass_rate": pass_rate,
                "passed_runs": passed_runs,
                "total_runs": total_runs,
                "avg_pnl": avg_pnl,
                "scenario_rates": scenario_rates,
            }
        )

    return {"ok": True, "error": None, "cells": cell_results}


def run_live_worker() -> int:
    """Persistent line-delimited JSON worker for `live/engine.py` so live strategy code
    never runs in the server process.
    """
    apply_resource_limits(cpu_seconds=3600, memory_mb=512)
    strategy_instance: Any = None

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
            cmd = msg.get("cmd")
            if cmd == "init":
                code = msg["code"]
                params = dict(msg.get("params") or {})
                limits = dict(msg.get("limits") or config.get_desk_limits())
                ns = load_strategy_namespace(code)
                eff_params = dict(ns.get("PARAMS", {}))
                eff_params.update(params)
                strategy_instance = ns["Strategy"](eff_params, limits)
                sys.stdout.write(json.dumps({"ok": True, "params": eff_params}) + "\n")
                sys.stdout.flush()
            elif cmd == "step":
                if strategy_instance is None:
                    sys.stdout.write(json.dumps({"ok": False, "error": "Not initialized"}) + "\n")
                    sys.stdout.flush()
                    continue
                for raw_fill in msg.get("fills", []):
                    fill_obj = Fill(
                        tick=int(raw_fill["tick"]),
                        side=str(raw_fill["side"]),
                        price=int(raw_fill["price"]),
                        size=int(raw_fill["size"]),
                        fee=int(raw_fill["fee"]),
                    )
                    strategy_instance.on_fill(fill_obj)
                raw_state = msg["state"]
                state_obj = MarketState(
                    tick=int(raw_state["tick"]),
                    mid=int(raw_state["mid"]),
                    recent_mids=tuple(int(x) for x in raw_state["recent_mids"]),
                    position=int(raw_state["position"]),
                    recent_trades=tuple(
                        (int(t[0]), str(t[1]), int(t[2])) for t in raw_state["recent_trades"]
                    ),
                    limits=dict(raw_state["limits"]),
                )
                q = strategy_instance.on_tick(state_obj)
                if isinstance(q, Quote):
                    q_dict = {
                        "bid_price": q.bid_price,
                        "bid_size": int(q.bid_size),
                        "ask_price": q.ask_price,
                        "ask_size": int(q.ask_size),
                    }
                else:
                    q_dict = {"bid_price": None, "bid_size": 0, "ask_price": None, "ask_size": 0}
                sys.stdout.write(json.dumps({"ok": True, "quote": q_dict}) + "\n")
                sys.stdout.flush()
            elif cmd == "stop":
                sys.stdout.write(json.dumps({"ok": True}) + "\n")
                sys.stdout.flush()
                return 0
        except Exception as exc:  # noqa: BLE001
            sys.stdout.write(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}) + "\n")
            sys.stdout.flush()
    return 0


def main() -> int:
    if "--live" in sys.argv:
        return run_live_worker()

    apply_resource_limits(cpu_seconds=20, memory_mb=512)
    raw_stdin = sys.stdin.read()
    if not raw_stdin.strip():
        sys.stdout.write(json.dumps({"ok": False, "error": "Empty stdin payload", "results": []}))
        return 1
    try:
        payload = json.loads(raw_stdin)
        if payload.get("mode") == "sweep_cells":
            response = execute_sweep_cells_request(payload)
        else:
            response = execute_batch_request(payload)
        sys.stdout.write(json.dumps(response))
        sys.stdout.flush()
        return 0
    except Exception as exc:  # noqa: BLE001
        sys.stdout.write(
            json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}", "results": []})
        )
        sys.stdout.flush()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
