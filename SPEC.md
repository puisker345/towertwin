# TowerTwin: one-shot build spec for Gemini 4 Argon (v2)

> **How to use this file:** paste the whole document into Gemini 4 Argon (AI Studio Build, Antigravity, or your coding agent with the `gemini-api-dev` skill installed). Use `thinking_level: "high"` for the build. Everything Argon needs is below.

> **What changed in v2:** the demo now starts from a verified desk strategy that a judge breaks by tightening a rule, and Argon repairs it (Section 2). Approval is bound to a hash of the exact code and parameters that passed, after a final re-verification on untouched holdout seeds (Section 8.6). Position limits are checked after every fill. Sweep runs always contain their crash. The sandbox namespace can now actually load a class. Real Gemini integration is inside the cut line (Section 12).

---

## 0. Instructions to Argon (read first)

You are the lead engineer building **TowerTwin** in one run, at a 5-hour hackathon (Google DeepMind × Imperial College London, Track 1: Frontier Agents). Work autonomously. Do not stop to ask questions.

**How to work:**
1. Read this entire document before writing code.
2. Write `PLAN.md`: your build order, the file tree, and any assumption you had to make. Record assumptions in `ASSUMPTIONS.md` and move on; don't wait for answers.
3. Build in the milestone order in Section 12. **Each milestone must run and pass its tests before you start the next.** If you run short on time, a finished earlier milestone beats a half-finished later one.
4. Verify your own work: write the tests in Section 11 first for each module, run them, and iterate until they pass with zero regressions. Never mark something done without running it.
5. When you hit a non-obvious setup or build quirk, write the lesson into `AGENTS.md`.
6. Use current Gemini API conventions only (Section 9). Do not use deprecated parameters.
7. Keep everything honest: nothing in the demo may be faked. Mock mode exists for offline development and as a stage backup, and it must be visibly labelled "MOCK" in the UI whenever it is on.
8. If API credits are not available yet, build against mock mode first, then switch to real Gemini calls as soon as a key exists. The real repair loop is part of the minimum demo, not a stretch goal.

**Definition of done:** `python -m towertwin` starts a server at `http://localhost:8000`. With `GEMINI_API_KEY` set and `MOCK_LLM=0`, the full demo flow works end to end on real Gemini calls: a judge tightens a desk rule → checks go red on the current strategy → Argon repairs it (red to green) → final re-verification on holdout seeds → sign-off briefing → hash-bound approval → live 3D twin with flash-crash and kill-switch buttons. The same flow also works in mock mode with no API key. All tests in Section 11 pass.

---

## 1. Product summary

**TowerTwin** is a digital twin of a trading market for a student market-making desk. An AI agent writes a market-making strategy, a deterministic simulator stress-tests it (including a flash crash), Python checks prove whether it is safe, and the agent refines the strategy until every check passes. A human then reviews a spoken briefing and approves before the bot is allowed to trade in the live twin.

- **Tagline:** "Argon plans. Python proves. You approve."
- **Named user:** the head of the market making desk at Queen's Tower Exchange (QTE), Imperial's student trading simulation. Today, writing and safely testing a new quoting strategy takes days of write, backtest and fix cycles.
- **Judging criteria to satisfy (from the organisers):**
  - **Specificity:** one sharp problem for one real person.
  - **Alchemy:** expressive voice + live market simulation.
  - **Rigor:** the system visibly catches a mistake, runs a test, and keeps a human in control.
  - **Courage:** zero slides; the live product is on screen within the first 10 seconds.

---

## 2. The 3-minute stage demo (build for exactly this)

The demo is a **repair**, not a build from scratch. The desk already has a verified strategy (the "desk strategy", `mocks/strategy_v3_desk.py`, verified at a 100-lot position limit). A judge changes a desk rule, the strategy breaks under the new rule, and Argon repairs it. This is honest (nothing is told to fail) and saves an iteration.

The whole product exists to make this sequence work reliably:

