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
| balance buffer | refuses to start unless `stake + 0.00000100` is available |

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

---

## 4. What Is Refused Unconditionally

Deposit and withdrawal are blocked at the client layer (`request()` money-verb chokepoint). There is no flag that enables them. That is a policy decision, not a TODO.

---

## 5. If gambling stops being fun

This tool exists to study a system, not to fund anyone. If betting has stopped being entertainment for you: 1-800-GAMBLER (US), or search for your national helpline. Setting a hard budget in `PlayPolicy` before you start is the feature most worth using.
