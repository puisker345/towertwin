"""Scenario definitions, bounds, JSON schema, and fit_to_length (Section 5.5)."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, replace
from typing import Any

from .. import config

logger = logging.getLogger(__name__)

BOUNDS: dict[str, tuple[float, float]] = {
    "start": (500, 2800),
    "depth_pct": (1.0, 15.0),
    "duration": (5, 120),
    "sigma_mult": (1.0, 10.0),
    "sell_flow_mult": (0.1, 6.0),
    "buy_flow_mult": (0.1, 6.0),
    "recovery_pct": (0.0, 100.0),
}

JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "name",
        "description",
        "start",
        "depth_pct",
        "duration",
        "sigma_mult",
        "sell_flow_mult",
        "buy_flow_mult",
        "recovery_pct",
    ],
    "properties": {
        "name": {"type": "string", "minLength": 1, "maxLength": 80},
        "description": {"type": "string", "minLength": 1, "maxLength": 240},
        "start": {"type": "integer", "minimum": 500, "maximum": 2800},
        "depth_pct": {"type": "number", "minimum": 1.0, "maximum": 15.0},
        "duration": {"type": "integer", "minimum": 5, "maximum": 120},
        "sigma_mult": {"type": "number", "minimum": 1.0, "maximum": 10.0},
        "sell_flow_mult": {"type": "number", "minimum": 0.1, "maximum": 6.0},
        "buy_flow_mult": {"type": "number", "minimum": 0.1, "maximum": 6.0},
        "recovery_pct": {"type": "number", "minimum": 0.0, "maximum": 100.0},
    },
}


@dataclass(frozen=True)
class Scenario:
    name: str
    description: str
    start: int = config.CRASH_START
    depth_pct: float = config.CRASH_DEPTH_PCT
    duration: int = config.CRASH_DURATION
    sigma_mult: float = config.CRASH_SIGMA_MULT
    sell_flow_mult: float = config.CRASH_SELL_FLOW_MULT
    buy_flow_mult: float = config.CRASH_BUY_FLOW_MULT
    recovery_pct: float = config.CRASH_RECOVERY_PCT
    fallback: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Scenario":
        return cls(
            name=str(data.get("name", "Scenario")),
            description=str(data.get("description", "")),
            start=int(round(float(data.get("start", config.CRASH_START)))),
            depth_pct=float(data.get("depth_pct", config.CRASH_DEPTH_PCT)),
            duration=int(round(float(data.get("duration", config.CRASH_DURATION)))),
            sigma_mult=float(data.get("sigma_mult", config.CRASH_SIGMA_MULT)),
            sell_flow_mult=float(data.get("sell_flow_mult", config.CRASH_SELL_FLOW_MULT)),
            buy_flow_mult=float(data.get("buy_flow_mult", config.CRASH_BUY_FLOW_MULT)),
            recovery_pct=float(data.get("recovery_pct", config.CRASH_RECOVERY_PCT)),
            fallback=bool(data.get("fallback", False)),
        )


BASELINE_CRASH = Scenario(
    name="Baseline Flash Crash",
    description="6% sudden drop over 30 ticks starting at tick 2000 with 5x volatility and 4x sell flow.",
    start=config.CRASH_START,
    depth_pct=config.CRASH_DEPTH_PCT,
    duration=config.CRASH_DURATION,
    sigma_mult=config.CRASH_SIGMA_MULT,
    sell_flow_mult=config.CRASH_SELL_FLOW_MULT,
    buy_flow_mult=config.CRASH_BUY_FLOW_MULT,
    recovery_pct=config.CRASH_RECOVERY_PCT,
)


def _clamp_val(key: str, raw: float | int, is_int: bool = False) -> float | int:
    lo, hi = BOUNDS[key]
    val = float(raw)
    if val < lo or val > hi:
        clamped = max(lo, min(hi, val))
        logger.warning("Clamped scenario field %s from %s to %s", key, raw, clamped)
        val = clamped
    return int(round(val)) if is_int else float(val)


def parse_and_clamp_scenario(raw_input: str | dict[str, Any], fallback: bool = False) -> Scenario:
    """Parse JSON or dict into a Scenario, clamping numeric fields to BOUNDS.

    Raises ValueError if JSON is malformed or required fields are missing/wrong type.
    """
    if isinstance(raw_input, str):
        text = raw_input.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Malformed scenario JSON: {exc}") from exc
    elif isinstance(raw_input, dict):
        data = dict(raw_input)
    else:
        raise ValueError(f"Expected JSON string or dict, got {type(raw_input).__name__}")

    if not isinstance(data, dict):
        raise ValueError("Scenario JSON root must be an object")

    required_keys = JSON_SCHEMA["required"]
    for key in required_keys:
        if key not in data:
            raise ValueError(f"Missing required scenario field: {key!r}")

    name = str(data["name"]).strip()
    description = str(data["description"]).strip()
    if not name or not description:
        raise ValueError("Scenario 'name' and 'description' must be non-empty strings")

    try:
        start = int(_clamp_val("start", float(data["start"]), is_int=True))
        depth_pct = float(_clamp_val("depth_pct", float(data["depth_pct"])))
        duration = int(_clamp_val("duration", float(data["duration"]), is_int=True))
        sigma_mult = float(_clamp_val("sigma_mult", float(data["sigma_mult"])))
        sell_flow_mult = float(_clamp_val("sell_flow_mult", float(data["sell_flow_mult"])))
        buy_flow_mult = float(_clamp_val("buy_flow_mult", float(data["buy_flow_mult"])))
        recovery_pct = float(_clamp_val("recovery_pct", float(data["recovery_pct"])))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid numeric field in scenario: {exc}") from exc

    return Scenario(
        name=name,
        description=description,
        start=start,
        depth_pct=depth_pct,
        duration=duration,
        sigma_mult=sigma_mult,
        sell_flow_mult=sell_flow_mult,
        buy_flow_mult=buy_flow_mult,
        recovery_pct=recovery_pct,
        fallback=bool(data.get("fallback", fallback)),
    )


validate_and_clamp_scenario = parse_and_clamp_scenario


def fit_to_length(scenario: Scenario, ticks: int, reference_ticks: int = 3000) -> Scenario:
    """Return a copy of `scenario` whose crash window fits inside a run of `ticks` ticks.

    Per Section 5.5:
    - If `ticks < reference_ticks`, rescale `start` proportionally (`start * ticks / 3000`, rounded).
    - Clamp so that `start >= 200` (enough history for detection) and
      `start + duration + 300 <= ticks` (room to observe the halt and the start of recovery).
    - Asserts that the crash window lies inside the run.
    """
    ticks = int(ticks)
    if ticks < 505:
        raise ValueError(f"Backtest ticks ({ticks}) must be >= 505 to fit crash + history + recovery")

    duration = int(scenario.duration)
    max_allowed_duration = ticks - 200 - 300
    if duration > max_allowed_duration:
        duration = max(5, max_allowed_duration)

    if ticks < reference_ticks:
        scaled_start = int(round(scenario.start * (ticks / float(reference_ticks))))
    else:
        scaled_start = int(scenario.start)

    max_start = ticks - duration - 300
    clamped_start = max(200, min(scaled_start, max_start))

    fitted = replace(scenario, start=clamped_start, duration=duration)
    assert 200 <= fitted.start, f"Crash start {fitted.start} < 200"
    assert fitted.start + fitted.duration + 300 <= ticks, (
        f"Crash window ({fitted.start} + {fitted.duration} + 300) exceeds ticks ({ticks})"
    )
    return fitted
