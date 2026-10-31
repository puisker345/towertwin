"""Red-team scenario synthesis (Flash) and 5x5 parameter robustness sweep (Section 8.3)."""

from __future__ import annotations

import asyncio
from typing import Any

from .. import config
from ..events import event_bus
from ..sandbox.runner import run_sweep_cells_subproc
from ..sim.scenarios import BASELINE_CRASH, Scenario, fit_to_length, validate_and_clamp_scenario
from .gemini_client import gemini_client
from .prompts import RED_TEAM_ANGLES


def build_axis_values(base_val: int | float) -> list[int | float]:
    """Build 5 values around `base_val` over a wide stress range so boundary failure regions appear."""
    if isinstance(base_val, int) and not isinstance(base_val, bool):
        b = max(1, int(base_val))
        return [0, max(1, b - 1), b, b + 2, b + 7]

    fval = float(base_val)
    if abs(fval) < 1e-6:
        return [0.005, 0.05, 0.12, 0.20, 0.36]
    multipliers = (0.1, 0.5, 1.0, 1.5, 3.0)
    vals = [round(fval * m, 4) for m in multipliers]
    vals[2] = round(fval, 4)
    return vals


async def generate_redteam_scenarios(emit_events: bool = True) -> list[Scenario]:
    """Fire 6 concurrent Flash calls for the 6 stress angles, emitting `redteam_scenario` as each arrives."""

    async def _one_angle(idx: int, angle: str) -> Scenario:
        raw_dict = await gemini_client.generate_redteam_scenario(idx, angle)
        clamped = validate_and_clamp_scenario(raw_dict, fallback=bool(raw_dict.get("fallback", False)))
        fitted = fit_to_length(clamped, config.SWEEP_TICKS)
        if emit_events:
            event_bus.emit(
                "redteam_scenario",
                index=idx,
                angle=angle,
                scenario=fitted.to_dict(),
            )
        return fitted

    tasks = [
        asyncio.create_task(_one_angle(idx, angle))
        for idx, angle in enumerate(RED_TEAM_ANGLES)
    ]
    scenarios = await asyncio.gather(*tasks)
    return list(scenarios)


async def run_parameter_sweep(
    code: str,
    base_params: dict[str, Any],
    sweep_params: tuple[str, str],
    redteam_scenarios: list[Scenario],
    limits: dict[str, Any],
    emit_events: bool = True,
) -> dict[str, Any]:
    """Run the 5x5 parameter robustness grid across (baseline + 6 red-team scenarios) in parallel subprocesses."""
    param_x, param_y = sweep_params
    val_x_base = base_params.get(param_x, 4)
    val_y_base = base_params.get(param_y, 0.12)

    x_values = build_axis_values(val_x_base)
    y_values = build_axis_values(val_y_base)

    all_scenarios = [
        fit_to_length(BASELINE_CRASH, config.SWEEP_TICKS),
        *[fit_to_length(sc, config.SWEEP_TICKS) for sc in redteam_scenarios],
    ]
    # Per Section 8.3: use 1 in-sample seed per cell so the 25x7=175 backtest sweep completes in ~2-3s
    sweep_seeds = [config.in_sample_seeds[0]]

    rows_cells: list[list[dict[str, Any]]] = []
    for r_idx, vy in enumerate(y_values):
        row_list: list[dict[str, Any]] = []
        for c_idx, vx in enumerate(x_values):
            row_list.append(
                {
                    "row": r_idx,
                    "col": c_idx,
                    "param_x": param_x,
                    "param_y": param_y,
                    "val_x": vx,
                    "val_y": vy,
                }
            )
        rows_cells.append(row_list)

    grid: list[list[dict[str, Any]]] = [[{} for _ in range(5)] for _ in range(5)]

    async def _run_row(row_Spec: list[dict[str, Any]]) -> list[dict[str, Any]]:
        res = await asyncio.to_thread(
            run_sweep_cells_subproc,
            code,
            row_Spec,
            all_scenarios,
            sweep_seeds,
            config.SWEEP_TICKS,
            limits,
            30.0,
        )
        if emit_events:
            for cell_out in res:
                event_bus.emit("sweep_cell", **cell_out)
        return res

    row_results = await asyncio.gather(*[_run_row(r) for r in rows_cells])
    flat_cells: list[dict[str, Any]] = []
    for r_list in row_results:
        for cell_out in r_list:
            r = int(cell_out["row"])
            c = int(cell_out["col"])
            grid[r][c] = cell_out
            flat_cells.append(cell_out)

    # Choose the most robust cell (preferring the centre loop-verified cell (2,2) if tied or near-top)
    def _robust_score(r: int, c: int) -> float:
        own = float(grid[r][c].get("pass_rate", 0.0))
        neighbors: list[float] = []
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                nr, nc = r + dr, c + dc
                if 0 <= nr < 5 and 0 <= nc < 5:
                    neighbors.append(float(grid[nr][nc].get("pass_rate", 0.0)))
        nb_avg = sum(neighbors) / len(neighbors) if neighbors else own
        return 0.7 * own + 0.3 * nb_avg

    best_r, best_c = 2, 2
    best_score = _robust_score(2, 2)
    center_pass_rate = float(grid[2][2].get("pass_rate", 0.0)) if grid[2][2] else 0.0

    for r in range(5):
        for c in range(5):
            sc = _robust_score(r, c)
            # Only move away from centre (2,2) if another interior region is strictly more robust
            if sc > best_score + 0.05:
                best_score = sc
                best_r, best_c = r, c

    chosen_cell = grid[best_r][best_c] if grid[best_r][best_c] else {
        "row": 2,
        "col": 2,
        "val_x": val_x_base,
        "val_y": val_y_base,
        "pass_rate": center_pass_rate,
    }
    best_params = dict(base_params)
    best_params[param_x] = chosen_cell["val_x"]
    best_params[param_y] = chosen_cell["val_y"]

    scenario_totals: dict[str, list[float]] = {sc.name: [] for sc in all_scenarios}
    for cell_out in flat_cells:
        for sc_name, rate in (cell_out.get("scenario_rates") or {}).items():
            if sc_name in scenario_totals:
                scenario_totals[sc_name].append(float(rate))

    scenario_summary = [
        {
            "name": sc.name,
            "mean_pass_rate": round(
                sum(scenario_totals[sc.name]) / len(scenario_totals[sc.name]), 4
            )
            if scenario_totals[sc.name]
            else 0.0,
        }
        for sc in all_scenarios
    ]

    summary_payload = {
        "sweep_params": [param_x, param_y],
        "x_values": x_values,
        "y_values": y_values,
        "grid": grid,
        "best_cell": {"row": best_r, "col": best_c, "pass_rate": chosen_cell.get("pass_rate", 0.0)},
        "best_params": best_params,
        "scenario_summary": scenario_summary,
    }
    if emit_events:
        event_bus.emit("sweep_done", **summary_payload)
    return summary_payload
