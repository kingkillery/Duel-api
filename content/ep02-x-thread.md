# EP02 — X thread (6–8 posts)

Publish window: **2026-09-27** (flagship). Answer-first. Referral is footnote only.
UTM on repo/demo links: `?utm_source=x&utm_medium=social&utm_campaign=ep02`

---

**1/8**
Every betting bot promises to print money.

Mine just told me to do nothing.

That refusal is the most useful output it has ever produced.

**2/8**
duel-api is a private-API toolkit for duel.com.

Capture → Spec → Replay → Refresh. Drift tripwire. Tri-state session staleness.

Then I built the strategy tester everyone wants.

It returned: optimal stake = 0.

**3/8**
The loss switch-up protocol refuses to name a bet until three gates pass:

• Can this family afford the entry?
• Does worst case stay above the floor?
• Is total exposure fundable — the SUM over all 11 slots, not the average?

Fail any one → HALT with reasons. No silent clamp. No hope-sized stake.

**4/8**
Watch a real micro-bankroll fail closed:

balance 0.64 µBTC · floor 0.30 µBTC
plan-1 exposure sums > 1 µBTC

Every family excluded, reason printed.
VERDICT: HALT

A tool that will say "don't" is a tool you can trust when it says "do".

**5/8**
The invariant the backtester keeps proving:

E[net] = −edge × E[total wagered]

Sizing changes variance. Never the sign.
Kelly at observed tiers → stake exactly 0.
Fails closed on purpose.

**6/8**
Try it offline (no account, no network):

```
pip install --extra-index-url https://kingkillery.github.io/Duel-api/simple/ duel-api
duel-api doctor
duel-api demo
```

331 tests. MIT. https://github.com/kingkillery/Duel-api

**7/8**
Scope / fair warning:

Automating duel.com may violate its Terms of Service — your account, your risk.
Nothing here is financial advice. Never gamble money you can't afford to lose.
If gambling stops being fun: 1-800-GAMBLER

**8/8**
I may earn a commission if you sign up through my link (https://duel.com/r/jumpyhitman), at no cost to you.

The referral is a footnote on purpose (docs/why-i-built-this.md). If the pitch ever becomes the link, the tool failed.

Demo video: docs/demo-voiced.mp4 in the repo.
