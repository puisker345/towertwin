"""Mock strategies and canned model artifacts for offline/mock mode."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..sim.types import Quote
from . import strategy_v1_naive, strategy_v2_skew, strategy_v3_desk, strategy_v4_repaired

MOCKS_DIR = Path(__file__).resolve().parent

# Inject Quote into imported mock modules for in-process unit testing
for _mod in (strategy_v1_naive, strategy_v2_skew, strategy_v3_desk, strategy_v4_repaired):
    _mod.__dict__["Quote"] = Quote


def read_mock_strategy(name: str) -> str:
    """Read raw source code of a mock strategy file."""
    filename = name if name.endswith(".py") else f"{name}.py"
    return (MOCKS_DIR / filename).read_text(encoding="utf-8")


def load_redteam_scenarios_raw() -> list[dict[str, Any]]:
    """Load canned Flash red-team scenarios from mocks/redteam_scenarios.json."""
    return json.loads((MOCKS_DIR / "redteam_scenarios.json").read_text(encoding="utf-8"))


def read_signoff_report_md() -> str:
    """Read canned sign-off report markdown."""
    return (MOCKS_DIR / "signoff_report.md").read_text(encoding="utf-8").strip()