1. **0:00:** the 3D twin is already running with the desk strategy quoting live. "This is our desk's strategy, verified at a 100-lot limit."
2. **0:10:** a judge drags the **position limit slider** (allowed range 40–100) and the presenter clicks **Run**. The current strategy is re-checked in the sandbox under the new rule (no model call, about 3 s). **Position limit** goes **red** with exact numbers, e.g. "Position reached +87 lots at tick 2,016 (limit 60)." At the same moment, the Flash red-team cards start landing in parallel (they don't depend on the strategy).
3. **0:20:** Argon repairs the strategy. The loop ring lights *Propose → Run sandbox → Check proof → Refine*; the rationale and a code diff appear.
4. **~1:00:** checks turn green. (If they don't, a second iteration uses the 30-second buffer.)
5. **1:05:** the robustness sweep runs across the red-team scenarios and fills the heatmap (≤ 20 s), while the presenter narrates what was checked.
6. **1:30:** **final verification**: the exact code and parameters chosen for approval are re-run on untouched holdout seeds; the artifact hash appears. The sign-off modal opens and a 15-second voice briefing plays (the presenter's consented cloned voice if available).
7. **1:55:** presenter clicks **Approve** (bound to that hash). The repaired bot quotes live in the 3D twin. Presenter clicks **Trigger flash crash**: terrain turns red, sonar pitch rises, the circuit breaker halts quoting, inventory stays within the new limit. **Kill switch** visible.
8. **2:30–3:00:** close: "Argon repaired it, Python proved it, and I can only approve exactly what was proven." Buffer for a second iteration.

Timing targets: re-check of the current strategy ≤ 5 s; each Argon iteration ≤ 35 s at `thinking_level: "medium"`; sweep ≤ 20 s; final verification ≤ 10 s; briefing ≤ 15 s. Mock mode: each model step ≤ 2 s.

A **"Build from scratch"** mode (naive baseline → robust strategy over several iterations) stays available for rehearsal and Q&A, but it is not the stage path.

---

## 3. Architecture

```
Browser (web/)                              Python backend (towertwin/)
┌──────────────────────────────┐   SSE     ┌───────────────────────────────────────┐
│ 3D twin (Three.js)           │◄──────────│ FastAPI app + event bus               │
│ Loop ring, checks panel,     │           │                                       │
│ iteration timeline, diff,    │  REST     │ agent/orchestrator  ── Argon (brain)  │
│ heatmap, controls, briefing  │──────────►│ agent/redteam       ── Flash (parallel)│
└──────────────────────────────┘           │ agent/briefing      ── Flash TTS      │
                                           │                                       │
                                           │ sandbox/validate + runner (subprocess)│
                                           │ sim/market + ledger + scenarios       │
                                           │ checks/checks  (deterministic proof)  │
                                           │ live/engine (real-time twin + gate)   │
                                           └───────────────────────────────────────┘
```

**Key principle:** the Python simulator is the single source of truth. The 3D view only renders events produced by the backend (backtest replays and the live engine). The visuals and the checks can never disagree.

**Roles of each model:**

| Role | Model | Job |
| --- | --- | --- |
| Brain (thinker) | `gemini-4-argon` | Writes and refines the strategy code; reviews sweep results; writes the sign-off report |
| Red team (parallel) | `gemini-3.8-flash` | Each call proposes one adversarial stress scenario as JSON, in parallel |
| Performer (voice) | `gemini-3.8-flash-tts` | Reads the sign-off briefing aloud |
| Verifier | Python sandbox + `checks.py` | Runs backtests and deterministic checks. Never the model's opinion |

---

## 4. Repository layout

```
towertwin/
  __main__.py            # `python -m towertwin` → starts uvicorn on :8000
  config.py              # all limits, sim params, seeds, feature flags (env-overridable)
  app.py                 # FastAPI: REST endpoints, SSE stream, static files
  events.py              # in-process pub/sub event bus → SSE
  sim/
    market.py            # deterministic order-flow simulator
    scenarios.py         # Scenario dataclass, baseline + flash-crash scenario, JSON schema + bounds
    ledger.py            # exchange-side ledger (integer cents) — the ground truth books
    types.py             # MarketState, Quote, Fill dataclasses
  checks/
    checks.py            # C1–C7 deterministic checks → CheckReport
  sandbox/
    validate.py          # AST static validation of generated strategy code
    runner.py            # run backtests in an isolated subprocess with timeouts/limits
    harness.py           # code executed inside the subprocess (loads strategy, runs sim)
  agent/
    gemini_client.py     # thin wrapper over google-genai Interactions API + mock backend
    prompts.py           # all prompt templates (Section 8)
    orchestrator.py      # the propose → run → check → refine loop
    redteam.py           # parallel Flash scenario generation + param sweep
    final_verify.py      # re-verify the exact approval candidate on holdout seeds + artifact hash
    briefing.py          # sign-off report → TTS audio (with fallbacks)
  live/
    engine.py            # real-time twin: ticks at ~20/s, approval gate, crash + kill controls
  mocks/
    strategy_v1_naive.py     # symmetric quoting, no inventory control, no halt → must FAIL C2, C5 (scratch mode)
    strategy_v2_skew.py      # inventory skew, no crash halt → passes C2 but FAILS C5 (scratch mode)
    strategy_v3_desk.py      # the desk's current strategy: skew + volatility halt, with its OWN fixed
                             # inventory cap PARAMS["max_inventory"] = 90 (it does NOT read the desk
                             # limit) → passes all at limit 100, FAILS C2 at limits 40–80
    strategy_v4_repaired.py  # v3 with the inventory cap derived from limits["position_limit"] minus a
                             # safety buffer, and size tapering near the cap → passes all at 40–100
    redteam_scenarios.json   # canned Flash outputs for mock mode
    signoff_report.md        # canned review for mock mode
web/
  index.html  style.css  app.js  twin3d.js  panels.js  audio.js
tests/
  test_ledger.py  test_market.py  test_checks.py  test_validate.py
  test_runner.py  test_calibration.py  test_orchestrator_mock.py  test_api.py
scripts/
  smoke_e2e.py           # headless end-to-end run of the demo flow in mock mode
  ui_check.py            # optional Playwright check: page loads, loop goes green, screenshot
PLAN.md  ASSUMPTIONS.md  AGENTS.md  README.md  requirements.txt  .env.example
```

`requirements.txt`: `fastapi`, `uvicorn[standard]`, `pydantic>=2`, `google-genai>=2.3.0`, `numpy` (sim and sweep only, never inside strategy code), `pytest`, `httpx` (tests), optionally `playwright`.

`.env.example`:
```
GEMINI_API_KEY=
MOCK_LLM=1            # 1 = canned model outputs (offline), 0 = real Gemini
ARGON_THINKING=medium # "medium" for the loop (speed), "high" for the review step
VOICE_SAMPLE=         # optional path to a consented voice sample for replication
SANDBOX=local         # local subprocess sandbox (default); "antigravity" = stretch goal
```

---

## 5. The market simulator (`sim/`)

Simple, fast and **fully deterministic given a seed**. Realism is secondary to repeatability. Use integer cents everywhere for prices and money so reconciliation can be exact to the penny.

### 5.1 Types (`sim/types.py`)

```python
@dataclass(frozen=True)
class Quote:
    bid_price: int | None   # cents; None = no bid
    bid_size: int           # lots, >= 0
    ask_price: int | None   # cents; None = no ask
    ask_size: int

@dataclass(frozen=True)
class Fill:
    tick: int
    side: str               # "buy" (we bought, hit on our bid) or "sell" (we sold, lifted on our ask)
    price: int              # cents
    size: int               # lots
    fee: int                # cents charged to us

@dataclass(frozen=True)
class MarketState:
    tick: int
    mid: int                         # cents, current mid
    recent_mids: tuple[int, ...]     # last 50 mids, oldest first (past only, never future)
    position: int                    # our position per exchange ledger (lots, signed)
    recent_trades: tuple[tuple[int, str, int], ...]  # last 20 market trades: (price, aggressor_side, size)
    limits: dict                     # the desk rules (position_limit, max_order_size, ...)
```

### 5.2 Default parameters (`config.py`, all overridable)

| Parameter | Default | Notes |
| --- | --- | --- |
| `ticks` | 3000 | backtest length |
| `start_mid` | 10_000 | cents (= 100.00) |
| `tick_size` | 1 | cent |
| `sigma` | 2.0 | std dev of mid change per tick, in cents (round to int) |
| `arrival_rate` | 0.8 | Poisson rate of market orders per side per tick |
| `order_size` | uniform 1–5 | lots per market order |
| `fill_k` | 0.35 | fill probability = `exp(-fill_k * distance_ticks)` where distance = |quote − mid| in ticks, capped at 1.0 |
| `fee_per_lot` | 1 | cent per lot filled |
| `crash.start` | 2000 | tick the crash begins (baseline scenario) |
| `crash.depth_pct` | 6.0 | mid falls this % over `crash.duration` ticks |
| `crash.duration` | 30 | ticks |
| `crash.sigma_mult` | 5.0 | volatility multiplier during and for 200 ticks after the crash |
| `crash.sell_flow_mult` | 4.0 | sell market-order rate multiplier during the crash (adverse selection) |
| `crash.buy_flow_mult` | 0.3 | buy rate multiplier during the crash |
| `crash.recovery_pct` | 40 | % of the drop recovered linearly over the following 300 ticks |
| `in_sample_seeds` | [1, 2, 3] | shown to Argon in full detail |
| `oos_seeds` | [101, 102, 103] | validation seeds. Argon sees only pass/fail during the loop. Because that pass/fail is fed back, these are **not** a true held-out test |
| `holdout_seeds` | [201, 202, 203, 204, 205] | **untouched test set.** Never used in calibration tests, prompts, the loop or the sweep. Used exactly once per run, in final verification (Section 8.6) |
| `sweep_ticks` | 1500 | backtest length for sweep runs only (see 5.5 for how crashes are kept inside the run) |
| `position_limit_range` | 40–100 | allowed slider range in the UI and API |

### 5.3 Tick loop (`sim/market.py`)

For each tick `t`:
1. Update mid: deterministic drift from the scenario (crash path) + Gaussian noise `N(0, sigma × multiplier)`, rounded to an int, using a `numpy.random.Generator(PCG64(seed))`.
2. Build `MarketState` (past data only) and call `strategy.on_tick(state)` → `Quote` or `None` (`None` = not quoting).
3. Validate the quote against hard engine rules (sizes ≥ 0, bid < ask when both present). Invalid quotes are recorded as violations and treated as no quote.
4. Draw market buy and sell orders (Poisson counts, each with a random size).
   - A market **sell** can fill against our **bid**; a market **buy** can fill against our **ask**.
   - Fill if `rng.random() < exp(-fill_k × |quote_price − mid|)` and our size remaining > 0. Fill size = `min(order size, remaining quote size)`.
5. For each fill, in order: update the ledger, **record the position immediately after this fill** (per-fill telemetry: tick, fill index, position), then call `strategy.on_fill(fill)`. Within a tick, process the tick's market buy and sell orders in a seeded random order, so intra-tick paths are deterministic.
6. Record per-tick telemetry: mid, quote, end-of-tick position, **maximum |position| reached at any point during the tick**, mark-to-market PnL, fills, halted flag (quote is `None` or both sizes 0).

Return a `BacktestResult` with: telemetry arrays, all fills, all quotes, ledger summary, violations, runtime, and the strategy's final `report()`.

**Important honest-failure property:** during the crash, sell flow dominates, so a naive symmetric market maker keeps getting hit on its bid and accumulates a large long position as the price falls. This is real adverse selection, not a scripted failure.

### 5.4 Ledger (`sim/ledger.py`)

Exchange-side ground truth, integer cents:
- buy fill: `position += size`, `cash -= price × size`, `fees += fee`
- sell fill: `position -= size`, `cash += price × size`, `fees += fee`
- `pnl(mid) = cash − fees + position × mid`

Unit-test it against a hand-computed example (Section 11).

### 5.5 Scenarios (`sim/scenarios.py`)

`Scenario` is a dataclass with every crash parameter from 5.2 plus `name` and `description`. Provide:
- `BASELINE_CRASH`: the defaults above.
- `JSON_SCHEMA` + `BOUNDS` for red-team scenarios. Bounds: `start` 500–2800, `depth_pct` 1–15, `duration` 5–120, `sigma_mult` 1–10, `sell_flow_mult` and `buy_flow_mult` 0.1–6 (allow **spike-up** crashes by letting buy flow dominate), `recovery_pct` 0–100. Anything out of bounds is clamped and logged; malformed JSON is rejected.
- `fit_to_length(scenario, ticks)`: returns a copy whose crash window fits inside a run of `ticks` ticks. If the run is shorter than the scenario was written for, rescale `start` proportionally (`start × ticks / 3000`, rounded), then clamp so that `start ≥ 200` (enough history for detection) and `start + duration + 300 ≤ ticks` (room to observe the halt and the start of recovery). Every backtest, including sweep runs, must call this and **assert** that the crash window lies inside the run. A sweep run that never contains its crash is a bug, not a pass.

---

## 6. Strategy contract (what Argon writes)

Generated strategy code must be a single Python module containing exactly this:

```python
PARAMS = {            # tunable numbers the strategy uses; the sweep varies these
    "half_spread_ticks": 2,
    "skew_per_lot": 0.05,
    # ... any others the strategy needs
}
SWEEP_PARAMS = ("half_spread_ticks", "skew_per_lot")   # exactly two keys of PARAMS for the heatmap

class Strategy:
    def __init__(self, params: dict, limits: dict): ...
    def on_tick(self, state) -> "Quote | None": ...      # Quote is injected into the module namespace
    def on_fill(self, fill) -> None: ...                 # must update the strategy's own books
    def report(self) -> dict: ...                        # {"position": int, "cash": int, "fees": int} in lots / integer cents
```

**Rules for generated code (enforced by `sandbox/validate.py`):**
- Allowed imports only: `math`, `statistics`, `collections`. Everything else is rejected.
- Forbidden names and attributes: `open`, `exec`, `eval`, `compile`, `__import__`, `globals`, `locals`, `vars`, `getattr`, `setattr`, `delattr`, `input`, `breakpoint`, any dunder attribute access (`__class__`, `__dict__`, `__subclasses__`, etc.), `os`, `sys`, `subprocess`, `socket`.
- Must define `PARAMS` (a dict of numbers), `SWEEP_PARAMS` (a 2-tuple of `PARAMS` keys) and `class Strategy` with the four methods.
- Source ≤ 300 lines.
- The strategy may read the desk rules from `limits`. Whether it does is up to the code; the desk strategy (v3) deliberately keeps its own fixed inventory cap, which is why tightening the desk rule breaks it.

Validation failures are reported back to Argon exactly like failed checks, so the loop can fix them.

**Desk rules given to Argon** (from `config.py`, with the slider value applied):

| Rule | Default |
| --- | --- |
| `position_limit` | 100 lots, absolute, at all times |
| `max_order_size` | 10 lots per side per quote |
| `max_drawdown` | 25_000 cents. **Calibrate** (see 11.3) and document the final value |
| `halt_within_ticks` | 5: must stop quoting within 5 ticks of a crash starting, using only past data |
| `calm_ticks_to_resume` | 100: may resume once the market has been calm this long |

---

## 7. Deterministic checks (`checks/checks.py`)

Each check returns `{"id", "name", "passed": bool, "value", "threshold", "detail"}`. `detail` is one precise human-readable sentence with numbers and the tick where it failed, e.g. *"Position reached +143 lots at tick 2017 (limit 100)."* These sentences are what Argon reads in the refine step, so make them specific.

| ID | Name | Rule |
| --- | --- | --- |
| C1 | Books reconcile to the penny | `strategy.report()` position, cash and fees equal the exchange ledger **exactly** at the end of every in-sample run |
| C2 | Position limit | `max |position| ≤ position_limit` **after every individual fill** (not just at tick ends), every run |
| C3 | Order size | every quoted size ≤ `max_order_size`; no invalid quotes |
| C4 | Max drawdown | peak-to-trough of mark-to-market PnL ≤ `max_drawdown` on every in-sample run |
| C5 | Circuit breaker | in the window `[crash.start + halt_within_ticks, crash.start + crash.duration]`, the strategy placed **zero** quotes with non-zero size |
| C6 | Validation profit | final PnL > 0 on **every** validation (`oos_seeds`) seed (only pass/fail is sent to Argon). Label it "Validation" in the UI, not "out-of-sample", because its pass/fail informs refinement |
| C7 | Runs cleanly | no exceptions, each backtest finishes within its time limit |

**Which seeds each check uses:** during the loop, C1–C5 and C7 run on the in-sample seeds (full detail to Argon) and C6 on the validation seeds (pass/fail only). In final verification (Section 8.6), **C1–C7 all run on the holdout seeds**, replacing the validation seeds for C6, plus the in-sample seeds again.

C5 uses the crash window of the scenario actually run (after `fit_to_length`), never the baseline's `crash.start`.

`CheckReport` = all checks + summary metrics per seed (final PnL, max |position| including intra-tick, max drawdown, fills, quote uptime %) + the `artifact_hash` of the code and parameters that were checked. Serialise to JSON for the UI and to compact text for prompts.

---

## 8. The agent

### 8.1 Orchestrator loop (`agent/orchestrator.py`)

`POST /api/run` takes `{position_limit, mode}` where `mode` is `"repair"` (default, the stage path) or `"scratch"`.

**Repair mode (stage path):**
1. Start the 6 red-team Flash calls in the background immediately (Section 8.3, step 1). They don't depend on the strategy.
2. Load the current desk strategy (`mocks/strategy_v3_desk.py`, or the last approved artifact if one exists in this session). Emit `iteration_proposed(0, "Current desk strategy", no diff)`.
3. Re-check it under the new desk rules (sandbox + checks, no model call). Emit `checks(0, report)`.
4. If everything passes, emit `loop_passed` (nothing to repair) and go to the sweep.
5. Otherwise enter the loop below with `previous_code` = the desk strategy and `failure_report` = that report. The first Argon call uses the **REPAIR** prompt.

**Scratch mode:** the loop below starts with `previous_code = None` and the first call uses the **PROPOSE (scratch)** prompt.

```
for iteration in 1..MAX_ITERATIONS (6):
    emit loop_stage("propose")
    code, rationale = argon.propose(iteration, previous_code, failure_report)   # REPAIR / REFINE / PROPOSE prompt
    emit iteration_proposed(iteration, rationale, diff(previous_code, code))

    validation = validate(code)
    if not validation.ok:
        failure_report = validation errors; emit checks(...); continue

    emit loop_stage("sandbox")
    results = runner.run_backtests(code, scenario=BASELINE_CRASH, seeds=in_sample + oos)
    emit replay(best-effort telemetry of seed 1, downsampled for the 3D view)

    emit loop_stage("check")
    report = checks.evaluate(results)
    emit checks(iteration, report)

    if report.all_passed: emit loop_stage("passed"); break
    emit loop_stage("refine")
    previous_code = code
    failure_report = report.for_prompt()   # in-sample details + validation pass/fail only; never holdout
```

- Use the Interactions API's server-side state: pass `previous_interaction_id` on each refinement call so Argon keeps context across iterations without resending everything.
- In scratch mode, iteration 1 asks for **the simplest possible symmetric market maker**, and the UI labels it "Iteration 1: naive baseline". In repair mode there is no deliberately naive step: the starting strategy is the desk's real one.
- Parse Argon's output: one fenced ```python block (the code) and one line beginning `RATIONALE:` (≤ 25 words). If parsing fails, retry once with a short format reminder, then count it as a failed iteration.
- If the loop exhausts iterations, emit `loop_failed` with the best attempt; the UI shows it honestly.

### 8.2 Prompt templates (`agent/prompts.py`)

**REPAIR (repair mode, first call):**
```
You maintain a market-making strategy for a student trading desk's simulated exchange.
The desk has just changed its rules. The current strategy was re-tested under the new rules
in a deterministic simulator, including a flash crash, and these checks FAILED
(exact numbers, from code, not opinions):
{FAILED_CHECKS}
These passed: {PASSED_CHECKS}
In-sample metrics per seed: {METRICS}
New desk rules: {DESK_RULES}
Current strategy:
{CURRENT_CODE}
Repair the strategy so every check passes under the new rules. Make the smallest change that
fixes the root cause, and prefer deriving limits from `limits` over hard-coded numbers.
Keep the contract exactly: {STRATEGY_CONTRACT}
Use only past data in MarketState. Keep PARAMS and SWEEP_PARAMS meaningful for a robustness sweep.
Return exactly one ```python code block, then one line: RATIONALE: <max 25 words>.
```

**PROPOSE (scratch mode, iteration 1):**
```
You are writing a market-making strategy for a student trading desk's simulated exchange.
Write the SIMPLEST possible symmetric market maker as a baseline: quote a fixed half-spread
around the mid on both sides with a fixed size. No inventory control, no crash handling.
It must follow this contract exactly:
{STRATEGY_CONTRACT}
Desk rules (these will be checked deterministically later):
{DESK_RULES}
Market description: {MARKET_DESCRIPTION}
Return exactly one ```python code block, then one line: RATIONALE: <max 25 words>.
```

**REFINE (iteration ≥ 2):**
```
Your strategy was backtested in a deterministic simulator, including a flash crash.
These checks FAILED (exact numbers, from code, not opinions):
{FAILED_CHECKS}
These passed: {PASSED_CHECKS}
In-sample metrics per seed: {METRICS}
Validation seeds: {VALIDATION_PASS_FAIL}   (pass/fail only; a separate untouched holdout set is used at the end)
Revise the strategy so every check passes. Change only what is needed and keep it under 300 lines.
Use only past data in MarketState. Keep PARAMS and SWEEP_PARAMS meaningful for a robustness sweep.
Return exactly one ```python code block, then one line: RATIONALE: <max 25 words>.
```

**RED TEAM (Flash, run N=6 in parallel, each with a different `{ANGLE}`):**
```
You are a risk red-teamer. Propose ONE stress scenario for a market-making bot as JSON matching
this schema and bounds: {SCENARIO_SCHEMA_AND_BOUNDS}
Angle for this scenario: {ANGLE}
Return only the JSON object. Include a short "name" and a one-sentence "description".
```
Angles: deeper crash, faster crash, crash early in the session, spike-up squeeze (buy flow dominates), choppy high-volatility market without a crash, liquidity drought with slow recovery.

**REVIEW AND SIGN-OFF REPORT (Argon, `thinking_level: "medium"` on the stage path to meet the timing; `"high"` allowed in rehearsal):**
```
You are preparing a strategy for the desk head's approval.
Final strategy code: {CODE}
Check report: {CHECK_REPORT}
Sweep results (pass rate per parameter cell and per scenario): {SWEEP_SUMMARY}
1) Choose final PARAMS from the most robust region of the sweep (not the single best cell). Return them as JSON.
   Whatever you choose will be re-verified from scratch on untouched holdout seeds before approval;
   if it fails, the last parameters that passed the loop are used instead.
2) Write a sign-off report (max 150 words): what changed, what was verified, remaining risks,
   and a clear recommendation. Write it as provisional: the final verification result is added after.
3) Write a spoken briefing (max 40 words, plain sentences, no symbols) that ends by asking for approval.
Return JSON: {"final_params": {...}, "report_md": "...", "briefing_text": "..."}
```

### 8.3 Red team + sweep (`agent/redteam.py`)

1. Fire the 6 Flash red-team calls **concurrently** (asyncio or a thread pool) **as soon as `/api/run` starts**, in parallel with the repair loop. Validate, clamp and `fit_to_length(…, sweep_ticks)` each scenario. Emit `redteam_scenario` events as each arrives, so the UI shows them landing in parallel. If a call fails or times out, fall back to a canned scenario for that angle and label its card "fallback".
2. After `loop_passed`, build the grid: the two `SWEEP_PARAMS`, 5 values each, centred on the current values (±50% in even steps; integers stay integers).
3. For each cell × (baseline + 6 red-team scenarios) × in-sample seeds, run the backtest in a process pool (`concurrent.futures.ProcessPoolExecutor`) with `sweep_ticks` and each scenario passed through `fit_to_length`. Assert the crash window is inside every run. Each cell's score = fraction of runs passing C2–C5 and C7. **Never use validation or holdout seeds in the sweep.**
4. Emit `sweep_cell` events as cells complete (the heatmap fills live), then `sweep_done`.
5. Call the REVIEW prompt. Its `final_params` are only a **candidate**: go straight to final verification (Section 8.6). `signoff_ready` is emitted only from there.

Keep the sweep under ~20 s on a laptop. If it is slower, reduce the grid to 4×4 or use one in-sample seed per cell, and document the choice.

### 8.4 Briefing (`agent/briefing.py`)

- Generate audio with `gemini-3.8-flash-tts` from `briefing_text`.
- If `VOICE_SAMPLE` is set, use the model's voice replication with that sample. **Only use a voice whose owner has consented.** Show a small "Voice: replicated with consent" label in the UI when this is active.
- Look up the exact TTS and voice-replication request format with the `gemini-api-dev` skill or the official docs. Do not guess parameter names.
- Fallbacks, in order: a prebuilt TTS voice → the browser's `speechSynthesis` reading `briefing_text`. The demo must never stall on audio.
- Serve the audio at `GET /api/briefing.wav` (or the format the API returns).

### 8.5 Mock mode (`MOCK_LLM=1`)

`gemini_client.py` exposes the same interface in both modes. In mock mode:
- Repair mode: `propose` returns `mocks/strategy_v4_repaired.py` with a canned rationale and a 1.5 s artificial delay (so the animation is watchable).
- Scratch mode: `propose` returns `mocks/strategy_v1_naive.py`, then `v2_skew`, then `v3_desk`, with canned rationales and the same delay.
- Red team returns the 6 scenarios in `mocks/redteam_scenarios.json`, staggered by 300 ms.
- Review returns `mocks/signoff_report.md`-based JSON.
- The **backtests and checks still run for real** on the mock strategies. Only the model outputs are canned.
- The UI shows a bright **MOCK** badge whenever mock mode is on.

### 8.6 Final verification and approval binding (`agent/final_verify.py`)

The thing the human approves must be **exactly** the thing that was proven. Steps:

1. **Candidate** = the final code + the REVIEW step's `final_params` + the current desk rules.
2. Compute `artifact_hash = sha256(code bytes + canonical JSON of params + canonical JSON of limits)`. Show the first 8 hex characters in the UI as the artifact ID.
3. Re-run **all checks C1–C7** on that exact candidate, on the baseline crash scenario (full `ticks`), with the in-sample seeds **and the untouched holdout seeds** (holdout replaces validation for C6). This is the first and only time holdout seeds are used.
4. If every check passes: emit `signoff_ready` with the report, the briefing text, the full check report and `artifact_hash`. Store the candidate as the approvable artifact.
5. If any check fails: discard the REVIEW parameters, fall back to the parameters that passed the loop, and repeat steps 1–3. If that also fails on holdout, emit `signoff_blocked` with the report. Approval is impossible, and the UI says so honestly ("Failed on unseen holdout data").
6. Append the final verification result to `report_md` (e.g. "Final verification: 7/7 checks on 3 in-sample + 5 holdout seeds. Artifact 3f9a21c0.").

`POST /api/approve` requires `{"artifact_hash": "<hash>"}`. The server returns 409 unless the hash matches the stored, verified artifact. The live engine loads **only** that stored artifact (code and params as hashed), never anything else. The live view shows the artifact ID next to the bot.

---

## 9. Gemini API usage (current conventions; follow exactly)

- SDK: `google-genai` version 2.3.0 or later. Use the **Interactions API**.
- Basic call:
  ```python
  from google import genai
  client = genai.Client()  # reads GEMINI_API_KEY
  interaction = client.interactions.create(
      model="gemini-4-argon",
      input=prompt,
      generation_config={"thinking_level": "medium"},  # "low" | "medium" | "high"
  )
  text = interaction.output_text
  ```
- Refinement calls pass `previous_interaction_id=<last interaction id>` to keep server-side context.
- `thinking_level`: `"medium"` for repair, refine and the stage-path review (speed for the stage). `"high"` only for scratch mode and rehearsal runs. Measure real iteration latency early and record it in `AGENTS.md`; if an iteration exceeds 35 s at `"medium"`, shorten the prompt context before anything else.
- **Do not use** `thinking_budget`, `temperature`, `top_p`, `top_k` or explicit `cached_content`. They are deprecated for `gemini-4-argon`. Implicit caching is automatic.
- Set HTTP timeouts to 15–20 minutes for any `"high"` call, or use `background=True` and poll `client.interactions.get(id=...)` until the status is no longer `in_progress` or `queued`.
- Default output limit is 65,536 tokens, which is plenty here. Do not raise it.
- Flash calls use `model="gemini-3.8-flash"`. Check the skill docs for its supported config.
- Wrap every call with: a timeout, one retry on transient errors, and a clear `model_error` event to the UI.
- Never log or send the API key to the browser.

**Optional stretch (`SANDBOX=antigravity`):** run backtests inside the Antigravity managed agent (`agent="antigravity-preview-09-2026"`, `agent_config={"type": "antigravity", "model": "gemini-4-argon"}`, `environment` remote with a network allowlist of nothing). Only attempt this after everything else passes; the local subprocess sandbox is the default and must keep working.

---

## 10. Sandbox runner, live engine, API and UI

### 10.1 Sandbox runner (`sandbox/runner.py`)

- Run each backtest batch in a **separate subprocess** (`python -m towertwin.sandbox.harness`), passing the code, scenario, seeds and limits via stdin as JSON. Read JSON results from stdout.
- In the child: apply `resource.setrlimit` for CPU seconds (20) and memory (512 MB) where the OS supports it, and exec the strategy in a restricted namespace.
  - `__builtins__` contains only: `__build_class__` (**required**, or `class Strategy:` fails to load), `abs`, `min`, `max`, `round`, `int`, `float`, `len`, `range`, `reversed`, `sum`, `sorted`, `enumerate`, `zip`, `list`, `dict`, `tuple`, `set`, `bool`, `isinstance`, `any`, `all`, `pow`, `divmod`, `object`, `Exception`, `ValueError`, `ZeroDivisionError`, `True`, `False`, `None`.
  - Module globals: `__name__ = "strategy"` (required by class creation), plus injected `Quote`, `math`, `statistics`, `collections`. Because `import` statements need `__import__`, which is forbidden, **pre-inject the three allowed modules and have the validator strip or reject `import` lines**. Pick one approach, document it in `ASSUMPTIONS.md`, and make sure all four mock strategies load under it (test in 11.1).
- Parent-side wall-clock timeout of 30 s; kill the process on expiry and report C7 as failed.
- **Honest limits:** the AST validator plus restricted namespace is a guard against accidents and casual misuse, **not** a security boundary. The real containment is the separate process with resource limits and a timeout. Say so in the README. If time allows, run the child with no network (e.g. `unshare -n` on Linux) and a read-only temp working directory.

### 10.2 Live engine (`live/engine.py`)

- Runs the market at ~20 ticks/second in a background task, streaming `tick` events (mid, top-of-book depth snapshot, our quotes, fills, position, PnL, halted flag, volatility).
- On startup the server runs final verification (Section 8.6) on the **desk strategy** (`strategy_v3_desk`) at the default 100-lot limit and stores it as the initial approved artifact. The live engine runs it, so the twin is alive with a bot from the first second of the demo. If that startup verification fails, the server refuses to start the bot and says why (this catches calibration regressions before going on stage). When the judge changes the desk rule, the desk strategy keeps trading in the live twin until a repaired artifact is approved (show "Running: desk strategy, verified at limit 100" so this is never misleading). If the live position breaches the new tighter limit before approval, show it honestly in red; it reinforces why the repair matters.
- **Approval gate (server-enforced):** `POST /api/approve` with `{"artifact_hash"}` only succeeds after `signoff_ready` and only for the hash of the stored verified artifact (Section 8.6). The engine refuses to load any strategy without a matching approval. The UI cannot bypass this.
- `POST /api/crash` injects the baseline crash (or a chosen red-team scenario) starting at the next tick.
- `POST /api/kill` immediately sets all quotes to none and unloads the bot (human override). The UI shows "KILLED BY HUMAN" until reset.
- Depth for the 3D view: synthesise a plausible resting-depth profile around the mid each tick (e.g. depth at level `i` ticks away ∝ `exp(−0.15·i)` × noise, thinned sharply during a crash). This is a visual layer only and must not affect fills or checks.

### 10.3 REST and SSE API (`app.py`)

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/` | serves `web/index.html` |
| GET | `/api/config` | desk rules, mock flag, voice flag |
| POST | `/api/run` | body `{position_limit?: int (40–100), mode?: "repair" \| "scratch"}` → starts the job (rejects if one is running) |
| GET | `/api/events` | SSE stream of all events |
| POST | `/api/approve` | body `{artifact_hash}` → 409 unless it matches the verified artifact; otherwise loads exactly that artifact in the live twin |
| POST | `/api/crash` | trigger a flash crash in the live twin |
| POST | `/api/kill` | kill switch |
| POST | `/api/reset` | reset everything to the idle twin |
| GET | `/api/briefing.wav` | briefing audio |

