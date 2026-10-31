# START HERE (instructions for the coding agent)

You are building **TowerTwin** at a 5-hour hackathon (Google DeepMind × Imperial College London). Hacking runs 11:00 to a **hard 4:00 PM deadline**, with stage demos at 4:30.

## Files in this folder
- `SPEC.md`: the complete build spec. **Read it in full before doing anything.** It is the source of truth.
- `reference/Gemini_4_Argon_Builder_Guide.pdf`: the organisers' guide to Gemini 4 Argon, the Interactions API and the other models. Check it for API conventions.
- `.env`: environment settings. `GEMINI_API_KEY` is blank and `MOCK_LLM=1` until the team adds a key.
- `.gitignore`: keeps `.env` and build junk out of git.

## How to work
1. If the Gemini API skill isn't already available to you, run `npx skills add google-gemini/gemini-skills --skill gemini-api-dev` in this folder.
2. Write `PLAN.md` and `ASSUMPTIONS.md` first.
3. Build milestones **M1 to M9 strictly in order** (SPEC Section 12). Write and run each milestone's tests from SPEC Section 11 before starting the next. Never claim something works without running it.
4. Don't wait for answers to questions. Make a sensible assumption, record it in `ASSUMPTIONS.md`, and carry on.
5. After each milestone, post a 3-line status: what's done, test results, and anything the team must decide.
6. There is no API key yet, so build against mock mode. When the team says the key is in `.env`, set `MOCK_LLM=0` and complete the **real Argon repair run required by M3** before moving on.
7. Record anything non-obvious you learn (setup quirks, measured model latency) in `AGENTS.md`.
8. Never read `GEMINI_API_KEY` into logs, prompts, the browser or git.
9. If it is 3:15 PM or later, stop adding features and finish the current milestone cleanly.

Start now.
