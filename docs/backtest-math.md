# Backtest Mathematics

The backtest package is the part of this repository that argues against using the rest of it. That is deliberate: a betting harness whose most useful output is *don't* is the only honest kind.

---

## 1. Structural Guarantee: Network-Free by AST

`tests/test_backtest.py` parses every module in `backtest/` and fails if any of them import a network-capable library (`httpx`, `requests`, `urllib.request`, `socket`, …). Consequences:

- Backtest results **cannot** depend on live state, latency, or luck-of-capture.
- The engine is trivially hostable — the hard part of putting it on a web page is already solved.
- Any future edit that quietly phones home breaks the build.

---

## 2. The Invariant

For any stake schedule on a negative-edge game:

```
E[net] = −edge × E[total wagered]
```

Bet sizing changes **variance**, never the **sign** of expectation. Every staking system — flat, Martingale, Fibonacci, D'Alembert, Paroli, custom ladders — is a different way to distribute the same inevitable cost.

---

## 3. Kelly Says Zero

Applying fractional Kelly sizing to duel.com's observed dice tiers:

```
edge tiers observed: 0.001 → 0.0099
f* ≈ −0.0010018  (negative at every tier)
optimal stake = 0
```

`bankroll.py` encodes this:

- `BankrollPolicy` — growth-optimal sizing; **fails closed** (refuses all bets) when EV ≤ 0, because the growth-optimal stake is exactly zero.
- `PlayPolicy` — entertainment mode: fixed stake, hard budget, hard stop. The only sanctioned way to bet.

---

## 4. Measured Examples

| Strategy | Constraint | Outcome (Monte Carlo, 50k sessions) |
|---|---|---|
| Flat | live caps (µ stakes, loss floor) | P(ahead) ≈ 41–49 %, P95 ≈ +7 µ |
| Martingale | deep ladder | `ScheduleError` — ladder rejected at construction (stake overflow) |
| Paroli | press ×2 on wins, bank after 3 | banks at profit cap 45.5 % vs flat 41.2 % — but ~2.2× expected edge cost |
| Flat, uncapped | no profit/loss floor | same P(ahead), tails ~1.8× wider |

Ruin probability at the live session caps measured **0.5314** for a flat strategy — a coin flip you pay for the privilege of flipping.

**Verdict:** ride-the-gain only pays under a take-profit; nothing pays in expectation.

---

## 5. Reproduce

```
py -3.13 -m backtest --help          # local engine (network-free, guaranteed)
py -3.13 -m pytest tests/test_backtest.py -q
```

The AST guard test is the one that keeps this page honest.