**SSE event types** (JSON `{"type", "ts", ...}`): `loop_stage`, `iteration_proposed`, `checks`, `replay`, `loop_passed`, `loop_failed`, `redteam_scenario`, `sweep_cell`, `sweep_done`, `final_verification`, `signoff_ready`, `signoff_blocked`, `approved`, `tick`, `crash_started`, `halted`, `resumed`, `killed`, `model_error`, `mock_mode`.

### 10.4 Frontend (`web/`)

Plain HTML, CSS and ES modules. Three.js from a CDN (`cdnjs.cloudflare.com`). No build step. Dark theme, high contrast, readable from the back of a lecture hall (minimum 18px for key numbers).

**Layout (16:9, 1920×1080 target, also usable at 1280×720):**
- **Centre, about 60% width:** the 3D twin.
- **Right column:** loop ring at the top, then the checks panel, then the iteration timeline.
- **Bottom strip:** controls (position limit slider 40–100, Run, a small Repair / From scratch toggle defaulting to Repair, Approve, Trigger flash crash, Kill switch, Reset), PnL and inventory sparklines, and the heatmap after the sweep.
- **Top bar:** "TowerTwin" wordmark, the tagline, the MOCK badge when relevant, and the current stage label.

**3D twin (`twin3d.js`):**
- A terrain mesh: x = price levels around the mid (±40 ticks), z = time (the newest row at the front, scrolling back; keep the last ~120 rows), height = resting depth. Bid side cool blue, ask side warm amber.
- The mid as a glowing line along the terrain; our bot's quotes as two bright markers at their price levels, with size shown as marker scale.
- Fills as expanding ripple rings on the terrain at the fill price.
- During a crash: the terrain tints red, the camera shakes slightly (subtle, max 0.5° for 1 s), and the depth thins.
- When halted: our quote markers fade out and a floating "HALTED · circuit breaker" label appears.
- WebAudio sonar ping on each fill; base pitch rises with realised volatility. Muted by default, with a toggle on the control bar.
- Target 60 fps; cap pixel ratio at 2; provide a "2D mode" toggle (plain canvas price and depth chart) as a fallback.

