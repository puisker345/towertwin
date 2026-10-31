# Assumptions & Calibration Decisions (`ASSUMPTIONS.md`)

## 1. Package Structure & Invocation (`python -m towertwin`)
- **Assumption**: All code is built directly inside `towertwin/` as instructed in `START_HERE.md`. To ensure `python -m towertwin` and `import towertwin.*` work seamlessly whether the user runs commands from inside `towertwin/` or from its parent workspace directory, we configure a `.pth` entry in Python's `site-packages` pointing to the parent directory and add `sys.path` bootstrapping in `conftest.py`, `__main__.py`, and `scripts/`.

## 2. Restricted Namespace & Allowed Imports (`sandbox/validate.py` & `sandbox/harness.py`)
- **Spec requirement**: Section 6 allows imports of `math`, `statistics`, and `collections` only, while Section 10.1 forbids `__import__` in `__builtins__` and specifies: *"pre-inject the three allowed modules and have the validator strip or reject `import` lines. Pick one approach, document it in `ASSUMPTIONS.md`, and make sure all four mock strategies load under it."*
- **Chosen approach**:
  1. `sandbox/validate.py` inspects `ast.Import` and `ast.ImportFrom` nodes: it **accepts** imports of `math`, `statistics`, and `collections` (and rejects any other module or relative import).
  2. `sandbox/harness.py` pre-injects `math`, `statistics`, `collections` (plus `deque`, `defaultdict`, `Counter` when imported via `from collections import ...`) and `Quote` into the restricted execution globals alongside `__name__ = "strategy"`. Before calling `compile()`/`exec()`, `harness.py` strips the validated `import math` / `import statistics` / `import collections` AST nodes (converting any `from <allowed> import <name>` or `import <allowed> as <alias>` into safe variable bindings against the pre-injected module objects).
  3. This ensures strategies work whether they explicitly write `import math, statistics, collections` (as LLMs naturally do and as our mock strategies do) or use the pre-injected globals directly, while `__import__` remains completely absent from `__builtins__`.

## 3. Cross-Platform Resource Limits (`sandbox/harness.py`)
- **Assumption**: `resource.setrlimit` (`RLIMIT_CPU`, `RLIMIT_AS`) is applied on POSIX systems (`linux`/`darwin`). On Windows where the `resource` module is not part of the standard library, `harness.py` guards `import resource` with `try/except (ImportError, ValueError, OSError)` and relies on the parent process wall-clock timeout (30s) and process termination in `sandbox/runner.py`.

## 4. Simulator Mechanics & Calibration (`sim/market.py`, `config.py`, `mocks/`)
- **Crash path drift & trough liquidations**: During `[crash.start, crash.start + crash.duration]`, the deterministic crash drop of `start_mid * (crash.depth_pct / 100.0)` cents is distributed across `crash.duration` ticks (negative when sell flow dominates, positive for spike-up squeezes where `buy_flow_mult > sell_flow_mult * 1.5`) with active flash-crash turbulence (`sigma_mult` and minimum microstructure dislocation). Immediately after the cliff (`[crash.end, crash.end + 35)`), step volatility settles while distressed trough sell/buy liquidations continue before the 300-tick recovery unwinds inventory.
- **Market order flow**: Each tick draws `n_buy ~ Poisson(arrival_rate * buy_mult)` and `n_sell ~ Poisson(arrival_rate * sell_mult)`. Orders are combined and shuffled deterministically using `rng.permutation` so intra-tick fill order is unbiased and repeatable.
- **Calibrated values**:
  - `max_drawdown = 25_000` cents ($250.00).
  - `in_sample_seeds = [1, 2, 3]`, `oos_seeds = [101, 102, 103]`, `holdout_seeds = [201, 202, 203, 204, 205]`.
  - Under these defaults on `BASELINE_CRASH`:
    - `strategy_v1_naive`: Quotes symmetrically (`half_spread_ticks=2`, `order_size=8`) with no inventory cap and no crash halt → accumulates >100 lots during normal/crash flow and quotes through the crash → **fails C2 and C5** at limit 100.
    - `strategy_v2_skew`: Skews quotes by inventory and caps order sizes so `|position| <= 85` at all times, but has no crash halt → **passes C2 and fails C5** at limit 100.
    - `strategy_v3_desk`: Uses reservation-price skew (`res_price = state.mid - skew_ticks`) + volatility/step circuit breaker, with zero `state.tick` checks, and uses its own fixed `PARAMS["max_inventory"] = 90` cap instead of reading `limits["position_limit"]`. During the post-crash trough liquidation flow right after `[2000, 2030]`, it absorbs distressed sell flow up to `+90` lots at tick `2063` (seed 2) before unwinding into the recovery → **passes all C1–C7 at limit 100**, and **fails ONLY C2 at limits 40, 60, and 80**.
    - `strategy_v4_repaired`: Reads `limits["position_limit"]`, enforces a safety buffer of `max_order_size` (`buffer_lots = max(max_order_size, PARAMS["safety_buffer"]) = 10`, so `effective_cap = position_limit - 10`), and bounds bid/ask sizes so `|position|` never exceeds `position_limit - max_order_size` (`30, 50, 70, 83` at limits `40, 60, 80, 100`) → **passes all C1–C7 at limits 40, 60, 80, and 100** (including `PnL > 0` on every in-sample, validation, and holdout seed).

## 5. Parameter Sweep Speed & Grid Range (`agent/redteam.py`)
- **Assumption**: To guarantee the 5×5 parameter sweep completes well under the 20s target on a laptop (typically ~2–3s) while showing a clear boundary failure region around a robust interior safe zone, `build_axis_values` spans `[0, max(1, b - 1), b, b + 2, b + 7]` for integer parameters (`half_spread_ticks`) and multipliers `(0.1, 0.5, 1.0, 1.5, 3.0)` for float parameters (`skew_per_lot`), evaluated across `(baseline + 6 red-team scenarios)` on `in_sample_seeds[:1]` at `sweep_ticks = 1500` with `fit_to_length(scenario, 1500)`.

## 6. Mock Mode & Live Gemini API Key
- **Assumption**: Per `START_HERE.md` item 6, `.env` initially has `GEMINI_API_KEY=` blank and `MOCK_LLM=1`. All milestones are built and verified in mock mode first, with the real `google-genai` Interactions API (`gemini-4-argon`, `gemini-3.8-flash`, `gemini-3.8-flash-tts`) fully implemented in `agent/gemini_client.py` and `agent/briefing.py` so flipping `MOCK_LLM=0` once the key is added immediately runs the live Argon repair loop.
