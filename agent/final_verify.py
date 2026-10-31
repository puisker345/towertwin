"""Final verification on untouched holdout_seeds and SHA-256 approval binding (Section 8.6)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from .. import config
from ..checks.checks import CheckReport, compute_artifact_hash, evaluate_checks
from ..events import event_bus
from ..sandbox.runner import run_backtests_batch
from ..sim.scenarios import BASELINE_CRASH


@dataclass
class VerifiedArtifact:
    code: str
    params: dict[str, Any]
    limits: dict[str, Any]
    artifact_hash: str
    artifact_id: str
    report: CheckReport
    report_md: str
    briefing_text: str
    used_fallback_params: bool
    passed: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_hash": self.artifact_hash,
            "artifact_id": self.artifact_id,
            "params": dict(self.params),
            "limits": dict(self.limits),
            "check_report": self.report.to_dict(),
            "report_md": self.report_md,
            "briefing_text": self.briefing_text,
            "used_fallback_params": self.used_fallback_params,
            "passed": self.passed,
        }


def evaluate_final_candidate_sync(
    code: str,
    candidate_params: dict[str, Any],
    limits: dict[str, Any],
    ticks: int = config.TICKS,
) -> tuple[CheckReport, dict[str, Any]]:
    """Run C1-C7 on `in_sample_seeds` + `holdout_seeds` in the isolated child subprocess."""
    in_seeds = list(config.in_sample_seeds)
    ho_seeds = list(config.holdout_seeds)
    combined_seeds = in_seeds + ho_seeds

    results, effective_params, _ = run_backtests_batch(
        code=code,
        scenario=BASELINE_CRASH,
        seeds=combined_seeds,
        ticks=ticks,
        limits=limits,
        params_override=candidate_params,
        full_telemetry_seeds=[in_seeds[0]] if in_seeds else None,
    )
    in_count = len(in_seeds)
    in_results = results[:in_count]
    ho_results = results[in_count:]

    report = evaluate_checks(
        in_sample_results=in_results,
        validation_results=ho_results,
        limits=limits,
        code=code,
        params=effective_params,
        is_final_verification=True,
    )
    return report, effective_params


async def run_final_verification(
    code: str,
    loop_params: dict[str, Any],
    review_params: dict[str, Any] | None,
    limits: dict[str, Any],
    report_md: str,
    briefing_text: str,
    emit_events: bool = True,
    on_before_signoff: Any = None,
) -> VerifiedArtifact:
    """Execute Section 8.6 final verification with automatic fallback to `loop_params` if needed."""
    candidate_params = dict(review_params) if review_params is not None else dict(loop_params)
    used_fallback = False

    report, eff_params = await asyncio.to_thread(
        evaluate_final_candidate_sync,
        code,
        candidate_params,
        limits,
        config.TICKS,
    )

    if not report.all_passed and candidate_params != dict(loop_params):
        # Discard REVIEW parameters and fall back to the parameters that passed the loop (Section 8.6 step 5)
        used_fallback = True
        report, eff_params = await asyncio.to_thread(
            evaluate_final_candidate_sync,
            code,
            dict(loop_params),
            limits,
            config.TICKS,
        )

    artifact_hash = compute_artifact_hash(code, eff_params, limits)
    artifact_id = artifact_hash[:8]
    report.artifact_hash = artifact_hash

    passed_count = sum(1 for c in report.checks if c.passed)
    total_count = len(report.checks)
    in_n = len(config.in_sample_seeds)
    ho_n = len(config.holdout_seeds)

    fallback_note = " (Reverted to loop parameters after review candidate failed unseen evaluation.)" if used_fallback else ""
    footer_line = (
        f"\n\n---\n**Final verification:** {passed_count}/{total_count} checks on "
        f"{in_n} in-sample + {ho_n} holdout seeds.{fallback_note} Artifact `{artifact_id}`."
    )
    final_report_md = report_md.strip() + footer_line

    artifact = VerifiedArtifact(
        code=code,
        params=eff_params,
        limits=dict(limits),
        artifact_hash=artifact_hash,
        artifact_id=artifact_id,
        report=report,
        report_md=final_report_md,
        briefing_text=briefing_text,
        used_fallback_params=used_fallback,
        passed=report.all_passed,
    )

    if on_before_signoff is not None:
        on_before_signoff(artifact)

    if emit_events:
        event_bus.emit(
            "final_verification",
            passed=report.all_passed,
            report=report.to_dict(),
            artifact_hash=artifact_hash,
            artifact_id=artifact_id,
            params=eff_params,
            used_fallback_params=used_fallback,
            in_sample_count=in_n,
            unseen_count=ho_n,
        )
        if report.all_passed:
            event_bus.emit(
                "signoff_ready",
                artifact_hash=artifact_hash,
                artifact_id=artifact_id,
                params=eff_params,
                limits=dict(limits),
                report_md=final_report_md,
                briefing_text=briefing_text,
                check_report=report.to_dict(),
                used_fallback_params=used_fallback,
                voice_consented=config.has_voice_sample(),
            )
        else:
            event_bus.emit(
                "signoff_blocked",
                reason="Failed on unseen holdout data",
                artifact_hash=artifact_hash,
                artifact_id=artifact_id,
                report_md=final_report_md,
                check_report=report.to_dict(),
            )

    return artifact
