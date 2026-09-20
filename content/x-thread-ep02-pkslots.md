# EP02 X thread — @pkslots

Posted 2026-09-19 from **@pkslots** (confirmed handle). Post 1 went live (`Your post was sent.` Profile count 2 → 3). X web timeline did not hydrate status URLs in this session, so replies 2–7 were not attached automatically — paste them as replies under post 1.

Install: `pip install --extra-index-url https://kingkillery.github.io/Duel-api/simple/ duel-api`

## 1 — hook (posted)

Every betting bot promises to print money. Mine just told me to do nothing.

VERDICT: HALT

That refusal is the most valuable output it's ever produced.

The bot that refuses to bet. Thread ↓

## 2 — protocol

duel-api is open-source for duel.com. Before it names ANY bet it checks:

• can this family afford the entry
• does the worst case stay above the floor
• is total exposure fundable — the SUM over 11 slots, not the average, not the best case

## 3 — gauntlet

Watch it work. Balance: 0.64 µBTC. Floor: 0.30.

Plan-1 exposure sums to over 1 µBTC — more than the account holds. Every family fails the same check, one by one, with its reason printed next to it.

## 4 — verdict

Naming an unrunnable command would be a lie, so the protocol HALTs. No blended exposure. No silent clamp. No bet sized on hope.

A tool that says "don't" is a tool you can trust when it says "do".

## 5 — install

Install (one line):

pip install --extra-index-url https://kingkillery.github.io/Duel-api/simple/ duel-api

Then: duel-api doctor && duel-api demo

331 tests. Inspect the math:
https://github.com/kingkillery/Duel-api

## 6 — start here

Start here (onboarding, including why it won't bet):
https://github.com/kingkillery/Duel-api/issues/1

Discussions: https://github.com/kingkillery/Duel-api/discussions/3

## 7 — disclosure (required)

I may earn a commission if you sign up through my link (https://duel.com/r/jumpyhitman), at no extra cost to you. This tool automates play and may violate duel.com's Terms of Service — your account, your risk.

Nothing here is financial advice. Never gamble money you can't afford to lose.
