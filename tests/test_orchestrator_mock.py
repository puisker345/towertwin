"""Integration tests for holdout isolation and mock orchestrator E2E flows (SPEC Sections 11.1 & 11.2)."""

from __future__ import annotations

from pathlib import Path

import pytest

from towertwin import config
from towertwin.agent.gemini_client import gemini_client
from towertwin.agent.orchestrator import orchestrator
from towertwin.checks.checks import compute_artifact_hash
from towertwin.events import event_bus

_PACKAGE_ROOT = Path(__file__).resolve().parent.parent


def test_holdout_isolation_source_grep_and_prompt_isolation():
    """Assert `holdout_seeds` is referenced ONLY in `config.py` and `agent/final_verify.py` (excluding tests)."""
    target_token = "holdout" + "_seeds"
    referencing_files: set[str] = set()

    for py_file in _PACKAGE_ROOT.rglob("*.py"):
        rel = py_file.relative_to(_PACKAGE_ROOT).as_posix()
        if rel.startswith("tests/") or rel.startswith(".agents/"):
            continue
        content = py_file.read_text(encoding="utf-8")
        if target_token in content:
            referencing_files.add(rel)

    assert referencing_files == {"config.py", "agent/final_verify.py"}, (
        f"Holdout isolation violated! Found `{target_token}` in: {sorted(referencing_files)}"
    )


@pytest.mark.anyio
async def test_mock_repair_mode_e2e_event_sequence(monkeypatch):
    """Verify full repair mode event sequence and ordering (Section 11.2)."""
    monkeypatch.setenv("MOCK_LLM", "1")
    monkeypatch.setenv("TOWERTWIN_DELAY_SCALE", "0.05")
    event_bus.clear_history()

    limits = config.get_tightened_desk_limits()
    limits["position_limit"] = 60

    artifact = await orchestrator.run(mode="repair", limits=limits)
    assert artifact is not None
    assert artifact.passed
    assert len(artifact.artifact_hash) == 64

    events = event_bus.history
    types = [e["type"] for e in events]

    # 1. iteration_proposed(0) -> checks(0) with C2 failed
    iter0_prop_idx = next(
        i for i, e in enumerate(events) if e["type"] == "iteration_proposed" and e.get("iteration") == 0
    )
    checks0_idx = next(
        i for i, e in enumerate(events) if e["type"] == "checks" and e.get("iteration") == 0
    )
    assert iter0_prop_idx < checks0_idx
    checks0_evt = events[checks0_idx]
    assert not checks0_evt["all_passed"]
    c2_0 = next(c for c in checks0_evt["checks"] if c["id"] == "C2")
    assert not c2_0["passed"]

    # Verify loop prompt inputs on checks(0) never contain unseen seed numbers (301..305)
    prompt_fields = checks0_evt["report"]
    for unseen_seed in (301, 302, 303, 304, 305):
        assert unseen_seed not in [m["seed"] for m in prompt_fields["seed_metrics"]]

    # 2. loop_stage -> iteration_proposed(1) -> checks(1) all passed -> loop_passed
    iter1_prop_idx = next(
        i for i, e in enumerate(events) if e["type"] == "iteration_proposed" and e.get("iteration") == 1
    )
    checks1_idx = next(
        i for i, e in enumerate(events) if e["type"] == "checks" and e.get("iteration") == 1
    )
    loop_passed_idx = next(i for i, e in enumerate(events) if e["type"] == "loop_passed")
    assert checks0_idx < iter1_prop_idx < checks1_idx < loop_passed_idx
    assert events[checks1_idx]["all_passed"] is True

    # 3. redteam_scenario x 6 arrive during the loop (before loop_passed, not after it)
    rt_indices = [i for i, e in enumerate(events) if e["type"] == "redteam_scenario"]
    assert len(rt_indices) == 6
    assert all(idx < loop_passed_idx for idx in rt_indices), (
        f"Expected all 6 redteam_scenario events before loop_passed ({loop_passed_idx}), got {rt_indices}"
    )

    # 4. sweep_cell x 25 -> sweep_done -> final_verification -> signoff_ready carrying artifact_hash
    sweep_cell_indices = [i for i, e in enumerate(events) if e["type"] == "sweep_cell"]
    assert len(sweep_cell_indices) == 25
    sweep_done_idx = next(i for i, e in enumerate(events) if e["type"] == "sweep_done")
    final_ver_idx = next(i for i, e in enumerate(events) if e["type"] == "final_verification")
    signoff_idx = next(i for i, e in enumerate(events) if e["type"] == "signoff_ready")

    assert loop_passed_idx < min(sweep_cell_indices)
    assert max(sweep_cell_indices) < sweep_done_idx < final_ver_idx < signoff_idx
    assert events[signoff_idx]["artifact_hash"] == artifact.artifact_hash


@pytest.mark.anyio
async def test_mock_scratch_mode_reaches_loop_passed_within_3_iterations(monkeypatch):
    """Verify scratch mode progresses v1 -> v2 -> v3 and reaches loop_passed within 3 iterations (Section 11.2)."""
    monkeypatch.setenv("MOCK_LLM", "1")
    monkeypatch.setenv("TOWERTWIN_DELAY_SCALE", "0.02")
    event_bus.clear_history()

    artifact = await orchestrator.run(mode="scratch", limits=config.get_desk_limits())
    assert artifact is not None
    assert artifact.passed

    events = event_bus.history
    loop_passed_evt = next(e for e in events if e["type"] == "loop_passed")
    assert 1 <= int(loop_passed_evt["iteration"]) <= 3

    checks_by_iter = {
        int(e["iteration"]): e for e in events if e["type"] == "checks"
    }
    assert not checks_by_iter[1]["all_passed"]
    assert not checks_by_iter[2]["all_passed"]
    assert checks_by_iter[3]["all_passed"]


@pytest.mark.anyio
async def test_review_params_failing_holdout_falls_back_to_loop_params(monkeypatch):
    """Verify Section 11.2: if REVIEW returns params that fail holdout verification, fallback to loop params."""
    monkeypatch.setenv("MOCK_LLM", "1")
    monkeypatch.setenv("TOWERTWIN_DELAY_SCALE", "0.02")
    event_bus.clear_history()

    # Inject review parameters that disable skew and widen order size so C2 fails on final verification
    bad_review_params = {
        "half_spread_ticks": 1,
        "order_size": 10,
        "skew_per_lot": 0.0,
        "soft_cap_ratio": 2.0,
        "drop_trigger_cents": 999,
        "vol_trigger_cents": 999.0,
    }
    gemini_client.test_review_params_override = bad_review_params
    try:
        limits = config.get_tightened_desk_limits()
        artifact = await orchestrator.run(mode="repair", limits=limits)
        assert artifact is not None
        assert artifact.passed
        assert artifact.used_fallback_params is True

        # Approved / verified hash must correspond to the loop fallback params, NEVER the failed candidate
        failed_hash = compute_artifact_hash(artifact.code, bad_review_params, limits)
        expected_fallback_hash = compute_artifact_hash(artifact.code, artifact.params, limits)
        assert artifact.artifact_hash == expected_fallback_hash
        assert artifact.artifact_hash != failed_hash
    finally:
        gemini_client.test_review_params_override = None
