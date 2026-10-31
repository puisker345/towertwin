# TowerTwin Build Plan (`PLAN.md`)

## 1. Build Order (Milestones M1–M9 Strictly in Order)

Each milestone is built and verified with its Section 11 tests before starting the next milestone.

1. **M1 — Core (`sim/`, `checks/`, `mocks/`, `config.py`)**
   - `config.py`: All default simulator parameters, desk rules (`position_limit=100`, `max_order_size=10`, `max_drawdown=25000`, `halt_within_ticks=5`, `calm_ticks_to_resume=100`), seeds (`in_sample_seeds=[1,2,3]`, `oos_seeds=[101,102,103]`, `holdout_seeds=[201,202,203,204,205]`), and env-overridable flags (`MOCK_LLM`, `ARGON_THINKING`, `VOICE_SAMPLE`, `SANDBOX`).
   - `sim/types.py`: Frozen dataclasses `Quote`, `Fill`, `MarketState`.
   - `sim/ledger.py`: Exchange-side integer-cent ledger (`buy`/`sell` fills, exact mark-to-market `pnl(mid)`).
   - `sim/scenarios.py`: `Scenario` dataclass, `BASELINE_CRASH`, `JSON_SCHEMA`, `BOUNDS`, `validate_and_clamp_scenario()`, and `fit_to_length(scenario, ticks)`.
   - `sim/market.py`: Deterministic order-flow simulator using `numpy.random.Generator(PCG64(seed))`, intra-tick seeded fill ordering, per-fill telemetry (`tick`, `fill_index`, `position`), per-tick max `|position|`, no look-ahead in `MarketState`.
   - `checks/checks.py`: Deterministic checks `C1`–`C7` and `CheckReport` with exact human-readable failure sentences (tick, fill index, numbers) and `artifact_hash`.
   - `mocks/`:
     - `strategy_v1_naive.py` (symmetric quoting, no inventory control, no crash halt → fails C2, C5 at limit 100)
     - `strategy_v2_skew.py` (inventory skew + hard cap, no crash halt → passes C2, fails C5 at limit 100)
     - `strategy_v3_desk.py` (desk strategy: skew + crash volatility/drift halt + fixed `PARAMS["max_inventory"] = 90` → passes C1–C7 at limit 100, fails C2 at limits 40, 60, 80)
     - `strategy_v4_repaired.py` (derives inventory cap from `limits["position_limit"] - safety_buffer` with size tapering + crash halt → passes C1–C7 at limits 40, 60, 80, 100)
     - `redteam_scenarios.json` & `signoff_report.md`
   - **Tests**: `tests/test_ledger.py`, `tests/test_market.py`, `tests/test_checks.py`, `tests/test_calibration.py`.

2. **M2 — Sandbox (`sandbox/validate.py`, `sandbox/harness.py`, `sandbox/runner.py`)**
   - `sandbox/validate.py`: AST static validator enforcing ≤ 300 lines, allowed imports (`math`, `statistics`, `collections`), forbidden names/builtins and dunder attribute access, required `PARAMS`, `SWEEP_PARAMS`, and `class Strategy` methods (`__init__`, `on_tick`, `on_fill`, `report`).
   - `sandbox/harness.py`: Subprocess entry point (`python -m towertwin.sandbox.harness`), applies `resource.setrlimit` where supported, strips allowed import statements from AST before compiling so code runs without `__import__`, injects `Quote`, `math`, `statistics`, `collections`, `__name__ = "strategy"`, and restricted `__builtins__` (with `__build_class__`).
   - `sandbox/runner.py`: Runs backtest batches in isolated child subprocesses over JSON stdin/stdout with wall-clock timeout (30s) and kills runaway processes cleanly, reporting C7 failure.
   - **Tests**: `tests/test_validate.py`, `tests/test_runner.py`.

3. **M3 — Agent Loop, Holdout Final Verification, Event Bus & API (`events.py`, `agent/`, `app.py`, `__main__.py`)**
   - `events.py`: Thread/async-safe pub/sub event bus streaming JSON SSE events (`{"type", "ts", ...}`).
   - `agent/prompts.py`: Prompt templates for `REPAIR`, `PROPOSE`, `REFINE`, `RED_TEAM`, and `REVIEW`.
   - `agent/gemini_client.py`: Thin wrapper over `google-genai` Interactions API (`client.interactions.create` with `model="gemini-4-argon"`, `generation_config={"thinking_level": ...}`, `previous_interaction_id`, `model="gemini-3.8-flash"`, `model="gemini-3.8-flash-tts"`) and full `MOCK_LLM=1` backend.
   - `agent/final_verify.py`: Computes `artifact_hash = sha256(code + canonical_json(params) + canonical_json(limits))`, runs C1–C7 on `in_sample_seeds` + `holdout_seeds` (`[201..205]`), falls back to loop params if review params fail holdout, emits `final_verification` and `signoff_ready` / `signoff_blocked`.
   - `agent/orchestrator.py`: Repair mode (re-checks desk strategy at iteration 0 → Argon repair loop) and Scratch mode (`v1` → `v2` → `v3`), parallel red-team + sweep hookup, review + final verification.
   - `app.py` & `__main__.py`: FastAPI REST + SSE server on `:8000` with hash-bound `/api/approve` (409 on missing/wrong/stale hash).
   - **Tests**: `tests/test_orchestrator_mock.py`, `tests/test_api.py` (including holdout isolation grep test and artifact hash sensitivity test).