**Loop ring (`panels.js`):** four nodes in a circle (Propose · Run sandbox · Check proof · Refine) plus an Approve node below. The active stage pulses; completed passes leave a trail; the iteration count sits in the centre ("Iteration 2 of 6").

**Checks panel:** one row per check (C1–C7) with a name, a status pill (grey pending → red fail / green pass), the measured value against the threshold, and the `detail` sentence shown on hover or tap. Rows animate when the status changes.

**Iteration timeline:** one card per iteration with the rationale, a pass count (e.g. "4/7"), and an expandable code diff (simple line-based diff, added lines in green, removed in red).

**Heatmap:** a 5×5 grid on canvas, with axes labelled with the two `SWEEP_PARAMS` names; colour = pass rate (red 0 → green 1). Cells fill in as `sweep_cell` events arrive. The chosen final cell gets an outline.

**Red-team cards:** six small cards that land one by one, each showing the scenario name and description.

**Sign-off modal:** the report (rendered markdown), the final verification line (checks passed, number of in-sample and holdout seeds), the artifact ID, a Play briefing button (auto-plays once), the voice label, then **Approve** and **Reject** buttons. Approve sends the artifact hash. Reject keeps the desk strategy running and the repaired one offline.

**Timeline card 0** shows "Current desk strategy" with its re-check under the new rule, so the first red result is clearly the real strategy, not a staged baseline.

