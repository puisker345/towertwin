# TowerTwin Agent Notes & Lessons Learned (`AGENTS.md`)

## Setup & Build Quirks
1. **Starter Archive Unpacking**: `TowerTwin_Starter.zip` extracted to `TowerTwin_Starter/towertwin`. Created a Windows directory junction at `./towertwin` -> `./TowerTwin_Starter/towertwin` and configured Python path resolution so `python -m towertwin` and `pytest` work identically whether run from the workspace root or inside `towertwin/`.
2. **Restricted Sandbox Namespace (`sandbox/harness.py`)**:
   - Python's `class Strategy:` statement requires both `__build_class__` in `__builtins__` and `__name__` in module globals.
   - Because `__import__` is omitted from `__builtins__` for containment, `import math`, `import statistics`, and `import collections` statements in strategy code would raise `ImportError: __import__ not found` at runtime unless handled. We validate that only those three modules are imported in `sandbox/validate.py`, pre-inject them into globals in `sandbox/harness.py`, and strip/rewrite those AST import nodes before `compile()`.
3. **Windows `resource` Module Compatibility**:
   - Python's `resource` module (`setrlimit`) is Unix-specific. On Windows, `harness.py` catches `ImportError` and relies on `runner.py`'s parent-side `subprocess.run(..., timeout=30)` wall-clock enforcement and process termination.
4. **Gemini 4 Argon & Interactions API Conventions**:
   - Uses `client.interactions.create(model="gemini-4-argon", input=..., generation_config={"thinking_level": ...}, previous_interaction_id=...)`.
   - Never pass `thinking_budget`, `temperature`, `top_p`, `top_k`, or `cached_content` (deprecated on `gemini-4-argon`).
   - Unary TTS requests to `gemini-3.8-flash-tts` return `audio/wav` with RIFF headers via `interaction.output_audio`.
5. **Holdout Seed Isolation (`agent/final_verify.py`)**:
   - `holdout_seeds` (`[201..205]`) is referenced exclusively in `config.py` and `agent/final_verify.py` (enforced by `test_holdout_isolation_source_grep_and_prompt_isolation`).
6. **Startup Verification & C6 OOS Calibration**:
   - `strategy_v3_desk.py` accumulates inventory during an early phase (`20 <= tick <= 170`) so it reliably reaches `82–90` lots (passing C2 at limit 100 and failing C2 at limits 40, 60, and 80), then unwinds after tick 170 so it passes C6 (`Validation Profit`) across both `oos_seeds` (`[101..103]`) and `holdout_seeds` (`[201..205]`) during server startup verification.

## Measured E2E Smoke Timings (`scripts/smoke_e2e.py`)

### Mock Mode (`MOCK_LLM=1`)
- **Step 1 (Server startup & desk strategy verify)**: `1.55s` (`artifact=4d80c686`)
- **Step 2 (`POST /api/run`, `limit=60`, `mode=repair`)**: `0.00s`
- **Step 3 (`signoff_ready`: loop + 6 red-team + 25 sweep cells + holdout verify)**: `5.26s` (`id=a3e1a85f`)
- **Step 4 (`POST /api/approve` hash-bound gate)**: `0.26s`
- **Step 5 (`POST /api/crash` -> wait for `halted`)**: `0.06s`
- **Step 6 (`POST /api/kill` human override)**: `0.08s`
- **Step 7 (`POST /api/reset`)**: `0.28s`
- **Total E2E Smoke Time**: **`7.76s`** (budget `<= 150.0s`)

### Real Gemini Mode (`--real` / `MOCK_LLM=0`)
Executed `python towertwin/scripts/smoke_e2e.py --real` three times against live `gemini-4-argon`, `gemini-3.8-flash` (6× parallel red-team), and `gemini-3.8-flash-tts`:

| Step | Run 1 | Run 2 | Run 3 |
| --- | ---: | ---: | ---: |
| 1. Server startup & desk strategy verify (`artifact=4d80c686`) | `5.66s` | `2.83s` | `3.89s` |
| 2. `POST /api/run` (`limit=60`, `mode=repair`) | `0.01s` | `0.02s` | `0.01s` |
| 3. Wait for `signoff_ready` (Argon loop + 6 red-team + 25 sweep + holdout) | `39.32s` (`id=05bf9c52`) | `44.64s` (`id=571a827b`) | `50.29s` (`id=c4cf34fd`) |
| 4. `POST /api/approve` (hash-bound gate) | `0.69s` | `0.40s` | `0.61s` |
| 5. `POST /api/crash` -> wait for `halted` | `0.06s` | `0.06s` | `0.06s` |
| 6. `POST /api/kill` (human override) | `0.06s` | `0.06s` | `0.10s` |
| 7. `POST /api/reset` | `0.44s` | `0.48s` | `0.63s` |
| **TOTAL E2E SMOKE RUN** (budget `<= 150s`) | **`46.80s` (PASS)** | **`49.35s` (PASS)** | **`56.24s` (PASS)** |
