# Operator Console — GUI wireframes (v0.1 draft)

First GUI per the launch packet: a local **Safety Dashboard**, not a betting control panel. HALT is first-class, not an error state. Fully local, read-only by default, no account needed for tabs 1–2. No auto-signup, no affiliate link in execution paths, no default live-wager button, no raw token display.

## Tab 1 — Welcome

```text
+----------------------------------------------------------+
| duel-api Operator Console              [Doctor: OK | v0.2.2] |
+----------------------------------------------------------+
| > The bot that refuses to bet.                           |
|                                                          |
|  What this is: open-source, offline-first research and   |
|  protocol toolkit. Inspect the math, don't buy fantasies.|
|                                                          |
|  [ Run Offline Demo ]   [ Run Doctor ]   [ Read the Gates ]|
|                                                          |
|  331 tests green. No account needed to learn.            |
+----------------------------------------------------------+
```

- Buttons call existing modules in-process: `demo`, `doctor`, open `docs/betting-gates.md`.
- Header shows local environment health only — never tokens, cookies, or balances.

## Tab 2 — Demo & Backtest

```text
+----------------------------------------------------------+
| Staking shape: [ Paroli-style v1 plan ▾ ]  Seed: [ 7 ]   |
| Sessions: [ 10000 ]                    [ Run Backtest ]   |
+----------------------------------------------------------+
| Distribution chart (canvas)                              |
| Drawdown curve (canvas)                                  |
|----------------------------------------------------------|
| EV caveat (always visible, not dismissible):             |
| Sizing changes variance, never the sign of expected      |
| value. Kelly returns zero under negative edge.           |
| [ Export redacted report ]                               |
+----------------------------------------------------------+
```

- Wraps the existing backtest engine; seed + sessions map to current CLI flags.
- Export writes ledger/config/verdict explanation with session values redacted.

## Tab 3 — Protocol Verdict

```text
+----------------------------------------------------------+
| Local balance (you type it, never fetched): [ 0.64 ] µBTC|
| Floor: [ 0.30 ]                        [ Calculate ]      |
+----------------------------------------------------------+
| Family   | Entry | Floor headroom | Exposure share | Result   |
| s01      |  ...  | ...            | ...            | EXCLUDED |
| ...      |       |                |                |          |
| TOTAL exposure (SUM over Plan 1 slots): 1.0x µBTC        |
+----------------------------------------------------------+
|  VERDICT: HALT — no safe/fundable option.                |
|  Reasons: <per-family exclusion reasons>                 |
|  A 0-round 0-net row is not a settled round.             |
+----------------------------------------------------------+
```

- Calls the `next_round.py` protocol logic (config_requirements, family_fitness, recovery-stake clamp) directly — same gates pinned by `tests/test_protocol_funding.py`.
- HALT rendered as the successful outcome panel (green shield, not red error).

## Tab 4 — Diagnostics

```text
+----------------------------------------------------------+
| [ Run Doctor ]                                           |
| component table (same as `duel-api doctor`)              |
| Local files status: ledger / configs / verdicts          |
| Session status: present/absent — values NEVER displayed  |
+----------------------------------------------------------+
| Advanced (explicitly guarded, collapsed by default):     |
| ⚠ Session tooling + runner live here. Local-only        |
| storage, no invisible browser capture, no default       |
| "place wager" route. Four-deep gates stay armed.        |
| [ Reveal advanced ]                                      |
+----------------------------------------------------------+
```

## MVP acceptance criteria

1. Tabs 1–2 work with no account, no network, no session file.
2. Tab 3 reproduces the CLI verdict for the same balance/floor inputs (differential test against `next_round.py`).
3. Tab 4 never renders a token/cookie/balance fetched from anywhere.
4. Windows standalone packaging path proven (PyInstaller) before any Tauri discussion.
5. `pytest` suite covers verdict parity + redaction (ledger hygiene: no 0-round 0-net rows exported).

## Sequence

Wireframes (this doc) → PySide6 MVP (Python-native, calls existing modules directly) → validate info-architecture with real users → Tauri only if usage justifies a cross-platform shell. No Streamlit/Gradio default (localhost server surface).