---

## 11. Verification: tests Argon must write and pass

### 11.1 Unit tests
- **Ledger:** buy 3 @ 10,000 (fee 3), sell 2 @ 10,005 (fee 2), mark at 10,002 → position 1, cash = −30,000 + 20,010 = −9,990, fees 5, PnL = −9,990 − 5 + 10,002 = 7 cents. Assert exactly.
- **Determinism:** the same seed, scenario and strategy produce identical telemetry and fills; a different seed produces different telemetry.
- **No look-ahead:** `MarketState` at tick `t` contains no data from `t+1` onwards (property test).
- **Validator:** rejects `import os`, `open(...)`, `__import__`, `().__class__.__subclasses__()`, `getattr(math, 'x')`, files over 300 lines, and missing `PARAMS` or `SWEEP_PARAMS`; accepts all four mock strategies.
- **Restricted namespace loads real strategies:** all four mock strategies (which use `class Strategy:`, `math`, `statistics` and `collections`) load and run inside the child's restricted namespace. This catches a missing `__build_class__` or `__name__`.
- **Runner:** an infinite loop strategy is killed and reported as C7 failed within 35 s; an exception in `on_tick` is reported as C7 failed with the traceback summary.
- **Checks:** each check has a hand-built telemetry fixture that passes and one that fails, with an exact `detail` string.
- **Intra-tick breach:** a fixture where the position goes 99 → 101 → 99 within one tick (limit 100) fails C2, with the detail naming the tick and fill index.
- **Crash inside every run:** for every red-team bound extreme and every mock scenario, `fit_to_length(scenario, sweep_ticks)` and `fit_to_length(scenario, ticks)` place the full crash window inside the run; a scenario with `start = 2800` at `sweep_ticks = 1500` is rescaled, not silently skipped.
- **Holdout isolation:** a test asserts `holdout_seeds` is referenced only in `config.py` and `agent/final_verify.py` (grep the source), and that the loop's prompts never contain holdout results.
- **Artifact hash:** the same code, params and limits give the same hash; changing one character of code, one param, or the position limit changes it.

