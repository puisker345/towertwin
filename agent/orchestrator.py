"""Agent orchestrator loop for Repair and Scratch modes (Section 8.1)."""

from __future__ import annotations

import asyncio
import difflib
from pathlib import Path
from typing import Any

from .. import config
from ..checks.checks import CheckReport, evaluate_checks, make_validation_error_report
from ..events import event_bus
from ..sandbox.runner import run_backtests_batch
from ..sandbox.validate import validate
from ..sim.scenarios import BASELINE_CRASH
from .briefing import briefing_store
from .final_verify import VerifiedArtifact, run_final_verification
from .gemini_client import gemini_client
from .redteam import generate_redteam_scenarios, run_parameter_sweep

_MOCKS_DIR = Path(__file__).resolve().parent.parent / "mocks"


def compute_unified_diff(old_code: str | None, new_code: str) -> str:
    """Compute a clean unified diff between `old_code` and `new_code`."""
    if not old_code:
        return ""
    old_lines = old_code.splitlines(keepends=True)
    new_lines = new_code.splitlines(keepends=True)
    diff = difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile="previous_strategy.py",
        tofile="candidate_strategy.py",
        n=2,
    )
    return "".join(diff)


def _downsample_replay(result_dict: dict[str, Any], step: int = 10) -> list[dict[str, Any]]:
    """Downsample tick telemetry for lightweight SSE replay in the 3D/2D view."""
    telemetry = result_dict.get("telemetry") or []
    out: list[dict[str, Any]] = []
    for idx, item in enumerate(telemetry):
        # Keep every `step`-th tick plus all ticks inside the crash window or with fills
        if idx % step == 0 or item.get("halted") or item.get("fills") or idx == len(telemetry) - 1:
            out.append(item)
    return out


def _evaluate_candidate_sync(
    code: str,
    limits: dict[str, Any],
    ticks: int = config.TICKS,
) -> tuple[CheckReport, dict[str, Any], tuple[str, str] | None, list[dict[str, Any]]]:
    """Run candidate strategy on `in_sample_seeds + validation_seeds` in a child subprocess."""
    in_seeds = list(config.in_sample_seeds)
    val_seeds = list(config.validation_seeds)
    all_seeds = in_seeds + val_seeds

    results, eff_params, sweep_params = run_backtests_batch(
        code=code,
        scenario=BASELINE_CRASH,
        seeds=all_seeds,
        ticks=ticks,
        limits=limits,
        full_telemetry_seeds=[in_seeds[0]] if in_seeds else None,
    )
    in_count = len(in_seeds)
    in_results = results[:in_count]
    val_results = results[in_count:]

    report = evaluate_checks(
        in_sample_results=in_results,
        validation_results=val_results,
        limits=limits,
        code=code,
        params=eff_params,
        is_final_verification=False,
    )
    replay_points: list[dict[str, Any]] = []
    if in_results:
        first_serial = in_results[0].to_serializable(include_full_telemetry=True)
        replay_points = _downsample_replay(first_serial, step=10)

    return report, eff_params, sweep_params, replay_points


