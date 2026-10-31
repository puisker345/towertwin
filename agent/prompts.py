"""Prompt templates and output parsers for Argon and Flash (Section 8.2)."""

from __future__ import annotations

import json
import re
from typing import Any

STRATEGY_CONTRACT = """
1. Only allowed imports: `math`, `statistics`, `collections`. `Quote` is pre-injected in globals.
2. Top-level `PARAMS = {...}` dictionary of numeric parameters (int or float).
3. Top-level `SWEEP_PARAMS = ("param_x", "param_y")` 2-tuple naming two distinct keys of `PARAMS`.
4. `class Strategy` with exact methods:
   - `def __init__(self, params: dict, limits: dict)`
   - `def on_tick(self, state: MarketState) -> Quote`
     where `Quote(bid_price: int | None, bid_size: int, ask_price: int | None, ask_size: int)`
     and `state` has `.tick`, `.mid` (int cents), `.recent_mids` (tuple[int, ...], oldest first),
     `.position` (int lots), `.recent_trades` (tuple of `(price, side, size)`), `.limits` (dict).
     To halt quoting during a crash, return `Quote(None, 0, None, 0)`.
   - `def on_fill(self, fill: Fill) -> None`
     where `fill` has `.tick`, `.side` ("buy" or "sell"), `.price` (int cents), `.size` (int lots), `.fee` (int cents).
   - `def report(self) -> dict` returning `{"position": int, "cash": int, "fees": int}` in exact integer cents/lots.
5. Maximum 300 lines of Python. No file I/O, no `getattr`, no dunder attribute access.
""".strip()

MARKET_DESCRIPTION = """
Discrete-time market simulator (3,000 ticks per session, initial mid 10,000 cents = $100.00, 1 cent tick size).
Maker fee is 1 cent per lot filled. Normal regime has mean-reverting mid with Poisson market buy/sell orders.
Each run contains a sudden flash crash window (default 6% drop over 30 ticks with 5x volatility and 4x sell flow)
followed by partial recovery.
""".strip()

SCENARIO_SCHEMA_AND_BOUNDS = """
{
  "name": "string (short title)",
  "description": "string (one-sentence risk description)",
  "start": "int in [500, 2800] (tick when crash begins)",
  "depth_pct": "float in [1.0, 15.0] (total % move magnitude; always positive)",
  "duration": "int in [5, 120] (ticks of the crash phase)",
  "sigma_mult": "float in [1.0, 10.0] (volatility multiplier during crash)",
  "sell_flow_mult": "float in [0.1, 6.0] (sell order arrival multiplier)",
  "buy_flow_mult": "float in [0.1, 6.0] (buy order arrival multiplier; set > sell_flow_mult for spike-up squeeze)",
  "recovery_pct": "float in [0.0, 100.0] (% of move recovered over 300 ticks)"
}
""".strip()

RED_TEAM_ANGLES: tuple[str, ...] = (
    "deeper crash",
    "faster crash",
    "crash early in the session",
    "spike-up squeeze (buy flow dominates)",
    "choppy high-volatility market without a crash",
    "liquidity drought with slow recovery",
)

REPAIR_PROMPT = """You maintain a market-making strategy for a student trading desk's simulated exchange.
The desk has just changed its rules. The current strategy was re-tested under the new rules
in a deterministic simulator, including a flash crash, and these checks FAILED
(exact numbers, from code, not opinions):
{FAILED_CHECKS}
These passed: {PASSED_CHECKS}
In-sample metrics per seed: {METRICS}
New desk rules: {DESK_RULES}
Current strategy:
```python
{CURRENT_CODE}
```
Repair the strategy so every check passes under the new rules. Make the smallest change that
fixes the root cause, and prefer deriving limits from `limits` over hard-coded numbers.
Keep a safety buffer of at least max_order_size below position_limit rather than setting inventory caps equal to hard limits, and never quote a size that could take |position| past (position_limit - max_order_size).
Keep the contract exactly:
{STRATEGY_CONTRACT}
Use only past data in MarketState. Keep PARAMS and SWEEP_PARAMS meaningful for a robustness sweep.
Return exactly one ```python code block, then one line: RATIONALE: <max 25 words>."""

PROPOSE_PROMPT = """You are writing a market-making strategy for a student trading desk's simulated exchange.
Write the SIMPLEST possible symmetric market maker as a baseline: quote a fixed half-spread
around the mid on both sides with a fixed size. No inventory control, no crash handling.
It must follow this contract exactly:
{STRATEGY_CONTRACT}
Desk rules (these will be checked deterministically later):
{DESK_RULES}
Market description: {MARKET_DESCRIPTION}
Return exactly one ```python code block, then one line: RATIONALE: <max 25 words>."""

REFINE_PROMPT = """Your strategy was backtested in a deterministic simulator, including a flash crash.
These checks FAILED (exact numbers, from code, not opinions):
{FAILED_CHECKS}
These passed: {PASSED_CHECKS}
In-sample metrics per seed: {METRICS}
Validation seeds: {VALIDATION_PASS_FAIL}   (pass/fail only; a separate untouched evaluation set is used at the end)
Revise the strategy so every check passes. Change only what is needed and keep it under 300 lines.
Use only past data in MarketState. Keep PARAMS and SWEEP_PARAMS meaningful for a robustness sweep.
Return exactly one ```python code block, then one line: RATIONALE: <max 25 words>."""