### 11.2 Orchestrator and API
- **Mock end to end (repair mode):** with `MOCK_LLM=1` and `position_limit = 60`, `/api/run` produces: `iteration_proposed(0)` → `checks(0)` with C2 failed → `loop_stage` → `iteration_proposed(1)` → `checks(1)` all passed → `loop_passed`; `redteam_scenario` × 6 arrive during the loop (not after it); then `sweep_cell` × 25 → `sweep_done` → `final_verification` → `signoff_ready` carrying an `artifact_hash`.
- **Mock end to end (scratch mode):** reaches `loop_passed` within 3 iterations.
- **Approval gate:** `/api/approve` before `signoff_ready` returns 409; with a wrong or stale hash returns 409; with the correct hash returns 200, emits `approved`, and the live engine's loaded code and params hash to the same value.
- **Approved equals verified:** if the REVIEW step returns parameters that fail holdout verification, the system falls back to the loop's parameters, re-verifies, and the approved hash corresponds to the fallback, never to the failed candidate.
- **Kill switch:** after `/api/kill`, no `tick` event contains a bot quote.

### 11.3 Calibration test (critical for an honest demo)
Tune the simulator defaults and `max_drawdown` so that, on the baseline crash scenario with in-sample seeds **and** validation seeds (**never holdout seeds**):
- `strategy_v1_naive` **fails C2 and C5** (and may fail C4) at limit 100
- `strategy_v2_skew` **passes C2 but fails C5** at limit 100
- `strategy_v3_desk` **passes all of C1–C7 at limit 100**, and **fails C2 at limits 40, 60 and 80**
- `strategy_v4_repaired` **passes all of C1–C7 at limits 40, 60, 80 and 100**

