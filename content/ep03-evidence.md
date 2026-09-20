# EP03 evidence — generated 2026-09-20 (due on air Oct 4)

Command (offline, deterministic, no account):

```text
duel-api demo --sessions 10000 --seed 7
```

## Verbatim output (card + narration source of truth)

```text
duel-api demo
────────────────────────────────────────────────────────────────────────

  game              dice-style, 1:1 payout
  edge              0.1000% (the most favorable tier duel.com offers)
  sessions          10,000 per schedule, deterministic seed
  base stake        0.50

every staking schedule, same destination
┌──────────────────────────────────────────────────────────────────────────────────┐
│ schedule     │ EV/session │    implied edge │ P(target) │ P(stop) │ mean wagered │
├──────────────────────────────────────────────────────────────────────────────────┤
│ flat         │    -0.0787 │ +0.199% ±0.113% │     0.436 │   0.564 │        39.59 │
│ martingale-3 │    -0.0338 │ +0.176% ±0.246% │     0.465 │   0.535 │        19.16 │
│ fibonacci-5  │    -0.0195 │ +0.076% ±0.193% │     0.487 │   0.513 │        25.51 │
└──────────────────────────────────────────────────────────────────────────────────┘

  pooled implied edge+0.157% across every schedule (true edge 0.100%)
  per-schedule estimates carry sampling noise; the pool converges to the truth

the invariant
  E[net] = -edge x E[total wagered]
  Sizing changes variance, never sign. Kelly f* = -0.001000.

verdict
  growth-optimal stake is exactly zero - BankrollPolicy fails closed.
  The most useful thing this tool can print.
```

## Card-ready numbers (must match on screen)

- Title card: `Sizing changes variance, never the sign.` + `Kelly f* = -0.001000`
- Table overlay: flat `-0.0787` / martingale-3 `-0.0338` / fibonacci-5 `-0.0195` EV per session — all negative, best tier `0.1000%`.
- Close: `BankrollPolicy fails closed` + `growth-optimal stake is exactly zero`.

Draft card (deterministic SVG + PNG render, visually verified 2026-09-20):
`content/media/kelly-zero-card-draft.svg` → `content/media/kelly-zero-card-draft.png`
(1280×720, terminal aesthetic, numbers match §verbatim output). Human may
restyle, but no number may change.
Vertical Shorts variant (1080×1920, same numbers, visually verified):
`content/media/kelly-zero-card-vertical.svg` → `content/media/kelly-zero-card-vertical.png`

## Code pointers (for the description / pinned comment)

- Kelly-zero verdict: `demo.py:80-86` (negative fraction → "exactly zero - BankrollPolicy fails closed").
- Fail-closed policy: `bankroll.py:1-25` (`EV < 0` permits exactly zero), enforcement at `bankroll.py:138-144`.
- Regression proof: `331 tests` (see README) — gates pinned, not just printed.

## Still human: render per calendar gate

Narration timing JSON measured → horizontal + vertical + voiced + GIF → watch end-to-end with the numbers above matching the script → repo link with `?utm_source=youtube&utm_medium=video&utm_campaign=ep03` → disclosure → pinned comment (install + ToS + RG).