RED_TEAM_PROMPT = """You are a risk red-teamer. Propose ONE stress scenario for a market-making bot as JSON matching
this schema and bounds:
{SCENARIO_SCHEMA_AND_BOUNDS}
Angle for this scenario: {ANGLE}
Return only the JSON object. Include a short "name" and a one-sentence "description"."""

REVIEW_PROMPT = """You are preparing a strategy for the desk head's approval.
Final strategy code:
```python
{CODE}
```
Check report: {CHECK_REPORT}
Sweep results (pass rate per parameter cell and per scenario): {SWEEP_SUMMARY}
1) Choose final PARAMS from the most robust region of the sweep (not the single best cell). Return them as JSON.
   Whatever you choose will be re-verified from scratch on untouched final evaluation seeds before approval;
   if it fails, the last parameters that passed the loop are used instead.
2) Write a sign-off report (max 150 words): what changed, what was verified, remaining risks,
   and a clear recommendation. Write it as provisional: the final verification result is added after.
3) Write a spoken briefing (max 40 words, plain sentences, no symbols) that ends by asking for approval.
Return JSON: {{"final_params": {{...}}, "report_md": "...", "briefing_text": "..."}}"""

FORMAT_RETRY_PROMPT = """Your previous response could not be parsed.
Return ONLY:
1) Exactly one fenced ```python ... ``` code block containing the complete strategy module.
2) One line at the very end starting with `RATIONALE: ` followed by at most 25 words."""


def build_repair_prompt(
    current_code: str,
    failed_checks: str,
    passed_checks: str,
    metrics: str,
    limits: dict[str, Any],
) -> str:
    return REPAIR_PROMPT.format(
        FAILED_CHECKS=failed_checks,
        PASSED_CHECKS=passed_checks,
        METRICS=metrics,
        DESK_RULES=json.dumps(limits, sort_keys=True),
        CURRENT_CODE=current_code.strip(),
        STRATEGY_CONTRACT=STRATEGY_CONTRACT,
    )


def build_propose_prompt(limits: dict[str, Any]) -> str:
    return PROPOSE_PROMPT.format(
        STRATEGY_CONTRACT=STRATEGY_CONTRACT,
        DESK_RULES=json.dumps(limits, sort_keys=True),
        MARKET_DESCRIPTION=MARKET_DESCRIPTION,
    )


def build_refine_prompt(
    failed_checks: str,
    passed_checks: str,
    metrics: str,
    validation_pass_fail: str,
) -> str:
    return REFINE_PROMPT.format(
        FAILED_CHECKS=failed_checks,
        PASSED_CHECKS=passed_checks,
        METRICS=metrics,
        VALIDATION_PASS_FAIL=validation_pass_fail,
    )


def build_redteam_prompt(angle: str) -> str:
    return RED_TEAM_PROMPT.format(
        SCENARIO_SCHEMA_AND_BOUNDS=SCENARIO_SCHEMA_AND_BOUNDS,
        ANGLE=angle,
    )


def build_review_prompt(code: str, check_report_summary: str, sweep_summary: str) -> str:
    return REVIEW_PROMPT.format(
        CODE=code.strip(),
        CHECK_REPORT=check_report_summary,
        SWEEP_SUMMARY=sweep_summary,
    )


_CODE_BLOCK_RE = re.compile(r"```(?:python|py)?\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)
_XML_PYTHON_RE = re.compile(r"<python>\s*(.*?)\s*</python>", re.DOTALL | re.IGNORECASE)
_RATIONALE_RE = re.compile(r"^(?:RATIONALE|THOUGHT)\s*:\s*(.+)$", re.MULTILINE | re.IGNORECASE)


def parse_strategy_response(text: str) -> tuple[str | None, str]:
    """Extract `(code, rationale)` from an Argon response (Section 8.1)."""
    if not text:
        return None, ""
    code: str | None = None
    m_code = _CODE_BLOCK_RE.search(text)
    if m_code:
        code = m_code.group(1).strip()
    else:
        m_xml = _XML_PYTHON_RE.search(text)
        if m_xml:
            code = m_xml.group(1).strip()

    rationale = "Updated strategy parameters and risk guards to satisfy all C1-C7 desk constraints."
    m_rat = _RATIONALE_RE.search(text)
    if m_rat:
        words = m_rat.group(1).strip().split()
        rationale = " ".join(words[:25])
    else:
        # Look for last non-empty line outside code fence
        stripped_text = _CODE_BLOCK_RE.sub("", text).strip()
        lines = [ln.strip() for ln in stripped_text.splitlines() if ln.strip()]
        if lines:
            words = lines[-1].split()
            rationale = " ".join(words[:25])

    return code, rationale


def parse_json_object(text: str) -> dict[str, Any] | None:
    """Extract a JSON object from model output (handling optional markdown code fences)."""
    if not text:
        return None
    cleaned = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, re.DOTALL | re.IGNORECASE)
    if fence:
        cleaned = fence.group(1)
    else:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1 and end > start:
            cleaned = cleaned[start : end + 1]
    try:
        parsed = json.loads(cleaned)
        return parsed if isinstance(parsed, dict) else None
    except Exception:  # noqa: BLE001
        return None
