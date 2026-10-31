"""End-to-end smoke test for the full TowerTwin demo flow (Section 11.4)."""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT.parent) not in sys.path:
    sys.path.insert(0, str(_ROOT.parent))


def _find_free_port(default: int = 8015) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if s.connect_ex(("127.0.0.1", default)) != 0:
            return default
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _print_step(step_name: str, passed: bool, elapsed: float, detail: str = "") -> None:
    status = "PASS" if passed else "FAIL"
    extra = f" ({detail})" if detail else ""
    print(f"[{status}] {step_name:<42} {elapsed:6.2f}s{extra}", flush=True)


def run_smoke(use_real_llm: bool = False) -> int:
    port = _find_free_port(8015)
    base_url = f"http://127.0.0.1:{port}"

    env = os.environ.copy()
    env["PORT"] = str(port)
    env["MOCK_LLM"] = "0" if use_real_llm else "1"

    mode_str = "REAL GEMINI (MOCK_LLM=0)" if use_real_llm else "MOCK MODE (MOCK_LLM=1)"
    print(f"=== TowerTwin E2E Smoke Test [{mode_str}] on {base_url} ===", flush=True)

    t_total_start = time.perf_counter()
    server_proc = subprocess.Popen(
        [sys.executable, "-m", "towertwin"],
        cwd=str(_ROOT.parent),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    all_ok = True
    try:
        with httpx.Client(base_url=base_url, timeout=30.0) as client:
            # Step 1: Server startup & initial desk bot verification
            t0 = time.perf_counter()
            started = False
            cfg_data: dict[str, Any] = {}
            while time.perf_counter() - t0 < 40.0:
                try:
                    r = client.get("/api/config")
                    if r.status_code == 200:
                        cfg_data = r.json()
                        started = True
                        break
                except Exception:  # noqa: BLE001
                    time.sleep(0.25)
            el1 = time.perf_counter() - t0
            _print_step(
                "1. Server startup & desk strategy verify",
                started,
                el1,
                f"artifact={cfg_data.get('artifact_id', 'none')}",
            )
            if not started:
                return 1

            # Step 2: Set position_limit = 60 and start repair run
            t0 = time.perf_counter()
            run_resp = client.post("/api/run", json={"position_limit": 60, "mode": "repair"})
            el2 = time.perf_counter() - t0
            ok2 = run_resp.status_code == 200
            _print_step("2. POST /api/run (limit=60, mode=repair)", ok2, el2)
            if not ok2:
                return 1

            # Step 3: Stream SSE until signoff_ready
            t0 = time.perf_counter()
            signoff_evt: dict[str, Any] | None = None
            sweep_cells = 0
            redteam_cards = 0
            with client.stream("GET", "/api/events", timeout=140.0) as sse:
                for line in sse.iter_lines():
                    if time.perf_counter() - t0 > 135.0:
                        break
                    if not line or not line.startswith("data:"):
                        continue
                    payload = json.loads(line[5:].strip())
                    etype = payload.get("type")
                    if etype == "redteam_scenario":
                        redteam_cards += 1
                    elif etype == "sweep_cell":
                        sweep_cells += 1
                    elif etype == "signoff_ready":
                        signoff_evt = payload
                        break
                    elif etype in ("signoff_blocked", "loop_failed"):
                        break

            el3 = time.perf_counter() - t0
            ok3 = signoff_evt is not None and bool(signoff_evt.get("artifact_hash"))
            artifact_hash = (signoff_evt or {}).get("artifact_hash", "")
            artifact_id = (signoff_evt or {}).get("artifact_id", "")
            _print_step(
                "3. Wait for signoff_ready (loop+sweep+holdout)",
                ok3,
                el3,
                f"id={artifact_id}, redteam={redteam_cards}, sweep={sweep_cells}",
            )
            if not ok3:
                return 1

            # Step 4: Approve with artifact_hash
            t0 = time.perf_counter()
            app_resp = client.post("/api/approve", json={"artifact_hash": artifact_hash})
            el4 = time.perf_counter() - t0
            ok4 = app_resp.status_code == 200 and app_resp.json().get("artifact_hash") == artifact_hash
            _print_step("4. POST /api/approve (hash-bound gate)", ok4, el4, f"id={artifact_id}")
            if not ok4:
                return 1

            # Step 5: Trigger flash crash & wait for halted (circuit breaker)
            t0 = time.perf_counter()
            crash_resp = client.post("/api/crash", json={})
            ok_crash = crash_resp.status_code == 200
            halted_seen = False
            with client.stream("GET", "/api/events", timeout=15.0) as sse:
                for line in sse.iter_lines():
                    if time.perf_counter() - t0 > 12.0:
                        break
                    if not line or not line.startswith("data:"):
                        continue
                    payload = json.loads(line[5:].strip())
                    if payload.get("type") == "halted" or (
                        payload.get("type") == "tick" and payload.get("halted") is True
                    ):
                        halted_seen = True
                        break
            el5 = time.perf_counter() - t0
            ok5 = ok_crash and halted_seen
            _print_step("5. POST /api/crash -> wait for halted", ok5, el5)
            all_ok = all_ok and ok5

            # Step 6: Trigger kill switch & verify no quotes
            t0 = time.perf_counter()
            kill_resp = client.post("/api/kill")
            ok_kill = kill_resp.status_code == 200
            killed_tick_verified = False
            with client.stream("GET", "/api/events", timeout=10.0) as sse:
                for line in sse.iter_lines():
                    if time.perf_counter() - t0 > 5.0:
                        break
                    if not line or not line.startswith("data:"):
                        continue
                    payload = json.loads(line[5:].strip())
                    if payload.get("type") == "tick" and payload.get("killed") is True:
                        killed_tick_verified = payload.get("quote") is None
                        break
            el6 = time.perf_counter() - t0
            ok6 = ok_kill and killed_tick_verified
            _print_step("6. POST /api/kill (human override)", ok6, el6)
            all_ok = all_ok and ok6

            # Step 7: Reset to idle twin
            t0 = time.perf_counter()
            reset_resp = client.post("/api/reset")
            el7 = time.perf_counter() - t0
            ok7 = reset_resp.status_code == 200
            _print_step("7. POST /api/reset", ok7, el7)
            all_ok = all_ok and ok7

        total_elapsed = time.perf_counter() - t_total_start
        within_budget = total_elapsed <= 150.0
        _print_step("TOTAL E2E SMOKE RUN (budget <= 150s)", all_ok and within_budget, total_elapsed)
        return 0 if (all_ok and within_budget) else 1

    finally:
        server_proc.terminate()
        try:
            server_proc.wait(timeout=5.0)
        except Exception:  # noqa: BLE001
            server_proc.kill()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TowerTwin E2E Smoke Verification")
    parser.add_argument("--real", action="store_true", help="Run with real Gemini models (MOCK_LLM=0)")
    args = parser.parse_args()
    sys.exit(run_smoke(use_real_llm=args.real))