Assert these in `tests/test_calibration.py`. Record the final calibrated values and the reasoning in `ASSUMPTIONS.md`. If you cannot reach this split, adjust the crash parameters (not the checks) and document what you changed.

Holdout seeds stay out of calibration so final verification remains a genuine test. It is acceptable (and honest) if a strategy that passed calibration occasionally fails holdout: that is exactly what the holdout is for, and the UI reports it.

### 11.4 Smoke and UI checks
- `scripts/smoke_e2e.py`: starts the server (mock mode by default; `--real` uses Gemini), runs the entire repair demo flow through the API (set limit 60 → run → wait for sign-off → approve with the hash → crash → wait for halted → kill → reset), prints PASS/FAIL per step **and the elapsed time of each step**, and fails if the total exceeds 150 s. Run it with `--real` at least three times before the stage and record the timings in `AGENTS.md`.
- Optional `scripts/ui_check.py` (Playwright, headless Chromium): loads the page, sets the slider, clicks Run in mock mode, waits until all seven check pills are green, approves, triggers a crash, and saves `ui_check.png`. Look at the screenshot and fix any layout problems you see.

---

## 12. Milestones (build in this order; each must be green before the next)

| # | Milestone | Done when |
| --- | --- | --- |
| M1 | Core: types, ledger, simulator (with per-fill position), scenarios (with `fit_to_length`), checks, four mock strategies | Unit tests and the calibration test pass |
| M2 | Sandbox: validator, subprocess runner, harness, restricted namespace | Validator, namespace and runner tests pass |
| M3 | Repair loop with **real Argon** (mock as fallback), final verification with holdout seeds, artifact hash, hash-bound approval, event bus, SSE, REST | Orchestrator and API tests pass in mock mode, and one real Gemini repair run reaches `signoff_ready` (or fails honestly with clear events) |
| M4 | Minimal UI: slider, loop ring, checks panel, timeline (card 0 = desk strategy), sign-off modal with artifact ID, approve, 2D chart | A real repair visibly goes red to green in the browser and can be approved |
| — | **Cut line.** M1–M4 is a genuine model-driven, verified, human-approved demo | |
| M5 | Red team (Flash, parallel, started with the run) and sweep heatmap | 6 scenarios land during the repair; 25 cells stream into the UI in under 20 s |
| M6 | Live engine and 3D twin: desk strategy live from startup, approval swap, crash, kill | The smoke script passes in under 150 s; 3D runs smoothly |
| M7 | Voice briefing with fallbacks | Audio plays, or the fallback reads the text |
| M8 | Polish: typography, animations, sonar audio, README with the demo script | `ui_check.png` looks stage-ready |
| M9 | Rehearsal: three timed `--real` smoke runs, record the backup video | Timings recorded in `AGENTS.md`; video saved |

