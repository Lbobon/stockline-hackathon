# Simulated calls

A model plays a Médina client from their persona file (`data/context/clients/*.md`, the same files the jury uses to play clients). The real agent answers exactly as it does on the Voix page: `run_agent`, voice mode, real ERP. The only difference is that there's no speech-to-text or text-to-speech. Each call is then scored two ways:
- automatic checks on the agent's tool calls
- a judge model

The result is a report with the success rate, the agent's latency and its cost per call: the numbers `DECISIONS.md` asks for.

```bash
cd apps/ai
uv run python -m sim --list                    # the scenarios
uv run python -m sim                           # every scenario once
uv run python -m sim rupture_broche camion     # just these
uv run python -m sim --repeat 3 --concurrency 4
```

- **Before running:** the ERP must be up (`make dev`, or the `medina-kit` launch on Loric's laptop).
- **ERP reset:** each run first reloads the ERP from the data pack (`POST /api/admin/reset`), so every run starts from the same Tuesday 05:30. Pass `--no-reset` to keep your local changes.
- **Cost:** it uses real tokens. A call costs about $0.10 for the agent (Sonnet 5.5, effort low) plus about $0.06 for the caller and judge. The caller and judge default to `claude-opus-5-5`; change them with `SIM_CALLER_MODEL` / `SIM_JUDGE_MODEL`.
- **Output:** `sim/runs/<date>/report.md` (summary, one row per call, full transcripts) and `calls.jsonl` (everything, including tool results). Both are git-ignored.

## What the numbers mean

- **Success:** every automatic check passed **and** the judge says the agent handled the call the way a good Médina employee would.
- **Latency:** measured from the caller's text to the agent's first word, and to the end of the turn. It's measured in-process, so on the Voix page add speech-to-text finalisation, text-to-speech and the network.
- **Cost:** the agent's LLM tokens at list prices (cache reads and writes included). Speech-to-text and text-to-speech aren't counted.

## Adding a scenario

Copy one in `scenarios.py`, then:
1. Pick the client and their persona file.
2. Write the caller's private goal (in French, as the client would think it).
3. Describe what a good agent does, for the judge.
4. Add checks from the helpers, e.g. `confirmed()`, `not_called("confirm_order")`, `order_qty("prd_0001", 64, 80)`, `writes_only_for("cli_0083")`, `no_substitution()`.

The scenarios follow the call types in METIER.md ("Où la voix change la donne") and today's data:
- the 20 kg spit and the veal escalope are out of stock
- blocked accounts
- a client near their credit limit
- five clients called El Baraka
