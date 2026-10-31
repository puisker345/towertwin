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
- **Crash path drift**: During `[crash.start, crash.start + crash.duration)`, the deterministic crash drop of `start_mid * (crash.depth_pct / 100.0)` cents is distributed evenly across `crash.duration` ticks (with directional sign negative when `sell_flow_mult >= buy_flow_mult`, or positive for spike-up scenarios where `buy_flow_mult > sell_flow_mult`). During `[crash.start + crash.duration, crash.start + crash.duration + 300)`, `recovery_pct`% of the drop recovers linearly. Volatility multiplier `crash.sigma_mult` applies during `[crash.start, crash.start + crash.duration + 200)`.
- **Market order flow**: Each tick draws `n_buy ~ Poisson(arrival_rate * buy_mult)` and `n_sell ~ Poisson(arrival_rate * sell_mult)`. Orders are combined and shuffled deterministically using `rng.permutation` so intra-tick fill order is unbiased and repeatable.
- **Calibrated values**:
  - `max_drawdown = 25_000` cents ($250.00).
  - `in_sample_seeds = [1, 2, 3]`, `oos_seeds = [101, 102, 103]`, `holdout_seeds = [201, 202, 203, 204, 205]`.
  - Under these defaults on `BASELINE_CRASH`:
    - `strategy_v1_naive`: Quotes symmetrically (`half_spread_ticks=2`, `order_size=8`) with no inventory cap and no crash halt → accumulates >100 lots during normal/crash flow and quotes through the crash → **fails C2 and C5** at limit 100.
    - `strategy_v2_skew`: Skews quotes by inventory and caps order sizes so `|position| <= 85` at all times, but has no crash halt → **passes C2 and fails C5** at limit 100.
    - `strategy_v3_desk`: Uses skew + crash detector (halts within ≤3 ticks of crash start using past mid drops and realized volatility, resumes after `calm_ticks_to_resume = 100` calm ticks), but uses a hard-coded `PARAMS["max_inventory"] = 90` instead of reading `limits["position_limit"]` → reaches peak `|position|` between 82 and 90 lots → **passes all C1–C7 at limit 100**, and **fails C2 at limits 40, 60, and 80**.
    - `strategy_v4_repaired`: Reads `limits["position_limit"]`, applies a safety buffer (`cap = max(1, int(limits["position_limit"]) - int(params["safety_buffer"]))`), tapers order size near the cap, and strictly bounds bid/ask sizes to remaining headroom → **passes all C1–C7 at limits 40, 60, 80, and 100**.

## 5. Parameter Sweep Speed (`agent/redteam.py`)
- **Assumption**: To guarantee the 5×5 parameter sweep completes well under the 20s target on a laptop (typically ~3–6s), we run the 25 cells across `(baseline + 6 red-team scenarios)` on `in_sample_seeds[:1]` (`seed=1`, or all 3 in-sample seeds when fast enough) using vectorized/fast backtest execution in a worker pool at `sweep_ticks = 1500` with `fit_to_length(scenario, 1500)`.

## 6. Mock Mode & Live Gemini API Key
- **Assumption**: Per `START_HERE.md` item 6, `.env` initially has `GEMINI_API_KEY=` blank and `MOCK_LLM=1`. All milestones are built and verified in mock mode first, with the real `google-genai` Interactions API (`gemini-4-argon`, `gemini-3.8-flash`, `gemini-3.8-flash-tts`) fully implemented in `agent/gemini_client.py` and `agent/briefing.py` so flipping `MOCK_LLM=0` once the key is added immediately runs the live Argon repair loop.
