# Start here — duel-api

**The bot that refuses to bet.**

This Discussion is the onboarding post. Read this before opening issues about "why didn't it bet?"

## What this is

`duel-api` is a private-API automation toolkit for [duel.com](https://duel.com), plus an honest backtester whose best answer is often **stake = 0**.

- Capture → Spec → Replay → Refresh
- Bundle-hash drift tripwire
- Tri-state session staleness
- Loss switch-up protocol that **HALTs** when entry, floor, or total exposure fails
- **331 tests**, MIT

Repo: https://github.com/kingkillery/Duel-api

## Install (60 seconds)

```bash
pip install --extra-index-url https://kingkillery.github.io/Duel-api/simple/ duel-api
duel-api doctor
duel-api demo
```

`doctor` is offline-safe. `demo` runs the offline strategy comparison — no session, no network.

## What it will not do

- Deposit or withdraw (blocked at the client)
- Bypass bot gates (reports and stays read-only)
- Bet by accident (money commands gated four deep)
- Pretend negative EV is positive — Kelly at observed tiers returns **exactly zero**

Invariant: `E[net] = −edge × E[total wagered]`. Sizing changes variance, never the sign.

## First 5 minutes

1. `duel-api doctor` — see which tier you can run
2. `duel-api demo` — see stake=0 / fail-closed
3. Read [docs/backtest-math.md](https://github.com/kingkillery/Duel-api/blob/master/docs/backtest-math.md)
4. Read [docs/betting-gates.md](https://github.com/kingkillery/Duel-api/blob/master/docs/betting-gates.md) before any money command
5. Optional story: [docs/why-i-built-this.md](https://github.com/kingkillery/Duel-api/blob/master/docs/why-i-built-this.md)

## Asking for help

- Bug / drift: include `duel-api doctor` output + OS + Python version
- Protocol HALT: paste the printed reasons (that is the feature)
- Feature ask: say whether you want offline math, read-only live, or gated money paths

## Fair warning (required)

Automating duel.com **may violate its Terms of Service** — your account, your risk. Captured sessions are credentials; this repo gitignores them and so should you. Nothing here is financial advice. Never gamble money you can't afford to lose.

If gambling stops being fun: **1-800-GAMBLER** (US), or search your national helpline.

## Disclosure (FTC)

I may earn a commission if you sign up through my link, at no cost to you. The referral is a **footnote** on purpose — see `docs/why-i-built-this.md`. If the pitch ever becomes the link, the tool failed.

---

Welcome. Star the repo if the methodology helped. Open a thread if something HALTed and you want a second set of eyes on the reasons.
