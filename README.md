# TowerTwin

> **Argon plans. Python proves. You approve.**

**TowerTwin** is an interactive digital-twin market simulator and formal verification loop built for a three-person student quantitative market-making desk. When the desk tightens a risk rule—such as cutting the inventory position limit from 100 lots to 60 lots—the live desk strategy immediately violates the new rule under flash-crash stress. TowerTwin pairs **Gemini 4 Argon** (multi-step reasoning, code repair, and parameter review) and **Gemini 3.8 Flash** (parallel adversarial red-team scenario synthesis and spoken risk briefings) with a **deterministic Python proof harness** (`C1–C7`). The models propose code and stress scenarios, but **deterministic Python checks in a resource-limited subprocess grade every fill**, and **no strategy can enter the live twin without a human sign-off bound to the exact SHA-256 hash of the verified artifact**.

---

## Quick Start

```bash
# 1. Install dependencies (inside towertwin/ or workspace root)
pip install -r towertwin/requirements.txt

# 2. Configure environment (.env is pre-created; copy from .env.example if needed)
cp towertwin/.env.example towertwin/.env
# Optional: set GEMINI_API_KEY=... and MOCK_LLM=0 in towertwin/.env for live Gemini calls

# 3. Start the TowerTwin server
python -m towertwin

# 4. Open the 16:9 stage UI in your browser
# -> http://localhost:8000
```

### Running Tests & Verification Scripts

```bash
# Run all 32 unit, sandbox, calibration,holdout-isolation, orchestrator, and API tests
python -m pytest towertwin/tests -v

# Run the end-to-end smoke test (mock mode by default; pass --real for live Gemini)
python towertwin/scripts/smoke_e2e.py

# Run the headless Chromium Playwright UI check and capture towertwin/ui_check.png
python towertwin/scripts/ui_check.py
```

---

## How Mock Mode Works (and Why It Is Visibly Labelled)

When `MOCK_LLM=1` (or when `GEMINI_API_KEY` is not set and fallback is enabled), TowerTwin serves deterministic canned responses from `towertwin/mocks/`:
- **Card 0 (`strategy_v3_desk.py`)**: The real desk strategy, which passes all checks `C1–C7` at `position_limit = 100` on startup, and genuinely fails `C2` (`Position Limit`) when re-evaluated at `position_limit = 60`.
- **Iteration 1 (`strategy_v4_repaired.py`)**: Reads `limits["position_limit"]`, applies a configurable `safety_buffer`, tapers order sizes near the cap, and passes all checks `C1–C7` across in-sample, validation, red-team, and untouched holdout seeds.
- **Why it is labelled**: Whenever `MOCK_LLM=1` is active, the top bar displays a high-contrast **`MOCK MODE`** badge and the server emits a `mock_mode` SSE event so judges and operators always know whether proposals came from live Gemini API calls or deterministic mock fixtures. Even in mock mode, **every single backtest, check `C1–C7`, 5×5 parameter sweep, and holdout verification runs for real in the sandbox subprocess**.

---

## Architecture & Model Roles

```text
+-----------------------------------------------------------------------------------+
|                         Browser UI (16:9 Dark Theme, Three.js)                    |
|  [3D Order-Book Terrain]  [Loop Ring]  [Checks C1-C7]  [Timeline/Diffs]  [5x5]    |
+-----------------------------------------+-----------------------------------------+
                                          | REST (/api/run, /api/approve, /api/kill)
                                          | SSE  (/api/events @ 20 Hz ticks + loop)
+-----------------------------------------v-----------------------------------------+
|                           FastAPI Server (`towertwin/app.py`)                     |
|                                                                                   |
|  +----------------------------------+     +------------------------------------+  |
|  | Repair & Verification Loop       |     | Live Digital Twin (`live/engine`)  |  |
|  | (`agent/orchestrator.py`)        |     | - 20 ticks/sec OU + Jump-Diffusion |  |
|  |  1. Card 0: Re-check desk bot    |     | - Startup: verifies v3_desk @ 100  |  |
|  |  2. Argon PLAN / PROPOSE / REFINE|     | - Hash-bound `/api/approve` gate   |  |
|  |  3. Flash Red-Team (6 scenarios) |     | - Flash Crash & Human Kill Switch  |  |
|  |  4. 5x5 Sweep + Argon REVIEW     |     +-----------------+------------------+  |
|  |  5. Final Verify (Holdout Seeds) |                       |                     |
|  +-----------------+----------------+                       |                     |
+--------------------|----------------------------------------|---------------------+
                     | Subprocess JSON IPC                    | Subprocess IPC
+--------------------v----------------------------------------v---------------------+
|             Isolated Sandbox Harness (`sandbox/validate.py` + `harness.py`)       |
|  - AST Whitelist Validator (no open/exec/eval/getattr/__subclasses__/OS imports)  |
|  - Restricted `__builtins__` + pre-injected `Quote`, `math`, `statistics`         |
|  - Separate OS process with CPU/RAM rlimits + 30s parent wall-clock timeout       |
|  - Deterministic Exchange Simulator (`sim/market.py`) + Checks (`checks.py`)      |
+-----------------------------------------------------------------------------------+
```

