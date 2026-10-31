"""Gemini Interactions API client with mock fallback (Section 8.5 & Section 9)."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from .. import config
from ..events import event_bus
from .prompts import (
    FORMAT_RETRY_PROMPT,
    build_propose_prompt,
    build_redteam_prompt,
    build_refine_prompt,
    build_repair_prompt,
    build_review_prompt,
    parse_json_object,
    parse_strategy_response,
)

_MOCKS_DIR = Path(__file__).resolve().parent.parent / "mocks"

MOCK_RATIONALES = {
    "repair": "Derive dynamic soft cap (85% of position_limit), widen spread to 4c, and tighten skew to 0.14c/lot to eliminate intra-tick C2 breaches.",
    "scratch_1": "Baseline symmetric market maker quoting a fixed 2-cent half-spread with 8-lot size on both sides and exact integer ledger accounting.",
    "scratch_2": "Add linear inventory skew and hard position-limit gating on bid and ask sizes so inventory never breaches position_limit.",
    "scratch_3": "Add causal 5-tick drop and realized-volatility circuit breaker that halts within 5 ticks of a crash and resumes after 100 calm ticks.",
}

DEFAULT_BRIEFING_TEXT = (
    "Desk rule tightened to sixty lots. The previous strategy breached inventory during the flash crash. "
    "I added a dynamic soft inventory cap and stronger skew. All seven checks pass across in-sample and unseen seeds. "
    "Ready for your approval."
)


def _get_delay_scale() -> float:
    raw = os.environ.get("TOWERTWIN_DELAY_SCALE")
    if raw is not None:
        try:
            return max(0.0, float(raw))
        except ValueError:
            pass
    # Keep unit tests crisp while preserving realistic pacing in normal server mode
    if "PYTEST_CURRENT_TEST" in os.environ:
        return 0.08
    return 1.0


def _extract_interaction_text(interaction: Any) -> str:
    """Extract text output from a google-genai Interaction object across SDK versions."""
    txt = getattr(interaction, "output_text", None)
    if isinstance(txt, str) and txt.strip():
        return txt
    outputs = getattr(interaction, "outputs", None)
    if outputs:
        for item in reversed(list(outputs)):
            item_text = getattr(item, "text", None)
            if isinstance(item_text, str) and item_text.strip():
                return item_text
    return ""


class GeminiClient:
    """Unified interface for Gemini 4 Argon, Gemini 3.8 Flash, and Gemini 3.8 Flash TTS."""

    def __init__(self) -> None:
        # Optional test hook for Section 11.2 holdout fallback verification
        self.test_review_params_override: dict[str, Any] | None = None

    @property
    def is_mock(self) -> bool:
        return config.is_mock_llm() or not config.has_gemini_api_key()

    def _create_sdk_client(self, timeout_ms: int = 120_000) -> Any:
        from google import genai
        from google.genai import types

        return genai.Client(
            api_key=config.get_gemini_api_key(),
            http_options=types.HttpOptions(timeout=timeout_ms),
        )

    def _call_interactions_sync(
        self,
        model: str,
        prompt: str,
        thinking_level: str | None = None,
        previous_interaction_id: str | None = None,
        timeout_ms: int = 180_000,
    ) -> tuple[str, str | None]:
        """Run a synchronous `client.interactions.create` call with 1 retry on transient error."""
        last_exc: Exception | None = None
        for attempt in range(2):
            try:
                client = self._create_sdk_client(timeout_ms=timeout_ms)
                kwargs: dict[str, Any] = {
                    "model": model,
                    "input": prompt,
                }
                if thinking_level is not None and model == config.MODEL_ARGON:
                    # Per Section 9: ONLY set thinking_level; never set temperature/top_p/top_k/thinking_budget
                    kwargs["generation_config"] = {"thinking_level": thinking_level}
                if previous_interaction_id:
                    kwargs["previous_interaction_id"] = previous_interaction_id

                interaction = client.interactions.create(**kwargs)
                text = _extract_interaction_text(interaction)
                interaction_id = getattr(interaction, "id", None)
                return text, interaction_id
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if attempt == 0:
                    continue
        raise RuntimeError(f"Interactions API call to {model} failed: {type(last_exc).__name__}: {last_exc}")

    async def propose_strategy(
        self,
        mode: str,
        iteration: int,
        limits: dict[str, Any],
        previous_code: str | None = None,
        failure_report: dict[str, str] | None = None,
        previous_interaction_id: str | None = None,
    ) -> tuple[str, str, str | None]:
        """Propose or refine a strategy via Argon (`(code, rationale, interaction_id)`)."""
        scale = _get_delay_scale()
        if self.is_mock:
            await asyncio.sleep(1.5 * scale)
            return self._mock_propose(mode, iteration, limits), self._mock_rationale(mode, iteration), f"mock-int-{iteration}"

        if mode == "repair" and iteration == 1:
            assert previous_code is not None and failure_report is not None
            prompt = build_repair_prompt(
                current_code=previous_code,
                failed_checks=failure_report["failed_checks"],
                passed_checks=failure_report["passed_checks"],
                metrics=failure_report["metrics"],
                limits=limits,
            )
            thinking_level = "medium"
        elif mode == "scratch" and iteration == 1:
            prompt = build_propose_prompt(limits=limits)
            thinking_level = "high"
        else:
            assert failure_report is not None
            prompt = build_refine_prompt(
                failed_checks=failure_report["failed_checks"],
                passed_checks=failure_report["passed_checks"],
                metrics=failure_report["metrics"],
                validation_pass_fail=failure_report["validation_pass_fail"],
            )
            thinking_level = "medium"

        timeout_ms = 900_000 if thinking_level == "high" else 180_000
        try:
            text, int_id = await asyncio.to_thread(
                self._call_interactions_sync,
                config.MODEL_ARGON,
                prompt,
                thinking_level,
                previous_interaction_id,
                timeout_ms,
            )
            code, rationale = parse_strategy_response(text)
            if not code:
                # Retry once with short format reminder per Section 8.1
                retry_text, int_id = await asyncio.to_thread(
                    self._call_interactions_sync,
                    config.MODEL_ARGON,
                    FORMAT_RETRY_PROMPT,
                    "medium",
                    int_id or previous_interaction_id,
                    120_000,
                )
                code, rationale = parse_strategy_response(retry_text)
            if code:
                return code, rationale, int_id
            raise ValueError("Model response did not contain a valid ```python code block.")
        except Exception as exc:  # noqa: BLE001
            event_bus.emit(
                "model_error",
                stage="propose",
                iteration=iteration,
                message=f"Argon propose error ({type(exc).__name__}); falling back to verified mock candidate.",
                fallback=True,
            )
            return self._mock_propose(mode, iteration, limits), self._mock_rationale(mode, iteration), previous_interaction_id

    def _mock_propose(self, mode: str, iteration: int, limits: dict[str, Any]) -> str:
        if mode == "repair":
            return (_MOCKS_DIR / "strategy_v4_repaired.py").read_text(encoding="utf-8")
        if iteration == 1:
            return (_MOCKS_DIR / "strategy_v1_naive.py").read_text(encoding="utf-8")
        if iteration == 2:
            return (_MOCKS_DIR / "strategy_v2_skew.py").read_text(encoding="utf-8")
        # Scratch iteration >= 3: return v3_desk at default limit 100, or v4_repaired if limit < 100
        if int(limits.get("position_limit", 100)) < 100:
            return (_MOCKS_DIR / "strategy_v4_repaired.py").read_text(encoding="utf-8")
        return (_MOCKS_DIR / "strategy_v3_desk.py").read_text(encoding="utf-8")

    def _mock_rationale(self, mode: str, iteration: int) -> str:
        if mode == "repair":
            return MOCK_RATIONALES["repair"]
        if iteration == 1:
            return MOCK_RATIONALES["scratch_1"]
        if iteration == 2:
            return MOCK_RATIONALES["scratch_2"]
        return MOCK_RATIONALES["scratch_3"]

    async def generate_redteam_scenario(self, index: int, angle: str) -> dict[str, Any]:
        """Generate one red-team stress scenario via Gemini 3.8 Flash (or staggered mock)."""
        scale = _get_delay_scale()
        canned_list = json.loads((_MOCKS_DIR / "redteam_scenarios.json").read_text(encoding="utf-8"))
        canned = dict(canned_list[index % len(canned_list)])

        if self.is_mock:
            await asyncio.sleep(0.25 * (index + 1) * scale)
            canned["fallback"] = False
            return canned

        prompt = build_redteam_prompt(angle)
        try:
            text, _ = await asyncio.to_thread(
                self._call_interactions_sync,
                config.MODEL_FLASH,
                prompt,
                None,
                None,
                45_000,
            )
            parsed = parse_json_object(text)
            if parsed and "name" in parsed and "duration" in parsed:
                parsed["fallback"] = False
                return parsed
            raise ValueError("Invalid JSON schema from Flash red-team call.")
        except Exception as exc:  # noqa: BLE001
            event_bus.emit(
                "model_error",
                stage="redteam",
                index=index,
                message=f"Flash red-team call {index} failed ({type(exc).__name__}); using fallback scenario.",
                fallback=True,
            )
            canned["fallback"] = True
            return canned

    async def generate_review(
        self,
        code: str,
        loop_params: dict[str, Any],
        check_report_summary: str,
        sweep_summary: str,
        best_sweep_params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run the REVIEW step via Argon (`thinking_level='medium'`) returning `final_params`, `report_md`, `briefing_text`."""
        scale = _get_delay_scale()
        default_report_md = (_MOCKS_DIR / "signoff_report.md").read_text(encoding="utf-8").strip()
        candidate_params = dict(loop_params)
        if best_sweep_params:
            candidate_params.update(best_sweep_params)
        if self.test_review_params_override is not None:
            candidate_params = dict(self.test_review_params_override)

        if self.is_mock:
            await asyncio.sleep(0.6 * scale)
            return {
                "final_params": candidate_params,
                "report_md": default_report_md,
                "briefing_text": DEFAULT_BRIEFING_TEXT,
            }

        prompt = build_review_prompt(
            code=code,
            check_report_summary=check_report_summary,
            sweep_summary=sweep_summary,
        )
        try:
            text, _ = await asyncio.to_thread(
                self._call_interactions_sync,
                config.MODEL_ARGON,
                prompt,
                "medium",
                None,
                180_000,
            )
            parsed = parse_json_object(text)
            if parsed and isinstance(parsed.get("report_md"), str):
                raw_fp = parsed.get("final_params")
                final_params = dict(loop_params)
                if isinstance(raw_fp, dict):
                    for k, v in raw_fp.items():
                        if k in final_params and isinstance(v, (int, float)):
                            final_params[k] = v
                if self.test_review_params_override is not None:
                    final_params = dict(self.test_review_params_override)
                return {
                    "final_params": final_params,
                    "report_md": str(parsed["report_md"]).strip(),
                    "briefing_text": str(parsed.get("briefing_text") or DEFAULT_BRIEFING_TEXT).strip(),
                }
            raise ValueError("Review response did not match expected JSON structure.")
        except Exception as exc:  # noqa: BLE001
            event_bus.emit(
                "model_error",
                stage="review",
                message=f"Argon review error ({type(exc).__name__}); using fallback sign-off report.",
                fallback=True,
            )
            return {
                "final_params": candidate_params,
                "report_md": default_report_md,
                "briefing_text": DEFAULT_BRIEFING_TEXT,
            }


gemini_client = GeminiClient()
