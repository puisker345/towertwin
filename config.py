"""Configuration, simulator parameters, desk rules, seed sets, and environment flags."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

# Load .env from towertwin directory or workspace root if present without exposing secrets
for _env_candidate in (
    Path(__file__).resolve().parent / ".env",
    Path(__file__).resolve().parent.parent / ".env",
    Path.cwd() / ".env",
):
    if _env_candidate.is_file():
        for _line in _env_candidate.read_text(encoding="utf-8").splitlines():
            _stripped = _line.strip()
            if not _stripped or _stripped.startswith("#") or "=" not in _stripped:
                continue
            _k, _v = _stripped.split("=", 1)
            _k = _k.strip()
            if "#" in _v:
                _v = _v.split("#", 1)[0]
            _v = _v.strip().strip('"').strip("'")
            if _k and _v and not os.environ.get(_k, "").strip():
                os.environ[_k] = _v


def _env_int(name: str, default: int) -> int:
    val = os.environ.get(name, "").strip()
    return int(val) if val else default


def _env_float(name: str, default: float) -> float:
    val = os.environ.get(name, "").strip()
    return float(val) if val else default


def _env_str(name: str, default: str) -> str:
    val = os.environ.get(name, "").strip()
    return val if val else default


# Server host & port
HOST: str = _env_str("HOST", _env_str("TOWERTWIN_HOST", "0.0.0.0"))
PORT: int = _env_int("PORT", _env_int("TOWERTWIN_PORT", 8000))

# Model identifiers (Section 9)
MODEL_ARGON: str = "gemini-4-argon"
MODEL_FLASH: str = "gemini-3.8-flash"
MODEL_TTS: str = "gemini-3.8-flash-tts"

# Simulator default parameters (Section 5.2)
TICKS: int = _env_int("TOWERTWIN_TICKS", 3000)
START_MID: int = _env_int("TOWERTWIN_START_MID", 10_000)  # cents (= $100.00)
INITIAL_MID: int = START_MID
TICK_SIZE: int = _env_int("TOWERTWIN_TICK_SIZE", 1)       # cent
SIGMA: float = _env_float("TOWERTWIN_SIGMA", 2.0)         # std dev of mid change per tick, cents
SIGMA_NORMAL: float = SIGMA
ARRIVAL_RATE: float = _env_float("TOWERTWIN_ARRIVAL_RATE", 0.8)
LAMBDA_BASE: float = ARRIVAL_RATE
ORDER_SIZE_MIN: int = _env_int("TOWERTWIN_ORDER_SIZE_MIN", 1)
ORDER_SIZE_MAX: int = _env_int("TOWERTWIN_ORDER_SIZE_MAX", 5)
FILL_K: float = _env_float("TOWERTWIN_FILL_K", 0.35)
FEE_PER_LOT: int = _env_int("TOWERTWIN_FEE_PER_LOT", 1)   # cent per lot filled
FEE_CENTS_PER_LOT: int = FEE_PER_LOT
RECOVERY_TICKS: int = 200
KAPPA: float = 0.02
AGGRESSION_SCALE_CENTS: float = 3.5

# Baseline crash parameters (Section 5.2)
CRASH_START: int = _env_int("TOWERTWIN_CRASH_START", 2000)
CRASH_DEPTH_PCT: float = _env_float("TOWERTWIN_CRASH_DEPTH_PCT", 6.0)
CRASH_DURATION: int = _env_int("TOWERTWIN_CRASH_DURATION", 30)
CRASH_SIGMA_MULT: float = _env_float("TOWERTWIN_CRASH_SIGMA_MULT", 5.0)
CRASH_SELL_FLOW_MULT: float = _env_float("TOWERTWIN_CRASH_SELL_FLOW_MULT", 4.0)
CRASH_BUY_FLOW_MULT: float = _env_float("TOWERTWIN_CRASH_BUY_FLOW_MULT", 0.3)
CRASH_RECOVERY_PCT: float = _env_float("TOWERTWIN_CRASH_RECOVERY_PCT", 40.0)

# Seed sets (Section 5.2)
in_sample_seeds: list[int] = [1, 2, 3]
oos_seeds: list[int] = [101, 102, 103]
validation_seeds: list[int] = oos_seeds
holdout_seeds: list[int] = [201, 202, 203, 204, 205]

IN_SAMPLE_SEEDS = in_sample_seeds
OOS_SEEDS = oos_seeds

# Sweep & slider bounds (Section 5.2)
SWEEP_TICKS: int = _env_int("TOWERTWIN_SWEEP_TICKS", 1500)
POSITION_LIMIT_RANGE: tuple[int, int] = (40, 100)

# Default desk rules (Section 6)
DEFAULT_LIMITS: dict[str, int] = {
    "position_limit": _env_int("TOWERTWIN_POSITION_LIMIT", 100),
    "max_order_size": _env_int("TOWERTWIN_MAX_ORDER_SIZE", 10),
    "max_drawdown": _env_int("TOWERTWIN_MAX_DRAWDOWN", 25_000),
    "halt_within_ticks": _env_int("TOWERTWIN_HALT_WITHIN_TICKS", 5),
    "calm_ticks_to_resume": _env_int("TOWERTWIN_CALM_TICKS_TO_RESUME", 100),
}

MAX_ITERATIONS: int = _env_int("TOWERTWIN_MAX_ITERATIONS", 6)


def is_mock_llm() -> bool:
    """Return True if MOCK_LLM is enabled (default 1 when unset)."""
    val = os.environ.get("MOCK_LLM", "1").strip().lower()
    return val not in ("0", "false", "no")


def get_argon_thinking(default: str = "medium") -> str:
    """Return configured thinking_level ('low' | 'medium' | 'high')."""
    val = os.environ.get("ARGON_THINKING", default).strip().lower()
    if val in ("low", "medium", "high"):
        return val
    return default


def get_voice_sample() -> str:
    """Return optional path to consented voice sample if configured."""
    return os.environ.get("VOICE_SAMPLE", "").strip()


def has_voice_sample() -> bool:
    """Return True if a consented voice sample file is configured and exists."""
    vp = get_voice_sample()
    return bool(vp and Path(vp).is_file())


def get_sandbox_mode() -> str:
    """Return sandbox mode ('local' or 'antigravity')."""
    return os.environ.get("SANDBOX", "local").strip().lower() or "local"


def has_api_key() -> bool:
    """Return True if GEMINI_API_KEY is non-empty without exposing its value."""
    return bool(os.environ.get("GEMINI_API_KEY", "").strip())


has_gemini_api_key = has_api_key


def get_gemini_api_key() -> str:
    """Return GEMINI_API_KEY from environment for SDK initialization only."""
    return os.environ.get("GEMINI_API_KEY", "").strip()


def get_desk_limits(position_limit: int | None = None) -> dict[str, int]:
    """Return a copy of desk rules with optional clamped position_limit override."""
    limits = dict(DEFAULT_LIMITS)
    if position_limit is not None:
        lo, hi = POSITION_LIMIT_RANGE
        limits["position_limit"] = max(lo, min(hi, int(position_limit)))
    return limits


def get_tightened_desk_limits(position_limit: int = 60) -> dict[str, int]:
    """Return tightened desk rules (default 60 lots) for the repair demo flow."""
    return get_desk_limits(position_limit)


def get_public_config(current_limits: dict[str, int] | None = None) -> dict[str, Any]:
    """Return safe public config for GET /api/config (never includes API keys)."""
    return {
        "limits": dict(current_limits or DEFAULT_LIMITS),
        "position_limit_range": list(POSITION_LIMIT_RANGE),
        "mock_llm": is_mock_llm(),
        "has_api_key": has_api_key(),
        "voice_replicated": has_voice_sample(),
        "argon_thinking": get_argon_thinking(),
        "sandbox": get_sandbox_mode(),
        "ticks": TICKS,
        "sweep_ticks": SWEEP_TICKS,
        "in_sample_seeds": list(in_sample_seeds),
        "oos_seeds": list(oos_seeds),
    }