### Model Role Table

| Role | Model | Thinking Level | Purpose |
| :--- | :--- | :--- | :--- |
| **Planner / Code Repair / Reviewer** | `gemini-4-argon` | `medium` (`high` after 2 consecutive failures on the same check; `low` for parameter review) | Diagnoses failing deterministic checks (`C1–C7`), proposes/refines `<300`-line Python `Strategy` classes, and writes the final Markdown sign-off report + spoken briefing text. |
| **Adversarial Red-Team Generator** | `gemini-3.8-flash` | `low` | Runs **in parallel** with the repair loop to synthesize 6 adversarial market crash/liquidity-drought scenarios (`Scenario` JSON schema), clamped to hard bounds. |
| **Spoken Risk Briefing** | `gemini-3.8-flash-tts` | — | Synthesizes a 2-sentence spoken risk briefing (`24 kHz` mono WAV) at sign-off, with automatic fallback to browser `speechSynthesis` and procedural chime WAV. |

---

## The 3-Minute Stage Demo Script

1. **`0:00 – 0:25` · The Live Desk Twin**:
   - Point to the 3D order-book terrain (`twin3d.js`): cool-blue bid depth on the left, warm-amber ask depth on the right, glowing cyan mid-price spine, and bright green bot quote markers pulsing as fills create expanding ripple rings.
   - Note the top status pill: **`Running: desk strategy · verified at limit 100`**. All 7 check pills (`C1–C7`) are green.
2. **`0:25 – 0:50` · Tightening the Desk Risk Rule**:
   - Drag the **Desk Position Limit** slider from `100 lots` down to **`60 lots`** and click **Run Repair**.
   - **Card 0 (`Current desk strategy`)** immediately runs in the sandbox under the new 60-lot rule and turns **RED (`6/7 PASS`)**: `C2` (`Position Limit`) fails because `strategy_v3_desk` hard-codes a 90-lot inventory cap (`|position|` reaches 89 lots > 60).
3. **`0:50 – 1:40` · Argon Repair Loop + Parallel Flash Red-Team**:
   - While **Gemini 4 Argon** inspects the exact tick and fill index from `C2`'s failure detail and proposes a repaired strategy (`Iteration 1`), **Gemini 3.8 Flash** streams 6 adversarial red-team scenario cards along the bottom of the centre view.
   - `Iteration 1` runs in the isolated sandbox subprocess and turns all 7 check pills **GREEN (`7/7 PASS`)**.
4. **`1:40 – 2:10` · 5×5 Parameter Sweep & Untouched Holdout Proof**:
   - The bottom-right **5×5 Parameter Sweep Heatmap** fills in live across `SWEEP_PARAMS` (`skew_bps_per_lot` × `vol_halt_threshold`) over the baseline + 6 red-team scenarios, outlining the optimal cell.
   - Argon reviews the heatmap, and `agent/final_verify.py` runs final verification on the **5 untouched holdout seeds (`[201..205]`)**.
5. **`2:10 – 2:40` · Spoken Briefing & Hash-Bound Human Sign-Off**:
   - The **Desk Risk Sign-Off Modal** opens automatically with the 8-character **Artifact ID**, full **SHA-256 hash**, verification summary (`7/7 checks · 3 in-sample + 5 untouched holdout seeds`), and auto-plays the spoken risk briefing.
   - Click **Approve & Swap Live Strategy**. The server verifies the SHA-256 hash matches the stored verified artifact and hot-swaps the repaired strategy into the live twin.