4. **M4 — Minimal UI (`web/index.html`, `web/style.css`, `web/app.js`, `web/panels.js`)**
   - Top bar with TowerTwin wordmark, tagline, MOCK badge, active stage indicator.
   - Right column: SVG/canvas loop ring (Propose · Run sandbox · Check proof · Refine + Approve), C1–C7 checks panel with animated pills and hover details, iteration timeline starting at Card 0 ("Current desk strategy") with expandable line-based code diffs.
   - Bottom controls: position limit slider (40–100), Repair/From scratch toggle, Run, Approve, Trigger flash crash, Kill switch, Reset, PnL & inventory sparklines, 2D canvas chart fallback.
   - Sign-off modal with markdown report, final verification summary, 8-char artifact hash ID, Play briefing button, voice label, Approve & Reject buttons.

5. **M5 — Red Team & Parameter Sweep Heatmap (`agent/redteam.py`, `web/panels.js`)**
   - 6 parallel `gemini-3.8-flash` red-team scenario calls launched immediately when `/api/run` starts; validated, clamped, and fitted via `fit_to_length(..., sweep_ticks)`.
   - 5×5 parameter grid over `SWEEP_PARAMS` (±50% around current values, integers stay integers), evaluated in parallel across `(baseline + 6 red-team scenarios) × in_sample_seeds` with crash containment assertion.
   - Live streaming `redteam_scenario`, `sweep_cell` (×25), and `sweep_done` events rendered into the UI cards and 5×5 canvas heatmap.

6. **M6 — Live Engine & 3D Twin (`live/engine.py`, `web/twin3d.js`, `scripts/smoke_e2e.py`)**
   - Startup final verification of `strategy_v3_desk` at limit 100; runs at ~20 ticks/s streaming `tick` events with synthesized resting depth.
   - Server-enforced approval gate swaps to the newly verified artifact on `/api/approve`; supports `/api/crash`, `/api/kill`, and `/api/reset`.
   - Three.js 3D order-book terrain mesh (±40 ticks × 120 time rows), glowing mid line, scaled quote markers, fill ripple rings, crash red tint + camera shake + depth thinning, floating `HALTED · circuit breaker` banner, and 2D fallback toggle.
   - End-to-end verification via `scripts/smoke_e2e.py`.

7. **M7 — Voice Briefing with Fallbacks (`agent/briefing.py`, `web/audio.js`)**
   - `gemini-3.8-flash-tts` audio generation with optional consented `VOICE_SAMPLE` replication, falling back to prebuilt TTS / synthesized WAV at `GET /api/briefing.wav` and browser `speechSynthesis`.

8. **M8 — Polish, Sonar Audio, UI Check & Documentation (`web/audio.js`, `scripts/ui_check.py`, `README.md`)**
   - WebAudio API fill sonar ping (muted by default, pitch scales with realized volatility).
   - High-contrast lecture-hall typography (≥18px key numbers) and smooth animations.
   - Playwright verification via `scripts/ui_check.py` producing `ui_check.png`.
   - Complete `README.md` with pitch, quick start, architecture, 3-minute stage script, checks & seed table, and safety notes.

9. **M9 — Rehearsal & Timing Logs (`AGENTS.md`, `scripts/smoke_e2e.py`)**
   - Record smoke test timings (both `MOCK_LLM=1` and 3× `--real` with `MOCK_LLM=0`) and setup lessons in `AGENTS.md`. All 3 real runs completed in `46.80s`, `49.35s`, and `56.24s` (well within the `<= 150s` budget).

---

## 2. Repository File Tree

```
towertwin/
  __init__.py
  __main__.py
  config.py
  app.py
  events.py
  sim/
    __init__.py
    types.py
    ledger.py
    scenarios.py
    market.py
  checks/
    __init__.py
    checks.py
  sandbox/
    __init__.py
    validate.py
    harness.py
    runner.py
  agent/
    __init__.py
    gemini_client.py
    prompts.py
    orchestrator.py
    redteam.py
    final_verify.py
    briefing.py
  live/
    __init__.py
    engine.py
  mocks/
    __init__.py
    strategy_v1_naive.py
    strategy_v2_skew.py
    strategy_v3_desk.py
    strategy_v4_repaired.py
    redteam_scenarios.json
    signoff_report.md
  web/
    index.html
    style.css
    app.js
    twin3d.js
    panels.js
    audio.js
  tests/
    conftest.py
    test_ledger.py
    test_market.py
    test_checks.py
    test_validate.py
    test_runner.py
    test_calibration.py
    test_orchestrator_mock.py
    test_api.py
  scripts/
    smoke_e2e.py
    ui_check.py
  PLAN.md
  ASSUMPTIONS.md
  AGENTS.md
  README.md
  requirements.txt
  .env.example
```
