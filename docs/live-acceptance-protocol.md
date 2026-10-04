# Live Acceptance Protocol — Quote & Settlement

Status: **DRAFT — unsigned.** Nothing here has been executed. This document is the
*requirements* half of the open checklist item "Establish fail-closed evidence
requirements for quote and pending-settlement checks". A human must run it, in
order, and record the results. An agent must not mark this accepted.

## Why this exists

The offline suite (`tests/test_client.py`, `tests/test_protocol_funding.py`,
`tests/test_next_bet.py`) pins the *shape* of every local decision. It cannot
establish:

- that the site's live payout quote matches what `dice_edge()` derives,
- that a real bet settles the way the ledger expects,
- that a captured token is actually accepted.

Those are live facts. Local arithmetic and an `ADVANCE` ledger label are not
evidence for any of them (decision 2026-09-19, "Local checker output is not live
clearance").

## Preconditions — do not start unless all hold

- [ ] You are at the keyboard. An agent may not run any step below.
- [ ] Session is fresh: `py -3.13 -m duel_doctor` reports a valid session
      (or run `capture_session.py` first — but note it **invalidates** any
      captured token, so capture tokens *after* any session refresh).
- [ ] You have decided the wager units: amount, side, currency, target. A unit
      mistake is a ~200× stake error (decision 2026-09-17).
- [ ] You accept that this protocol includes **one real minimum-stake wager**.
      It is the only way to observe settlement. If you are not willing to place
      it, stop here and leave the item open.
- [ ] Bankroll is micro-scaled (floor 0.30 µBTC). Do not run this against a
      full-size bankroll.

## Part A — Quote check (no wager)

Establishes that the derived payout quote matches the live config.

- [ ] A1. Capture the live config:
      `py -3.13 automation_cli.py dice-config > /tmp/config.json` (read-only).
- [ ] A2. Derive the quote for your intended bet:
      `py -3.13 -c "from automation_client import DuelClient; ... dice_edge(target=..., side=...)"`
      (or read the `[LIVE] 4b site quote must show >= ...` line `next_bet.py` prints).
- [ ] A3. In the browser, open the dice game and read the **site-displayed**
      multiplier for the same target/side.
- [ ] A4. Record both numbers here:

      derived (chance, multiplier): ______________________
      site-displayed multiplier:    ______________________

- [ ] A5. Verdict — record exactly one:
      - [ ] MATCH (within the site's display rounding)
      - [ ] MISMATCH → **STOP.** Do not place the wager. File the discrepancy.

A "MATCH" here means the local `dice_edge` derivation is faithful to the live
config. It does **not** mean any bet has settled.

## Part B — Settlement check (one real wager)

Establishes that a real round settles the way the ledger records it.

- [ ] B1. Mint a token: place a **minimum-stake** wager manually in the browser
      at the target/side from Part A. This also produces the bet-body token.
- [ ] B2. From DevTools → Network → the `POST /api/v2/dice/bet` request, copy the
      bet-body token. Record only that it was obtained — **never paste it here**
      (secrets stay out of the vault; decision 2026-09-17).
- [ ] B3. Note the balance **before** the bet: ______________________
- [ ] B4. Note the balance **after** settlement: ______________________
- [ ] B5. Verify the arithmetic:
      - [ ] win → `after - before == stake × (multiplier − 1)` (or the site's exact payout)
      - [ ] loss → `after - before == −stake`
- [ ] B6. Write the round to the ledger and confirm it is recorded as settled:
      - a row with `rounds >= 1` and the observed net,
      - `is_settled(row) is True`,
      - and it advances `last_round` / contributes to `loss_streak`.

      recorded row (no secrets): ______________________

- [ ] B7. Verdict — record exactly one:
      - [ ] SETTLES AS EXPECTED
      - [ ] DIVERGES → **STOP.** File it. The ledger rule is wrong until proven otherwise.

## Part C — Pending-settlement boundary, live

Establishes that an *unsettled* attempt is not mistaken for a settled round.

- [ ] C1. Start the token flow but **abort before the wager is sent** (close the
      tab, or kill the process between mint and send).
- [ ] C2. Confirm no new ledger row was written (`run_single_round.py` refuses a
      0-round attempt and exits non-zero).
- [ ] C3. If a 0-round/0-net row does appear, confirm `is_settled()` treats it as
      a non-round and that the loss streak did not reset.
- [ ] C4. Record the observed outcome: ______________________
- [ ] C5. Verdict — record exactly one:
      - [ ] ABORT LEAVES THE LEDGER UNTOUCHED
      - [ ] ABORT WROTE OR MIS-CLASSIFIED A ROW → **STOP.** File it.

## What a pass establishes

Only this, and nothing more:

- Part A: the local quote derivation matches the live config.
- Part B: one real wager settled as the ledger expects.
- Part C: an aborted attempt does not pollute the ledger.

It does **not** establish that a *batch* is safe, that tokens persist across a
session refresh, that the runner path is fully gate-covered, or that any strategy
is profitable. Those remain separate, open items.

## If anything diverges

Stop at that step. Do not continue, do not "work around" it. Record the step, the
observed values (no secrets), and leave the checklist item open. A divergence is a
finding, not an obstacle to route around.

## Sign-off

    Ran by:      ______________________  (human)
    Date:        ______________________
    Result:      [ ] ACCEPTED  [ ] DIVERGED (see note)
    Note:        ______________________

Until this is signed, the item stays open and no agent may claim live acceptance.
