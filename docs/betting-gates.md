# Betting Gates

Money can move through exactly two commands — `dice-bet` and `autobet` — and both are gated more deeply than anything else in the client. This page documents the gates and the live-discovered token rules that motivated them.

---

## 1. The Four-Deep Gate (`dice-bet`)

A live bet requires all of:

1. `--yes` — the global opt-in for state-changing commands
2. `--enable-betting` — the money-surface flag
3. `--live` — without it, the command is a **dry run** (validated, not sent)
4. `--confirm-bet` — the final, per-call confirmation

Plus a `--security-token` minted by a real browser bet. Omit any one and nothing is sent.

---

## 2. `autobet` Gates

| Gate | Purpose |
|---|---|
| `--yes` | global opt-in |
| `--confirm` | per-run live confirmation |
| `--security-token` | browser-minted bet-body token |
| `--dry-run` | default posture on first use of a config |
| `max_loss` / `max_profit` / `max_rounds` | hard per-run caps from the strategy config |
| balance buffer | refuses to start unless `stake + buffer` is available (`buffer` comes from the config, default `0.00000100`) |

`PlayPolicy` (entertainment budget) is the only sanctioned sizing mode; `BankrollPolicy` refuses all betting at negative EV.

---

## 3. Token Rules (Live-Discovered)

| Token source | Result |
|---|---|
| CLI-minted `security_token` | `400 incorrect_2fa` |
| In-page-minted token | `400 incorrect_2fa` |
| **Browser bet-body token** (captured from a real `POST /api/v2/dice/bet`) | works — reusable for many rounds within a ~600 s TTL |

Two operational corollaries:

- **Never rotate cookies between capture and a batch** — a `capture_session.py` run invalidates the captured token.
- Tokens retire mid-batch (~50–70 bets observed). The batch runner retries stranded configs once with a fresh capture.
- `run_single_round.py` token order is `--token` paste (a browser bet-body token from DevTools Network `POST dice/bet`) → REST mint → CDP capture, and it follows the config currency (`currency_id` 101 → BTC, 109 → SOL).

---

## 4. What Is Refused Unconditionally

Deposit and withdrawal are blocked at the client layer (`request()` money-verb chokepoint). There is no flag that enables them. That is a policy decision, not a TODO.

---

## 5. Protocol Verdict Gates (`next_round.py`)

The switch-up protocol decides *and names the command*, so its verdict is itself a
gate. Three rules keep it from recommending something the runner will refuse or
something that would carry the balance through the floor:

| Rule | Meaning |
|---|---|
| Entry is affordable | `base_stake + buffer <= balance` for every slot the family would run |
| Worst case stays above the floor | `balance - exposure >= FLOOR_UBTC` (0.30 µBTC — micro-bankroll reset 2026-09-19, was 75.00) |
| Exposure is the SUM, not the max | plan1 runs 11 configs sequentially, each with its own `max_loss` (`round_flow.py:184-185`), so its worst case is 11 × `max_loss`. Single-config families use their own `max_loss` |

Recovery specs are clamped to the bankroll rather than fixed: the stake is scaled
down so the family's aggregate exposure fits the headroom, preserving the config's
own loss:stake ratio, never dropping below `MIN_STAKE_BTC` (0.05 µBTC, the micro
stake already in use). Clamping is **fail-closed**: if any slot in a family cannot
be clamped, the whole family is refused, because leaving the rest at the old cap
would still breach. When nothing is fundable and floor-safe the protocol prints
`VERDICT: HALT (no fundable floor-safe family)` with a per-family reason instead
of naming a command that would fail.

`--ignore-floor` bypasses the floor check for one verdict computation; the runner preflight still enforces its own funding gate.

### Ledger hygiene

A **0-round, 0-net row is not a round**. It is either an operator `RESET` marker or
a runner attempt that aborted before any wager. Such rows must not advance the
round counter or reset the loss streak (a spurious streak reset flips RECOVERY to
STANDARD). Accordingly:

- `run_single_round.py` refuses to record a 0-round attempt and leaves the ledger
  untouched, exiting non-zero.
- `next_round.py` skips unsettled rows when computing `last_round`, `loss_streak`,
  and `session_drawdown`.

A 0-round row with a **non-zero** net (e.g. a partial killed mid-round) still
counts, because the balance moved.

Evidence: `tests/test_protocol_funding.py` (20 cases) pins these rules against real
ledger row shapes.

---


## 6. If gambling stops being fun

This tool exists to study a system, not to fund anyone. If betting has stopped being entertainment for you: 1-800-GAMBLER (US), or search for your national helpline. Setting a hard budget in `PlayPolicy` before you start is the feature most worth using.