If credits aren't available when M3 starts, build M3 against mock mode and add the real Gemini calls the moment a key exists. Do not move on to M5 until one real repair run has completed.

---

## 13. README content to generate

- One-paragraph pitch, the tagline, and who it's for.
- Quick start: `pip install -r requirements.txt`, copy `.env.example` to `.env`, `python -m towertwin`, open `http://localhost:8000`.
- How mock mode works and why it's labelled.
- An architecture diagram (ASCII is fine) and the model-role table.
- The 3-minute demo script from Section 2.
- The checks table from Section 7, and the three seed sets (in-sample, validation, holdout) with what each is used for.
- Safety notes: deterministic verification; untouched holdout seeds used only in final verification; approval bound to the hash of the exact verified artifact; server-enforced approval gate; kill switch; generated code runs in a separate resource-limited process (the namespace restrictions are a guard, not a security boundary); consented voice only; simulation only with no real money.

---

## 14. Non-negotiables

- The **checks are code**. The model never grades its own work.
- Generated code **never** runs in the server process.
- The bot **cannot** trade in the live twin without an approval whose hash matches the verified artifact. **What is approved is exactly what was proven**; any change to code, parameters or desk rules requires re-verification.
- Holdout seeds are used **only** in final verification.
- **Mock mode is always visibly labelled.**
- Prices and money are **integers in cents**. Reconciliation is exact.
- No real exchange connections, no real money, no external network calls from the sandbox.
- If time runs short, finish the current milestone cleanly and stop. A working M4 beats a broken M8.