class Orchestrator:
    """Manages the repair / scratch verification loop, sweep, review, and final verification."""

    def __init__(self) -> None:
        self.running: bool = False
        self.current_task: asyncio.Task[VerifiedArtifact | None] | None = None
        self.latest_verified_artifact: VerifiedArtifact | None = None

    async def run(
        self,
        mode: str = "repair",
        limits: dict[str, Any] | None = None,
        starting_code: str | None = None,
        on_before_signoff: Any = None,
    ) -> VerifiedArtifact | None:
        """Run the full Section 8 verification pipeline and return the `VerifiedArtifact` if passed."""
        self.running = True
        try:
            active_mode = mode if mode in ("repair", "scratch") else "repair"
            if limits is not None:
                active_limits = dict(limits)
            elif active_mode == "repair":
                active_limits = config.get_tightened_desk_limits()
            else:
                active_limits = config.get_desk_limits()

            event_bus.emit(
                "mock_mode",
                mock_llm=gemini_client.is_mock,
                mode=active_mode,
                limits=active_limits,
            )

            # Step 1 (Section 8.1 & 8.3): Start the 6 red-team Flash calls immediately in parallel
            redteam_task = asyncio.create_task(generate_redteam_scenarios(emit_events=True))

            previous_code: str | None = None
            failure_report: dict[str, str] | None = None
            previous_interaction_id: str | None = None

            best_code: str = ""
            best_params: dict[str, Any] = {}
            best_sweep_params: tuple[str, str] | None = None
            best_report: CheckReport | None = None
            loop_succeeded = False
            winning_iteration = 0

            if active_mode == "repair":
                # Step 2 (Section 8.1): Load current desk strategy and emit iteration_proposed(0)
                desk_code = (
                    starting_code
                    if starting_code
                    else (_MOCKS_DIR / "strategy_v3_desk.py").read_text(encoding="utf-8")
                )
                val0 = validate(desk_code)
                event_bus.emit(
                    "iteration_proposed",
                    iteration=0,
                    label="Current desk strategy",
                    rationale="Re-evaluating current deployed desk strategy under updated desk limits.",
                    code=desk_code,
                    diff="",
                    params=val0.params,
                )

                # Step 3 (Section 8.1): Re-check it under the new desk rules (sandbox + checks, no model call)
                report0, params0, sweep0, replay0 = await asyncio.to_thread(
                    _evaluate_candidate_sync,
                    desk_code,
                    active_limits,
                    config.TICKS,
                )
                if replay0:
                    event_bus.emit(
                        "replay",
                        iteration=0,
                        seed=config.in_sample_seeds[0],
                        telemetry=replay0,
                        scenario=BASELINE_CRASH.to_dict(),
                    )
                event_bus.emit(
                    "checks",
                    iteration=0,
                    report=report0.to_dict(),
                    checks=[c.to_dict() for c in report0.checks],
                    seed_metrics=[m.to_dict() for m in report0.seed_metrics],
                    all_passed=report0.all_passed,
                    passed_count=sum(1 for c in report0.checks if c.passed),
                    total_count=len(report0.checks),
                    artifact_hash=report0.artifact_hash,
                )

                best_code = desk_code
                best_params = params0
                best_sweep_params = sweep0
                best_report = report0

                if report0.all_passed:
                    loop_succeeded = True
                    winning_iteration = 0
                else:
                    previous_code = desk_code
                    failure_report = report0.for_prompt()

            # Step 5 (Section 8.1): Iterative Argon propose -> validate -> sandbox -> check -> refine loop
            if not loop_succeeded:
                for iteration in range(1, config.MAX_ITERATIONS + 1):
                    event_bus.emit("loop_stage", stage="propose", iteration=iteration)

                    code, rationale, previous_interaction_id = await gemini_client.propose_strategy(
                        mode=active_mode,
                        iteration=iteration,
                        limits=active_limits,
                        previous_code=previous_code,
                        failure_report=failure_report,
                        previous_interaction_id=previous_interaction_id,
                    )

                    val = validate(code)
                    diff_text = compute_unified_diff(previous_code, code)
                    iter_label = (
                        "Iteration 1: naive baseline"
                        if (active_mode == "scratch" and iteration == 1)
                        else f"Iteration {iteration}"
                    )
                    event_bus.emit(
                        "iteration_proposed",
                        iteration=iteration,
                        label=iter_label,
                        rationale=rationale,
                        code=code,
                        diff=diff_text,
                        params=val.params,
                    )

                    if not val.ok:
                        err_report = make_validation_error_report(
                            val.errors,
                            limits=active_limits,
                            code=code,
                            params=val.params,
                        )
                        event_bus.emit(
                            "checks",
                            iteration=iteration,
                            report=err_report.to_dict(),
                            checks=[c.to_dict() for c in err_report.checks],
                            seed_metrics=[],
                            all_passed=False,
                            passed_count=0,
                            total_count=len(err_report.checks),
                            artifact_hash=err_report.artifact_hash,
                        )
                        event_bus.emit("loop_stage", stage="refine", iteration=iteration)
                        previous_code = code
                        failure_report = err_report.for_prompt()
                        continue

                    event_bus.emit("loop_stage", stage="sandbox", iteration=iteration)
                    report, eff_params, sweep_params, replay_pts = await asyncio.to_thread(
                        _evaluate_candidate_sync,
                        code,
                        active_limits,
                        config.TICKS,
                    )
                    if replay_pts:
                        event_bus.emit(
                            "replay",
                            iteration=iteration,
                            seed=config.in_sample_seeds[0],
                            telemetry=replay_pts,
                            scenario=BASELINE_CRASH.to_dict(),
                        )

                    event_bus.emit("loop_stage", stage="check", iteration=iteration)
                    event_bus.emit(
                        "checks",
                        iteration=iteration,
                        report=report.to_dict(),
                        checks=[c.to_dict() for c in report.checks],
                        seed_metrics=[m.to_dict() for m in report.seed_metrics],
                        all_passed=report.all_passed,
                        passed_count=sum(1 for c in report.checks if c.passed),
                        total_count=len(report.checks),
                        artifact_hash=report.artifact_hash,
                    )

                    if best_report is None or sum(1 for c in report.checks if c.passed) >= sum(
                        1 for c in best_report.checks if c.passed
                    ):
                        best_code = code
                        best_params = eff_params
                        best_sweep_params = sweep_params
                        best_report = report

                    if report.all_passed:
                        event_bus.emit("loop_stage", stage="passed", iteration=iteration)
                        loop_succeeded = True
                        winning_iteration = iteration
                        break

                    event_bus.emit("loop_stage", stage="refine", iteration=iteration)
                    previous_code = code
                    failure_report = report.for_prompt()

            # Ensure all 6 red-team scenarios have arrived before emitting loop_passed / loop_failed
            redteam_scenarios = await redteam_task

            if not loop_succeeded or best_report is None:
                event_bus.emit(
                    "loop_failed",
                    iteration=config.MAX_ITERATIONS,
                    code=best_code,
                    params=best_params,
                    report=best_report.to_dict() if best_report else None,
                )
                return None

            event_bus.emit(
                "loop_passed",
                iteration=winning_iteration,
                code=best_code,
                params=best_params,
                artifact_hash=best_report.artifact_hash,
            )

            # Step 6 (Section 8.3): Run 5x5 parameter robustness sweep across (baseline + 6 red-team scenarios)
            active_sweep_params = best_sweep_params or ("half_spread_ticks", "skew_per_lot")
            sweep_summary = await run_parameter_sweep(
                code=best_code,
                base_params=best_params,
                sweep_params=active_sweep_params,
                redteam_scenarios=redteam_scenarios,
                limits=active_limits,
                emit_events=True,
            )

            # Step 7 (Section 8.2 & 8.3): Argon REVIEW step
            review_out = await gemini_client.generate_review(
                code=best_code,
                loop_params=best_params,
                check_report_summary=best_report.in_sample_metrics_text(),
                sweep_summary=str(sweep_summary.get("scenario_summary", [])),
                best_sweep_params=sweep_summary.get("best_params"),
            )
            review_params = review_out.get("final_params") or best_params
            report_md = str(review_out.get("report_md") or "")
            briefing_text = str(review_out.get("briefing_text") or "")

            # Prepare audio briefing asynchronously while final verification runs
            briefing_task = asyncio.create_task(briefing_store.generate(briefing_text))

            def _register_artifact(art: VerifiedArtifact) -> None:
                if art.passed:
                    self.latest_verified_artifact = art
                else:
                    self.latest_verified_artifact = None
                if on_before_signoff is not None:
                    on_before_signoff(art)

            # Step 8 (Section 8.6): Final verification on in-sample + untouched holdout seeds
            verified_artifact = await run_final_verification(
                code=best_code,
                loop_params=best_params,
                review_params=review_params,
                limits=active_limits,
                report_md=report_md,
                briefing_text=briefing_text,
                emit_events=True,
                on_before_signoff=_register_artifact,
            )
            await briefing_task

            return verified_artifact
        finally:
            self.running = False


orchestrator = Orchestrator()
