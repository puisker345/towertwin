"""Pytest configuration ensuring `towertwin` is importable and anyio uses asyncio."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PACKAGE_DIR = Path(__file__).resolve().parent.parent
_PARENT_DIR = _PACKAGE_DIR.parent

if str(_PARENT_DIR) not in sys.path:
    sys.path.insert(0, str(_PARENT_DIR))


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