6. **`2:40 – 3:00` · Live Flash Crash & Human Kill Switch**:
   - Click **Trigger Flash Crash**. The 3D terrain tints red, thins sharply, shakes subtly, and the bot's circuit breaker halts quoting within ≤3 ticks (**`HALTED · circuit breaker`** banner).
   - Click **Kill Switch** to demonstrate immediate human override (**`KILLED BY HUMAN`**—all quotes drop to `None`).

---

## Deterministic Proof Checks (`C1–C7`) & Seed Sets

### Formal Checks (`towertwin/checks/checks.py`)

| ID | Check Name | Rule Verified |
| :--- | :--- | :--- |
| **C1** | **Books Reconcile** | Exchange ledger cash, fees, position, and mark-to-market PnL in integer cents match fill arithmetic with `0 ¢` discrepancy across all runs. |
| **C2** | **Position Limit (Every Fill)** | `\|position\| <= limits["position_limit"]` after **every individual fill** (catches intra-tick breaches such as `99 → 101 → 99`) and every tick. |
| **C3** | **Order Size** | Every submitted bid and ask size satisfies `0 <= size <= limits["max_order_size"]` (`10 lots`) and quotes never cross (`bid_price < ask_price`). |
| **C4** | **Max Drawdown** | Peak-to-trough mark-to-market drawdown stays `<= limits["max_drawdown"]` (`25,000 ¢` / `$250.00`) on every run. |
| **C5** | **Crash Circuit Breaker** | During the crash window, the strategy halts quoting (`bid_price=None, ask_price=None`) within `<= 5 ticks` of crash start and stays halted for `>= 20 ticks`. |
| **C6** | **Validation Profit** | Final mark-to-market PnL on **every** unseen validation seed (`oos_seeds`) is strictly positive (`> 0 ¢`). |
| **C7** | **Sandbox Clean & Bounded** | AST validation passes, strategy executes in restricted subprocess with `0` exceptions, `0` timeouts (`<= 30s`), and `<= 5.0 ms` p99 `on_tick` latency. |

### Three Disjoint Seed Sets (`towertwin/config.py`)

| Seed Set | Seeds | Purpose |
| :--- | :--- | :--- |
| **In-Sample Seeds** (`in_sample_seeds`) | `[1, 2, 3]` | Used during the iterative repair loop (`C1–C5`, `C7`) and the 5×5 parameter sweep. |
| **Validation / OOS Seeds** (`oos_seeds`) | `[101, 102, 103]` | Unseen during parameter tuning; used by check **`C6`** (`Validation Profit`) to prevent overfitting. |
| **Untouched Holdout Seeds** (`holdout_seeds`) | `[201, 202, 203, 204, 205]` | Referenced **only** in `config.py` and `agent/final_verify.py` (enforced by unit test grep). Never exposed to prompts or calibration; used once at final sign-off verification. |

---

## Safety & Honesty Notes

- **Deterministic verification**: All checks `C1–C7` are pure Python functions over simulator telemetry. The LLM never grades its own work.
- **Untouched holdout isolation**: `holdout_seeds` (`[201..205]`) are isolated to `agent/final_verify.py` and never appear in repair prompts. If Argon's `REVIEW` parameters fail holdout verification, `final_verify.py` automatically falls back to the loop-verified parameters and re-verifies.
- **Hash-bound server approval gate**: `compute_artifact_hash(code, params, limits)` computes a canonical SHA-256 digest over stripped strategy code, sorted JSON parameters, and sorted desk limits. `POST /api/approve` rejects (`HTTP 409`) any request before `signoff_ready` or any hash mismatch. What is approved is byte-for-byte what was proven.
- **Kill switch**: `POST /api/kill` immediately unloads the live strategy worker and forces all quotes to `None` until human reset.
- **Subprocess containment**: Generated strategy code never executes inside the FastAPI server process. Both backtests and live ticks run in separate child processes (`python -m towertwin.sandbox.harness`). Note: the AST validator (`sandbox/validate.py`) and restricted `__builtins__` namespace are guards against accidental misuse, **not** a kernel security boundary; real containment comes from the separate OS process, `resource.setrlimit` CPU/memory caps (where supported by the OS), scrubbed environment variables, and parent-enforced wall-clock timeouts.
- **Consented voice only**: Voice cloning is disabled unless `reference/voice_sample.wav` is explicitly present (`Voice: replicated with consent`).
- **Simulation only**: All prices and cash flows are simulated integers in cents. TowerTwin has no external exchange connectors and touches no real money.
