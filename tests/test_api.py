"""REST API and live engine approval-gate / kill-switch tests (SPEC Section 11.2)."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from towertwin.app import app
from towertwin.checks.checks import compute_artifact_hash
from towertwin.events import event_bus
from towertwin.live.engine import live_engine


@pytest.mark.anyio
async def test_api_approval_gate_and_kill_switch(monkeypatch):
    """Test 409 before signoff, 409 with wrong hash, 200 with verified hash, and kill switch."""
    monkeypatch.setenv("MOCK_LLM", "1")
    monkeypatch.setenv("TOWERTWIN_DELAY_SCALE", "0.02")

    await live_engine.initialize_startup_bot()
    live_engine.set_pending_artifact(None)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # 1. Config endpoint works
        cfg_resp = await client.get("/api/config")
        assert cfg_resp.status_code == 200
        cfg_data = cfg_resp.json()
        assert cfg_data["mock_llm"] is True

        # 2. /api/approve BEFORE signoff_ready must return 409
        pre_resp = await client.post("/api/approve", json={"artifact_hash": "deadbeef" * 8})
        assert pre_resp.status_code == 409

        # 3. Run repair mode with position_limit = 60 and wait for completion
        run_resp = await client.post(
            "/api/run",
            json={"position_limit": 60, "mode": "repair", "wait": True},
        )
        assert run_resp.status_code == 200
        run_data = run_resp.json()
        assert run_data["passed"] is True
        verified_hash = run_data["artifact_hash"]
        assert isinstance(verified_hash, str) and len(verified_hash) == 64

        # 4. /api/approve with wrong hash must return 409
        wrong_resp = await client.post("/api/approve", json={"artifact_hash": "0" * 64})
        assert wrong_resp.status_code == 409

        # 5. /api/approve with the exact verified hash returns 200 and emits `approved`
        ok_resp = await client.post("/api/approve", json={"artifact_hash": verified_hash})
        assert ok_resp.status_code == 200
        ok_data = ok_resp.json()
        assert ok_data["artifact_hash"] == verified_hash

        # Verify `approved` event was emitted and live engine's loaded code & params hash to the same value
        assert any(e["type"] == "approved" and e["artifact_hash"] == verified_hash for e in event_bus.history)
        live_computed_hash = compute_artifact_hash(
            live_engine.loaded_code,
            live_engine.loaded_params,
            live_engine.limits,
        )
        assert live_computed_hash == verified_hash

        # 6. Stale repeat approval after artifact was consumed returns 409
        stale_resp = await client.post("/api/approve", json={"artifact_hash": verified_hash})
        assert stale_resp.status_code == 409

        # 7. Briefing audio endpoint returns valid RIFF WAV bytes
        wav_resp = await client.get("/api/briefing.wav")
        assert wav_resp.status_code == 200
        assert wav_resp.headers["content-type"].startswith("audio/wav")
        assert wav_resp.content.startswith(b"RIFF")

        # 8. Crash trigger endpoint
        crash_resp = await client.post("/api/crash", json={})
        assert crash_resp.status_code == 200
        assert any(e["type"] == "crash_started" for e in event_bus.history)

        # 9. Kill switch: after /api/kill, no `tick` event contains a bot quote
        kill_resp = await client.post("/api/kill")
        assert kill_resp.status_code == 200
        assert live_engine.killed is True
        for _ in range(5):
            tick_evt = live_engine.step_once()
            assert tick_evt["quote"] is None
            assert tick_evt["killed"] is True

        # 10. Reset restores the desk strategy
        reset_resp = await client.post("/api/reset")
        assert reset_resp.status_code == 200
        assert live_engine.killed is False
        await live_engine.stop()
