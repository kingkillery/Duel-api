# jev — advisory strategy selection

`jev` is TypeSafe's System One judgment model, called through the Vercel AI
Gateway (`POST https://ai-gateway.vercel.sh/typesafe/v1/systemone`, model
`typesafe-ai/jev`). It answers small typed questions — a `choice` among
approved options, or a `noul` yes/no probability — and the answers feed
strategy *selection*, never stake math.

## What it does here

| Entry point | Flag | jev's job |
|---|---|---|
| `next_round.py` | `--jev` | Pick one family among the verdict's already-fundable options (STANDARD), or among all spec-able recovery families (RECOVERY). A confident `sit_out` pick tightens the verdict to HALT. |
| `run_11_strategies.py` | `--jev` | One fan-out call scores all 11 configs plus a batch-go question; the batch runs in descending-score order with low-scoring configs dropped. `--jev-top N` caps it. Mid-batch, when running net is negative, a `continue` question can stop the batch early. |

`auto_goal.py` consumes `next_round.py`'s printed command, so `--jev` flows
through the autonomous loop unchanged — the picked command prints last.

## Safety contract

jev can only ever **narrow** what the deterministic layer already approved:

- It chooses among families that passed entry-affordability and floor-safety
  checks; it cannot add options, change stakes, or override a HALT.
- `sit_out` needs a higher confidence bar (`0.80`) than a normal pick (`0.60`).
- On transport failure, malformed answers, or low confidence, the
  deterministic result stands unchanged (`run_11_strategies.py` runs all
  configs unless `--jev-strict` aborts instead).
- It does not predict outcomes. It is an efficiency layer — which approved
  shape fits this bankroll and recent history — not an edge claim. The
  backtest verdict (`optimal stake = 0` at negative EV) still stands.

## Setup

`AI_GATEWAY_API_KEY` in the environment or `.env` (gitignored). Set it with
the masked-input tool, never in chat or committed files:

```console
$ py -3.13 tools/set_gateway_key.py
```

## Where things live

- `jev.py` — transport, auth, answer parsing, pure selection helpers.
  Fail-closed: every failure raises `JevError`.
- `jev_questions.py` — **all** question text and thresholds in one place.
  This is the file to review; the model never sees question ids, only
  `instructions`.
- `tests/test_jev.py` — pins the narrowing contract with the gateway mocked.
