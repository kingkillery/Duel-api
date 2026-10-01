# DuckDice Provider

`duckdice_provider.py` is a second money provider alongside the Duel client. It talks
to [DuckDice](https://duckdice.io)'s **official Bot API** rather than Duel's
reverse-engineered private `/api/v2` routes, so it needs no browser, no captured
session, and no `--security-token`.

Select it on the shared money surfaces with the global `--provider` flag:

```console
$ duel-api --provider duckdice dice-bet --live ...     # live wager
$ duel-api --provider duckdice dice-bet ...            # dry run (default)
$ duel-api --provider duckdice autobet ...             # batched rounds
```

## Authentication

Authentication is the Bot API key, sent as the `api_key` query parameter on every
request. Resolution order:

1. the `api_key=` constructor argument (tests)
2. `DUCKDICE_API_KEY` in the environment
3. `DUCKDICE_API_KEY` parsed out of `.env` (project-local, then module dir)
4. the `.duckdice_key` file (Ducky compatibility fallback)

`.duckdice_key` is **gitignored** — it is a live-money credential in the same class
as a captured session. Never commit it, and never paste it into a log or issue.

The constructor resolves the key immediately, including for a client that is only
used as a dry run. A dry run does not require a key and does not transmit one:
`_require_key()` is only reached from `_request()`, which only live paths and
balance reads call. If no key is configured, the stored value is an empty string.

Successful response bodies are returned as the provider parsed them. `_redact()`
runs on transport and HTTP error text, stripping the key value and any
`api_key=` query parameter before that text can reach a message. It is not applied
to a successful response body.

## What is read-only

| Call | Path | Effect |
|---|---|---|
| `user_info()` | `GET /api/bot/user-info` | read-only balances |
| `balance_for(currency)` | (wraps the above) | read-only balance lookup |

## The money surface

`place_dice_bet()` is the single write path. It validates the stake, applies any
supplied sizing policy, sends `POST /api/dice/play`, then **validates the authoritative
response fields** (`bet.hash`, `bet.number`, `bet.result`, `bet.betAmount`,
`bet.winAmount`, `bet.profit`, `user.balance`) before any of them reach stake
accounting. A missing, empty, non-finite, or non-numeric money field raises
`DuckDiceError` rather than silently entering the ledger.

Session-realized profit and the balance are updated only from those validated
server-returned fields — never from local arithmetic.

## The edge is a constant, not a measurement

Duel's `dice_edge()` reads the live `scaling_edge` config table and refuses when it
cannot. DuckDice's Bot API publishes no such table, so this provider uses the site's
flat, advertised **1% house edge**, expressed as the single constant `HOUSE_EDGE`
in `duckdice_provider.py`:

```python
HOUSE_EDGE = Decimal("0.01")
```

`dice_edge()` returns the win probability for the requested target/side and the
payout multiplier `(1 - HOUSE_EDGE) / probability`. The implied EV of any DuckDice
bet is `-HOUSE_EDGE` up to `Decimal` context rounding, and is **negative at every
valid target** — that sign is the guarantee. This is deliberate: it is the actual
site constant, and it means `BankrollPolicy` sizes to zero and `--edge-guard`
refuses every DuckDice bet, the same fail-closed outcome the Duel path reaches from
its measured edge.

If DuckDice ever changes its published edge, `HOUSE_EDGE` is the one line to change.

## Gate differences from the Duel path

For `dice-bet`, live dispatch requires `--yes`, `--enable-betting`, `--live`,
and `--confirm-bet`. Two authentication requirements differ:

| | Duel | DuckDice |
|---|---|---|
| Browser-minted `--security-token` | required | **not used** — auth is the Bot API key |
| Captured session / profile check | required for live | **not used** — `autobet` skips the profile lookup |

The separate `autobet` command requires its own provider-specific dry-run approval
and `--yes --confirm`; it enables the guarded client programmatically. A Duel
dry run cannot unlock DuckDice. Do not mistake its batch-level confirmation for
four separate per-round human confirmations. Both paths enforce the stake cap;
edge-based refusal applies when an edge policy is explicitly requested.

See [betting-gates.md](betting-gates.md) for the shared gate list.

## Manual wager page

There is no local dice simulator. A manual DuckDice wager goes through the
localhost desk in [local-trade.md](local-trade.md), which calls
`DuckDiceProvider.place_dice_bet()` and stays disabled unless the server process
was started with `DUEL_TRADE_LIVE=1`. The hosted sandbox remains the offline
backtester only.
